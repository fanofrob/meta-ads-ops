"""
send_email.py
Sends the daily Meta Ads PDF report via SendGrid.

Requires env vars:
  SENDGRID_API_KEY   — SendGrid API key
  EMAIL_FROM         — sender address (must be verified in SendGrid)
  EMAIL_TO           — comma-separated recipient addresses
"""

import base64
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import requests

REPORTS_DIR = Path("outputs/reports")


def _latest_pdf() -> Path | None:
    files = sorted(REPORTS_DIR.glob("????-??-??.pdf"), reverse=True)
    return files[0] if files else None


def send_report() -> None:
    api_key  = os.getenv("SENDGRID_API_KEY", "")
    from_email = os.getenv("EMAIL_FROM", "")
    to_emails  = [e.strip() for e in os.getenv("EMAIL_TO", "").split(",") if e.strip()]

    if not api_key:
        print("[ERROR] SENDGRID_API_KEY is not set.")
        sys.exit(1)
    if not from_email or not to_emails:
        print("[ERROR] EMAIL_FROM and EMAIL_TO must be set.")
        sys.exit(1)

    pdf_path = _latest_pdf()
    if not pdf_path:
        print("[ERROR] No PDF report found in outputs/reports/")
        sys.exit(1)

    report_date = (date.today() - timedelta(days=1)).isoformat()
    subject = f"GHF Meta Ads Report — {report_date}"

    pdf_b64 = base64.b64encode(pdf_path.read_bytes()).decode("utf-8")

    payload = {
        "personalizations": [{"to": [{"email": e} for e in to_emails]}],
        "from": {"email": from_email},
        "subject": subject,
        "content": [{"type": "text/plain", "value": f"Meta Ads daily report for {report_date}. See attached PDF."}],
        "attachments": [{
            "content":     pdf_b64,
            "type":        "application/pdf",
            "filename":    pdf_path.name,
            "disposition": "attachment"
        }]
    }

    resp = requests.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        data=json.dumps(payload),
        timeout=30
    )

    if resp.status_code in (200, 202):
        print(f"[OK] Report emailed to: {', '.join(to_emails)}")
    else:
        print(f"[ERROR] SendGrid returned {resp.status_code}: {resp.text}")
        sys.exit(1)


if __name__ == "__main__":
    send_report()
