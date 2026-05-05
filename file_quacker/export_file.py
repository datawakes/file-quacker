"""Export a DuckDB source to a flat file via DuckDB's ``COPY ... TO``.

Flat (any delimiter) + Parquet are single-statement atomic writes via
DuckDB; Excel uses ``openpyxl`` streaming (DuckDB's native xlsx support
requires the ``spatial`` extension, which we don't pull in).

Every exporter respects the dialog's column mapping: source columns are
projected through ``"source" as "target"`` aliases, so the user's target
renames appear in the file's header / schema.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import RLock
from typing import Literal

from . import db
from .export_source import ExportSource


@dataclass
class Mapping:
    source: str
    target: str


@dataclass
class FlatTarget:
    path: str
    delimiter: str = '|'
    header: bool = True
    quote: str = '"'
    null: str = ''
    # Default to CRLF; the most common destination is Windows tooling
    # / SQL Server bcp / Excel, all of which prefer it.  Unix consumers
    # tolerate CRLF; Windows consumers often choke on bare LF.
    line_ending: Literal['crlf', 'lf'] = 'crlf'


@dataclass
class ParquetTarget:
    path: str
    compression: Literal['snappy', 'zstd', 'gzip', 'uncompressed'] = 'snappy'


@dataclass
class ExcelTarget:
    path: str
    sheet_name: str = 'Sheet1'


FileTarget = FlatTarget | ParquetTarget | ExcelTarget


_lock = RLock()
_progress: dict = {'phase': 'idle', 'rows_written': 0, 'elapsed_ms': 0, 'error': None}
_cancel_flag = False
_running = False


def _set(**kw):
    with _lock:
        _progress.update(kw)


def get_progress() -> dict:
    with _lock:
        return dict(_progress)


def cancel() -> bool:
    global _cancel_flag
    _cancel_flag = True
    return True


def export_to_file(source: ExportSource | dict, target: dict,
                   mappings: list[dict] | None = None) -> dict:
    """Dispatch to the right exporter based on ``target['kind']``."""
    global _cancel_flag, _running
    with _lock:
        if _running:
            return {'ok': False, 'error': 'a file export is already running', 'elapsed_ms': 0}
        _running = True
    _cancel_flag = False
    if isinstance(source, dict):
        source = ExportSource(**source)
    maps = _coerce_mappings(mappings)
    kind = target.get('kind')
    try:
        if kind == 'flat':
            return _export_flat(source, FlatTarget(**_strip_kind(target)), maps)
        if kind == 'parquet':
            return _export_parquet(source, ParquetTarget(**_strip_kind(target)), maps)
        if kind == 'excel':
            return _export_excel(source, ExcelTarget(**_strip_kind(target)), maps)
        raise ValueError(f'unknown target kind: {kind!r}')
    finally:
        with _lock:
            _running = False


def _coerce_mappings(mappings: list[dict] | None) -> list[Mapping]:
    if not mappings:
        return []
    out: list[Mapping] = []
    for m in mappings:
        if isinstance(m, Mapping):
            out.append(m)
        else:
            out.append(Mapping(source=m['source'], target=m.get('target') or m['source']))
    return out


def _strip_kind(d: dict) -> dict:
    return {k: v for k, v in d.items() if k != 'kind'}


def _select_with_mappings(qtable: str, mappings: list[Mapping]) -> tuple[str, list[str]]:
    """Return ``(select_list, target_column_names)`` honoring the user's
    source→target renames.  Falls back to every non-meta column on the
    table when mappings is empty.
    """
    if mappings:
        parts = [
            f'{db.quote_ident(m.source)} as {db.quote_ident(m.target)}'
            for m in mappings
        ]
        targets = [m.target for m in mappings]
        return ', '.join(parts), targets

    # No mappings provided; use every column except _src_row_num, with
    # source names as both source AND target.
    c = db.conn()
    info = c.execute(f"""
        pragma table_info({qtable})
        ;
    """).fetchall()
    cols = [r[1] for r in info if r[1] != '_src_row_num']
    return ', '.join(db.quote_ident(n) for n in cols), cols


def _export_flat(source: ExportSource, tgt: FlatTarget,
                 mappings: list[Mapping]) -> dict:
    t0 = time.perf_counter()
    _set(phase='writing', rows_written=0, elapsed_ms=0, error=None)
    src_name, cleanup = source.materialize()
    try:
        qtable = db.quote_ident(src_name)
        select_list, _ = _select_with_mappings(qtable, mappings)
        # Parameter placeholders aren't supported inside COPY's option list,
        # so we literal-escape values.  Path is a trusted local path typed
        # by the user in their own app; single quotes are doubled defensively.
        path = tgt.path.replace("'", "''")
        delim = tgt.delimiter.replace("'", "''")
        quote = tgt.quote.replace("'", "''")
        null = tgt.null.replace("'", "''")
        header = 'true' if tgt.header else 'false'
        # DuckDB's `new_line` accepts '\r\n', '\n', or '\r' (the literal
        # escape sequences inside the SQL string).  CRLF is the default
        # since most downstream tooling (Excel, SQL Server bcp, Windows
        # text consumers) expects it; LF stays available for Unix-y
        # pipelines.
        new_line = '\\r\\n' if tgt.line_ending == 'crlf' else '\\n'
        copy_sql = f"""
            copy (select {select_list} from {qtable})
            to '{path}'
            (header {header}, delimiter '{delim}',
             quote '{quote}', null '{null}',
             new_line '{new_line}')
            ;
        """
        c = db.conn()
        c.execute(copy_sql)
        (n,) = c.execute(f"""
            select  count(*)
            from    {qtable}
            ;
        """).fetchone()
        elapsed = int((time.perf_counter() - t0) * 1000)
        _set(phase='done', rows_written=int(n), elapsed_ms=elapsed)
        return {'ok': True, 'rows_written': int(n), 'path': tgt.path,
                'elapsed_ms': elapsed}
    except Exception as ex:
        _set(phase='error', error=str(ex))
        return {'ok': False, 'error': str(ex),
                'elapsed_ms': int((time.perf_counter() - t0) * 1000)}
    finally:
        cleanup()


def _export_parquet(source: ExportSource, tgt: ParquetTarget,
                    mappings: list[Mapping]) -> dict:
    t0 = time.perf_counter()
    _set(phase='writing', rows_written=0, elapsed_ms=0, error=None)
    src_name, cleanup = source.materialize()
    try:
        qtable = db.quote_ident(src_name)
        select_list, _ = _select_with_mappings(qtable, mappings)
        path = tgt.path.replace("'", "''")
        compression = tgt.compression.upper()
        copy_sql = f"""
            copy (select {select_list} from {qtable})
            to '{path}'
            (format parquet, compression {compression})
            ;
        """
        c = db.conn()
        c.execute(copy_sql)
        (n,) = c.execute(f"""
            select  count(*)
            from    {qtable}
            ;
        """).fetchone()
        elapsed = int((time.perf_counter() - t0) * 1000)
        _set(phase='done', rows_written=int(n), elapsed_ms=elapsed)
        return {'ok': True, 'rows_written': int(n), 'path': tgt.path,
                'elapsed_ms': elapsed}
    except Exception as ex:
        _set(phase='error', error=str(ex))
        return {'ok': False, 'error': str(ex),
                'elapsed_ms': int((time.perf_counter() - t0) * 1000)}
    finally:
        cleanup()


def _export_excel(source: ExportSource, tgt: ExcelTarget,
                  mappings: list[Mapping]) -> dict:
    import openpyxl  # lazy; only loaded when Excel export is used

    t0 = time.perf_counter()
    _set(phase='writing', rows_written=0, elapsed_ms=0, error=None)
    src_name, cleanup = source.materialize()
    try:
        qtable = db.quote_ident(src_name)
        select_list, target_cols = _select_with_mappings(qtable, mappings)
        c = db.conn()
        cur = c.execute(f"""
            select  {select_list}
            from    {qtable}
            ;
        """)

        wb = openpyxl.Workbook(write_only=True)
        ws = wb.create_sheet(title=tgt.sheet_name)
        ws.append(target_cols)

        n = 0
        for row in cur.fetchall():
            if _cancel_flag:
                _set(phase='error', error='cancelled')
                return {'ok': False, 'error': 'cancelled',
                        'rows_written': n,
                        'elapsed_ms': int((time.perf_counter() - t0) * 1000)}
            ws.append(list(row))
            n += 1
            if n % 5000 == 0:
                _set(rows_written=n,
                     elapsed_ms=int((time.perf_counter() - t0) * 1000))

        wb.save(tgt.path)
        elapsed = int((time.perf_counter() - t0) * 1000)
        _set(phase='done', rows_written=n, elapsed_ms=elapsed)
        return {'ok': True, 'rows_written': n, 'path': tgt.path,
                'elapsed_ms': elapsed}
    except Exception as ex:
        _set(phase='error', error=str(ex))
        return {'ok': False, 'error': str(ex),
                'elapsed_ms': int((time.perf_counter() - t0) * 1000)}
    finally:
        cleanup()
