"""_src_row_num metadata column survives a downstream filter."""

from __future__ import annotations

import tempfile
from pathlib import Path

from file_quacker import db, ingest


def test_src_row_num_survives_filters():
    p = Path(tempfile.gettempdir()) / 'fq_rownum.csv'
    p.write_text(
        'id,flag\n'
        '1,A\n'
        '2,B\n'
        '3,A\n'
        '4,B\n'
        '5,A\n',
        encoding='utf-8',
    )
    r = ingest.load_file(str(p))
    qname = db.quote_ident(r.name)

    rows = db.conn().execute(f"""
        select  _src_row_num
        ,       id
        from    {qname}
        order   by _src_row_num
        ;
    """).fetchall()
    assert rows == [(1, '1'), (2, '2'), (3, '3'), (4, '4'), (5, '5')]

    rows = db.conn().execute(f"""
        select  _src_row_num
        ,       id
        from    {qname}
        where   flag = 'A'
        order   by _src_row_num
        ;
    """).fetchall()
    assert rows == [(1, '1'), (3, '3'), (5, '5')]

    assert r.columns[0]['name'] == '_src_row_num'
