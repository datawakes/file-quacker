"""Free-form SQL execution against the shared DuckDB session.

Query results are materialized into a hidden ``__fq_sql_result_<n>``
workspace table so the frontend can page through the full result set
with windowed fetches; same pattern the main grid uses for loaded
tables.  No row cap; cancellation interrupts the CTAS and drops any
partial table.

Two concerns live here that api.py would rather not:
  * tracking the cursor of the currently running query so a separate
    `cancel` call from the UI can interrupt it;
  * cleaning up prior result tables so they don't accumulate.
"""

from __future__ import annotations

import time
from threading import RLock
from typing import Any

import duckdb

from . import db

RESULT_TABLE_PREFIX = '__fq_sql_result'
FIRST_WINDOW_SIZE = 500

_active: duckdb.DuckDBPyConnection | None = None
_active_lock = RLock()
_result_tick = 0
_created_results: set[str] = set()


def _set_active(cur: duckdb.DuckDBPyConnection | None) -> None:
    global _active
    with _active_lock:
        _active = cur


# A "result-producing" statement is one we can safely wrap in
# ``CREATE TABLE AS``.  Non-result statements (DDL / DML / PRAGMA) run
# directly and report an empty envelope.  The check is a lightweight
# keyword sniff; comments are stripped first.
_RESULT_KEYWORDS = ('SELECT', 'WITH', 'FROM', 'VALUES', 'TABLE', 'DESCRIBE', 'SUMMARIZE')


def _strip_leading_comments(sql: str) -> str:
    s = sql
    while True:
        s = s.lstrip()
        if s.startswith('--'):
            nl = s.find('\n')
            s = s[nl + 1:] if nl >= 0 else ''
            continue
        if s.startswith('/*'):
            end = s.find('*/')
            s = s[end + 2:] if end >= 0 else ''
            continue
        break
    return s


def _is_result_producing(sql: str) -> bool:
    s = _strip_leading_comments(sql).upper()
    # Paren-wrapped SELECT is valid: `(SELECT ...)`
    if s.startswith('('):
        s = s.lstrip('(').lstrip()
    return any(s.startswith(kw) for kw in _RESULT_KEYWORDS)


def run(sql_text: str) -> dict:
    """Execute ``sql_text`` and return a JSON-safe result envelope.

    Shape on success (result-producing query):
        { ok: True, result_table, columns: [{name, type}],
          rows: [first window], row_count, elapsed_ms, truncated: False }
    Shape on success (DDL / DML / PRAGMA):
        { ok: True, result_table: None, columns: [], rows: [],
          row_count: 0, elapsed_ms, truncated: False }
    On DuckDB error:
        { ok: False, error: str, elapsed_ms }
    """
    global _result_tick

    cursor = db.conn()
    _set_active(cursor)
    t0 = time.perf_counter()

    try:
        _drop_prior_results(cursor)

        if not _is_result_producing(sql_text):
            cursor.execute(sql_text)
            return _empty_envelope(t0)

        _result_tick += 1
        result_table = f'{RESULT_TABLE_PREFIX}_{_result_tick}'
        qtable = db.quote_ident(result_table)
        try:
            cursor.execute(f"""
                create table {qtable} as
                {sql_text}
                ;
            """)
            _created_results.add(result_table)
        except duckdb.Error:
            try:
                cursor.execute(f"""
                    drop table if exists {qtable}
                    ;
                """)
            except Exception:
                pass
            raise

        desc = cursor.execute(f"""
            pragma table_info({qtable})
            ;
        """).fetchall()
        cols = [{'name': d[1], 'type': d[2]} for d in desc]
        (n,) = cursor.execute(f"""
            select  count(*)
            from    {qtable}
            ;
        """).fetchone()
        row_count = int(n)

        first = cursor.execute(f"""
            select  *
            from    {qtable}
            limit   {FIRST_WINDOW_SIZE}
            ;
        """).fetchall()
        safe: list[list[Any]] = [[_json_safe(v) for v in r] for r in first]

        return {
            'ok': True,
            'result_table': result_table,
            'columns': cols,
            'rows': safe,
            'row_count': row_count,
            'truncated': False,
            'elapsed_ms': int((time.perf_counter() - t0) * 1000),
        }
    except duckdb.Error as ex:
        return {
            'ok': False,
            'error': str(ex),
            'elapsed_ms': int((time.perf_counter() - t0) * 1000),
        }
    finally:
        _set_active(None)


def _empty_envelope(t0: float) -> dict:
    return {
        'ok': True,
        'result_table': None,
        'columns': [],
        'rows': [],
        'row_count': 0,
        'truncated': False,
        'elapsed_ms': int((time.perf_counter() - t0) * 1000),
    }


def _drop_prior_results(cursor) -> None:
    """Drop only the result tables we created in earlier runs, so a
    user-created table that happens to share the prefix is preserved."""
    for name in list(_created_results):
        try:
            cursor.execute(f"""
                drop table if exists {db.quote_ident(name)}
                ;
            """)
        except Exception:
            pass
        finally:
            _created_results.discard(name)


def cancel() -> bool:
    """Interrupt the currently running query, if any.  The running
    ``run()`` call resolves with an error envelope describing the
    cancellation; any partial CTAS table is dropped on the next run."""
    with _active_lock:
        if _active is None:
            return False
        try:
            _active.interrupt()
            return True
        except Exception:
            return False


def save_as_table(sql_text: str, table_name: str) -> dict:
    """Run ``create table <name> as <sql>`` and report the resulting row count."""
    cursor = db.conn()
    qname = db.quote_ident(table_name)
    cursor.execute(f"""
        create table {qname} as
        {sql_text}
        ;
    """)
    (rc,) = cursor.execute(f"""
        select  count(*)
        from    {qname}
        ;
    """).fetchone()
    return {'ok': True, 'name': table_name, 'row_count': int(rc)}


def _json_safe(v: Any) -> Any:
    if v is None or isinstance(v, (int, float, bool, str)):
        return v
    return str(v)
