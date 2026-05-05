"""Import rows from a remote database into a DuckDB workspace table.

Driver-neutral entry point: pass any ``Driver`` (currently just
``SqlServerDriver``; Postgres / MySQL slot in later) and a source
spec (table or query) and a target workspace name.  The reader
streams rows in chunks so memory stays bounded even when the source
table is large; an optional row cap (default 1,000,000) prevents
accidental "import a billion-row table" mishaps.

The import is materialized via a brief Python detour:
  1. pyodbc reads in chunks of ``CHUNK_SIZE`` rows.
  2. We accumulate up to ``row_cap`` rows in Python lists.
  3. DuckDB's ``executemany INSERT`` (after a CTAS for column
     definitions) lands them in the workspace table.

This keeps it cross-driver: any DB-API connection works.  When we
add Postgres via DuckDB's ATTACH extension the same flow applies,
but for ATTACH-capable drivers we'll add a fast path that uses
DuckDB-native INSERT FROM REMOTE (much faster, no Python detour).
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict

from . import db
from .ingest import IngestResult, _unique_name


CHUNK_SIZE = 5_000
DEFAULT_ROW_CAP = 1_000_000

_cancel_flag: bool = False
_running: bool = False
_lock = threading.RLock()


def cancel_active() -> None:
    """Set the cancel flag observed by the next chunk fetch.  The
    in-flight ``executemany`` itself can't be interrupted mid-batch
    but we'll stop after the current chunk and roll back the partial
    table, leaving the workspace clean."""
    global _cancel_flag
    with _lock:
        _cancel_flag = True


def _reset_cancel() -> None:
    global _cancel_flag
    with _lock:
        _cancel_flag = False


def _is_cancelled() -> bool:
    with _lock:
        return _cancel_flag


def import_via_pyodbc(*,
                      driver,
                      conn_opts: dict,
                      database: str | None,
                      schema: str | None,
                      table: str | None,
                      query: str | None,
                      target_name: str,
                      row_cap: int | None = DEFAULT_ROW_CAP) -> dict:
    """Stream rows from a remote database (via the driver's pyodbc
    connection) into a DuckDB workspace table.  Returns an
    IngestResult-as-dict so the frontend can refresh the sidebar
    using the same plumbing as file ingest.

    Exactly one of ``table`` or ``query`` must be set."""
    if not table and not query:
        raise ValueError("import requires either a table name or a query")
    if table and query:
        raise ValueError("pass either a table or a query, not both")

    global _running
    with _lock:
        if _running:
            return {'ok': False, 'error': 'an import is already running', 'elapsed_ms': 0}
        _running = True

    _reset_cancel()
    t0 = time.perf_counter()

    try:
        return _import_via_pyodbc_inner(
            driver=driver, conn_opts=conn_opts, database=database,
            schema=schema, table=table, query=query,
            target_name=target_name, row_cap=row_cap, t0=t0,
        )
    finally:
        with _lock:
            _running = False


def _import_via_pyodbc_inner(*,
                             driver, conn_opts: dict,
                             database: str | None, schema: str | None,
                             table: str | None, query: str | None,
                             target_name: str, row_cap: int | None,
                             t0: float) -> dict:
    # Build the SELECT.  For the table case we use SQL Server bracket
    # quoting since the only driver right now is SQL Server; once
    # Postgres lands this becomes ``driver.qualified_ident(...)``.
    if table:
        parts = []
        if database: parts.append(_qident_ss(database))
        if schema:   parts.append(_qident_ss(schema))
        parts.append(_qident_ss(table))
        sql = f'select * from {".".join(parts)}'
    else:
        sql = query.strip().rstrip(';')                  # type: ignore[union-attr]
        # Wrap the user's query so the final SELECT can carry our
        # row-number column and we don't have to reason about ORDER
        # BY / sub-selects.
        sql = f'select * from ({sql}) as src'

    # Open the source connection (with the right database in scope so
    # bracketed table refs resolve).
    opts_with_db = dict(conn_opts)
    if database:
        opts_with_db['database'] = database

    final_target = _unique_name(target_name)
    qtarget = db.quote_ident(final_target)

    n_rows = 0
    duck = db.conn()
    columns: list[str] = []

    with driver.connect(opts_with_db, omit_database=False, timeout=20) as src_conn:
        cursor = src_conn.cursor()
        cursor.execute(sql)
        if cursor.description is None:
            return {'ok': False, 'error': 'query returned no result set',
                    'elapsed_ms': _ms(t0)}
        # Pull names AND types from `cursor.description`.  Unlike the
        # file ingest path (where everything lands as VARCHAR and the
        # user runs Clone to derive types), a DB source has explicit
        # column types in its catalog; so we honor them up front,
        # the same way Parquet ingest does.  Falls back to VARCHAR
        # for any cursor type code we don't recognize.
        cols_with_types = _columns_with_types(cursor)
        columns = [c[0] for c in cols_with_types]
        # Drop only after the source query succeeded, so a connection
        # or query failure leaves any existing target table untouched.
        duck.execute(f"""
            drop table if exists {qtarget}
            ;
        """)
        _create_empty_target(duck, qtarget, cols_with_types)

        cap = row_cap if row_cap and row_cap > 0 else None
        placeholders = ', '.join(['?'] * len(columns))
        col_list = ', '.join(db.quote_ident(c) for c in columns)
        insert_sql = f'insert into {qtarget} ({col_list}) values ({placeholders})'

        while True:
            if _is_cancelled():
                duck.execute(f"""
                    drop table if exists {qtarget}
                    ;
                """)
                return {'ok': False, 'error': 'cancelled',
                        'elapsed_ms': _ms(t0)}

            remaining = (cap - n_rows) if cap is not None else CHUNK_SIZE
            if cap is not None and remaining <= 0:
                break
            this_chunk = min(CHUNK_SIZE, remaining if cap else CHUNK_SIZE)
            batch = cursor.fetchmany(this_chunk)
            if not batch:
                break
            duck.executemany(insert_sql, [list(r) for r in batch])
            n_rows += len(batch)

    # No `_src_row_num` for DB-sourced tables: the column is a
    # positional id meaningful only for files (row N in the source
    # file).  A SQL heap has no inherent order, so synthesizing a
    # row number against it would be misleading.

    res = IngestResult(
        name=final_target,
        source_path=_describe_source(database, schema, table, query),
        format='csv',                                    # closest existing label
        materialized=True,
        row_count=n_rows,
        col_count=len(columns),
        has_rejects=(cap is not None and n_rows >= cap),
        columns=[{'name': n, 'type': t, 'nullable': True} for n, t in cols_with_types],
        delimiter=None,
        encoding=None,
        first_row=None,
        strategy='import_db',
    )
    out = asdict(res)
    out['ok'] = True
    out['elapsed_ms'] = _ms(t0)
    return out


def _create_empty_target(duck, qtarget: str,
                         cols_with_types: list[tuple[str, str]]) -> None:
    """Create the workspace table with the column types derived from
    the source's pyodbc cursor description.  Pyodbc converts SQL
    Server values into matching Python objects (decimal.Decimal,
    datetime.datetime, etc.), and DuckDB's `executemany` accepts
    those bindings directly into the typed columns we create here."""
    parts = ', '.join(f'{db.quote_ident(n)} {t}' for n, t in cols_with_types)
    duck.execute(f"""
        create table {qtarget} ({parts})
        ;
    """)


def _columns_with_types(cursor) -> list[tuple[str, str]]:
    """Map each pyodbc cursor.description entry to ``(name, duckdb_type)``.

    ``cursor.description`` is a list of tuples
    ``(name, type_code, display_size, internal_size, precision, scale, nullable)``.
    The ``type_code`` is the Python class pyodbc will return for
    that column's values, which is unambiguous enough to bridge to
    DuckDB without round-tripping through ODBC type identifiers.
    Anything we don't recognize falls back to VARCHAR; safe by
    construction since pyodbc would deliver such values as ``str``.
    """
    out: list[tuple[str, str]] = []
    for col in cursor.description:
        name = col[0]
        type_code = col[1]
        precision = col[4] if len(col) > 4 else None
        scale = col[5] if len(col) > 5 else None
        out.append((name, _python_type_to_duckdb(type_code, precision, scale)))
    return out


def _python_type_to_duckdb(type_code, precision, scale) -> str:
    """One-shot mapping for the SQL-Server-via-pyodbc type set.  Add
    cases as new drivers (Postgres, Snowflake) introduce types that
    pyodbc / their connector return."""
    import datetime
    import decimal
    import uuid

    if type_code is bool:                                # SQL Server `bit`
        return 'BOOLEAN'
    if type_code is int:                                 # int / bigint / smallint / tinyint
        return 'BIGINT'
    if type_code is float:                               # float / real
        return 'DOUBLE'
    if type_code is decimal.Decimal:
        if precision and scale is not None:
            return f'DECIMAL({min(int(precision), 38)}, {int(scale)})'
        return 'DECIMAL(38, 10)'
    if type_code is datetime.datetime:                   # datetime / datetime2 / smalldatetime
        return 'TIMESTAMP'
    if type_code is datetime.date:
        return 'DATE'
    if type_code is datetime.time:
        return 'TIME'
    if type_code is bytes or type_code is bytearray:     # varbinary / image / hierarchyid
        return 'BLOB'
    if type_code is uuid.UUID:                           # uniqueidentifier
        return 'UUID'
    # str (varchar/nvarchar/char/text/xml/etc.) and any unknown code.
    return 'VARCHAR'


def _qident_ss(name: str) -> str:
    """SQL Server identifier quoting for the source-side SELECT.  Same
    rules as `[brackets]` in the dialect; duplicated here to avoid a
    cross-package import for what's essentially one regex."""
    import re
    if re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', name):
        return name
    return '[' + name.replace(']', ']]') + ']'


def _describe_source(database, schema, table, query) -> str:
    if table:
        bits = [b for b in (database, schema, table) if b]
        return 'sqlserver://' + '.'.join(bits)
    return 'sqlserver://(query)'


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)
