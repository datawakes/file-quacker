"""Shared, driver-agnostic credential service.

Splits a connection form into non-secret fields (saved as profile
metadata via ``connections``) and secret fields (saved in the OS vault
via ``secret_store``), using the driver's own ``FieldSpec`` list as the
source of truth for what counts as a secret.

Every pluggable driver gets save / load / secure-storage for free; adding
a new driver is purely declaring its connection form.  Nothing here is
SQL-Server-specific.
"""

from __future__ import annotations

from . import connections, secret_store


def secret_keys(driver_id: str) -> set[str]:
    """Field keys the driver considers secret: any ``kind == 'password'``
    field plus any explicitly flagged ``secret=True``."""
    from . import drivers

    form = drivers.driver_for(driver_id).connection_form
    return {f.key for f in form if f.kind == 'password' or getattr(f, 'secret', False)}


def _split(driver_id: str, conn_opts: dict) -> tuple[dict, dict]:
    """Partition conn_opts into (non-secret, secret).  Empty secret values
    are dropped so e.g. Windows auth stores nothing in the vault."""
    keys = secret_keys(driver_id)
    public: dict = {}
    secrets: dict = {}
    for k, v in conn_opts.items():
        if k in keys:
            if v not in (None, ''):
                secrets[k] = v
        else:
            public[k] = v
    return public, secrets


def save(driver_id: str, name: str, conn_opts: dict, *, save_secret: bool = True) -> dict:
    """Persist a profile.  With ``save_secret=False`` the non-secret
    metadata is saved but no secret is written (tier: "profile, no
    password").  Returns a UI-safe summary (never includes the secret).

    A secret field left blank means "keep what's stored", not "erase it":
    the UI shows a masked placeholder and sends an empty value when the
    user didn't retype the password, so re-saving after a non-secret edit
    must not wipe the saved secret.
    """
    public, secrets = _split(driver_id, conn_opts)
    pid = connections.slugify(name)
    if save_secret:
        existing = secret_store.get_secrets(pid)
        for k in secret_keys(driver_id):
            if k not in secrets and existing.get(k):
                secrets[k] = existing[k]
    rec = connections.upsert_profile(name, driver_id, public)
    if save_secret:
        secret_store.set_secrets(rec['id'], secrets)
    else:
        secret_store.delete_secrets(rec['id'])
        secrets = {}
    return {
        'id': rec['id'],
        'name': rec['name'],
        'driver_id': driver_id,
        'has_secret': bool(secrets),
        'secret_lengths': {k: len(v) for k, v in secrets.items()},
    }


def get(profile_id: str) -> dict | None:
    """Profile for populating the form; the secret stays in the vault and is
    resolved backend-side at use, never returned here."""
    rec = connections.get_profile(profile_id)
    if rec is None:
        return None
    secrets = secret_store.get_secrets(profile_id)
    return {
        'id': rec['id'],
        'name': rec['name'],
        'driver_id': rec['driver_id'],
        'conn_opts': rec.get('conn_opts', {}),
        'has_secret': bool(secrets),
        # Char count per secret field (NOT the value) so the UI can render
        # masked placeholder dots of the right length.
        'secret_lengths': {k: len(v) for k, v in secrets.items()},
    }


def resolve(profile_id: str) -> dict | None:
    """Full conn_opts for a saved profile, secrets merged back in.  Used
    backend-side only; the secret never returns to the frontend."""
    rec = connections.get_profile(profile_id)
    if rec is None:
        return None
    merged = dict(rec.get('conn_opts', {}))
    merged.update(secret_store.get_secrets(profile_id))
    return merged


def delete(profile_id: str) -> bool:
    """Remove a profile and its vault secret."""
    secret_store.delete_secrets(profile_id)
    return connections.delete_profile(profile_id)


def list_profiles(driver_id: str | None = None) -> list[dict]:
    """UI-safe profile list (no secrets); ``has_secret`` flags whether a
    password is on file so the frontend can show the saved affordance."""
    out: list[dict] = []
    for rec in connections.list_profiles(driver_id):
        out.append({
            'id': rec['id'],
            'name': rec['name'],
            'driver_id': rec['driver_id'],
            'has_secret': bool(secret_store.get_secrets(rec['id'])),
        })
    return out
