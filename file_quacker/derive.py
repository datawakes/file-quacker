"""Derive a typed table from a raw (VARCHAR) one.

Flow:
    suggest_casts(raw_name)   → per-column analysis: strict-integer,
                                decimal P/S inference, priority-cascade
                                date-format detection.  Returns a
                                suggested target type (including
                                `DECIMAL(P,S)` or a date format) when
                                any single type covers ≥95% of non-null
                                values.

    preview_derive(...)       → runs the generated SELECT with LIMIT 20.

    create_derived_table(...) → `create table <new> as <select>`.

Non-ISO date/time values (`2/14/24`, `Feb 14, 2024`, `20240214...`) are
cast via `try_strptime(col, '<fmt>')` rather than `try_cast`, since
DuckDB's `try_cast as DATE` only accepts ISO `YYYY-MM-DD`.

Decimal values are detected by the regex `^-?\\d+(\\.\\d+)?$` and sized
to the smallest `DECIMAL(P, S)` that holds the observed precision.
Strict integer detection uses `^-?\\d+$` so `"1.0"` doesn't round
silently into BIGINT.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import duckdb

from . import db
from .db import display_ident as _display_ident

SUGGEST_THRESHOLD = 95.0
# Stricter threshold for callers where a single non-conforming value
# would break the operation (e.g. SQL Server binds a varchar to a
# tinyint column; one bad row aborts the chunk).  Use this when
# typing decisions can't be reviewed by a human first.
STRICT_THRESHOLD = 100.0

# Regex pre-filter for values that plausibly look like dates / times.
# Broad enough to admit named months ("Feb 14, 2024") and timezone
# offsets ("+00:00"); strptime then rejects anything that doesn't
# actually parse.
_DATE_LIKE_RE = r'^[0-9A-Za-z/\-:.\sT,+]+(\s?[AP]M)?$'

# (format, kind) ordered for correct precedence:
#
#   * Time-bearing formats first; they're more specific than
#     pure-date formats, and strptime requires exact-match so they
#     only win when the value has the time component.
#
#   * Within the pure-date group, 2-digit-year (%y) variants appear
#     BEFORE 4-digit-year (%Y) variants.  %y is strict (needs exactly
#     2 digits) while %Y accepts 1-4; so for "2/14/24" %y wins
#     (year → 2024) instead of %Y matching it as year 24 AD.  For
#     "2/14/2024" %y fails and %Y takes over correctly.
DATE_FORMATS: tuple[tuple[str, str], ...] = (
    ('%Y-%m-%d %H:%M:%S.%f', 'TIMESTAMP'),
    ('%Y-%m-%d %H:%M:%S',    'TIMESTAMP'),
    ('%Y-%m-%dT%H:%M:%S.%f', 'TIMESTAMP'),
    ('%Y-%m-%dT%H:%M:%S',    'TIMESTAMP'),
    ('%Y%m%dT%H%M%S',        'TIMESTAMP'),
    ('%Y%m%d%H%M%S',         'TIMESTAMP'),
    ('%m/%d/%Y %I:%M:%S %p', 'TIMESTAMP'),
    ('%m/%d/%Y %H:%M:%S.%f', 'TIMESTAMP'),
    ('%m/%d/%Y %H:%M:%S',    'TIMESTAMP'),
    ('%m/%d/%y %H:%M:%S',    'TIMESTAMP'),
    ('%m/%d/%Y %I:%M %p',    'TIMESTAMP'),
    ('%Y-%m-%d',             'DATE'),
    ('%Y%m%d',               'DATE'),
    ('%m/%d/%y',             'DATE'),
    ('%m/%d/%Y',             'DATE'),
    ('%m-%d-%y',             'DATE'),
    ('%m-%d-%Y',             'DATE'),
    ('%d/%m/%Y',             'DATE'),
    ('%b %d, %Y',            'DATE'),
    ('%B %d, %Y',            'DATE'),
    ('%a, %b %d, %Y',        'DATE'),
)

# Formats that DuckDB's plain `try_cast` handles on its own; no need
# to carry the format through to the frontend.
_ISO_CAST_FORMATS = frozenset({
    '%Y-%m-%d',
    '%Y-%m-%d %H:%M:%S',
    '%Y-%m-%d %H:%M:%S.%f',
    '%Y-%m-%dT%H:%M:%S',
    '%Y-%m-%dT%H:%M:%S.%f',
})


@dataclass
class CastSpec:
    source: str
    target: str
    cast_to: str | None
    strict: bool = False
    # Only relevant when cast_to is DATE / TIMESTAMP for a non-ISO
    # value – triggers `try_strptime(col, fmt)` instead of `try_cast`.
    date_format: str | None = None


# --------------------------------------------------------------------------- #
# Suggestion                                                                  #
# --------------------------------------------------------------------------- #

def suggest_casts(table_name: str) -> list[dict]:
    c = db.conn()
    qtable = db.quote_ident(table_name)
    cols = c.execute(f"""
        pragma table_info({qtable})
        ;
    """).fetchall()

    varchar_names = [row[1] for row in cols if row[2].upper() == 'VARCHAR']
    analyses = _analyze_varchar_cols(qtable, varchar_names)

    out: list[dict] = []
    for row in cols:
        name = row[1]
        src_type = row[2]
        qcol = db.quote_ident(name)
        sample = _sample(c, qtable, qcol)
        if src_type.upper() != 'VARCHAR':
            out.append({
                'source': name,
                'source_type': src_type,
                'suggested_type': None,
                'cast_coverage': {},
                'sample_values': sample,
                'detected_date_format': None,
            })
            continue

        analysis = analyses[name]
        suggested, fmt = _pick_suggestion(analysis)
        out.append({
            'source': name,
            'source_type': src_type,
            'suggested_type': suggested,
            'cast_coverage': analysis['coverage'],
            'sample_values': sample,
            'detected_date_format': fmt,
        })
    return out


def _analyze_varchar_cols(qtable: str, col_names: list[str]) -> dict[str, dict]:
    """Coverage + P/S + date-cascade for every VARCHAR column, run in
    parallel across a worker pool.

    Per-column queries turn out to be faster than one mega-aggregate
    query (DuckDB's planner/executor overhead on hundreds of aggregates
    outweighs the single-scan benefit), and threading gives a real 2–3×
    wall-clock win since `db.conn()` hands out thread-safe cursors that
    share one root connection.  Each column still scans the full table.
    no sampling, so late-file outliers can't be missed.
    """
    if not col_names:
        return {}
    max_workers = min(8, len(col_names))
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        pairs = list(ex.map(lambda n: (n, _analyze_one(qtable, n)), col_names))
    return dict(pairs)


def _analyze_one(qtable: str, col_name: str) -> dict:
    """Single-column coverage + P/S + cascade analysis.  Thread-safe:
    each call gets its own cursor from `db.conn()`."""
    c = db.conn()
    qcol = db.quote_ident(col_name)

    # Pass 1: coverage.  The integer / decimal / double regexes require
    # `(0|[1-9]\d*)` for the integer part; disqualifies leading-zero
    # strings like "010876" (BINs) or "01234" (ZIP codes) which are
    # identifiers, not numbers, and would otherwise round-trip through
    # a numeric type and lose the leading zero on export.  DOUBLE keeps
    # an optional scientific-notation suffix so genuine `1.5e10`-style
    # values still count.
    row = c.execute(f"""
        select  count({qcol})                                                                  as non_null
        ,       sum(case when regexp_matches({qcol}, '^-?(0|[1-9]\\d*)(\\.0+)?$')
                     and try_cast({qcol} as bigint) is not null then 1 else 0 end)              as ok_int
        ,       sum(case when regexp_matches({qcol}, '^-?(0|[1-9]\\d*)(\\.\\d+)?$')
                     then 1 else 0 end)                                                         as ok_dec
        ,       sum(case when regexp_matches({qcol}, '^-?(0|[1-9]\\d*)(\\.\\d+)?([eE][+-]?\\d+)?$')
                     and try_cast({qcol} as double) is not null then 1 else 0 end)              as ok_double
        ,       sum(case when try_cast({qcol} as boolean) is not null then 1 else 0 end)        as ok_bool
        ,       sum(case when try_cast({qcol} as date) is not null
                     and cast(try_cast({qcol} as date) as varchar) = {qcol} then 1 else 0 end)  as ok_raw_date
        ,       sum(case when try_cast({qcol} as timestamp) is not null then 1 else 0 end)      as ok_raw_ts
        from    {qtable}
        ;
    """).fetchone()
    non_null, ok_int, ok_dec, ok_double, ok_bool, ok_raw_date, ok_raw_ts = (
        int(v or 0) for v in row
    )

    def pct(v: int) -> float:
        return round((v / non_null * 100.0), 2) if non_null else 0.0

    raw_date_pct = pct(ok_raw_date)
    raw_ts_pct   = pct(ok_raw_ts)
    dec_pct      = pct(ok_dec)
    int_pct      = pct(ok_int)
    double_pct   = pct(ok_double)

    # Pass 2: DECIMAL(P, S) sizing; only when decimal coverage qualifies.
    dec_p: int | None = None
    dec_s: int | None = None
    if non_null and dec_pct >= SUGGEST_THRESHOLD:
        dec_p, dec_s = _detect_decimal_ps(c, qtable, qcol)

    # Pass 3: non-ISO date cascade; skip when the raw path already
    # clears the threshold, AND skip when a numeric cast already clears
    # it (_pick_suggestion favors numeric over cascade, so the 21-format
    # strptime sweep would be thrown away).
    cnt = 0
    kind: str | None = None
    fmt: str | None = None
    raw_ok = raw_date_pct >= SUGGEST_THRESHOLD or raw_ts_pct >= SUGGEST_THRESHOLD
    numeric_ok = (
        int_pct    >= SUGGEST_THRESHOLD
        or dec_pct    >= SUGGEST_THRESHOLD
        or double_pct >= SUGGEST_THRESHOLD
    )
    if non_null and not raw_ok and not numeric_ok:
        cnt, kind, fmt = _detect_date_format(c, qtable, qcol)
    cascade_pct = pct(cnt)

    date_pct = max(raw_date_pct, cascade_pct if kind == 'DATE'      else 0.0)
    ts_pct   = max(raw_ts_pct,   cascade_pct if kind == 'TIMESTAMP' else 0.0)

    return {
        'coverage': {
            'BIGINT':    int_pct,
            'DECIMAL':   dec_pct,
            'DOUBLE':    double_pct,
            'BOOLEAN':   pct(ok_bool),
            'DATE':      date_pct,
            'TIMESTAMP': ts_pct,
        },
        'decimal_precision': dec_p,
        'decimal_scale':     dec_s,
        # Raw percentages preserved so _pick_suggestion can apply
        # whatever threshold its caller wants (lenient for the dialog,
        # strict for SQL Server export).
        'raw_date_pct':      raw_date_pct,
        'raw_ts_pct':        raw_ts_pct,
        'cascade_pct':       cascade_pct,
        'cascade_kind':      kind,
        'cascade_format':    fmt,
        'non_null':          non_null,
    }


def _pick_suggestion(
    a: dict,
    threshold: float = SUGGEST_THRESHOLD,
) -> tuple[str | None, str | None]:
    """Pick the umbrella type that ≥ ``threshold`` percent of the
    column's non-null values fit cleanly.  Pass STRICT_THRESHOLD when
    even a single non-conforming value would break the downstream
    operation (SQL Server bind, etc.); the lenient default is fine for
    the Clone dialog where the user reviews the suggestion."""
    cov         = a['coverage']
    raw_date    = a.get('raw_date_pct', 0.0)
    raw_ts      = a.get('raw_ts_pct',   0.0)
    cascade     = a.get('cascade_pct',  0.0)
    cascade_kind = a.get('cascade_kind')
    cascade_fmt  = a.get('cascade_format')

    if cov.get('BIGINT', 0.0) >= threshold:
        return 'BIGINT', None

    if cov.get('DECIMAL', 0.0) >= threshold and a.get('decimal_precision'):
        p, s = a['decimal_precision'], a['decimal_scale']
        return f'DECIMAL({p},{s})', None

    if cov.get('DOUBLE', 0.0) >= threshold:
        return 'DOUBLE', None

    # DATE / TIMESTAMP – raw cast first (no format needed); fall back
    # to the non-ISO cascade format only when raw alone doesn't clear
    # the threshold.
    if cov.get('DATE', 0.0) >= threshold:
        if raw_date >= threshold:
            return 'DATE', None
        if cascade >= threshold and cascade_kind == 'DATE':
            return 'DATE', (None if cascade_fmt in _ISO_CAST_FORMATS else cascade_fmt)

    if cov.get('TIMESTAMP', 0.0) >= threshold:
        if raw_ts >= threshold:
            return 'TIMESTAMP', None
        if cascade >= threshold and cascade_kind == 'TIMESTAMP':
            return 'TIMESTAMP', (None if cascade_fmt in _ISO_CAST_FORMATS else cascade_fmt)

    if cov.get('BOOLEAN', 0.0) >= threshold:
        return 'BOOLEAN', None

    return None, None


def _detect_decimal_ps(c, qtable: str, qcol: str) -> tuple[int | None, int | None]:
    """DECIMAL(P, S) sized to cover ~90% of observed precision.

    `max(scale)` naively would let a single Excel-compute artifact like
    `3.66666666666667` balloon the scale to 14, padding zeros onto every
    value.  `quantile_disc(scale, 0.9)` uses the 90th-percentile scale
    (capped at 10) so outliers get rounded on cast.  Trailing zeros are
    trimmed so `"15.00"` → scale 0 and `"3.140"` → scale 2.
    """
    row = c.execute(f"""
        with cte_nums as
        (   select  length(split_part(replace({qcol}, '-', ''), '.', 1))          as ilen
            ,       case when position('.' in {qcol}) > 0
                         then length(rtrim(split_part({qcol}, '.', 2), '0'))
                         else 0 end                                               as s
            from    {qtable}
            where   {qcol} is not null
            and     regexp_matches({qcol}, '^-?(0|[1-9]\\d*)(\\.\\d+)?$')
        )
        select  max(ilen)                   as max_int
        ,       quantile_disc(s, 0.9)       as p90_scale
        from    cte_nums
        ;
    """).fetchone()
    if row is None or row[0] is None:
        return None, None
    max_int   = max(int(row[0] or 1), 1)
    scale     = min(int(row[1] or 0), 10)
    precision = min(max_int + scale, 38)
    return precision, scale


def _detect_date_format(
    c,
    qtable: str,
    qcol: str,
) -> tuple[int, str | None, str | None]:
    """Find the majority non-ISO date format across date-like values.
    Returns `(match_count, 'DATE'|'TIMESTAMP', format_string)`."""
    # Defensive escape: DATE_FORMATS today contains no `'` but a future
    # entry that did would be a SQL-injection vector here.
    case_lines = [
        f"when try_strptime({qcol}, '{fmt.replace(chr(39), chr(39) * 2)}')"
        f" is not null then '{fmt.replace(chr(39), chr(39) * 2)}|{kind}'"
        for fmt, kind in DATE_FORMATS
    ]
    case_block = '\n                '.join(case_lines)
    try:
        row = c.execute(f"""
            select  detected
            ,       count(*) as cnt
            from (
                select  case
                    {case_block}
                        else 'unparseable'
                    end as detected
                from    {qtable}
                where   {qcol} is not null
                and     regexp_matches({qcol}, '{_DATE_LIKE_RE}')
            )
            group   by detected
            order   by count(*) desc
            limit   1
            ;
        """).fetchone()
    except duckdb.Error:
        return 0, None, None
    if not row:
        return 0, None, None
    detected, cnt = row
    if detected == 'unparseable':
        return 0, None, None
    fmt, kind = detected.split('|')
    return int(cnt), kind, fmt


def _sample(c, qtable: str, qcol: str, n: int = 3) -> list[str]:
    rows = c.execute(f"""
        select  cast({qcol} as varchar)
        from    {qtable}
        where   {qcol} is not null
        limit   {int(n)}
        ;
    """).fetchall()
    return [str(r[0]) for r in rows]


# --------------------------------------------------------------------------- #
# Auto-derive                                                                 #
# --------------------------------------------------------------------------- #

def auto_derive(source_table: str, new_name: str) -> dict:
    """Typed clone with suggested casts accepted for every column.
    Skips the per-column sample queries `suggest_casts` runs for its
    preview UI; the samples would be discarded here."""
    c = db.conn()
    qtable = db.quote_ident(source_table)
    cols = c.execute(f"""
        pragma table_info({qtable})
        ;
    """).fetchall()
    varchar_names = [row[1] for row in cols if row[2].upper() == 'VARCHAR']
    analyses = _analyze_varchar_cols(qtable, varchar_names)

    specs: list[CastSpec] = []
    for row in cols:
        name = row[1]
        src_type = row[2]
        if src_type.upper() != 'VARCHAR':
            cast_to, fmt = None, None
        else:
            cast_to, fmt = _pick_suggestion(analyses[name])
        specs.append(CastSpec(
            source=name,
            target=name,
            cast_to=cast_to,
            strict=False,
            date_format=fmt,
        ))
    return create_derived_table(source_table, new_name, specs, None)


# --------------------------------------------------------------------------- #
# Preview / create                                                            #
# --------------------------------------------------------------------------- #

def preview_derive(
    source_table: str,
    casts: list[CastSpec],
    where_clause: str | None,
    limit: int = 20,
) -> dict:
    sql = _compose_select(source_table, casts, where_clause) + f'\nlimit   {int(limit)}\n;\n'
    return _execute_preview(sql)


def create_derived_table(
    source_table: str,
    new_name: str,
    casts: list[CastSpec],
    where_clause: str | None,
) -> dict:
    create_sql = derive_sql(source_table, new_name, casts, where_clause)
    c = db.conn()
    c.execute(create_sql)
    qnew = db.quote_ident(new_name)
    (rc,) = c.execute(f"""
        select  count(*)
        from    {qnew}
        ;
    """).fetchone()
    return {'ok': True, 'name': new_name, 'row_count': int(rc), 'sql': create_sql}


def derive_sql(
    source_table: str,
    new_name: str,
    casts: list[CastSpec],
    where_clause: str | None,
) -> str:
    """Compose the CREATE TABLE SQL that `create_derived_table` would
    execute.  Shared with the Show-SQL preview so the button text and
    the commit are guaranteed identical."""
    qnew = _display_ident(new_name)
    select_sql = _compose_select(source_table, casts, where_clause)
    return f'create or replace table {qnew} as\n{select_sql}\n;'


# --------------------------------------------------------------------------- #
# Compose SELECT                                                              #
# --------------------------------------------------------------------------- #

def _compose_select(source_table: str, casts: list[CastSpec], where_clause: str | None) -> str:
    """Format per coding_conventions.md:
      * bare identifiers unless they need quoting
      * `select ` on line 1 (minimal padding to the first expression)
      * leading comma rows share an `as` column, aligned to the widest
        expression among them; shorter expressions get trailing spaces
      * `from    ` / `where   ` – 8-char keyword padding
    """
    src = _display_ident(source_table)
    items = [(_cast_expr(spec), _display_ident(spec.target)) for spec in casts]
    # Align `as` across the leading-comma rows (lines 2+).  Line 1 keeps
    # a minimal 2-space gutter so `select <col>  as <col>` stays tight
    # even when the widest expression below is long.
    max_expr = max((len(e) for e, _ in items[1:]), default=0)

    lines: list[str] = []
    for i, (expr, alias) in enumerate(items):
        if i == 0:
            lines.append(f'select {expr}  as {alias}')
        else:
            padded = expr.ljust(max_expr)
            lines.append(f'     , {padded}  as {alias}')
    sel = '\n'.join(lines)
    clause = f'\nwhere  {where_clause.strip()}' if where_clause and where_clause.strip() else ''
    return f'{sel}\nfrom   {src}{clause}'


def _cast_expr(spec: CastSpec) -> str:
    src = _display_ident(spec.source)
    if not spec.cast_to:
        return src
    cast_to = spec.cast_to.strip()
    up = cast_to.upper()
    # Non-ISO dates / timestamps need strptime; plain try_cast only
    # handles ISO `YYYY-MM-DD[...]` forms.
    if spec.date_format and up in ('DATE', 'TIMESTAMP'):
        safe_fmt = spec.date_format.replace("'", "''")
        fn = 'strptime' if spec.strict else 'try_strptime'
        return f"cast({fn}({src}, '{safe_fmt}') as {cast_to.lower()})"
    fn = 'cast' if spec.strict else 'try_cast'
    return f'{fn}({src} as {cast_to.lower()})'


def _execute_preview(sql: str) -> dict:
    c = db.conn()
    cursor = c.execute(sql)
    cols = [{'name': d[0], 'type': str(d[1])} for d in (cursor.description or [])]
    rows = cursor.fetchall()
    safe = [[_json_safe(v) for v in row] for row in rows]
    return {'columns': cols, 'rows': safe, 'sql': sql.strip()}


def _json_safe(v: Any) -> Any:
    if v is None or isinstance(v, (int, float, bool, str)):
        return v
    return str(v)
