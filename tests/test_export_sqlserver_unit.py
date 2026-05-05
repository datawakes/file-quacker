"""Pure unit checks for the SQL Server export path: snake_case + bracket-quoting.

No external services; safe to run in default CI."""

from __future__ import annotations

import pytest

from file_quacker import export


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
