"""SQL Server export typing follows the trim option: trimmed exports bind
stripped values, so padded numbers and dates get typed; raw exports don't."""

from __future__ import annotations

from file_quacker import db, ddl, export


def _table(name: str, cols: list[str], rows: list[tuple]) -> dict:
    c = db.conn()
    qname = db.quote_ident(name)
    c.execute(f'create or replace table {qname} ({", ".join(f"{x} varchar" for x in cols)})')
    c.executemany(f'insert into {qname} values ({", ".join("?" for _ in cols)})', rows)
    return {'kind': 'table', 'name': name, 'sql': None}


def _mappings(src: dict, trim: bool) -> dict[str, dict]:
    return {m['source']: m for m in export.suggest_mappings(src, trim_strings=trim)}


def test_padded_values_typed_only_when_export_trims():
    src = _table('ss_padded', ['amt', 'day', 'ts', 'plain'], [
        ('\u00a012.50', ' 2024-01-15',       '2024-01-15 10:30:00\u00a0', '1.5'),
        ('3.25 ',       '2024-02-01\u00a0', ' 2024-02-01 08:00:00',      '2.5'),
        ('\u00a01234.5', '2024-03-01',      '2024-03-01 09:15:00',       '3.5'),
    ])
    on = _mappings(src, trim=True)
    assert on['amt']['sql_type'] == 'decimal(6, 2)'
    assert on['day']['sql_type'] == 'date'
    assert on['ts']['sql_type'].startswith('datetime')
    assert on['plain']['sql_type'] == 'decimal(2, 1)'

    off = _mappings(src, trim=False)
    for col in ('amt', 'day', 'ts'):
        assert 'char' in off[col]['sql_type'], off[col]
    assert off['plain']['sql_type'] == 'decimal(2, 1)'


def test_blank_values_make_a_trimmed_column_nullable():
    """'  ' and NBSP-only cells export as NULL with trim on, so the typed
    column has to allow nulls; with trim off they keep the column text."""
    src = _table('ss_blanks', ['n'], [('1',), ('  ',), ('\u00a03',), ('\u00a0',)])
    on = _mappings(src, trim=True)['n']
    assert on['sql_type'] == 'tinyint'
    assert on['nullable'] is True

    off = _mappings(src, trim=False)['n']
    assert 'char' in off['sql_type']
    assert off['nullable'] is False


def test_standalone_ddl_keeps_raw_typing():
    """The Generate DDL panel has no trim option and stays raw."""
    _table('ss_ddl_raw', ['amt'], [('\u00a012.50',), ('3.25',)])
    sql = ddl.generate('ss_ddl_raw', 'sqlserver').lower()
    assert 'decimal' not in sql
