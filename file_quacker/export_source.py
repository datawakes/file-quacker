"""Abstraction over an export's row source: either a loaded table or an
ad-hoc SQL query.  ``materialize()`` returns a plain DuckDB identifier
that can be quoted with ``db.quote_ident`` and a cleanup callable the
caller must invoke when done.  For queries this creates a throwaway
view; for tables it is a no-op."""

from __future__ import annotations

import time
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
            view = f'fq_export_{int(time.time() * 1000)}'
            qview = db.quote_ident(view)
            # Wrap the user's SQL in a subquery so trailing statements
            # (e.g. `select 1; drop table x;`) are rejected by the
            # parser instead of executing silently.
            inner = self.sql.strip().rstrip(';').strip()
            db.conn().execute(f"""
                create or replace view {qview} as
                select * from (
                    {inner}
                )
                ;
            """)
            def _cleanup() -> None:
                try:
                    db.conn().execute(f"""
                        drop view if exists {qview}
                        ;
                    """)
                except Exception:
                    pass
            return view, _cleanup
        raise ValueError(f'unknown kind: {self.kind!r}')


def _noop() -> None:
    return None
