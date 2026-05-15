"""An export's row source: either a loaded table or an ad-hoc SQL query.

``materialize()`` returns a DuckDB identifier the caller can quote with
``db.quote_ident``, plus a cleanup callable.  For queries it creates a
throwaway table the column probes can hit directly; without that, every
probe would re-run the user's query."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Callable, Literal

from . import db


@dataclass
class ExportSource:
    kind: Literal['table', 'query']
    name: str | None = None  # required when kind='table'
    sql: str | None = None   # required when kind='query'

    def materialize(self) -> tuple[str, Callable[[], None]]:
        if self.kind == 'table':
            if not self.name:
                raise ValueError("ExportSource(kind='table') requires name")
            return self.name, _noop
        if self.kind == 'query':
            if not self.sql:
                raise ValueError("ExportSource(kind='query') requires sql")
            # Regular table, not TEMP: DuckDB temp tables aren't shared
            # across cursors, and every probe pulls its own cursor.  The
            # `__fq_export_` prefix is filtered out by `api.list_tables`
            # so the throwaway never shows in the sidebar -- keep the
            # two in sync.  The uuid suffix avoids collisions when two
            # query exports start in the same millisecond on Windows.
            tbl = f'__fq_export_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}'
            qtbl = db.quote_ident(tbl)
            # Wrap the user's SQL in a subquery so trailing statements
            # (e.g. `select 1; drop table x;`) are rejected by the
            # parser instead of executing silently.
            inner = self.sql.strip().rstrip(';').strip()
            db.conn().execute(f"""
                create table {qtbl} as
                select * from (
                    {inner}
                )
                ;
            """)
            def _cleanup() -> None:
                # Best-effort drop.  The caller runs us inside a
                # `finally`; raising here would hide the real error.
                try:
                    db.conn().execute(f"""
                        drop table if exists {qtbl}
                        ;
                    """)
                except Exception:
                    pass
            return tbl, _cleanup
        raise ValueError(f'unknown kind: {self.kind!r}')


def _noop() -> None:
    return None
