"""
Optional: fetch full creative details from the Meta API.

This is separate from the read-only file adapters because it makes live API
calls. Use only when the copy fields (hook_text, headline, etc.) are not
available from the raw ad snapshots.

Imports api.py from the main project via sys.path — read-only, no mutations.
All calls are GET requests. Logs to outputs/api.log per project convention.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from creative_intelligence import config

# ─────────────────────────────────────────────
# Lazy import of src/api.py without modifying it
# ─────────────────────────────────────────────

def _load_api_module():
    """Dynamically load src/api.py without adding src/ to the global sys.path."""
    api_path = Path(__file__).parent.parent.parent / "src" / "api.py"
    if not api_path.exists():
        raise ImportError(f"src/api.py not found at {api_path}")
    spec = importlib.util.spec_from_file_location("meta_api", api_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Creative fields we care about for copy extraction.
_CREATIVE_FIELDS = ",".join([
    "id",
    "name",
    "title",
    "body",
    "object_story_spec",
    "asset_feed_spec",
    "effective_object_story_id",
    "thumbnail_url",
    "video_id",
    "call_to_action_type",
    "link_url",
    "url_tags",
])


def fetch_creative_details(creative_ids: list[str]) -> list[dict[str, Any]]:
    """Fetch creative copy details from Meta API for a batch of creative IDs.

    Returns a list of raw API response dicts.
    Requires META_ACCESS_TOKEN to be set.
    """
    missing = config.validate()
    if missing:
        raise RuntimeError(f"Missing config: {missing}")

    api = _load_api_module()
    results = []
    for cid in creative_ids:
        url = f"{api.base_url()}/{cid}"
        row = api._get(url, {"fields": _CREATIVE_FIELDS})
        if row:
            results.append(row)
    return results


def _extract_copy(raw: dict[str, Any]) -> dict[str, Any]:
    """Parse a raw creative API response into copy fields."""
    copy: dict[str, Any] = {
        "hook_text":     "",
        "primary_text":  "",
        "headline":      "",
        "description":   "",
        "cta":           raw.get("call_to_action_type", ""),
        "destination_url": raw.get("link_url", ""),
        "format":        "",
    }

    # object_story_spec contains link_data / video_data
    oss = raw.get("object_story_spec") or {}
    link_data  = oss.get("link_data") or {}
    video_data = oss.get("video_data") or {}

    if link_data:
        copy["primary_text"]  = link_data.get("message", "")
        copy["headline"]      = link_data.get("name", "")
        copy["description"]   = link_data.get("description", "")
        copy["destination_url"] = link_data.get("link", copy["destination_url"])
        copy["format"] = "image"
        # Best-effort hook = first sentence of primary text
        pt = copy["primary_text"]
        copy["hook_text"] = pt.split(".")[0].split("\n")[0][:200] if pt else ""

    elif video_data:
        copy["primary_text"]  = video_data.get("message", "")
        copy["headline"]      = video_data.get("title", "")
        copy["description"]   = video_data.get("link_description", "")
        copy["destination_url"] = video_data.get("call_to_action", {}).get("value", {}).get("link", "")
        copy["format"] = "video"
        pt = copy["primary_text"]
        copy["hook_text"] = pt.split(".")[0].split("\n")[0][:200] if pt else ""

    # asset_feed_spec (DCO / multi-text)
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
        if not copy["hook_text"] and copy["primary_text"]:
            pt = copy["primary_text"]
            copy["hook_text"] = pt.split(".")[0].split("\n")[0][:200]
        if not copy["format"]:
            copy["format"] = "dco"

    # video_id presence signals video format even without video_data
    if raw.get("video_id") and not copy["format"]:
        copy["format"] = "video"

    return copy


def enrich_records_with_copy(
    records: list[dict[str, Any]],
    dry_run: bool = True,
) -> list[dict[str, Any]]:
    """Fetch creative details from Meta and merge copy into records.

    Args:
        records: list of dicts from ads_reader.build_creative_records()
        dry_run: if True, skip API calls and return records unchanged.
    """
    if dry_run:
        return records

    creative_ids = list({r["creative_id"] for r in records if r.get("creative_id")})
    raw_details  = fetch_creative_details(creative_ids)
    detail_map   = {d["id"]: _extract_copy(d) for d in raw_details}

    enriched = []
    for rec in records:
        copy = detail_map.get(rec["creative_id"], {})
        enriched.append({**rec, **copy})
    return enriched
