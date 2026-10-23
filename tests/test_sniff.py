"""sniff: encoding, delimiter and layout guesses on small generated files."""

from __future__ import annotations

import codecs

import duckdb
import pytest

from file_quacker import sniff

HEADER = 'id,name,amount,day\n'


def _rows(n: int, start: int = 1) -> str:
    return ''.join(f'{i},name {i},{i * 1.5:.2f},2024-01-{i % 28 + 1:02d}\n' for i in range(start, start + n))


def _write(tmp_path, data: bytes | str, name: str = 'f.csv'):
    p = tmp_path / name
    p.write_bytes(data.encode('utf-8') if isinstance(data, str) else data)
    return p


# --------------------------------------------------------------------------- #
# Encoding                                                                    #
# --------------------------------------------------------------------------- #

def test_empty_file_is_utf8(tmp_path):
    g = sniff.detect_encoding(_write(tmp_path, b''))
    assert (g.encoding, g.bom) == ('utf-8', False)


def test_ascii_is_utf8(tmp_path):
    g = sniff.detect_encoding(_write(tmp_path, HEADER + _rows(5)))
    assert (g.encoding, g.bom) == ('utf-8', False)


def test_utf8_bom(tmp_path):
    g = sniff.detect_encoding(_write(tmp_path, codecs.BOM_UTF8 + 'id\ncafé\n'.encode()))
    assert (g.encoding, g.bom) == ('utf-8', True)


def test_utf16_le_bom(tmp_path):
    g = sniff.detect_encoding(_write(tmp_path, 'id|note\n1|héllo\n'.encode('utf-16')))
    assert (g.encoding, g.bom) == ('utf-16', True)


def test_utf16_be_bom_says_duckdb_cannot_read_it(tmp_path):
    g = sniff.detect_encoding(_write(tmp_path, codecs.BOM_UTF16_BE + 'id|note\n1|x\n'.encode('utf-16-be')))
    assert (g.encoding, g.bom) == ('utf-16', True)
    assert 'big-endian' in g.why
    assert g.big_endian_utf16


def test_utf16_be_without_bom(tmp_path):
    g = sniff.detect_encoding(_write(tmp_path, (HEADER + _rows(5)).encode('utf-16-be')))
    assert (g.encoding, g.bom, g.big_endian_utf16) == ('utf-16', False, True)


def test_little_endian_is_not_flagged(tmp_path):
    assert not sniff.detect_encoding(_write(tmp_path, 'id\n1\n'.encode('utf-16'))).big_endian_utf16


def test_utf16_without_bom(tmp_path):
    g = sniff.detect_encoding(_write(tmp_path, (HEADER + _rows(5)).encode('utf-16-le')))
    assert (g.encoding, g.bom) == ('utf-16', False)


def test_char_split_across_chunks_is_still_utf8(tmp_path, monkeypatch):
    monkeypatch.setattr(sniff, 'SCAN_BYTES', 64)
    # 'é' is two bytes; 63 ASCII bytes before it put it across every chunk edge
    data = ('a' * 63 + 'é' + 'b' * 63 + '€') * 20
    assert len(data.encode()) > 64 * 20
    assert sniff.detect_encoding(_write(tmp_path, data)).encoding == 'utf-8'


def test_bad_byte_at_the_end_of_a_multi_chunk_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sniff, 'SCAN_BYTES', 64)
    good = (HEADER + 'x,café,1,2\n' * 200).encode()
    g = sniff.detect_encoding(_write(tmp_path, good + b'9,caf\xe9,1,2\n'))
    assert (g.encoding, g.clean) == ('utf-8', False)
    assert f'1 bad byte sequence(s) (first at byte {len(good) + 5:,})' in g.why


def test_truncated_char_at_the_very_end(tmp_path, monkeypatch):
    monkeypatch.setattr(sniff, 'SCAN_BYTES', 64)
    good = ('x,café\n' * 100).encode()
    g = sniff.detect_encoding(_write(tmp_path, good + b'\xc3'))
    assert (g.encoding, g.clean) == ('utf-8', False)
    assert f'first at byte {len(good):,}' in g.why


def test_clean_files_say_so(tmp_path):
    assert sniff.detect_encoding(_write(tmp_path, 'id\ncafé\n')).clean
    assert sniff.detect_encoding(_write(tmp_path, b'id\ncaf\xe9\n')).clean


def test_a_real_replacement_char_is_not_a_bad_byte(tmp_path):
    g = sniff.detect_encoding(_write(tmp_path, ('x,\ufffd café\n' * 20).encode() + b'y,\xff\n'))
    assert (g.encoding, g.clean) == ('utf-8', False)
    assert 'with 1 bad' in g.why


def test_many_bad_bytes_fall_back_to_cp1252(tmp_path):
    data = ('x,café\n' * 5000).encode() + b'y,caf\xe9 \x93q\x94\n' * (sniff.MAX_BAD_UTF8 // 3 + 1)
    g = sniff.detect_encoding(_write(tmp_path, data))
    assert (g.encoding, g.clean) == ('cp1252', True)


def test_few_good_chars_fall_back_to_cp1252(tmp_path):
    # 1 bad among 6 non-ASCII sequences is over the 10% share
    g = sniff.detect_encoding(_write(tmp_path, ('x,café\n' * 5).encode() + b'y,caf\xe9\n'))
    assert (g.encoding, g.clean) == ('latin-1', True)


def test_bad_byte_count_stops_at_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(sniff, 'SCAN_BYTES', 64)
    monkeypatch.setattr(sniff, 'COUNT_BYTES', 1024)
    g = sniff.detect_encoding(_write(tmp_path, ('x,café\n' * 500).encode() + b'y,\xff\n'))
    assert (g.encoding, g.clean) == ('utf-8', False)
    assert 'at least 1 bad' in g.why


def test_cp1252_quotes(tmp_path):
    g = sniff.detect_encoding(_write(tmp_path, b'id|note\n1|\x93hello\x94\n2|caf\xe9\n'))
    assert (g.encoding, g.bom) == ('cp1252', False)


def test_high_latin_only_reads_as_latin1(tmp_path):
    g = sniff.detect_encoding(_write(tmp_path, b'id|city\n1|Z\xfcrich\n2|K\xf6ln\n'))
    assert g.encoding == 'latin-1'


def test_byte_undefined_in_cp1252_falls_to_iso_8859_1(tmp_path):
    g = sniff.detect_encoding(_write(tmp_path, b'id|note\n1|\x93a\x94\n2|\x81\n'))
    assert g.encoding == 'iso-8859-1'


def test_undefined_byte_in_a_later_chunk(tmp_path, monkeypatch):
    monkeypatch.setattr(sniff, 'SCAN_BYTES', 64)
    data = b'id|note\n1|\x93a\x94\n' + b'2|plain\n' * 100 + b'3|\x9d\n'
    assert sniff.detect_encoding(_write(tmp_path, data)).encoding == 'iso-8859-1'


@pytest.fixture(scope='module')
def con():
    return duckdb.connect()


def test_duckdb_rejects_only_the_rows_with_bad_bytes(tmp_path, con):
    text = HEADER + _rows(40).replace('name ', 'nàme ')
    data = text.encode().replace('nàme 7,'.encode(), b'n\xe9me 7,').replace('nàme 30,'.encode(), b'name\xff 30,')
    p = _write(tmp_path, data)
    assert sniff.detect_encoding(p).clean is False
    # select *: DuckDB only checks the encoding of the columns a query reads
    rows = con.execute(
        'select * from read_csv(?, delim = \',\', header = true, all_varchar = true, encoding = \'utf-8\','
        ' ignore_errors = true, store_rejects = true);',
        [str(p)],
    ).fetchall()
    assert sorted(int(r[0]) for r in rows) == [i for i in range(1, 41) if i not in (7, 30)]


@pytest.mark.parametrize('data', [
    b'id|note\n1|\x93hello\x94\n',
    b'id|city\n1|Z\xfcrich\n',
    b'id|note\n1|\x81\x93\n',
])
def test_duckdb_reads_the_guess(tmp_path, con, data):
    p = _write(tmp_path, data)
    enc = sniff.detect_encoding(p).encoding
    rows = con.execute(
        'select * from read_csv(?, delim = ?, header = true, all_varchar = true, encoding = ?);',
        [str(p), '|', enc],
    ).fetchall()
    assert rows[0][1] == data.split(b'\n')[1].split(b'|')[1].decode('latin-1' if enc != 'cp1252' else 'cp1252')


# --------------------------------------------------------------------------- #
# Delimiter                                                                   #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize('delim', [',', '|', ';', '\t'])
def test_each_delimiter_wins_on_its_own_file(tmp_path, delim):
    p = _write(tmp_path, (HEADER + _rows(30)).replace(',', delim))
    best = sniff.score_delimiters(p, 'utf-8')[0]
    assert (best.delimiter, best.field_count, best.coverage) == (delim, 4, 1.0)


def test_pipe_file_with_commas_in_quoted_narratives(tmp_path):
    lines = ['id|note|amount'] + [f'{i}|"said, then, more, words"|{i}' for i in range(30)]
    scores = sniff.score_delimiters(_write(tmp_path, '\n'.join(lines) + '\n'), 'utf-8')
    assert scores[0].delimiter == '|'
    assert scores[0].field_count == 3


def test_no_quote_splits_inside_quotes(tmp_path):
    p = _write(tmp_path, 'a|b\n"x|y"|z\n"x|y"|z\n')
    assert sniff.score_delimiters(p, 'utf-8')[0].field_count == 2
    assert sniff.score_delimiters(p, 'utf-8', quote='')[0].field_count == 3


def test_one_column_file_has_no_delimiter(tmp_path):
    assert sniff.score_delimiters(_write(tmp_path, 'name\nalpha\nbeta\n'), 'utf-8') == []


def test_delimiters_read_only_the_head(tmp_path, monkeypatch):
    monkeypatch.setattr(sniff, 'HEAD_BYTES', 1000)
    p = _write(tmp_path, HEADER + _rows(40) + 'a;b;c;d;e;f\n' * 5000)
    assert sniff.score_delimiters(p, 'utf-8')[0].delimiter == ','


def test_utf16_delimiters(tmp_path):
    p = _write(tmp_path, (HEADER + _rows(10)).replace(',', ';').encode('utf-16'))
    assert sniff.score_delimiters(p, 'utf-16')[0].delimiter == ';'


# --------------------------------------------------------------------------- #
# Layout                                                                      #
# --------------------------------------------------------------------------- #

def _layout(tmp_path, data, **kw):
    lay = sniff.detect_layout(_write(tmp_path, data), kw.pop('encoding', 'utf-8'), kw.pop('delimiter', ','), **kw)
    return lay.first_row, lay.footer_rows


def test_plain_header_and_data(tmp_path):
    assert _layout(tmp_path, HEADER + _rows(50)) == (1, 0)


def test_title_lines_above_the_header(tmp_path):
    data = 'Monthly report\nGenerated 2026-01-01\n\n' + HEADER + _rows(50)
    assert _layout(tmp_path, data) == (4, 0)


def test_no_header(tmp_path):
    assert _layout(tmp_path, _rows(50)) == (0, 0)


def test_numeric_file_without_header(tmp_path):
    data = ''.join(f'{i},{i * 2.5:.1f},{i * 7}\n' for i in range(1, 40))
    assert _layout(tmp_path, data) == (0, 0)


def test_code_columns_keep_a_header_of_the_same_width(tmp_path):
    codes = [('NY', 'US'), ('CA', 'DE'), ('TX', 'FR'), ('ON', 'CA')] * 10
    data = 'ST,CC\n' + ''.join(f'{st},{cc}\n' for st, cc in codes)
    assert _layout(tmp_path, data) == (1, 0)


def test_single_letter_header_over_single_letter_values(tmp_path):
    assert _layout(tmp_path, 'a,b,c\n' + 'x,y,z\n' * 30) == (1, 0)


def test_year_header_over_counts_keeps_the_header(tmp_path):
    rows = ''.join(f'{"East" if i % 2 else "West"},{i * 7},{i * 11},{i * 13}\n' for i in range(1, 40))
    assert _layout(tmp_path, 'Region,2023,2024,2025\n' + rows) == (1, 0)


def test_year_column_without_a_header_is_still_data(tmp_path):
    data = ''.join(f'{2000 + i % 20},{i * 2.5:.1f},{i * 7}\n' for i in range(1, 40))
    assert _layout(tmp_path, data) == (0, 0)


def test_mostly_blank_header_is_still_the_header(tmp_path):
    rows = ''.join(f'{i},v{i},w{i},x{i},y{i},z\n' for i in range(1, 30))
    assert _layout(tmp_path, 'id,,,,,\n' + rows) == (1, 0)


def test_excel_padded_title_lines_are_skipped(tmp_path):
    rows = ''.join(f'{i},n{i},c{i},{i}.50\n' for i in range(1, 40))
    data = 'Monthly Sales Report,,,\nGenerated 2024-05-01,,,\n,,,\nid,name,city,amount\n' + rows
    assert _layout(tmp_path, data) == (4, 0)


def test_padded_line_over_data_is_a_header_not_a_title(tmp_path):
    # no later row reads as a header, so the padded line is the header
    rows = ''.join(f'{i},n{i},c{i},{i}.50\n' for i in range(1, 40))
    assert _layout(tmp_path, 'Sales,,,\n' + rows) == (1, 0)


def test_text_only_columns_keep_the_header(tmp_path):
    data = 'first,last\n' + ''.join(f'Ann{"e" * (i % 4)},Lee{"s" * (i % 3)}\n' for i in range(30))
    assert _layout(tmp_path, data) == (1, 0)


def test_footer_lines(tmp_path):
    data = HEADER + _rows(50) + 'Total rows: 50\n\n--- end of report ---\n'
    assert _layout(tmp_path, data) == (1, 2)


def test_padded_footer_line(tmp_path):
    assert _layout(tmp_path, HEADER + _rows(50) + 'Total rows: 50,,,\n') == (1, 1)


def test_sparse_last_row_is_kept(tmp_path):
    rows = ''.join(f'{i},n{i},c,{i}\n' for i in range(1, 100))
    assert _layout(tmp_path, 'id,name,city,amount\n' + rows + '100,,,\n') == (1, 0)


def test_sparse_last_row_without_a_summary_word_is_kept(tmp_path):
    assert _layout(tmp_path, HEADER + _rows(50) + 'note: see above,,,\n') == (1, 0)


def test_summary_words_mark_a_full_width_footer(tmp_path):
    for line in ('Grand total,,99.00,', 'TOTALS:,,,', 'Records: 50,,,', 'Page 1 of 1,,,'):
        assert _layout(tmp_path, HEADER + _rows(50) + line + '\n') == (1, 1), line


def test_cut_off_last_row_is_kept(tmp_path):
    rows = ''.join(f'{i},n{i},c,{i}\n' for i in range(1, 40))
    assert _layout(tmp_path, 'id,name,city,amount\n' + rows + '40,n40\n') == (1, 0)


def test_cut_off_last_row_in_a_code_column_is_kept(tmp_path):
    rows = ''.join(f'A{i:03d},n{i},c,{i}\n' for i in range(1, 40))
    assert _layout(tmp_path, 'code,name,city,amount\n' + rows + 'A040,n40\n') == (1, 0)


def test_narrow_summary_lines_still_drop(tmp_path):
    for line in ('Total: 123', 'Records: 50', '--- end of report ---'):
        assert _layout(tmp_path, HEADER + _rows(50) + line + '\n') == (1, 1), line


def test_unterminated_quote_does_not_drop_the_last_rows(tmp_path):
    rows = ''.join(f'{i},n{i},c{i}\n' if i != 10 else '10,"open quote,c10\n' for i in range(1, 30))
    assert _layout(tmp_path, 'id,name,city\n' + rows)[1] == 0


def test_sparse_rows_in_the_data_are_not_a_footer(tmp_path):
    data = HEADER + ''.join(f'{i},,,\n' if i % 5 == 0 else f'{i},n,1,2024-01-01\n' for i in range(50))
    assert _layout(tmp_path, data + '7,,,\n') == (1, 0)


def test_quoted_line_breaks_in_data_rows(tmp_path):
    rows = ''.join(f'{i},"two\nlines",1.5,2024-01-02\n' if i % 3 == 0 else f'{i},one,1.5,2024-01-02\n'
                   for i in range(1, 31))
    assert _layout(tmp_path, HEADER + rows + '31,"last\nrow",1.5,2024-01-02\n') == (1, 0)


def test_tail_window_inside_a_quoted_field(tmp_path, monkeypatch):
    # every note ends in a line break, so some windows open on a line that
    # starts with a closing quote, and a parse from there flips the quoting
    monkeypatch.setattr(sniff, 'HEAD_BYTES', 2000)
    rows = ''.join(f'{i},"note {i}, more\n",{i}.5,2024-01-02\n' for i in range(500))
    plain = _write(tmp_path, HEADER + rows, 'plain.csv')
    footed = _write(tmp_path, HEADER + rows + 'Total rows: 500\n', 'footed.csv')
    for tail in range(1000, 1060, 10):
        monkeypatch.setattr(sniff, 'TAIL_BYTES', tail)
        assert sniff.detect_layout(plain, 'utf-8', ',').footer_rows == 0
        assert sniff.detect_layout(footed, 'utf-8', ',').footer_rows == 1


def test_quoted_line_break_in_a_title_line(tmp_path):
    body = '"Monthly\nreport"\n' + HEADER + _rows(30)
    # DuckDB counts quoted records here, and lines when there is a BOM
    assert _layout(tmp_path, body) == (2, 0)
    assert _layout(tmp_path, codecs.BOM_UTF8 + body.encode()) == (3, 0)
    assert _layout(tmp_path, body, quote='') == (3, 0)


@pytest.mark.parametrize('bom', [b'', codecs.BOM_UTF8])
def test_first_row_lands_on_the_header_in_duckdb(tmp_path, con, bom):
    p = _write(tmp_path, bom + ('"Monthly\nreport"\nTitle line\n' + HEADER + _rows(30)).encode())
    first_row = sniff.detect_layout(p, 'utf-8', ',').first_row
    con.execute(
        "select * from read_csv(?, delim = ',', quote = '\"', escape = '\"', header = true, skip = ?, all_varchar = true);",
        [str(p), first_row - 1],
    )
    assert [d[0] for d in con.description] == ['id', 'name', 'amount', 'day']


def test_utf16_layout(tmp_path):
    data = ('Report\n' + HEADER + _rows(30) + 'Total rows: 30\n').encode('utf-16')
    assert _layout(tmp_path, data, encoding='utf-16') == (2, 1)


def test_empty_file_layout(tmp_path):
    assert _layout(tmp_path, b'') == (1, 0)


def test_one_line_files(tmp_path):
    assert _layout(tmp_path, 'id,name\n') == (1, 0)
    assert _layout(tmp_path, '1,2.5,2024-01-01') == (0, 0)
