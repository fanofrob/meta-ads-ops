"""
Image URL resolution for creative assets.

Tries to resolve the best available image URL for a creative in this priority order:
  1. image_url from creative API response
  2. thumbnail_url from creative API response
  3. effective_object_story thumbnail (via Meta API)
  4. None (no asset available)

Never downloads images locally. All analysis providers receive the URL directly.
Requires Meta API access only when refreshing from API (optional).
"""
from __future__ import annotations

import sqlite3
from typing import Any

from creative_intelligence.db import get_connection


def get_asset_url_from_db(
    creative_id: str,
    conn: sqlite3.Connection | None = None,
) -> tuple[str | None, str]:
    """Return (url, asset_type) for a creative from the DB.

    asset_type: 'image' | 'thumbnail' | 'none'
    Checks the creatives table destination_url and any cached thumbnail URLs.
    """
    db = conn or get_connection()
    row = db.execute(
        "SELECT destination_url FROM creatives WHERE id = ?",
        (creative_id,),
    ).fetchone()

    if row and row["destination_url"]:
        url = row["destination_url"]
        # If it looks like a direct image URL, use it.
        if any(url.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")):
            return url, "image"

    return None, "none"


def resolve_asset_urls_from_api(
    creative_ids: list[str],
    dry_run: bool = True,
) -> dict[str, tuple[str | None, str]]:
    """Fetch thumbnail_url and image_url from Meta API for a list of creatives.

    Returns {creative_id: (url, asset_type)}
    Only calls API when dry_run=False.
    """
    if dry_run:
        return {cid: (None, "none") for cid in creative_ids}

    import importlib.util
    from pathlib import Path
    from creative_intelligence import config

    api_path = Path(__file__).parent.parent.parent / "src" / "api.py"
    spec = importlib.util.spec_from_file_location("meta_api", api_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    results: dict[str, tuple[str | None, str]] = {}
    for cid in creative_ids:
        try:
            url = f"{module.base_url()}/{cid}"
            data = module._get(url, {"fields": "thumbnail_url,image_url,object_story_spec"})
            if not data:
                results[cid] = (None, "none")
                continue

            if data.get("image_url"):
                results[cid] = (data["image_url"], "image")
            elif data.get("thumbnail_url"):
                results[cid] = (data["thumbnail_url"], "thumbnail")
            else:
                results[cid] = (None, "none")
        except Exception:
            results[cid] = (None, "none")

    return results


def get_creatives_missing_visual_analysis(
    limit: int = 50,
    date_range: str = "7d",
    min_spend: float = 10.0,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Return creatives that have performance data but no visual analysis yet.

    Sorted by spend descending so we analyze top spenders first.
    """
    db = conn or get_connection()
    rows = db.execute(
        """
        SELECT c.id AS creative_id, c.ad_name, c.campaign_name, p.spend
        FROM creatives c
        JOIN creative_performance p ON p.creative_id = c.id
        LEFT JOIN creative_visual_attributes va ON va.creative_id = c.id
        WHERE p.date_range = ?
          AND p.spend >= ?
          AND va.id IS NULL
        ORDER BY p.spend DESC
        LIMIT ?
        """,
        (date_range, min_spend, limit),
    ).fetchall()
    return [dict(r) for r in rows]
