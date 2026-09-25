"""
fetch_creatives.py
Fetches creative copy details (body, headline, CTA, format) for ads that have
spend in the last 7 days. Saves raw JSON to data/raw/.

Read-only. No mutations. Does not modify any other file in src/.

Why this file exists:
  fetch_ads.py only stores the creative ID, not the copy fields.
  The creative intelligence subsystem needs body text, headlines, and hook
  text to tag and analyse creative patterns. This script fills that gap.

Usage:
  python src/fetch_creatives.py               # fetch creatives for 7d active ads
  python src/fetch_creatives.py --all         # fetch creatives for all 1240 ads
  python src/fetch_creatives.py --limit 50    # fetch at most N creatives (for testing)
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import date
from pathlib import Path

from api import _get, load_env, save_raw

# Fields we need from each creative object.
CREATIVE_FIELDS = ",".join([
    "id",
    "name",
    "body",
    "title",
    "object_story_spec",
    "asset_feed_spec",
    "effective_object_story_id",
    "thumbnail_url",
    "video_id",
    "call_to_action_type",
    "link_url",
    "url_tags",
])

# Batch size for Meta Graph API batch requests.
# Meta allows up to 50 per batch; we use 40 to stay safely under limits.
_BATCH_SIZE = 40

# Minimum pause between batch requests (seconds). Keeps us well under rate limits.
_BATCH_PAUSE = 0.5


def _latest_raw_file(prefix: str) -> Path | None:
    """Find the most recent data/raw/{prefix}_YYYY-MM-DD.json file."""
    raw_dir = Path("data/raw")
    pattern = re.compile(rf"^{re.escape(prefix)}_(\d{{4}}-\d{{2}}-\d{{2}})\.json$")
    candidates = []
    for p in raw_dir.glob(f"{prefix}_*.json"):
        m = pattern.match(p.name)
        if m:
            candidates.append((m.group(1), p))
    if not candidates:
        return None
    return max(candidates, key=lambda x: x[0])[1]


def _load_active_creative_ids(fetch_all: bool = False, window: str = "7d") -> list[str]:
    """Return creative IDs to fetch.

    By default: only creatives from ads that have spend in the latest
    insights_<window> file (7d or 30d) — keeps the fetch small and focused.
    With fetch_all=True: all creative IDs from the latest ads file.
    """
    ads_file = _latest_raw_file("ads")
    if not ads_file:
        print("[ERROR] No ads_*.json found in data/raw/. Run fetch_ads.py first.")
        sys.exit(1)

    ads = json.loads(ads_file.read_text())
    print(f"  Loaded {len(ads)} ads from {ads_file.name}")

    if fetch_all:
        ids = list({
            a["creative"]["id"]
            for a in ads
            if a.get("creative", {}).get("id")
        })
        print(f"  Using all {len(ids)} unique creative IDs")
        return ids

    # Filter to ads with spend in the latest insights file for the window
    insights_file = _latest_raw_file(f"insights_{window}")
    if not insights_file:
        print(f"[WARN] No insights_{window}_*.json found — falling back to all ads.")
        ids = list({a["creative"]["id"] for a in ads if a.get("creative", {}).get("id")})
        print(f"  Using {len(ids)} creative IDs (no insights filter)")
        return ids

    insights = json.loads(insights_file.read_text())
    active_ad_ids = {row["ad_id"] for row in insights if row.get("ad_id")}
    print(f"  {len(active_ad_ids)} ads have {window} spend (from {insights_file.name})")

    ad_id_to_creative = {
        a["id"]: a["creative"]["id"]
        for a in ads
        if a.get("creative", {}).get("id")
    }

    ids = list({
        ad_id_to_creative[ad_id]
        for ad_id in active_ad_ids
        if ad_id in ad_id_to_creative
    })
    print(f"  {len(ids)} unique creative IDs to fetch")
    return ids


def _fetch_creative_batch(
    creative_ids: list[str],
    env: dict,
) -> list[dict]:
    """Fetch a batch of creative IDs via the Meta Batch API.

    Meta's batch endpoint: POST /vX.X with a 'batch' JSON array.
    We simulate it as multiple GET requests (simpler, avoids POST auth complexity).
    """
    results = []
    base = f"https://graph.facebook.com/{env['META_API_VERSION']}"
    token = env["META_ACCESS_TOKEN"]

    for cid in creative_ids:
        url = f"{base}/{cid}"
        try:
            data = _get(url, {"fields": CREATIVE_FIELDS, "access_token": token})
            if data and "id" in data:
                results.append(data)
        except SystemExit:
            # _get calls sys.exit on hard errors; convert to a skip with warning
            print(f"  [WARN] Failed to fetch creative {cid} — skipping")
        time.sleep(0.05)  # 50ms between individual calls within a batch

    return results


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Fetch Meta ad creative copy details.")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Fetch creatives for ALL ads, not just 7d-active ads.",
    )
    parser.add_argument(
        "--window",
        choices=["7d", "30d", "90d"],
        default="7d",
        help="Which insights window decides the 'active' ads (default 7d).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of creatives to fetch (useful for testing).",
    )
    args = parser.parse_args()

    print("Fetching creative copy details...\n")

    env = load_env()
    print(f"  Account ID : {env['META_AD_ACCOUNT_ID']}")
    print(f"  API version: {env['META_API_VERSION']}\n")

    creative_ids = _load_active_creative_ids(fetch_all=args.all, window=args.window)

    if args.limit:
        creative_ids = creative_ids[: args.limit]
        print(f"  Limiting to {args.limit} creatives (--limit flag)")

    total = len(creative_ids)
    print(f"\n  Fetching {total} creatives in batches of {_BATCH_SIZE}...\n")

    all_results = []
    for i in range(0, total, _BATCH_SIZE):
        batch = creative_ids[i : i + _BATCH_SIZE]
        batch_num = i // _BATCH_SIZE + 1
        total_batches = (total + _BATCH_SIZE - 1) // _BATCH_SIZE
        print(f"  Batch {batch_num}/{total_batches} ({len(batch)} creatives)...")

        fetched = _fetch_creative_batch(batch, env)
        all_results.extend(fetched)
        print(f"    Fetched {len(fetched)} — running total: {len(all_results)}")

        if i + _BATCH_SIZE < total:
            time.sleep(_BATCH_PAUSE)

    out_path = save_raw(all_results, f"creatives_{date.today().isoformat()}.json")

    print(f"\n[OK] Fetched {len(all_results)} creatives.")
    print(f"  Saved to {out_path}")

    # Quick summary of what we got
    has_body  = sum(1 for c in all_results if c.get("body") or c.get("object_story_spec"))
    has_video = sum(1 for c in all_results if c.get("video_id"))
    print(f"\n  With copy (body or story_spec): {has_body} ({has_body/max(len(all_results),1):.0%})")
    print(f"  Video creatives:                {has_video} ({has_video/max(len(all_results),1):.0%})")


if __name__ == "__main__":
    main()
