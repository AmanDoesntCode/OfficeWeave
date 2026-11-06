"""Native close interception and the two explicit credential-retention choices."""
from pathlib import Path
import threading


class CloseController:
    def __init__(self, application):
        self.application = application
        self.window = None
        self.allowed = False
        self.pending = False
        self.busy = False
        self.lock = threading.RLock()

    def on_closing(self):
        with self.lock:
            if self.allowed:
                return True
            self.pending = True
        # Native closing runs on the window thread. JS evaluation must be dispatched
        # separately so WebView2 can process it after this callback vetoes the close.
        threading.Thread(target=self._show, daemon=True, name="mail-merger-close-dialog").start()
        return False

    def _show(self):
        script = (Path(__file__).parent / "assets" / "close-dialog.js").read_text(encoding="utf-8")
        try:
            self.window.evaluate_js(script)
        except Exception:
            pass  # A not-yet-loaded window remains open; the user can try closing again.

    def choose(self, choice):
        if choice not in {"cancel", "sign_out", "stay_signed_in"}:
            raise ValueError("Unknown close action.")
        with self.lock:
            if not self.pending or self.busy:
                return {"ok": False, "error": "No close request is waiting."}
            if choice == "cancel":
                self.pending = False
                return {"ok": True}
            self.busy = True
        try:
            # Wait for in-flight submissions before deleting credentials or closing SQLite.
            self.application.engine.shutdown()
            if self.application.engine.running:
                raise RuntimeError("Sending is still pausing. Wait a moment and try again.")
            self.application.store.save(self.application.campaign)
            if choice == "sign_out":
                if hasattr(self.application, "audit"):
                    self.application.audit.record("sign_out", self.application.auth.accounts().get("gmail", ""))
                self.application.auth.sign_out()
            with self.lock:
                self.allowed = True
            self.window.destroy()
            return {"ok": True}
        except Exception:
            return {"ok": False, "error": "Could not close safely. Wait for sending to pause, then try again."}
        finally:
            with self.lock:
                self.busy = False


class CloseBridge:
    """Expose only the narrow close command to the local desktop frontend."""
    def __init__(self, controller):
        self._controller = controller

    def close_application(self, choice):
        return self._controller.choose(choice)

    def import_otp_setup(self):
        import webview
        from .otp_setup import import_setup
        try:
            paths = self._controller.window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False, file_types=("Private setup (*.txt)",))
            if not paths:
                return {"ok": False, "message": "Setup cancelled."}
            sender = import_setup(paths[0], self._controller.application.store.root)
            return {"ok": True, "message": "OTP sender configured: " + sender}
        except ValueError as exc:
            return {"ok": False, "message": str(exc)}
        except Exception:
            return {"ok": False, "message": "Could not import the private setup TXT."}

    def export_otp_setup(self):
        import webview
        from .otp_setup import export_setup
        from .vault import atomic_write
        data = export_setup(self._controller.application.store.root)
        paths = self._controller.window.create_file_dialog(webview.FileDialog.SAVE, save_filename="Mail-Merger-private-setup.txt")
        if not paths:
            return "Export cancelled."
        atomic_write(Path(paths[0]), data.encode("utf-8"))
        return "Private setup TXT exported. It contains the OTP sender credentials; share it only with authorised users."

    def export_login_audit(self):
        import webview
        from openpyxl import Workbook
        from .audit import HEADERS
        paths = self._controller.window.create_file_dialog(webview.FileDialog.SAVE, save_filename="Mail-Merger-login-audit.xlsx")
        if not paths:
            return "Export cancelled."
        book = Workbook(write_only=True)
        sheet = book.create_sheet("Logins")
        sheet.append(HEADERS)
        fields = ("event_id", "time_utc", "account_email", "event", "result", "app_version", "session_id", "installation_id")
        for event in self._controller.application.store.audit_rows():
            sheet.append([("'" + str(event[key])) if str(event[key]).startswith(("=", "+", "-", "@")) else str(event[key]) for key in fields])
        book.save(paths[0])
        return "Login audit exported."

    def sign_out_and_lock(self):
        if not self._controller.application.store.vault.enabled and not getattr(self._controller.application, "account_scoped", False):
            return {"ok": False}
        self._controller.pending = True
        return self._controller.choose("sign_out")

    def select_attachment_files(self):
        import webview
        paths = self._controller.window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True)
        return list(paths or [])

    def export_recovery_key(self):
        import webview
        from .vault import atomic_write
        data = self._controller.application.store.vault.export_key()
        paths = self._controller.window.create_file_dialog(webview.FileDialog.SAVE, save_filename="Mail-Merger-recovery.json")
        if not paths:
            return "Export cancelled."
        path = Path(paths[0])
        atomic_write(path, data.encode("utf-8"))
        return "Encrypted recovery key exported. Keep its passphrase separately."
