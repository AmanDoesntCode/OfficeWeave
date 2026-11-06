from __future__ import annotations

import json
import os
import smtplib
import ssl
import threading
from pathlib import Path

import httpx

GOOGLE_IDENTITY = ["openid", "https://www.googleapis.com/auth/userinfo.email"]
GOOGLE_SHEETS = GOOGLE_IDENTITY + ["https://www.googleapis.com/auth/spreadsheets.readonly"]


class AuthenticationRequired(ValueError):
    pass


class ProtectedCredentials:
    """Credential bytes are only persisted with current-user Windows DPAPI protection."""

    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def _path(self, key):
        if key not in {"gmail", "sheets", "outlook", "otp"}:
            raise ValueError("Unknown credential key.")
        return self.directory / (key + ".dpapi")

    def read(self, key: str) -> dict | None:
        with self.lock:
            path = self._path(key)
            if not path.exists():
                return None
            if os.name != "nt":
                raise AuthenticationRequired("Saved sign-in is supported on Windows only.")
            import win32crypt
            try:
                raw = win32crypt.CryptUnprotectData(path.read_bytes(), None, None, None, 1)[1]
                return json.loads(raw)
            except Exception as exc:
                raise AuthenticationRequired("Saved credentials could not be unlocked. Sign in again.") from exc

    def write(self, key: str, value: dict):
        if os.name != "nt":
            raise AuthenticationRequired("Windows DPAPI is required to save credentials.")
        import win32crypt
        raw = json.dumps(value).encode()
        encrypted = win32crypt.CryptProtectData(raw, "Mail-Merger", None, None, None, 1)
        with self.lock:
            target = self._path(key)
            temporary = target.with_suffix(".tmp")
            temporary.write_bytes(encrypted)
            temporary.replace(target)

    def delete(self, key: str):
        with self.lock:
            self._path(key).unlink(missing_ok=True)


class AuthService:
    def __init__(self, root: Path, credentials=None):
        self.root = root
        self.config_directory = root / "config"
        self.config_directory.mkdir(parents=True, exist_ok=True)
        self.vault = credentials or ProtectedCredentials(root / "credentials")
        self.lock = threading.RLock()
        self.busy = False
        self.pending_provider = None
        self.status = "Sign in to Gmail."
        self.generation = 0

    def _sender_accounts(self) -> dict[str, str]:
        for key in ("gmail",):
            try:
                data = self.vault.read(key)
                if data and (key != "gmail" or data.get("method") == "app_password"):
                    return {key: data.get("email", "Connected")}
            except AuthenticationRequired:
                pass
        return {}

    def accounts(self) -> dict[str, str]:
        with self.lock:
            result = self._sender_accounts()
            try:
                data = self.vault.read("sheets")
                if data:
                    result["sheets"] = data.get("email", "Connected")
            except AuthenticationRequired:
                pass
        return result

    def save_configuration(self, google_json: str, microsoft_client_id: str):
        with self.lock:
            if self.busy:
                raise ValueError("Wait for the current sign-in before changing configuration.")
            if google_json.strip():
                try:
                    config = json.loads(google_json)
                    installed = config["installed"]
                    if not installed.get("client_id") or not installed.get("client_secret"):
                        raise ValueError()
                    if installed.get("auth_uri", "https://accounts.google.com/o/oauth2/auth") not in {
                        "https://accounts.google.com/o/oauth2/auth", "https://accounts.google.com/o/oauth2/v2/auth"
                    } or installed.get("token_uri", "https://oauth2.googleapis.com/token") != "https://oauth2.googleapis.com/token":
                        raise ValueError()
                except (ValueError, KeyError, TypeError) as exc:
                    raise ValueError("Paste a Google Desktop app OAuth JSON with an installed section.") from exc
                (self.config_directory / "google-client.json").write_text(json.dumps(config), encoding="utf-8")
                self.vault.delete("sheets")
            if microsoft_client_id.strip():
                raise ValueError("Microsoft accounts are no longer supported.")

    def begin(self, key: str) -> str:
        if key not in {"gmail", "sheets"}:
            raise ValueError("Only Gmail sign-in is supported.")
        with self.lock:
            if key == "gmail" and self._sender_accounts():
                return "Sign out of your current account first."
        if key == "gmail":
            return "Enter your Gmail address and app password."
        config_name = "google-client.json"
        if not (self.config_directory / config_name).is_file():
            return "Add your credentials in Accounts → OAuth settings."
        with self.lock:
            if key == "gmail" and self._sender_accounts():
                return "Sign out of your current account first."
            if self.busy:
                return "A sign-in is already open in your browser."
            self.busy = True
            self.pending_provider = key
            generation = self.generation
            self.status = "Complete sign-in in your system browser."
        def login():
            try:
                data = self._google_login(key)
                with self.lock:
                    if generation == self.generation:
                        self.vault.write(key, data)
                        self.status = f"Connected {data['email']} ({key})."
            except Exception:
                # Provider exceptions can contain authorization codes or token response bodies.
                self.status = "Sign-in did not finish. Check local OAuth configuration and try again."
            finally:
                self.busy = False
                self.pending_provider = None
        threading.Thread(target=login, daemon=True, name="mail-merger-sign-in").start()
        return self.status

    def connect_gmail(self, email: str, app_password: str) -> str:
        from .templates import address
        email = address(email)
        password = "".join(app_password.split())
        if len(password) != 16 or not password.isascii() or not password.isalnum():
            raise ValueError("Enter your 16-character Google app password.")
        with self.lock:
            if self._sender_accounts():
                raise AuthenticationRequired("Sign out of your current account first.")
            if self.busy:
                raise ValueError("Another sign-in is in progress.")
            self.busy = True
            self.pending_provider = "gmail"
            generation = self.generation
            self.status = "Connecting to Gmail…"
        try:
            # Authentication only: no message is sent during connection setup.
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=25) as smtp:
                smtp.login(email, password)
            with self.lock:
                if generation != self.generation:
                    raise AuthenticationRequired("Sign-in was cancelled.")
                self.vault.delete("outlook")
                self.vault.write("gmail", {"email": email, "method": "app_password", "app_password": password})
                self.status = f"Gmail connected: {email}"
            return self.status
        except smtplib.SMTPAuthenticationError as exc:
            self.status = "Google rejected the email or app password."
            raise AuthenticationRequired(self.status) from exc
        except (smtplib.SMTPException, OSError) as exc:
            self.status = "Could not connect to Gmail. Check your connection and try again."
            raise AuthenticationRequired(self.status) from exc
        finally:
            self.busy = False
            self.pending_provider = None

    def gmail_credentials(self, expected_email: str = "") -> tuple[str, str]:
        with self.lock:
            data = self.vault.read("gmail")
            if not data or data.get("method") != "app_password" or not data.get("app_password"):
                raise AuthenticationRequired("Connect Gmail using your email and app password.")
            if expected_email and data["email"].casefold() != expected_email.casefold():
                raise AuthenticationRequired("Connect the Gmail account selected for this campaign.")
            return data["email"], data["app_password"]

    def validate_sender(self, provider: str, expected_email: str = ""):
        if provider == "gmail":
            self.gmail_credentials(expected_email)
        else:
            self.token(provider, expected_email)

    def _google_login(self, key: str) -> dict:
        from google_auth_oauthlib.flow import InstalledAppFlow
        path = self.config_directory / "google-client.json"
        if not path.exists():
            raise AuthenticationRequired("Configure a Google desktop OAuth client first.")
        flow = InstalledAppFlow.from_client_secrets_file(str(path), scopes=GOOGLE_SHEETS, autogenerate_code_verifier=True)
        credentials = flow.run_local_server(host="127.0.0.1", port=0, timeout_seconds=180,
                                           authorization_prompt_message="", success_message="Mail-Merger sign-in complete. You can close this tab.",
                                           access_type="offline", prompt="consent")
        response = httpx.get("https://openidconnect.googleapis.com/v1/userinfo",
                             headers={"Authorization": f"Bearer {credentials.token}"}, timeout=20)
        response.raise_for_status()
        user = response.json()
        if not user.get("email_verified"):
            raise AuthenticationRequired("A verified Google email address is required.")
        return {"email": user["email"], "credentials": json.loads(credentials.to_json())}

    def token(self, key: str, expected_email: str = "") -> str:
        if key not in {"gmail", "sheets"}:
            raise AuthenticationRequired("Microsoft accounts are no longer supported. Use Gmail.")
        if key == "gmail":
            raise AuthenticationRequired("Gmail uses an app password, not an OAuth token.")
        with self.lock:
            data = self.vault.read(key)
            if not data or (expected_email and data["email"].casefold() != expected_email.casefold()):
                raise AuthenticationRequired("Sign in with the campaign's sender account before sending.")
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            credentials = Credentials.from_authorized_user_info(data["credentials"])
            if not credentials.valid:
                if not credentials.refresh_token:
                    raise AuthenticationRequired("Google sign-in expired. Sign in again.")
                try:
                    credentials.refresh(Request())
                except Exception as exc:
                    raise AuthenticationRequired("Google sign-in expired. Sign in again.") from exc
                data["credentials"] = json.loads(credentials.to_json())
                self.vault.write(key, data)
            return credentials.token

    def sign_out(self):
        with self.lock:
            self.generation += 1
            for key in ("gmail", "outlook", "sheets"):
                self.vault.delete(key)
            self.status = "Signed out. Local campaigns remain on this computer."
