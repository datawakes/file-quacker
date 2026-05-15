"""Generate CREATE TABLE DDL for a loaded table.

The output type for every column is derived from the actual values,
not copied from the DuckDB source type; the source type is used
only to route the column to the right probe.

The probes themselves are dialect-agnostic; they return the *facts*
(int range, decimal precision/scale, max string length, timestamp
boundary properties) and a target ``Dialect`` (see
``file_quacker/drivers/``) renders those facts into its own type
vocabulary.  Adding a new dialect (Postgres, MySQL, Snowflake) is a
matter of writing a small naming class; no ddl.py changes required.

| Source kind                | Probe                                     |
|----------------------------|-------------------------------------------|
| Numeric (int/decimal/float)| `_probe_numeric` – single unified probe   |
| TIMESTAMP                  | `_probe_timestamp` – 1753 floor + subsec  |
| BLOB                       | `_probe_blob_len`                         |
| VARCHAR                    | Clone analyzer; numeric umbrellas reuse   |
|                            | `_probe_numeric`; else `_probe_string`    |
| DATE / TIME / BOOLEAN /    | Source type is exhaustive – every native  |
| UUID                       | value fits a single output type           |
"""

from __future__ import annotations

import re

from . import db, derive, inspect_progress, instrument
from .drivers import (
    Dialect,
    NumericResult,
    StringResult,
    TimestampResult,
    dialect_for,
)
from .export_file import WS_TRIM_PATTERN


_DECIMAL_RE = re.compile(r'^DECIMAL\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)$', re.IGNORECASE)


_BIGINT_MIN = -(1 << 63)
_BIGINT_MAX = (1 << 63) - 1

# 1753-01-01 is the lower bound for SQL Server `datetime`; values
# outside require `datetime2`.  The probe captures this as a fact;
# whether it matters is up to the dialect.
_DATETIME_FLOOR = '1753-01-01'


# --------------------------------------------------------------------------- #
# Public entry point                                                          #
# --------------------------------------------------------------------------- #

def generate(table_name: str, dialect: str = 'sqlserver',
             trim_strings: bool = False) -> str:
    """Render a CREATE TABLE statement in `dialect`.  Defaults to
    SQL Server; any registered dialect id is valid.

    ``trim_strings`` mirrors the export-time option: when on, string
    columns are sized from their trimmed lengths so a downstream
    char(N) target won't pad whitespace back into the trimmed values.
    """
    # Drain any leftover events from a prior run so this run's summary
    # only reflects work this call did.
    instrument.take()
    with instrument.timed('ddl.generate', table=table_name, dialect=dialect):
        d = dialect_for(dialect)
        c = db.conn()
        qtable = db.quote_ident(table_name)
        rows = c.execute(f"""
            pragma table_info({qtable})
            ;
        """).fetchall()
        if not rows:
            raise ValueError(f'no columns for {table_name}')

        # _src_row_num is internal metadata; never appears in DDL.
        cols = [(r[1], r[2]) for r in rows if r[1] != '_src_row_num']

        col_infos: list[dict] = []
        with inspect_progress.session(total=len(cols)):
            for i, (name, duck_type) in enumerate(cols):
                with instrument.timed('column.total', col=name, src=duck_type):
                    nullable = _has_nulls(c, qtable, name)
                    sql_type = _column_type(c, qtable, name, duck_type, d, trim_strings)
                col_infos.append({'name': name, 'sql_type': sql_type, 'nullable': nullable})
                inspect_progress.set_progress(current=i + 1)

        rendered = _format(table_name, col_infos, d)

    if instrument.enabled:
        return rendered + '\n' + instrument.format_summary(instrument.take())
    return rendered


# --------------------------------------------------------------------------- #
# Type resolution                                                             #
# --------------------------------------------------------------------------- #

def _has_nulls(c, qtable: str, col_name: str) -> bool:
    qcol = db.quote_ident(col_name)
    with instrument.timed('_has_nulls', col=col_name):
        (n, non_null) = c.execute(f"""
            select  count(*)
            ,       count({qcol})
            from    {qtable}
            ;
        """).fetchone()
    return int(n) != int(non_null)


def _column_type(c, qtable: str, col_name: str, duck_type: str,
                 d: Dialect, trim_strings: bool = False,
                 analysis: dict | None = None) -> str:
    """Pick the right probe for `duck_type` and hand the result to
    `d` for naming.  ``trim_strings`` only affects string sizing; numeric
    / date / timestamp probes don't care."""
    t = duck_type.upper()
    qcol = db.quote_ident(col_name)

    # ----- exhaustive source types: every native value fits target ----- #
    if t in ('BOOLEAN', 'BOOL'):    return d.format_boolean()
    if t == 'DATE':                 return d.format_date()
    if t == 'TIME':                 return d.format_time()
    if t == 'UUID':                 return d.format_uuid()

    # ----- branches that need a data probe ----------------------------- #
    if t.startswith('TIMESTAMP'):
        info = _probe_timestamp(c, qtable, qcol)
        # Every value sits at midnight – render as DATE rather than padding
        # the column with `00:00:00` time components on every row.
        if info.midnight_only:
            return d.format_date()
        return d.format_timestamp(info)
    if t == 'BLOB':
        return d.format_binary(_probe_blob_len(c, qtable, qcol))

    if _is_integer(t):
        return _render_int_typed(c, qtable, qcol, d)

    if t.startswith('DECIMAL') or t.startswith('NUMERIC'):
        m = _DECIMAL_RE.match(t)
        if m:
            return d.format_decimal(int(m.group(1)), int(m.group(2)))
        return _render_numeric(c, qtable, qcol, d)

    if t in ('FLOAT', 'REAL', 'DOUBLE'):
        return _render_numeric(c, qtable, qcol, d)

    if t == 'VARCHAR':
        suggested, _fmt, _via_ts = _varchar_type_suggestion(qtable, col_name, analysis)
        if suggested:
            up = suggested.upper()
            if up == 'BOOLEAN':
                if (not getattr(d, 'boolean_accepts_yes_no', True)
                        and not _has_only_strict_boolean_values(c, qtable, qcol)):
                    return d.format_string(_probe_string(c, qtable, qcol, trim_strings))
                return d.format_boolean()
            if up == 'DATE':        return d.format_date()
            if up == 'TIMESTAMP':
                return d.format_timestamp(_probe_timestamp(c, qtable, qcol))
            if (up == 'BIGINT'
                    or up in ('DOUBLE', 'FLOAT', 'REAL')
                    or up.startswith('DECIMAL')
                    or up.startswith('NUMERIC')):
                return _render_numeric(c, qtable, qcol, d)
        return d.format_string(_probe_string(c, qtable, qcol, trim_strings))

    return d.format_unknown()


def _render_int_typed(c, qtable: str, qcol: str, d: Dialect) -> str:
    """Fast path for typed integer columns. The source type has already
    validated the values, so only the range is needed to choose the
    narrowest integer type."""
    with instrument.timed('_render_int_typed', col=qcol):
        row = c.execute(f"""
            select  min({qcol})
            ,       max({qcol})
            ,       count({qcol})
            from    {qtable}
            ;
        """).fetchone()
    if row is None or int(row[2] or 0) == 0:
        formatted = d.format_int(0, 0)
        return formatted if formatted is not None else d.format_decimal(1, 0)
    mn, mx = int(row[0]), int(row[1])
    formatted = d.format_int(mn, mx)
    if formatted is not None:
        return formatted
    precision = max(1, len(str(max(abs(mn), abs(mx)))))
    return d.format_decimal(min(precision, 38), 0)


def _render_numeric(c, qtable: str, qcol: str, d: Dialect) -> str:
    """Run the unified numeric probe and ask `d` to render the result."""
    res = _probe_numeric(c, qtable, qcol)
    if res.kind == 'fallback':
        return d.format_numeric_fallback()
    if res.kind == 'int':
        formatted = d.format_int(res.mn, res.mx)              # type: ignore[arg-type]
        if formatted is not None:
            return formatted
        # Out of int range for this dialect; fall through to decimal.
        precision = max(1, len(str(max(abs(res.mn or 0), abs(res.mx or 0)))))
        return d.format_decimal(min(precision, 38), 0)
    # res.kind == 'decimal'
    return d.format_decimal(res.precision or 1, res.scale or 0)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Data-driven probes (dialect-agnostic – return facts, not type names)        #
# --------------------------------------------------------------------------- #

def _probe_numeric(c, qtable: str, qcol: str) -> NumericResult:
    """Pick a numeric type from the values in the column.

    Casts each non-null value to text and keeps only plain decimal literals.
    Anything with scientific notation, currency, or a leading zero is left
    out and the column falls back to a wider carrier chosen by the dialect.

    From the surviving values the probe takes the max integer-side digit
    count, the max trimmed scale, and a `has_dot` flag. The source is
    trusted as-is — Excel float artifacts are already cleaned up at ingest.

    A column with no decimals narrows to the smallest int family that holds
    its min/max; otherwise we emit `decimal(max_int + scale, scale)`. The
    `has_dot` floor of 1 keeps `1.00`-padded columns from collapsing to int
    and breaking a VARCHAR export bound to a narrow integer column. When
    `max_int + scale` would exceed 38 we reduce scale first so the integer
    side is preserved.
    """
    with instrument.timed('_probe_numeric', col=qcol):
        row = c.execute(f"""
            with cte_strs as
            (   select  try_cast({qcol} as varchar) as s
                from    {qtable}
                where   {qcol} is not null
            ), cte_parts as
            (   select  s
                ,       length(split_part(replace(s, '-', ''), '.', 1))    as ilen
                ,       case when position('.' in s) > 0
                             then length(rtrim(split_part(s, '.', 2), '0'))
                             else 0 end                                    as scl
                ,       case when position('.' in s) > 0 then 1 else 0 end as has_dot
                from    cte_strs
                where   regexp_matches(s, '{derive.PLAIN_DECIMAL_RE}')
            )
            select  (select count(*) from cte_strs)   as n_total
            ,       count(*)                          as n_plain
            ,       max(ilen)                         as max_int
            ,       max(scl)                          as max_scale
            ,       max(has_dot)                      as has_dot
            from    cte_parts
            ;
        """).fetchone()

    n_total = int(row[0] or 0) if row else 0
    if n_total == 0:
        # Empty / all-NULL; sensible default is int.
        return NumericResult(kind='int', mn=0, mx=0)

    n_plain = int(row[1] or 0)
    if n_plain == 0 or n_plain < n_total:
        return NumericResult(kind='fallback')

    max_int   = max(int(row[2] or 1), 1)
    max_scl   = int(row[3] or 0)
    has_dot   = int(row[4] or 0) == 1
    scale     = max(max_scl, 1 if has_dot else 0)
    scale     = min(scale, max(0, 38 - max_int))

    if scale == 0:
        # Pure integer column; narrow on real bigint min/max.
        narrow = _narrowest_int_via_string(c, qtable, qcol)
        if narrow is not None:
            return NumericResult(kind='int', mn=narrow[0], mx=narrow[1])
        return NumericResult(kind='decimal', precision=min(max_int, 38), scale=0)

    precision = min(max_int + scale, 38)
    return NumericResult(kind='decimal', precision=precision, scale=scale)


def _narrowest_int_via_string(c, qtable: str, qcol: str) -> tuple[int, int] | None:
    """Compute (min, max) via try_cast(varchar → bigint).  Returns
    None when any value overflows bigint."""
    with instrument.timed('_narrowest_int_via_string', col=qcol):
        row = c.execute(f"""
            with cte as
            (   select  try_cast(try_cast({qcol} as varchar) as bigint) as v
                from    {qtable}
                where   {qcol} is not null
            )
            select  min(v)
            ,       max(v)
            ,       count(v)
            ,       count(*)
            from    cte
            ;
        """).fetchone()
    if row is None or int(row[3] or 0) == 0:
        return None
    if int(row[2] or 0) < int(row[3]):
        return None                                  # some value overflowed
    return int(row[0]), int(row[1])


def _probe_timestamp(c, qtable: str, qcol: str) -> TimestampResult:
    """Whether the column needs sub-millisecond precision, whether any
    value precedes 1753-01-01, and whether every value sits at midnight.
    SQL Server cares about the first two for the datetime / datetime2
    split; the midnight flag lets the caller downgrade to DATE."""
    ts_expr = f'try_cast({qcol} as timestamp)'
    with instrument.timed('_probe_timestamp', col=qcol):
        row = c.execute(f"""
            select  min({ts_expr})                          as mn
            ,       max(extract(microsecond from {ts_expr})) as mx_us
            ,       sum(case when {ts_expr} is not null
                              and {derive.has_time_sql(ts_expr)}
                         then 1 else 0 end)                  as with_time
            ,       count({qcol})                            as n
            from    {qtable}
            where   {qcol} is not null
            ;
        """).fetchone()
    if row is None or row[0] is None:
        return TimestampResult(needs_subsecond=False, pre_1753=False, midnight_only=False)
    mn = str(row[0])
    mx_us = int(row[1] or 0)
    with_time = int(row[2] or 0)
    n = int(row[3] or 0)
    return TimestampResult(
        needs_subsecond=(mx_us % 1000) != 0,
        pre_1753=(mn < _DATETIME_FLOOR),
        midnight_only=(n > 0 and with_time == 0),
    )


def _probe_blob_len(c, qtable: str, qcol: str) -> int | None:
    """Max byte length of a BLOB column.  ``None`` for empty / all-NULL."""
    row = c.execute(f"""
        select  max(octet_length({qcol}))
        from    {qtable}
        where   {qcol} is not null
        ;
    """).fetchone()
    if row is None or row[0] is None:
        return None
    return int(row[0])


def _probe_string(c, qtable: str, qcol: str,
                  trim_strings: bool = False) -> StringResult:
    """Min / max string length for sizing varchar / char.

    When ``trim_strings`` is on, the lengths are measured after the
    same whitespace strip the export pipeline applies. Without that, a
    column of `' abc '`-style values (all 5 chars raw, all 3 chars
    trimmed) would size as char(5), and SQL Server's char(5) padding
    would put the trimmed-off whitespace right back as trailing spaces.
    """
    expr = (f"regexp_replace({qcol}, '{WS_TRIM_PATTERN}', '', 'g')"
            if trim_strings else qcol)
    with instrument.timed('_probe_string', col=qcol):
        row = c.execute(f"""
            select  min(length({expr}))
            ,       max(length({expr}))
            from    {qtable}
            where   {qcol} is not null
            ;
        """).fetchone()
    if row is None or row[1] is None:
        return StringResult(min_len=0, max_len=1, fixed=False)
    min_len = int(row[0])
    max_len = max(1, int(row[1]))
    return StringResult(min_len=min_len, max_len=max_len, fixed=(min_len == max_len))


def _has_only_strict_boolean_values(c, qtable: str, qcol: str) -> bool:
    """Whether every non-null VARCHAR value in the column is one of the
    forms a strict BOOLEAN target accepts on insert: 0/1/true/false
    (case-insensitive). Yes/No/Y/N pass DuckDB's permissive try_cast but
    SQL Server's bit rejects them.
    """
    with instrument.timed('_has_only_strict_boolean_values', col=qcol):
        row = c.execute(f"""
            select  sum(case when lower({qcol}) in ('0', '1', 'true', 'false')
                             then 0 else 1 end)
            from    {qtable}
            where   {qcol} is not null
            ;
        """).fetchone()
    return int(row[0] or 0) == 0


def _varchar_type_suggestion(qtable: str, col_name: str,
                              analysis: dict | None = None) -> tuple[str | None, str | None, bool]:
    """Run the Clone analyzer at the strict 100% threshold to pick
    the column's umbrella (BIGINT, DOUBLE, DECIMAL, DATE, TIMESTAMP,
    BOOLEAN); a single non-conforming value would break the export
    bind, so the suggestion has to hold for every non-null row."""
    if analysis is None:
        analysis = derive._analyze_one(qtable, col_name)
    return derive._pick_suggestion(analysis, threshold=derive.STRICT_THRESHOLD)


def _is_integer(t: str) -> bool:
    return t in (
        'TINYINT', 'SMALLINT', 'INTEGER', 'BIGINT', 'HUGEINT',
        'UTINYINT', 'USMALLINT', 'UINTEGER', 'UBIGINT',
    )


def _sqlserver_type(c, qtable: str, col_name: str, duck_type: str,
                    trim_strings: bool = False,
                    analysis: dict | None = None) -> str:
    """SQL Server column type for ``col_name``, sized from observed values.

    Pass ``analysis`` when the caller already ran the VARCHAR analyzer."""
    return _column_type(c, qtable, col_name, duck_type,
                        dialect_for('sqlserver'), trim_strings, analysis)


# --------------------------------------------------------------------------- #
# Formatting                                                                  #
# --------------------------------------------------------------------------- #

def _format(table_name: str, cols: list[dict], d: Dialect) -> str:
    names = [d.quote_ident(c['name']) for c in cols]
    types = [c['sql_type'] for c in cols]
    name_w = max(len(n) for n in names)
    type_w = max(len(t) for t in types)

    lines = [f'create table {d.quote_ident(table_name)}']
    for i, (n, t, ci) in enumerate(zip(names, types, cols)):
        prefix = '(   ' if i == 0 else '  , '
        null_str = 'not null' if not ci['nullable'] else '    null'
        # 2-space gutters between name/type/null.  Padding lives in
        # the ljust so all columns align even when one name is much
        # longer than the others.
        lines.append(f'{prefix}{n.ljust(name_w)}  {t.ljust(type_w)}  {null_str}')
    lines.append(')')
    lines.append(';')
    return '\n'.join(lines)
