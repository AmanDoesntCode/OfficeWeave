"""Durable, optional sign-in event reporting; never contains mail or credentials."""
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
import threading
from uuid import uuid4

import httpx

HEADERS = ["Event ID", "Time (UTC)", "Account email", "Event", "Result", "App version", "Session ID", "Installation ID"]


def validate_endpoint(url, secret):
    if not re.fullmatch(r"https://script\.google\.com/macros/s/[A-Za-z0-9_-]+/exec", url or ""):
        raise ValueError("Use the deployed Google Apps Script /exec URL for audit logging.")
    if len(secret or "") < 32:
        raise ValueError("The audit secret must contain at least 32 characters.")


class AuditService:
    def __init__(self, store):
        self.store = store
        self.session = uuid4().hex
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.thread = None
        with store.connect() as db:
            row = db.execute("SELECT data FROM settings WHERE key='installation-id'").fetchone()
            self.installation = row[0] if row else uuid4().hex
            if not row:
                db.execute("INSERT INTO settings VALUES ('installation-id',?)", (self.installation,))

    def record(self, event, email="", result="success"):
        payload = {"event_id": uuid4().hex, "time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "account_email": email, "event": event, "result": result, "app_version": "1.0.0",
                   "session_id": self.session, "installation_id": self.installation}
        self.store.add_audit(payload)
        self.wake.set()

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._run, daemon=True, name="mail-merger-login-audit")
        self.thread.start()

    def _run(self):
        while not self.stop.is_set():
            try:
                self.flush()
            except Exception:
                pass  # Keep pending events; never log transport exceptions/secrets.
            self.wake.wait(15)
            self.wake.clear()

    def flush(self, client=None):
        from .auth import ProtectedCredentials
        from .account_storage import installation_root
        config = ProtectedCredentials(installation_root(self.store.root) / "credentials").read("otp") or {}
        if not config.get("audit_url"):
            return
        validate_endpoint(config["audit_url"], config.get("audit_secret"))
        owned = client is None
        client = client or httpx.Client(timeout=5, follow_redirects=True, trust_env=False)
        try:
            for event in self.store.pending_audit(25):
                if self.stop.is_set():
                    break
                payload = json.dumps(event, separators=(",", ":"), ensure_ascii=False)
                signature = hmac.new(config["audit_secret"].encode(), payload.encode(), hashlib.sha256).hexdigest()
                response = client.post(config["audit_url"], json={"payload": payload, "signature": signature})
                response.raise_for_status()
                ack = response.json()
                if not ack.get("ok") or ack.get("event_id") != event["event_id"]:
                    break
                self.store.mark_audit_sent(event["event_id"])
        finally:
            if owned:
                client.close()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join(7)
