"""Per-user application data directory + atomic JSON helpers.

All writable state lives under the user's profile, never beside the
(shared, possibly read-only) exe.  On Windows this resolves to
``%APPDATA%\\FileQuacker\\`` (Roaming) so a user's config follows them
across RDS / terminal-server session hosts; keyring secrets roam the same
way (``CRED_PERSIST_ENTERPRISE``), so the two stay consistent.

Writes are atomic (temp file + ``os.replace``) because the same user may
have two app instances open in different sessions.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

APP_DIR_NAME = 'FileQuacker'


def data_dir() -> Path:
    """Resolve (and create) the per-user data directory.

    ``%APPDATA%\\FileQuacker`` on Windows; ``~/.file_quacker`` as a
    cross-platform / service-context fallback when ``APPDATA`` is unset.
    """
    appdata = os.environ.get('APPDATA')
    d = Path(appdata) / APP_DIR_NAME if appdata else Path.home() / '.file_quacker'
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_json(path: Path, default: Any) -> Any:
    """Return parsed JSON, or ``default`` if the file is missing or corrupt —
    a half-written config shouldn't take down the app."""
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, OSError):
        return default


def write_json(path: Path, data: Any) -> None:
    """Atomically write ``data`` as pretty JSON to ``path``.

    Writes to a sibling temp file, fsyncs, then ``os.replace`` (atomic on
    the same filesystem) so a concurrent reader never sees a partial
    file.  Raises ``OSError`` if the profile location is unwritable; the
    caller surfaces that as a friendly message.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
