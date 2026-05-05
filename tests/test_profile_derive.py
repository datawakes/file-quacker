"""Column profiler + derive end-to-end."""

from __future__ import annotations

import tempfile
from pathlib import Path

from file_quacker import ingest
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
