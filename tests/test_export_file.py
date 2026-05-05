"""Flat / Parquet / Excel export round-trip via api.export_to_file."""

from __future__ import annotations

import csv
import tempfile
from pathlib import Path

from file_quacker import db, ingest
from file_quacker.api import Api


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
