"""Explicit, opt-in legacy SMTP diagnostic; never connects during test collection."""


def main():
    import os
    import smtplib
    import ssl
    from dotenv import load_dotenv
    load_dotenv()
    email, password = os.getenv("login_email"), os.getenv("login_passwd")
    if not email or not password:
        raise SystemExit("Configure legacy CLI credentials in .env first.")
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=20) as server:
            server.login(email, password)
        print("Gmail SMTP authentication successful. No message was sent.")
    except Exception:
        raise SystemExit("SMTP authentication failed. Check your connection and app password.")


if __name__ == "__main__":
    main()
