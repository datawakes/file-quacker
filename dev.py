"""Dev orchestrator.

Starts the Vite dev server as a child process, waits until it responds on
port 5173, then launches the pywebview shell pointed at it. Ctrl+C (or
closing the pywebview window) tears both down.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / 'frontend'
VITE_PORT = 5173
VITE_TIMEOUT_S = 30

NPM = 'npm.cmd' if sys.platform == 'win32' else 'npm'


def wait_for_port(port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.25)
    return False


def main() -> int:
    if not (FRONTEND / 'node_modules').exists():
        print('[dev] frontend/node_modules missing. Run `npm --prefix frontend install` first.', file=sys.stderr)
        return 1

    print(f'[dev] starting Vite on :{VITE_PORT}...')
    vite = subprocess.Popen([NPM, 'run', 'dev'], cwd=FRONTEND)
    try:
        if not wait_for_port(VITE_PORT, VITE_TIMEOUT_S):
            print(f'[dev] Vite did not come up on :{VITE_PORT} within {VITE_TIMEOUT_S}s', file=sys.stderr)
            return 2
        print('[dev] Vite up. Launching pywebview shell...')
        subprocess.run([sys.executable, '-m', 'file_quacker', '--dev'], cwd=ROOT)
        return 0
    finally:
        vite.terminate()
        try:
            vite.wait(timeout=5)
        except subprocess.TimeoutExpired:
            vite.kill()


if __name__ == '__main__':
    raise SystemExit(main())
