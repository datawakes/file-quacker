"""Free-form SQL surface: run_sql / DDL / error envelope / save_query_as_table."""

from __future__ import annotations

import tempfile
from pathlib import Path

from file_quacker import ingest
from file_quacker.api import Api


def test_sql_surface_end_to_end():
    p = Path(tempfile.gettempdir()) / 'fq_sql_smoke.csv'
    p.write_text('id,name\n1,alice\n2,bob\n3,carol\n', encoding='utf-8')
    r = ingest.load_file(str(p))
    api = Api()

    res = api.run_sql(f'select * from {r.name} order by id desc')
    assert res['ok'] is True
    assert [c['name'] for c in res['columns']] == ['_src_row_num', 'id', 'name']
    assert res['rows'] == [[3, '3', 'carol'], [2, '2', 'bob'], [1, '1', 'alice']]

    res = api.run_sql(f"create view v_top as select * from {r.name} where id <> '1'")
    assert res['ok'] is True

    res = api.run_sql('select * from table_does_not_exist')
    assert res['ok'] is False
    assert 'table_does_not_exist' in res['error'].lower()

    res = api.save_query_as_table(f'select * from {r.name} where id = 2', 'just_bob')
    assert res['ok'] is True and res['row_count'] == 1
