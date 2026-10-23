"""Keyword column names (`order`, `select`, `values`) get quoted in generated DDL."""

from __future__ import annotations

import duckdb
import pytest

from file_quacker import db, ddl, export
from file_quacker.drivers import dialect_for

KEYWORD_COLS = ('order', 'select', 'values', 'date', 'amount')


@pytest.fixture(scope='module')
def kw_table():
    db.conn().execute("""
        create or replace table kw_ddl_src as
        select  'a'                as "order"
        ,       'b'                as "select"
        ,       'c'                as "values"
        ,       date '2024-01-01'  as "date"
        ,       1                  as amount
        ;
    """)
    return 'kw_ddl_src'


@pytest.mark.parametrize('name,expected', [
    ('order', '[order]'),
    ('Select', '[Select]'),
    ('values', '[values]'),
    ('date', '[date]'),          # ODBC reserved
    ('amount', 'amount'),
    ('patient_id', 'patient_id'),
    ('x]y', '[x]]y]'),
])
def test_sqlserver_quote_ident(name, expected):
    assert dialect_for('sqlserver').quote_ident(name) == expected


@pytest.mark.parametrize('name,expected', [
    ('order', '"order"'),
    ('select', '"select"'),
    ('time', 'time'),            # DuckDB column_name keyword, fine bare
    ('amount', 'amount'),
    ('a b', '"a b"'),
])
def test_duckdb_quote_ident(name, expected):
    assert dialect_for('duckdb').quote_ident(name) == expected


def test_sqlserver_ddl_brackets_keywords(kw_table):
    text = ddl.generate(kw_table, 'sqlserver')
    names = [line.lstrip('(, ').split()[0] for line in text.splitlines()[1:-2]]
    assert names == ['[order]', '[select]', '[values]', '[date]', 'amount']


def test_duckdb_ddl_runs(kw_table):
    text = ddl.generate(kw_table, 'duckdb')
    duckdb.connect().execute(text)   # a fresh database, so the name is free


def test_export_ddl_brackets_keywords():
    exp = export.ExportOptions(
        source={'kind': 'table', 'name': 'kw_ddl_src'},
        schema='dbo',
        table_name='order',
        mappings=[export.ColumnMapping(source=n, target=n, sql_type='varchar(10)', nullable=True)
                  for n in KEYWORD_COLS],
    )
    text = export.preview_ddl(exp)
    assert text.splitlines()[0] == 'create table dbo.[order]'
    assert '[values]' in text and '[select]' in text
