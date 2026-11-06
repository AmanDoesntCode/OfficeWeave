"""The native unlock screen starts before any campaign or Gradio server is opened."""
from pathlib import Path


class UnlockBridge:
    def __init__(self, vault, on_unlocked):
        self._vault = vault
        self._on_unlocked = on_unlocked
        self._busy = False
        self._window = None

    def import_otp_setup(self):
        import webview
        from .otp_setup import import_setup
        try:
            paths = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False, file_types=("Private setup (*.txt)",))
            if not paths:
                return {"ok": False, "message": "Setup cancelled."}
            sender = import_setup(paths[0], self._vault.path.parent)
            return {"ok": True, "message": "OTP sender configured: " + sender}
        except ValueError as exc:
            return {"ok": False, "message": str(exc)}
        except Exception:
            return {"ok": False, "message": "Could not import the private setup TXT."}

    def restore_recovery_key(self):
        import webview
        try:
            paths = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False, file_types=("Recovery key (*.json)",))
            return {"ok": True, "message": self._vault.restore_key(paths[0])} if paths else {"ok": False, "message": "Restore cancelled."}
        except ValueError as exc:
            return {"ok": False, "message": str(exc)}
        except Exception:
            return {"ok": False, "message": "Could not restore this recovery key."}

    def change_code_account(self, passphrase, email, password):
        try:
            return {"ok": True, "message": self._vault.change_mail_account(passphrase, email, password)}
        except ValueError as exc:
            return {"ok": False, "message": str(exc)}
        except Exception:
            return {"ok": False, "message": "Could not update the code-sending account."}

    def request_unlock_code(self, passphrase):
        try:
            return {"ok": True, "message": self._vault.request_code(passphrase)}
        except ValueError as exc:
            return {"ok": False, "message": str(exc)}
        except Exception:
            return {"ok": False, "message": "Could not request an unlock code."}

    def unlock_history(self, code):
        if self._busy:
            return {"ok": False, "message": "Opening history..."}
        self._busy = True
        try:
            self._vault.verify_code(code)
            url = self._on_unlocked()
            return {"ok": True, "message": "History unlocked.", "url": url}
        except ValueError as exc:
            return {"ok": False, "message": str(exc)}
        except Exception:
            return {"ok": False, "message": "Could not open history. Close the app and try again."}
        finally:
            self._busy = False


def run_locked_desktop(root, vault, *, hidden=False, window_hook=None):
    import html
    import webview
    from .closing import CloseBridge, CloseController
    from .controller import Application
    from .desktop import LocalServer
    from .storage import Store

    context = {}

    def start_application():
        if context.get("server"):
            return context["url"]
        application = Application(Store(root, vault=vault))
        server = LocalServer(application)
        context["server"] = server
        url = server.start()
        context["url"] = url
        controller = CloseController(application)
        controller.window = window
        window.expose(CloseBridge(controller).close_application,
                      CloseBridge(controller).sign_out_and_lock,
                      CloseBridge(controller).select_attachment_files,
                      CloseBridge(controller).export_recovery_key)
        window.expose(CloseBridge(controller).import_otp_setup, CloseBridge(controller).export_otp_setup,
                      CloseBridge(controller).export_login_audit)
        application.audit.record("history_unlock", vault.owner)
        window.events.closing += controller.on_closing
        # The JS caller navigates only after its bridge promise has resolved.
        # Navigating inside the bridge would discard its pending return callback.
        return url

    bridge = UnlockBridge(vault, start_application)
    document = (Path(__file__).parent / "assets" / "unlock.html").read_text("utf-8")
    document = document.replace("OWNER_EMAIL", html.escape(vault.owner))
    window = webview.create_window("Mail-Merger", html=document, width=1240, height=900,
                                  min_size=(860, 650), background_color="#f5f7fb", js_api=bridge, hidden=hidden)
    bridge._window = window
    if not hidden:
        from .otp_setup import attach_setup_prompt
        attach_setup_prompt(window, root)
    if window_hook:
        window_hook(window)
    try:
        webview.start(gui="edgechromium", private_mode=True, debug=False)
    finally:
        if context.get("server"):
            context["server"].close()
