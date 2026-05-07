"""SQL Server dialect; type / identifier mapping only.

This is the rendering side of the DDL pipeline; the actual data
probes (min/max, p90 scale, string length, timestamp boundary) live
in ``ddl.py`` and feed ``NumericResult`` / ``TimestampResult`` /
``StringResult`` instances into these methods.

Decisions encoded here:
  * Identifiers use ``[brackets]`` when not bare-safe.
  * ``float`` / ``real`` are intentionally **never emitted**; exports
    bind through ODBC as decimal and IEEE round-trip would lose
    precision.  ``decimal(38, 10)`` is the wide fallback.
  * ``datetime`` rejects pre-1753 and truncates to ~3.33 ms.  When
    either boundary is exceeded we promote to ``datetime2``.
  * ``char(n)`` is preferred over ``varchar(n)`` when min_len ==
    max_len <= 8; keeps short fixed-length codes from carrying a
    length-prefix overhead.
  * ``binary``/``varbinary``: above 8000 bytes, SQL Server requires
    ``varbinary(max)``; otherwise emit a sized ``varbinary(N)``.
"""

from __future__ import annotations

import re

from .base import Dialect, StringResult, TimestampResult


_SAFE_IDENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

_BIGINT_MIN = -(1 << 63)
_BIGINT_MAX = (1 << 63) - 1


class SqlServerDialect:
    id: str = 'sqlserver'
    display_name: str = 'SQL Server'
    # bit only accepts 0/1/true/false on insert; Yes/No round-trips fine in
    # DuckDB but blows up at the pyodbc bind. Keep the BOOLEAN suggestion
    # to columns whose values are already in a bit-compatible shape.
    boolean_accepts_yes_no: bool = False

    # ----- identifiers --------------------------------------------- #
    def quote_ident(self, name: str) -> str:
        if _SAFE_IDENT.match(name):
            return name
        return '[' + name.replace(']', ']]') + ']'

    # ----- numeric ------------------------------------------------- #
    def format_int(self, mn: int, mx: int) -> str | None:
        if 0          <= mn and mx <=          255: return 'tinyint'
        if -32768     <= mn and mx <=        32767: return 'smallint'
        if -2147483648 <= mn and mx <= 2147483647:  return 'int'
        if _BIGINT_MIN <= mn and mx <= _BIGINT_MAX: return 'bigint'
        return None

    def format_decimal(self, precision: int, scale: int) -> str:
        if scale == 0:
            return f'decimal({min(precision, 38)}, 0)'
        p = min(precision, 38)
        return f'decimal({p}, {scale})'

    def format_numeric_fallback(self) -> str:
        return 'decimal(38, 10)'

    # ----- temporal ------------------------------------------------ #
    def format_date(self) -> str:
        return 'date'

    def format_time(self) -> str:
        return 'time'

    def format_timestamp(self, info: TimestampResult) -> str:
        return 'datetime2' if (info.pre_1753 or info.needs_subsecond) else 'datetime'

    # ----- text ---------------------------------------------------- #
    def format_string(self, info: StringResult) -> str:
        max_len = max(1, info.max_len)
        if info.fixed and max_len <= 8:
            return f'char({max_len})'
        return f'varchar({max_len})'

    # ----- misc ---------------------------------------------------- #
    def format_boolean(self) -> str:
        return 'bit'

    def format_uuid(self) -> str:
        return 'uniqueidentifier'

    def format_binary(self, max_len: int | None) -> str:
        if max_len is None or max_len <= 0 or max_len > 8000:
            return 'varbinary(max)'
        return f'varbinary({max_len})'

    def format_unknown(self) -> str:
        return 'nvarchar(max)'


# Sanity check at import time: matches the protocol.
_: Dialect = SqlServerDialect()
