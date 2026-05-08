"""Flat / Parquet / Excel export round-trip via api.export_to_file."""

from __future__ import annotations

import csv
import tempfile
from pathlib import Path

from file_quacker import db, ingest
from file_quacker.api import Api


def _write_csv(name: str, body: str) -> Path:
    p = Path(tempfile.gettempdir()) / name
    p.write_text(body, encoding='utf-8')
    return p


def test_export_flat_pipe():
    src = Path(tempfile.gettempdir()) / 'fq_file_src.csv'
    src.write_text(
        'id,name,amount\n'
        '1,alpha,1.5\n'
        '2,beta,2.5\n'
        '3,gamma,3.5\n',
        encoding='utf-8',
    )
    r = ingest.load_file(str(src))
    api = Api()
    source = {'kind': 'table', 'name': r.name, 'sql': None}

    pipe_path = Path(tempfile.gettempdir()) / 'fq_out_pipe.txt'
    pipe_path.unlink(missing_ok=True)
    res = api.export_to_file(source, {
        'kind': 'flat', 'path': str(pipe_path),
        'delimiter': '|', 'header': True, 'quote': '"', 'null': '',
    })
    assert res['ok']
    assert res['rows_written'] == 3
    text = pipe_path.read_text(encoding='utf-8').splitlines()
    assert text[0] == 'id|name|amount'
    assert text[1] == '1|alpha|1.5'


def test_export_flat_tab_with_mapping():
    src = Path(tempfile.gettempdir()) / 'fq_file_src.csv'
    src.write_text('id,name,amount\n1,alpha,1.5\n2,beta,2.5\n3,gamma,3.5\n', encoding='utf-8')
    r = ingest.load_file(str(src))
    api = Api()
    source = {'kind': 'table', 'name': r.name, 'sql': None}

    tab_path = Path(tempfile.gettempdir()) / 'fq_out_tab.tsv'
    tab_path.unlink(missing_ok=True)
    mappings = [
        {'source': 'id',   'target': 'row_id'},
        {'source': 'name', 'target': 'full_name'},
    ]
    res = api.export_to_file(source, {
        'kind': 'flat', 'path': str(tab_path),
        'delimiter': '\t', 'header': True, 'quote': '"', 'null': '',
    }, mappings)
    assert res['ok']
    with tab_path.open(encoding='utf-8', newline='') as f:
        rows = list(csv.reader(f, delimiter='\t'))
    assert rows[0] == ['row_id', 'full_name']
    assert len(rows) == 4


def test_export_parquet():
    src = Path(tempfile.gettempdir()) / 'fq_file_src.csv'
    src.write_text('id,name,amount\n1,alpha,1.5\n2,beta,2.5\n3,gamma,3.5\n', encoding='utf-8')
    r = ingest.load_file(str(src))
    api = Api()
    source = {'kind': 'table', 'name': r.name, 'sql': None}

    pq_path = Path(tempfile.gettempdir()) / 'fq_out.parquet'
    pq_path.unlink(missing_ok=True)
    res = api.export_to_file(source, {
        'kind': 'parquet', 'path': str(pq_path), 'compression': 'snappy',
    })
    assert res['ok']
    (n,) = db.conn().execute(f"""
        select  count(*)
        from    read_parquet('{pq_path.as_posix()}')
        ;
    """).fetchone()
    assert n == 3


def test_export_excel_with_mapping():
    src = Path(tempfile.gettempdir()) / 'fq_file_src.csv'
    src.write_text('id,name,amount\n1,alpha,1.5\n2,beta,2.5\n3,gamma,3.5\n', encoding='utf-8')
    r = ingest.load_file(str(src))
    api = Api()
    source = {'kind': 'table', 'name': r.name, 'sql': None}

    xlsx_path = Path(tempfile.gettempdir()) / 'fq_out.xlsx'
    xlsx_path.unlink(missing_ok=True)
    excel_maps = [
        {'source': 'id',     'target': 'ID'},
        {'source': 'name',   'target': 'Name'},
        {'source': 'amount', 'target': 'Amount'},
    ]
    res = api.export_to_file(source, {
        'kind': 'excel', 'path': str(xlsx_path), 'sheet_name': 'Data',
    }, excel_maps)
    assert res['ok']

    import openpyxl
    wb = openpyxl.load_workbook(xlsx_path, read_only=True)
    ws = wb['Data']
    rows = list(ws.iter_rows(values_only=True))
    assert rows[0] == ('ID', 'Name', 'Amount')
    assert len(rows) == 4


def test_export_flat_trims_whitespace_by_default():
    """VARCHAR cells lose leading / trailing whitespace; numeric columns
    are untouched. Includes a Unicode separator (NBSP) and an ASCII info
    separator (\\x1f) to confirm the cascade matches preprocess_ff."""
    src = _write_csv(
        'fq_trim_src.csv',
        'id,name,note\n'
        '1,"  alpha  "," unicode-nbsp "\n'
        '2,"\tbeta\t","\x1finfo-sep\x1f"\n'
        '3," gamma ","clean"\n',
    )
    r = ingest.load_file(str(src))
    source = {'kind': 'table', 'name': r.name, 'sql': None}

    out = Path(tempfile.gettempdir()) / 'fq_trim_default.txt'
    out.unlink(missing_ok=True)
    res = Api().export_to_file(source, {
        'kind': 'flat', 'path': str(out),
        'delimiter': '|', 'header': True, 'quote': '"', 'null': '',
    })
    assert res['ok']
    rows = out.read_text(encoding='utf-8').splitlines()
    assert rows[0] == 'id|name|note'
    assert rows[1] == '1|alpha|unicode-nbsp'
    assert rows[2] == '2|beta|info-sep'
    assert rows[3] == '3|gamma|clean'


def test_export_flat_trim_can_be_disabled():
    """trim_strings=False keeps the source bytes intact."""
    src = _write_csv(
        'fq_trim_off.csv',
        'id,name\n1,"  alpha  "\n2,"\tbeta\t"\n',
    )
    r = ingest.load_file(str(src))
    source = {'kind': 'table', 'name': r.name, 'sql': None}

    out = Path(tempfile.gettempdir()) / 'fq_trim_off.txt'
    out.unlink(missing_ok=True)
    res = Api().export_to_file(source, {
        'kind': 'flat', 'path': str(out),
        'delimiter': '|', 'header': True, 'quote': '"', 'null': '',
    }, None, False)
    assert res['ok']
    rows = out.read_text(encoding='utf-8').splitlines()
    # DuckDB's COPY only adds quotes when the value contains the delimiter,
    # so the cells come through bare — but the whitespace is intact.
    assert rows[1] == '1|  alpha  '
    assert rows[2] == '2|\tbeta\t'


def test_export_flat_trim_only_touches_string_columns():
    """Trim wraps regexp_replace around VARCHAR columns only. Numeric
    source columns are projected as bare identifiers, so their values
    come through unchanged."""
    src = _write_csv(
        'fq_trim_numeric.csv',
        'id,price\n1,1.55\n2,2.75\n',
    )
    r = ingest.load_file(str(src))
    api = Api()
    typed = api.auto_derive(r.name, f'{r.name}_typed')
    source = {'kind': 'table', 'name': typed['name'], 'sql': None}

    out = Path(tempfile.gettempdir()) / 'fq_trim_numeric.txt'
    out.unlink(missing_ok=True)
    res = api.export_to_file(source, {
        'kind': 'flat', 'path': str(out),
        'delimiter': '|', 'header': True, 'quote': '"', 'null': '',
    })
    assert res['ok']
    rows = out.read_text(encoding='utf-8').splitlines()
    assert rows[0] == 'id|price'
    assert rows[1] == '1|1.55'
    assert rows[2] == '2|2.75'


def test_export_flat_trim_promotes_all_whitespace_to_null():
    """A cell that's nothing but whitespace lands as NULL in the output,
    matching how a truly-empty cell already behaves. Without this,
    quoted-whitespace cells would land as empty strings while unquoted-
    empty cells (already NULL in DuckDB) would land as NULL — confusing
    inconsistency for the user looking at the destination table."""
    src = _write_csv(
        'fq_trim_blank_null.csv',
        'id,note\n'
        '1,"                         "\n'
        '2,real value\n'
        '3,\n',
    )
    r = ingest.load_file(str(src))
    source = {'kind': 'table', 'name': r.name, 'sql': None}

    out = Path(tempfile.gettempdir()) / 'fq_trim_blank_null.txt'
    out.unlink(missing_ok=True)
    res = Api().export_to_file(source, {
        'kind': 'flat', 'path': str(out),
        'delimiter': '|', 'header': True, 'quote': '"', 'null': 'NULL',
    })
    assert res['ok']
    rows = out.read_text(encoding='utf-8').splitlines()
    # All-whitespace cell promoted to NULL, matches the unquoted-empty row.
    assert rows[1] == '1|NULL'
    assert rows[2] == '2|real value'
    assert rows[3] == '3|NULL'


def test_export_parquet_trim_default_on():
    src = _write_csv(
        'fq_trim_pq_src.csv',
        'name,note\n  alpha  ,clean\n  beta  , nbsp \n',
    )
    r = ingest.load_file(str(src))
    source = {'kind': 'table', 'name': r.name, 'sql': None}

    out = Path(tempfile.gettempdir()) / 'fq_trim_out.parquet'
    out.unlink(missing_ok=True)
    res = Api().export_to_file(source, {
        'kind': 'parquet', 'path': str(out), 'compression': 'snappy',
    })
    assert res['ok']
    rows = db.conn().execute(
        f"select name, note from read_parquet('{out.as_posix()}') order by name"
    ).fetchall()
    assert rows == [('alpha', 'clean'), ('beta', 'nbsp')]
