"""Raw-fidelity ingest + escape handling on CSV load."""

from __future__ import annotations

import tempfile
from pathlib import Path

from file_quacker import db, ingest


def test_raw_fidelity_csv():
    p = Path(tempfile.gettempdir()) / 'fq_smoke.csv'
    p.write_text(
        'id,dob,code,note\n'
        '1,2/2/24,007,"he said ""hi"""\n'
        '2,2024-02-02,"$1,000.00",plain\n'
        '3,,,empty\n',
        encoding='utf-8',
    )
    r = ingest.load_file(str(p))
    rows = db.conn().execute(f'select * from {db.quote_ident(r.name)}').fetchall()

    assert len(rows) == 3
    assert rows[0] == (1, '1', '2/2/24', '007', 'he said "hi"')
    assert rows[1] == (2, '2', '2024-02-02', '$1,000.00', 'plain')
    assert rows[2] == (3, '3', None, None, 'empty')
