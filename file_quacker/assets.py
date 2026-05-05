"""Locate the frontend entry point for pywebview.

In dev mode (`--dev`), pywebview points at the Vite dev server.
In a PyInstaller one-file build, `sys._MEIPASS` is the temp extraction dir;
the frontend bundle lives under `<_MEIPASS>/dist/`.
In an unpacked source checkout, we serve `<repo>/frontend/dist/`.
"""

from __future__ import annotations

import sys
from pathlib import Path

DEV_URL = 'http://localhost:5173'


def frontend_url(dev: bool) -> str:
    if dev:
        return DEV_URL

    index = _bundled_dist_root() / 'index.html'
    if not index.exists():
        raise FileNotFoundError(
            f'Built frontend not found at {index}. '
            f'Run `npm --prefix frontend run build` before launching without --dev.'
        )
    return index.as_uri()


def _bundled_dist_root() -> Path:
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        return Path(meipass) / 'dist'
    # running from source
    return Path(__file__).resolve().parent.parent / 'frontend' / 'dist'
