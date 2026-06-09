"""OS-vault secret storage via keyring (driver-agnostic).

Stores a per-profile JSON blob of ``{field_key: value}`` under a single
keyring entry, so a profile with zero, one, or several secret fields all
work the same way (keyring holds one string per (service, account)).

On Windows, keyring's backend discovery walks importlib entry points,
which PyInstaller's static analysis misses -> a frozen build can come up
with the no-op 'fail' backend and silently lose secrets.  We pin
``WinVaultKeyring`` explicitly so the bundled exe behaves like the source
build.  (Tests override this via ``keyring.set_keyring(...)``.)
"""

from __future__ import annotations

import json
import sys

import keyring

# Constant service name; the keyring "username" slot carries the profile id.
SERVICE = 'FileQuacker'

# Pin the platform backend so the frozen exe doesn't fall back to a no-op.
# If the backend module isn't importable we leave keyring's auto-selected
# backend in place and let callers handle any runtime failure.
try:  # pragma: no cover - platform/packaging specific
    if sys.platform == 'win32':
        from keyring.backends.Windows import WinVaultKeyring

        keyring.set_keyring(WinVaultKeyring())
except ImportError:
    pass


def set_secrets(profile_id: str, secrets: dict[str, str]) -> None:
    """Persist ``secrets`` for ``profile_id``.  An empty dict clears any
    previously stored secret rather than writing an empty blob."""
    if not secrets:
        delete_secrets(profile_id)
        return
    keyring.set_password(SERVICE, profile_id, json.dumps(secrets))


def get_secrets(profile_id: str) -> dict[str, str]:
    """Return the stored secret dict for ``profile_id`` (``{}`` if none)."""
    raw = keyring.get_password(SERVICE, profile_id)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def delete_secrets(profile_id: str) -> None:
    """Remove the stored secret for ``profile_id``; a no-op if absent."""
    try:
        keyring.delete_password(SERVICE, profile_id)
    except keyring.errors.PasswordDeleteError:
        pass
