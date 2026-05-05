"""Optional timing telemetry for DDL/type detection.

When enabled, timed probes are collected and appended to the generated DDL
output as comments. When disabled, probes short-circuit on a flag check.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Iterator

enabled: bool = False

_lock = threading.Lock()
_buffer: list[tuple[str, dict, float]] = []


@contextmanager
def timed(label: str, **fields) -> Iterator[None]:
    if not enabled:
        yield
        return
    t0 = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        with _lock:
            _buffer.append((label, fields, elapsed_ms))


def take() -> list[tuple[str, dict, float]]:
    """Drain and return buffered timing results."""
    with _lock:
        out = list(_buffer)
        _buffer.clear()
    return out


def format_summary(events: list[tuple[str, dict, float]]) -> str:
    """Render timing results as SQL comments, slowest first."""
    if not events:
        return ''
    label_w = max(len(label) for label, _, _ in events)
    field_strs = [
        ' '.join(f'{k}={v}' for k, v in f.items()) for _, f, _ in events
    ]
    field_w = max((len(s) for s in field_strs), default=0)

    by_elapsed = sorted(
        zip(events, field_strs), key=lambda x: x[0][2], reverse=True,
    )
    total = sum(ms for _, _, ms in events)
    lines = [
        '',
        '-- --- timing breakdown (--dev) -----------------------------',
        f'-- {"label".ljust(label_w)}  {"context".ljust(field_w)}  elapsed_ms',
    ]
    for (label, _, ms), fs in by_elapsed:
        lines.append(f'-- {label.ljust(label_w)}  {fs.ljust(field_w)}  {ms:>10.2f}')
    lines.append(f'-- {"TOTAL".ljust(label_w)}  {"".ljust(field_w)}  {total:>10.2f}')
    return '\n'.join(lines)
