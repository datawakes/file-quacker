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

from . import db, instrument
from .db import display_ident as _display_ident

SUGGEST_THRESHOLD = 95.0
# Stricter threshold for callers where a single non-conforming value
# would break the operation (e.g. SQL Server binds a varchar to a
# tinyint column; one bad row aborts the chunk).  Use this when
# typing decisions can't be reviewed by a human first.
STRICT_THRESHOLD = 100.0

# Broad pre-filter for fallback date detection when no sample is available.
# It only narrows obvious non-date values; strptime does the real validation.
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


def _timed_analyze_one(qtable: str, col_name: str) -> tuple[str, dict]:
    with instrument.timed('_analyze_one', col=col_name):
        return col_name, _analyze_one(qtable, col_name)


def _analyze_varchar_cols(qtable: str, col_names: list[str]) -> dict[str, dict]:
    """Analyze VARCHAR columns in parallel.

    Each column gets a small ASC/DESC distinct-value sniff first. The full-table
    scan then runs only the type checks that the sniff indicates are useful.

    Per-column queries are faster here than one large aggregate query, and worker
    threads improve wall-clock time because DuckDB cursors share the same root
    connection safely.
    """
    if not col_names:
        return {}
    max_workers = min(8, len(col_names))
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        pairs = list(ex.map(lambda n: _timed_analyze_one(qtable, n), col_names))
    return dict(pairs)


def _analyze_one(qtable: str, col_name: str) -> dict:
    """Analyze one VARCHAR column using a sniff-gated full-table scan.

    Each call uses its own DuckDB cursor. A small ASC/DESC distinct-value sniff
    runs the broad type checks and date-format cascade first. The full-table scan
    then skips type families that had zero sniff hits, and verifies only the
    winning date format when one was found.

    The ASC/DESC bracket helps catch edge-value outliers while keeping the sniff
    small and deterministic.
    """
    c = db.conn()
    qcol = db.quote_ident(col_name)

    sniff = _sniff_one(c, qtable, qcol)

    def _expr(skip: bool, sql: str) -> str:
        return '0' if skip else sql

    sn = sniff['n']
    skip_int      = sn > 0 and sniff['int']      == 0
    skip_dec      = sn > 0 and sniff['dec']      == 0
    skip_double   = sn > 0 and sniff['double']   == 0
    skip_bool     = sn > 0 and sniff['bool']     == 0
    skip_raw_date = sn > 0 and sniff['raw_date'] == 0
    skip_raw_ts   = sn > 0 and sniff['raw_ts']   == 0

    int_sql      = (f"sum(case when regexp_matches({qcol}, '^-?(0|[1-9]\\d*)(\\.0+)?$')"
                    f"          and try_cast({qcol} as bigint) is not null then 1 else 0 end)")
    dec_sql      = (f"sum(case when regexp_matches({qcol}, '^-?(0|[1-9]\\d*)(\\.\\d+)?$')"
                    f"          then 1 else 0 end)")
    double_sql   = (f"sum(case when regexp_matches({qcol}, '^-?(0|[1-9]\\d*)(\\.\\d+)?([eE][+-]?\\d+)?$')"
                    f"          and try_cast({qcol} as double) is not null then 1 else 0 end)")
    bool_sql     = f"sum(case when try_cast({qcol} as boolean) is not null then 1 else 0 end)"
    raw_date_sql = (f"sum(case when try_cast({qcol} as date) is not null"
                    f"          and cast(try_cast({qcol} as date) as varchar) = {qcol} then 1 else 0 end)")
    raw_ts_sql   = f"sum(case when try_cast({qcol} as timestamp) is not null then 1 else 0 end)"

    # Pass 1: coverage.
    # When the sniff finds no type candidates, a bare count(*) is enough.
    n_active = sum(1 for s in (skip_int, skip_dec, skip_double, skip_bool,
                                skip_raw_date, skip_raw_ts) if not s)
    if n_active == 0:
        with instrument.timed('coverage_pass', col=col_name, umbrellas=0):
            (nn,) = c.execute(f"""
                select  count({qcol}) from {qtable};
            """).fetchone()
        non_null = int(nn or 0)
        ok_int = ok_dec = ok_double = ok_bool = ok_raw_date = ok_raw_ts = 0
    else:
        with instrument.timed('coverage_pass', col=col_name, umbrellas=n_active):
            row = c.execute(f"""
                select  count({qcol})                       as non_null
                ,       {_expr(skip_int,      int_sql)}     as ok_int
                ,       {_expr(skip_dec,      dec_sql)}     as ok_dec
                ,       {_expr(skip_double,   double_sql)}  as ok_double
                ,       {_expr(skip_bool,     bool_sql)}    as ok_bool
                ,       {_expr(skip_raw_date, raw_date_sql)} as ok_raw_date
                ,       {_expr(skip_raw_ts,   raw_ts_sql)}  as ok_raw_ts
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
    # Sniff also skips this when the sample saw no decimal-shaped values.
    dec_p: int | None = None
    dec_s: int | None = None
    if non_null and dec_pct >= SUGGEST_THRESHOLD and not skip_dec:
        dec_p, dec_s = _detect_decimal_ps(c, qtable, qcol)

    # Pass 3: non-ISO date cascade.  Skipped when raw / numeric casts
    # already clear threshold (`_pick_suggestion` would prefer those
    # anyway).  When the cascade *is* needed, we lean on the sniff:
    #   * it already ran the full 21-format strptime sweep on the
    #     50-row bracket sample, so a zero-hit sample lets us skip
    #     the full-table cascade outright (the dominant case for
    #     plain-text columns); and
    #   * when a format won the sample, we verify with that *one*
    #     format on the full table instead of re-running 21
    #     strptime calls per row.
    # Fully-null columns (sn == 0) get the original 21-format full-
    # table cascade as a fallback — no sample = no shortcut.
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
        if sn == 0:
            cnt, kind, fmt = _detect_date_format(c, qtable, qcol)
        elif sniff['cascade_fmt'] is not None:
            cnt, kind, fmt = _detect_date_format_single(
                c, qtable, qcol, sniff['cascade_fmt'], sniff['cascade_kind'],
            )
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


# Reusable date-format probes for the sniff sample.
# fmt_<i> keeps result columns aligned with DATE_FORMATS.
_SNIFF_FORMAT_CASES = ',\n            '.join(
    f"sum(case when try_strptime(v, '{fmt.replace(chr(39), chr(39) * 2)}') "
    f"is not null then 1 else 0 end) as fmt_{i}"
    for i, (fmt, _kind) in enumerate(DATE_FORMATS)
)


def _sniff_one(c, qtable: str, qcol: str, n: int = 25) -> dict:
    """Return ASC/DESC distinct-value sniff results for one column.

    The sniff keeps up to `n` distinct values from each lexical edge, de-duping
    overlap between the two sides. Using distinct values prevents repeated values
    at one edge from hiding nearby outliers.

    The returned hit counts let full-table analysis skip type families that had
    zero sample hits.
    """
    with instrument.timed('_sniff_one', col=qcol, n=n):
        row = c.execute(f"""
            with cte_asc as
            (   select  distinct {qcol} as v
                from    {qtable}
                where   {qcol} is not null
                order   by v asc
                limit   {int(n)}
            ), cte_desc as
            (   select  distinct {qcol} as v
                from    {qtable}
                where   {qcol} is not null
                order   by v desc
                limit   {int(n)}
            ), cte_sample as
            (   select  v from cte_asc
                union
                select  v from cte_desc
            )
            select  count(*)                                                                 as n
            ,       sum(case when regexp_matches(v, '^-?(0|[1-9]\\d*)(\\.0+)?$')
                         and try_cast(v as bigint) is not null then 1 else 0 end)            as ok_int
            ,       sum(case when regexp_matches(v, '^-?(0|[1-9]\\d*)(\\.\\d+)?$')
                         then 1 else 0 end)                                                  as ok_dec
            ,       sum(case when regexp_matches(v, '^-?(0|[1-9]\\d*)(\\.\\d+)?([eE][+-]?\\d+)?$')
                         and try_cast(v as double) is not null then 1 else 0 end)            as ok_double
            ,       sum(case when try_cast(v as boolean) is not null then 1 else 0 end)      as ok_bool
            ,       sum(case when try_cast(v as date) is not null
                         and cast(try_cast(v as date) as varchar) = v then 1 else 0 end)     as ok_raw_date
            ,       sum(case when try_cast(v as timestamp) is not null then 1 else 0 end)    as ok_raw_ts
            ,       {_SNIFF_FORMAT_CASES}
            from    cte_sample
            ;
        """).fetchone()
    if not row or int(row[0] or 0) == 0:
        return {'n': 0, 'int': 0, 'dec': 0, 'double': 0, 'bool': 0,
                'raw_date': 0, 'raw_ts': 0,
                'cascade_fmt': None, 'cascade_kind': None, 'cascade_hits': 0}

    # Pick the date format with the most sample hits.
    # Ties follow DATE_FORMATS order; zero hits skips full-table date checks.
    fmt_hits = [int(v or 0) for v in row[7:7 + len(DATE_FORMATS)]]
    best_idx = -1
    best_hits = 0
    for i, h in enumerate(fmt_hits):
        if h > best_hits:
            best_idx, best_hits = i, h
    if best_idx >= 0:
        cfmt, ckind = DATE_FORMATS[best_idx]
    else:
        cfmt, ckind = None, None

    return {
        'n':            int(row[0]),
        'int':          int(row[1] or 0),
        'dec':          int(row[2] or 0),
        'double':       int(row[3] or 0),
        'bool':         int(row[4] or 0),
        'raw_date':     int(row[5] or 0),
        'raw_ts':       int(row[6] or 0),
        'cascade_fmt':  cfmt,
        'cascade_kind': ckind,
        'cascade_hits': best_hits,
    }


def _detect_decimal_ps(c, qtable: str, qcol: str) -> tuple[int | None, int | None]:
    """DECIMAL(P, S) sized to cover ~90% of observed precision.

    `max(scale)` naively would let a single Excel-compute artifact like
    `3.66666666666667` balloon the scale to 14, padding zeros onto every
    value.  `quantile_disc(scale, 0.9)` uses the 90th-percentile scale
    (capped at 10) so outliers get rounded on cast.  Trailing zeros are
    trimmed so `"15.00"` → scale 0 and `"3.140"` → scale 2.
    """
    with instrument.timed('_detect_decimal_ps', col=qcol):
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


def _detect_date_format_single(
    c,
    qtable: str,
    qcol: str,
    fmt: str,
    kind: str,
) -> tuple[int, str | None, str | None]:
    """Verify only the sample-winning date format against the full column.

    This avoids rerunning the full date-format cascade when the sniff already
    found the likely format.
    """
    safe_fmt = fmt.replace(chr(39), chr(39) * 2)
    try:
        with instrument.timed('_detect_date_format_single', col=qcol, fmt=fmt):
            row = c.execute(f"""
                select  count(*)
                from    {qtable}
                where   {qcol} is not null
                and     try_strptime({qcol}, '{safe_fmt}') is not null
                ;
            """).fetchone()
    except duckdb.Error:
        return 0, None, None
    if not row or row[0] is None:
        return 0, None, None
    return int(row[0]), kind, fmt


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
        with instrument.timed('_detect_date_format', col=qcol, formats=len(DATE_FORMATS)):
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
