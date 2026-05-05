"""SQL Server integration tests; round-trip via fast_executemany.

Marked `integration`; skipped by default.  Run with ``pytest -m integration``
when the four ``FQ_SS_*`` env vars are set and ODBC Driver 18 is installed.

Env vars:
    FQ_SS_SERVER     host or host,port
    FQ_SS_DATABASE   database name
    FQ_SS_USER       SQL login
    FQ_SS_PASS       password
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
from pathlib import Path

import pytest

from file_quacker import export, ingest
from file_quacker.api import Api


pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #

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


def _drop_if_exists(conn: export.ConnectionOptions, schema: str, tbl: str) -> None:
    with export._connect(conn) as c:
        cur = c.cursor()
        cur.execute(f"""
            if object_id(?, 'U') is not null
                drop table {export._qident_ss(schema)}.{export._qident_ss(tbl)}
            ;
        """, f'{schema}.{tbl}')
        c.commit()


def _count_rows(conn: export.ConnectionOptions, schema: str, tbl: str) -> int:
    with export._connect(conn) as c:
        (n,) = c.cursor().execute(
            f'select count(*) from {export._qident_ss(schema)}.{export._qident_ss(tbl)}'
        ).fetchone()
    return int(n)


def _make_csv(path: Path, rows: int = 1000) -> None:
    lines = ['id,name,amount']
    for i in range(1, rows + 1):
        lines.append(f'{i},name_{i},{round(i * 1.5, 2)}')
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


# --------------------------------------------------------------------------- #
# Tests                                                                       #
# --------------------------------------------------------------------------- #

def test_test_connection_accepts_good_and_rejects_bad(conn):
    api = Api()
    ok = api.test_sqlserver_connection(conn.__dict__)
    assert ok['ok']

    bad = api.test_sqlserver_connection({**conn.__dict__, 'server': '10.255.255.1'})
    assert not bad['ok']


def test_list_schemas_includes_dbo(conn):
    schemas = Api().list_sqlserver_schemas(conn.__dict__)
    assert 'dbo' in schemas


def test_round_trip_drop_fail_append(conn):
    api = Api()
    csv_path = Path(tempfile.gettempdir()) / 'fq_smoke_export.csv'
    _make_csv(csv_path, rows=1000)
    r = ingest.load_file(str(csv_path))
    source = {'kind': 'table', 'name': r.name, 'sql': None}

    sug = api.suggest_export_mappings(source)
    assert len(sug) == 3
    mappings = [{k: s[k] for k in ('source', 'target', 'sql_type', 'nullable')} for s in sug]

    schema, tbl = 'dbo', 'fq_smoke_export'
    _drop_if_exists(conn, schema, tbl)

    base_opts = {
        'source': source, 'schema': schema, 'table_name': tbl,
        'mappings': mappings, 'if_exists': 'drop',
    }

    # drop mode
    res = api.export_to_sqlserver(conn.__dict__, base_opts)
    assert res['ok']
    assert res['rows_written'] == 1000
    assert _count_rows(conn, schema, tbl) == 1000

    # fail mode rejects existing
    res = api.export_to_sqlserver(conn.__dict__, {**base_opts, 'if_exists': 'fail'})
    assert not res['ok'] and 'already exists' in (res.get('error') or '')

    # append mode doubles
    res = api.export_to_sqlserver(conn.__dict__, {**base_opts, 'if_exists': 'append'})
    assert res['ok']
    assert _count_rows(conn, schema, tbl) == 2000

    _drop_if_exists(conn, schema, tbl)


def test_narrow_varchar_surfaces_error_rows(conn):
    api = Api()
    csv_path = Path(tempfile.gettempdir()) / 'fq_smoke_export.csv'
    _make_csv(csv_path, rows=1000)
    r = ingest.load_file(str(csv_path))
    source = {'kind': 'table', 'name': r.name, 'sql': None}
    sug = api.suggest_export_mappings(source)
    mappings = [{k: s[k] for k in ('source', 'target', 'sql_type', 'nullable')} for s in sug]

    narrow = [dict(m) for m in mappings]
    for m in narrow:
        if m['source'] == 'name':
            m['sql_type'] = 'varchar(4)'

    schema, tbl = 'dbo', 'fq_smoke_export'
    _drop_if_exists(conn, schema, tbl)
    res = api.export_to_sqlserver(conn.__dict__, {
        'source': source, 'schema': schema, 'table_name': tbl,
        'mappings': narrow, 'if_exists': 'drop',
    })
    assert not res['ok']
    assert res.get('error_rows')


def test_cancel_mid_export(conn):
    api = Api()
    csv_path = Path(tempfile.gettempdir()) / 'fq_smoke_export.csv'
    _make_csv(csv_path, rows=100_000)
    big = ingest.load_file(str(csv_path))
    source = {'kind': 'table', 'name': big.name, 'sql': None}
    sug = api.suggest_export_mappings(source)
    mappings = [{k: s[k] for k in ('source', 'target', 'sql_type', 'nullable')} for s in sug]

    schema, tbl = 'dbo', 'fq_smoke_export'
    _drop_if_exists(conn, schema, tbl)

    def delayed_cancel():
        time.sleep(0.6)
        api.cancel_export()

    t = threading.Thread(target=delayed_cancel)
    t.start()
    res = api.export_to_sqlserver(conn.__dict__, {
        'source': source, 'schema': schema, 'table_name': tbl,
        'mappings': mappings, 'if_exists': 'drop',
    })
    t.join()
    assert not res['ok']
    _drop_if_exists(conn, schema, tbl)
