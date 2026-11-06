"""Hidden native Gmail -> OTP -> account workspace check. No real mail/network."""
import json
import os
from pathlib import Path
import smtplib
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mail_merger.models import Campaign
from mail_merger.storage import Store
from mail_merger.startup import run_account_desktop


class FakeSMTP:
    def __init__(self, *args, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def login(self, *args): pass


class FakeAuth:
    busy = False
    status = "Connected for simulation."
    def __init__(self): self.email = None
    def accounts(self): return {"gmail": self.email} if self.email else {}
    def connect_gmail(self, email, password):
        assert password == "abcdefghijklmnop"
        self.email = email
    def gmail_credentials(self, email=""): return self.email, "abcdefghijklmnop"
    def sign_out(self): self.email = None


def main():
    smtplib.SMTP_SSL = FakeSMTP
    owner = len(sys.argv) > 1 and sys.argv[1] == "owner"
    email = "owner@example.com" if owner else "other@example.com"
    result, sent = {}, []
    class FakeOTP:
        def __init__(self, root, recipient): self.email = recipient
        def request(self):
            sent.append(self.email)
            return "Code sent to " + self.email
        def verify(self, code):
            if code != "123456": raise ValueError("Incorrect sign-in code.")

    auth = FakeAuth()
    with tempfile.TemporaryDirectory(prefix="mail-merger-signin-otp-") as temporary:
        root = Path(temporary)
        store = Store(root)
        store.save(Campaign(name="Existing owner history"))
        store.enable_encryption("owner@example.com", "abcdefghijklmnop", "codes@example.com", "abcdefghijklmnop")
        store.vault.use_app_password("abcdefghijklmnop")
        store.close()

        def hook(window, bridge):
            bridge._otp_factory = FakeOTP
            def wait_js(expression):
                deadline = time.monotonic() + 40
                while time.monotonic() < deadline:
                    if window.evaluate_js(expression): return
                    time.sleep(.1)
                raise AssertionError(expression)
            def drive():
                try:
                    wait_js("!!window.pywebview?.api?.sign_in_gmail")
                    result["gmail_first"] = window.evaluate_js("!document.getElementById('signin').hidden && document.getElementById('unlock').hidden")
                    window.evaluate_js("document.getElementById('email').value=" + json.dumps(email) + ";document.getElementById('password').value='abcdefghijklmnop';document.getElementById('login').requestSubmit()")
                    wait_js("!document.getElementById('unlock').hidden")
                    result["otp_before_workspace"] = window.evaluate_js("!document.getElementById('mail-merger-shell') && !document.getElementById('verify').hidden")
                    result["no_extra_passphrase"] = window.evaluate_js("!document.getElementById('passphrase') && document.getElementById('upgrade').hidden")
                    assert sent == [email]
                    window.evaluate_js("document.getElementById('code').value='000000';document.getElementById('verify').requestSubmit()")
                    wait_js("document.getElementById('message').textContent.includes('Incorrect')")
                    window.evaluate_js("document.getElementById('code').value='123456';document.getElementById('verify').requestSubmit()")
                    wait_js("document.getElementById('mail-merger-shell')?.dataset.ready === 'true'")
                    title = window.evaluate_js("document.getElementById('sidebar-campaign').textContent")
                    result["correct_history"] = ("Existing owner history" in title) if owner else ("Existing owner history" not in title)
                    window.destroy()
                    wait_js("!!document.getElementById('mail-merger-close')")
                    window.evaluate_js("document.querySelector('[data-choice=sign_out]').click()")
                except Exception as exc:
                    result["error"] = str(exc)
                    print(json.dumps(result), flush=True)
                    os._exit(1)
            threading.Thread(target=drive, daemon=True).start()
        def watchdog():
            time.sleep(90)
            print(json.dumps({"ok": False, "error": "Native sign-in check timed out."}), flush=True)
            os._exit(1)
        threading.Thread(target=watchdog, daemon=True).start()
        run_account_desktop(root, auth=auth, hidden=True, window_hook=hook)
    result["signed_out"] = auth.email is None
    result["ok"] = all(result.get(key) for key in ("gmail_first", "otp_before_workspace", "no_extra_passphrase", "correct_history", "signed_out")) and not result.get("error")
    print(json.dumps(result), flush=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
