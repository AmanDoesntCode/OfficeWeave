from unittest.mock import Mock
import pytest
from mail_merger.account_storage import history_root, installation_root
from mail_merger.startup import AccountStartupBridge
from mail_merger.storage import Store
from mail_merger.vault import Vault
from test_vault import NoNetworkSMTP

class StartupAuth:
    busy = False
    status = 'Connected for simulation.'
    def __init__(self, email=None): self.email = email
    def accounts(self): return {'gmail': self.email} if self.email else {}
    def sign_out(self): self.email = None
    def connect_gmail(self, email, password):
        if password != 'abcdefghijklmnop': raise ValueError('Invalid app password.')
        self.email = email.strip()
    def gmail_credentials(self, email):
        if self.email != email: raise ValueError('No saved sign-in.')
        return email, 'abcdefghijklmnop'

class FakeOTP:
    def __init__(self, root, email):
        self.email = email
        self.code = '111111' if email == 'old@example.com' else '222222'
        self.used = False
    def request(self): return 'Code sent to ' + self.email
    def verify(self, code):
        if self.used or code != self.code: raise ValueError('Incorrect sign-in code.')
        self.used = True

def startup(root, auth=None, callback=None):
    return AccountStartupBridge(root, auth or StartupAuth(), callback or Mock(return_value='local-url'), otp_factory=FakeOTP)

@pytest.fixture
def encrypted_legacy(tmp_path, monkeypatch, campaign):
    monkeypatch.setattr('smtplib.SMTP_SSL', NoNetworkSMTP)
    root = tmp_path / 'installation'
    store = Store(root)
    store.save(campaign)
    store.enable_encryption('old@example.com', 'private passphrase 123', 'codes@example.com', 'abcdefghijklmnop')
    store.close()
    return root

def test_other_gmail_requires_otp_without_touching_old_history(encrypted_legacy):
    callback = Mock(return_value='local-url')
    bridge = startup(encrypted_legacy, callback=callback)
    result = bridge.sign_in_gmail('new@example.com', 'abcdefghijklmnop')
    assert result['stage'] == 'otp' and bridge._vault is None
    callback.assert_not_called()
    assert bridge.verify_sign_in_code('222222')['url'] == 'local-url'
    selected, vault, auth = callback.call_args.args
    assert selected.parent == encrypted_legacy / 'accounts'
    assert installation_root(selected) == encrypted_legacy
    assert not vault.enabled and auth.email == 'new@example.com'
    assert Vault(encrypted_legacy).locked

def test_legacy_upgrade_only_after_otp_and_is_needed_once(encrypted_legacy):
    callback = Mock(return_value='local-url')
    bridge = startup(encrypted_legacy, callback=callback)
    assert bridge.sign_in_gmail('old@example.com', 'abcdefghijklmnop')['stage'] == 'otp'
    callback.assert_not_called()
    assert not bridge.upgrade_previous_history('private passphrase 123')['ok']
    assert bridge.verify_sign_in_code('111111')['stage'] == 'upgrade'
    callback.assert_not_called()
    assert bridge.upgrade_previous_history('private passphrase 123')['url'] == 'local-url'
    again = startup(encrypted_legacy)
    assert again.sign_in_gmail('old@example.com', 'abcdefghijklmnop')['stage'] == 'otp'
    assert again.verify_sign_in_code('111111')['url'] == 'local-url'

def test_new_plain_history_also_requires_otp(tmp_path):
    callback = Mock(return_value='local-url')
    bridge = startup(tmp_path, callback=callback)
    assert bridge.sign_in_gmail('new@example.com', 'abcdefghijklmnop')['stage'] == 'otp'
    assert not bridge.verify_sign_in_code('000000')['ok']
    callback.assert_not_called()
    assert bridge.verify_sign_in_code('222222')['url'] == 'local-url'
    assert not bridge.verify_sign_in_code('222222')['ok']

def test_switch_account_discards_previous_otp(encrypted_legacy):
    callback = Mock(return_value='local-url')
    bridge = startup(encrypted_legacy, callback=callback)
    assert bridge.sign_in_gmail('old@example.com', 'abcdefghijklmnop')['stage'] == 'otp'
    assert bridge.back_to_sign_in()['ok']
    assert not bridge.verify_sign_in_code('111111')['ok']
    assert bridge.sign_in_gmail('new@example.com', 'abcdefghijklmnop')['stage'] == 'otp'
    assert not bridge.verify_sign_in_code('111111')['ok']
    callback.assert_not_called()
    assert bridge.verify_sign_in_code('222222')['url'] == 'local-url'

def test_failed_login_never_selects_or_opens_history(encrypted_legacy):
    callback = Mock()
    bridge = startup(encrypted_legacy, callback=callback)
    assert not bridge.sign_in_gmail('old@example.com', 'incorrect')['ok']
    callback.assert_not_called()
    assert bridge._vault is None

def test_saved_account_still_requires_email_otp(encrypted_legacy):
    callback = Mock()
    bridge = startup(encrypted_legacy, StartupAuth('old@example.com'), callback)
    assert bridge._vault is None
    assert bridge.continue_saved_account()['stage'] == 'otp'
    callback.assert_not_called()

def test_profile_selection_is_stable_and_distinct(tmp_path):
    assert history_root(tmp_path, 'PERSON@example.com') == history_root(tmp_path, 'person@example.com')
    assert history_root(tmp_path, 'person@example.com') != history_root(tmp_path, 'other@example.com')

def test_unencrypted_legacy_history_matches_recorded_sender_only(tmp_path, campaign):
    store = Store(tmp_path)
    store.save(campaign)
    store.close()
    assert history_root(tmp_path, campaign.sender) == tmp_path.resolve()
    assert history_root(tmp_path, 'other@example.com').parent == tmp_path.resolve() / 'accounts'

def test_shared_otp_settings_are_resolved_from_installation(tmp_path, monkeypatch):
    from conftest import MemoryVault
    memory = MemoryVault()
    memory.write('otp', {'email': 'codes@example.com', 'app_password': 'abcdefghijklmnop'})
    paths = []
    monkeypatch.setattr('mail_merger.auth.ProtectedCredentials', lambda path: (paths.append(path), memory)[1])
    selected = history_root(tmp_path, 'other@example.com')
    assert Vault(selected).default_mail_sender() == 'codes@example.com'
    assert paths == [tmp_path.resolve() / 'credentials']

def test_fresh_workspace_preserves_legacy_and_is_reused_next_time(encrypted_legacy):
    bridge = startup(encrypted_legacy)
    bridge.sign_in_gmail('old@example.com', 'abcdefghijklmnop')
    assert bridge.verify_sign_in_code('111111')['stage'] == 'upgrade'
    assert bridge.start_new_workspace()['url'] == 'local-url'
    selected = bridge._history_root
    store = Store(selected)
    store.close()
    assert history_root(encrypted_legacy, 'old@example.com') == selected
    assert Vault(encrypted_legacy).locked
