"""Show DDL's name option: as-is keeps workspace names, snake_case matches Export to SQL."""

from __future__ import annotations

from file_quacker import db, ddl, export
from file_quacker.api import Api


def _table() -> str:
    c = db.conn()
    c.execute("""
        create or replace table "Claim Export" as
        select  1               as "claim.id"
        ,       'A'             as "claim.status"
        ,       2               as "Claim ID"
        ,       'x'             as "order"
        ;
    """)
    return 'Claim Export'


def test_as_is_keeps_names_and_brackets():
    text = ddl.generate(_table(), 'sqlserver')
    assert 'create table [Claim Export]' in text
    assert '[claim.id]' in text and '[Claim ID]' in text


def test_snake_case_drops_brackets_and_dedupes():
    text = ddl.generate(_table(), 'sqlserver', snake_names=True)
    assert 'create table claim_export' in text
    assert '[' not in text.replace('[order]', '')   # only the keyword stays quoted
    # "claim.id" and "Claim ID" both snake to claim_id; the second gets _1.
    assert '(   claim_id ' in text and '  , claim_id_1 ' in text


def test_snake_names_match_export_mappings():
    name = _table()
    mapped = [m['target'] for m in export.suggest_mappings({'kind': 'table', 'name': name, 'sql': None})]
    text = ddl.generate(name, 'sqlserver', snake_names=True)
    for target in mapped:
        assert export._qident_ss(target) in text


def test_api_returns_both_variants_from_one_analysis(monkeypatch):
    name = _table()
    probes = []
    real = ddl._column_type
    monkeypatch.setattr(ddl, '_column_type', lambda *a, **k: probes.append(a[2]) or real(*a, **k))
    r = Api().generate_ddl(name, 'duckdb')
    assert '"claim.status"' in r['sql']
    assert 'claim_status' in r['sql_snake']
    assert len(probes) == 4   # each column probed once for both variants
