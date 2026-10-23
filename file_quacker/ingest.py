"""File ingestion → DuckDB tables / views.

Text files load as views over `read_csv(..., all_varchar=True, ...)` so
source values are preserved exactly; no silent date / int coercion.
`escape` mirrors `quote` (DuckDB's CSV convention) so RFC-4180 doubled-
quote escapes parse correctly.  All identifiers pass through
`db.quote_ident` to prevent SQL injection via table / column names.

Text files load with the fastest settings first (parallel, strict).
When DuckDB rejects the file, its error message picks the next attempt:
null padding for short rows, `ignore_errors` for rows with extra fields
(those land in a `<name>__rejects` table), a bigger line limit, or a
looser quote rule.  The encoding, delimiter and layout (title lines,
header, summary rows at the bottom) come from `sniff`; the encoding only
changes when DuckDB says the bytes don't decode.
"""

from __future__ import annotations

import codecs
import csv as _csv
import itertools
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

import duckdb

from . import db, derive, sniff

# Tried in this order after the first guess, only on a decoding error.
# iso-8859-1 reads every byte (DuckDB's built-in latin-1 rejects 0x80-0x9F).
FALLBACK_ENCODINGS: tuple[str, ...] = ('utf-8', 'cp1252', 'iso-8859-1')

# read_csv's buffer.  DuckDB's default (16 x max_line_size = 32 MB) cuts a
# file into many parallel batches, and the table they build makes later
# scans 10-40% slower (suggest_casts / auto_derive), even though its
# storage layout reads the same.  From 64 MB on, downstream speed matches
# the old 32 MB-line-size loads.  An explicit buffer also lifts DuckDB's
# 2 MB line limit, so long narrative cells need no extra rung.
READ_BUFFER_BYTES = 64 * 1024 * 1024

# Up to about this many rows, number them inside the load with
# row_number(): with a buffer that holds the whole file, DuckDB then
# parses it on all threads, which matters for wide rows (RTN: 1.6 s vs
# 2.3 s).  Past it the window costs more than it saves (big_comma: 2.2 s
# vs 1.0 s), so the number comes from rowid afterwards.
INLINE_NUMBER_MAX_ROWS = 250_000
INLINE_READ_BUFFER_BYTES = 256 * 1024 * 1024

SQL_SAFE_NAME = re.compile(r'[^0-9A-Za-z_]+')

# Metadata column prepended to every loaded table/view.  Survives any
# downstream filter or sort, so the operator can always recover a row's
# position in the source file.
ROW_NUM_COLUMN = '_src_row_num'


@dataclass
class IngestOptions:
    delimiter: str | None = None
    quote: str = '"'
    escape: str | None = None
    encoding: str | None = None
    # 1-based line of the header, 0 = no header row.  None detects it.
    first_row: int | None = None
    skip_rows: int = 0
    # Summary rows to drop from the bottom.  None detects them.
    skip_footer: int | None = None
    ignore_errors: bool = False
    # Starting values for the first attempt; the ladder turns parallel off
    # when null padding meets quoted newlines, and strict mode off as a
    # last resort.
    parallel: bool = True
    strict_mode: bool = True
    # Rows DuckDB samples for dialect detection; None keeps its default.
    sample_size: int | None = None
    # Views lazy-read the source on every query and tank interactive perf;
    # always materialize.
    materialize: bool = True
    sheet: str | None = None
    preferred_table_name: str | None = None
    # Nulled only in columns where every other value looks like a number
    # or a date, so a country code 'NA' next to 'US' survives.
    # Case-sensitive (DuckDB binary collation), so the surname `Null`,
    # lowercase `na` etc. survive.  Empty string is intentionally absent:
    # DuckDB already nulls unquoted empty fields in CSV, and a blanket
    # `'' -> NULL` rule loses too much in parquet/xlsx.
    nullify_tokens: list[str] = field(
        default_factory=lambda: ['NA', 'NaN']
    )
    # Frontend sets this after the "load this one or all in folder?"
    # prompt; > 1 entry triggers `read_parquet([...])`.
    parquet_paths: list[str] | None = None
    # Excel preamble cropping.  `excel_header_row` is 1-based (so 3 means
    # "skip two title rows"); `excel_start_col`/`end_col` are A-style.
    excel_header_row: int | None = None
    excel_start_col: str | None = None
    excel_end_col: str | None = None


@dataclass
class IngestResult:
    name: str
    source_path: str
    format: Literal['csv', 'parquet', 'xlsx']
    materialized: bool
    row_count: int
    col_count: int
    has_rejects: bool
    columns: list[dict] = field(default_factory=list)
    # Effective ingest options; what DuckDB actually parsed with.  For
    # non-text formats the delimiter/encoding fields are None.
    delimiter: str | None = None
    encoding: str | None = None
    first_row: int | None = None
    footer_rows: int | None = None
    # Name of the strategy that succeeded (for file-format debugging).
    strategy: str | None = None
    # Plain-English notes on what detection found, set only when worth
    # telling the user (bad bytes, a fallback encoding, a byte order mark,
    # title lines or summary rows).
    encoding_note: str | None = None
    layout_note: str | None = None


# --------------------------------------------------------------------------- #
# Public entry points                                                         #
# --------------------------------------------------------------------------- #

def preview_file_lines(path: str, n: int = 30, encoding: str | None = None,
                       delimiter: str | None = None) -> dict:
    """First ``n`` raw lines of a text file plus what detection found:
    the encoding used for the preview, the delimiter, and the header row
    and footer rows a plain load would use (with ``delimiter`` when the
    user picked one).  Feeds the Open-with-options dialog.  Decode
    failures fall back to UTF-8 + replace so the user always sees
    something (even mojibake) and can pick a different encoding from the
    dropdown.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    found = _sniff_csv(p, IngestOptions(encoding=_duckdb_encoding(encoding) if encoding else None,
                                        delimiter=delimiter or None))
    used = found.encodings[0]
    try:
        with p.open('r', encoding=_python_codec(used, p), errors='replace', newline='') as f:
            lines = []
            for i, line in enumerate(f):
                if i >= n:
                    break
                lines.append(line.rstrip('\n').rstrip('\r'))
    except (LookupError, UnicodeDecodeError):
        with p.open('rb') as f:
            raw = f.read()
        lines = raw.decode('utf-8', errors='replace').splitlines()[:n]
        used = 'utf-8 (forced)'
    return {
        'lines': lines,
        'encoding': used,
        'encoding_note': found.encoding_note(found.encodings[0]),
        'sniffed_delimiter': found.delimiter,
        'detected_first_row': found.first_row,
        'detected_footer_rows': found.footer_rows,
        'layout_note': found.layout_why,
    }


def load_file(path: str, opts: IngestOptions | None = None) -> IngestResult:
    opts = _clean_numbers(opts or IngestOptions())
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    ext = p.suffix.lower()
    if ext == '.parquet':
        return _load_parquet(p, opts)
    # .xls / .xlsx / .xlsm extensions sometimes lie; ELT pipelines
    # commonly transform Excel files to pipe-delimited text and keep
    # the original extension so downstream tooling stays unchanged.
    # Sniff the first 8 bytes; if it's not the Excel magic, fall
    # through to the CSV ladder.
    if ext in ('.xlsx', '.xlsm', '.xls') and not _looks_like_excel(p):
        return _load_csv(p, opts)
    if ext == '.xlsx':
        return _load_xlsx(p, opts)
    return _load_csv(p, opts)


def _clean_numbers(opts: IngestOptions) -> IngestOptions:
    """A cleared number field reaches us as '' (or junk): header row and
    footer go back to auto-detect, skipped lines to 0."""
    def number(v):
        try:
            return int(v) if v is not None and v != '' else None
        except (TypeError, ValueError):
            return None
    return replace(opts, first_row=number(opts.first_row), skip_footer=number(opts.skip_footer),
                   skip_rows=number(opts.skip_rows) or 0)


# --------------------------------------------------------------------------- #
# CSV / delimited text                                                        #
# --------------------------------------------------------------------------- #

@dataclass
class _CsvSniff:
    """What `sniff` found about a text file, with the user's choices on top."""
    guess: sniff.EncodingGuess | None   # None when the user set the encoding
    encodings: list[str]                # to try, in order
    # UTF-8 with a few bad bytes: the rows holding them go to the rejects
    bad_rows_expected: bool
    # One of those bad bytes sits in the header line, so FQ names the columns
    header_not_utf8: bool
    delimiter: str
    other_delims: list[sniff.DelimiterScore]   # ranked, best first
    field_count: int | None             # most common width for `delimiter`, when scored
    first_row: int
    footer_rows: int
    footer_detected: bool               # False when the user set it
    layout_why: str | None              # None when the user set both
    # layout_why when the detected values differ from "header on line 1,
    # no footer" and were used
    layout_note: str | None

    def encoding_note(self, used: str) -> str | None:
        """The encoding guess's reason, when it's worth telling the user."""
        g = self.guess
        if g is None:
            return None
        if self.header_not_utf8:
            return (f"{g.why.split(';')[0]}; one is in the header line, so the column "
                    'names are read as Windows-1252 and the rows holding the others are rejected')
        if used != g.encoding:
            return f'{g.why}; DuckDB could not decode it that way, so it is read as {used}'
        if g.encoding == 'utf-8' and g.clean and not g.bom:
            return None
        return g.why


def _sniff_csv(path: Path, opts: IngestOptions) -> _CsvSniff:
    """Encoding, delimiter and layout for `path`; values the user set win."""
    guess = None if opts.encoding else sniff.detect_encoding(path)
    encoding = opts.encoding or guess.encoding
    scores = [] if opts.delimiter else sniff.score_delimiters(path, encoding, opts.quote)
    delimiter = opts.delimiter or (scores[0].delimiter if scores else ',')
    layout = None
    if opts.first_row is None or opts.skip_footer is None:
        layout = sniff.detect_layout(path, encoding, delimiter, opts.quote)
    first_row = layout.first_row if opts.first_row is None else opts.first_row
    footer_rows = layout.footer_rows if opts.skip_footer is None else opts.skip_footer
    layout_note = None
    if layout and ((opts.first_row is None and first_row != 1)
                   or (opts.skip_footer is None and footer_rows > 0)):
        layout_note = layout.why

    tolerant = guess is not None and not guess.clean
    # DuckDB silently drops a header line it can't decode and promotes the
    # first data row, so then FQ reads the names itself.
    header_not_utf8 = tolerant and first_row > 0 and _line_not_utf8(path, opts.skip_rows + first_row)
    if guess is None:
        encodings = [opts.encoding]
    else:
        encodings = list(dict.fromkeys((guess.encoding, *FALLBACK_ENCODINGS)))
    return _CsvSniff(
        guess=guess,
        encodings=encodings,
        bad_rows_expected=tolerant,
        header_not_utf8=header_not_utf8,
        delimiter=delimiter,
        other_delims=[s for s in scores if s.delimiter != delimiter],
        field_count=next((s.field_count for s in scores if s.delimiter == delimiter), None),
        first_row=first_row,
        footer_rows=footer_rows,
        footer_detected=opts.skip_footer is None,
        layout_why=layout.why if layout else None,
        layout_note=layout_note,
    )


def _duckdb_encoding(name: str) -> str:
    """DuckDB's utf-16 is little-endian; it rejects the -le spellings."""
    key = name.strip().lower().replace('_', '-')
    return 'utf-16' if key in ('utf-16le', 'utf-16-le') else name


def _python_codec(encoding: str, path: Path) -> str:
    """Python codec that reads `path` the way DuckDB's `encoding` does."""
    if encoding == 'utf-8':
        return 'utf-8-sig'   # drops a byte order mark, reads plain UTF-8 the same
    if encoding == 'utf-16':
        with path.open('rb') as f:
            bom = f.read(2) in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)
        return 'utf-16' if bom else 'utf-16-le'
    return encoding


def _line_not_utf8(path: Path, line_no: int) -> bool:
    """True when physical line `line_no` (1-based) isn't valid UTF-8.
    Layout counts quote-aware records, which only differ from lines when
    a title line holds a quoted line break."""
    try:
        _physical_line(path, line_no).decode('utf-8')
    except UnicodeDecodeError:
        return True
    return False


def _physical_line(path: Path, line_no: int) -> bytes:
    with path.open('rb') as f:
        for _ in range(line_no - 1):
            f.readline()
        return f.readline()


def _header_names(path: Path, opts: IngestOptions, encoding: str, delim: str) -> list[str]:
    """Column names from the header line, named the way DuckDB names
    them.  Bytes that don't decode read as Windows-1252."""
    line_no = opts.skip_rows + opts.first_row
    codec = _python_codec(encoding, path)
    if codec.startswith('utf-16'):
        with path.open('r', encoding=codec, errors='replace', newline='') as f:
            for _ in range(line_no - 1):
                f.readline()
            text = f.readline()
    else:
        raw = _physical_line(path, line_no)
        try:
            text = raw.decode(codec)
        except UnicodeDecodeError:
            text = raw.decode('cp1252', errors='replace')
    return _duckdb_names(_parse_record(text.rstrip('\r\n'), delim, opts.quote))


def _parse_record(text: str, delim: str, quote: str) -> list[str]:
    if quote:
        rdr = _csv.reader([text], delimiter=delim, quotechar=quote, doublequote=True)
    else:
        rdr = _csv.reader([text], delimiter=delim, quoting=_csv.QUOTE_NONE)
    return next(rdr, [])


def _duckdb_names(fields: list[str]) -> list[str]:
    """Trimmed, blanks as columnN (0-based), repeats suffixed _1, _2 ...
    compared without case: what DuckDB does with a header row."""
    names: list[str] = []
    seen: set[str] = set()
    counts: dict[str, int] = {}
    for i, field_ in enumerate(fields):
        base = field_.strip() or f'column{i}'
        name, k = base, counts.get(base.lower(), 0)
        while name.lower() in seen:
            k += 1
            name = f'{base}_{k}'
        counts[base.lower()] = k
        seen.add(name.lower())
        names.append(name)
    return names


def _width(path: Path, opts: IngestOptions, encoding: str, delim: str) -> int:
    """Most common field count in the head of a headerless file."""
    with path.open('r', encoding=_python_codec(encoding, path), errors='replace', newline='') as f:
        head = f.read(1 << 20)
    if opts.quote:
        rdr = _csv.reader(head.splitlines(), delimiter=delim, quotechar=opts.quote, doublequote=True)
    else:
        rdr = _csv.reader(head.splitlines(), delimiter=delim, quoting=_csv.QUOTE_NONE)
    counts = Counter(len(r) for r in itertools.islice(rdr, 1000) if r)
    return counts.most_common(1)[0][0] if counts else 1


@dataclass(frozen=True)
class _CsvAttempt:
    """One set of read_csv settings tried by the ladder."""
    encoding: str
    delim: str | None               # None lets DuckDB sniff it
    parallel: bool = True
    strict_mode: bool = True
    # FQ names the columns and DuckDB skips the header line as data
    own_header: bool = False
    # FQ gives DuckDB the whole dialect (auto_detect=false), for when its
    # sniffer gives up on rows that the reader itself can reject one by one
    no_sniff: bool = False
    null_padding: bool = False
    no_quote: bool = False
    ignore_errors: bool = False
    # Number rows with row_number() in the load (see INLINE_NUMBER_MAX_ROWS)
    number_inline: bool = False

    def label(self, user_encoding: bool, sniffed_delim: str) -> str:
        parts = [f'{self.encoding} (user-set)' if user_encoding else self.encoding]
        if self.delim is None:
            parts.append('auto-delim')
        elif self.delim != sniffed_delim:
            parts.append(f'delimiter {self.delim!r}')
        if self.own_header:
            parts.append('own header')
        if self.no_sniff:
            parts.append('no-sniff')
        if self.null_padding:
            parts.append('null_padding')
        if not self.parallel:
            parts.append('sequential')
        if self.no_quote:
            parts.append('no-quote')
        if not self.strict_mode:
            parts.append('strict=false')
        if self.ignore_errors:
            parts.append('ignore_errors')
        return ' + '.join(parts)


def _load_csv(path: Path, opts: IngestOptions) -> IngestResult:
    """Load a delimited-text file.  Starts with the fastest settings and,
    after each failure, changes only what DuckDB's error points at.  A
    user-set encoding, delimiter, header row or footer count is never
    changed.
    """
    if opts.encoding:
        opts = replace(opts, encoding=_duckdb_encoding(opts.encoding))
    found = _sniff_csv(path, opts)
    if found.guess is not None and found.guess.big_endian_utf16:
        raise IngestError(f'Could not read this file: {found.guess.why}.')
    opts = replace(opts, first_row=found.first_row, skip_footer=found.footer_rows)
    delim = found.delimiter
    user_encoding = bool(opts.encoding)
    may_probe_delim = opts.delimiter is None
    name = _unique_name(opts.preferred_table_name or _derive_table_name(path))
    # Reject tables are temp tables, so every attempt shares one cursor.
    c = db.conn()

    def label(a: _CsvAttempt) -> str:
        return a.label(user_encoding, delim)

    def names_for(a: _CsvAttempt) -> list[str] | None:
        if a.delim is None:
            return None
        if a.own_header or (a.no_sniff and opts.first_row > 0):
            return _header_names(path, opts, a.encoding, a.delim)
        if a.no_sniff:
            width = found.field_count if a.delim == found.delimiter and found.field_count else None
            return [f'column{i}' for i in range(width or _width(path, opts, a.encoding, a.delim))]
        return None

    attempt: _CsvAttempt | None = _CsvAttempt(
        encoding=found.encodings[0],
        delim=delim,
        parallel=opts.parallel,
        strict_mode=opts.strict_mode,
        own_header=found.header_not_utf8,
        ignore_errors=opts.ignore_errors or found.bad_rows_expected,
        number_inline=opts.materialize and _fits_inline(path),
    )
    errors: list[str] = []
    while attempt is not None:
        try:
            reject_types = _run_csv_attempt(c, name, path, opts, attempt, names_for(attempt))
        except duckdb.Error as ex:
            kind = _classify_csv_error(str(ex))
            if kind == 'unsupported_encoding':
                _drop_failed_load(c, name, opts.materialize)
                raise IngestError(f'DuckDB cannot read the encoding {attempt.encoding!r}. '
                                  'Pick another one, or leave the encoding on Auto.') from None
            errors.append(f'[{label(attempt)}] {_error_detail(ex)}')
            if attempt.delim is None:
                may_probe_delim = False
            attempt = _next_attempt(attempt, kind, found.encodings, delim, may_probe_delim)
            continue
        if attempt.delim is None and _data_col_count(c, name) == 1:
            # DuckDB's sniffer falls back to one column when it finds no
            # dialect; that's no answer for a file that failed to parse.
            errors.append(f'[{label(attempt)}] DuckDB found a single column')
            may_probe_delim = False
            attempt = _next_attempt(attempt, 'other', found.encodings, delim, may_probe_delim)
            continue
        retry = _retry_for_rejects(attempt, reject_types, found.encodings,
                                   found.bad_rows_expected)
        if retry is None:
            break
        errors.append(f'[{label(attempt)}] rejected rows: ' + ', '.join(sorted(reject_types)))
        attempt = retry
    else:
        _drop_failed_load(c, name, opts.materialize)
        raise IngestError(
            'Could not read this file after trying '
            f'{len(errors)} strategies:\n  ' + '\n  '.join(errors)
        )

    if attempt.ignore_errors and 'UNQUOTED VALUE' in reject_types and not attempt.no_quote:
        attempt = _keep_more_rows(c, name, path, opts, attempt, names_for)
    if may_probe_delim and attempt.delim == delim and _data_col_count(c, name) == 1:
        attempt = _probe_other_delims(c, name, path, opts, attempt, found.other_delims, names_for)

    strategy = label(attempt)
    has_rejects = False
    if attempt.ignore_errors:
        rejected, spanned = _save_rejects(c, name)
        has_rejects = rejected > 0
        strategy += f" ({rejected:,} row{'' if rejected == 1 else 's'} rejected"
        # A broken quote can swallow several source lines into one record.
        strategy += f', spanning {spanned:,} lines)' if spanned > rejected else ')'
    # The footer was detected with quotes honored; once the ladder reads
    # quotes loosely the rows no longer line up with that count.
    keep_footer = found.footer_detected and (attempt.no_quote or not attempt.strict_mode)
    footer_rows = 0
    if opts.materialize and not keep_footer:
        footer_rows = _drop_footer(name, opts.skip_footer)
    if opts.materialize and not attempt.number_inline:
        _number_rows(name)
    if opts.first_row == 0:
        _rename_default_columns(name)
    if opts.materialize and opts.nullify_tokens:
        _nullify_placeholder_tokens(name, opts.nullify_tokens)

    row_count, col_count, cols = _describe(name)
    layout_note = found.layout_note
    if keep_footer and opts.skip_footer and layout_note:
        layout_note += '; those rows are kept, since the quotes had to be read loosely'
    return IngestResult(
        name=name,
        source_path=str(path),
        format='csv',
        materialized=opts.materialize,
        row_count=row_count,
        col_count=col_count,
        has_rejects=has_rejects,
        columns=cols,
        delimiter=attempt.delim,
        encoding=attempt.encoding,
        first_row=opts.first_row,
        footer_rows=footer_rows,
        strategy=strategy,
        encoding_note=found.encoding_note(attempt.encoding),
        layout_note=layout_note,
    )


def _run_csv_attempt(c: duckdb.DuckDBPyConnection, name: str, path: Path,
                     opts: IngestOptions, attempt: _CsvAttempt,
                     names: list[str] | None) -> set[str]:
    """Load `path` into `name` with one set of settings.  Returns the
    error types of rejected rows (empty without ignore_errors).  DuckDB
    errors bubble up to the ladder."""
    params = _read_csv_params(opts, attempt, names)
    qname = db.quote_ident(name)
    object_kw = 'table' if opts.materialize else 'view'
    if attempt.ignore_errors:
        _drop_reject_tables(c)
    c.execute(f"""
        create or replace {object_kw} {qname} as
        select  {_row_num_placeholder(opts.materialize and not attempt.number_inline)}  as {db.quote_ident(ROW_NUM_COLUMN)}
        ,       *
        from    read_csv(
                    {_sql_path_literal(str(path))}
                  , {_fmt_params(params)}
                )
        ;
    """)
    if not opts.materialize:
        # A view parses lazily; read it once so parse errors surface here.
        c.execute(f"""
            select  count(*)
            from    {qname}
            ;
        """).fetchone()
    if not attempt.ignore_errors:
        return set()
    rows = c.execute("""
        select  distinct cast(error_type as varchar)
        from    temp.reject_errors
        ;
    """).fetchall()
    return {r[0] for r in rows}


def _read_csv_params(opts: IngestOptions, attempt: _CsvAttempt,
                     names: list[str] | None = None) -> dict:
    """read_csv arguments for one attempt.  `names` are the column names
    FQ supplies for an own-header or no-sniff attempt."""
    quote = '' if attempt.no_quote else opts.quote
    escape = quote if attempt.no_quote or opts.escape is None else opts.escape
    params: dict = {
        'header': opts.first_row >= 1 and not attempt.own_header,
        'quote': quote,
        'escape': escape,
        # DuckDB would otherwise guess '#' as a comment character and drop
        # rows such as '#N/A,...' without a word.
        'comment': '',
        'all_varchar': True,
        'skip': _skip(opts) + (1 if attempt.own_header else 0),
        'encoding': attempt.encoding,
        'parallel': attempt.parallel,
        'strict_mode': attempt.strict_mode,
        'buffer_size': INLINE_READ_BUFFER_BYTES if attempt.number_inline else READ_BUFFER_BYTES,
    }
    if attempt.delim is not None:
        params['delim'] = attempt.delim
    if attempt.no_sniff:
        params['auto_detect'] = False
        params['columns'] = {n: 'VARCHAR' for n in names or []}
    elif attempt.own_header:
        params['names'] = names or []
    if attempt.null_padding:
        params['null_padding'] = True
    if attempt.ignore_errors:
        params['ignore_errors'] = True
        params['store_rejects'] = True
    if opts.sample_size is not None:
        params['sample_size'] = opts.sample_size
    return params


def _skip(opts: IngestOptions) -> int:
    return opts.skip_rows + max(opts.first_row - 1, 0)


def _next_attempt(attempt: _CsvAttempt, kind: str, encodings: list[str],
                  delim: str, may_probe_delim: bool) -> _CsvAttempt | None:
    """Settings to try after `attempt` failed with an error of `kind`
    (see `_classify_csv_error`).  None when nothing is left to try."""
    # A failed delimiter probe goes back to the sniffed delimiter.
    attempt = replace(attempt, delim=delim)
    next_encoding = _next_encoding(attempt.encoding, encodings)
    if kind == 'encoding' and next_encoding:
        return replace(attempt, encoding=next_encoding)
    if kind == 'encoding' and not attempt.ignore_errors:
        # No encoding left to try (or the user set it): keep what decodes
        # and put the rest in the rejects table.
        return replace(attempt, ignore_errors=True)
    if kind == 'sniff' and not attempt.no_sniff:
        # The sniffer gives up on a sample holding a broken quote, short
        # rows or title lines; read with FQ's own dialect so the reader
        # reports (or rejects) the actual rows.
        return replace(attempt, no_sniff=True)
    if kind == 'too_few' and not attempt.null_padding:
        return replace(attempt, null_padding=True)
    if kind == 'null_padding_quoted_newlines' and attempt.parallel:
        return replace(attempt, parallel=False)
    if kind == 'too_many' and not attempt.ignore_errors:
        return replace(attempt, ignore_errors=True)
    # Unclassified, or the matching fix is already on: loosen one more
    # step.  Broken quoting first loses the bad rows to the rejects, then
    # relaxes strict mode, and only then reads quotes as data.
    if may_probe_delim and kind != 'quote':
        # DuckDB can only pick a delimiter while sniffing
        return replace(attempt, delim=None, no_sniff=False)
    if not attempt.null_padding and kind != 'quote':
        return replace(attempt, null_padding=True)
    if not attempt.ignore_errors:
        return replace(attempt, ignore_errors=True)
    if attempt.strict_mode and not attempt.no_quote:
        return replace(attempt, strict_mode=False)
    if not attempt.no_quote:
        return replace(attempt, no_quote=True, strict_mode=False)
    return None
def _retry_for_rejects(attempt: _CsvAttempt, reject_types: set[str],
                       encodings: list[str], bad_rows_expected: bool) -> _CsvAttempt | None:
    """ignore_errors also drops rows a better setting would keep; retry
    with that setting when the rejects show one.  Rows with bad bytes are
    the expected rejects when the file is UTF-8 with a few of them."""
    next_encoding = _next_encoding(attempt.encoding, encodings)
    expected = bad_rows_expected and attempt.encoding == 'utf-8'
    if 'INVALID ENCODING' in reject_types and next_encoding and not expected:
        return replace(attempt, encoding=next_encoding)
    if 'MISSING COLUMNS' in reject_types and not attempt.null_padding:
        return replace(attempt, null_padding=True)
    return None


def _next_encoding(current: str, encodings: list[str]) -> str | None:
    i = encodings.index(current)
    return encodings[i + 1] if i + 1 < len(encodings) else None


def _keep_more_rows(c: duckdb.DuckDBPyConnection, name: str, path: Path,
                    opts: IngestOptions, attempt: _CsvAttempt,
                    names_for: Callable[[_CsvAttempt], list[str] | None]) -> _CsvAttempt:
    """A quote that never closes swallows every line up to the next quote
    into one rejected row; reading quotes as data then keeps more.  The
    quoted load (bad rows in the rejects) still wins unless it lost a
    real share of the file, and never when the file quotes its values
    (they would keep their quote characters).  Strict mode stays on so
    split rows are rejected instead of cut short."""
    qname = db.quote_ident(name)

    def rows() -> int:
        return c.execute(f"""
            select  count(*)
            from    {qname}
            ;
        """).fetchone()[0]

    kept = rows()
    loose = replace(attempt, no_quote=True)
    try:
        _run_csv_attempt(c, name, path, opts, loose, names_for(loose))
        loose_rows = rows()
        if loose_rows - kept > max(5, loose_rows // 100) and not _mostly_quoted(c, qname, opts.quote):
            return loose
    except duckdb.Error:
        pass  # the quoted load already worked; fall back to it
    _run_csv_attempt(c, name, path, opts, attempt, names_for(attempt))
    return attempt


def _mostly_quoted(c: duckdb.DuckDBPyConnection, qname: str, quote: str) -> bool:
    """True when most values in the first 1000 rows of a no-quote load
    start and end with the quote character."""
    wrapped = _sql_str_literal(f'{quote}%{quote}')
    row = c.execute(f"""
        select  count_if(columns(* exclude ({db.quote_ident(ROW_NUM_COLUMN)})) like {wrapped})
        ,       count(columns(* exclude ({db.quote_ident(ROW_NUM_COLUMN)})))
        from    (select * from {qname} limit 1000)
        ;
    """).fetchone()
    n = len(row) // 2
    return sum(row[:n]) * 2 > sum(row[n:])


def _probe_other_delims(c: duckdb.DuckDBPyConnection, name: str, path: Path,
                        opts: IngestOptions, attempt: _CsvAttempt,
                        others: list[sniff.DelimiterScore],
                        names_for: Callable[[_CsvAttempt], list[str] | None]) -> _CsvAttempt:
    """A one-column load while other delimiters split the file usually
    means the top-ranked one was wrong: try the runner-up when it covers
    the file well, then let DuckDB pick.  Puts the original load back
    when neither does better."""
    probes = [s.delimiter for s in others[:1] if s.coverage >= 0.9 and s.field_count > 1]
    if others:
        probes.append(None)
    for d in probes:
        probe = replace(attempt, delim=d)
        if d is None and probe.no_sniff:
            probe = replace(probe, no_sniff=False)   # DuckDB can't pick a delimiter unsniffed
        try:
            _run_csv_attempt(c, name, path, opts, probe, names_for(probe))
            if _data_col_count(c, name) > 1:
                return probe
        except duckdb.Error:
            pass  # the original load already worked; fall back to it
    if probes:
        _run_csv_attempt(c, name, path, opts, attempt, names_for(attempt))
    return attempt


def _data_col_count(c: duckdb.DuckDBPyConnection, name: str) -> int:
    rows = c.execute(f"""
        pragma table_info({db.quote_ident(name)})
        ;
    """).fetchall()
    return sum(1 for r in rows if r[1] != ROW_NUM_COLUMN)


def _row_num_placeholder(fill_later: bool) -> str:
    """Row-number expression for a load's select list.  With `fill_later`
    it is a placeholder that `_number_rows` fills from rowid (far cheaper
    than row_number() on big loads, and it keeps the column first); views
    and small loads number inline with row_number()."""
    return 'cast(null as bigint)' if fill_later else 'row_number() over ()'


def _fits_inline(path: Path) -> bool:
    """True when the whole file fits one inline read buffer and holds few
    enough rows to number inside the load.  The size check comes first, so
    long lines at the top of a big file can't talk the estimate down."""
    size = path.stat().st_size
    return size <= INLINE_READ_BUFFER_BYTES and _estimated_rows(path, size) <= INLINE_NUMBER_MAX_ROWS


def _estimated_rows(path: Path, size: int, sample_bytes: int = 1 << 20) -> int:
    """Line count extrapolated from the first MB."""
    with path.open('rb') as f:
        head = f.read(sample_bytes)
    if not head:
        return 0
    return size * max(head.count(b'\n'), 1) // len(head)


def _drop_footer(name: str, rows: int) -> int:
    """Delete the last `rows` rows (summary lines under the data).  Runs
    before `_number_rows`, while rowids still run 0..n-1 in file order.
    Returns how many went."""
    if rows <= 0:
        return 0
    qname = db.quote_ident(name)
    (deleted,) = db.conn().execute(f"""
        delete  from {qname}
        where   rowid >= (select count(*) from {qname}) - ?
        ;
    """, [rows]).fetchone()
    return int(deleted)


def _number_rows(name: str) -> None:
    """Fill the row-number placeholder from rowid, which follows file order."""
    db.conn().execute(f"""
        update  {db.quote_ident(name)}
        set     {db.quote_ident(ROW_NUM_COLUMN)} = rowid + 1
        ;
    """)


def _save_rejects(c: duckdb.DuckDBPyConnection, name: str) -> tuple[int, int]:
    """Copy DuckDB's reject_errors into `<name>__rejects` when it has rows.
    Returns ``(records, lines)``: the rejected records (one can hold several
    errors) and the source lines they span."""
    rejected, spanned = c.execute("""
        select  count(*)
        ,       coalesce(sum(lines), 0)
        from (
            select  line
            ,       max(1 + length(body) - length(replace(body, chr(10), '')))  as lines
            from (
                -- csv_line can carry a line break at either end; drop those
                select  line
                ,       trim(coalesce(csv_line, ''), chr(13) || chr(10))  as body
                from    temp.reject_errors
            )
            group   by line
        )
        ;
    """).fetchone()
    if rejected:
        rejects_name = _unique_name(f'{name}__rejects')
        c.execute(f"""
            create or replace table {db.quote_ident(rejects_name)} as
            select  *
            from    temp.reject_errors
            ;
        """)
    _drop_reject_tables(c)
    return int(rejected), int(spanned)


def _drop_reject_tables(c: duckdb.DuckDBPyConnection) -> None:
    for table in ('reject_errors', 'reject_scans'):
        c.execute(f"""
            drop table if exists temp.{table}
            ;
        """)


def _drop_failed_load(c: duckdb.DuckDBPyConnection, name: str, materialize: bool) -> None:
    """A retry after a load that worked can leave that load behind."""
    c.execute(f"""
        drop {'table' if materialize else 'view'} if exists {db.quote_ident(name)}
        ;
    """)
    _drop_reject_tables(c)


# Where DuckDB's own description of the problem ends: hints, the list of
# encodings it knows, the dialect it searched, the settings dump and the
# query echo follow.
_ERROR_TAIL = re.compile(
    r'^\s*(Possible (fixes|Solution)|The currently supported encodings|The search space used was'
    r'|file = |LINE \d+:)')


def _error_detail(ex: BaseException, max_line: int = 160, max_total: int = 400) -> str:
    """DuckDB's description of the problem (error type, line number, the
    echoed row, what went wrong) joined with `|`, clipped to a few hundred
    characters."""
    kept: list[str] = []
    for line in str(ex).splitlines():
        if _ERROR_TAIL.match(line):
            break
        line = line.strip()
        if line:
            kept.append(line if len(line) <= max_line else line[:max_line] + '…')
    detail = ' | '.join(kept)
    return detail if len(detail) <= max_total else detail[:max_total] + '…'


# DuckDB 2.0 read_csv error descriptions → what went wrong.  The message
# echoes the bad row ("Original Line: ...") before the description, so
# the last match wins and row text can't pose as the error.
_CSV_ERROR_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ('unsupported_encoding', re.compile(
        r'^(?:Invalid Input Error: )?The CSV Reader does not support the encoding', re.M)),
    ('encoding', re.compile(
        r'^Invalid unicode \(byte sequence mismatch\) detected\. '
        r'This file is not \S+ encoded\.$', re.M)),
    ('encoding', re.compile(
        r'^(?:Invalid Input Error: )?File is not \S+ encoded$', re.M)),
    ('null_padding_quoted_newlines', re.compile(
        r'^ ?The parallel scanner does not support null_padding in conjunction '
        r'with quoted new lines\.', re.M)),
    ('columns', re.compile(
        r'^Expected Number of Columns: (\d+) Found: (\d+)$', re.M)),
    ('line_size', re.compile(
        r'^Maximum line size of \d+ bytes exceeded\.', re.M)),
    ('quote', re.compile(
        r'^Value with unterminated quote found\.$', re.M)),
    ('sniff', re.compile(
        r'^(?:Invalid Input Error: )?Error when sniffing file ', re.M)),
)


def _classify_csv_error(message: str) -> str:
    """Name the problem in a DuckDB read_csv error: 'unsupported_encoding'
    (final), 'encoding', 'too_few', 'too_many', 'null_padding_quoted_newlines',
    'line_size', 'quote', 'sniff' (dialect detection failed) or 'other'."""
    last: tuple[int, str, re.Match[str]] | None = None
    for kind, pattern in _CSV_ERROR_PATTERNS:
        for m in pattern.finditer(message):
            if last is None or m.start() > last[0]:
                last = (m.start(), kind, m)
    if last is None:
        return 'other'
    _, kind, m = last
    if kind == 'columns':
        expected, found = int(m.group(1)), int(m.group(2))
        return 'too_few' if found < expected else 'too_many'
    return kind


class IngestError(Exception):
    """Raised when every attempt in the ladder fails."""


# --------------------------------------------------------------------------- #
# Parquet                                                                     #
# --------------------------------------------------------------------------- #

def _load_parquet(path: Path, opts: IngestOptions) -> IngestResult:
    paths = opts.parquet_paths or [str(path)]
    if opts.preferred_table_name:
        name = opts.preferred_table_name
    elif len(paths) > 1:
        # Multi-file load; name the table after the parent directory
        # (the dataset's "name" in Spark/Hive output conventions).
        # Reuse `_derive_table_name` so the dir name goes through the
        # same snake_case + sql-safe sanitizer as the file-stem path.
        parent_dir = Path(paths[0]).parent
        name = _derive_table_name(parent_dir) if parent_dir.name else _derive_table_name(path)
    else:
        name = _derive_table_name(path)
    name = _unique_name(name)
    qname = db.quote_ident(name)
    object_kw = 'table' if opts.materialize else 'view'
    if len(paths) == 1:
        from_clause = f'read_parquet({_sql_path_literal(paths[0])})'
        source_path = paths[0]
    else:
        # `union_by_name=true` lets sibling parquets with slightly
        # different column orderings (or extra/missing columns) union
        # cleanly; missing columns become NULL.
        list_sql = '[' + ', '.join(_sql_path_literal(p) for p in paths) + ']'
        from_clause = f'read_parquet({list_sql}, union_by_name=true)'
        source_path = f'{Path(paths[0]).parent} ({len(paths)} parquet files)'
    c = db.conn()
    # DuckDB's file_row_number virtual column numbers a single file for
    # free (an UPDATE from rowid is slow on parquet-loaded tables).  It
    # restarts per file, and a real column of that name would shadow it.
    (shadowed,) = c.execute(f"""
        select  count(*)
        from    (describe select * from {from_clause})
        where   column_name = 'file_row_number'
        ;
    """).fetchone()
    row_num = ('file_row_number + 1' if len(paths) == 1 and not shadowed
               else 'row_number() over ()')
    c.execute(f"""
        create or replace {object_kw} {qname} as
        select  {row_num}  as {db.quote_ident(ROW_NUM_COLUMN)}
        ,       *
        from    {from_clause}
        ;
    """)
    if opts.materialize:
        # An all-NULL parquet column comes in typed "NULL", which takes no
        # values and has no DDL type.
        db.retype_null_columns(c, name)
    if opts.materialize and opts.nullify_tokens:
        _nullify_placeholder_tokens(name, opts.nullify_tokens)
    row_count, col_count, cols = _describe(name)
    return IngestResult(
        name=name, source_path=source_path, format='parquet',
        materialized=opts.materialize, row_count=row_count,
        col_count=col_count, has_rejects=False, columns=cols,
    )


def inspect_parquet_set(path: str) -> dict:
    """List sibling .parquet files in the same directory.

    Returns ``{this_path, peers, total_bytes}`` where ``peers`` always
    includes the original path.  The frontend uses this to ask
    "load just this file or all parquet files in the folder?" before
    deciding which paths to pass to ``_load_parquet``.
    """
    p = Path(path)
    if not p.exists() or p.suffix.lower() != '.parquet':
        size = p.stat().st_size if p.exists() else 0
        return {'this_path': str(p), 'peers': [str(p)], 'total_bytes': size}

    parent = p.parent
    # Case-insensitive match; Windows is fine with .PARQUET / .parquet
    # naming variants in the same directory.
    seen: set[str] = set()
    peers: list[Path] = []
    for entry in sorted(parent.iterdir()):
        if not entry.is_file():
            continue
        if entry.suffix.lower() != '.parquet':
            continue
        key = str(entry).lower()
        if key in seen:
            continue
        seen.add(key)
        peers.append(entry)
    if not peers:
        peers = [p]

    total_bytes = sum(pp.stat().st_size for pp in peers)
    return {
        'this_path': str(p),
        'peers': [str(pp) for pp in peers],
        'total_bytes': int(total_bytes),
    }


# Values a placeholder token may sit next to and still be nulled: FQ's
# number shape (derive.SNAPPABLE_NUMERIC_RE) plus ISO and US dates, each
# with an optional time.
_DATE_SHAPE_RE = (
    r'^(\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?'
    r'|\d{1,2}/\d{1,2}/(\d{2}|\d{4})( \d{1,2}:\d{2}(:\d{2})?( ?[AP]M)?)?)$'
)


def _nullify_placeholder_tokens(table_name: str, tokens: list[str]) -> None:
    """Null placeholder tokens (NA, NaN, …) in VARCHAR columns whose other
    values are all number- or date-shaped, so 'NA' goes in a numeric
    column but stays as Namibia's code next to 'US' and 'DE'.

    DuckDB's min/max stats rule out most columns for free (text columns,
    numbers without a token); one query checks the rest and one UPDATE
    nulls the columns that qualify."""
    c = db.conn()
    qname = db.quote_ident(table_name)
    token_bytes = [t.encode('utf-8') for t in tokens]
    cols = [col for col, st in _varchar_stats(c, table_name).items()
            if _may_hold_only_tokens_and_shapes(st, token_bytes)]
    if not cols:
        return

    in_tokens = 'in (' + ', '.join(f'${i}' for i in range(1, len(tokens) + 1)) + ')'
    facts: list[str] = []
    for col in cols:
        qcol = db.quote_ident(col)
        facts += [
            f'count_if({qcol} {in_tokens})',
            f'count({qcol})',
            f"bool_and({qcol} {in_tokens} "
            f"or regexp_matches({qcol}, '{derive.SNAPPABLE_NUMERIC_RE}') "
            f"or regexp_matches({qcol}, '{_DATE_SHAPE_RE}'))",
        ]
    row = c.execute(f"""
        select  {_select_list(facts)}
        from    {qname}
        ;
    """, tokens).fetchone()
    qualifying = [
        db.quote_ident(col)
        for col, (hits, non_null, all_shaped) in zip(cols, zip(*[iter(row)] * 3))
        if hits and non_null > hits and all_shaped
    ]
    if not qualifying:
        return
    sets = '\n        ,       '.join(
        f'{q} = case when {q} {in_tokens} then null else {q} end' for q in qualifying)
    where = '\n        or      '.join(f'{q} {in_tokens}' for q in qualifying)
    c.execute(f"""
        update  {qname}
        set     {sets}
        where   {where}
        ;
    """, tokens)


def _varchar_stats(c: duckdb.DuckDBPyConnection, table_name: str) -> dict[str, dict | None]:
    """DuckDB's stats() for each VARCHAR data column; empty for an empty
    table.  Reading them costs next to nothing."""
    qname = db.quote_ident(table_name)
    cols = [r[1] for r in c.execute(f"""
        pragma table_info({qname})
        ;
    """).fetchall() if r[2].upper() == 'VARCHAR' and r[1] != ROW_NUM_COLUMN]
    if not cols:
        return {}
    row = c.execute(f"""
        select  {_select_list(f'stats({db.quote_ident(col)})' for col in cols)}
        from    {qname}
        limit   1
        ;
    """).fetchone()
    return dict(zip(cols, row)) if row else {}


def _may_hold_numbers(stats: dict | None) -> bool:
    """False when a column's stats rule out number-shaped values, which
    all start with '-' or a digit."""
    if not stats or not stats.get('has_no_null'):
        return False
    lo, hi = stats.get('min'), stats.get('max')
    if lo is None or hi is None:
        return True
    return _stats_bytes(lo)[:1] <= b'9' and _stats_bytes(hi)[:1] >= b'-'


def _may_hold_only_tokens_and_shapes(stats: dict | None, tokens: list[bytes]) -> bool:
    """Cheap check on a column's stats(): False when its min or max is
    neither a token nor number/date-shaped, or no token fits between
    them.  The stats hold a prefix of the real min and max."""
    if not stats or not stats.get('has_no_null'):
        return False
    lo, hi = stats.get('min'), stats.get('max')
    if lo is None or hi is None:
        return True
    lo, hi = _stats_bytes(lo), _stats_bytes(hi)

    def plausible(edge: bytes) -> bool:
        return (edge[:1].isdigit() or edge[:1] == b'-'
                or any(t.startswith(edge) for t in tokens))

    if not (plausible(lo) and plausible(hi)):
        return False
    return any(lo <= t and (t <= hi or t.startswith(hi)) for t in tokens)


def _stats_bytes(text: str) -> bytes:
    """stats() writes quotes, backslashes and non-ASCII bytes as \\xHH."""
    return re.sub(rb'\\x([0-9A-Fa-f]{2})',
                  lambda m: bytes([int(m.group(1), 16)]), text.encode('utf-8'))


def _select_list(items) -> str:
    """Leading-comma select list, indented for the 8-space SQL blocks here."""
    return '\n        ,       '.join(items)


# --------------------------------------------------------------------------- #
# Excel                                                                       #
# --------------------------------------------------------------------------- #

def _load_xlsx(path: Path, opts: IngestOptions) -> IngestResult:
    c = db.conn()
    c.execute("""
        install excel
        ;
    """)
    c.execute("""
        load excel
        ;
    """)

    name = opts.preferred_table_name or _derive_table_name(path)
    if opts.sheet and not opts.preferred_table_name:
        # Disambiguate when loading multiple sheets from the same file.
        name = f'{name}_{_derive_table_name(Path(opts.sheet))}'
    name = _unique_name(name)
    qname = db.quote_ident(name)
    object_kw = 'table' if opts.materialize else 'view'

    params: dict = {
        'all_varchar': True,
        'empty_as_varchar': True,
        'ignore_errors': True,
    }
    if opts.sheet:
        params['sheet'] = opts.sheet
    # Bounded `range=` keeps DuckDB's loaded columns aligned with the
    # date-intent probe below; `stop_at_empty=true` stops a column-only
    # range like `A1:B` from reading 1M empty Excel rows.
    start_col = (opts.excel_start_col or 'A').upper()
    header_row = max(1, opts.excel_header_row or 1)
    end_col = (opts.excel_end_col or '').upper() or None
    params['header'] = True
    if end_col is None:
        end_col = _detect_end_col_on_row(path, opts.sheet, header_row, start_col)
    params['range'] = f'{start_col}{header_row}:{end_col}'
    params['stop_at_empty'] = True

    def _build_sql(p: Path, kw: str) -> str:
        return f"""
            create or replace {kw} {qname} as
            select  {_row_num_placeholder(kw == 'table')}  as {db.quote_ident(ROW_NUM_COLUMN)}
            ,       *
            from    read_xlsx(
                        {_sql_path_literal(str(p))}
                      , {_fmt_params(params)}
                    )
            ;
        """

    cleaned: Path | None = None
    try:
        c.execute(_build_sql(path, object_kw))
    except duckdb.Error as ex:
        # DuckDB's excel reader is stricter than Excel about styles.xml
        # / xf entries; files exported by some tools (Aspose, certain
        # SaaS dashboards) parse fine in Excel but error here.  Re-save
        # the workbook via openpyxl, which rewrites styles.xml from
        # scratch, and retry once.  The retry forces TABLE mode (even
        # if the caller asked for a view) so we can safely delete the
        # cleaned temp file after load; a view would have nothing to
        # read once the temp is gone.
        if not _is_styles_xml_error(ex):
            raise
        try:
            cleaned = _resave_xlsx_for_duckdb(path)
        except Exception:
            # Couldn't even open in openpyxl; surface DuckDB's
            # original message rather than the secondary failure.
            raise ex from None
        try:
            c.execute(_build_sql(cleaned, 'table'))
        except duckdb.Error:
            cleaned.unlink(missing_ok=True)
            raise
    finally:
        if cleaned is not None and cleaned.exists():
            cleaned.unlink(missing_ok=True)

    if opts.materialize or cleaned is not None:
        _number_rows(name)
    if opts.materialize:
        # read_xlsx with all_varchar=True strips Excel's cell formats,
        # so re-read them via openpyxl and convert date serials → ISO.
        try:
            intents = _detect_excel_column_intents(
                path, opts.sheet, header_row, start_col, end_col,
                nullify_tokens=opts.nullify_tokens,
            )
        except Exception:
            intents = []  # best-effort; don't fail the load
        # Tokens first, while the stats still describe the raw cells.
        if opts.nullify_tokens:
            _nullify_placeholder_tokens(name, opts.nullify_tokens)
        _clean_excel_cells(name, intents)
    row_count, col_count, cols = _describe(name)
    return IngestResult(
        name=name, source_path=str(path), format='xlsx',
        materialized=opts.materialize, row_count=row_count,
        col_count=col_count, has_rejects=False, columns=cols,
    )


def _clean_excel_cells(table_name: str, intents: list[dict]) -> None:
    """Rewrite raw Excel cell text in one UPDATE.

    Date serials in intent-flagged columns become ISO strings.  Intent
    gates the column; the per-cell branch is picked by value range,
    because Excel format strings can lie (a `mmss.0`-formatted column may
    still carry full datetime serials, so we trust the value):

      integer in [367, 73415]               → date (1901..2100)
      fractional in [367, 73415]            → datetime
      fraction in [0, 1), time-intent only  → time-of-day

    Excel epoch is 1899-12-30 (the 1900 leap-year quirk).

    Every other numeric cell gets its float artifacts rounded off.  Excel
    stores numbers as doubles, so a cell like `=11/3` lands as
    3.66666666666667.  Each one is rounded to the nearest clean scale
    (0, 2, 4, or 6) when it sits within `1e-10` of one; anything further
    out falls through to `round(., 8)` as the upper limit.  Scientific
    notation (DuckDB hands it back for very small or very large numbers)
    becomes plain decimal the same way.  Values too large for
    `decimal(38, N)` keep their original text via `try_cast` and flow
    through the export-time fallback path.  The cleaned value goes
    through `decimal(...)` so integers read '5' instead of '5.0';
    trailing-zero trimming happens later in type derivation.
    """
    c = db.conn()
    qname = db.quote_ident(table_name)
    rows = c.execute(f"""
        pragma table_info({qname})
        ;
    """).fetchall()
    # Pair intents to data columns by position; skip the prepended ROW_NUM.
    data_cols = [r[1] for r in rows if r[1] != ROW_NUM_COLUMN]
    stats = _varchar_stats(c, table_name)
    sets: list[str] = []
    touched: list[str] = []
    for i, col_name in enumerate(data_cols):
        if col_name not in stats:
            continue
        qcol = db.quote_ident(col_name)
        intent = (intents[i].get('intent') or '') if i < len(intents) else ''
        branches = _excel_snap_branches(qcol)
        if intent in ('date', 'datetime', 'time'):
            branches = _excel_date_branches(qcol, intent) + branches
            touched.append(f'try_cast({qcol} as double) is not null')
        elif _may_hold_numbers(stats[col_name]):
            touched.append(f"regexp_matches({qcol}, '{derive.SNAPPABLE_NUMERIC_RE}')")
        else:
            # A multi-column UPDATE rewrites every listed column on each
            # touched row, so leave out columns with no numbers.
            continue
        sets.append(f'{qcol} = case\n                    '
                    + '\n                    '.join(branches)
                    + '\n                end')
    if not sets:
        return
    c.execute(f"""
        update  {qname}
        set     {_select_list(sets)}
        where   {' or '.join(touched)}
        ;
    """)


def _excel_date_branches(qcol: str, intent: str) -> list[str]:
    as_dbl = f'try_cast({qcol} as double)'
    date_expr = (f"strftime(date '1899-12-30' "
                 f"+ cast(floor({as_dbl}) as integer), '%Y-%m-%d')")
    dt_expr = (f"strftime(timestamp '1899-12-30' "
               f"+ to_microseconds(cast({as_dbl} * 86400000000 as bigint)), "
               f"'%Y-%m-%d %H:%M:%S')")
    branches = [
        f'when {as_dbl} between 367 and 73415 and {as_dbl} = floor({as_dbl}) then {date_expr}',
        f'when {as_dbl} between 367 and 73415 then {dt_expr}',
    ]
    if intent == 'time':
        # Gated on intent so stray 0.5s in date columns don't render as
        # 12:00:00.  strftime() doesn't accept TIME; cast straight to varchar.
        time_expr = (f"cast(time '00:00:00' "
                     f"+ to_microseconds(cast({as_dbl} * 86400000000 as bigint)) "
                     f"as varchar)")
        branches.append(f'when {as_dbl} >= 0 and {as_dbl} < 1 then {time_expr}')
    return branches


def _excel_snap_branches(qcol: str) -> list[str]:
    v = f'cast({qcol} as double)'

    # try_cast on the inner decimal cast so a magnitude that overflows
    # decimal(38, N) leaves the cell untouched instead of aborting the
    # whole UPDATE.
    def snap(scale: int) -> str:
        return (f'coalesce(try_cast(try_cast({v} as decimal(38, {scale})) '
                f'as varchar), {qcol})')

    return [
        f"when {qcol} is null "
        f"or not regexp_matches({qcol}, '{derive.SNAPPABLE_NUMERIC_RE}') then {qcol}",
        f"when abs({v}) < 1e-12 then '0'",
        f'when abs({v} - round({v}, 0)) < 1e-10 then {snap(0)}',
        f'when abs({v} - round({v}, 2)) < 1e-10 then {snap(2)}',
        f'when abs({v} - round({v}, 4)) < 1e-10 then {snap(4)}',
        f'when abs({v} - round({v}, 6)) < 1e-10 then {snap(6)}',
        f'else {snap(8)}',
    ]


def _is_styles_xml_error(ex: BaseException) -> bool:
    """True for DuckDB excel-reader complaints about styles.xml / xf
    entries.  These are the symptoms of cosmetic XLSX malformations
    that Excel tolerates but the bundled excel extension rejects."""
    msg = str(ex).lower()
    return 'styles.xml' in msg or 'xf entry' in msg or 'invalid xf' in msg


def _resave_xlsx_for_duckdb(path: Path) -> Path:
    """Open `path` with openpyxl and save to a sibling temp file with
    a clean styles.xml.  The returned path is the caller's to delete.
    Uses ``data_only=False`` so cell formulas survive the round-trip
    (DuckDB's reader will still see only the cached value)."""
    import tempfile
    import openpyxl
    wb = openpyxl.load_workbook(filename=str(path), read_only=False, data_only=False)
    fd, tmp = tempfile.mkstemp(prefix='fq_clean_', suffix='.xlsx',
                               dir=str(path.parent))
    import os
    os.close(fd)
    wb.save(tmp)
    wb.close()
    return Path(tmp)


def inspect_excel_file(path: str) -> dict:
    """Probe an Excel file for the chooser modal.

    Returns:
      ``{'kind': 'text', 'this_path': ...}`` when the file's bytes
      don't match the Excel magic; i.e. an ELT-transformed
      pipe-delimited file masquerading under an .xls/.xlsx extension.
      The frontend should fall back to the CSV ingest path.

      ``{'kind': 'excel', 'this_path': ..., 'sheets': [...]}``
      otherwise, with one entry per sheet:
      ``{'name': str, 'header_row': int, 'start_col': str,
         'detected': bool, 'preview': [[cell, ...], ...]}``.
      `header_row` is 1-based and `start_col` is a column letter; both
      are auto-detected from the densest non-empty row in the top-
      left corner of the sheet.  `detected` is True when the
      auto-detector chose anything other than A1; the frontend can
      use it to highlight that we cropped a preamble.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    if not _looks_like_excel(p):
        return {'kind': 'text', 'this_path': str(p), 'sheets': []}

    # openpyxl is already a project dependency; used in the export
    # writer.  Read-only mode is fast and memory-light.
    import openpyxl
    wb = openpyxl.load_workbook(filename=str(p), read_only=True, data_only=True)
    try:
        sheets: list[dict] = []
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            preview, header_row, start_col_idx, end_col_idx = _excel_sheet_preview(ws)
            start_col = _col_letter(start_col_idx)
            end_col = _col_letter(end_col_idx)
            sheets.append({
                'name': sheet_name,
                'header_row': header_row,
                'start_col': start_col,
                'end_col': end_col,
                'detected': header_row > 1 or start_col != 'A',
                'preview': preview,
            })
        return {'kind': 'excel', 'this_path': str(p), 'sheets': sheets}
    finally:
        wb.close()


def load_excel_workbook(path: str, sheet_specs: list[dict],
                        opts: IngestOptions | None = None) -> list[dict]:
    """Load one or more sheets from an Excel file.  Each entry in
    ``sheet_specs`` is ``{'name', 'header_row', 'start_col'}``.  Returns
    a list of ``IngestResult``-as-dict.  The frontend gets back the
    summary for every sheet it asked for so it can list them in a
    success toast and refresh the sidebar."""
    base_opts = opts or IngestOptions()
    results: list[dict] = []
    for spec in sheet_specs:
        sub = IngestOptions(
            **{k: v for k, v in base_opts.__dict__.items()
               if k not in ('sheet', 'excel_header_row', 'excel_start_col',
                            'excel_end_col', 'preferred_table_name')},
            sheet=spec.get('name'),
            excel_header_row=int(spec.get('header_row') or 1),
            excel_start_col=(spec.get('start_col') or 'A'),
            excel_end_col=(spec.get('end_col') or None),
        )
        r = load_file(path, sub)
        from dataclasses import asdict
        results.append(asdict(r))
    return results


def _detect_end_col_on_row(path: Path, sheet: str | None,
                           header_row: int, start_col: str) -> str:
    """Open the workbook with openpyxl and return the rightmost
    non-empty column letter on ``header_row`` (1-based).  Used as a
    bounded fallback for DuckDB's `range=` argument so we never
    allocate the full 16k-column block.

    Falls back to ``start_col`` when the row turns out to be empty;
    that gives a single-column range which is benign and will hand
    back zero rows via `stop_at_empty=true`."""
    import openpyxl
    start_idx = _col_index_from_letter(start_col)
    wb = openpyxl.load_workbook(filename=str(path), read_only=True, data_only=True)
    try:
        # Match DuckDB's first-sheet default (wb.active may differ).
        ws = wb[sheet] if sheet else wb.worksheets[0]
        scan_max = _scan_col_bound(ws)
        rightmost = start_idx
        for row in ws.iter_rows(min_row=header_row, max_row=header_row,
                                min_col=1, max_col=scan_max, values_only=True):
            for j, v in enumerate(row, start=1):
                if v is not None and str(v) != '':
                    if j > rightmost:
                        rightmost = j
            break  # only one row
        return _col_letter(rightmost)
    finally:
        wb.close()


def _scan_col_bound(ws, hard_cap: int = 1024) -> int:
    """Column upper bound from ws.max_column, capped to defend against
    stale or 16384-default <dimension> tags."""
    try:
        mx = ws.max_column or 0
    except Exception:
        mx = 0
    if mx <= 0 or mx > hard_cap:
        return hard_cap
    return int(mx)


def _detect_excel_column_intents(path: Path, sheet: str | None,
                                 header_row: int, start_col: str,
                                 end_col: str | None,
                                 nullify_tokens: list[str] | None = None,
                                 sample_rows: int = 200) -> list[dict]:
    """Classify each column in [start_col..end_col] by openpyxl
    number_format: 'date', 'datetime', 'time', or '' for mixed/none.
    Needs ≥80% of non-null sampled cells to agree.  Cells whose string
    value is in ``nullify_tokens`` are skipped so a mostly-'NULL'
    column with one real date still gets classified."""
    import openpyxl
    nullset = set(nullify_tokens or ())
    start_idx = _col_index_from_letter(start_col)
    wb = openpyxl.load_workbook(filename=str(path), read_only=True, data_only=True)
    try:
        # Match DuckDB's first-sheet default (wb.active may differ).
        ws = wb[sheet] if sheet else wb.worksheets[0]
        end_idx = (_col_index_from_letter(end_col)
                   if end_col else _scan_col_bound(ws))
        if end_idx < start_idx:
            end_idx = start_idx
        data_start = header_row + 1
        data_end = data_start + sample_rows - 1

        tally: dict[int, dict[str, int]] = {}
        for row in ws.iter_rows(min_row=data_start, max_row=data_end,
                                min_col=start_idx, max_col=end_idx):
            for cell in row:
                v = cell.value
                if v is None or v == '':
                    continue
                if isinstance(v, str) and v in nullset:
                    continue
                t = tally.setdefault(cell.column, {
                    'date': 0, 'datetime': 0, 'time': 0, 'total': 0
                })
                t['total'] += 1
                kind = _classify_excel_format(cell.number_format or '')
                if kind in ('date', 'datetime', 'time'):
                    t[kind] += 1
    finally:
        wb.close()

    out: list[dict] = []
    for col_idx in range(start_idx, end_idx + 1):
        t = tally.get(col_idx)
        intent = ''
        if t and t['total'] > 0:
            # Datetime wins ties as the more specific form.
            best = max(('datetime', 'date', 'time'), key=lambda k: t[k])
            if t[best] >= max(1, int(t['total'] * 0.8)):
                intent = best
        out.append({'index': col_idx, 'letter': _col_letter(col_idx), 'intent': intent})
    return out


def _classify_excel_format(fmt: str) -> str:
    """Excel number_format → 'date' | 'datetime' | 'time' | 'other'.
    `m` is ambiguous (month vs minute), so we only look at unambiguous
    tokens once is_date_format confirms it's some kind of date/time."""
    from openpyxl.styles.numbers import is_date_format
    if not fmt:
        return 'other'
    low = fmt.lower().strip()
    if low in ('', 'general'):
        return 'other'
    if not is_date_format(fmt):
        return 'other'
    # Drop bracketed sections (`[h]`, `[$-409]`) and quoted literals.
    stripped = re.sub(r'\[[^\]]*\]', '', low)
    stripped = re.sub(r'"[^"]*"', '', stripped)
    has_date = 'y' in stripped or 'd' in stripped or 'mmm' in stripped
    has_time = 'h' in stripped or 's' in stripped or 'am/pm' in low or 'a/p' in low
    if has_date and has_time:
        return 'datetime'
    if has_date:
        return 'date'
    if has_time:
        return 'time'
    return 'date'  # is_date_format true but no unambiguous tokens


def _col_index_from_letter(letter: str) -> int:
    """Excel column letter → 1-based index ('A' → 1, 'AA' → 27)."""
    n = 0
    for ch in (letter or 'A').upper():
        if not ('A' <= ch <= 'Z'):
            continue
        n = n * 26 + (ord(ch) - ord('A') + 1)
    return n or 1


def _col_letter(idx: int) -> str:
    """1-based column index → Excel column letter (A, B, ..., Z, AA, AB, ...)."""
    if idx < 1:
        idx = 1
    out = ''
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        out = chr(ord('A') + rem) + out
    return out


def _excel_sheet_preview(ws, max_rows: int = 30, preview_col_cap: int = 200) -> tuple[list[list[str]], int, int, int]:
    """Read a sample of the top-left of ``ws`` and return:
    ``(preview_grid, header_row, start_col, end_col)`` (all 1-based).

    Column extent comes from ws.max_column (capped) so wide sheets
    expose every column instead of getting clipped at 30.

    Auto-detect strategy:
      * Walk the sample looking for the row with the highest density
        of non-empty cells.  That row is almost always the header in
        files with a preamble (title rows are sparse, data rows are
        dense, header rows are dense too).  Earliest densest row
        wins so we keep as many rows as possible.
      * The start / end columns are the leftmost / rightmost non-
        empty cells on that same row.
      * If nothing looks data-shaped, default to (1, 1, 1).
    """
    max_cols = _scan_col_bound(ws, hard_cap=preview_col_cap)
    preview: list[list[str]] = []
    for row in ws.iter_rows(min_row=1, max_row=max_rows,
                            min_col=1, max_col=max_cols,
                            values_only=True):
        preview.append(['' if v is None else str(v) for v in row])
    if not preview:
        return preview, 1, 1, 1

    best_density = 0
    best_row = 1
    best_left = 1
    best_right = 1
    for r_idx, row in enumerate(preview, start=1):
        nonempty_cols = [i for i, v in enumerate(row, start=1) if v != '']
        density = len(nonempty_cols)
        if density > best_density:
            best_density = density
            best_row = r_idx
            best_left = nonempty_cols[0]
            best_right = nonempty_cols[-1]

    # Sparse / non-tabular; don't pretend we found a header.
    if best_density < 2:
        return preview, 1, 1, 1
    return preview, best_row, best_left, best_right


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _looks_like_excel(path: Path) -> bool:
    """Magic-byte check.  `.xlsx` / `.xlsm` are ZIP archives starting
    with `PK\\x03\\x04`; `.xls` is an OLE compound document starting
    with `\\xd0\\xcf\\x11\\xe0\\xa1\\xb1\\x1a\\xe1`.  Anything else is a
    text file with a misleading extension."""
    try:
        with path.open('rb') as f:
            head = f.read(8)
    except OSError:
        return False
    return head.startswith(b'PK\x03\x04') or head.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1')


def _derive_table_name(path: Path) -> str:
    raw = path.stem
    clean = SQL_SAFE_NAME.sub('_', raw).strip('_').lower() or 'table'
    if clean[0].isdigit():
        clean = 't_' + clean
    return clean


def _unique_name(base: str) -> str:
    c = db.conn()
    rows = c.execute("""
        select  table_name
        from    information_schema.tables
        where   table_schema = current_schema()
        ;
    """).fetchall()
    existing = {r[0] for r in rows}
    if base not in existing:
        return base
    i = 2
    while f'{base}_{i}' in existing:
        i += 1
    return f'{base}_{i}'


def _rename_default_columns(name: str) -> None:
    """DuckDB names headerless columns column0, column1, ...  → col_0, col_1."""
    c = db.conn()
    rows = c.execute(f"""
        pragma table_info({db.quote_ident(name)})
        ;
    """).fetchall()
    for row in rows:
        old = row[1]
        if old.startswith('column') and old[len('column'):].isdigit():
            new = 'col_' + old[len('column'):]
            c.execute(f"""
                alter table {db.quote_ident(name)}
                rename column {db.quote_ident(old)} to {db.quote_ident(new)}
                ;
            """)


def _describe(name: str) -> tuple[int, int, list[dict]]:
    c = db.conn()
    rows = c.execute(f"""
        pragma table_info({db.quote_ident(name)})
        ;
    """).fetchall()
    cols = [{'name': r[1], 'type': r[2]} for r in rows]
    (rc,) = c.execute(f"""
        select  count(*)
        from    {db.quote_ident(name)}
        ;
    """).fetchone()
    return int(rc), len(cols), cols


def _sql_str_literal(s: str) -> str:
    """SQL string literal — single-quote escape only."""
    return "'" + s.replace("'", "''") + "'"


def _sql_path_literal(p: str) -> str:
    """SQL string literal for a filesystem path: also normalizes `\\`
    to `/` so Windows paths round-trip through DuckDB's read_csv /
    read_parquet without backslash-escape headaches."""
    return _sql_str_literal(p.replace('\\', '/'))


def _fmt_params(params: dict) -> str:
    """Format a dict as `key1 = val1, key2 = val2` for DuckDB function args."""
    parts = []
    for k, v in params.items():
        if isinstance(v, bool):
            parts.append(f"{k} = {'true' if v else 'false'}")
        elif isinstance(v, int):
            parts.append(f"{k} = {v}")
        elif isinstance(v, str):
            parts.append(f"{k} = {_sql_str_literal(v)}")
        elif isinstance(v, list):
            parts.append(f"{k} = [{', '.join(_sql_str_literal(x) for x in v)}]")
        elif isinstance(v, dict):
            inner = ', '.join(f'{_sql_str_literal(a)}: {_sql_str_literal(b)}' for a, b in v.items())
            parts.append(f'{k} = {{{inner}}}')
        else:
            raise TypeError(f'unsupported param type for {k}: {type(v).__name__}')
    return ', '.join(parts)
