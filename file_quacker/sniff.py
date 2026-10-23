"""Quick looks at a delimited text file before DuckDB reads it.

Three guesses: the encoding, the delimiter, and the layout (title lines
above the header, whether there is a header at all, and summary lines at
the bottom).  Pure Python and cheap on big files: the encoding check
reads every byte, since one bad byte near the end still decides it; the
other two read only the head and the tail of the file.
"""

from __future__ import annotations

import codecs
import csv
import io
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from statistics import median

CANDIDATE_DELIMS = (',', '|', ';', '\t')

# encoding scan chunk; small enough to stay in the CPU cache, which makes
# the scan about 1.5x faster than 1-4 MB chunks
SCAN_BYTES = 128 << 10
HEAD_BYTES = 1 << 20      # the head sample is this many bytes...
HEAD_RECORDS = 1000       # ...or this many records, whichever ends first
TAIL_BYTES = 64 << 10     # the tail sample for footers
VOTE_ROWS = 200           # rows under the header candidate that vote on it

# a record filled under this share of the median of the records near it
# is a title or summary line
SPARSE_SHARE = 0.5
# the share of a column's values that makes it a number, date or fixed-length column
TYPED_SHARE = 0.9
MAX_PREAMBLE = 50
MAX_FOOTER = 20

# A file with a few bad bytes among plenty of good UTF-8 stays UTF-8 (the
# rows holding them get rejected) rather than every accent turning into
# mojibake under cp1252.  Real cp1252 files have next to no sequences that
# happen to be valid UTF-8, so the share cut-off has a wide margin.
MAX_BAD_UTF8 = 100
MAX_BAD_UTF8_SHARE = 0.1      # of all non-ASCII sequences
COUNT_BYTES = 256 << 20       # how far the bad-byte count reads

# DuckDB's built-in latin-1 rejects every byte in 0x80-0x9F, and its
# cp1252 (like Python's) rejects these five
_NOT_C1 = bytes(b for b in range(256) if not 0x80 <= b <= 0x9F)
_ASCII = bytes(range(128))
_CP1252_UNDEFINED = b'\x81\x8d\x8f\x90\x9d'

_BIG_ENDIAN = 'big-endian UTF-16, which DuckDB reads as garbage: save the file as UTF-8 or little-endian UTF-16'

# the first cell of a footer line: "Total", "Grand total:", "Rows: 12", ...
_SUMMARY = re.compile(r'(grand\s+)?(totals?|count|sum|end|generated|report|records|rows|page)\b', re.IGNORECASE)
_YEAR = re.compile(r'(19[0-9]{2}|20[0-9]{2}|2100)')

_NUMBER = re.compile(r'[+-]?[0-9][0-9,]*(\.[0-9]+)?')
_DATE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}([T ][0-9]{2}:[0-9]{2}(:[0-9]{2}(\.[0-9]+)?)?)?'
                   r'|[0-9]{1,2}/[0-9]{1,2}/[0-9]{4}')


@dataclass
class EncodingGuess:
    encoding: str   # a DuckDB read_csv encoding name
    bom: bool
    why: str
    clean: bool = True  # False: a few bytes don't decode, so load with ignore_errors
    big_endian_utf16: bool = False  # DuckDB can't read it under any encoding name


@dataclass
class DelimiterScore:
    delimiter: str
    field_count: int    # most common field count (> 1) across sampled records
    coverage: float     # share of sampled records with field_count fields


@dataclass
class Layout:
    first_row: int      # FQ's first_row: 1-based line of the header, 0 = no header row
    footer_rows: int    # trailing rows to drop (0 when none)
    why: str


# --------------------------------------------------------------------------- #
# Encoding                                                                    #
# --------------------------------------------------------------------------- #

def detect_encoding(path: Path) -> EncodingGuess:
    """A byte order mark decides.  Otherwise utf-8 when every byte is
    utf-8 (plain ASCII included), or when a few bad bytes sit among
    plenty of good UTF-8 (clean=False), then latin-1 when no byte falls in
    0x80-0x9F (same text as cp1252, and DuckDB reads it without the
    encodings extension), then cp1252, then iso-8859-1, which maps every
    byte.
    """
    with open(path, 'rb') as f:
        head = f.read(4096)
    if not head:
        return EncodingGuess('utf-8', False, 'the file is empty')
    if head.startswith(codecs.BOM_UTF8):
        return EncodingGuess('utf-8', True, 'the file starts with a UTF-8 byte order mark')
    if head.startswith(codecs.BOM_UTF16_LE):
        return EncodingGuess('utf-16', True, 'the file starts with a UTF-16 byte order mark')
    if head.startswith(codecs.BOM_UTF16_BE):
        return EncodingGuess('utf-16', True, _BIG_ENDIAN, big_endian_utf16=True)
    order = _utf16_order(head)
    if order == 'le':
        return EncodingGuess('utf-16', False, 'every other byte is zero: UTF-16 without a byte order mark')
    if order == 'be':
        return EncodingGuess('utf-16', False, _BIG_ENDIAN, big_endian_utf16=True)

    bad = _first_non_utf8(path)
    if bad is None:
        return EncodingGuess('utf-8', False, 'every byte reads as UTF-8')
    valid, n_bad, complete = _utf8_counts(path)
    n_bad = max(n_bad, 1)   # the first one may sit past COUNT_BYTES
    if valid and n_bad <= MAX_BAD_UTF8 and n_bad <= MAX_BAD_UTF8_SHARE * (valid + n_bad):
        some = f'{n_bad}' if complete else f'at least {n_bad}'
        return EncodingGuess('utf-8', False, f'UTF-8 with {some} bad byte sequence(s) (first at byte {bad:,}); '
                                             'rows holding them will be rejected', clean=False)
    with open(path, 'rb') as f:
        f.seek(bad)
        where = f'byte 0x{f.read(1)[0]:02X} at offset {bad:,} is not UTF-8'
    single = _single_byte_encoding(path)
    if single == 'latin-1':
        return EncodingGuess(single, False, f'{where}; no byte falls in 0x80-0x9F, so latin-1 reads it')
    if single == 'cp1252':
        return EncodingGuess(single, False, f'{where}; every byte is Windows-1252')
    return EncodingGuess(single, False,
                         f'{where}, and bytes like 0x81 are not Windows-1252 either; ISO-8859-1 reads every byte')


def _utf16_order(head: bytes) -> str | None:
    # ASCII saved as UTF-16 has a zero in every other byte
    even, odd = head[0::2].count(0), head[1::2].count(0)
    if odd > len(head) // 5 and even * 10 < odd:
        return 'le'
    if even > len(head) // 5 and odd * 10 < even:
        return 'be'
    return None


def _first_non_utf8(path: Path) -> int | None:
    """Offset of the first byte that isn't UTF-8, None when the whole
    file is.  The incremental decoder carries a character split across
    two chunks; all-ASCII chunks skip the decode.
    """
    dec = codecs.getincrementaldecoder('utf-8')()
    pos = 0
    with open(path, 'rb') as f:
        while chunk := f.read(SCAN_BYTES):
            pending = len(dec.getstate()[0])
            if pending or not chunk.isascii():
                try:
                    dec.decode(chunk)
                except UnicodeDecodeError as ex:
                    return pos - pending + ex.start
            pos += len(chunk)
    pending = len(dec.getstate()[0])
    return pos - pending if pending else None


def _utf8_counts(path: Path) -> tuple[int, int, bool]:
    """(valid multi-byte UTF-8 sequences, bad ones, whether the count saw
    the whole file).  Reads up to COUNT_BYTES and stops early once the bad
    ones pass MAX_BAD_UTF8.  Each bad sequence decodes to one U+FFFD.
    """
    dec = codecs.getincrementaldecoder('utf-8')('replace')
    valid = bad = pos = 0
    with open(path, 'rb') as f:
        while chunk := f.read(SCAN_BYTES):
            pos += len(chunk)
            if chunk.isascii() and not dec.getstate()[0]:
                continue
            text = dec.decode(chunk)
            # a real U+FFFD in the file isn't a bad byte
            n_bad = text.count('\ufffd') - chunk.count(b'\xef\xbf\xbd')
            n_ascii = len(chunk) - len(chunk.translate(None, _ASCII))
            valid += len(text) - n_ascii - n_bad
            bad += n_bad
            if bad > MAX_BAD_UTF8 or pos >= COUNT_BYTES:
                return valid, bad, False
    # a character cut off by the end of the file
    bad += dec.decode(b'', final=True).count('\ufffd')
    return valid, bad, True


def _single_byte_encoding(path: Path) -> str:
    """latin-1 when no byte falls in 0x80-0x9F, cp1252 when all of those
    are Windows-1252 characters, else iso-8859-1.
    """
    any_c1 = False
    with open(path, 'rb') as f:
        while chunk := f.read(SCAN_BYTES):
            if any(b in chunk for b in _CP1252_UNDEFINED):
                return 'iso-8859-1'
            # the translate is the slow part; after one hit only the five matter
            any_c1 = any_c1 or bool(chunk.translate(None, _NOT_C1))
    return 'cp1252' if any_c1 else 'latin-1'


# --------------------------------------------------------------------------- #
# Delimiter                                                                   #
# --------------------------------------------------------------------------- #

def score_delimiters(path: Path, encoding: str, quote: str = '"') -> list[DelimiterScore]:
    """The candidates that split the head into more than one field, best
    first: most records covered, then more fields, then candidate order.
    """
    text, _ = _head_text(path, encoding)
    scores = []
    for delim in CANDIDATE_DELIMS:
        rows = [r for _, r in _records(text, delim, quote)[0] if r]
        counts = Counter(len(r) for r in rows if len(r) > 1)
        if counts:
            n, hits = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
            scores.append(DelimiterScore(delim, n, hits / len(rows)))
    return sorted(scores, key=lambda s: (-s.coverage, -s.field_count, CANDIDATE_DELIMS.index(s.delimiter)))


# --------------------------------------------------------------------------- #
# Layout                                                                      #
# --------------------------------------------------------------------------- #

def detect_layout(path: Path, encoding: str, delimiter: str, quote: str = '"') -> Layout:
    """Where the header sits and how many summary rows trail the data.
    Leans to "header on line 1, no footer" unless the file clearly says
    otherwise.
    """
    text, whole = _head_text(path, encoding)
    recs, complete = _records(text, delimiter, quote)
    if not recs:
        return Layout(1, 0, 'the file is empty')
    start = _first_full_record(recs)
    if start is None:
        return Layout(1, 0, f'none of the first {MAX_PREAMBLE} rows is filled like the rows below it')

    line, head = recs[start]
    below = [r for _, r in recs[start + 1:]]
    has_header, why = _header_verdict(head, below)
    titles = f'{start} title line(s) above; ' if start else ''
    if has_header:
        first_row = _skip_target(path, recs, start, quote)
        first_data, data = start + 1, below
        why = f'{titles}header on line {line}: {why}'
    else:
        first_row = 0
        first_data, data = start, [head] + below
        why = (f'{start} title line(s) above stay in, since there is no header to skip to; ' if start else '') + why

    # the head sample may hold the footer itself; keep it out of the stats
    if whole and complete:
        stats = data[:max(1, len(data) - MAX_FOOTER)]
    else:
        stats = data
    footer = _footer_rows(path, encoding, delimiter, quote, stats, first_data)
    if footer:
        why += f'; {footer} summary row(s) at the bottom'
    return Layout(first_row, footer, why)


def _first_full_record(recs: list[tuple[int, list[str]]]) -> int | None:
    """Index of the first record that isn't a title line.  A title line
    is filled under half the median of the next 50 records.  When it is
    as wide as them too (Excel pads every line to the sheet's width), it
    only counts as a title if the next well-filled record wins the header
    vote; otherwise it is a header with blank names (`id,,,,`).
    """
    for k in range(min(MAX_PREAMBLE, len(recs))):
        if not _sparse(recs, k):
            return k
        width = Counter(len(r) for _, r in recs[k + 1:k + 51]).most_common(1)[0][0]
        if len(recs[k][1]) >= width and not _header_further_down(recs, k):
            return k
    return None


def _sparse(recs: list[tuple[int, list[str]]], k: int) -> bool:
    nxt = [_filled(r) for _, r in recs[k + 1:k + 51]]
    return bool(nxt) and _filled(recs[k][1]) < SPARSE_SHARE * median(nxt)


def _header_further_down(recs: list[tuple[int, list[str]]], k: int) -> bool:
    j = next((j for j in range(k + 1, min(MAX_PREAMBLE, len(recs))) if not _sparse(recs, j)), None)
    if j is None:
        return False
    hv, dv, _ = _votes(recs[j][1], [r for _, r in recs[j + 1:j + 1 + VOTE_ROWS]])
    return hv > dv


def _header_verdict(head: list[str], below: list[list[str]]) -> tuple[bool, str]:
    if not below:
        named = all(_reads_as_name(v) for v in head if v.strip())
        return named, 'one row, ' + ('every cell reads as a name' if named else 'a cell is not a name')
    hv, dv, typed = _votes(head, below[:VOTE_ROWS])
    # no header takes a first row that matches the number or date shape
    # of most typed columns; text and code columns never argue for it
    if dv * 2 > typed:
        return False, f'no header: {dv} of {typed} number/date columns match the first row'
    if not hv and not dv:
        return True, 'no typed column argues against a header'
    return True, f'{hv} cells read as names over typed or fixed-width columns, {dv} look like data'


def _votes(head: list[str], rows: list[list[str]]) -> tuple[int, int, int]:
    """(header votes, data votes, typed columns) for a candidate header
    row.  Per column, from the values below it: when 90% share a number
    or date shape, the cell votes data if it fits those values, and
    header if it reads as a name instead.  When 90% share one length
    instead, a name of another length votes header; a code column's
    header often has the codes' length, so it never votes data.  A year
    over a column of non-years reads as a name.
    """
    hv = dv = typed = 0
    for j, cell in enumerate(head):
        vals = [r[j].strip() for r in rows if j < len(r) and r[j].strip()]
        if not vals or not any(ch.isalnum() for ch in cell):
            continue
        cell = cell.strip()
        shapes = Counter(_shape(v) for v in vals)
        kind = 'number' if shapes['number'] >= shapes['date'] else 'date'
        n_len, how_many = Counter(len(v) for v in vals).most_common(1)[0]
        if shapes[kind] >= TYPED_SHARE * len(vals):
            typed += 1
            if _fits(cell, kind, vals):
                dv += 1
            elif _reads_as_name(cell) or _YEAR.fullmatch(cell):
                hv += 1
        elif len(vals) >= 2 and how_many >= TYPED_SHARE * len(vals):
            hv += len(cell) != n_len and _reads_as_name(cell)
    return hv, dv, typed


def _fits(cell: str, kind: str, vals: list[str]) -> bool:
    """True when the cell could be one more value of the column: same
    shape, a length the values have, and no year over a column of
    non-years ("2024" heading a column of counts).
    """
    if _shape(cell) != kind or not min(map(len, vals)) <= len(cell) <= max(map(len, vals)):
        return False
    if _YEAR.fullmatch(cell):
        return sum(1 for v in vals if _YEAR.fullmatch(v)) >= TYPED_SHARE * len(vals)
    return True


def _shape(v: str) -> str:
    if _NUMBER.fullmatch(v):
        return 'number'
    if _DATE.fullmatch(v):
        return 'date'
    return 'text'


def _reads_as_name(v: str) -> bool:
    return any(ch.isalpha() for ch in v)


def _filled(row: list[str]) -> int:
    return sum(1 for v in row if v.strip())


def _skip_target(path: Path, recs: list[tuple[int, list[str]]], start: int, quote: str) -> int:
    """first_row for the header at recs[start], so FQ's skip = first_row - 1
    lands on it.  DuckDB's skip counts lines when the file has a byte
    order mark or quoting is off, and quote-aware records otherwise; the
    two differ only when a title line holds a quoted line break.
    """
    with open(path, 'rb') as f:
        bom = f.read(3).startswith((codecs.BOM_UTF8, codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE))
    return recs[start][0] if bom or not quote else start + 1


def _footer_rows(path: Path, encoding: str, delimiter: str, quote: str,
                 data: list[list[str]], first_data: int) -> int:
    """Trailing one-line records that are narrower than the data (when no
    data row in the head is, and the first cell doesn't look like the
    first column's values), or that read like a summary ("Total: 12")
    and are narrower or sparser than a typical data row.  A sparse row
    alone stays: a missed footer shows, a dropped row doesn't.  Blank
    lines don't count; DuckDB skips them, so they never become rows.
    """
    data = [r for r in data if r]
    if not data:
        return 0
    n = Counter(len(r) for r in data).most_common(1)[0][0]
    typical_fill = median(_filled(r) for r in data)
    ragged = any(len(r) < n for r in data)

    tail = _tail_records(path, encoding, delimiter, quote, n, first_data)
    rows = [r for r in tail if r]
    for count, r in enumerate(reversed(rows)):
        if count > MAX_FOOTER:
            return 0
        narrower = len(r) < n
        summary = _reads_as_summary(r) and (narrower or _filled(r) < typical_fill)
        cut_off = narrower and r[0].strip() and _fits_first_column(r[0].strip(), data)
        one_line = not any('\n' in v or '\r' in v for v in r)
        if not (one_line and len(r) <= n and (narrower and not ragged and not cut_off or summary)):
            return count
    return 0


def _fits_first_column(cell: str, data: list[list[str]]) -> bool:
    """True when the cell has the shape of the first column's values (a
    number among numbers, a code of the codes' length): a narrower last
    row starting like that is a cut-off data row, not a footer.
    """
    vals = [r[0].strip() for r in data if r and r[0].strip()]
    if not vals:
        return False
    kind, hits = Counter(map(_shape, vals)).most_common(1)[0]
    if kind != 'text' and hits >= TYPED_SHARE * len(vals):
        return _shape(cell) == kind
    n_len, how_many = Counter(map(len, vals)).most_common(1)[0]
    return how_many >= TYPED_SHARE * len(vals) and len(cell) == n_len


def _reads_as_summary(row: list[str]) -> bool:
    first = next((v.strip() for v in row if v.strip()), '')
    return bool(_SUMMARY.match(first))


def _tail_records(path: Path, encoding: str, delimiter: str, quote: str,
                  n: int, first_data: int) -> list[list[str]]:
    """The records in the last TAIL_BYTES of the file (from first_data on
    when that is the whole file); empty when they don't parse.
    """
    size = os.path.getsize(path)
    codec = _codec(encoding)
    start = max(0, size - TAIL_BYTES)
    if codec.startswith('utf-16'):
        start -= start % 2
    with open(path, 'rb') as f:
        f.seek(start)
        text = f.read().decode(codec, errors='replace')
    if start == 0:
        recs, ok = _records(text.removeprefix('\ufeff'), delimiter, quote, limit=None)
        return [r for _, r in recs[first_data:]] if ok else []

    cut = text.find('\n')
    if cut < 0:
        return []
    # The window may open inside a quoted field that spans lines.  Parse it
    # both ways (from the first line start, and from the first closing
    # quote) and keep the one whose last records match the data's width.
    tries = [(text[cut + 1:], 0)]
    if quote:
        close = re.search(re.escape(quote) + '(?=[' + re.escape(delimiter) + '\r\n])', text)
        if close:
            tries.append((text[close.end():], 1))
    best, best_hits = [], -1
    for chunk, partial in tries:
        recs, ok = _records(chunk, delimiter, quote, limit=None)
        rows = [r for _, r in recs[partial:]]
        hits = sum(len(r) == n for r in rows[-50:])
        if ok and hits > best_hits:
            best, best_hits = rows, hits
    return best


# --------------------------------------------------------------------------- #
# Shared readers                                                              #
# --------------------------------------------------------------------------- #

def _codec(encoding: str) -> str:
    # DuckDB's utf-16 is little-endian; a name Python doesn't know still
    # gives the ASCII delimiters back through latin-1
    if encoding.lower().replace('_', '-') == 'utf-16':
        return 'utf-16-le'
    try:
        return codecs.lookup(encoding).name
    except LookupError:
        return 'latin-1'


def _head_text(path: Path, encoding: str) -> tuple[str, bool]:
    """The first HEAD_BYTES as text, cut back to a whole line, and whether
    that is the whole file.
    """
    with open(path, 'rb') as f:
        raw = f.read(HEAD_BYTES + 1)
    whole = len(raw) <= HEAD_BYTES
    dec = codecs.getincrementaldecoder(_codec(encoding))('replace')
    text = dec.decode(raw[:HEAD_BYTES], final=whole).removeprefix('\ufeff')
    if not whole and '\n' in text:
        text = text[:text.rindex('\n') + 1]
    return text, whole


def _records(text: str, delimiter: str, quote: str,
             limit: int | None = HEAD_RECORDS) -> tuple[list[tuple[int, list[str]]], bool]:
    """(1-based first line, fields) per record, read the way DuckDB quotes
    (a doubled quote inside quotes; quote='' turns quoting off), and
    whether the text parsed to its end.  A blank line is a record of no
    fields.
    """
    if quote:
        rdr = csv.reader(io.StringIO(text, newline=''), delimiter=delimiter, quotechar=quote, doublequote=True)
    else:
        rdr = csv.reader(io.StringIO(text, newline=''), delimiter=delimiter, quoting=csv.QUOTE_NONE)
    out: list[tuple[int, list[str]]] = []
    line = 1
    try:
        for row in rdr:
            out.append((line, row))
            line = rdr.line_num + 1
            if limit is not None and len(out) >= limit:
                return out, False
    except csv.Error:
        # an oversized field or a stray quote: keep what parsed
        return out, False
    return out, True
