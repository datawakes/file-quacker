"""SQL Server driver; operations against a live SQL Server.

Wraps the existing functions in ``file_quacker/export.py`` (connect,
list databases / schemas, etc.) behind the generic ``Driver``
interface.  Adds two operations that previously didn't exist:

  * ``list_tables``; sources for the Import-from-DB flow.
  * ``import_table``; read a table or query into a workspace
    DuckDB table, applying an optional row cap.

Intentionally a thin layer; the existing pyodbc plumbing in
``export.py`` stays put and continues to be used by the Export side
of the app, unchanged.  Phase 4 will move Postgres / MySQL on top of
this same interface (mostly via DuckDB's ATTACH extensions, with a
much smaller per-driver footprint).
"""

from __future__ import annotations


from .base import Driver, FieldSpec
from .sqlserver_dialect import SqlServerDialect


# Connection-form spec the frontend uses to render a generic dialog.
# Order matters: anything that requires authentication to be valid
# (Database, in this case; the catalog can't be probed until creds
# resolve) sits after Password.  Each driver supplies its own
# fields; the dialog component doesn't need to know about driver-
# specific quirks.
_FORM: list[FieldSpec] = [
    FieldSpec('server',    'Server',       'text',    placeholder='localhost or host.domain'),
    FieldSpec('port',      'Port',         'number',  required=False, default=1433),
    FieldSpec('auth',      'Auth',         'select',  default='sql',
              options=['sql', 'windows']),
    FieldSpec('username',  'Username',     'text',     required=False),
    FieldSpec('password',  'Password',     'password', required=False),
    FieldSpec('azure',     'Azure SQL',    'select',   default='no',
              options=['no', 'yes']),
    FieldSpec('database',  'Database',     'text',    required=False),
]


class SqlServerDriver:
    id: str = 'sqlserver'
    display_name: str = 'SQL Server'
    dialect = SqlServerDialect()

    capabilities: tuple[str, ...] = ('export', 'import', 'ddl')

    @property
    def connection_form(self) -> list[FieldSpec]:
        return _FORM

    # ----- availability + connect ----- #
    def is_available(self) -> dict:
        from .. import export as export_mod
        return export_mod.check_odbc_driver()

    def _coerce_opts(self, conn_opts: dict):
        from .. import export as export_mod
        # Frontend may send "yes"/"no" strings for boolean-ish fields;
        # normalize before handing to the dataclass.
        d = dict(conn_opts)
        if isinstance(d.get('azure'), str):
            d['azure'] = d['azure'].lower() == 'yes'
        if 'port' in d and isinstance(d['port'], str):
            d['port'] = int(d['port']) if d['port'] else None
        return export_mod.ConnectionOptions(**d)

    def connect(self, conn_opts: dict, *, omit_database: bool = False, timeout: int = 10):
        from .. import export as export_mod
        return export_mod._connect(
            self._coerce_opts(conn_opts),
            timeout=timeout,
            omit_database=omit_database,
        )

    def test_connection(self, conn_opts: dict) -> dict:
        from .. import export as export_mod
        return export_mod.test_connection(self._coerce_opts(conn_opts))

    # ----- catalog ----- #
    def list_databases(self, conn_opts: dict) -> list[str]:
        from .. import export as export_mod
        return export_mod.list_databases(self._coerce_opts(conn_opts))

    def list_schemas(self, conn_opts: dict, database: str) -> list[str]:
        from .. import export as export_mod
        opts = self._coerce_opts({**conn_opts, 'database': database})
        return export_mod.list_schemas(opts)

    def list_tables(self, conn_opts: dict, database: str, schema: str) -> list[dict]:
        """Tables + views in `database.schema` with row counts.  Row
        counts come from `sys.dm_db_partition_stats` (cheap; doesn't
        scan the table); user-visible objects only."""
        opts = self._coerce_opts(conn_opts)
        opts.database = database
        with self.connect(opts.__dict__, omit_database=False, timeout=10) as conn:
            rows = conn.cursor().execute("""
                select  s.name                                   as schema_name
                ,       o.name                                   as table_name
                ,       case o.type when 'U' then 'table'
                             else 'view' end                     as kind
                ,       isnull(sum(p.row_count), 0)              as row_count
                from    sys.objects                              o
                join    sys.schemas                              s
                  on    s.schema_id = o.schema_id
                left
                join    sys.dm_db_partition_stats                p
                  on    p.object_id = o.object_id
                  and   p.index_id in (0, 1)
                where   o.type in ('U', 'V')
                and     s.name = ?
                group   by s.name, o.name, o.type
                order   by o.name
                ;
            """, schema).fetchall()
        return [
            {'schema': r[0], 'name': r[1], 'kind': r[2], 'row_count': int(r[3] or 0)}
            for r in rows
        ]

    # ----- import ----- #
    def import_table(self, conn_opts: dict, *,
                     database: str | None = None,
                     schema: str | None = None,
                     table: str | None = None,
                     query: str | None = None,
                     target_name: str,
                     row_cap: int | None = 1_000_000) -> dict:
        """Read a SQL Server table or query into a workspace DuckDB
        table.  Exactly one of ``(table, query)`` should be set.

        ``row_cap`` defaults to 1,000,000 rows; pass ``None`` to
        disable.  The cap is applied at fetch time (not via SQL Server
        TOP) so it's portable and so cancellation can interrupt mid-
        scan without leaving server-side cursors open.
        """
        from .. import import_db
        return import_db.import_via_pyodbc(
            driver=self,
            conn_opts=conn_opts,
            database=database,
            schema=schema,
            table=table,
            query=query,
            target_name=target_name,
            row_cap=row_cap,
        )

    def cancel(self) -> None:
        from .. import import_db
        import_db.cancel_active()


# Sanity check: matches the protocol.
_: Driver = SqlServerDriver()                    # type: ignore[assignment]
