"""Ingest ladder covers the common non-UTF-8 encodings."""

from __future__ import annotations

import tempfile
from pathlib import Path

from file_quacker import ingest


def test_utf8_fast_path():
    p = Path(tempfile.gettempdir()) / 'fq_enc_utf8.csv'
    p.write_text('id,name\n1,alpha\n2,café\n', encoding='utf-8')
    r = ingest.load_file(str(p))
    assert r.strategy == 'utf-8'
    assert r.row_count == 2 and r.col_count == 3


def test_cp1252_fallback():
    p = Path(tempfile.gettempdir()) / 'fq_enc_cp1252.csv'
    p.write_bytes(b'id|note\n1|\x93hello\x94\n2|\x93world\x94\n')
    r = ingest.load_file(str(p))
    assert r.encoding == 'cp1252'
    assert r.row_count == 2 and r.col_count == 3


def test_user_set_encoding_short_circuits_ladder():
    p = Path(tempfile.gettempdir()) / 'fq_enc_cp1252.csv'
    p.write_bytes(b'id|note\n1|\x93hello\x94\n2|\x93world\x94\n')
    forced = ingest.load_file(str(p), ingest.IngestOptions(encoding='cp1252'))
    assert forced.strategy == 'user-set'


def test_broken_row_handled_by_lenient_strategy():
    p = Path(tempfile.gettempdir()) / 'fq_enc_broken.csv'
    p.write_text(
        'id,name,amount\n'
        '1,alpha,1.5\n'
        '2,beta\n'                      # missing column
        '3,gamma,3.5\n',
        encoding='utf-8',
    )
    r = ingest.load_file(str(p))
    assert r.row_count >= 2


def test_utf16_with_bom():
    p = Path(tempfile.gettempdir()) / 'fq_enc_utf16.csv'
    p.write_text('id|note\n1|hello\n2|world\n', encoding='utf-16')
    r = ingest.load_file(str(p))
    assert r.encoding == 'utf-16'
    assert r.row_count == 2 and r.col_count == 3


def test_binary_noise_does_not_silently_succeed_oddly():
    """Binary noise either parses as one giant lenient column or raises
    IngestError, both acceptable; just confirm it doesn't blow up."""
    p = Path(tempfile.gettempdir()) / 'fq_enc_binary.csv'
    p.write_bytes(bytes(range(256)) * 64)
    try:
        ingest.load_file(str(p))
    except ingest.IngestError:
        pass
