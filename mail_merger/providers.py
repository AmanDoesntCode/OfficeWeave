from __future__ import annotations

import asyncio
import smtplib
import ssl
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
from requests.exceptions import ConnectionError as RequestsConnectionError, Timeout as RequestsTimeout

from .auth import AuthenticationRequired
from .models import PreparedMail, Submission


def retry_seconds(value: str | None) -> float:
    if not value:
        return 0
    try:
        return max(0, float(value))
    except ValueError:
        try:
            return max(0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError):
            return 0


class APIProvider:
    def __init__(self, provider: str, sender: str, auth, client: httpx.AsyncClient | None = None, workers: int = 2):
        self.provider = provider
        self.sender = sender
        self.auth = auth
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(45, connect=15),
                                                 limits=httpx.Limits(max_connections=workers, max_keepalive_connections=workers))

    async def submit(self, mail: PreparedMail) -> Submission:
        try:
            token = await asyncio.to_thread(self.auth.token, self.provider, self.sender)
        except AuthenticationRequired:
            return Submission("auth", "Sign-in required; campaign paused.")
        except (httpx.RequestError, RequestsConnectionError, RequestsTimeout, OSError):
            return Submission("retry", "Network interrupted while refreshing sign-in; no message submitted.", network_interrupted=True)
        except Exception:
            return Submission("auth", "Could not refresh authentication; campaign paused.")
        url = ("https://gmail.googleapis.com/gmail/v1/users/me/messages/send" if self.provider == "gmail"
               else "https://graph.microsoft.com/v1.0/me/sendMail")
        try:
            response = await self.client.post(url, content=mail.payload,
                                             headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            return Submission("retry", "Connection failed before submission; no message submitted.", network_interrupted=True)
        except httpx.RequestError:
            return Submission("unknown", "Connection interrupted during submission; campaign paused. Inspect Sent mail before retrying.", network_interrupted=True)
        if response.status_code in {200, 202}:
            return Submission("accepted", "Accepted by provider; delivery is not confirmed.")
        if response.status_code == 401:
            return Submission("auth", "Provider requires sign-in again; campaign paused.")
        if response.status_code in {403, 429}:
            try:
                error = response.json().get("error", {})
                reason = str(error.get("code", "")) + " " + " ".join(
                    str(item.get("reason", "")) for item in error.get("errors", []))
                # Used only for classification, never stored or shown verbatim.
                description = str(error.get("message", "")).casefold()
            except (ValueError, AttributeError, TypeError):
                reason, description = "", ""
            delay = retry_seconds(response.headers.get("Retry-After"))
            if any(part in reason.casefold() + description for part in
                   ("dailylimit", "quota", "mail sending", "sending limit", "sendingquota")) or delay >= 300:
                return Submission("quota", "Sending quota reached; campaign paused.", delay)
            if response.status_code == 429 or "ratelimit" in reason.casefold() or "throttl" in reason.casefold():
                return Submission("retry", "Provider throttled this request.", delay)
            return Submission("auth", "Provider denied permission. Check account access and sign in again.")
        if response.status_code >= 500 or response.status_code == 408:
            return Submission("unknown", f"Provider returned HTTP {response.status_code}; acceptance is uncertain. Inspect Sent mail.")
        return Submission("failed", f"Provider rejected the message (HTTP {response.status_code}).")

    async def aclose(self):
        await self.client.aclose()


class GmailSMTPProvider:
    """Exclusive, reusable TLS connections; blocking SMTP runs off the UI loop."""

    def __init__(self, sender: str, auth, workers: int = 2):
        self.sender = sender
        self.auth = auth
        self.available = asyncio.Queue(maxsize=workers)
        self.connections = [None] * workers
        for index in range(workers):
            self.available.put_nowait(index)

    @staticmethod
    def _rejection(code: int, response: bytes) -> Submission:
        # Provider text is used only to classify the error, never displayed or logged.
        description = response.decode("utf-8", errors="replace").casefold() if isinstance(response, bytes) else str(response).casefold()
        if any(part in description for part in ("daily", "quota", "sending limit", "too many messages")):
            return Submission("quota", "Gmail sending limit reached; campaign paused.")
        if code in {530, 534, 535, 538}:
            return Submission("auth", "Gmail authentication failed. Reconnect with an app password.")
        if 400 <= code < 500:
            return Submission("retry", f"Gmail temporarily rejected the message (SMTP {code}).")
        return Submission("failed", f"Gmail rejected the message (SMTP {code}).")

    def _close(self, index):
        connection = self.connections[index]
        self.connections[index] = None
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

    def _send(self, index, mail):
        submitting = False
        try:
            email, password = self.auth.gmail_credentials(self.sender)
            connection = self.connections[index]
            if connection is None:
                connection = smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=30)
                self.connections[index] = connection
                connection.login(email, password)
            submitting = True
            refused = connection.sendmail(email, mail.envelope_to or [mail.to], mail.payload)
            if refused:
                return Submission("unknown", "Message accepted for some addresses; other recipients were rejected. Inspect Sent mail before retrying.")
            return Submission("accepted", "Accepted by Gmail.")
        except AuthenticationRequired:
            self._close(index)
            return Submission("auth", "Connect Gmail using your email and app password.")
        except smtplib.SMTPAuthenticationError:
            self._close(index)
            return Submission("auth", "Gmail authentication failed. Reconnect with an app password.")
        except smtplib.SMTPRecipientsRefused as exc:
            self._close(index)
            responses = list(exc.recipients.values())
            if responses and all(400 <= code < 500 for code, _ in responses):
                return Submission("retry", "Gmail temporarily rejected the recipient addresses.")
            return Submission("failed", "Gmail rejected the recipient addresses.")
        except smtplib.SMTPResponseException as exc:
            self._close(index)
            return self._rejection(exc.smtp_code, exc.smtp_error)
        except (smtplib.SMTPServerDisconnected, OSError):
            self._close(index)
            if submitting:
                return Submission("unknown", "Connection interrupted during submission; campaign paused. Inspect Sent mail before retrying.", network_interrupted=True)
            return Submission("retry", "Could not connect to Gmail before submission; no message submitted.", network_interrupted=True)
        except smtplib.SMTPException:
            self._close(index)
            return Submission("unknown" if submitting else "failed", "SMTP session failed; inspect Sent mail for uncertain submissions.")
        except Exception:
            self._close(index)
            if submitting:
                return Submission("unknown", "Submission failed with an uncertain outcome. Inspect Sent mail before retrying.")
            return Submission("auth", "Could not access saved Gmail credentials.")

    async def submit(self, mail: PreparedMail) -> Submission:
        index = await self.available.get()
        try:
            return await asyncio.to_thread(self._send, index, mail)
        finally:
            self.available.put_nowait(index)

    async def aclose(self):
        for index in range(len(self.connections)):
            await asyncio.to_thread(self._close, index)
