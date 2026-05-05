"""Column profiling; stats computed in DuckDB for one column at a time.

Works against a `source_sql` (any SELECT statement), which lets the same
code profile both a loaded table (via `SELECT * FROM "t"`) and an
arbitrary SQL result pane.  When the source includes `_src_row_num`,
"1st instance" metrics use it directly; otherwise we use a windowed
row_number over the source's natural scan order.

The result envelope is a single dict the frontend renders row-by-row.
Branches by DuckDB type:

  * all types; column position, distinct, nulls, fill %, cardinality,
                  min, max, top-3 (with percentage)
  * numeric; zero / negative counts, mean, median, stddev,
                  Q1 / Q3 / IQR, outlier count
  * varchar; min / max length (with "first instance" row), empty,
                  whitespace-only, leading / trailing spaces, contains
                  spaces, email-match, type-sniff preview
  * date / time; future-date count, date range in days, unique years,
                  earliest, latest
"""

from __future__ import annotations

from typing import Any

from . import db

EMAIL_RE = r'^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$'


def profile_table(table_name: str, column_name: str) -> dict:
    qtable = db.quote_ident(table_name)
    return _profile(f'select * from {qtable}', column_name)


def profile_query(source_sql: str, column_name: str) -> dict:
    return _profile(source_sql, column_name)


# --------------------------------------------------------------------------- #
# Core                                                                        #
# --------------------------------------------------------------------------- #

def _profile(source_sql: str, column_name: str) -> dict:
    c = db.conn()
    clean_sql = source_sql.strip().rstrip(';').strip()

    # DESCRIBE <select> returns (column_name, column_type, null, key, default, extra).
    desc = c.execute(f"""
        describe {clean_sql}
        ;
    """).fetchall()
    col_names = [r[0] for r in desc]
    col_types = {r[0]: r[1] for r in desc}
    if column_name not in col_types:
        raise ValueError(f'column not found: {column_name}')

    col_type = col_types[column_name]
    col_pos = col_names.index(column_name) + 1
    has_src_row = '_src_row_num' in col_names
    row_expr = db.quote_ident('_src_row_num') if has_src_row else 'row_number() over ()'

    qcol = db.quote_ident(column_name)
    qsub = f'({clean_sql}) _fq_src'

    common = _common_stats(c, qsub, qcol)
    branch: dict[str, Any] = {}
    upper = col_type.upper()
    if _is_numeric(upper):
        branch = _numeric_stats(c, qsub, qcol)
        kind = 'numeric'
    elif upper == 'VARCHAR':
        branch = _varchar_stats(c, qsub, qcol, row_expr, common)
        kind = 'varchar'
    elif upper.startswith('DATE') or upper.startswith('TIMESTAMP'):
        branch = _temporal_stats(c, qsub, qcol)
        kind = 'temporal'
    else:
        kind = 'other'

    return {
        'column': column_name,
        'type': col_type,
        'kind': kind,
        'column_position': col_pos,
        'common': common,
        'branch': branch,
    }


# --------------------------------------------------------------------------- #
# Common stats                                                                #
# --------------------------------------------------------------------------- #

def _common_stats(c, qsub: str, qcol: str) -> dict:
    row = c.execute(f"""
        select  count(*)                              as n
        ,       count({qcol})                         as non_null
        ,       count(distinct {qcol})                as distinct_cnt
        ,       cast(min({qcol}) as varchar)          as min_v
        ,       cast(max({qcol}) as varchar)          as max_v
        from    {qsub}
        ;
    """).fetchone()
    n, non_null, distinct_cnt, min_v, max_v = row
    nulls = int(n) - int(non_null)
    fill_pct = (float(non_null) / n * 100.0) if n else 0.0
    cardinality_pct = (float(distinct_cnt) / n * 100.0) if n else 0.0

    top = c.execute(f"""
        select  cast({qcol} as varchar)  as v
        ,       count(*)                 as cnt
        from    {qsub}
        where   {qcol} is not null
        group   by {qcol}
        order   by cnt desc
        ,       v
        limit   3
        ;
    """).fetchall()
    top_values = [
        {
            'value': None if v is None else str(v),
            'count': int(cnt),
            'percent': round(int(cnt) / int(non_null) * 100.0, 2) if non_null else 0.0,
        }
        for (v, cnt) in top
    ]

    return {
        'row_count': int(n),
        'non_null_count': int(non_null),
        'distinct_count': int(distinct_cnt),
        'null_count': nulls,
        'fill_pct': round(fill_pct, 2),
        'cardinality_pct': round(cardinality_pct, 2),
        'min': _as_str(min_v),
        'max': _as_str(max_v),
        'top_values': top_values,
    }


# --------------------------------------------------------------------------- #
# Numeric                                                                     #
# --------------------------------------------------------------------------- #

NUMERIC_PREFIXES = (
    'TINYINT', 'SMALLINT', 'INTEGER', 'BIGINT', 'HUGEINT',
    'UTINYINT', 'USMALLINT', 'UINTEGER', 'UBIGINT',
    'FLOAT', 'REAL', 'DOUBLE', 'DECIMAL', 'NUMERIC',
)


def _is_numeric(t: str) -> bool:
    return any(t.startswith(p) for p in NUMERIC_PREFIXES)


def _numeric_stats(c, qsub: str, qcol: str) -> dict:
    row = c.execute(f"""
        select  sum(case when {qcol} = 0 then 1 else 0 end)    as zeros
        ,       sum(case when {qcol} < 0 then 1 else 0 end)    as negatives
        ,       avg({qcol})                                    as mean_v
        ,       median({qcol})                                 as median_v
        ,       stddev_samp({qcol})                            as stddev_v
        ,       quantile_cont({qcol}, 0.25)                    as q1
        ,       quantile_cont({qcol}, 0.75)                    as q3
        from    {qsub}
        where   {qcol} is not null
        ;
    """).fetchone()
    zeros, negatives, mean_v, median_v, stddev_v, q1, q3 = row
    q1_f = float(q1) if q1 is not None else None
    q3_f = float(q3) if q3 is not None else None
    iqr = (q3_f - q1_f) if (q1_f is not None and q3_f is not None) else None
    outliers: int | None = None
    if iqr is not None:
        lo = q1_f - 1.5 * iqr
        hi = q3_f + 1.5 * iqr
        (o,) = c.execute(f"""
            select  count(*)
            from    {qsub}
            where   {qcol} is not null
            and     ({qcol} < ? or {qcol} > ?)
            ;
        """, [lo, hi]).fetchone()
        outliers = int(o)

    return {
        'zero_count': int(zeros or 0),
        'negative_count': int(negatives or 0),
        'mean': _round(mean_v),
        'median': _round(median_v),
        'stddev': _round(stddev_v),
        'q1': _round(q1_f),
        'q3': _round(q3_f),
        'iqr': _round(iqr),
        'outlier_count': outliers,
    }


# --------------------------------------------------------------------------- #
# VARCHAR                                                                     #
# --------------------------------------------------------------------------- #

def _varchar_stats(c, qsub: str, qcol: str, row_expr: str, common: dict) -> dict:
    row = c.execute(f"""
        select  min(length({qcol}))                                           as min_len
        ,       max(length({qcol}))                                           as max_len
        ,       sum(case when {qcol} = '' then 1 else 0 end)                  as empty_cnt
        ,       sum(case when {qcol} ~ '^\\s+$' then 1 else 0 end)             as ws_only_cnt
        ,       sum(case when {qcol} <> trim({qcol}) then 1 else 0 end)       as trim_diff_cnt
        ,       sum(case when position(' ' in {qcol}) > 0 then 1 else 0 end)  as has_space_cnt
        ,       sum(case when {qcol} ~ ? then 1 else 0 end)                   as email_cnt
        from    {qsub}
        where   {qcol} is not null
        ;
    """, [EMAIL_RE]).fetchone()
    min_len, max_len, empty_cnt, ws_only_cnt, trim_diff_cnt, has_space_cnt, email_cnt = row

    # 1st-instance ordinals for the min-length and max-length rows.
    min_first_row = max_first_row = None
    if min_len is not None and max_len is not None:
        r = c.execute(f"""
            with cte_tagged as
            (   select  length({qcol})  as n
                ,       {row_expr}      as src_row
                from    {qsub}
                where   {qcol} is not null
            )
            select  (select src_row from cte_tagged where n = ? order by src_row limit 1) as min_first
            ,       (select src_row from cte_tagged where n = ? order by src_row limit 1) as max_first
            ;
        """, [int(min_len), int(max_len)]).fetchone()
        min_first_row, max_first_row = r

    # Type-sniff preview; % that TRY_CASTs cleanly.
    sniff = c.execute(f"""
        select
            sum(case when try_cast({qcol} as date)      is not null then 1 else 0 end) as ok_date
        ,   sum(case when try_cast({qcol} as timestamp) is not null then 1 else 0 end) as ok_ts
        ,   sum(case when try_cast({qcol} as bigint)    is not null then 1 else 0 end) as ok_int
        ,   sum(case when try_cast({qcol} as double)    is not null then 1 else 0 end) as ok_double
        ,   sum(case when try_cast({qcol} as boolean)   is not null then 1 else 0 end) as ok_bool
        ,   count(*)                                                                   as non_null
        from    {qsub}
        where   {qcol} is not null
        ;
    """).fetchone()
    ok_date, ok_ts, ok_int, ok_double, ok_bool, non_null = sniff
    non_null = int(non_null or 0)

    def pct(ok: Any) -> float:
        return round((int(ok or 0) / non_null * 100.0), 2) if non_null else 0.0

    return {
        'min_length': int(min_len) if min_len is not None else None,
        'max_length': int(max_len) if max_len is not None else None,
        'min_length_first_row': int(min_first_row) if min_first_row is not None else None,
        'max_length_first_row': int(max_first_row) if max_first_row is not None else None,
        'empty_string_count': int(empty_cnt or 0),
        'whitespace_only_count': int(ws_only_cnt or 0),
        'leading_or_trailing_space_count': int(trim_diff_cnt or 0),
        'contains_space_count': int(has_space_cnt or 0),
        'email_match_count': int(email_cnt or 0),
        'type_sniff': {
            'date_pct':      pct(ok_date),
            'timestamp_pct': pct(ok_ts),
            'integer_pct':   pct(ok_int),
            'double_pct':    pct(ok_double),
            'boolean_pct':   pct(ok_bool),
        },
    }


# --------------------------------------------------------------------------- #
# Temporal                                                                    #
# --------------------------------------------------------------------------- #

def _temporal_stats(c, qsub: str, qcol: str) -> dict:
    row = c.execute(f"""
        select  sum(case when {qcol} > now() then 1 else 0 end)  as future_cnt
        ,       count(distinct date_part('year', {qcol}))        as unique_years
        ,       min({qcol})                                      as earliest
        ,       max({qcol})                                      as latest
        from    {qsub}
        where   {qcol} is not null
        ;
    """).fetchone()
    future_cnt, unique_years, earliest, latest = row
    range_days: int | None = None
    if earliest is not None and latest is not None:
        (days,) = c.execute("""
            select  cast(date_diff('day', cast(? as timestamp), cast(? as timestamp)) as bigint)
            ;
        """, [str(earliest), str(latest)]).fetchone()
        range_days = int(days) if days is not None else None

    return {
        'future_count': int(future_cnt or 0),
        'unique_years': int(unique_years or 0),
        'earliest': _as_str(earliest),
        'latest': _as_str(latest),
        'range_days': range_days,
    }


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _as_str(v: Any) -> str | None:
    return None if v is None else str(v)


def _round(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None
