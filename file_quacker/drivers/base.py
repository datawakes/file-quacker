"""Dialect + Driver protocols and the shared probe-result types.

The DDL generator runs **dialect-agnostic probes** against the data
(min/max, p90 scale, etc.) and feeds the results into a ``Dialect``
that knows how to render the corresponding type name in its target
language.  Splitting the probe (universal) from the rendering
(per-dialect) keeps every dialect tiny; most are a couple dozen
lines of name lookups.

``Driver`` is the operations interface (connect / list / export /
import / cancel).  Phase 1 only wires up dialects; drivers land in a
later phase.  The protocol is here now so module structure doesn't
churn when we add the first non-SQL-Server driver.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable


# --------------------------------------------------------------------------- #
# Probe results; what the universal probes hand to the dialect for naming.   #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class NumericResult:
    """Outcome of the unified numeric probe.

    ``kind`` is what the dialect should emit:

      * ``'int'``      narrowest int family that holds [mn, mx]
      * ``'decimal'``  fixed-precision (precision, scale) sized to
                       the data
      * ``'fallback'`` values couldn't be characterized (mixed
                         scientific, all NaN/Inf, etc.); dialect picks
                         a wide carrier
    """
    kind: Literal['int', 'decimal', 'fallback']
    mn: int | None = None        # int range (kind='int')
    mx: int | None = None
    precision: int | None = None  # decimal sizing (kind='decimal')
    scale: int | None = None


@dataclass(frozen=True)
class TimestampResult:
    """Outcome of the timestamp probe.  The probe captures whether
    pre-1753 dates are present (matters for SQL Server's ``datetime``),
    whether sub-millisecond precision is used, and whether every value
    has its time component at midnight (lets the caller downgrade to
    DATE)."""
    needs_subsecond: bool
    pre_1753: bool
    midnight_only: bool = False


@dataclass(frozen=True)
class StringResult:
    """Outcome of the string-length probe."""
    min_len: int
    max_len: int
    fixed: bool                  # min_len == max_len (good for char(n))


# --------------------------------------------------------------------------- #
# Dialect protocol                                                            #
# --------------------------------------------------------------------------- #

@runtime_checkable
class Dialect(Protocol):
    """Pure type-and-identifier mapping.  Stateless; no connection."""

    id: str
    display_name: str
    # When False, the BOOLEAN suggestion path runs an extra strict check on
    # VARCHAR sources before emitting `format_boolean()` – DuckDB happily
    # casts Yes/No/Y/N to bool, but SQL Server's bit only takes 0/1/true/
    # false, so a Yes/No column passed through as bit fails on insert.
    boolean_accepts_yes_no: bool = True

    def quote_ident(self, name: str) -> str: ...

    # Numeric paths ---------------------------------------------------- #
    def format_int(self, mn: int, mx: int) -> str | None:
        """Narrowest int that holds [mn, mx], or None if out of range
        (caller falls back to decimal)."""
        ...

    def format_decimal(self, precision: int, scale: int) -> str: ...

    def format_numeric_fallback(self) -> str:
        """When values can't be characterized; the widest safe carrier."""
        ...

    # Temporal --------------------------------------------------------- #
    def format_date(self) -> str: ...
    def format_time(self) -> str: ...
    def format_timestamp(self, info: TimestampResult) -> str: ...

    # Text ------------------------------------------------------------- #
    def format_string(self, info: StringResult) -> str: ...

    # Misc ------------------------------------------------------------- #
    def format_boolean(self) -> str: ...
    def format_uuid(self) -> str: ...
    def format_binary(self, max_len: int | None) -> str: ...
    def format_unknown(self) -> str:
        """Catch-all for types we don't know how to map."""
        ...


# --------------------------------------------------------------------------- #
# Driver protocol; operations against a live database                        #
# --------------------------------------------------------------------------- #
# Phase 1: only the shape; concrete drivers come in a later phase.            #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class FieldSpec:
    """One field in a driver's connection form.  The frontend reads
    this and renders the right input.  Keep it small; anything
    fancy gets handled at the driver level."""
    key: str                     # form field name; round-tripped in conn dict
    label: str                   # human-readable
    kind: Literal['text', 'password', 'number', 'select', 'checkbox']
    required: bool = True
    default: Any = None
    placeholder: str | None = None
    options: list[str] | None = None  # for kind='select'
    # When True (or when kind == 'password'), this field is a *secret*:
    # the credential service stores it in the OS vault (keyring) instead
    # of the plaintext connections.json.  Lets a driver mark non-password
    # secrets (API keys, tokens, key passphrases) without the credential
    # layer knowing anything driver-specific.
    secret: bool = False


@runtime_checkable
class Driver(Protocol):
    id: str
    display_name: str
    dialect: Dialect

    @property
    def connection_form(self) -> list[FieldSpec]: ...

    def is_available(self) -> dict:
        """Per-driver availability check.  E.g. SQL Server returns the
        installed ODBC driver list; Postgres returns whether the
        DuckDB extension loaded.  Shape:
        ``{'ok': bool, 'message': str, ...driver-specific fields...}``."""
        ...

    def connect(self, conn_opts: dict) -> Any:
        """Return whatever connection handle the driver wants to use
        for subsequent operations.  Lifetime is managed by the caller
        (`with` block typical)."""
        ...

    def list_databases(self, conn) -> list[str]: ...
    def list_schemas(self, conn, database: str) -> list[str]: ...
    def list_tables(self, conn, database: str, schema: str) -> list[str]: ...

    # Export / import are intentionally typed loosely here; the
    # concrete payloads live with the export module.  When a real
    # second driver lands we'll tighten these.
    def export_table(self, conn, **kwargs) -> dict: ...
    def import_table(self, conn, **kwargs) -> dict: ...
    def cancel(self) -> None: ...
