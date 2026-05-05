"""auto_derive + generate_ddl end-to-end."""

from __future__ import annotations

import tempfile
from pathlib import Path

from file_quacker import ingest
from file_quacker.api import Api


def test_auto_derive_plus_ddl_for_both_dialects():
    p = Path(tempfile.gettempdir()) / 'fq_auto_smoke.csv'
    p.write_text(
        'id,dob,label\n'
        '1,2024-01-15,alpha\n'
        '2,2024-02-02,beta\n'
        '3,1999-12-31,gamma\n',
        encoding='utf-8',
    )
    r = ingest.load_file(str(p))
    api = Api()

    created = api.auto_derive(r.name, f'{r.name}_auto')
    assert created['ok'] is True
    assert created['row_count'] == 3

    ddl_duck = api.generate_ddl(created['name'], 'duckdb')['sql']
    ddl_mssql = api.generate_ddl(created['name'], 'sqlserver')['sql']

    # Both dialects narrow the int column (1..3 fits in tinyint).
    assert 'tinyint' in ddl_duck.lower()
    assert 'tinyint' in ddl_mssql.lower()
    assert 'date' in ddl_duck.lower() and 'date' in ddl_mssql.lower()
    assert 'varchar' in ddl_duck.lower() and 'varchar' in ddl_mssql.lower()
