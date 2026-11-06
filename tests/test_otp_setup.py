from pathlib import Path
from unittest.mock import Mock

import pytest

from conftest import MemoryVault
from mail_merger.auth import AuthService
from mail_merger.otp_setup import export_setup, import_setup, parse_setup
from mail_merger.vault import Vault
from test_vault import NoNetworkSMTP


def test_setup_supports_two_lines_and_named_fields():
    for text in ("sender@example.com\nabcd efgh ijkl mnop", "email=sender@example.com\napp_password=abcd efgh ijkl mnop"):
        assert parse_setup(text) == {"email": "sender@example.com", "app_password": "abcdefghijklmnop"}


@pytest.mark.parametrize("text", ["not valid", "email=a@example.com\napp_password=short", "email=a@example.com\nemail=b@example.com\napp_password=abcdefghijklmnop"])
def test_bad_setup_does_not_return_secret_details(text):
    with pytest.raises(ValueError) as exc:
        parse_setup(text)
    assert "abcdefghijklmnop" not in str(exc.value)


def test_import_verifies_without_copying_txt_or_sending_mail(tmp_path, monkeypatch):
    vault = MemoryVault()
    monkeypatch.setattr("mail_merger.otp_setup.ProtectedCredentials", lambda _: vault)
    monkeypatch.setattr("smtplib.SMTP_SSL", NoNetworkSMTP)
    path = tmp_path / "private.txt"
    path.write_text("email=sender@example.com\napp_password=abcdefghijklmnop")
    root = tmp_path / "history"
    assert import_setup(path, root) == "sender@example.com"
    assert vault.read("otp")["app_password"] == "abcdefghijklmnop"
    assert not root.exists()
    assert export_setup(root).startswith("email=sender@example.com\n")


def test_signout_keeps_dedicated_otp_sender(tmp_path):
    vault = MemoryVault()
    vault.write("otp", {"email": "codes@example.com", "app_password": "abcdefghijklmnop"})
    vault.write("gmail", {"email": "user@example.com", "method": "app_password", "app_password": "ponmlkjihgfedcba"})
    AuthService(tmp_path, vault).sign_out()
    assert vault.read("otp") and not vault.read("gmail")


def test_default_otp_account_enables_vault_without_password_in_ui(tmp_path, monkeypatch):
    vault = Vault(tmp_path)
    monkeypatch.setattr(vault, "default_mail_account", lambda: {"sender": "codes@example.com", "password": "abcdefghijklmnop"})
    monkeypatch.setattr("smtplib.SMTP_SSL", NoNetworkSMTP)
    vault.enable("owner@example.com", "private passphrase 123", "codes@example.com", "")
    assert vault.owner == "owner@example.com"
    assert "abcdefghijklmnop" not in vault.path.read_text()


def test_first_run_prompt_stops_after_successful_import(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from mail_merger.otp_setup import attach_setup_prompt
    class Event:
        def __init__(self):
            self.handler = None
        def __iadd__(self, handler):
            self.handler = handler
            return self
    available = [False]
    monkeypatch.setattr(Vault, "default_mail_sender", lambda _: "codes@example.com" if available[0] else "")
    window = SimpleNamespace(events=SimpleNamespace(loaded=Event()), evaluate_js=Mock())
    attach_setup_prompt(window, tmp_path)
    window.events.loaded.handler()
    assert window.evaluate_js.call_count == 1
    available[0] = True
    window.events.loaded.handler()
    assert window.evaluate_js.call_count == 1
