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


def test_ddl_renders_date_for_typed_timestamp_all_midnight():
    """A typed TIMESTAMP column whose values all sit at midnight renders
    as DATE in DDL — no point padding the column with `00:00:00` time
    components on every row."""
    p = Path(tempfile.gettempdir()) / 'fq_ddl_midnight.csv'
    p.write_text(
        'created_at\n'
        '2024-01-15 00:00:00\n'
        '2024-02-02 00:00:00\n',
        encoding='utf-8',
    )
    r = ingest.load_file(str(p))
    api = Api()
    typed = api.auto_derive(r.name, f'{r.name}_typed')
    ddl_mssql = api.generate_ddl(typed['name'], 'sqlserver')['sql'].lower()
    ddl_duck  = api.generate_ddl(typed['name'], 'duckdb')['sql'].lower()
    assert 'date' in ddl_mssql and 'datetime' not in ddl_mssql
    assert 'date' in ddl_duck and 'timestamp' not in ddl_duck


def test_string_columns_sized_from_trimmed_lengths_when_trim_on():
    """A column of uniformly-padded values used to size as char(N) on the
    raw length, then SQL Server's char padding put the whitespace right
    back as trailing spaces. The probe now respects the trim flag and
    sizes from trimmed lengths so the destination column actually fits
    the trimmed values."""
    p = Path(tempfile.gettempdir()) / 'fq_trim_size.csv'
    p.write_text(
        'flag,note\n'
        ' Y N , abc \n'
        ' Y N , def \n'
        ' Y N , ghi \n',
        encoding='utf-8',
    )
    r = ingest.load_file(str(p))
    api = Api()
    src = {'kind': 'table', 'name': r.name, 'sql': None}
    trimmed = {m['source']: m['sql_type'] for m in api.suggest_export_mappings(src, True)}
    raw = {m['source']: m['sql_type'] for m in api.suggest_export_mappings(src, False)}
    assert trimmed['flag'] == 'char(3)'  # 'Y N' after trim
    assert trimmed['note'] == 'char(3)'  # 'abc'/'def'/'ghi' after trim
    assert raw['flag'] == 'char(5)'      # ' Y N '
    assert raw['note'] == 'char(5)'      # ' abc '/' def '/' ghi '


def test_string_columns_with_trailing_newlines_size_correctly_after_trim():
    """Reported case: source values end in \\r\\n and the column got
    sized to char(N) including the newline length, then SQL Server
    padded the trimmed values back to N with spaces."""
    p = Path(tempfile.gettempdir()) / 'fq_trim_newline.csv'
    p.write_text(
        'code\n"abc\r\n"\n"def\r\n"\n"ghi\r\n"\n',
        encoding='utf-8',
    )
    r = ingest.load_file(str(p))
    api = Api()
    src = {'kind': 'table', 'name': r.name, 'sql': None}
    trimmed = {m['source']: m['sql_type'] for m in api.suggest_export_mappings(src, True)}
    assert trimmed['code'] == 'char(3)'


def test_alphanumeric_outlier_at_scale_falls_through_to_varchar():
    """At ~100k rows, one stray alphanumeric value gives 99.999% numeric
    coverage. Plain percentage rounding would push that up to 100.0 and
    the strict threshold check would treat the column as numeric, so the
    column landed as decimal(38, 10) and blew up at export. The pct()
    clamp keeps 100.0 for true-100% only.
    """
    n_clean = 100_000
    p = Path(tempfile.gettempdir()) / 'fq_alpha_scale.csv'
    rows = ['lot_number'] + [str(i + 10000) for i in range(n_clean)] + ['f7039']
    p.write_text('\n'.join(rows) + '\n', encoding='utf-8')
    r = ingest.load_file(str(p))
    ddl = Api().generate_ddl(r.name, 'sqlserver')['sql'].lower()
    assert 'numeric' not in ddl
    assert 'decimal' not in ddl
    assert re.search(r'\blot_number\b\s+(varchar|char)', ddl), ddl


def test_yes_no_column_renders_as_varchar_for_sqlserver():
    """A column of Yes/No values – which DuckDB happily casts to bool but
    SQL Server's bit rejects on insert – should land as varchar in the
    SQL Server DDL. DuckDB DDL keeps the boolean suggestion since DuckDB's
    bool type accepts the wider set of strings."""
    p = Path(tempfile.gettempdir()) / 'fq_yes_no.csv'
    p.write_text(
        'is_active\nYes\nNo\nYes\nYes\nNo\n',
        encoding='utf-8',
    )
    r = ingest.load_file(str(p))
    api = Api()
    ddl_mssql = api.generate_ddl(r.name, 'sqlserver')['sql'].lower()
    ddl_duck  = api.generate_ddl(r.name, 'duckdb')['sql'].lower()

    assert 'is_active' in ddl_mssql
    assert 'bit' not in ddl_mssql
    assert re.search(r'\bis_active\b\s+varchar\(\d+\)', ddl_mssql), ddl_mssql
    # DuckDB keeps the wider boolean acceptance.
    assert 'boolean' in ddl_duck


def test_zero_one_column_still_renders_as_bit_for_sqlserver():
    """A column of strict 0/1/true/false strings is still safe as bit –
    only the wider DuckDB-style boolean strings get downgraded."""
    p = Path(tempfile.gettempdir()) / 'fq_one_zero.csv'
    p.write_text(
        'flag\n1\n0\n1\nTrue\nfalse\n',
        encoding='utf-8',
    )
    r = ingest.load_file(str(p))
    ddl_mssql = Api().generate_ddl(r.name, 'sqlserver')['sql'].lower()
    assert re.search(r'\bflag\b\s+bit', ddl_mssql), ddl_mssql


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
