"""Persistent DuckDB session.

The whole app shares one in-memory DuckDB database.  `conn()` returns a
fresh `.cursor()` per call because pywebview dispatches every js_api
invocation on a worker thread, and DuckDB's Python root connection is
not safe across threads; cross-thread reads can miss recently created
tables/views.  Cursors are thread-local and share the catalog, which is
what we want.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from threading import RLock

import duckdb

_root: duckdb.DuckDBPyConnection | None = None
_temp_dir: Path | None = None
_lock = RLock()


def _ensure_root() -> duckdb.DuckDBPyConnection:
    global _root, _temp_dir
    with _lock:
        if _root is None:
            _temp_dir = Path(tempfile.mkdtemp(prefix='fq_duckdb_'))
            _root = duckdb.connect(database=':memory:')
            _root.execute(f"""
                set temp_directory = '{_temp_dir.as_posix()}'
                ;
            """)
        return _root


def conn() -> duckdb.DuckDBPyConnection:
    """Return a thread-local cursor sharing the catalog with every other caller."""
    return _ensure_root().cursor()


def close() -> None:
    """Tear down the connection and remove the spill directory."""
    global _root, _temp_dir
    with _lock:
        if _root is not None:
            _root.close()
            _root = None
        if _temp_dir is not None and _temp_dir.exists():
            import shutil
            shutil.rmtree(_temp_dir, ignore_errors=True)
            _temp_dir = None


_SAFE_IDENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def is_safe_ident(name: str) -> bool:
    """True when a name needs no quoting (matches `[A-Za-z_][A-Za-z0-9_]*`)."""
    return bool(_SAFE_IDENT.match(name))


def quote_ident(name: str) -> str:
    """Quote a DuckDB identifier, escaping embedded double quotes.

    Used everywhere DDL / DML composes user-provided names; f-string
    concatenation of raw names into SQL is a known injection shape.
    """
    return '"' + name.replace('"', '""') + '"'


def display_ident(name: str) -> str:
    """Return ``name`` bare when safe, otherwise double-quoted.  Used
    when emitting human-facing SQL where bare names read better."""
    return name if is_safe_ident(name) else quote_ident(name)
