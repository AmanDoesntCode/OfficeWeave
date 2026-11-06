"""Exercise the real WebView2 close event with fake credentials and no mail/network."""
import json
import os
from pathlib import Path
import sys
import time
import threading
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import webview
from mail_merger.closing import CloseBridge, CloseController


def main():
    choice = sys.argv[1] if len(sys.argv) > 1 else "stay_signed_in"
    app = SimpleNamespace(engine=Mock(running=False), store=Mock(), auth=Mock(), campaign=object())
    controller = CloseController(app)
    window = webview.create_window("Mail-Merger close check", html="<h1>Mail-Merger</h1>",
                                   hidden=True, js_api=CloseBridge(controller))
    controller.window = window
    window.events.closing += controller.on_closing
    result = {"choice": choice}

    def wait_js(expression):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if window.evaluate_js(expression):
                return
            time.sleep(.1)
        raise AssertionError(expression)

    def check():
        try:
            print("Waiting for native bridge", flush=True)
            wait_js("!!window.pywebview?.api?.close_application")
            print("Requesting native close", flush=True)
            window.destroy()  # Must be vetoed by the native FormClosing handler.
            wait_js("!!document.getElementById('mail-merger-close')")
            print("Overlay visible", flush=True)
            window.evaluate_js("document.querySelector('[data-choice=cancel]').click()")
            wait_js("!document.getElementById('mail-merger-close')")
            result["cancel_kept_window"] = not app.engine.shutdown.called
            window.destroy()
            wait_js("!!document.getElementById('mail-merger-close')")
            window.evaluate_js(f"document.querySelector('[data-choice={choice}]').click()")
        except Exception as exc:
            result["error"] = str(exc)
            controller.allowed = True
            window.destroy()

    def watchdog():
        time.sleep(45)
        print(json.dumps({"ok": False, "error": "Native close check timed out."}), flush=True)
        os._exit(1)
    threading.Thread(target=watchdog, daemon=True).start()
    webview.start(check, gui="edgechromium", private_mode=True, debug=False)
    result["paused"] = app.engine.shutdown.called
    result["saved"] = app.store.save.called
    result["signed_out"] = app.auth.sign_out.called
    result["ok"] = (not result.get("error") and result.get("cancel_kept_window")
                    and result["paused"] and result["saved"]
                    and result["signed_out"] == (choice == "sign_out"))
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
