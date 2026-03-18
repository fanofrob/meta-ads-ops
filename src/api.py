"""
api.py
Shared helpers for Meta Marketing API read-only access.
"""

import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

REQUIRED_VARS = ["META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID", "META_API_VERSION"]


def load_env() -> dict:
    """Validate required environment variables and return them."""
    missing = [v for v in REQUIRED_VARS if not os.getenv(v)]
    if missing:
        print(f"[ERROR] Missing required environment variables: {', '.join(missing)}")
        sys.exit(1)
    return {v: os.getenv(v) for v in REQUIRED_VARS}


def base_url(env: dict, path: str) -> str:
    """Build a versioned Graph API URL for the given account-relative path."""
    return f"https://graph.facebook.com/{env['META_API_VERSION']}/{env['META_AD_ACCOUNT_ID']}/{path}"


# Error codes that are transient and safe to retry
_RETRYABLE_CODES = {1, 2, 4, 17, 341}  # unknown/service unavailable/rate limit
_MAX_RETRIES = 4


def _get(url: str, params: dict) -> dict:
    """Make a single GET request with exponential backoff for transient errors."""
    for attempt in range(_MAX_RETRIES):
        try:
            response = requests.get(url, params=params, timeout=60)
        except requests.exceptions.ConnectionError:
            if attempt < _MAX_RETRIES - 1:
                wait = 2 ** attempt
                print(f"  [WARN] Network error — retrying in {wait}s (attempt {attempt + 1}/{_MAX_RETRIES})...")
                time.sleep(wait)
                continue
            print("[ERROR] Network error — could not reach the Meta API.")
            sys.exit(1)
        except requests.exceptions.Timeout:
            if attempt < _MAX_RETRIES - 1:
                wait = 2 ** attempt
                print(f"  [WARN] Request timed out — retrying in {wait}s (attempt {attempt + 1}/{_MAX_RETRIES})...")
                time.sleep(wait)
                continue
            print("[ERROR] Request timed out after all retries.")
            sys.exit(1)

        data = response.json()

        if "error" in data:
            err = data["error"]
            code = err.get("code")
            if code in _RETRYABLE_CODES and attempt < _MAX_RETRIES - 1:
                wait = 2 ** (attempt + 1)
                print(f"  [WARN] API error (code {code}) — retrying in {wait}s (attempt {attempt + 1}/{_MAX_RETRIES})...")
                time.sleep(wait)
                continue
            print(f"[ERROR] API error (code {code}): {err.get('message')}")
            sys.exit(1)

        return data

    print("[ERROR] All retries exhausted.")
    sys.exit(1)


def paginated_get(url: str, params: dict, label: str = "records") -> list:
    """Fetch all pages from a paginated endpoint and return a flat list."""
    results = []
    page = 1

    while url:
        print(f"  Fetching page {page}...")
        data = _get(url, params)

        page_data = data.get("data", [])
        results.extend(page_data)
        print(f"  Retrieved {len(page_data)} {label} (total so far: {len(results)})")

        # Clear params after the first request — the next cursor URL already includes them
        url = data.get("paging", {}).get("next")
        params = {}
        page += 1

    return results


def save_raw(data: list, filename: str) -> Path:
    """Save a list to data/raw/{filename} as formatted JSON."""
    out = Path("data/raw") / filename
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2))
    return out
