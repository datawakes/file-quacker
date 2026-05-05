"""Pluggable dialect / driver registry.

Two orthogonal concepts live here:

  * ``Dialect``; pure type-and-identifier mapping for a target SQL
    language (SQL Server, Postgres, DuckDB, ...).  Used by the DDL
    generator and by anything that has to render `CREATE TABLE` or
    qualified identifiers.  Stateless; no connection required.

  * ``Driver``; operations against a live database (connect, list
    schemas/tables, export, import, cancel).  Each driver carries
    its preferred ``Dialect`` and a connection-form spec the UI
    uses to render the right inputs.  Stateful by definition.

The registry holds one instance per driver / dialect.  Lookups are by
short string id (``'sqlserver'``, ``'postgres'``, ``'duckdb'``) so
the API surface and the frontend can stay parametric.
"""

from __future__ import annotations

from .base import Dialect, Driver, NumericResult, TimestampResult, StringResult

# Concrete dialects are imported here so the registry is populated by
# the time anyone calls `dialect_for`.  Drivers (which wrap operations)
# come online in a later phase; for now only dialect support is
# wired up, so the existing DDL generator keeps working through this
# new indirection.
from .sqlserver_dialect import SqlServerDialect
from .duckdb_dialect import DuckDBDialect
from .sqlserver_driver import SqlServerDriver


_DIALECTS: dict[str, Dialect] = {
    'sqlserver': SqlServerDialect(),
    'duckdb':    DuckDBDialect(),
}

# Driver registry; operations against a live database.  Phase 3
# ships SQL Server only; Postgres / MySQL / SQLite / Snowflake
# follow in later phases without changing the registry shape.
_DRIVERS: dict[str, Driver] = {
    'sqlserver': SqlServerDriver(),                  # type: ignore[dict-item]
}


def dialect_for(dialect_id: str) -> Dialect:
    """Look up a dialect by id; raises ``KeyError`` for unknown ids
    so the caller can surface a clear message rather than fall
    through to an arbitrary default."""
    try:
        return _DIALECTS[dialect_id]
    except KeyError as ex:
        raise KeyError(
            f"unknown dialect {dialect_id!r}; "
            f"known: {sorted(_DIALECTS)}"
        ) from ex


def driver_for(driver_id: str) -> Driver:
    try:
        return _DRIVERS[driver_id]
    except KeyError as ex:
        raise KeyError(
            f"unknown driver {driver_id!r}; "
            f"known: {sorted(_DRIVERS)}"
        ) from ex


def known_dialects() -> list[str]:
    return sorted(_DIALECTS)


def known_drivers() -> list[str]:
    return sorted(_DRIVERS)


__all__ = [
    'Dialect',
    'Driver',
    'NumericResult',
    'TimestampResult',
    'StringResult',
    'dialect_for',
    'driver_for',
    'known_dialects',
    'known_drivers',
]
