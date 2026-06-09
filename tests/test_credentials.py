"""Shared, driver-agnostic credential service.

Verifies the secret/metadata split, vault round-trip, the no-password
tier, Windows-auth (no secret), delete, and driver-filtered listing.
Uses an in-memory keyring backend and a temp ``%APPDATA%`` so the tests
need no real OS vault and leave no trace.
"""

from __future__ import annotations

import json

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

from file_quacker import connections, credentials, secret_store, userdata


class MemoryKeyring(KeyringBackend):
    """Throwaway in-process keyring backend for tests."""

    priority = 1

    def __init__(self):
        super().__init__()
        self._store: dict[tuple[str, str], str] = {}

    def set_password(self, service, username, password):
        self._store[(service, username)] = password

    def get_password(self, service, username):
        return self._store.get((service, username))

    def delete_password(self, service, username):
        try:
            del self._store[(service, username)]
        except KeyError:
            raise PasswordDeleteError('not found')


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv('APPDATA', str(tmp_path))
    keyring.set_keyring(MemoryKeyring())
    yield


def test_save_splits_secret_from_metadata():
    opts = {'server': 'db1', 'port': 1433, 'auth': 'sql',
            'username': 'sa', 'password': 's3cret', 'azure': False}
    res = credentials.save('sqlserver', 'Prod SQL', opts)
    assert res['id'] == 'prod_sql'
    assert res['has_secret'] is True

    # The password is never written to connections.json.
    raw = (userdata.data_dir() / 'connections.json').read_text(encoding='utf-8')
    assert 's3cret' not in raw
    stored = json.loads(raw)['profiles'][0]['conn_opts']
    assert 'password' not in stored
    assert stored['server'] == 'db1'

    # resolve() merges the vault secret back in (backend-side only).
    merged = credentials.resolve('prod_sql')
    assert merged['password'] == 's3cret'
    assert merged['server'] == 'db1'


def test_save_without_secret_keeps_metadata_only():
    opts = {'server': 'db1', 'auth': 'sql', 'username': 'sa', 'password': 'x'}
    res = credentials.save('sqlserver', 'No Pw', opts, save_secret=False)
    assert res['has_secret'] is False

    merged = credentials.resolve('no_pw')
    assert merged.get('password') in (None, '')
    assert merged['username'] == 'sa'


def test_windows_auth_stores_no_secret():
    opts = {'server': 'db1', 'auth': 'windows', 'username': '', 'password': ''}
    res = credentials.save('sqlserver', 'Win', opts)
    assert res['has_secret'] is False
    assert secret_store.get_secrets('win') == {}
    assert credentials.resolve('win')['auth'] == 'windows'


def test_overwrite_updates_in_place():
    credentials.save('sqlserver', 'Same', {'server': 'a', 'auth': 'sql',
                                           'username': 'u', 'password': 'p1'})
    credentials.save('sqlserver', 'Same', {'server': 'b', 'auth': 'sql',
                                           'username': 'u', 'password': 'p2'})
    assert len(connections.list_profiles()) == 1
    merged = credentials.resolve('same')
    assert merged['server'] == 'b'
    assert merged['password'] == 'p2'


def test_resave_with_blank_password_preserves_secret():
    # Save with a password, then re-save after a non-secret edit while the
    # password field is blank (UI placeholder) — the secret must survive.
    credentials.save('sqlserver', 'Keep', {'server': 'a', 'auth': 'sql',
                                           'username': 'u', 'password': 'orig'})
    res = credentials.save('sqlserver', 'Keep', {'server': 'a', 'auth': 'sql',
                                                 'username': 'u', 'password': '', 'azure': True})
    assert res['has_secret'] is True
    assert res['secret_lengths'] == {'password': 4}
    merged = credentials.resolve('keep')
    assert merged['password'] == 'orig'      # preserved
    assert merged['azure'] is True           # non-secret change applied


def test_resave_with_new_password_overrides():
    credentials.save('sqlserver', 'Chg', {'server': 'a', 'auth': 'sql',
                                          'username': 'u', 'password': 'old'})
    credentials.save('sqlserver', 'Chg', {'server': 'a', 'auth': 'sql',
                                          'username': 'u', 'password': 'new99'})
    assert credentials.resolve('chg')['password'] == 'new99'


def test_unchecking_save_secret_clears_stored_password():
    credentials.save('sqlserver', 'Clr', {'server': 'a', 'auth': 'sql',
                                          'username': 'u', 'password': 'orig'})
    res = credentials.save('sqlserver', 'Clr', {'server': 'a', 'auth': 'sql',
                                                'username': 'u', 'password': ''}, save_secret=False)
    assert res['has_secret'] is False
    assert res['secret_lengths'] == {}
    assert credentials.resolve('clr').get('password') in (None, '')


def test_delete_removes_metadata_and_secret():
    credentials.save('sqlserver', 'Gone', {'server': 'db1', 'auth': 'sql',
                                           'username': 'sa', 'password': 'p'})
    assert credentials.delete('gone') is True
    assert credentials.resolve('gone') is None
    assert secret_store.get_secrets('gone') == {}
    assert credentials.delete('gone') is False


def test_list_profiles_filtered_by_driver():
    credentials.save('sqlserver', 'A', {'server': 's', 'auth': 'windows'})
    profs = credentials.list_profiles('sqlserver')
    assert [p['name'] for p in profs] == ['A']
    assert profs[0]['has_secret'] is False
    assert credentials.list_profiles('postgres') == []


def test_secret_keys_comes_from_driver_form():
    # SQL Server's password field is kind='password' -> treated as secret.
    assert 'password' in credentials.secret_keys('sqlserver')
    assert 'server' not in credentials.secret_keys('sqlserver')


def test_get_returns_secret_lengths_not_values():
    credentials.save('sqlserver', 'L', {'server': 's', 'auth': 'sql',
                                        'username': 'sa', 'password': 'abcdef'})
    detail = credentials.get('l')
    assert detail['has_secret'] is True
    assert detail['secret_lengths'] == {'password': 6}   # length only, no value
    assert 'password' not in detail['conn_opts']
    # No-secret profile reports empty lengths.
    credentials.save('sqlserver', 'W', {'server': 's', 'auth': 'windows'})
    assert credentials.get('w')['secret_lengths'] == {}


def test_resolve_conn_opts_precedence():
    from file_quacker.api import _resolve_conn_opts

    credentials.save('sqlserver', 'P', {'server': 'stored', 'auth': 'sql',
                                        'username': 'sa', 'password': 'storedpw'})

    # No profile -> conn_opts pass through unchanged.
    sent = {'server': 'typed', 'password': 'typedpw'}
    assert _resolve_conn_opts('sqlserver', sent, None) is sent

    # Profile, blank fields from JS -> stored values + vault secret used,
    # and the password is supplied backend-side (never sent by the UI).
    merged = _resolve_conn_opts('sqlserver', {'server': '', 'password': ''}, 'p')
    assert merged['server'] == 'stored'
    assert merged['password'] == 'storedpw'

    # Profile + a non-empty typed override wins for that field only.
    merged = _resolve_conn_opts('sqlserver', {'server': 'override'}, 'p')
    assert merged['server'] == 'override'
    assert merged['password'] == 'storedpw'

    # Unknown profile id falls through to what was sent.
    assert _resolve_conn_opts('sqlserver', sent, 'nope') is sent
