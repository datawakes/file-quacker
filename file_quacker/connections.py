"""Saved DB connection profiles (non-secret metadata only).

A profile is ``{id, name, driver_id, conn_opts}`` where ``conn_opts``
holds only the non-secret connection fields; secrets live in the OS vault
(see ``secret_store``).  Driver-agnostic by design: SQL Server today,
Postgres / MySQL / etc. later with no change here.

Persisted to ``connections.json`` in the per-user data dir.
"""

from __future__ import annotations

import re
from typing import Any

from . import userdata

_FILE = 'connections.json'


def _path():
    return userdata.data_dir() / _FILE


def _load() -> list[dict]:
    data = userdata.read_json(_path(), {'profiles': []})
    profiles = data.get('profiles') if isinstance(data, dict) else None
    return profiles if isinstance(profiles, list) else []


def _save(profiles: list[dict]) -> None:
    userdata.write_json(_path(), {'profiles': profiles})


def slugify(name: str) -> str:
    """Stable id from a display name; also the keyring account key."""
    s = re.sub(r'[^a-z0-9]+', '_', name.strip().lower()).strip('_')
    return s or 'profile'


def list_profiles(driver_id: str | None = None) -> list[dict]:
    out = _load()
    if driver_id is not None:
        out = [p for p in out if p.get('driver_id') == driver_id]
    return out


def get_profile(profile_id: str) -> dict | None:
    return next((p for p in _load() if p.get('id') == profile_id), None)


def upsert_profile(name: str, driver_id: str, conn_opts: dict[str, Any]) -> dict:
    """Insert or replace the profile keyed by ``slugify(name)``."""
    pid = slugify(name)
    rec = {'id': pid, 'name': name, 'driver_id': driver_id, 'conn_opts': conn_opts}
    profiles = _load()
    for i, p in enumerate(profiles):
        if p.get('id') == pid:
            profiles[i] = rec
            break
    else:
        profiles.append(rec)
    _save(profiles)
    return rec


def delete_profile(profile_id: str) -> bool:
    """Drop the profile; return False if it didn't exist."""
    profiles = _load()
    kept = [p for p in profiles if p.get('id') != profile_id]
    if len(kept) == len(profiles):
        return False
    _save(kept)
    return True
