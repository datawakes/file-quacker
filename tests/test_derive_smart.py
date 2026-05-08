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


def _suggest_for(csv: str) -> dict[str, dict]:
    p = Path(tempfile.gettempdir()) / 'fq_midnight_src.csv'
    p.write_text(csv, encoding='utf-8')
    r = ingest.load_file(str(p))
    return {s['source']: s for s in Api().suggest_casts(r.name)}


def test_iso_timestamp_all_midnight_downgrades_to_date():
    """A column of ISO timestamps where every value is at midnight should
    suggest DATE instead of TIMESTAMP, and the cast goes through TIMESTAMP
    first so '2024-01-15 00:00:00' parses correctly."""
    suggestions = _suggest_for(
        'created_at\n'
        '2024-01-15 00:00:00\n'
        '2024-02-02 00:00:00\n'
        '1999-12-31 00:00:00\n'
    )
    s = suggestions['created_at']
    assert s['suggested_type'] == 'DATE'
    assert s['via_timestamp'] is True
    assert s['detected_date_format'] is None


def test_iso_timestamp_with_real_time_stays_timestamp():
    """Mixing in even one non-midnight value keeps the column as TIMESTAMP."""
    suggestions = _suggest_for(
        'created_at\n'
        '2024-01-15 00:00:00\n'
        '2024-02-02 09:30:15\n'
        '1999-12-31 00:00:00\n'
    )
    s = suggestions['created_at']
    assert s['suggested_type'] == 'TIMESTAMP'
    assert s['via_timestamp'] is False


def test_non_iso_timestamp_all_midnight_downgrades_keeping_format():
    """Cascade-format timestamps that all sit at midnight downgrade to DATE
    while keeping the strptime format, so the cast still parses the time
    portion of the source string."""
    suggestions = _suggest_for(
        'created_at\n'
        '01/15/2024 00:00:00\n'
        '02/02/2024 00:00:00\n'
        '12/31/1999 00:00:00\n'
    )
    s = suggestions['created_at']
    assert s['suggested_type'] == 'DATE'
    assert s['detected_date_format'] == '%m/%d/%Y %H:%M:%S'
    assert s['via_timestamp'] is False


def test_midnight_downgrade_round_trips_via_auto_derive():
    """auto_derive on a midnight-only TIMESTAMP column lands a real DATE
    column with the dates intact and the time portion dropped."""
    p = Path(tempfile.gettempdir()) / 'fq_midnight_auto.csv'
    p.write_text(
        'created_at\n'
        '2024-01-15 00:00:00\n'
        '2024-02-02 00:00:00\n',
        encoding='utf-8',
    )
    r = ingest.load_file(str(p))
    api = Api()
    out = api.auto_derive(r.name, f'{r.name}_typed')
    (col_type,) = db.conn().execute(f"""
        select  column_type
        from    (describe {db.quote_ident(out['name'])})
        where   column_name = 'created_at'
    """).fetchone()
    assert col_type.upper() == 'DATE'
    rows = db.conn().execute(
        f"select cast(created_at as varchar) from {db.quote_ident(out['name'])} "
        f"order by _src_row_num"
    ).fetchall()
    assert [r[0] for r in rows] == ['2024-01-15', '2024-02-02']
