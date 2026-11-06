import hashlib
import hmac
import json

import httpx
import pytest

from mail_merger.audit import AuditService, validate_endpoint
from conftest import MemoryVault


def test_audit_is_local_when_not_configured(application, monkeypatch):
    vault = MemoryVault()
    monkeypatch.setattr("mail_merger.auth.ProtectedCredentials", lambda _: vault)
    application.audit.record("sign_in", "owner@example.com")
    application.audit.flush()
    event = application.store.pending_audit()[0]
    assert event["account_email"] == "owner@example.com"
    assert "time_utc" in event and "session_id" in event
    assert not {"password", "otp", "body", "recipients"} & event.keys()


def test_signed_upload_retains_same_id_until_acknowledged(application, monkeypatch):
    vault = MemoryVault()
    config = {"audit_url": "https://script.google.com/macros/s/example/exec", "audit_secret": "s" * 40}
    vault.write("otp", config)
    monkeypatch.setattr("mail_merger.auth.ProtectedCredentials", lambda _: vault)
    application.audit.record("sign_in", "owner@example.com")
    requests = []
    acknowledge = [False]
    def receiver(request):
        data = json.loads(request.content)
        expected = hmac.new(config["audit_secret"].encode(), data["payload"].encode(), hashlib.sha256).hexdigest()
        assert hmac.compare_digest(data["signature"], expected)
        event = json.loads(data["payload"])
        requests.append(event["event_id"])
        return httpx.Response(200, json={"ok": acknowledge[0], "event_id": event["event_id"]})
    with httpx.Client(transport=httpx.MockTransport(receiver)) as client:
        application.audit.flush(client)
        assert application.store.pending_audit()
        acknowledge[0] = True
        application.audit.flush(client)
    assert len(requests) == 2 and requests[0] == requests[1]
    assert not application.store.pending_audit()


@pytest.mark.parametrize("url", ["http://script.google.com/macros/s/id/exec", "https://example.com/exec", "https://script.google.com.evil.test/macros/s/id/exec"])
def test_audit_rejects_untrusted_endpoint(url):
    with pytest.raises(ValueError):
        validate_endpoint(url, "s" * 40)
