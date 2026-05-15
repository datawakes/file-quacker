"""Shared column-inspection progress state.

Export, DDL, and Derive all do per-column analysis on wide tables and
each uses these helpers so the frontend can poll one endpoint and show
the same counter.  The polling thread reads while a worker writes, so
state access and inspection serialization need separate locks.
"""

from __future__ import annotations

from contextlib import contextmanager
from threading import Lock
from typing import Iterator

_serial_lock = Lock()
_state_lock = Lock()
_progress: dict = {
    'phase': 'idle',
    'current': 0,
    'total': 0,
    'error': None,
}


def reset(total: int = 0) -> None:
    with _state_lock:
        _progress.update({
            'phase': 'idle',
            'current': 0,
            'total': total,
            'error': None,
        })


def set_progress(**kw) -> None:
    with _state_lock:
        _progress.update(kw)


def get_progress() -> dict:
    with _state_lock:
        return dict(_progress)


@contextmanager
def session(total: int) -> Iterator[None]:
    """Run one column-inspection session.  Serializes against other
    callers; the terminal phase ('done' or 'error') is left in place so
    a late-resolving poll can read it before the next session resets."""
    with _serial_lock:
        reset(total=total)
        set_progress(phase='inspecting')
        try:
            yield
            set_progress(phase='done')
        except Exception as ex:
            set_progress(phase='error', error=str(ex))
            raise
