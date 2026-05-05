"""Currency column with one Excel-compute outlier should land DECIMAL(5,2).

The outlier rounds on cast, instead of inflating every row to (17,14)
and rendering `0` as `0E-16`.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from file_quacker import db, ingest
from file_quacker.api import Api


def test_decimal_outlier_does_not_inflate_scale():
    p = Path(tempfile.gettempdir()) / 'fq_decimal_outlier.csv'
    p.write_text(
        'sec_ince_amt\n'
        '15\n50\n290.08\n290.08\n100.39\n0\n24.97\n130\n130\n3.66666666666667\n',
        encoding='utf-8',
    )
    r = ingest.load_file(str(p))
    api = Api()
    s = next(x for x in api.suggest_casts(r.name) if x['source'] == 'sec_ince_amt')
    assert s['suggested_type'] == 'DECIMAL(5,2)'

    created = api.auto_derive(r.name, f'{r.name}_typed')
    assert created['ok']

    (col_type,) = db.conn().execute(f"""
        select  column_type
        from    (describe {db.quote_ident(created['name'])})
        where   column_name = 'sec_ince_amt'
    """).fetchone()
    assert col_type.upper() == 'DECIMAL(5,2)'

    rows = db.conn().execute(
        f"select cast(sec_ince_amt as varchar) "
        f"from {db.quote_ident(created['name'])} order by _src_row_num"
    ).fetchall()
    values = [r[0] for r in rows]
    assert '0.00' in values
    assert '3.67' in values
