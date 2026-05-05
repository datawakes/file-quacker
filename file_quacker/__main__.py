"""File Quacker entry point.

Usage:
    python -m file_quacker             # production: loads bundled frontend
    python -m file_quacker --dev       # dev: points at the Vite dev server
    python -m file_quacker --trace     # add timing details to DDL output
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import webview

# Absolute imports; when PyInstaller bundles this as the entry script it is
# executed as `__main__`, not as `file_quacker.__main__`, so relative imports
# (`.api`, `.assets`) fail with "attempted relative import with no known
# parent package".
from file_quacker import instrument
from file_quacker.api import Api
from file_quacker.assets import frontend_url


def _icon_path() -> str | None:
    """Locate ``file_quacker.ico``.  In a PyInstaller one-file build the
    icon is extracted into ``sys._MEIPASS``; from source we look one level
    above the package.  Returns None if the file isn't found.
    """
    meipass = getattr(sys, '_MEIPASS', None)
    candidate = (Path(meipass) if meipass else Path(__file__).resolve().parent.parent) / 'file_quacker.ico'
    return str(candidate) if candidate.exists() else None


def _tint_titlebar(title: str) -> None:
    """Tint the Win32 title bar to match the in-app yellow status bar
    (#FBF100 fill, #1A1A1A ink).  Uses DWM (Windows 11 22000+); silently
    no-ops on other platforms or older Windows."""
    if sys.platform != 'win32':
        return
    try:
        import ctypes
        from ctypes import wintypes

        hwnd = ctypes.windll.user32.FindWindowW(None, title)
        if not hwnd:
            return

        DWMWA_CAPTION_COLOR = 35
        DWMWA_TEXT_COLOR    = 36
        # COLORREF is 0x00BBGGRR; low byte holds R.
        caption = ctypes.c_int(0x00F1FB)   # #FBF100 yellow
        text    = ctypes.c_int(0x1A1A1A)   # near-black ink

        for attr, val in ((DWMWA_CAPTION_COLOR, caption), (DWMWA_TEXT_COLOR, text)):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd), wintypes.DWORD(attr),
                ctypes.byref(val), ctypes.sizeof(val),
            )
    except Exception:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(prog='file_quacker')
    parser.add_argument('--dev', action='store_true',
                        help='Load Vite dev server instead of bundled frontend')
    parser.add_argument('--trace', action='store_true',
                        help='Enable timing telemetry; appends a breakdown to DDL output. '
                             'Independent of --dev so you can profile against the bundled frontend.')
    args = parser.parse_args()

    # Trace mode appends DDL/derive timing details to generated DDL output.
    instrument.enabled = args.trace

    title = 'File Quacker'
    window = webview.create_window(
        title=title,
        url=frontend_url(dev=args.dev),
        js_api=Api(),
        width=1400,
        height=900,
        min_size=(900, 600),
    )
    window.events.shown += lambda: _tint_titlebar(title)
    icon = _icon_path()
    if icon:
        webview.start(debug=args.dev, icon=icon)
    else:
        webview.start(debug=args.dev)


if __name__ == '__main__':
    main()
