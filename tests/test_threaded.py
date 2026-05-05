"""pywebview-style cross-thread: ingest on one thread, schema on another.

If db.conn() shared a single connection across threads the schema call
would see an empty catalog.  Reproduces the symptom that drove the
cursor-per-call fix in db.py.
"""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path

from file_quacker import ingest
from file_quacker.api import Api


def test_cross_thread_ingest_then_schema():
    p = Path(tempfile.gettempdir()) / 'fq_thread_smoke.csv'
    header = ','.join(f'c{i}' for i in range(15))
    p.write_text(
        header + '\n'
        + ','.join(str(i) for i in range(15)) + '\n',
        encoding='utf-8',
    )

    ingest_result: dict = {}
    ingest_evt = threading.Event()

    def do_ingest():
        ingest_result['r'] = ingest.load_file(str(p))
        ingest_evt.set()

    threading.Thread(target=do_ingest, name='ingest').start()
    ingest_evt.wait(timeout=10)
    r = ingest_result['r']

    schema_result: dict = {}
    schema_evt = threading.Event()

    def do_schema():
        schema_result['s'] = Api().get_table_schema(r.name)
        schema_evt.set()

    threading.Thread(target=do_schema, name='schema').start()
    schema_evt.wait(timeout=10)
    schema = schema_result['s']

    assert len(schema) == 16
