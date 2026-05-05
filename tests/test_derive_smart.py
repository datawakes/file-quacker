"""Upgraded derive inference: strict int, DECIMAL(P,S), ISO + non-ISO dates."""

from __future__ import annotations

import tempfile
from pathlib import Path

from file_quacker import db, ingest
from file_quacker.api import Api


def test_smart_inference_paths():
    p = Path(tempfile.gettempdir()) / 'fq_smart_derive.csv'
    lines = [
        'us_slash|us_dash|iso_date|named|compact|iso_ts|iso_tz|zulu|price|floaty|strict_int|loose_int',
        '2/14/24|02-14-2024|2024-02-14|Feb 14, 2024|20240214|2024-02-14T10:30:15|2024-04-22T21:56:56.9531568+00:00|2024-04-22T21:56:56Z|12.50|1.0|1|1',
        '3/1/24|03-01-2024|2024-03-01|Mar 1, 2024|20240301|2024-03-01T09:15:00|2024-05-01T03:30:00.0000000+00:00|2024-05-01T03:30:00Z|9.99|2.0|2|2.0',
        '12/31/99|12-31-1999|1999-12-31|Dec 31, 1999|19991231|1999-12-31T23:59:59|1999-12-31T23:59:59.1234567+00:00|1999-12-31T23:59:59Z|1234.56|3.5|3|3',
    ]
    p.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    r = ingest.load_file(str(p), ingest.IngestOptions(delimiter='|'))
    api = Api()
    suggestions = {s['source']: s for s in api.suggest_casts(r.name)}

    assert suggestions['us_slash']['suggested_type'] == 'DATE'
    assert suggestions['us_slash']['detected_date_format'] == '%m/%d/%y'
    assert suggestions['us_dash']['suggested_type'] == 'DATE'
    assert suggestions['us_dash']['detected_date_format'] == '%m-%d-%Y'
    assert suggestions['named']['suggested_type'] == 'DATE'
    assert suggestions['named']['detected_date_format'] == '%b %d, %Y'

    assert suggestions['iso_date']['suggested_type'] == 'DATE'
    assert suggestions['iso_date']['detected_date_format'] is None
    assert suggestions['iso_ts']['suggested_type'] == 'TIMESTAMP'
    assert suggestions['iso_ts']['detected_date_format'] is None
    assert suggestions['iso_tz']['suggested_type'] == 'TIMESTAMP'
    assert suggestions['iso_tz']['detected_date_format'] is None
    assert suggestions['zulu']['suggested_type'] == 'TIMESTAMP'
    assert suggestions['zulu']['detected_date_format'] is None

    assert suggestions['price']['suggested_type'] == 'DECIMAL(6,2)'
    assert suggestions['strict_int']['suggested_type'] == 'BIGINT'
    assert suggestions['floaty']['suggested_type'] != 'BIGINT'
    assert suggestions['loose_int']['suggested_type'] == 'BIGINT'

    derived_name = f'{r.name}_derived'
    created = api.auto_derive(r.name, derived_name)
    assert created['ok']
    schema_rows = db.conn().execute(f'describe {db.quote_ident(derived_name)}').fetchall()
    derived_types = {row[0]: row[1].upper() for row in schema_rows}

    for col, expected in [
        ('us_slash', 'DATE'),
        ('us_dash', 'DATE'),
        ('iso_date', 'DATE'),
        ('named', 'DATE'),
        ('iso_ts', 'TIMESTAMP'),
        ('iso_tz', 'TIMESTAMP'),
        ('zulu', 'TIMESTAMP'),
        ('strict_int', 'BIGINT'),
    ]:
        assert expected in derived_types.get(col, '<missing>')

    for col in ('us_slash', 'iso_tz', 'zulu', 'price'):
        (non_null,) = db.conn().execute(
            f'select count({db.quote_ident(col)}) from {db.quote_ident(derived_name)}'
        ).fetchone()
        assert non_null == 3
