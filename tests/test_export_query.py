"""Export a SQL query result (not a table) to SQL Server.

Marked `integration`; skipped by default.  Exercises
``ExportSource(kind='query', sql=...)`` end-to-end: query → DuckDB view
→ suggest_mappings → export_to_sqlserver → row count check.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from file_quacker import export, ingest
from file_quacker.api import Api


pytestmark = pytest.mark.integration


def _env_conn() -> export.ConnectionOptions | None:
    parts = [os.environ.get(k) for k in ('FQ_SS_SERVER', 'FQ_SS_DATABASE', 'FQ_SS_USER', 'FQ_SS_PASS')]
    if not all(parts):
        return None
    s, d, u, p = parts
    return export.ConnectionOptions(server=s, database=d, auth='sql', username=u, password=p)


@pytest.fixture(scope='module')
def conn() -> export.ConnectionOptions:
    if not export.check_odbc_driver()['ok']:
        pytest.skip('ODBC Driver 18 for SQL Server not installed')
    c = _env_conn()
    if c is None:
        pytest.skip('FQ_SS_* env vars not set')
    return c


def _drop_if_exists(conn, schema, tbl):
    with export._connect(conn) as c:
        cur = c.cursor()
        cur.execute(f"""
            if object_id(?, 'U') is not null
                drop table {export._qident_ss(schema)}.{export._qident_ss(tbl)}
            ;
        """, f'{schema}.{tbl}')
        c.commit()


def _count_rows(conn, schema, tbl):
    with export._connect(conn) as c:
        (n,) = c.cursor().execute(f"""
            select  count(*)
            from    {export._qident_ss(schema)}.{export._qident_ss(tbl)}
            ;
        """).fetchone()
    return int(n)


def test_query_export_filters_to_five_rows(conn):
    p = Path(tempfile.gettempdir()) / 'fq_query_src.csv'
    rows = ['id,name,amount']
    for i in range(1, 11):
        rows.append(f'{i},n_{i},{i * 2.5}')
    p.write_text('\n'.join(rows) + '\n', encoding='utf-8')
    r = ingest.load_file(str(p))

    api = Api()

    query = f"""
        select  cast(id as integer)     as id
        ,       cast(amount as double)  as amount
        from    "{r.name}"
        where   cast(id as integer) <= 5
    """
    source = {'kind': 'query', 'name': None, 'sql': query}

    sug = api.suggest_export_mappings(source)
    assert len(sug) == 2
    mappings = [{k: s[k] for k in ('source', 'target', 'sql_type', 'nullable')} for s in sug]
    assert {m['source'] for m in mappings} == {'id', 'amount'}

    tbl = 'fq_smoke_query_export'
    _drop_if_exists(conn, 'dbo', tbl)

    res = api.export_to_sqlserver(conn.__dict__, {
        'source': source, 'schema': 'dbo', 'table_name': tbl,
        'mappings': mappings, 'if_exists': 'drop',
    })
    assert res['ok']
    assert _count_rows(conn, 'dbo', tbl) == 5
    _drop_if_exists(conn, 'dbo', tbl)
