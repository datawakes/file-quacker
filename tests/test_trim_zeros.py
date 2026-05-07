"""DECIMAL trim-zeros + effective-integer detection."""

from __future__ import annotations

import tempfile
from pathlib import Path

from file_quacker import ingest
from file_quacker.api import Api


def _suggestions_for(csv: str) -> dict[str, str]:
    p = Path(tempfile.gettempdir()) / 'fq_trim_zeros.csv'
    p.write_text(csv, encoding='utf-8')
    r = ingest.load_file(str(p))
    return {s['source']: s['suggested_type'] for s in Api().suggest_casts(r.name)}


def test_whole_numbers_with_trailing_zeros_become_bigint():
    actual = _suggestions_for(
        'qty,price,rate\n'
        '15.00,1.50,0.100\n'
        '50.00,2.50,0.200\n'
        '100.00,3.50,0.300\n'
    )
    assert actual['qty']   == 'BIGINT'
    assert actual['price'] == 'DECIMAL(2,1)'
    assert actual['rate']  == 'DECIMAL(2,1)'


def test_real_fractional_precision():
    actual = _suggestions_for('x\n1.5\n2.25\n3.125\n')
    assert actual['x'] == 'DECIMAL(4,3)'


def test_csv_artifact_inflates_scale_unprotected():
    """Non-Excel sources are trusted as-is. Excel files are handled during
    ingest (see test_excel_snap.py).
    """
    actual = _suggestions_for(
        'sec_ince_amt\n'
        '15\n50\n290.08\n290.08\n100.39\n0\n24.97\n130\n130\n3.66666666666667\n'
    )
    assert actual['sec_ince_amt'] == 'DECIMAL(17,14)'
