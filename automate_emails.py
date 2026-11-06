"""Legacy Mail-Merger CLI. Importing this module never connects or sends mail."""

from __future__ import annotations


def main(argv=None) -> int:
    import csv
    import os
    import time
    from datetime import datetime, timezone
    from pathlib import Path

    from dotenv import load_dotenv
    import yagmail

    from argument_parser import parse_arguments
    from read_glsheets import read_glsheets
    from mail_merger.templates import address, render

    args = parse_arguments(argv)
    load_dotenv()
    email, password = os.getenv("login_email"), os.getenv("login_passwd")
    if not email or not password:
        raise ValueError("Legacy CLI credentials are missing from .env. The desktop app uses OAuth instead.")
    records = read_glsheets(args.link)
    template = Path(args.content).read_text(encoding="utf-8")
    smtp = yagmail.SMTP(user=email, password=password, host="smtp.gmail.com", port=465)
    failures = 0
    try:
        log_path = Path("log.csv")
        write_header = not log_path.exists() or not log_path.stat().st_size
        with log_path.open("a", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            if write_header:
                writer.writerow(["Receiver", "Status", "Time", "Error"])
            for index, record in enumerate(records, 1):
                receiver = record.get("Receiver", "")
                try:
                    receiver = address(receiver)
                    # Retain old {Name} templates as well as the desktop ${Name} syntax.
                    body = render(template, record, html=True) if "${" in template else template.format(**record)
                    subject = render(args.subject, record)
                    smtp.send(to=receiver, cc=args.cc, bcc=args.bcc, subject=subject,
                              contents=body, attachments=args.attach)
                    status, error = "ACCEPTED", ""
                    print(f"[{index}/{len(records)}] Accepted by provider: {receiver}")
                except Exception:
                    failures += 1
                    status, error = "FAILED_OR_UNKNOWN", "Inspect Sent mail before retrying; submission may have occurred."
                    print(f"[{index}/{len(records)}] Failed or uncertain: {receiver}")
                writer.writerow([receiver, status, datetime.now(timezone.utc).isoformat(), error])
                stream.flush()
                if index < len(records):
                    time.sleep(2)
    finally:
        smtp.close()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
