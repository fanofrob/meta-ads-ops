"""
send_email.py
Sends the daily Meta Ads PDF report via Gmail SMTP.

Requires env vars:
  GMAIL_USER         — your Gmail address
  GMAIL_APP_PASSWORD — Gmail App Password (myaccount.google.com/apppasswords)
  EMAIL_TO           — comma-separated recipient addresses
"""

import os
import smtplib
import sys
from datetime import date, timedelta
from email.message import EmailMessage
from pathlib import Path

REPORTS_DIR = Path("outputs/reports")


def send_report() -> None:
    gmail_user     = os.getenv("GMAIL_USER", "")
    gmail_password = os.getenv("GMAIL_APP_PASSWORD", "")
    to_emails      = [e.strip() for e in os.getenv("EMAIL_TO", "").split(",") if e.strip()]

    if not gmail_user or not gmail_password:
        print("[ERROR] GMAIL_USER and GMAIL_APP_PASSWORD must be set.")
        sys.exit(1)
    if not to_emails:
        print("[ERROR] EMAIL_TO must be set.")
        sys.exit(1)

    pdfs = sorted(REPORTS_DIR.glob("????-??-??.pdf"), reverse=True)
    if not pdfs:
        print("[ERROR] No PDF report found in outputs/reports/")
        sys.exit(1)

    pdf_path = pdfs[0]
    report_date = (date.today() - timedelta(days=1)).isoformat()

    msg = EmailMessage()
    msg["Subject"] = f"GHF Meta Ads Report — {report_date}"
    msg["From"]    = gmail_user
    msg["To"]      = ", ".join(to_emails)
    msg.set_content(f"Meta Ads daily report for {report_date}. See attached PDF.")
    msg.add_attachment(
        pdf_path.read_bytes(),
        maintype="application",
        subtype="pdf",
        filename=pdf_path.name
    )

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(gmail_user, gmail_password)
        smtp.send_message(msg)

    print(f"[OK] Report emailed to: {', '.join(to_emails)}")


if __name__ == "__main__":
    send_report()
