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


from . import db, derive
from .drivers import (
    Dialect,
    NumericResult,
    StringResult,
    TimestampResult,
    dialect_for,
)


_BIGINT_MIN = -(1 << 63)
_BIGINT_MAX = (1 << 63) - 1

# 1753-01-01 is the lower bound for SQL Server `datetime`; values
# outside require `datetime2`.  The probe captures this as a fact;
# whether it matters is up to the dialect.
_DATETIME_FLOOR = '1753-01-01'


# --------------------------------------------------------------------------- #
# Public entry point                                                          #
# --------------------------------------------------------------------------- #

def generate(table_name: str, dialect: str = 'sqlserver') -> str:
    """Render a CREATE TABLE statement in `dialect`.  Defaults to
    SQL Server; any registered dialect id is valid."""
    d = dialect_for(dialect)
    c = db.conn()
    qtable = db.quote_ident(table_name)
    rows = c.execute(f"""
        pragma table_info({qtable})
        ;
    """).fetchall()
    if not rows:
        raise ValueError(f'no columns for {table_name}')

    col_infos: list[dict] = []
    for row in rows:
        name = row[1]
        # `_src_row_num` is internal metadata; never appears in DDL.
        if name == '_src_row_num':
            continue
        duck_type = row[2]
        nullable = _has_nulls(c, qtable, name)
        sql_type = _column_type(c, qtable, name, duck_type, d)
        col_infos.append({'name': name, 'sql_type': sql_type, 'nullable': nullable})

    return _format(table_name, col_infos, d)


# --------------------------------------------------------------------------- #
# Type resolution                                                             #
# --------------------------------------------------------------------------- #

def _has_nulls(c, qtable: str, col_name: str) -> bool:
    qcol = db.quote_ident(col_name)
    (n, non_null) = c.execute(f"""
        select  count(*)
        ,       count({qcol})
        from    {qtable}
        ;
    """).fetchone()
    return int(n) != int(non_null)


def _column_type(c, qtable: str, col_name: str, duck_type: str,
                 d: Dialect) -> str:
    """Pick the right probe for `duck_type` and hand the result to
    `d` for naming."""
    t = duck_type.upper()
    qcol = db.quote_ident(col_name)

    # ----- exhaustive source types: every native value fits target ----- #
    if t in ('BOOLEAN', 'BOOL'):    return d.format_boolean()
    if t == 'DATE':                 return d.format_date()
    if t == 'TIME':                 return d.format_time()
    if t == 'UUID':                 return d.format_uuid()

    # ----- branches that need a data probe ----------------------------- #
    if t.startswith('TIMESTAMP'):
        return d.format_timestamp(_probe_timestamp(c, qtable, qcol))
    if t == 'BLOB':
        return d.format_binary(_probe_blob_len(c, qtable, qcol))

    if (t in ('FLOAT', 'REAL', 'DOUBLE')
            or _is_integer(t)
            or t.startswith('DECIMAL')
            or t.startswith('NUMERIC')):
        return _render_numeric(c, qtable, qcol, d)

    if t == 'VARCHAR':
        suggested, _fmt = _varchar_type_suggestion(qtable, col_name)
        if suggested:
            up = suggested.upper()
            if up == 'BOOLEAN':     return d.format_boolean()
            if up == 'DATE':        return d.format_date()
            if up == 'TIMESTAMP':
                return d.format_timestamp(_probe_timestamp(c, qtable, qcol))
            if (up == 'BIGINT'
                    or up in ('DOUBLE', 'FLOAT', 'REAL')
                    or up.startswith('DECIMAL')
                    or up.startswith('NUMERIC')):
                return _render_numeric(c, qtable, qcol, d)
        return d.format_string(_probe_string(c, qtable, qcol))

    return d.format_unknown()


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
    """Universal numeric probe.

    Steps:
      1. Cast every non-null value to text.  Filter to plain decimal
         literals (no scientific notation).
      2. If no plain values, or a plain+scientific mix, return
         ``fallback`` – caller / dialect picks a wide carrier.
      3. Compute max integer-side digit count, the trimmed p90 scale,
         and a `has_dot` flag (any value with a fractional part).
         p90 (not max) keeps a single Excel-compute artifact like
         ``3.66666666666667`` from inflating every row's scale.
      4. If neither has_dot nor any trimmed scale, narrow to the
         smallest int family that holds min/max; fall back to
         ``decimal(P, 0)`` when values exceed bigint range.
      5. Otherwise emit ``decimal(max_int + scale, scale)`` capped at
         (38, 10), where scale = max(p90_trimmed, has_dot ? 1 : 0).
         The has_dot floor preserves a column's decimal nature even
         when every value is `.00`-padded – important for VARCHAR
         exports (`nvarchar('5.00') → tinyint` would fail; →
         `decimal(P, 1)` succeeds).
    """
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
            -- Leading-zero strings (e.g. "010876", "01234") aren't
            -- numbers; they're identifiers that must round-trip as
            -- text.  Excluding them here pushes the column to fallback
            -- rendering, which the caller handles as varchar(N).
            where   regexp_matches(s, '^[+-]?(0|[1-9]\\d*)(\\.\\d+)?$')
        )
        select  (select count(*) from cte_strs)   as n_total
        ,       count(*)                          as n_plain
        ,       max(ilen)                         as max_int
        ,       quantile_disc(scl, 0.9)           as p90_scale
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
    p90_trim  = min(int(row[3] or 0), 10)
    has_dot   = int(row[4] or 0) == 1
    scale     = max(p90_trim, 1 if has_dot else 0)

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
    """Whether the column needs sub-millisecond precision and whether
    any value precedes 1753-01-01.  SQL Server cares about both;
    DuckDB / Postgres don't (their `timestamp` covers everything)."""
    row = c.execute(f"""
        select  min(try_cast({qcol} as timestamp))     as mn
        ,       max(extract(microsecond from try_cast({qcol} as timestamp))) as mx_us
        from    {qtable}
        where   {qcol} is not null
        ;
    """).fetchone()
    if row is None or row[0] is None:
        return TimestampResult(needs_subsecond=False, pre_1753=False)
    mn = str(row[0])
    mx_us = int(row[1] or 0)
    return TimestampResult(
        needs_subsecond=(mx_us % 1000) != 0,
        pre_1753=(mn < _DATETIME_FLOOR),
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


def _probe_string(c, qtable: str, qcol: str) -> StringResult:
    """Min / max string length for sizing varchar / char."""
    row = c.execute(f"""
        select  min(length({qcol}))
        ,       max(length({qcol}))
        from    {qtable}
        where   {qcol} is not null
        ;
    """).fetchone()
    if row is None or row[1] is None:
        return StringResult(min_len=0, max_len=1, fixed=False)
    min_len = int(row[0])
    max_len = max(1, int(row[1]))
    return StringResult(min_len=min_len, max_len=max_len, fixed=(min_len == max_len))


def _varchar_type_suggestion(qtable: str, col_name: str) -> tuple[str | None, str | None]:
    """Run the Clone analyzer at the strict 100% threshold to pick
    the column's umbrella (BIGINT, DOUBLE, DECIMAL, DATE, TIMESTAMP,
    BOOLEAN); a single non-conforming value would break the export
    bind, so the suggestion has to hold for every non-null row."""
    analysis = derive._analyze_one(qtable, col_name)
    return derive._pick_suggestion(analysis, threshold=derive.STRICT_THRESHOLD)


def _is_integer(t: str) -> bool:
    return t in (
        'TINYINT', 'SMALLINT', 'INTEGER', 'BIGINT', 'HUGEINT',
        'UTINYINT', 'USMALLINT', 'UINTEGER', 'UBIGINT',
    )


def _sqlserver_type(c, qtable: str, col_name: str, duck_type: str) -> str:
    """SQL Server column type for ``col_name``, sized from observed values."""
    return _column_type(c, qtable, col_name, duck_type, dialect_for('sqlserver'))


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
