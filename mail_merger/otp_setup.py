"""Import a private installation configuration without uploading secret files."""
from pathlib import Path
import smtplib
import ssl

from .auth import ProtectedCredentials
from .templates import address
from .account_storage import installation_root


def parse_setup(text):
    lines = [line.strip() for line in text.lstrip("\ufeff").splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if any("=" in line for line in lines):
        values = {}
        for line in lines:
            key, separator, value = line.partition("=")
            if not separator or key.strip().lower() not in {"email", "app_password", "audit_url", "audit_secret"}:
                raise ValueError("Use email= and app_password= in the private setup TXT file.")
            key = key.strip().lower()
            if key in values:
                raise ValueError("The setup TXT contains a repeated setting.")
            values[key] = value.strip()
    elif len(lines) == 2:
        values = {"email": lines[0], "app_password": lines[1]}
    else:
        raise ValueError("The TXT must contain an email and Google app password.")
    values["email"] = address(values.get("email", ""))
    values["app_password"] = "".join(values.get("app_password", "").split())
    password = values["app_password"]
    if len(password) != 16 or not password.isascii() or not password.isalnum():
        raise ValueError("The TXT must contain a 16-character Google app password.")
    if values.get("audit_url") or values.get("audit_secret"):
        from .audit import validate_endpoint
        validate_endpoint(values.get("audit_url", ""), values.get("audit_secret", ""))
    return values


def import_setup(path, root):
    path = Path(path)
    if path.suffix.lower() != ".txt" or path.stat().st_size > 16_000:
        raise ValueError("Choose a private setup .txt file smaller than 16 KB.")
    values = parse_setup(path.read_text("utf-8-sig"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=25) as smtp:
            smtp.login(values["email"], values["app_password"])
    except (smtplib.SMTPException, OSError):
        raise ValueError("Could not verify the TXT's Gmail account. Check its app password and your connection.") from None
    ProtectedCredentials(installation_root(root) / "credentials").write("otp", values)
    return values["email"]


def export_setup(root):
    values = ProtectedCredentials(installation_root(root) / "credentials").read("otp")
    if not values:
        raise ValueError("Configure the OTP sender first.")
    return "\n".join(f"{key}={values[key]}" for key in ("email", "app_password", "audit_url", "audit_secret") if values.get(key)) + "\n"


def attach_setup_prompt(window, root):
    from .vault import Vault
    if Vault(root).default_mail_sender():
        return
    script = (Path(__file__).parent / "assets" / "otp-setup.js").read_text("utf-8")
    def show():
        try:
            if Vault(root).default_mail_sender():
                return
            window.evaluate_js(script)
        except Exception:
            pass
    window.events.loaded += show
