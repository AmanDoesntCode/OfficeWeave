"""Authenticate Gmail first, then open only that account's history."""
from pathlib import Path
import threading

from .account_storage import history_root
from .auth import AuthService
from .unlock import UnlockBridge
from .vault import Vault
from .login_otp import LoginOTP


class AccountStartupBridge(UnlockBridge):
    def __init__(self, root, auth, open_history, *, otp_factory=LoginOTP):
        super().__init__(None, self._open_selected_history)
        self._root = Path(root)
        self._auth = auth
        self._open_history = open_history
        self._history_root = None
        self._email = None
        self._opened = False
        self._verified = False
        self._otp = None
        self._otp_factory = otp_factory
        self._sign_in_lock = threading.Lock()

    def _open_selected_history(self):
        if not self._verified or not self._email or not self._vault:
            raise ValueError("Sign in with Gmail before opening history.")
        if self._vault.locked:
            raise ValueError("Unlock this account's history first.")
        if self._opened:
            raise ValueError("History is already open. Sign out before switching accounts.")
        url = self._open_history(self._history_root, self._vault, self._auth)
        self._opened = True
        return url

    def _begin_verification(self, email):
        self._email = email
        self._verified = False
        self._otp = self._otp_factory(self._root, email)
        message = self._otp.request()
        return {"ok": True, "stage": "otp", "owner": email, "message": message}

    def _select_history(self):
        if not self._verified:
            raise ValueError("Verify the sign-in code before opening history.")
        self._history_root = history_root(self._root, self._email)
        self._vault = Vault(self._history_root)
        if self._vault.enabled and self._vault.owner.casefold() != self._email.casefold():
            self._vault = None
            raise ValueError("This account's history folder has a different owner. Choose another history location.")
        if self._vault.locked:
            try:
                self._vault.use_app_password(self._auth.gmail_credentials(self._email)[1])
            except ValueError:
                return {"ok": True, "stage": "upgrade", "owner": self._email,
                        "message": "Sign-in verified. This older history needs a one-time upgrade; future sign-ins only need your app password and OTP."}
        return {"ok": True, "url": self._open_selected_history(), "message": "Sign-in complete."}

    def verify_sign_in_code(self, code):
        try:
            if self._opened or not self._otp or not self._email:
                raise ValueError("Sign in with Gmail first.")
            self._otp.verify(code)
            self._verified = True
            return self._select_history()
        except ValueError as exc:
            return {"ok": False, "message": str(exc)}
        except Exception:
            return {"ok": False, "message": "Could not open your workspace. Please try again."}

    def resend_sign_in_code(self):
        try:
            if self._opened or self._verified or not self._otp:
                raise ValueError("Sign in with Gmail first.")
            return {"ok": True, "message": self._otp.request()}
        except ValueError as exc:
            return {"ok": False, "message": str(exc)}

    def upgrade_previous_history(self, previous_password):
        try:
            if not self._verified or not self._vault or self._opened:
                raise ValueError("Verify your sign-in first.")
            self._vault.use_app_password(self._auth.gmail_credentials(self._email)[1], previous_password)
            return {"ok": True, "url": self._open_selected_history(), "message": "History upgraded. No separate history password will be needed next time."}
        except ValueError as exc:
            return {"ok": False, "message": str(exc)}
        except Exception:
            return {"ok": False, "message": "Could not upgrade the previous history."}

    def start_new_workspace(self):
        try:
            if not self._verified or self._opened:
                raise ValueError("Verify your sign-in first.")
            import hashlib
            profile = self._root / "accounts" / hashlib.sha256(self._email.casefold().encode()).hexdigest()
            if (profile / "mail-merger.sqlite3").exists() or Vault(profile).enabled:
                raise ValueError("This account already has a workspace. Upgrade its existing history to open it.")
            self._history_root, self._vault = profile, Vault(profile)
            return {"ok": True, "url": self._open_selected_history(), "message": "New workspace opened. Previous encrypted files were preserved."}
        except ValueError as exc:
            return {"ok": False, "message": str(exc)}

    def sign_in_gmail(self, email, password):
        if not self._sign_in_lock.acquire(blocking=False):
            return {"ok": False, "message": "Sign-in is already in progress."}
        try:
            if self._opened:
                raise ValueError("Sign out before switching accounts.")
            self._email, self._vault, self._otp, self._verified = None, None, None, False
            if self._auth.accounts():
                self._auth.sign_out()
            self._auth.connect_gmail(email, password)
            return self._begin_verification(self._auth.accounts()["gmail"])
        except ValueError as exc:
            return {"ok": False, "message": str(exc)}
        except Exception:
            return {"ok": False, "message": "Could not sign in. Check your email, app password, and connection."}
        finally:
            self._sign_in_lock.release()

    def continue_saved_account(self):
        if not self._sign_in_lock.acquire(blocking=False):
            return {"ok": False, "message": "Sign-in is already in progress."}
        try:
            if self._opened:
                raise ValueError("History is already open.")
            email = self._auth.accounts().get("gmail")
            if not email:
                raise ValueError("Enter your Gmail address and app password.")
            self._auth.gmail_credentials(email)
            return self._begin_verification(email)
        except ValueError as exc:
            return {"ok": False, "message": str(exc)}
        except Exception:
            return {"ok": False, "message": "Saved sign-in is unavailable. Sign in again."}
        finally:
            self._sign_in_lock.release()

    def import_otp_setup(self):
        import webview
        from .otp_setup import import_setup
        try:
            paths = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False, file_types=("Private setup (*.txt)",))
            if not paths:
                return {"ok": False, "message": "Setup cancelled."}
            sender = import_setup(paths[0], self._root)
            return {"ok": True, "message": "OTP sender configured: " + sender}
        except ValueError as exc:
            return {"ok": False, "message": str(exc)}
        except Exception:
            return {"ok": False, "message": "Could not import the private setup TXT."}

    def back_to_sign_in(self):
        if self._opened:
            return {"ok": False, "message": "Sign out before switching accounts."}
        self._email, self._vault, self._history_root, self._otp, self._verified = None, None, None, None, False
        self._auth.sign_out()
        return {"ok": True}

    def request_unlock_code(self, passphrase):
        return {"ok": False, "message": "Use the sign-in email code. No separate passphrase is required."}

    def unlock_history(self, code):
        return self.verify_sign_in_code(code)


def run_account_desktop(root, *, auth=None, hidden=False, window_hook=None):
    import html
    import webview
    from .closing import CloseBridge, CloseController
    from .controller import Application
    from .desktop import LocalServer
    from .storage import Store
    from .otp_setup import attach_setup_prompt

    auth = auth or AuthService(root)
    context = {}

    def open_history(selected_root, vault, selected_auth):
        application = Application(Store(selected_root, vault=vault), selected_auth)
        application.account_scoped = True
        server = LocalServer(application)
        context["server"] = server
        url = server.start()
        controller = CloseController(application)
        controller.window = window
        native = CloseBridge(controller)
        window.expose(native.close_application, native.sign_out_and_lock, native.select_attachment_files,
                      native.export_recovery_key, native.import_otp_setup, native.export_otp_setup, native.export_login_audit)
        window.events.closing += controller.on_closing
        application.audit.record("sign_in", selected_auth.accounts().get("gmail", ""))
        if vault.enabled:
            application.audit.record("history_unlock", vault.owner)
        return url

    bridge = AccountStartupBridge(root, auth, open_history)
    document = (Path(__file__).parent / "assets" / "account-startup.html").read_text("utf-8")
    saved = auth.accounts().get("gmail", "")
    document = document.replace("SAVED_ACCOUNT", html.escape(saved))
    window = webview.create_window("Mail-Merger", html=document, width=1240, height=900,
                                  min_size=(860, 650), background_color="#f5f7fb", js_api=bridge, hidden=hidden)
    bridge._window = window
    if not hidden:
        attach_setup_prompt(window, root)
    if window_hook:
        window_hook(window, bridge)
    try:
        webview.start(gui="edgechromium", private_mode=True, debug=False)
    finally:
        if context.get("server"):
            context["server"].close()


def browser_account_session(root):
    """Development mode follows the same Gmail -> mandatory OTP order."""
    import getpass
    auth = AuthService(root)
    saved = auth.accounts().get("gmail", "")
    email = input("Gmail address" + (" [" + saved + "]" if saved else "") + ": ").strip() or saved
    if not saved or email.casefold() != saved.casefold():
        auth.sign_out()
        auth.connect_gmail(email, getpass.getpass("Google app password: "))
    email, password = auth.gmail_credentials(email)
    otp = LoginOTP(Path(root), email)
    print(otp.request())
    otp.verify(getpass.getpass("Email sign-in code: "))
    selected = history_root(root, email)
    vault = Vault(selected)
    if vault.locked:
        try:
            vault.use_app_password(password)
        except ValueError:
            print("This older history needs a one-time key upgrade.")
            vault.use_app_password(password, getpass.getpass("Previous history password: "))
    return selected, vault, auth
