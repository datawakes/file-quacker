"""Persistent DuckDB session.

The whole app shares one in-memory DuckDB database.  `conn()` returns a
fresh `.cursor()` per call because pywebview dispatches every js_api
invocation on a worker thread, and DuckDB's Python root connection is
not safe across threads; cross-thread reads can miss recently created
tables/views.  Cursors are thread-local and share the catalog, which is
what we want.
"""

from __future__ import annotations

import functools
import re
import tempfile
from pathlib import Path
from threading import RLock

import duckdb

_root: duckdb.DuckDBPyConnection | None = None
_temp_dir: Path | None = None
_lock = RLock()


def _ensure_root() -> duckdb.DuckDBPyConnection:
    global _root, _temp_dir
    with _lock:
        if _root is None:
            _temp_dir = Path(tempfile.mkdtemp(prefix='fq_duckdb_'))
            _root = duckdb.connect(database=':memory:')
            _root.execute(f"""
                set temp_directory = '{_temp_dir.as_posix()}'
                ;
            """)
            # 2.0 errors on `1 // 0` and `x % 0`; global keeps 1.x's NULL on every cursor.
            _root.execute("""
                set global error_on_division_by_zero = false
                ;
            """)
        return _root


def conn() -> duckdb.DuckDBPyConnection:
    """Return a thread-local cursor sharing the catalog with every other caller."""
    return _ensure_root().cursor()


def close() -> None:
    """Tear down the connection and remove the spill directory."""
    global _root, _temp_dir
    with _lock:
        if _root is not None:
            _root.close()
            _root = None
        if _temp_dir is not None and _temp_dir.exists():
            import shutil
            shutil.rmtree(_temp_dir, ignore_errors=True)
            _temp_dir = None


_SAFE_IDENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


@functools.cache
def _keywords() -> frozenset[str]:
    """Keywords that can't stand bare as a column or table name.

    Reserved words (`when`, `select`, `order`) and type/function words
    (`left`, `join`, `like`) are parse errors there; `true`, `false` and
    `null` parse but read as literals instead of the column.  The
    `column_name` words (`time`, `values`, `position`) work bare.
    """
    rows = conn().execute("""
        select  keyword_name
        from    duckdb_keywords()
        where   keyword_category in ('reserved', 'type_function')
        ;
    """).fetchall()
    return frozenset(r[0] for r in rows)


def is_safe_ident(name: str) -> bool:
    """True when a name needs no quoting: matches `[A-Za-z_][A-Za-z0-9_]*`
    and isn't a keyword DuckDB would read as syntax."""
    return bool(_SAFE_IDENT.match(name)) and name.lower() not in _keywords()


def quote_ident(name: str) -> str:
    """Quote a DuckDB identifier, escaping embedded double quotes.

    Used everywhere DDL / DML composes user-provided names; f-string
    concatenation of raw names into SQL is a known injection shape.
    """
    return '"' + name.replace('"', '""') + '"'


def display_ident(name: str) -> str:
    """Return ``name`` bare when safe, otherwise double-quoted.  Used
    when emitting human-facing SQL where bare names read better."""
    return name if is_safe_ident(name) else quote_ident(name)


# The type `"NULL"` itself, not a struct field that happens to be named
# "NULL" (`STRUCT("NULL" INTEGER)`); a type is followed by `)`, `,`, `[`
# or the end of the string, a field name by a space.
_NULL_TYPE = re.compile(r'"NULL"(?=[\[),]|$)')


def _null_free_type(data_type: str) -> str:
    """``data_type`` with every untyped NULL swapped for VARCHAR, so
    `"NULL"[]` becomes `VARCHAR[]` and `STRUCT(k "NULL")` becomes
    `STRUCT(k VARCHAR)`."""
    return _NULL_TYPE.sub('VARCHAR', data_type)


def retype_null_columns(c: duckdb.DuckDBPyConnection, table: str | None = None) -> None:
    """Turn untyped NULL columns into VARCHAR.

    DuckDB 2.0 gives `select null as x` the type "NULL", which rejects
    any later non-null insert and has no type to offer in DDL.  Run this
    after anything that builds tables from user SQL.  With ``table``
    only that table is checked (one pragma); without, every base table
    in the current schema plus this cursor's temp tables (a catalog scan,
    ~10 ms on a wide catalog).
    The ALTER is cheap: an all-NULL column has no data to convert.
    """
    if table is None:
        rows = c.execute("""
            select  database_name
            ,       schema_name
            ,       table_name
            ,       column_name
            ,       data_type
            from    duckdb_columns()
            where   (   (database_name = current_database() and schema_name = current_schema())
                    or  database_name = 'temp'
                    )
            and     contains(data_type, '"NULL"')
            and     table_oid not in (select view_oid from duckdb_views())
            ;
        """).fetchall()
        hits = [
            ('.'.join(quote_ident(part) for part in (database, schema, name)), column, data_type)
            for database, schema, name, column, data_type in rows
        ]
    else:
        hits = [
            (quote_ident(table), name, data_type)
            for _cid, name, data_type, *_rest in c.execute(f"""
                pragma table_info({quote_ident(table)})
                ;
            """).fetchall()
        ]
    for qtable, column_name, data_type in hits:
        new_type = _null_free_type(data_type)
        if new_type == data_type:
            continue
        c.execute(f"""
            alter table {qtable}
            alter column {quote_ident(column_name)} type {new_type}
            ;
        """)
