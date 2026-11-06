"""Portable, account-owned encryption; email codes gate opening the local vault.

The passphrase protects the private key, and the private key unwraps the random
AES data key. OTPs are transient access checks, never encryption keys.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class VaultLocked(ValueError):
    pass


def atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    # OneDrive/antivirus can briefly hold a just-written file on Windows.
    for attempt in range(6):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(.05 * (2 ** attempt))


def b64(value):
    return base64.b64encode(value).decode("ascii")


class Vault:
    def default_mail_account(self):
        """Installation-local OTP credentials, separate from the sending account."""
        from .auth import AuthenticationRequired, ProtectedCredentials
        from .account_storage import installation_root
        try:
            data = ProtectedCredentials(installation_root(self.path.parent) / "credentials").read("otp")
            if data and data.get("email") and data.get("app_password"):
                return {"sender": data["email"], "password": data["app_password"]}
        except AuthenticationRequired:
            pass
        return None

    def default_mail_sender(self):
        account = self.default_mail_account()
        return account["sender"] if account else ""

    def __init__(self, root, mailer=None, clock=time.monotonic):
        self.path = Path(root) / "protection.json"
        try:
            self.config = json.loads(self.path.read_text("utf-8")) if self.path.exists() else None
        except (ValueError, OSError):
            self.config = None
        if self.config is None:
            # The encrypted record envelope retains only the vault identity so a
            # missing protection file can be recovered before reading any history.
            for path in (Path(root) / "campaigns").glob("*.json"):
                try:
                    envelope = json.loads(path.read_text("utf-8"))
                    if "encrypted" in envelope:
                        self.config = {"version": 1, "id": envelope.get("vault_id"), "owner": envelope.get("owner", "Encrypted account")}
                        break
                except (ValueError, OSError):
                    continue
        self.key = None
        self._challenge = None
        self._last_request = -1e20
        self._failures = 0
        self._blocked_until = 0
        self.lock = threading.RLock()
        self.clock = clock
        self.mailer = mailer or self._send_code

    @property
    def enabled(self):
        return self.config is not None

    @property
    def locked(self):
        return self.enabled and self.key is None

    @property
    def owner(self):
        return self.config["owner"] if self.enabled else ""

    @staticmethod
    def _padding():
        return padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None)

    @staticmethod
    def _encrypt(key, value, context):
        nonce = os.urandom(12)
        return "MM1:" + b64(nonce + AESGCM(key).encrypt(nonce, value.encode("utf-8"), context.encode("utf-8")))

    @staticmethod
    def _decrypt(key, value, context):
        raw = base64.b64decode(value[4:], validate=True)
        return AESGCM(key).decrypt(raw[:12], raw[12:], context.encode("utf-8")).decode("utf-8")

    def seal(self, value, context):
        if self.locked:
            raise VaultLocked("Unlock encrypted history first.")
        return self._encrypt(self.key, value, context) if self.enabled else value

    def open(self, value, context):
        if self.locked:
            raise VaultLocked("Unlock encrypted history first.")
        return self._decrypt(self.key, value, context) if value.startswith("MM1:") else value

    def enable(self, owner, passphrase, sender, app_password):
        from .templates import address
        if self.enabled:
            raise ValueError("Encryption is already enabled.")
        default = self.default_mail_account()
        if default and not app_password.strip() and (not sender.strip() or sender.strip().casefold() == default["sender"].casefold()):
            sender, app_password = default["sender"], default["password"]
        owner, sender = address(owner), address(sender)
        if len(passphrase) < 12:
            raise ValueError("Use a private-key passphrase of at least 12 characters.")
        password = "".join(app_password.split())
        if len(password) != 16 or not password.isascii() or not password.isalnum():
            raise ValueError("Enter the code-sending Gmail account's 16-character app password.")
        # Verify authentication before creating a vault. This sends no email.
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=25) as smtp:
                smtp.login(sender, password)
        except (smtplib.SMTPException, OSError):
            raise ValueError("Could not verify the code-sending Gmail account. Check its app password and your connection.") from None
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        key = AESGCM.generate_key(bit_length=256)
        config = {"version": 1, "owner": owner, "id": secrets.token_hex(16), "migration_pending": True,
                  "private_key": private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                       serialization.BestAvailableEncryption(passphrase.encode())).decode(),
                  "wrapped_key": b64(private.public_key().encrypt(key, self._padding())),
                  "mail": self._encrypt(key, json.dumps({"sender": sender, "password": password}), "otp-mail")}
        atomic_write(self.path, json.dumps(config).encode("utf-8"))
        self.config, self.key = config, key

    def request_code(self, passphrase):
        with self.lock:
            if not self.config or "private_key" not in self.config:
                raise ValueError("Restore your encrypted recovery key before requesting a code.")
            now = self.clock()
            if now < self._blocked_until:
                raise ValueError("Too many attempts. Wait five minutes before trying again.")
            if now - self._last_request < 60:
                raise ValueError("Wait 60 seconds before requesting another code.")
            self._last_request = now
            self._challenge = None
            try:
                private = serialization.load_pem_private_key(self.config["private_key"].encode(), passphrase.encode())
                key = private.decrypt(base64.b64decode(self.config["wrapped_key"]), self._padding())
                mail = json.loads(self._decrypt(key, self.config["mail"], "otp-mail"))
                mail = self.default_mail_account() or mail
            except Exception:
                self._failures += 1
                if self._failures >= 5:
                    self._blocked_until = now + 300
                raise ValueError("The private-key passphrase could not unlock this account.") from None
            code = f"{secrets.randbelow(1_000_000):06d}"
            salt = os.urandom(32)
            try:
                self.mailer(mail["sender"], mail["password"], self.owner, code)
            except Exception:
                raise ValueError("Could not email the code. Check the code-sending account and your connection.") from None
            self._challenge = (salt, hashlib.sha256(salt + code.encode()).digest(), now + 300, key)
            return "Code sent. It expires in five minutes."

    def use_app_password(self, app_password, previous_password=None):
        """Unlock after login OTP and remove the separate history passphrase."""
        if not self.enabled:
            return
        try:
            private = serialization.load_pem_private_key(self.config["private_key"].encode(),
                                                         (previous_password or app_password).encode())
            key = private.decrypt(base64.b64decode(self.config["wrapped_key"]), self._padding())
        except Exception:
            raise ValueError("The previous history password is needed once to upgrade this encrypted history.") from None
        with self.lock:
            config = dict(self.config)
            config["private_key"] = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                          serialization.BestAvailableEncryption(app_password.encode())).decode()
            config["key_method"] = "gmail_app_password"
            atomic_write(self.path, json.dumps(config).encode("utf-8"))
            self.config, self.key = config, key

    def verify_code(self, code):
        with self.lock:
            now = self.clock()
            if now < self._blocked_until or not self._challenge or now > self._challenge[2]:
                raise ValueError("Request a new unlock code.")
            salt, expected, _, key = self._challenge
            if not hmac.compare_digest(hashlib.sha256(salt + str(code).strip().encode()).digest(), expected):
                self._failures += 1
                if self._failures >= 5:
                    self._challenge = None
                    self._blocked_until = now + 300
                raise ValueError("Incorrect code.")
            self.key, self._challenge, self._failures = key, None, 0

    def export_key(self):
        if not self.enabled or self.locked:
            raise VaultLocked("Unlock history before exporting its encrypted recovery key.")
        # A passphrase-encrypted private key; no decrypted key or SMTP password.
        return json.dumps(self.config, indent=2)

    def restore_key(self, source):
        if not self.locked:
            raise ValueError("Recovery keys can only be restored while history is locked.")
        path = Path(source)
        if path.stat().st_size > 64_000:
            raise ValueError("Invalid recovery key file.")
        try:
            candidate = json.loads(path.read_text("utf-8"))
            if (candidate["version"] != 1 or (self.config["id"] and candidate["id"] != self.config["id"])
                    or candidate["owner"].casefold() != self.owner.casefold()
                    or "BEGIN ENCRYPTED PRIVATE KEY" not in candidate["private_key"]
                    or not candidate["mail"].startswith("MM1:")):
                raise ValueError()
            base64.b64decode(candidate["wrapped_key"], validate=True)
        except (KeyError, TypeError, ValueError):
            raise ValueError("This recovery key does not belong to the encrypted history.") from None
        atomic_write(self.path, json.dumps(candidate).encode("utf-8"))
        self.config, self._challenge = candidate, None
        return "Recovery key restored. Enter its passphrase to request an email code."

    def change_mail_account(self, passphrase, sender, password):
        from .templates import address
        sender = address(sender)
        password = "".join(password.split())
        if len(password) != 16 or not password.isascii() or not password.isalnum():
            raise ValueError("Enter the Gmail account's 16-character app password.")
        try:
            private = serialization.load_pem_private_key(self.config["private_key"].encode(), passphrase.encode())
            key = private.decrypt(base64.b64decode(self.config["wrapped_key"]), self._padding())
        except Exception:
            raise ValueError("Incorrect private-key passphrase.") from None
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=25) as smtp:
                smtp.login(sender, password)
        except (smtplib.SMTPException, OSError):
            raise ValueError("Could not verify the code-sending Gmail account.") from None
        with self.lock:
            config = dict(self.config)
            config["mail"] = self._encrypt(key, json.dumps({"sender": sender, "password": password}), "otp-mail")
            atomic_write(self.path, json.dumps(config).encode("utf-8"))
            self.config = config
            self._challenge = None
            self._last_request = -1e20
        return "Code-sending account updated. The unlock code will still go to " + self.owner + "."

    @staticmethod
    def _send_code(sender, password, owner, code):
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = sender, owner, "Mail-Merger unlock code"
        message.set_content(f"Your Mail-Merger unlock code is {code}. It expires in five minutes.\nIf you did not request this, do not share the code.")
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=25) as smtp:
            smtp.login(sender, password)
            smtp.send_message(message)
