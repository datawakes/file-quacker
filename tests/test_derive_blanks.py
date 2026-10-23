"""Derive reads values with edge blanks (NBSP and friends) cut off."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from file_quacker import db, derive
from file_quacker.text_values import WS_TRIM_PATTERN


def _table(name: str, cols: list[str], rows: list[tuple]) -> None:
    c = db.conn()
    qname = db.quote_ident(name)
    c.execute(f'create or replace table {qname} ({", ".join(f"{x} varchar" for x in cols)})')
    c.executemany(f'insert into {qname} values ({", ".join("?" for _ in cols)})', rows)


def _suggest(name: str) -> dict[str, dict]:
    return {s['source']: s for s in derive.suggest_casts(name)}


def _column(name: str, col: str) -> list:
    return [r[0] for r in db.conn().execute(
        f'select {col} from {db.quote_ident(name)} order by rowid').fetchall()]


def test_padded_values_type_and_cast():
    _table('blk_src', ['amt', 'qty', 'iso', 'us', 'code', 'txt'], [
        ('\u00a012.50 ', ' 5',   ' 2024-01-15',      '2/14/24\u00a0', '007',       ' a '),
        ('3.25',         '6\u00a0', '2024-02-01\u3000', ' 3/1/24',    '\u00a0008', 'b'),
        ('1234.5\u202f', '7\t',  '2024-03-01',       '12/31/99',      '009',       'c'),
    ])
    s = _suggest('blk_src')
    assert s['amt']['suggested_type'] == 'DECIMAL(6,2)'
    assert s['qty']['suggested_type'] == 'BIGINT'
    assert s['iso']['suggested_type'] == 'DATE'
    assert s['us']['suggested_type'] == 'DATE'
    assert s['us']['detected_date_format'] == '%m/%d/%y'
    # Leading zeros still mean an identifier, padded or not.
    assert s['code']['suggested_type'] is None
    assert s['txt']['suggested_type'] is None

    derive.auto_derive('blk_src', 'blk_typed')
    assert _column('blk_typed', 'amt') == [Decimal('12.50'), Decimal('3.25'), Decimal('1234.50')]
    assert _column('blk_typed', 'qty') == [5, 6, 7]
    assert _column('blk_typed', 'iso') == [dt.date(2024, 1, 15), dt.date(2024, 2, 1), dt.date(2024, 3, 1)]
    assert _column('blk_typed', 'us') == [dt.date(2024, 2, 14), dt.date(2024, 3, 1), dt.date(1999, 12, 31)]
    # Columns that stay VARCHAR keep their raw text.
    assert _column('blk_typed', 'code') == ['007', '\u00a0008', '009']
    assert _column('blk_typed', 'txt') == [' a ', 'b', 'c']


def test_padded_minority_still_found():
    """One value in ten carries a trailing NBSP: 90% raw coverage, below the
    threshold, so the analysis has to read the column stripped."""
    rows = [(f'{i}.5\u00a0' if i % 10 == 0 else f'{i}.5',) for i in range(1, 1001)]
    _table('blk_minority', ['v'], rows)
    s = _suggest('blk_minority')
    assert s['v']['suggested_type'] == 'DECIMAL(5,1)'
    assert s['v']['cast_coverage']['DECIMAL'] == 100.0
    derive.auto_derive('blk_minority', 'blk_minority_typed')
    (n,) = db.conn().execute('select count(v) from blk_minority_typed').fetchone()
    assert n == 1000


def test_strict_cast_reads_padded_values():
    _table('blk_strict', ['v', 'd'], [('\u00a012 ', ' 2/14/24'), ('  ', '3/1/24\u00a0')])
    casts = [
        derive.CastSpec(source='v', target='v', cast_to='BIGINT', strict=True),
        derive.CastSpec(source='d', target='d', cast_to='DATE', strict=True, date_format='%m/%d/%y'),
    ]
    rows = derive.preview_derive('blk_strict', casts, None)['rows']
    assert rows == [[12, '2024-02-14'], [None, '2024-03-01']]


def test_placeholders_count_against_coverage():
    """'N/A' and friends stay non-null in the coverage math (the 95% threshold
    absorbs a sprinkle); all-blank values count as null."""
    sprinkle = [(str(i),) for i in range(1, 100)] + [('N/A',)]
    _table('blk_na_few', ['v'], sprinkle)
    assert _suggest('blk_na_few')['v']['suggested_type'] == 'BIGINT'
    derive.auto_derive('blk_na_few', 'blk_na_few_typed')
    assert _column('blk_na_few_typed', 'v')[-1] is None

    lots = [(str(i),) for i in range(1, 81)] + [('N/A',)] * 10 + [('-',)] * 10
    _table('blk_na_lots', ['v'], lots)
    assert _suggest('blk_na_lots')['v']['suggested_type'] is None

    blanks = [(str(i),) for i in range(1, 81)] + [('   ',)] * 10 + [('\u00a0',)] * 10
    _table('blk_blank', ['v'], blanks)
    s = _suggest('blk_blank')['v']
    assert s['suggested_type'] == 'BIGINT'
    assert s['cast_coverage']['BIGINT'] == 100.0


def test_strict_analysis_keeps_raw_values_by_default():
    """The SQL Server export path calls `_analyze_one` directly and binds raw
    text, so it only strips when asked."""
    _table('blk_raw', ['v'], [('\u00a012',), ('\u00a013',), ('\u00a014',)])
    qtable = db.quote_ident('blk_raw')
    raw = derive._analyze_one(qtable, 'v')
    assert derive._pick_suggestion(raw, threshold=derive.STRICT_THRESHOLD)[0] is None
    stripped = derive._analyze_one(qtable, 'v', strip_blanks=True)
    assert derive._pick_suggestion(stripped, threshold=derive.STRICT_THRESHOLD)[0] == 'BIGINT'


def test_derived_table_from_view_has_no_null_type():
    c = db.conn()
    c.execute("""
        create or replace view blk_null_view as
        select  cast(range as varchar)  as id
        ,       null                    as nothing
        from    range(3)
        ;
    """)
    derive.auto_derive('blk_null_view', 'blk_null_typed')
    types = dict(c.execute('select column_name, column_type from (describe blk_null_typed)').fetchall())
    assert types == {'id': 'BIGINT', 'nothing': 'VARCHAR'}


def test_only_padded_columns_get_the_stripped_retry():
    """Clean columns keep the plain cast (and readable SQL); padded ones
    retry stripped values; a spec that doesn't say retries to be safe."""
    _table('blk_mixed', ['clean', 'padded'], [('1', ' 1'), ('2', '2\u00a0'), ('3', '3')])
    s = _suggest('blk_mixed')
    assert s['clean']['strip_blanks'] is False
    assert s['padded']['strip_blanks'] is True

    sql = derive.auto_derive('blk_mixed', 'blk_mixed_typed')['sql']
    assert 'try_cast(clean as bigint)  as clean' in sql
    assert 'case when try_cast(padded as bigint) is null' in sql
    assert _column('blk_mixed_typed', 'padded') == [1, 2, 3]

    unknown = derive.CastSpec(source='clean', target='clean', cast_to='BIGINT')
    told = derive.CastSpec(source='clean', target='clean', cast_to='BIGINT', strip_blanks=False)
    assert 'regexp_replace' in derive.derive_sql('blk_mixed', 'x', [unknown], None)
    assert 'regexp_replace' not in derive.derive_sql('blk_mixed', 'x', [told], None)


def test_python_strip_matches_the_trim_pattern():
    """The sniff strips its sample in Python; str.strip() has to cut the
    same set the SQL trim pattern does."""
    (sql_set,) = db.conn().execute("""
        select  list(cp order by cp)
        from    range(1, 55296) t(cp)
        where   regexp_matches(chr(cast(cp as integer)), ?)
        ;
    """, [WS_TRIM_PATTERN]).fetchone()
    py_set = [cp for cp in range(1, 55296) if ('x' + chr(cp)).strip() == 'x']
    assert sql_set == py_set


def test_padding_the_sniff_sample_misses():
    """100k numbers, 500 of them with a trailing NBSP and none at the sample
    edges: the near-miss check finds them, coverage reads 100% and the
    clone keeps every value."""
    vals = [f'{i}\u00a0' if i % 200 == 50 else str(i) for i in range(100000, 200000)]
    c = db.conn()
    c.execute('create or replace table blk_hidden (v varchar)')
    c.execute('insert into blk_hidden select unnest(?)', [vals])
    s = _suggest('blk_hidden')['v']
    assert s['suggested_type'] == 'BIGINT'
    assert s['cast_coverage']['BIGINT'] == 100.0
    assert s['strip_blanks'] is True
    derive.auto_derive('blk_hidden', 'blk_hidden_typed')
    (nulls,) = c.execute('select count(*) - count(v) from blk_hidden_typed').fetchone()
    assert nulls == 0


def test_under_full_coverage_keeps_the_stripped_retry():
    """A column typed below 100% may hold values the plain cast can't read,
    so its cast retries stripped values even without padding in sight."""
    _table('blk_partial', ['v'], [(str(i),) for i in range(1, 100)] + [('N/A',)])
    s = _suggest('blk_partial')['v']
    assert s['suggested_type'] == 'BIGINT'
    assert s['strip_blanks'] is True
