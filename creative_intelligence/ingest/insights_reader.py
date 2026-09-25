"""
Read-only adapter for data/raw/insights_*.json files.

Supports three date ranges already produced by the main pipeline:
  - insights_YYYY-MM-DD.json        → yesterday (date_range="yesterday")
  - insights_7d_YYYY-MM-DD.json     → last 7 days (date_range="7d")
  - insights_30d_YYYY-MM-DD.json    → last 30 days (date_range="30d")

Never writes to data/raw/. Never imports from src/.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from creative_intelligence import config

# Map date_range label → file prefix used by the main pipeline.
_PREFIX_MAP = {
    "yesterday": "insights",
    "7d":        "insights_7d",
    "30d":       "insights_30d",
    "90d":       "insights_90d",
}


def _latest_file(prefix: str, data_dir: Path | None = None) -> Path | None:
    directory = data_dir or config.RAW_DATA_DIR
    pattern = re.compile(rf"^{re.escape(prefix)}_(\d{{4}}-\d{{2}}-\d{{2}})\.json$")
    candidates: list[tuple[date, Path]] = []
    for p in directory.glob(f"{prefix}_*.json"):
        m = pattern.match(p.name)
        if m:
            candidates.append((date.fromisoformat(m.group(1)), p))
    if not candidates:
        return None
    best = max(candidates, key=lambda x: x[0])
    return best[1]


def _latest_snapshot_date(prefix: str, data_dir: Path | None = None) -> str | None:
    directory = data_dir or config.RAW_DATA_DIR
    pattern = re.compile(rf"^{re.escape(prefix)}_(\d{{4}}-\d{{2}}-\d{{2}})\.json$")
    candidates: list[date] = []
    for p in directory.glob(f"{prefix}_*.json"):
        m = pattern.match(p.name)
        if m:
            candidates.append(date.fromisoformat(m.group(1)))
    if not candidates:
        return None
    return max(candidates).isoformat()


def _parse_action_value(actions: list[dict], action_type: str) -> int | None:
    """Extract value for a specific action_type from Meta actions array."""
    for a in actions or []:
        if a.get("action_type") == action_type:
            try:
                return int(float(a.get("value", 0)))
            except (ValueError, TypeError):
                return None
    return None


def _parse_cost_per_action(cpa_list: list[dict], action_type: str) -> float | None:
    for a in cpa_list or []:
        if a.get("action_type") == action_type:
            try:
                return float(a.get("value", 0))
            except (ValueError, TypeError):
                return None
    return None


def _parse_video_metric(video_list: list[dict], pct_key: str) -> float | None:
    """Parse video_p25/p50/p75/p100 from video_p_X_watched_actions."""
    for item in video_list or []:
        if item.get("action_type", "").endswith(pct_key):
            try:
                return float(item.get("value", 0))
            except (ValueError, TypeError):
                return None
    return None


def load_insights(
    date_range: str = "7d",
    data_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Load raw insight rows for the given date_range.

    Returns the raw list as stored in the JSON file.
    Use parse_insights() for normalised records.
    """
    prefix = _PREFIX_MAP.get(date_range)
    if prefix is None:
        raise ValueError(f"Unknown date_range '{date_range}'. Use: {list(_PREFIX_MAP)}")
    path = _latest_file(prefix, data_dir)
    if path is None:
        return []
    return json.loads(path.read_text())


def parse_insights(
    date_range: str = "7d",
    data_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Return normalised performance dicts keyed by ad_id.

    Each dict contains:
      ad_id, creative_id (=ad_id if no separate creative), date_range,
      snapshot_date, spend, impressions, clicks, ctr, cpc, cpm, frequency,
      purchases, revenue, roas, cpa,
      video_views, video_view_rate, video_p25, video_p50, video_p75, video_p100
    """
    prefix = _PREFIX_MAP[date_range]
    snapshot_date = _latest_snapshot_date(prefix, data_dir) or date.today().isoformat()
    raw = load_insights(date_range, data_dir)

    # The 30d file contains one row per (ad, date) when time_increment=1.
    # We aggregate those to a single row per ad_id for the performance table.
    aggregated: dict[str, dict[str, Any]] = {}

    for row in raw:
        ad_id = row.get("ad_id") or row.get("id", "")
        if not ad_id:
            continue

        if ad_id not in aggregated:
            aggregated[ad_id] = {
                "ad_id":          ad_id,
                "creative_id":    ad_id,  # refined by creative_fetcher if needed
                "date_range":     date_range,
                "snapshot_date":  snapshot_date,
                "spend":          0.0,
                "impressions":    0,
                "clicks":         0,
                "purchases":      0,
                "revenue":        0.0,
                "video_views":    0,
            }

        entry = aggregated[ad_id]
        entry["spend"]       += float(row.get("spend", 0) or 0)
        entry["impressions"] += int(row.get("impressions", 0) or 0)
        entry["clicks"]      += int(row.get("clicks", 0) or 0)

        # Actions
        actions    = row.get("actions", [])
        cpa_list   = row.get("cost_per_action_type", [])
        video_list = row.get("video_p_X_watched_actions", [])

        purchases = _parse_action_value(actions, "omni_purchase")
        if purchases:
            entry["purchases"] += purchases

        # Revenue — use purchase_value if present
        rev_list = row.get("action_values", [])
        for rv in rev_list or []:
            if rv.get("action_type") == "omni_purchase":
                try:
                    entry["revenue"] += float(rv.get("value", 0))
                except (ValueError, TypeError):
                    pass

        vv = _parse_action_value(actions, "video_view")
        if vv:
            entry["video_views"] += vv

        # Store latest per-row values for rates (overwrite is fine for aggregated date ranges)
        if row.get("ctr"):
            entry["_last_ctr"] = float(row["ctr"])
        if row.get("cpc"):
            entry["_last_cpc"] = float(row["cpc"])
        if row.get("cpm"):
            entry["_last_cpm"] = float(row["cpm"])
        if row.get("frequency"):
            entry["_last_frequency"] = float(row["frequency"])

    # Compute derived metrics
    results = []
    for entry in aggregated.values():
        spend       = entry["spend"]
        impressions = entry["impressions"]
        clicks      = entry["clicks"]
        purchases   = entry["purchases"]
        revenue     = entry["revenue"]

        ctr       = (clicks / impressions * 100) if impressions else entry.pop("_last_ctr", None)
        cpc       = (spend / clicks)             if clicks      else entry.pop("_last_cpc", None)
        cpm       = (spend / impressions * 1000) if impressions else entry.pop("_last_cpm", None)
        frequency = entry.pop("_last_frequency", None)
        roas      = (revenue / spend)            if spend and revenue else None
        cpa       = (spend / purchases)          if purchases         else None

        # Clean up temp keys
        entry.pop("_last_ctr", None)
        entry.pop("_last_cpc", None)
        entry.pop("_last_cpm", None)

        results.append({
            **entry,
            "ctr":       round(ctr, 4)       if ctr       is not None else None,
            "cpc":       round(cpc, 4)       if cpc       is not None else None,
            "cpm":       round(cpm, 4)       if cpm       is not None else None,
            "frequency": round(frequency, 4) if frequency is not None else None,
            "roas":      round(roas, 4)      if roas      is not None else None,
            "cpa":       round(cpa, 4)       if cpa       is not None else None,
            "video_view_rate": round(entry["video_views"] / impressions * 100, 4)
                               if impressions and entry["video_views"] else None,
            "video_p25": None,
            "video_p50": None,
            "video_p75": None,
            "video_p100": None,
        })
    return results
