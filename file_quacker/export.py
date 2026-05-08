"""Export a DuckDB workspace table to SQL Server.

Identifier quoting goes through ``_qident_ss`` so no raw identifier
ever reaches SQL Server as an f-string substitution.

Flow: connect → (optionally) drop/create target → chunked
``fast_executemany`` with adaptive sizing (5 K – 100 K rows, double
under 1 s, halve over 10 s).  On chunk failure, recursively binary-
search the chunk down to a ≤10-row window, then per-row to identify the
exact offending rows + columns.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from decimal import Decimal
from threading import RLock
from typing import Literal

import pyodbc

from . import db, ddl as ddl_mod
from .export_file import _project, _string_columns
from .export_source import ExportSource


# --------------------------------------------------------------------------- #
# Dataclasses                                                                 #
# --------------------------------------------------------------------------- #

@dataclass
class ConnectionOptions:
    server: str
    database: str
    auth: Literal['sql', 'windows']
    username: str | None = None
    password: str | None = None
    port: int | None = None
    azure: bool = False


@dataclass
class ColumnMapping:
    source: str
    target: str
    sql_type: str
    nullable: bool
    # DuckDB source column type.  Carried through so the insert path
    # can size pyodbc parameter buffers without re-probing the catalog.
    # Optional for backward compatibility with older mapping dicts.
    source_type: str = ''


@dataclass
class ExportOptions:
    source: 'ExportSource'
    schema: str
    table_name: str
    mappings: list[ColumnMapping] = field(default_factory=list)
    if_exists: Literal['fail', 'drop', 'append'] = 'fail'
    # Strip leading / trailing whitespace from VARCHAR cells on the way out.
    # Default on; non-string columns are unaffected.
    trim_strings: bool = True

    def __post_init__(self):
        if isinstance(self.source, dict):
            self.source = ExportSource(**self.source)
        self.mappings = [
            m if isinstance(m, ColumnMapping) else ColumnMapping(**m)
            for m in self.mappings
        ]


# --------------------------------------------------------------------------- #
# Module state                                                                #
# --------------------------------------------------------------------------- #

REQUIRED_DRIVER = 'ODBC Driver 18 for SQL Server'

CHUNK_START = 20_000
CHUNK_MIN = 5_000
CHUNK_MAX = 100_000
CHUNK_GROW_UNDER_S = 1.0
CHUNK_SHRINK_OVER_S = 10.0
ERR_WINDOW = 10  # per-row probing kicks in once the failing range is this small

_lock = RLock()
_active_cursor: pyodbc.Cursor | None = None
_cancel_flag = False
_running = False
_progress: dict = {
    'phase': 'idle',
    'rows_written': 0,
    'total_rows': 0,
    'chunk_no': 0,
    'chunk_rows': 0,
    'last_chunk_ms': 0,
    'elapsed_ms': 0,
    'error': None,
}


def _reset_progress(total: int = 0) -> None:
    with _lock:
        _progress.update({
            'phase': 'idle',
            'rows_written': 0,
            'total_rows': total,
            'chunk_no': 0,
            'chunk_rows': 0,
            'last_chunk_ms': 0,
            'elapsed_ms': 0,
            'error': None,
        })


def _set_progress(**kw) -> None:
    with _lock:
        _progress.update(kw)


# --------------------------------------------------------------------------- #
# Identifier quoting + snake_case                                             #
# --------------------------------------------------------------------------- #

_SAFE_IDENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def _qident_ss(name: str) -> str:
    """SQL Server identifier quoting.

    Leaves safe identifiers (``[A-Za-z_][A-Za-z0-9_]*``) bare; bracket-
    quotes everything else and escapes embedded ``]`` by doubling it.
    Auto-snake_case produces bare identifiers for almost every column,
    so the emitted DDL reads cleanly; a user-edited target with a space
    or punctuation triggers bracketing on demand.
    """
    if _SAFE_IDENT.match(name):
        return name
    return '[' + name.replace(']', ']]') + ']'


def _unique_target(base: str, taken: set[str]) -> str:
    """Return ``base`` if free; otherwise ``base_1`` / ``base_2`` / ... until
    a free name is found. Used to de-dupe target column names so SQL Server
    doesn't reject the CREATE TABLE."""
    if base not in taken:
        return base
    i = 1
    while f'{base}_{i}' in taken:
        i += 1
    return f'{base}_{i}'


def _to_snake_case(name: str) -> str:
    """Replace space/dot/hyphen with ``_``, split on CamelCase
    transitions, lowercase, collapse repeated underscores, and strip
    leading/trailing ``_``.  Acronyms stay glued together
    (``Patient_ID`` → ``patient_id``).  Characters like ``$`` or ``()``
    pass through and get bracket-quoted in the emitted SQL.
    """
    name = re.sub(r'[ .-]', '_', name)
    if not name.isupper():
        name = re.sub(r'(?<!^)(?<!_)(?=[A-Z][a-z])|(?<=[a-z])(?=[A-Z])', '_', name)
    name = name.lower()
    name = re.sub(r'_{2,}', '_', name)
    name = name.strip('_')
    return name


# --------------------------------------------------------------------------- #
# Connection                                                                  #
# --------------------------------------------------------------------------- #

def _connect_string(o: ConnectionOptions, *, omit_database: bool = False) -> str:
    if o.azure:
        port = o.port or 1433
        server = f'tcp:{o.server},{port}'
    elif o.port:
        server = f'{o.server},{o.port}'
    else:
        server = o.server

    parts = [
        'Driver={' + REQUIRED_DRIVER + '}',
        f'Server={server}',
    ]
    if not omit_database and o.database:
        parts.append(f'Database={o.database}')
    if o.auth == 'windows':
        parts.append('Trusted_Connection=yes')
    else:
        parts.append(f'UID={o.username or ""}')
        parts.append(f'PWD={o.password or ""}')
    if not o.azure:
        parts.append('TrustServerCertificate=YES')
    return ';'.join(parts) + ';'


def _connect(o: ConnectionOptions, *, timeout: int = 10,
             omit_database: bool = False) -> pyodbc.Connection:
    return pyodbc.connect(
        _connect_string(o, omit_database=omit_database),
        timeout=timeout,
    )


# --------------------------------------------------------------------------- #
# Preflight / metadata                                                        #
# --------------------------------------------------------------------------- #

def check_odbc_driver() -> dict:
    drivers = list(pyodbc.drivers())
    return {
        'ok': REQUIRED_DRIVER in drivers,
        'drivers': drivers,
        'required': REQUIRED_DRIVER,
    }


def test_connection(opts: ConnectionOptions) -> dict:
    # Connects without Database so the user can pick one from the
    # resulting list_databases() without round-tripping through a DB
    # they haven't chosen yet.
    try:
        with _connect(opts, timeout=6, omit_database=True) as conn:
            row = conn.cursor().execute('select @@version').fetchone()
            ver = (row[0].splitlines()[0] if row and row[0] else '').strip()
            return {'ok': True, 'server_version': ver}
    except pyodbc.Error as ex:
        return {'ok': False, 'error': str(ex)}


def list_databases(opts: ConnectionOptions) -> list[str]:
    """User databases (excludes master / tempdb / model / msdb)."""
    with _connect(opts, timeout=6, omit_database=True) as conn:
        rows = conn.cursor().execute("""
            select  name
            from    sys.databases
            where   database_id > 4
            and     state = 0
            order   by name
            ;
        """).fetchall()
    return [r[0] for r in rows]


def list_schemas(opts: ConnectionOptions) -> list[str]:
    """``dbo`` plus user-created schemas.

    Excludes the fixed-role schemas (``db_*``), ``guest``,
    ``INFORMATION_SCHEMA``, and ``sys``.  Matches the user's expectation
    from the legacy app where the dropdown wasn't polluted with server
    internals.
    """
    with _connect(opts, timeout=6) as conn:
        rows = conn.cursor().execute("""
            select  name
            from    sys.schemas
            where   name = 'dbo'
            or      (name not like 'db[_]%'
                     and name not in ('guest', 'INFORMATION_SCHEMA', 'sys'))
            order   by name
            ;
        """).fetchall()
    return [r[0] for r in rows]


# --------------------------------------------------------------------------- #
# Mapping + DDL preview                                                       #
# --------------------------------------------------------------------------- #

def suggest_mappings(source: 'ExportSource | dict',
                     trim_strings: bool = True) -> list[dict]:
    """Per-column default mapping: target name = snake_case(source),
    type = ddl._sqlserver_type of the DuckDB column, nullable from data.

    SQL Server (and most other RDBMSes) reject duplicate column names in
    the same table, but snake-casing can collapse distinct source names
    (`Foo`, `FOO`, `foo`) onto the same target. The first occurrence keeps
    its name; subsequent ones get `_1`, `_2`, ... appended until unique.
    Suffix collisions with later sources cascade through the same scheme,
    so `[foo, foo, foo_1]` resolves to `[foo, foo_1, foo_1_1]`.

    ``trim_strings`` defaults to True to match the export dialog's default;
    string columns get sized from their trimmed lengths so a downstream
    char(N) target won't pad whitespace back into the values.
    """
    if isinstance(source, dict):
        source = ExportSource(**source)
    source_name, cleanup = source.materialize()
    try:
        c = db.conn()
        qtable = db.quote_ident(source_name)
        rows = c.execute(f"""
            pragma table_info({qtable})
            ;
        """).fetchall()
        out: list[dict] = []
        taken: set[str] = set()
        for row in rows:
            name = row[1]
            if name == '_src_row_num':
                continue
            duck_type = row[2]
            sql_type = ddl_mod._sqlserver_type(c, qtable, name, duck_type, trim_strings)
            nullable = ddl_mod._has_nulls(c, qtable, name)
            target = _unique_target(_to_snake_case(name), taken)
            taken.add(target)
            out.append({
                'source': name,
                'source_type': duck_type,
                'target': target,
                'sql_type': sql_type,
                'nullable': nullable,
            })
        return out
    finally:
        cleanup()


def preview_ddl(exp: ExportOptions) -> str:
    """CREATE TABLE text the exporter will run (ignoring ``if_exists``).

    Produces the legacy-app layout: ``(`` aligned with the first column,
    leading commas, name/type/null columns ljust-padded.  Uses the
    qualified ``[schema].[table]`` form (two separately-quoted idents)
    so dots in user input don't collapse into a single identifier.
    """
    qualified = f'{_qident_ss(exp.schema)}.{_qident_ss(exp.table_name)}'
    return _format_ddl(qualified, exp.mappings)


def _format_ddl(qualified_ident: str, mappings: list[ColumnMapping]) -> str:
    names = [_qident_ss(m.target) for m in mappings]
    types = [m.sql_type for m in mappings]
    name_w = max(len(n) for n in names)
    type_w = max(len(t) for t in types)
    lines = [f'create table {qualified_ident}']
    for i, (n, t, m) in enumerate(zip(names, types, mappings)):
        prefix = '(   ' if i == 0 else '  , '
        null_str = '    null' if m.nullable else 'not null'
        lines.append(f'{prefix}{n.ljust(name_w)}  {t.ljust(type_w)}  {null_str}')
    lines.append(')')
    lines.append(';')
    return '\n'.join(lines)


# --------------------------------------------------------------------------- #
# Export                                                                      #
# --------------------------------------------------------------------------- #

def export_to_sqlserver(conn_opts: ConnectionOptions, exp: ExportOptions) -> dict:
    global _active_cursor, _cancel_flag, _running
    if not exp.mappings:
        return {'ok': False, 'error': 'no column mappings provided', 'elapsed_ms': 0}

    with _lock:
        if _running:
            return {'ok': False, 'error': 'an export is already running', 'elapsed_ms': 0}
        _running = True

    t0 = time.perf_counter()
    _cancel_flag = False
    _reset_progress()
    _set_progress(phase='connecting')

    source_name, source_cleanup = exp.source.materialize()
    qsource = db.quote_ident(source_name)
    str_cols = _string_columns(qsource) if exp.trim_strings else set()
    src_select = ', '.join(
        _project(m.source, m.source in str_cols, exp.trim_strings)
        for m in exp.mappings
    )
    (total_rows,) = db.conn().execute(f"""
        select  count(*)
        from    {qsource}
        ;
    """).fetchone()
    total_rows = int(total_rows)
    _reset_progress(total=total_rows)
    _set_progress(phase='connecting')

    tgt_sql = f'{_qident_ss(exp.schema)}.{_qident_ss(exp.table_name)}'
    qualified_literal = f'{exp.schema}.{exp.table_name}'

    conn = None
    try:
        try:
            conn = _connect(conn_opts, timeout=10)
        except pyodbc.Error as ex:
            _set_progress(phase='error', error=str(ex))
            return {'ok': False, 'error': str(ex), 'elapsed_ms': _ms(t0)}

        _set_progress(phase='creating')
        with conn.cursor() as cur:
            exists_row = cur.execute("""
                select  1
                from    sys.tables t
                join    sys.schemas s on t.schema_id = s.schema_id
                where   t.name = ?
                and     s.name = ?
                ;
            """, exp.table_name, exp.schema).fetchone()
            exists = exists_row is not None

        if exists and exp.if_exists == 'fail':
            msg = f'table {qualified_literal} already exists'
            _set_progress(phase='error', error=msg)
            return {'ok': False, 'error': msg, 'elapsed_ms': _ms(t0)}

        if exists and exp.if_exists == 'drop':
            with conn.cursor() as cur:
                cur.execute(f"""
                    drop table {tgt_sql}
                    ;
                """)
                conn.commit()
            exists = False

        if not exists:
            ddl = preview_ddl(exp)
            with conn.cursor() as cur:
                cur.execute(ddl)
                conn.commit()

        return _insert_rows(conn, cur=None, exp=exp, tgt_sql=tgt_sql,
                            qsource=qsource, src_select=src_select,
                            total_rows=total_rows, t0=t0)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        with _lock:
            _active_cursor = None
            _running = False
        try:
            source_cleanup()
        except Exception:
            pass


def _insert_rows(conn, cur, exp: ExportOptions, tgt_sql: str,
                 qsource: str, src_select: str,
                 total_rows: int, t0: float) -> dict:
    """Stream rows from DuckDB and chunk-insert into SQL Server."""
    global _active_cursor, _cancel_flag

    tgt_cols_sql = ', '.join(_qident_ss(m.target) for m in exp.mappings)
    placeholders = ', '.join(['?'] * len(exp.mappings))
    insert_q = f'insert into {tgt_sql} ({tgt_cols_sql}) values ({placeholders})'

    c = db.conn()

    # Without setinputsizes, fast_executemany would size param buffers
    # from the first row's values; longer later rows then truncate.
    input_sizes = _compute_input_sizes(c, qsource, exp.mappings)

    _set_progress(phase='inserting')

    # Stream the source via fetchmany so we don't materialize the whole
    # result in Python memory before the first chunk goes out.
    duck_cur = c.execute(f"""
        select  {src_select}
        from    {qsource}
        ;
    """)

    chunk_size = CHUNK_START
    rows_written = 0
    chunk_no = 0

    cursor = conn.cursor()
    cursor.fast_executemany = True
    cursor.setinputsizes(input_sizes)
    with _lock:
        _active_cursor = cursor

    try:
        while True:
            if _cancel_flag:
                conn.rollback()
                msg = 'cancelled'
                _set_progress(phase='error', error=msg)
                return {'ok': False, 'error': msg, 'rows_written': rows_written,
                        'elapsed_ms': _ms(t0)}

            chunk = duck_cur.fetchmany(chunk_size)
            if not chunk:
                break

            chunk_no += 1
            _set_progress(chunk_no=chunk_no, chunk_rows=len(chunk),
                          elapsed_ms=_ms(t0))

            t_chunk = time.perf_counter()
            try:
                cursor.executemany(insert_q, chunk)
            except pyodbc.Error as ex:
                # Bsearch + per-row probe so the user sees the offending row.
                error_rows = _find_error_rows(
                    cursor, insert_q, chunk, base_offset=rows_written,
                    mappings=exp.mappings, top_error=str(ex),
                )
                conn.rollback()
                msg = str(ex).splitlines()[0] if str(ex) else 'insert failed'
                _set_progress(phase='error', error=msg, elapsed_ms=_ms(t0))
                return {
                    'ok': False,
                    'error': msg,
                    'rows_written': rows_written,
                    'error_rows': error_rows,
                    'elapsed_ms': _ms(t0),
                }

            chunk_ms = int((time.perf_counter() - t_chunk) * 1000)
            rows_written += len(chunk)
            _set_progress(rows_written=rows_written, last_chunk_ms=chunk_ms,
                          elapsed_ms=_ms(t0))

            elapsed_chunk = chunk_ms / 1000.0
            if elapsed_chunk < CHUNK_GROW_UNDER_S and chunk_size < CHUNK_MAX:
                chunk_size = min(chunk_size * 2, CHUNK_MAX)
            elif elapsed_chunk > CHUNK_SHRINK_OVER_S and chunk_size > CHUNK_MIN:
                chunk_size = max(chunk_size // 2, CHUNK_MIN)

        _set_progress(phase='verify', elapsed_ms=_ms(t0))
        conn.commit()
        _set_progress(phase='done', rows_written=rows_written, elapsed_ms=_ms(t0))
        return {'ok': True, 'rows_written': rows_written, 'elapsed_ms': _ms(t0)}
    finally:
        try:
            cursor.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Error-row narrowing                                                         #
# --------------------------------------------------------------------------- #

def _compute_input_sizes(c, qsource: str, mappings: list[ColumnMapping]) -> list:
    """Per-column `setinputsizes` hints for fast_executemany.

    Only VARCHAR source columns need a hint; that's where pyodbc's
    first-row buffer-sizing trap bites.  Numeric / date / bool params
    are fixed-width on the Python side, so `None` is correct for them.
    """
    if not mappings:
        return []

    string_positions = [
        (i, m) for i, m in enumerate(mappings)
        if m.source_type.upper() == 'VARCHAR'
    ]

    max_lens: dict[int, int] = {}
    if string_positions:
        agg_parts = ', '.join(
            f'max(length({db.quote_ident(m.source)})) as c{pos}'
            for pos, (_i, m) in enumerate(string_positions)
        )
        row = c.execute(f"""
            select  {agg_parts}
            from    {qsource}
            ;
        """).fetchone()
        for pos, (idx, _m) in enumerate(string_positions):
            max_lens[idx] = int(row[pos] or 0)

    sizes: list = []
    for i, m in enumerate(mappings):
        if m.source_type.upper() == 'VARCHAR':
            # +32 pad for trailing spaces / NULL placeholders / BOM.
            # Floor at 8 so empty / all-null columns get a usable buffer.
            buf = max(max_lens.get(i, 0), 8) + 32
            sizes.append((pyodbc.SQL_WVARCHAR, buf, 0))
        else:
            sizes.append(None)
    return sizes


def _find_error_rows(cursor, insert_q: str, chunk: list,
                     base_offset: int, mappings: list[ColumnMapping],
                     top_error: str) -> list[dict]:
    """Binary-search the failing chunk to a ≤ERR_WINDOW slice, then probe
    row-by-row within it to identify the exact offending rows.

    Returns up to 3 entries.  The cursor sits inside a failed-and-rolled-
    back transaction; we need a fresh savepoint-free cursor before each
    probe so pyodbc doesn't raise ``HY010`` (function sequence error).
    """
    start, end = _bsearch_range(cursor, insert_q, chunk, 0, len(chunk))
    probes: list[dict] = []
    for idx in range(start, end):
        try:
            cursor.execute(insert_q, chunk[idx])
        except pyodbc.Error as ex:
            probes.append(_describe_error_row(
                row_num=base_offset + idx + 1,
                row=chunk[idx],
                mappings=mappings,
                error=str(ex),
            ))
            if len(probes) >= 3:
                break
    if not probes:
        # Nothing re-raised in single-row probing; surface the chunk's
        # top-level error so the user still sees something useful.
        probes.append({
            'row': base_offset + start + 1,
            'marked': [],
            'values': _values_as_dict(chunk[start], mappings),
            'message': top_error.splitlines()[0] if top_error else 'unknown error',
        })
    return probes


def _bsearch_range(cursor, insert_q: str, chunk: list,
                   start: int, end: int) -> tuple[int, int]:
    if end - start <= ERR_WINDOW:
        return start, end
    mid = (start + end) // 2
    try:
        cursor.fast_executemany = True
        cursor.executemany(insert_q, chunk[start:mid])
        return _bsearch_range(cursor, insert_q, chunk, mid, end)
    except pyodbc.Error:
        return _bsearch_range(cursor, insert_q, chunk, start, mid)


def _describe_error_row(row_num: int, row, mappings: list[ColumnMapping],
                        error: str) -> dict:
    marked = _mark_offending_columns(row, mappings, error)
    msg = error.splitlines()[0] if error else ''
    # Annotate the message with repr() of each marked column's value.
    # Hidden characters (BOM, NBSP, control chars, trailing whitespace)
    # are invisible in the raw string but obvious in repr — surfacing
    # them here saves the user from running their own ad-hoc query when
    # the SQL Server error doesn't already include the value.
    if marked:
        by_target = {m.target: v for m, v in zip(mappings, row)}
        annots = [f'{col}={_short_repr(by_target.get(col))}' for col in marked]
        if annots:
            msg = f'{msg} · {", ".join(annots)}'
    return {
        'row': row_num,
        'marked': marked,
        'values': _values_as_dict(row, mappings),
        'message': msg,
    }


def _short_repr(v: object, limit: int = 50) -> str:
    """repr(v), capped to ``limit`` chars with a trailing ellipsis so a
    long blob value doesn't blow out the error pane."""
    r = repr(v)
    return r if len(r) <= limit else r[:limit] + '...'


def _mark_offending_columns(row, mappings: list[ColumnMapping],
                            error: str) -> list[str]:
    """Parse SQL Server error text for known patterns that name the column
    or show the offending value. Falls back to a per-column parse check
    when SQL Server's message is too terse to identify the offender on
    its own (e.g. error 8114, 'Error converting data type nvarchar to
    numeric', which doesn't name the column or include the value)."""
    marked: set[str] = set()
    col_match = re.search(r"column '([^']+)'", error)
    if col_match:
        marked.add(col_match.group(1))
    trunc_match = re.search(r"Truncated value: '([^']+)'", error)
    value_match = re.search(r"value '([^']+)'", error)
    hit_value = None
    if trunc_match:
        hit_value = trunc_match.group(1)
    elif value_match and 'Truncated' not in error:
        hit_value = value_match.group(1)
    if hit_value is not None:
        for m, v in zip(mappings, row):
            vs = '' if v is None else str(v)
            if vs == hit_value or (hit_value and vs.startswith(hit_value)):
                marked.add(m.target)

    # Fallback: error names a conversion failure but didn't include the
    # offending value, so the regex above couldn't locate the column.
    # Walk the row and flag any numeric-target column whose value
    # doesn't parse as the destination type.
    if not marked and 'converting data type' in error.lower():
        marked.update(_columns_failing_target_cast(row, mappings))

    return sorted(marked)


_TYPED_TARGET_RE = re.compile(
    r'^\s*(int|bigint|tinyint|smallint|decimal|numeric|float|real|money|smallmoney|bit)\b',
    re.IGNORECASE,
)

# Strings SQL Server's bit type accepts on insert. Same accept set used by
# ddl._has_only_strict_boolean_values, kept here as a tuple for fast
# per-row checking.
_BIT_ACCEPTED = ('0', '1', 'true', 'false')


def _columns_failing_target_cast(row, mappings: list[ColumnMapping]) -> list[str]:
    """Best-effort offender detection for type-conversion errors that
    SQL Server reports without naming the column. For each mapping with
    a numeric or bit target, parse the row's value the same way the
    bind layer would and return the columns that fail."""
    out: list[str] = []
    for m, v in zip(mappings, row):
        if v is None:
            continue
        if not _TYPED_TARGET_RE.match(m.sql_type or ''):
            continue
        if not _value_parses_as_target(m.sql_type, v):
            out.append(m.target)
    return out


def _value_parses_as_target(sql_type: str, v: object) -> bool:
    """Whether ``v`` parses cleanly as the target SQL Server type.
    Mirrors the cases the bind layer's nvarchar→numeric / nvarchar→bit
    cast would fail on (non-numeric text, comma thousand separators,
    Yes/No strings on a bit column, etc.)."""
    s = str(v).strip()
    if not s:
        return False
    t = sql_type.lower()
    try:
        if t.startswith('bit'):
            return s.lower() in _BIT_ACCEPTED
        if t.startswith(('decimal', 'numeric', 'money', 'smallmoney')):
            Decimal(s)
            return True
        if t.startswith(('float', 'real')):
            float(s)
            return True
        if t.startswith(('int', 'bigint', 'tinyint', 'smallint')):
            int(s)
            return True
    except Exception:
        return False
    return True


def _values_as_dict(row, mappings: list[ColumnMapping]) -> dict:
    out: dict = {}
    for m, v in zip(mappings, row):
        out[m.target] = None if v is None else _jsonable(v)
    return out


def _jsonable(v):
    if isinstance(v, (int, float, bool, str)) or v is None:
        return v
    return str(v)


# --------------------------------------------------------------------------- #
# Progress + cancel                                                           #
# --------------------------------------------------------------------------- #

def get_progress() -> dict:
    with _lock:
        return dict(_progress)


def cancel() -> bool:
    global _cancel_flag, _active_cursor
    _cancel_flag = True
    with _lock:
        cur = _active_cursor
    if cur is None:
        return False
    try:
        cur.cancel()
        return True
    except Exception:
        return False


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)
