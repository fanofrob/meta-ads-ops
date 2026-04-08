"""
Read-only adapter for data/raw/ads_*.json and data/raw/campaigns_*.json.

Never writes to data/raw/. Never imports from src/.
Finds the latest timestamped file for each prefix and returns parsed records.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from creative_intelligence import config


def _latest_file(prefix: str, data_dir: Path | None = None) -> Path | None:
    """Return the most recent file matching `prefix_YYYY-MM-DD.json`."""
    directory = data_dir or config.RAW_DATA_DIR
    pattern = re.compile(rf"^{re.escape(prefix)}_(\d{{4}}-\d{{2}}-\d{{2}})\.json$")
    candidates: list[tuple[date, Path]] = []
    for p in directory.glob(f"{prefix}_*.json"):
        m = pattern.match(p.name)
        if m:
            candidates.append((date.fromisoformat(m.group(1)), p))
    if not candidates:
        return None
    return max(candidates, key=lambda x: x[0])[1]


def _load(prefix: str, data_dir: Path | None = None) -> list[dict[str, Any]]:
    path = _latest_file(prefix, data_dir)
    if path is None:
        return []
    return json.loads(path.read_text())


def _extract_copy_from_raw(raw: dict[str, Any]) -> dict[str, Any]:
    """Parse a raw creative API response into copy fields.

    Mirrors the logic in creative_fetcher._extract_copy but reads from
    the saved creatives_*.json file instead of making live API calls.
    """
    copy: dict[str, Any] = {
        "hook_text":      "",
        "primary_text":   raw.get("body", ""),
        "headline":       raw.get("title", ""),
        "description":    "",
        "cta":            raw.get("call_to_action_type", ""),
        "destination_url": raw.get("link_url", ""),
        "format":         "",
    }

    oss = raw.get("object_story_spec") or {}
    link_data  = oss.get("link_data") or {}
    video_data = oss.get("video_data") or {}

    if link_data:
        copy["primary_text"]   = copy["primary_text"] or link_data.get("message", "")
        copy["headline"]       = copy["headline"]      or link_data.get("name", "")
        copy["description"]    = link_data.get("description", "")
        copy["destination_url"] = copy["destination_url"] or link_data.get("link", "")
        copy["format"] = "image"
    elif video_data:
        copy["primary_text"]   = copy["primary_text"] or video_data.get("message", "")
        copy["headline"]       = copy["headline"]      or video_data.get("title", "")
        copy["description"]    = video_data.get("link_description", "")
        cta_val = video_data.get("call_to_action", {}).get("value", {})
        copy["destination_url"] = copy["destination_url"] or cta_val.get("link", "")
        copy["format"] = "video"

    afs = raw.get("asset_feed_spec") or {}
    if afs:
        bodies     = afs.get("bodies", [])
        titles     = afs.get("titles", [])
        desc_list  = afs.get("descriptions", [])
        if bodies and not copy["primary_text"]:
            copy["primary_text"] = bodies[0].get("text", "")
        if titles and not copy["headline"]:
            copy["headline"] = titles[0].get("text", "")
        if desc_list and not copy["description"]:
            copy["description"] = desc_list[0].get("text", "")
        if not copy["format"]:
            copy["format"] = "dco"

    if raw.get("video_id") and not copy["format"]:
        copy["format"] = "video"

    # Best-effort hook = first sentence / line of primary text
    pt = copy["primary_text"]
    if pt and not copy["hook_text"]:
        first = pt.split("\n")[0].split(". ")[0]
        copy["hook_text"] = first[:200]

    return copy


def load_creatives_file(data_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load creatives_*.json and return a {creative_id: copy_fields} index.

    Returns an empty dict if no file exists yet — callers should handle this
    gracefully (copy fields will be empty until creatives are fetched).
    """
    raw_list = _load("creatives", data_dir)
    return {item["id"]: _extract_copy_from_raw(item) for item in raw_list if item.get("id")}


def load_ads(data_dir: Path | None = None) -> list[dict[str, Any]]:
    """Load latest ads snapshot.

    Returns list of dicts with at minimum:
      id, name, adset_id, status, creative (dict with id, etc.)
    """
    return _load("ads", data_dir)


def load_campaigns(data_dir: Path | None = None) -> list[dict[str, Any]]:
    """Load latest campaigns snapshot."""
    return _load("campaigns", data_dir)


def load_adsets(data_dir: Path | None = None) -> list[dict[str, Any]]:
    """Load latest adsets snapshot."""
    return _load("adsets", data_dir)


def build_campaign_index(data_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    """Return {campaign_id: campaign_dict} for fast lookups."""
    return {c["id"]: c for c in load_campaigns(data_dir)}


def build_adset_index(data_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    """Return {adset_id: adset_dict} for fast lookups."""
    return {a["id"]: a for a in load_adsets(data_dir)}


def build_creative_records(data_dir: Path | None = None) -> list[dict[str, Any]]:
    """Merge ads + campaigns + adsets into flat creative records.

    Each record maps 1:1 with an ad and contains all the fields
    the creative intelligence DB needs.
    """
    ads = load_ads(data_dir)
    campaign_idx = build_campaign_index(data_dir)
    adset_idx = build_adset_index(data_dir)

    # Load copy from the saved creatives file if present (no live API call).
    creatives_idx = load_creatives_file(data_dir)
    copy_found = sum(1 for c in creatives_idx.values() if c.get("primary_text") or c.get("hook_text"))
    if creatives_idx:
        print(f"  [ads_reader] Loaded {len(creatives_idx)} creative copy records "
              f"({copy_found} with body text)")

    records = []
    for ad in ads:
        adset = adset_idx.get(ad.get("adset_id", ""), {})
        campaign_id = adset.get("campaign_id") or ad.get("campaign_id", "")
        campaign = campaign_idx.get(campaign_id, {})

        creative_raw = ad.get("creative") or {}
        creative_id = creative_raw.get("id") or ad["id"]

        # Merge copy from file if available; fall back to empty strings.
        copy = creatives_idx.get(creative_id, {})

        records.append({
            "creative_id":   creative_id,
            "ad_id":         ad["id"],
            "ad_name":       ad.get("name", ""),
            "adset_id":      ad.get("adset_id", ""),
            "adset_name":    adset.get("name", ""),
            "campaign_id":   campaign_id,
            "campaign_name": campaign.get("name", ""),
            "status":        ad.get("status", ""),
            "hook_text":     copy.get("hook_text", ""),
            "primary_text":  copy.get("primary_text", ""),
            "headline":      copy.get("headline", ""),
            "description":   copy.get("description", ""),
            "cta":           copy.get("cta", ""),
            "destination_url": copy.get("destination_url", ""),
            "format":        copy.get("format", ""),
        })
    return records
