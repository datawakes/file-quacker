"""DuckDB dialect; used when generating DDL aimed at DuckDB itself.

Not strictly a "destination"; DuckDB is our backing store; but we
emit DuckDB DDL from the Show-DDL drawer and from the Clone dialog,
so the same dialect interface fits.

Identifiers use ``"double quotes"`` when not bare-safe.  Type names
match the ones DuckDB returns from ``pragma table_info`` so a
round-trip is identity.
"""

from __future__ import annotations

import re

from .base import Dialect, StringResult, TimestampResult


_SAFE_IDENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

_BIGINT_MIN = -(1 << 63)
_BIGINT_MAX = (1 << 63) - 1


class DuckDBDialect:
    id: str = 'duckdb'
    display_name: str = 'DuckDB'

    def quote_ident(self, name: str) -> str:
        if _SAFE_IDENT.match(name):
            return name
        return '"' + name.replace('"', '""') + '"'

    def format_int(self, mn: int, mx: int) -> str | None:
        if 0          <= mn and mx <=          255: return 'tinyint'
        if -32768     <= mn and mx <=        32767: return 'smallint'
        if -2147483648 <= mn and mx <= 2147483647:  return 'integer'
        if _BIGINT_MIN <= mn and mx <= _BIGINT_MAX: return 'bigint'
        return None

    def format_decimal(self, precision: int, scale: int) -> str:
        return f'decimal({min(precision, 38)}, {scale})'

    def format_numeric_fallback(self) -> str:
        return 'double'

    def format_date(self) -> str:
        return 'date'

    def format_time(self) -> str:
        return 'time'

    def format_timestamp(self, info: TimestampResult) -> str:
        # DuckDB's `timestamp` is microsecond precision; no pre-1753
        # restriction.  One type covers both probe outcomes.
        return 'timestamp'

    def format_string(self, info: StringResult) -> str:
        return 'varchar'

    def format_boolean(self) -> str:
        return 'boolean'

    def format_uuid(self) -> str:
        return 'uuid'

    def format_binary(self, max_len: int | None) -> str:
        return 'blob'

    def format_unknown(self) -> str:
        return 'varchar'


_: Dialect = DuckDBDialect()
