import json
import shutil

import pytest

from mail_merger.storage import Store
from mail_merger.vault import Vault, VaultLocked


class NoNetworkSMTP:
    def __init__(self, *args, **kwargs):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def login(self, *args):
        pass


@pytest.fixture
def vault_environment(tmp_path, monkeypatch):
    monkeypatch.setattr("smtplib.SMTP_SSL", NoNetworkSMTP)
    now = [1000.0]
    sent = []
    vault = Vault(tmp_path / "data", mailer=lambda *args: sent.append(args), clock=lambda: now[0])
    vault.enable("owner@example.com", "private passphrase 123", "codes@example.com", "abcdefghijklmnop")
    return vault, sent, now


def test_portable_private_key_and_no_windows_binding(vault_environment, tmp_path):
    vault, sent, now = vault_environment
    assert "ENCRYPTED PRIVATE KEY" in vault.config["private_key"]
    assert "abcdefghijklmnop" not in vault.path.read_text()
    sealed = vault.seal("private body", "campaign:abc")
    other = tmp_path / "another-pc"
    other.mkdir()
    shutil.copy2(vault.path, other / "protection.json")
    restored = Vault(other, mailer=lambda *args: sent.append(args), clock=lambda: now[0])
    with pytest.raises(VaultLocked):
        restored.open(sealed, "campaign:abc")
    restored.request_code("private passphrase 123")
    assert sent[-1][0] == "codes@example.com" and sent[-1][2] == "owner@example.com"
    with pytest.raises(VaultLocked):
        restored.open(sealed, "campaign:abc")
    restored.verify_code(sent[-1][3])
    assert restored.open(sealed, "campaign:abc") == "private body"
    with pytest.raises(ValueError):
        restored.verify_code(sent[-1][3])


def test_codes_expire_throttle_and_lock_after_five_wrong_attempts(vault_environment):
    vault, sent, now = vault_environment
    vault.key = None
    vault.request_code("private passphrase 123")
    with pytest.raises(ValueError, match="60 seconds"):
        vault.request_code("private passphrase 123")
    wrong = "999999" if sent[-1][3] != "999999" else "000000"
    for _ in range(5):
        with pytest.raises(ValueError):
            vault.verify_code(wrong)
    with pytest.raises(ValueError, match="five minutes"):
        vault.request_code("private passphrase 123")
    now[0] += 301
    vault.request_code("private passphrase 123")
    now[0] += 301
    with pytest.raises(ValueError, match="new unlock code"):
        vault.verify_code(sent[-1][3])
    assert vault.locked


def test_wrong_passphrase_cannot_request_mail_or_unlock(vault_environment):
    vault, sent, now = vault_environment
    vault.key = None
    with pytest.raises(ValueError, match="passphrase"):
        vault.request_code("incorrect password")
    assert not sent and vault.locked


def test_recovery_and_rotating_code_sender_preserve_owner(vault_environment, tmp_path):
    vault, sent, now = vault_environment
    exported = tmp_path / "recovery.json"
    exported.write_text(vault.export_key(), encoding="utf-8")
    vault.key = None
    vault.change_mail_account("private passphrase 123", "newcodes@example.com", "ponmlkjihgfedcba")
    vault.request_code("private passphrase 123")
    assert sent[-1][:3] == ("newcodes@example.com", "ponmlkjihgfedcba", "owner@example.com")
    vault.restore_key(exported)
    assert vault.owner == "owner@example.com"
    vault._last_request = -1e20
    vault.request_code("private passphrase 123")
    assert sent[-1][0] == "codes@example.com"


def test_missing_key_file_can_restore_only_the_matching_vault(tmp_path, monkeypatch, campaign):
    monkeypatch.setattr("smtplib.SMTP_SSL", NoNetworkSMTP)
    root = tmp_path / "history"
    store = Store(root)
    store.save(campaign)
    store.enable_encryption("owner@example.com", "private passphrase 123", "codes@example.com", "abcdefghijklmnop")
    exported = tmp_path / "recovery.json"
    exported.write_text(store.vault.export_key(), encoding="utf-8")
    store.close()
    (root / "protection.json").unlink()
    sent = []
    vault = Vault(root, mailer=lambda *args: sent.append(args))
    assert vault.locked and vault.owner == "owner@example.com"
    with pytest.raises(ValueError, match="Restore"):
        vault.request_code("private passphrase 123")
    vault.restore_key(exported)
    vault.request_code("private passphrase 123")
    vault.verify_code(sent[-1][3])
    reopened = Store(root, vault=vault)
    try:
        assert reopened.load(campaign.id).subject == campaign.subject
    finally:
        reopened.close()


def test_ciphertext_is_authenticated_and_bound_to_context(vault_environment):
    from cryptography.exceptions import InvalidTag
    vault, _, _ = vault_environment
    data = vault.seal("private", "campaign:abc")
    with pytest.raises(InvalidTag):
        vault.open(data, "campaign:other")
    changed = data[:-5] + ("A" if data[-5] != "A" else "B") + data[-4:]
    with pytest.raises((InvalidTag, ValueError)):
        vault.open(changed, "campaign:abc")


def test_store_encrypts_json_names_recipients_logs_and_details(tmp_path, monkeypatch, campaign):
    monkeypatch.setattr("smtplib.SMTP_SSL", NoNetworkSMTP)
    store = Store(tmp_path / "history")
    store.save(campaign)
    run = store.create_run(campaign, ["1", "2"])
    store.set_delivery(run, "1", "failed", "private failure detail")
    store.log(campaign.id, "one@example.com", "private log message")
    store.enable_encryption("owner@example.com", "private passphrase 123", "codes@example.com", "abcdefghijklmnop")
    assert store.load(campaign.id).subject == campaign.subject
    assert store.logs(campaign.id)[0][3] == "private log message"
    assert store.delivery(run, "1")["detail"] == "private failure detail"
    assert store.list_campaigns()[0][0].startswith(campaign.name)
    data = store._record_path(campaign.id).read_text()
    assert "encrypted" in data and "one@example.com" not in data and "${Name}" not in data
    store.close()
    raw = (tmp_path / "history" / "mail-merger.sqlite3").read_bytes()
    assert b"private log message" not in raw and b"private failure detail" not in raw
    assert b"one@example.com" not in raw and campaign.name.encode() not in raw
    with pytest.raises(VaultLocked):
        Store(tmp_path / "history")
    sent = []
    vault = Vault(tmp_path / "history", mailer=lambda *args: sent.append(args))
    vault.request_code("private passphrase 123")
    vault.verify_code(sent[-1][3])
    restored = Store(tmp_path / "history", vault=vault)
    try:
        assert restored.load(campaign.id).recipients[0].values["Name"] == "Ada"
        assert restored.counts(run) == {"failed": 1, "pending": 1}
    finally:
        restored.close()


def test_interrupted_encryption_conversion_resumes_after_unlock(tmp_path, monkeypatch, campaign):
    monkeypatch.setattr("smtplib.SMTP_SSL", NoNetworkSMTP)
    root = tmp_path / "history"
    store = Store(root)
    store.save(campaign)
    store.log(campaign.id, "one@example.com", "still needs encryption")
    # Simulate termination after the private key exists, before conversion.
    store.vault.enable("owner@example.com", "private passphrase 123", "codes@example.com", "abcdefghijklmnop")
    assert store.vault.config["migration_pending"]
    store.close()
    sent = []
    vault = Vault(root, mailer=lambda *args: sent.append(args))
    vault.request_code("private passphrase 123")
    vault.verify_code(sent[-1][3])
    restored = Store(root, vault=vault)
    try:
        assert not restored.vault.config["migration_pending"]
        assert "encrypted" in json.loads(restored._record_path(campaign.id).read_text())
        assert restored.logs(campaign.id)[0][3] == "still needs encryption"
        with restored.connect() as db:
            assert db.execute("SELECT recipient,message FROM logs").fetchone()[0].startswith("MM1:")
    finally:
        restored.close()
