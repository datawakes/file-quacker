"""ExportSource.materialize() / cleanup() round-trip + multi-statement guard."""

from __future__ import annotations

import tempfile
from pathlib import Path

from file_quacker import db, ingest
from file_quacker.export_source import ExportSource


def test_table_kind_is_passthrough():
    p = Path(tempfile.gettempdir()) / 'fq_src.csv'
    p.write_text('id,name\n1,alpha\n2,beta\n', encoding='utf-8')
    r = ingest.load_file(str(p))

    src = ExportSource(kind='table', name=r.name, sql=None)
    qname, cleanup = src.materialize()
    assert qname == r.name
    (n,) = db.conn().execute(f'select count(*) from "{qname}"').fetchone()
    assert n == 2
    cleanup()


def test_query_kind_creates_and_drops_view():
    p = Path(tempfile.gettempdir()) / 'fq_src.csv'
    p.write_text('id,name\n1,alpha\n2,beta\n', encoding='utf-8')
    r = ingest.load_file(str(p))

    src = ExportSource(kind='query', name=None, sql=f'select id from "{r.name}" where id = 1')
    qname, cleanup = src.materialize()
    assert qname.startswith('fq_export_')
    (n,) = db.conn().execute(f'select count(*) from "{qname}"').fetchone()
    assert n == 1
    cleanup()

    (gone,) = db.conn().execute(
        "select count(*) from information_schema.views where table_name = ?",
        [qname],
    ).fetchone()
    assert gone == 0


def test_multi_statement_query_is_rejected():
    """A trailing ;-separated DROP must not execute as a side effect of
    materializing the user's SELECT.  The parser rejects multi-statement
    bodies wrapped in select * from (...)."""
    import pytest
    p = Path(tempfile.gettempdir()) / 'fq_src.csv'
    p.write_text('id\n1\n', encoding='utf-8')
    ingest.load_file(str(p))

    es = ExportSource(kind='query', sql='select 1 as a; drop table foo;')
    with pytest.raises(Exception):
        name, cleanup = es.materialize()
        cleanup()
