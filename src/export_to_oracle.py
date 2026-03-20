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
ORACLE_SNAPSHOTS_FOLDER_ID = "1lZ6pYJBXrJi7iq6L9sCJEWiDcVD5kpI0"
SNAPSHOT_FILENAME = "meta_ads.json"
SNAPSHOT_FILE_ID = os.getenv("META_ADS_SNAPSHOT_FILE_ID", "")  # set this to avoid Drive quota issues
CREDENTIALS_PATH = os.getenv("GOOGLE_SERVICE_ACCOUNT_PATH", "credentials/sheets_service_account.json")

PURCHASE_ACTIONS = {"omni_purchase", "offsite_conversion.fb_pixel_purchase", "purchase"}

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

def build_snapshot() -> dict:
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    # Load yesterday's insights (daily file, not 7d/30d)
    insights = _load("insights")

    # Filter to yesterday only (file should already be yesterday-only, but guard anyway)
    rows = [r for r in insights if r.get("date_start") == yesterday or not r.get("date_start")]

    if not rows:
        # Fall back to all rows in file if date filtering strips everything
        rows = insights

    spend = sum(float(r.get("spend", 0)) for r in rows)
    impressions = sum(int(r.get("impressions", 0)) for r in rows)
    clicks = sum(int(r.get("clicks", 0)) for r in rows)
    purchases = sum(_action_count(r, PURCHASE_ACTIONS) for r in rows)
    revenue = sum(_action_value(r, PURCHASE_ACTIONS) for r in rows)

    ctr = (clicks / impressions * 100) if impressions > 0 else 0
    cpc = (spend / clicks) if clicks > 0 else 0
    cpm = (spend / impressions * 1000) if impressions > 0 else 0
    roas = (revenue / spend) if spend > 0 else 0
    cpa = (spend / purchases) if purchases > 0 else 0

    # Campaign count from campaigns file
    campaigns = _load("campaigns")
    active_campaigns = sum(1 for c in campaigns if c.get("status") == "ACTIVE")

    summary = (
        f"Spend ${spend:,.2f} | ROAS {roas:.2f}x | CPA ${cpa:,.2f} | "
        f"Purchases {purchases} | {active_campaigns} active campaigns"
    )

    return {
        "tool": "Meta Ads",
        "date": yesterday,
        "summary": summary,
        "data": {
            "Spend (yesterday)":    f"${spend:,.2f}",
            "Revenue (attributed)": f"${revenue:,.2f}",
            "ROAS":                 f"{roas:.2f}x",
            "CPA":                  f"${cpa:,.2f}",
            "Purchases":            str(purchases),
            "Impressions":          f"{impressions:,}",
            "Clicks":               f"{clicks:,}",
            "CTR":                  f"{ctr:.2f}%",
            "CPC":                  f"${cpc:.2f}",
            "CPM":                  f"${cpm:.2f}",
            "Active campaigns":     str(active_campaigns),
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

    if SNAPSHOT_FILE_ID:
        # Update the pre-created file directly (avoids service account quota issues)
        service.files().update(fileId=SNAPSHOT_FILE_ID, media_body=media).execute()
        print(f"  Updated snapshot (id: {SNAPSHOT_FILE_ID})")
    else:
        raise RuntimeError(
            "META_ADS_SNAPSHOT_FILE_ID is not set. "
            "Create meta_ads.json manually in the Snapshots Drive folder, "
            "copy its file ID, and add it as a GitHub secret."
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Building Meta Ads Oracle snapshot...\n")
    snapshot = build_snapshot()

    print(f"\nSnapshot summary: {snapshot['summary']}")
    print(f"Uploading to Drive folder: {ORACLE_SNAPSHOTS_FOLDER_ID}")
    upload_to_drive(snapshot)
    print("\n[OK] Meta Ads snapshot uploaded to GHF Oracle.")


if __name__ == "__main__":
    main()
