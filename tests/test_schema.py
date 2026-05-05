"""get_table_schema returns the right column count for a CSV load."""

from __future__ import annotations

import tempfile
from pathlib import Path

from file_quacker import ingest
from file_quacker.api import Api


def test_schema_has_src_row_num_plus_user_cols():
    p = Path(tempfile.gettempdir()) / 'fq_schema_smoke.csv'
    header = ','.join(f'c{i}' for i in range(15))
    p.write_text(
        header + '\n'
        + ','.join(str(i) for i in range(15)) + '\n'
        + ','.join(f'v{i}' for i in range(15)) + '\n'
        + ','.join('' for _ in range(15)) + '\n',
        encoding='utf-8',
    )
    r = ingest.load_file(str(p))

    api = Api()
    schema = api.get_table_schema(r.name)
    rows = api.get_rows(r.name, 0, 10)

    # 15 user cols + _src_row_num = 16
    assert len(schema) == 16
    assert len(rows['rows'][0]) == 16
