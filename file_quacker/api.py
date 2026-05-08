"""Bridge surface exposed to the Vue frontend via pywebview `js_api`.

Every public method becomes callable as `window.pywebview.api.<method>()`
from the frontend, returning a Promise that resolves with the JSON result.

All methods either return a JSON-safe payload or raise; pywebview
surfaces the exception as a rejected Promise.
"""

from __future__ import annotations

import platform
import sys
from dataclasses import asdict
from typing import Any

import duckdb
import webview

from . import (
    __version__,
    db,
    ddl as ddl_mod,
    derive,
    export as export_mod,
    ingest,
    profile as profile_mod,
    sql as sql_mod,
)


class Api:
    # --------------------------------------------------------------------- #
    # System                                                                #
    # --------------------------------------------------------------------- #
    def ping(self) -> dict:
        return {
            'ok': True,
            'message': 'pong',
            'app_version': __version__,
            'python_version': sys.version.split()[0],
            'platform': platform.platform(),
            'duckdb_version': duckdb.__version__,
        }

    def set_window_title(self, title: str) -> dict:
        """Update the OS window title. e.g. ``File Quacker – /path/to.csv``."""
        win = webview.windows[0] if webview.windows else None
        if win is None:
            return {'ok': False}
        win.set_title(title)
        return {'ok': True}

    def clipboard_read(self) -> str:
        """Read system clipboard text via the OS, bypassing WebView2's
        permission prompt for ``navigator.clipboard.readText`` (which
        also tends to return empty even after the user clicks Allow).
        Win32-only; returns '' on other platforms or on failure."""
        if sys.platform != 'win32':
            return ''
        try:
            import ctypes
            from ctypes import wintypes
            CF_UNICODETEXT = 13
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            user32.OpenClipboard.argtypes = [wintypes.HWND]
            user32.OpenClipboard.restype = wintypes.BOOL
            user32.GetClipboardData.argtypes = [wintypes.UINT]
            user32.GetClipboardData.restype = wintypes.HANDLE
            kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
            kernel32.GlobalLock.restype = ctypes.c_void_p
            kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
            if not user32.OpenClipboard(None):
                return ''
            try:
                h = user32.GetClipboardData(CF_UNICODETEXT)
                if not h:
                    return ''
                ptr = kernel32.GlobalLock(h)
                if not ptr:
                    return ''
                try:
                    return ctypes.wstring_at(ptr)
                finally:
                    kernel32.GlobalUnlock(h)
            finally:
                user32.CloseClipboard()
        except Exception:
            return ''

    # --------------------------------------------------------------------- #
    # File dialog                                                           #
    # --------------------------------------------------------------------- #
    def open_file_dialog(self) -> str | None:
        """Native Open-File picker filtered to our supported formats."""
        win = webview.windows[0] if webview.windows else None
        if win is None:
            return None
        types = (
            'Data files (*.csv;*.tsv;*.txt;*.parquet;*.xlsx)',
            'CSV (*.csv;*.tsv;*.txt)',
            'Parquet (*.parquet)',
            'Excel (*.xlsx)',
            'All files (*.*)',
        )
        result = win.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False, file_types=types)
        if not result:
            return None
        return result[0] if isinstance(result, (list, tuple)) else result

    def save_file_dialog(self, default_name: str, file_types: list[str] | None = None) -> str | None:
        """Native Save-As picker; returns the selected path, or None on cancel."""
        win = webview.windows[0] if webview.windows else None
        if win is None:
            return None
        ft = tuple(file_types) if file_types else ('All files (*.*)',)
        result = win.create_file_dialog(
            webview.FileDialog.SAVE,
            save_filename=default_name,
            file_types=ft,
        )
        if not result:
            return None
        return result[0] if isinstance(result, (list, tuple)) else result

    # --------------------------------------------------------------------- #
    # Ingest                                                                #
    # --------------------------------------------------------------------- #
    def load_file(self, path: str, opts: dict | None = None) -> dict:
        io = ingest.IngestOptions(**(opts or {}))
        result = ingest.load_file(path, io)
        return asdict(result)

    def preview_file_lines(self, path: str, n: int = 30, encoding: str | None = None) -> dict:
        """First N raw lines of a text file; feeds the Open-with-options
        dialog so the user can identify the real header row."""
        return ingest.preview_file_lines(path, n=n, encoding=encoding)

    def inspect_parquet_set(self, path: str) -> dict:
        """Detect sibling .parquet files in the same directory so the
        frontend can offer 'load just this file' vs 'load all in
        folder'.  Returns ``{this_path, peers, total_bytes}``."""
        return ingest.inspect_parquet_set(path)

    def inspect_excel_file(self, path: str) -> dict:
        """Probe an Excel file: real workbook vs a text file with a
        misleading extension, sheet names, and per-sheet auto-detected
        header row + start column for files with preamble blocks.
        Returns ``{kind: 'excel'|'text', sheets: [...]}``."""
        return ingest.inspect_excel_file(path)

    def load_excel_workbook(self, path: str, sheet_specs: list[dict],
                            opts: dict | None = None) -> list[dict]:
        """Load one or more sheets from an Excel file.  Returns a list
        of IngestResults so the frontend can refresh the sidebar and
        report all created tables."""
        io = ingest.IngestOptions(**(opts or {}))
        return ingest.load_excel_workbook(path, sheet_specs, io)

    # --------------------------------------------------------------------- #
    # Workspace inventory                                                   #
    # --------------------------------------------------------------------- #
    def list_tables(self) -> list[dict]:
        """All tables + views in the current schema, with row counts.
        Internal result tables (``__fq_sql_result_*``) are filtered out
        so they don't surface in the sidebar."""
        c = db.conn()
        rows = c.execute("""
            select  table_name     as table_name
            ,       table_type     as table_type
            from    information_schema.tables
            where   table_schema = current_schema()
            and     table_name not like '\\_\\_fq\\_sql\\_result\\_%' escape '\\'
            order   by table_name
            ;
        """).fetchall()
        out: list[dict] = []
        for (name, kind) in rows:
            (rc,) = c.execute(f"""
                select  count(*)
                from    {db.quote_ident(name)}
                ;
            """).fetchone()
            out.append({
                'name': name,
                'kind': 'table' if kind == 'BASE TABLE' else 'view',
                'row_count': int(rc),
            })
        return out

    def get_table_schema(self, name: str) -> list[dict]:
        c = db.conn()
        rows = c.execute(f"""
            pragma table_info({db.quote_ident(name)})
            ;
        """).fetchall()
        return [{'name': r[1], 'type': r[2], 'nullable': not bool(r[3])} for r in rows]

    def get_rows(self, name: str, offset: int = 0, limit: int = 500) -> dict:
        """Paginated window of rows from a table / view.

        Values are coerced to JSON-safe forms:
          None             → None (NULL)
          int / float / bool → passthrough (preserves numeric precision)
          everything else  → str()

        Sorting is handled client-side (capped in the frontend), so this
        endpoint always returns rows in DuckDB's natural order.
        """
        c = db.conn()
        qname = db.quote_ident(name)

        col_rows = c.execute(f"""
            pragma table_info({qname})
            ;
        """).fetchall()
        cols = [r[1] for r in col_rows]

        rows = c.execute(f"""
            select  *
            from    {qname}
            limit   {int(limit)}
            offset  {int(offset)}
            ;
        """).fetchall()

        safe: list[list[Any]] = [[_json_safe(v) for v in row] for row in rows]
        return {'columns': cols, 'rows': safe, 'offset': int(offset), 'limit': int(limit)}

    # --------------------------------------------------------------------- #
    # Mutations                                                             #
    # --------------------------------------------------------------------- #
    def rename_table(self, old: str, new: str) -> dict:
        c = db.conn()
        c.execute(f"""
            alter table {db.quote_ident(old)}
            rename to {db.quote_ident(new)}
            ;
        """)
        return {'ok': True, 'name': new}

    def drop_table(self, name: str) -> dict:
        c = db.conn()
        row = c.execute("""
            select  table_type
            from    information_schema.tables
            where   table_schema = current_schema()
            and     table_name   = ?
            ;
        """, [name]).fetchone()
        if row is None:
            return {'ok': False, 'error': 'not found'}
        kind = row[0]
        stmt = 'drop view' if kind == 'VIEW' else 'drop table'
        c.execute(f"""
            {stmt} {db.quote_ident(name)}
            ;
        """)
        return {'ok': True}

    def materialize_table(self, name: str) -> dict:
        """Convert a VIEW into a real TABLE so repeated scans are cheap."""
        c = db.conn()
        qname = db.quote_ident(name)
        temp = db.quote_ident(f'{name}__mat_tmp')
        c.execute(f"""
            create table {temp} as
            select  *
            from    {qname}
            ;
        """)
        c.execute(f"""
            drop view {qname}
            ;
        """)
        c.execute(f"""
            alter table {temp}
            rename to {qname}
            ;
        """)
        return {'ok': True}

    # --------------------------------------------------------------------- #
    # Free-form SQL                                                         #
    # --------------------------------------------------------------------- #
    def run_sql(self, sql_text: str) -> dict:
        """Execute arbitrary SQL against the workspace.  See sql.run."""
        return sql_mod.run(sql_text)

    def cancel_sql(self) -> dict:
        """Interrupt the current query, if one is running."""
        return {'cancelled': sql_mod.cancel()}

    def save_query_as_table(self, sql_text: str, table_name: str) -> dict:
        """Materialize a SELECT result into a new workspace table."""
        return sql_mod.save_as_table(sql_text, table_name)

    # --------------------------------------------------------------------- #
    # Column profiler                                                       #
    # --------------------------------------------------------------------- #
    def profile_column(self, table_name: str, column_name: str) -> dict:
        """Profile a column from a loaded table / view."""
        return profile_mod.profile_table(table_name, column_name)

    def profile_query_column(self, source_sql: str, column_name: str) -> dict:
        """Profile a column from an arbitrary SELECT result."""
        return profile_mod.profile_query(source_sql, column_name)

    # --------------------------------------------------------------------- #
    # Derive                                                                #
    # --------------------------------------------------------------------- #
    def suggest_casts(self, table_name: str) -> list[dict]:
        """Per-column suggested target type + cast-coverage percentages."""
        return derive.suggest_casts(table_name)

    def preview_derive(
        self,
        source_table: str,
        casts: list[dict],
        where_clause: str | None = None,
        limit: int = 20,
    ) -> dict:
        specs = [derive.CastSpec(**c) for c in casts]
        return derive.preview_derive(source_table, specs, where_clause, limit)

    def create_derived_table(
        self,
        source_table: str,
        new_name: str,
        casts: list[dict],
        where_clause: str | None = None,
    ) -> dict:
        specs = [derive.CastSpec(**c) for c in casts]
        return derive.create_derived_table(source_table, new_name, specs, where_clause)

    def derive_sql(
        self,
        source_table: str,
        new_name: str,
        casts: list[dict],
        where_clause: str | None = None,
    ) -> dict:
        """Compose the CREATE TABLE SQL the Clone dialog would execute,
        without running it; for the Show-SQL preview button."""
        specs = [derive.CastSpec(**c) for c in casts]
        return {'sql': derive.derive_sql(source_table, new_name, specs, where_clause)}

    def auto_derive(self, source_table: str, new_name: str) -> dict:
        """One-click derive: suggested type per column, no dialog."""
        return derive.auto_derive(source_table, new_name)

    # --------------------------------------------------------------------- #
    # DDL                                                                   #
    # --------------------------------------------------------------------- #
    def generate_ddl(self, table_name: str, dialect: str = 'duckdb') -> dict:
        # Validation lives inside the dialect registry; unknown ids
        # raise KeyError there, which surfaces as a clear message
        # rather than the cryptic dispatch failure we'd otherwise hit.
        return {'sql': ddl_mod.generate(table_name, dialect)}

    def list_dialects(self) -> list[dict]:
        """Every registered DDL dialect.  The frontend uses this to
        populate the dialect dropdown in the Show DDL drawer and the
        export preview.  Stays small even after Postgres / MySQL /
        Snowflake land; each is a couple-dozen-line naming class."""
        from . import drivers
        return [
            {'id': did, 'display_name': drivers.dialect_for(did).display_name}
            for did in drivers.known_dialects()
        ]

    # --------------------------------------------------------------------- #
    # Pluggable DB drivers – Import + (eventually) Export                   #
    # --------------------------------------------------------------------- #

    def list_drivers(self) -> list[dict]:
        """Every registered driver, with its connection-form spec and
        capability flags so the frontend can pick the right inputs
        and gate UI affordances (e.g. show import only when the
        driver advertises `'import'`)."""
        from . import drivers
        out: list[dict] = []
        for did in drivers.known_drivers():
            d = drivers.driver_for(did)
            out.append({
                'id': d.id,
                'display_name': d.display_name,
                'capabilities': list(getattr(d, 'capabilities', ())),
                'connection_form': [
                    {
                        'key': f.key, 'label': f.label, 'kind': f.kind,
                        'required': f.required, 'default': f.default,
                        'placeholder': f.placeholder, 'options': f.options,
                    }
                    for f in d.connection_form
                ],
            })
        return out

    def driver_is_available(self, driver_id: str) -> dict:
        from . import drivers
        return drivers.driver_for(driver_id).is_available()

    def driver_test_connection(self, driver_id: str, conn_opts: dict) -> dict:
        from . import drivers
        d = drivers.driver_for(driver_id)
        return d.test_connection(conn_opts)                          # type: ignore[attr-defined]

    def driver_list_databases(self, driver_id: str, conn_opts: dict) -> list[str]:
        from . import drivers
        return drivers.driver_for(driver_id).list_databases(conn_opts)

    def driver_list_schemas(self, driver_id: str, conn_opts: dict, database: str) -> list[str]:
        from . import drivers
        return drivers.driver_for(driver_id).list_schemas(conn_opts, database)

    def driver_list_tables(self, driver_id: str, conn_opts: dict,
                           database: str, schema: str) -> list[dict]:
        from . import drivers
        return drivers.driver_for(driver_id).list_tables(conn_opts, database, schema)

    def driver_import_from_db(self, driver_id: str, conn_opts: dict,
                              source: dict, target_name: str,
                              row_cap: int | None = 1_000_000) -> dict:
        """Stream a remote table or query into a DuckDB workspace
        table.  ``source`` shape:
            {'kind': 'table', 'database': str, 'schema': str, 'table': str}
            {'kind': 'query', 'database': str|None, 'sql': str}
        Pass ``row_cap=None`` (or 0) to disable the safety cap."""
        from . import drivers
        d = drivers.driver_for(driver_id)
        kind = source.get('kind', 'table')
        return d.import_table(                                       # type: ignore[attr-defined]
            conn_opts,
            database=source.get('database'),
            schema=source.get('schema') if kind == 'table' else None,
            table=source.get('table') if kind == 'table' else None,
            query=source.get('sql') if kind == 'query' else None,
            target_name=target_name,
            row_cap=row_cap if (row_cap is None or row_cap > 0) else None,
        )

    def driver_cancel_import(self, driver_id: str) -> dict:
        from . import drivers
        drivers.driver_for(driver_id).cancel()
        return {'ok': True}

    # --------------------------------------------------------------------- #
    # SQL Server export                                                     #
    # --------------------------------------------------------------------- #
    def check_odbc_driver(self) -> dict:
        return export_mod.check_odbc_driver()

    def test_sqlserver_connection(self, conn_opts: dict) -> dict:
        return export_mod.test_connection(export_mod.ConnectionOptions(**conn_opts))

    def list_sqlserver_databases(self, conn_opts: dict) -> list[str]:
        return export_mod.list_databases(export_mod.ConnectionOptions(**conn_opts))

    def list_sqlserver_schemas(self, conn_opts: dict) -> list[str]:
        return export_mod.list_schemas(export_mod.ConnectionOptions(**conn_opts))

    def suggest_export_mappings(self, source: dict,
                                trim_strings: bool = True) -> list[dict]:
        return export_mod.suggest_mappings(source, trim_strings)

    def preview_export_ddl(self, export_opts: dict) -> dict:
        return {'sql': export_mod.preview_ddl(export_mod.ExportOptions(**export_opts))}

    def export_to_sqlserver(self, conn_opts: dict, export_opts: dict) -> dict:
        return export_mod.export_to_sqlserver(
            export_mod.ConnectionOptions(**conn_opts),
            export_mod.ExportOptions(**export_opts),
        )

    def cancel_export(self) -> dict:
        return {'cancelled': export_mod.cancel()}

    def get_export_progress(self) -> dict:
        return export_mod.get_progress()

    # --------------------------------------------------------------------- #
    # File export                                                           #
    # --------------------------------------------------------------------- #
    def export_to_file(self, source: dict, target: dict,
                       mappings: list[dict] | None = None,
                       trim_strings: bool = True) -> dict:
        from . import export_file
        return export_file.export_to_file(source, target, mappings, trim_strings)

    def get_file_export_progress(self) -> dict:
        from . import export_file
        return export_file.get_progress()

    def cancel_file_export(self) -> dict:
        from . import export_file
        return {'cancelled': export_file.cancel()}


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _json_safe(v: Any) -> Any:
    if v is None or isinstance(v, (int, float, bool, str)):
        return v
    return str(v)
