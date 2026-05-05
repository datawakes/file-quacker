"""DATE-vs-TIMESTAMP suggestion picks TIMESTAMP for time-bearing values."""

from __future__ import annotations

import tempfile
from pathlib import Path

from file_quacker import ingest
from file_quacker.api import Api


def test_lossless_date_keeps_timestamp_for_time_bearing():
    p = Path(tempfile.gettempdir()) / 'fq_date_vs_ts.csv'
    p.write_text(
        'pure_date,iso_ts\n'
        '2024-01-15,2025-04-22T21:56:56.9531568+00:00\n'
        '2024-02-02,2025-04-22T22:01:12.1000000+00:00\n'
        '1999-12-31,2025-05-01T03:30:00.0000000+00:00\n',
        encoding='utf-8',
    )
    r = ingest.load_file(str(p))
    api = Api()

    suggestions = {s['source']: s for s in api.suggest_casts(r.name)}
    assert suggestions['pure_date']['suggested_type'] == 'DATE'
    assert suggestions['iso_ts']['suggested_type'] == 'TIMESTAMP'
