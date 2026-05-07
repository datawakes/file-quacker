"""auto_derive + generate_ddl end-to-end."""

from __future__ import annotations

import re
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


def test_ddl_decimal_sized_from_raw_varchar():
    """generate_ddl on a raw VARCHAR table sizes DECIMAL straight from the
    values: max integer digits and max trimmed scale. The has_dot floor lifts
    `1.00`-padded columns to scale 1 so a VARCHAR-to-decimal export bind
    works (a tinyint bind on '1.00' would fail).
    """
    p = Path(tempfile.gettempdir()) / 'fq_ddl_decimal.csv'
    p.write_text(
        'fee,padded_int,big_amt\n'
        '0.4500,1.00,1234.5\n'
        '0.5000,2.00,99.5\n'
        '1.2500,3.00,500.5\n',
        encoding='utf-8',
    )
    r = ingest.load_file(str(p))
    ddl = Api().generate_ddl(r.name, 'sqlserver')['sql'].lower()

    # '0.4500' trims to '45' (scale 2); '0.5000' to '5' (scale 1); '1.2500'
    # to '25' (scale 2). max scale = 2, max int = 1, precision = 3.
    assert re.search(r'\bfee\b\s+decimal\(3,\s*2\)', ddl), ddl
    # Padded integers: trim gives scale 0 for every value; has_dot floor
    # lifts scale to 1 so the column doesn't collapse to tinyint.
    assert re.search(r'\bpadded_int\b\s+decimal\(2,\s*1\)', ddl), ddl
    # max int = 4 ('1234'), max scale = 1, precision = 5.
    assert re.search(r'\bbig_amt\b\s+decimal\(5,\s*1\)', ddl), ddl
