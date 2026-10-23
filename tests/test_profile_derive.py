"""Column profiler + derive end-to-end."""

from __future__ import annotations

import tempfile
from pathlib import Path

from file_quacker import db, ingest, profile
from file_quacker.api import Api


def test_profile_and_derive_round_trip():
    p = Path(tempfile.gettempdir()) / 'fq_profile_smoke.csv'
    p.write_text(
        'id,dob,note,amount\n'
        '1,2024-01-15,hello,12.50\n'
        '2,2024-02-02,,-3.25\n'
        '3,1999-12-31,  spaces  ,0\n'
        '4,2024-03-01,plain,1e6\n'
        '5,,NULL?,\n',
        encoding='utf-8',
    )
    r = ingest.load_file(str(p))
    api = Api()

    prof = api.profile_column(r.name, 'id')
    assert prof['kind'] == 'varchar'
    assert prof['branch']['type_sniff']['integer_pct'] > 90

    prof = api.profile_column(r.name, 'dob')
    assert prof['branch']['type_sniff']['date_pct'] > 70

    suggestions = api.suggest_casts(r.name)
    by_src = {s['source']: s for s in suggestions}
    assert by_src['id']['suggested_type'] == 'BIGINT'

    casts = [
        {'source': 'id',  'target': 'id',  'cast_to': 'BIGINT', 'strict': False},
        {'source': 'dob', 'target': 'dob', 'cast_to': 'DATE',   'strict': False},
        {'source': 'note', 'target': 'note', 'cast_to': None, 'strict': False},
    ]
    prev = api.preview_derive(r.name, casts, limit=5)
    assert [c['name'] for c in prev['columns']] == ['id', 'dob', 'note']

    created = api.create_derived_table(r.name, casts=casts, new_name=f'{r.name}_typed')
    assert created['ok']


def test_profile_untyped_null_column_in_view():
    """DuckDB 2.0 types `select null as x` as "NULL"; it profiles as an
    all-null varchar instead of falling through to 'other'."""
    db.conn().execute("""
        create or replace view prof_null_view as
        select  range   as id
        ,       null    as nothing
        from    range(4)
        ;
    """)
    prof = profile.profile_table('prof_null_view', 'nothing')
    assert prof['kind'] == 'varchar'
    assert prof['common']['null_count'] == 4
    assert prof['common']['top_values'] == []
    assert prof['branch']['min_length'] is None
    assert prof['branch']['leading_or_trailing_unicode_blank_count'] == 0
    assert prof['branch']['type_sniff']['integer_pct'] == 0.0


def test_profile_counts_unicode_edge_blanks():
    values = ['\u00a0x', 'x\u2009', '\tx', ' x ', 'caf\u00e9', 'a\u00a0b', 'x']
    rows = ', '.join(f"('{v}')" for v in values)
    prof = profile.profile_query(f'select v from (values {rows}) t(v)', 'v')
    branch = prof['branch']
    # NBSP and thin space at an edge; the inner NBSP and the accent don't count.
    assert branch['leading_or_trailing_unicode_blank_count'] == 2
    # DuckDB's trim() also strips Unicode spaces, but not the tab.
    assert branch['leading_or_trailing_space_count'] == 3
