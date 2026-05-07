"""Pure unit checks for the SQL Server export path: snake_case + bracket-quoting.

No external services; safe to run in default CI."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from file_quacker import db, export, ingest


@pytest.mark.parametrize('src,expected', [
    ('Patient_ID',       'patient_id'),
    ('firstName',        'first_name'),
    ('Has Spaces',       'has_spaces'),
    ('already_snake',    'already_snake'),
    ('CamelCaseColumn',  'camel_case_column'),
    ('HTTPResponseCode', 'http_response_code'),
    ('col.with.dots',    'col_with_dots'),
    ('USD',              'usd'),
])
def test_snake_case(src, expected):
    assert export._to_snake_case(src) == expected


@pytest.mark.parametrize('src,expected', [
    ('foo',          'foo'),
    ('patient_id',   'patient_id'),
    ('_src_row_num', '_src_row_num'),
    ('weird col',    '[weird col]'),
    ('bracket]evil', '[bracket]]evil]'),
    ('foo]; drop x', '[foo]]; drop x]'),
])
def test_qident_ss(src, expected):
    assert export._qident_ss(src) == expected


def test_unique_target_first_occurrence_kept():
    taken: set[str] = set()
    assert export._unique_target('foo', taken) == 'foo'
    taken.add('foo')
    assert export._unique_target('foo', taken) == 'foo_1'


def test_unique_target_cascades_when_suffix_taken():
    """If `foo_1` is already taken, the next collision becomes `foo_2`."""
    taken = {'foo', 'foo_1'}
    assert export._unique_target('foo', taken) == 'foo_2'


def test_unique_target_handles_existing_suffix_source():
    """A literal source named `foo_1` that comes after two `foo`s gets
    `foo_1_1` because `foo_1` was already claimed by the de-duper."""
    taken: set[str] = set()
    t1 = export._unique_target('foo', taken); taken.add(t1)
    t2 = export._unique_target('foo', taken); taken.add(t2)
    t3 = export._unique_target('foo_1', taken); taken.add(t3)
    assert (t1, t2, t3) == ('foo', 'foo_1', 'foo_1_1')


def test_suggest_mappings_dedupes_targets():
    """Source columns whose snake-cased names collide get unique target
    names so the SQL Server CREATE TABLE doesn't reject duplicates. The
    first occurrence keeps its name; later collisions get _1, _2, ..."""
    src_path = Path(tempfile.gettempdir()) / 'fq_dedupe_src.csv'
    src_path.write_text('a,b,c\n1,2,3\n', encoding='utf-8')
    r = ingest.load_file(str(src_path))
    # DuckDB requires unique column names on tables, so build a query-source
    # whose SELECT projects the same name three different ways.
    sql = (
        f'select  a as foo'
        f',       b as Foo'
        f',       c as FOO'
        f'  from "{r.name}"'
    )
    src = {'kind': 'query', 'name': None, 'sql': sql}
    suggestions = export.suggest_mappings(src)
    targets = [s['target'] for s in suggestions]
    assert targets == ['foo', 'foo_1', 'foo_2'], targets


def _mappings(*pairs: tuple[str, str]) -> list[export.ColumnMapping]:
    """Build ColumnMapping list from (target, sql_type) pairs."""
    return [
        export.ColumnMapping(source=t, target=t, sql_type=ty,
                             nullable=True, source_type='VARCHAR')
        for t, ty in pairs
    ]


def test_mark_offending_columns_falls_back_to_per_column_parse():
    """SQL Server error 8114 has no value snippet to match against;
    the fallback should still flag the column whose value doesn't
    parse as its numeric target."""
    mappings = _mappings(
        ('id',          'int'),
        ('amount',      'decimal(10,2)'),
        ('description', 'nvarchar(200)'),
    )
    row = (1, 'not-a-number', 'free text')
    error = (
        '[42000] [Microsoft][ODBC Driver 18 for SQL Server][SQL Server]'
        'Error converting data type nvarchar to numeric. (8114)'
    )
    marked = export._mark_offending_columns(row, mappings, error)
    assert marked == ['amount']


def test_mark_offending_columns_int_target_with_decimal_string():
    """A '1.5' value bound to an int target trips 8114; the parse check
    correctly flags the int column (Decimal would accept it but int won't)."""
    mappings = _mappings(('qty', 'int'), ('price', 'decimal(10,2)'))
    row = ('1.5', '9.99')
    error = (
        '[42000] [Microsoft][SQL Server]Error converting data type '
        'nvarchar to int. (8114)'
    )
    marked = export._mark_offending_columns(row, mappings, error)
    assert marked == ['qty']


def test_mark_offending_columns_keeps_old_path_when_value_in_error():
    """The legacy 22018 path with a `Truncated value: '...'` snippet still
    works; the fallback only kicks in when no value was matched."""
    mappings = _mappings(
        ('prescription_prior_auth_ref_id', 'int'),
        ('amount',                          'decimal(10,2)'),
    )
    row = ('XYZ', '9.99')
    error = (
        "[22018] [Microsoft][SQL Server]Invalid character value for cast "
        "specification. Truncated value: 'XYZ'"
    )
    marked = export._mark_offending_columns(row, mappings, error)
    assert marked == ['prescription_prior_auth_ref_id']


def test_describe_error_row_annotates_message_with_value_repr():
    """Marked columns get their repr appended to the error message so
    invisible characters (BOM, control chars, embedded whitespace) are
    visible — repr is the cheapest way to surface them without a
    separate ad-hoc query."""
    mappings = _mappings(('lot_number', 'numeric(10,0)'))
    row = ('12\x00ABC',)  # null byte then letters — fails numeric cast
    error = (
        '[42000] [Microsoft][SQL Server]Error converting data type '
        'nvarchar to numeric. (8114)'
    )
    result = export._describe_error_row(42, row, mappings, error)
    assert result['marked'] == ['lot_number']
    assert "lot_number='12\\x00ABC'" in result['message']


def test_mark_offending_columns_returns_empty_when_no_signal():
    """Errors that aren't conversion-related and lack column / value
    info shouldn't pollute marked with arbitrary numeric columns."""
    mappings = _mappings(('id', 'int'))
    row = (123,)
    error = '[42000] [Microsoft][SQL Server]Some unrelated error.'
    assert export._mark_offending_columns(row, mappings, error) == []


def test_suggest_mappings_dedupe_cascades_through_existing_suffix():
    """A literal source named `foo_1` that follows two `foo` columns gets
    pushed to `foo_1_1` because `foo_1` is already claimed."""
    src_path = Path(tempfile.gettempdir()) / 'fq_dedupe_cascade.csv'
    src_path.write_text('a,b,c\n1,2,3\n', encoding='utf-8')
    r = ingest.load_file(str(src_path))
    sql = (
        f'select  a as foo'
        f',       b as Foo'
        f',       c as foo_1'
        f'  from "{r.name}"'
    )
    src = {'kind': 'query', 'name': None, 'sql': sql}
    suggestions = export.suggest_mappings(src)
    targets = [s['target'] for s in suggestions]
    assert targets == ['foo', 'foo_1', 'foo_1_1'], targets
