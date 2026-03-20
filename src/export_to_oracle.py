"""
export_to_oracle.py
Reads the latest raw Meta Ads data, builds a summary snapshot JSON,
and uploads it to the GHF Oracle Snapshots folder in Google Drive.

Run after the daily fetch scripts.
"""

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

RAW_DIR = Path("data/raw")
REPORTS_DIR = Path("outputs/reports")
ORACLE_SNAPSHOTS_FOLDER_ID = "1lZ6pYJBXrJi7iq6L9sCJEWiDcVD5kpI0"
SNAPSHOT_FILENAME = "meta_ads.json"
SNAPSHOT_FILE_ID = os.getenv("META_ADS_SNAPSHOT_FILE_ID", "")
REPORT_PDF_FILE_ID = os.getenv("REPORT_PDF_FILE_ID", "")
CREDENTIALS_PATH = os.getenv("GOOGLE_SERVICE_ACCOUNT_PATH", "credentials/sheets_service_account.json")

# Use only omni_purchase — it's the unified Meta metric and avoids double-counting
# with offsite_conversion.fb_pixel_purchase and purchase which cover the same events
PURCHASE_ACTIONS = {"omni_purchase"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _latest(prefix: str) -> Path | None:
    files = sorted(RAW_DIR.glob(f"{prefix}_????-??-??.json"), reverse=True)
    return files[0] if files else None


def _load(prefix: str) -> list:
    path = _latest(prefix)
    if not path:
        print(f"[WARN] No {prefix} file found — section will be empty.")
        return []
    print(f"  Loaded {path.name}")
    return json.loads(path.read_text())


def _action_value(row: dict, keys: set) -> float:
    total = 0.0
    for entry in row.get("action_values", []):
        if entry.get("action_type") in keys:
            total += float(entry.get("value", 0))
    return total


def _action_count(row: dict, keys: set) -> int:
    total = 0
    for entry in row.get("actions", []):
        if entry.get("action_type") in keys:
            total += int(float(entry.get("value", 0)))
    return total


# ---------------------------------------------------------------------------
# Build snapshot
# ---------------------------------------------------------------------------

def _aggregate(rows: list) -> dict:
    spend       = sum(float(r.get("spend", 0)) for r in rows)
    impressions = sum(int(r.get("impressions", 0)) for r in rows)
    clicks      = sum(int(r.get("clicks", 0)) for r in rows)
    purchases   = sum(_action_count(r, PURCHASE_ACTIONS) for r in rows)
    revenue     = sum(_action_value(r, PURCHASE_ACTIONS) for r in rows)
    return {
        "spend":       spend,
        "impressions": impressions,
        "clicks":      clicks,
        "purchases":   purchases,
        "revenue":     revenue,
        "ctr":         (clicks / impressions * 100) if impressions > 0 else 0,
        "cpc":         (spend / clicks) if clicks > 0 else 0,
        "cpm":         (spend / impressions * 1000) if impressions > 0 else 0,
        "roas":        (revenue / spend) if spend > 0 else 0,
        "cpa":         (spend / purchases) if purchases > 0 else 0,
    }


def build_snapshot() -> dict:
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    # Yesterday (daily insights file)
    insights_1d = _load("insights")
    rows_1d = [r for r in insights_1d if r.get("date_start") == yesterday or not r.get("date_start")]
    if not rows_1d:
        rows_1d = insights_1d
    d1 = _aggregate(rows_1d)

    # Last 7 days (7d insights file)
    insights_7d = _load("insights_7d")
    d7 = _aggregate(insights_7d)

    # Campaign count
    campaigns = _load("campaigns")
    active_campaigns = sum(1 for c in campaigns if c.get("status") == "ACTIVE")

    summary = (
        f"Yesterday — Spend ${d1['spend']:,.2f} | ROAS {d1['roas']:.2f}x | CPA ${d1['cpa']:,.2f} | Purchases {d1['purchases']} | "
        f"7d — Spend ${d7['spend']:,.2f} | ROAS {d7['roas']:.2f}x | {active_campaigns} active campaigns"
    )

    return {
        "tool": "Meta Ads",
        "date": yesterday,
        "summary": summary,
        "data": {
            "--- YESTERDAY ---":          "",
            "Spend":                      f"${d1['spend']:,.2f}",
            "Revenue (attributed)":       f"${d1['revenue']:,.2f}",
            "ROAS":                       f"{d1['roas']:.2f}x",
            "CPA":                        f"${d1['cpa']:,.2f}",
            "Purchases":                  str(d1['purchases']),
            "Impressions":                f"{d1['impressions']:,}",
            "Clicks":                     f"{d1['clicks']:,}",
            "CTR":                        f"{d1['ctr']:.2f}%",
            "CPC":                        f"${d1['cpc']:.2f}",
            "CPM":                        f"${d1['cpm']:.2f}",
            "--- LAST 7 DAYS ---":        "",
            "Spend (7d)":                 f"${d7['spend']:,.2f}",
            "Revenue (7d, attributed)":   f"${d7['revenue']:,.2f}",
            "ROAS (7d)":                  f"{d7['roas']:.2f}x",
            "CPA (7d)":                   f"${d7['cpa']:,.2f}",
            "Purchases (7d)":             str(d7['purchases']),
            "Impressions (7d)":           f"{d7['impressions']:,}",
            "CTR (7d)":                   f"{d7['ctr']:.2f}%",
            "CPC (7d)":                   f"${d7['cpc']:.2f}",
            "Active campaigns":           str(active_campaigns),
        }
    }


# ---------------------------------------------------------------------------
# Upload to Google Drive
# ---------------------------------------------------------------------------

def upload_to_drive(snapshot: dict) -> None:
    if not Path(CREDENTIALS_PATH).exists():
        print(f"[ERROR] Service account not found at {CREDENTIALS_PATH}")
        sys.exit(1)

    creds = service_account.Credentials.from_service_account_file(
        CREDENTIALS_PATH,
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    content = json.dumps(snapshot, indent=2).encode("utf-8")
    media = MediaInMemoryUpload(content, mimetype="application/json", resumable=False)

    if not SNAPSHOT_FILE_ID:
        raise RuntimeError(
            "META_ADS_SNAPSHOT_FILE_ID is not set. "
            "Run createMetaAdsSnapshotFile() in Apps Script, copy the file ID, "
            "and add it as a GitHub secret."
        )
    service.files().update(fileId=SNAPSHOT_FILE_ID, media_body=media).execute()
    print(f"  Updated JSON snapshot (id: {SNAPSHOT_FILE_ID})")


def upload_pdf_to_drive(pdf_path: Path) -> None:
    if not REPORT_PDF_FILE_ID:
        print("  [SKIP] REPORT_PDF_FILE_ID not set — skipping PDF upload.")
        return
    if not pdf_path.exists():
        print(f"  [SKIP] PDF not found at {pdf_path}")
        return

    creds = service_account.Credentials.from_service_account_file(
        CREDENTIALS_PATH,
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    media = MediaInMemoryUpload(pdf_path.read_bytes(), mimetype="application/pdf", resumable=False)
    service.files().update(fileId=REPORT_PDF_FILE_ID, media_body=media).execute()
    print(f"  Updated PDF report in Drive (id: {REPORT_PDF_FILE_ID})")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Building Meta Ads Oracle snapshot...\n")
    snapshot = build_snapshot()

    # Attach latest markdown report if available
    reports = sorted(REPORTS_DIR.glob("????-??-??.md"), reverse=True)
    if reports:
        snapshot["report"] = reports[0].read_text()
        print(f"  Attached report: {reports[0].name}")

    print(f"\nSnapshot summary: {snapshot['summary']}")
    print("Uploading JSON snapshot to Drive...")
    upload_to_drive(snapshot)

    # Upload PDF to Drive if available
    pdfs = sorted(REPORTS_DIR.glob("????-??-??.pdf"), reverse=True)
    if pdfs:
        print("Uploading PDF report to Drive...")
        upload_pdf_to_drive(pdfs[0])

    print("\n[OK] Meta Ads snapshot uploaded to GHF Oracle.")


if __name__ == "__main__":
    main()
