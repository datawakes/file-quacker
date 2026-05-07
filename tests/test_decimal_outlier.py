"""Excel numeric artifacts are handled during ingest, not during type derivation.

Excel values are cleaned up before type probing, so the column can still be typed
tightly. CSV/text sources are not cleaned up this way and are trusted as-is (see
`test_trim_zeros.test_csv_artifact_inflates_scale_unprotected`).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import openpyxl

from file_quacker import db, ingest
from file_quacker.api import Api


def _write_xlsx(rows: list[list]) -> Path:
    p = Path(tempfile.gettempdir()) / 'fq_decimal_outlier.xlsx'
    p.unlink(missing_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    wb.save(p)
    wb.close()
    return p


def _values(table: str, col: str) -> list[str]:
    rows = db.conn().execute(
        f"select  cast({db.quote_ident(col)} as varchar) "
        f"from    {db.quote_ident(table)} "
        f"order   by _src_row_num"
    ).fetchall()
    return [r[0] for r in rows]


def test_excel_artifact_snapped_at_ingest():
    p = _write_xlsx([
        ['sec_ince_amt'],
        [15], [50], [290.08], [290.08], [100.39],
        [0], [24.97], [130], [130],
        [11 / 3],   # float artifact: 3.6666666666666665
    ])
    r = ingest.load_file(str(p))
    assert r.format == 'xlsx'

    # Working table should already be clean — the artifact is capped
    # at scale 8, not deferred to a downstream cast.
    values = _values(r.name, 'sec_ince_amt')
    fractional_lengths = [
        len(v.split('.', 1)[1]) if '.' in v else 0 for v in values
    ]
    assert max(fractional_lengths) <= 8, values
    assert '3.66666667' in values, values

    s = next(x for x in Api().suggest_casts(r.name)
             if x['source'] == 'sec_ince_amt')
    # max(scale) over snapped values is still 8 (the artifact bucket).
    # The other values top out at scale 2, but the snapped artifact still
    # sets the column scale to 8. This shows the snap is a ceiling, not
    # a removal of all higher-scale artifacts.
    assert s['suggested_type'].startswith('DECIMAL')
    assert s['suggested_type'].endswith(',8)')


def test_excel_integer_cells_stay_integer():
    """Integer-valued Excel cells emit as `'5'`, not `'5.0'`, so the
    type probe lands on a narrow int family rather than padding the
    whole column with a needless decimal point."""
    p = _write_xlsx([
        ['qty'],
        [1], [2], [3], [100],
    ])
    r = ingest.load_file(str(p))
    values = _values(r.name, 'qty')
    assert all('.' not in v for v in values), values
    s = next(x for x in Api().suggest_casts(r.name) if x['source'] == 'qty')
    assert s['suggested_type'] == 'BIGINT'


def test_excel_clean_decimal_preserved():
    """Values that already sit on a clean scale (0.30, 1.50, 0.4500)
    snap to themselves.  `0.1 + 0.2` exercises the round-to-2 bucket."""
    p = _write_xlsx([
        ['rate'],
        [0.1 + 0.2],   # 0.30000000000000004 → snaps to 0.30
        [1.5],         # exact → 1.50
        [2.25],        # exact at scale 2 → 2.25
    ])
    r = ingest.load_file(str(p))
    values = _values(r.name, 'rate')
    assert '0.30' in values, values
    assert '1.50' in values, values
    assert '2.25' in values, values


def test_excel_leading_zero_string_untouched():
    """Identifier-shaped strings with leading zeros must not be
    snapped — the regex pre-filter excludes them so they round-trip
    as text."""
    p = _write_xlsx([
        ['code'],
        ['010876'],
        ['001234'],
        ['012345'],
    ])
    r = ingest.load_file(str(p))
    values = _values(r.name, 'code')
    assert values == ['010876', '001234', '012345']


def test_excel_negative_values_snapped():
    """Negative values go through the same cascade. The minus sign is
    preserved except for negative zero, which collapses to '0'."""
    p = _write_xlsx([
        ['v'],
        [-15],            # integer
        [-290.08],        # exact at scale 2
        [-(0.1 + 0.2)],   # -0.30000000000000004 → snaps to -0.30
        [-(11 / 3)],      # artifact → -3.66666667
        [-0.0],           # negative zero → '0'
    ])
    r = ingest.load_file(str(p))
    values = _values(r.name, 'v')
    assert '-15' in values, values
    assert '-290.08' in values, values
    assert '-0.30' in values, values
    assert '-3.66666667' in values, values
    assert '0' in values, values


def test_excel_scientific_notation_rewritten_to_plain():
    """DuckDB hands very small / very large numbers back as scientific
    notation strings. The snap rewrites them to plain decimal so the
    working table is uniform and downstream type-sizing has clean values
    to look at. Magnitudes too big to fit decimal(38, N) keep their
    original text and flow through the export fallback path."""
    p = _write_xlsx([
        ['v'],
        [1.23e-5],     # comes through as '1.23e-05'  → '0.00001230'
        [4.56e-7],     # comes through as '4.56e-07'  → '0.00000046' (rounds to 8)
        [1e-15],       # below the 1e-12 floor        → '0'
        [1.23e+40],    # too big for decimal(38, 0)   → stays as '1.23e+40'
    ])
    r = ingest.load_file(str(p))
    values = _values(r.name, 'v')
    assert '0.00001230' in values, values
    assert '0' in values, values
    assert '1.23e+40' in values, values


def test_excel_mixed_numeric_and_text_column():
    """A column with both numbers and free text should snap only the
    numeric cells; the text cells round-trip unchanged."""
    p = _write_xlsx([
        ['mixed'],
        [5],
        [0.1 + 0.2],
        ['hello'],
        ['1.2.3'],     # not a plain decimal
        ['$5.00'],     # currency, not a plain decimal
    ])
    r = ingest.load_file(str(p))
    values = _values(r.name, 'mixed')
    assert '5' in values
    assert '0.30' in values
    assert 'hello' in values
    assert '1.2.3' in values
    assert '$5.00' in values
