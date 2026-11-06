"""Email verification required after Gmail authentication, independent of storage."""
import hashlib
import hmac
import os
import secrets
import threading
import time

from .auth import ProtectedCredentials
from .vault import Vault


class LoginOTP:
    def __init__(self, root, email, *, mailer=None, clock=time.monotonic):
        self.root, self.email = root, email
        self.mailer = mailer or Vault._send_code
        self.clock = clock
        self.challenge = None
        self.last_sent = -1e20
        self.blocked_until = 0
        self.failures = 0
        self.lock = threading.RLock()

    def request(self):
        with self.lock:
            now = self.clock()
            if now < self.blocked_until:
                raise ValueError("Too many attempts. Wait five minutes before trying again.")
            if now - self.last_sent < 60:
                raise ValueError("Wait 60 seconds before requesting another code.")
            config = ProtectedCredentials(self.root / "credentials").read("otp") or {}
            if not config.get("email") or not config.get("app_password"):
                raise ValueError("Import the private installation setup TXT to configure the OTP sender.")
            self.challenge = None
            self.last_sent = now
            code = f"{secrets.randbelow(1_000_000):06d}"
            salt = os.urandom(32)
            try:
                self.mailer(config["email"], config["app_password"], self.email, code)
            except Exception:
                raise ValueError("Could not send the sign-in code. Check your connection and OTP sender settings.") from None
            self.challenge = (salt, hashlib.sha256(salt + code.encode()).digest(), now + 300)
            return "A sign-in code was sent to " + self.email + "."

    def verify(self, code):
        with self.lock:
            now = self.clock()
            if now < self.blocked_until or not self.challenge or now > self.challenge[2]:
                raise ValueError("Request a new sign-in code.")
            salt, expected, _ = self.challenge
            if not hmac.compare_digest(expected, hashlib.sha256(salt + str(code).strip().encode()).digest()):
                self.failures += 1
                if self.failures >= 5:
                    self.challenge = None
                    self.blocked_until = now + 300
                raise ValueError("Incorrect sign-in code.")
            self.challenge = None
            self.failures = 0
