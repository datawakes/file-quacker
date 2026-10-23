"""How text cells get read: the blanks the trim strips, and SQL helpers.

Shared by the file / SQL Server exports (trim option), derive (typed clone)
and the profiler, so they all agree on what an edge blank is.
"""

from __future__ import annotations

from . import db

# Whitespace classes the trim option strips off the ends of VARCHAR cells.
# Mirrors elt_preprocess_ff.auto_trim and pandas str.strip():
#   [[:space:]] – ASCII whitespace
#   \p{Z}       – Unicode separator characters (NBSP, narrow NBSP, etc.)
#   \x85        – NEL (C1 control)
#   \x1c-\x1f   – FS / GS / RS / US (ASCII info separators)
# Zero-width characters (ZWSP, ZWJ, BOM) are kept – they aren't whitespace.
WS_TRIM_PATTERN = r'^[[:space:]\p{Z}\x85\x1c-\x1f]+|[[:space:]\p{Z}\x85\x1c-\x1f]+$'


def string_columns(qtable: str) -> set[str]:
    """Names of VARCHAR columns on a DuckDB table or view."""
    info = db.conn().execute(f"""
        pragma table_info({qtable})
        ;
    """).fetchall()
    return {row[1] for row in info if row[2].upper() == 'VARCHAR'}


def stripped_sql(expr: str) -> str:
    """``expr`` with blanks cut off both ends; an all-blank value turns null,
    the way a truly-empty cell already reads."""
    return f"nullif(regexp_replace({expr}, '{WS_TRIM_PATTERN}', '', 'g'), '')"


def stripped_source(qtable: str, qcol: str) -> str:
    """Subquery that stands in for ``qtable`` with ``qcol`` read stripped
    (same column name), so a probe's SQL works unchanged on it."""
    return f'(select {stripped_sql(qcol)} as {qcol} from {qtable})'
