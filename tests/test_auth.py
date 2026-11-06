import json
import os

import pytest

from mail_merger.auth import AuthService, ProtectedCredentials
from conftest import MemoryVault


def test_credentials_not_saved_as_plaintext(tmp_path):
    if os.name != 'nt':
        pytest.skip('Windows DPAPI integration')
    vault = ProtectedCredentials(tmp_path)
    vault.write('gmail', {'email': 'sender@example.com', 'method': 'app_password', 'app_password': 'abcdefghijklmnop'})
    encrypted = (tmp_path / 'gmail.dpapi').read_bytes()
    assert b'abcdefghijklmnop' not in encrypted
    assert vault.read('gmail')['app_password'] == 'abcdefghijklmnop'
    vault.delete('gmail')
    assert vault.read('gmail') is None


def test_configuration_rejects_web_client_and_foreign_endpoints(tmp_path):
    auth = AuthService(tmp_path, MemoryVault())
    with pytest.raises(ValueError, match='Desktop'):
        auth.save_configuration('{"web":{"client_id":"id"}}', '')
    with pytest.raises(ValueError):
        auth.save_configuration(json.dumps({'installed': {'client_id': 'id', 'client_secret': 'secret',
                                                          'token_uri': 'https://evil.example/token'}}), '')
    assert not list(auth.config_directory.iterdir())


def test_signout_clears_all_token_roles(tmp_path):
    vault = MemoryVault()
    for key in ['gmail', 'outlook', 'sheets']:
        vault.write(key, {'email': 'sender@example.com', 'method': 'app_password'})
    auth = AuthService(tmp_path, vault)
    assert set(auth.accounts()) == {'gmail', 'sheets'}
    auth.sign_out()
    assert auth.accounts() == {}


def test_missing_configuration_is_actionable(tmp_path):
    auth = AuthService(tmp_path, MemoryVault())
    with pytest.raises(ValueError, match='Only Gmail'):
        auth.begin('outlook')
    assert 'app password' in auth.begin('gmail')
    assert not auth.busy
