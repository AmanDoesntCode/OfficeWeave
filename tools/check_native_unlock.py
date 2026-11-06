"""Hidden Windows startup check using an encrypted dummy history and fake mail."""
import json
import os
from pathlib import Path
import smtplib
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mail_merger.models import Campaign, Recipient
from mail_merger.storage import Store
from mail_merger.unlock import run_locked_desktop
from mail_merger.vault import Vault


class FakeSMTP:
    def __init__(self, *args, **kwargs):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def login(self, *args):
        pass


def main():
    smtplib.SMTP_SSL = FakeSMTP
    result = {}
    sent = []
    artifacts = Path(__file__).resolve().parents[1] / "test-results"
    artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="native-unlock-", dir=artifacts) as temp:
        root = Path(temp)
        store = Store(root)
        c = Campaign(name="Encrypted native check", headers=["Email", "Name"], email_column="Email")
        c.recipients = [Recipient("1", {"Email": "dummy@example.com", "Name": "Native"})]
        store.save(c)
        store.enable_encryption("dummy@example.com", "dummy private passphrase", "codes@example.com", "abcdefghijklmnop")
        store.close()
        vault = Vault(root, mailer=lambda *args: sent.append(args))

        def hook(window):
            def wait_js(expression):
                deadline = time.monotonic() + 40
                while time.monotonic() < deadline:
                    if window.evaluate_js(expression):
                        return
                    time.sleep(.1)
                raise AssertionError(expression)

            def drive():
                try:
                    wait_js("!!window.pywebview?.api?.request_unlock_code")
                    result["locked_screen"] = window.evaluate_js("document.querySelector('h1').textContent === 'Unlock your history'")
                    result["no_history_before_unlock"] = window.evaluate_js("!document.getElementById('mail-merger-shell')")
                    window.evaluate_js("document.getElementById('passphrase').value='dummy private passphrase';document.getElementById('request').requestSubmit()")
                    wait_js("!document.getElementById('verify').hidden")
                    assert sent and sent[-1][2] == "dummy@example.com"
                    window.evaluate_js("document.getElementById('code').value=" + json.dumps(sent[-1][3]) + ";document.getElementById('verify').requestSubmit()")
                    wait_js("document.getElementById('mail-merger-shell')?.dataset.ready === 'true'")
                    result["history_opened"] = window.evaluate_js("document.getElementById('sidebar-campaign').textContent.includes('Encrypted native check')")
                    window.destroy()
                    wait_js("!!document.getElementById('mail-merger-close')")
                    window.evaluate_js("document.querySelector('[data-choice=stay_signed_in]').click()")
                except Exception as exc:
                    result["error"] = str(exc)
                    try:
                        result["page"] = window.evaluate_js("({url:location.href,message:document.getElementById('message')?.textContent,heading:document.querySelector('h1')?.textContent,body:document.body.innerText.slice(0,1800)})")
                    except Exception:
                        pass
                    print(json.dumps(result), flush=True)
                    os._exit(1)
            threading.Thread(target=drive, daemon=True).start()

        def watchdog():
            time.sleep(90)
            print(json.dumps({"ok": False, "error": "Native unlock check timed out."}), flush=True)
            os._exit(1)
        threading.Thread(target=watchdog, daemon=True).start()
        run_locked_desktop(root, vault, hidden=True, window_hook=hook)
    result["ok"] = all(result.get(key) for key in ("locked_screen", "no_history_before_unlock", "history_opened")) and not result.get("error")
    print(json.dumps(result), flush=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
