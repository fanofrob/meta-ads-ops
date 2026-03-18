"""
check_access.py
Verifies Meta Marketing API credentials by fetching basic ad account metadata.
Read-only. Saves response to data/raw/check_access.json.
"""

import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

REQUIRED_VARS = ["META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID", "META_API_VERSION"]
FIELDS = "id,name,account_status,currency,timezone_name,amount_spent,balance"


def check_env() -> dict:
    missing = [v for v in REQUIRED_VARS if not os.getenv(v)]
    if missing:
        print(f"[ERROR] Missing required environment variables: {', '.join(missing)}")
        print("  Copy .env.example to .env and fill in the values.")
        sys.exit(1)
    return {v: os.getenv(v) for v in REQUIRED_VARS}


def fetch_account(env: dict) -> dict:
    account_id = env["META_AD_ACCOUNT_ID"]
    url = f"https://graph.facebook.com/{env['META_API_VERSION']}/{account_id}"
    params = {
        "fields": FIELDS,
        "access_token": env["META_ACCESS_TOKEN"],
    }

    try:
        response = requests.get(url, params=params, timeout=10)
    except requests.exceptions.ConnectionError:
        print("[ERROR] Network error — could not reach the Meta API. Check your connection.")
        sys.exit(1)
    except requests.exceptions.Timeout:
        print("[ERROR] Request timed out.")
        sys.exit(1)

    data = response.json()

    if "error" in data:
        err = data["error"]
        code = err.get("code")
        message = err.get("message", "Unknown error")

        if code == 190:
            print(f"[ERROR] Invalid or expired access token. Regenerate your token.\n  Detail: {message}")
        elif code == 100:
            print(f"[ERROR] Ad account not found: {account_id}\n  Detail: {message}")
        elif code in (200, 273, 10):
            print(f"[ERROR] Permission denied. Ensure the token has 'ads_read' permission.\n  Detail: {message}")
        else:
            print(f"[ERROR] API error (code {code}): {message}")

        sys.exit(1)

    return data


def save(data: dict) -> Path:
    out = Path("data/raw/check_access.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2))
    return out


ACCOUNT_STATUSES = {
    1: "Active",
    2: "Disabled",
    3: "Unsettled",
    7: "Pending review",
    8: "Pending closure",
    9: "In grace period",
    100: "Temporarily unavailable",
    101: "Closed",
}


def main():
    print("Checking Meta API access...\n")

    env = check_env()
    print(f"  Account ID : {env['META_AD_ACCOUNT_ID']}")
    print(f"  API version: {env['META_API_VERSION']}")

    data = fetch_account(env)
    out_path = save(data)

    status_code = data.get("account_status")
    status_label = ACCOUNT_STATUSES.get(status_code, f"Unknown ({status_code})")

    print("\n[OK] Connection successful.\n")
    print(f"  Name      : {data.get('name')}")
    print(f"  ID        : {data.get('id')}")
    print(f"  Status    : {status_label}")
    print(f"  Currency  : {data.get('currency')}")
    print(f"  Timezone  : {data.get('timezone_name')}")
    print(f"\n  Response saved to {out_path}")

    if status_code != 1:
        print(f"\n[WARN] Account status is '{status_label}'. Delivery may be affected.")


if __name__ == "__main__":
    main()
