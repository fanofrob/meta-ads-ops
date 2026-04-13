"""
Creative performance analysis.

Queries the creative_intelligence SQLite DB to identify:
  - top-performing creatives by metric
  - winner / loser classification
  - spend-weighted averages
  - performance tier buckets
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from creative_intelligence.db import get_connection

# Minimum spend to be considered a statistically meaningful creative.
MIN_SPEND_THRESHOLD = 10.0
# Minimum impressions for CTR to be meaningful.
MIN_IMPRESSIONS_THRESHOLD = 200


def _conn() -> sqlite3.Connection:
    return get_connection()


def top_creatives(
    metric: str = "roas",
    date_range: str = "7d",
    limit: int = 10,
    min_spend: float = MIN_SPEND_THRESHOLD,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Return top N creatives sorted by the given metric.

    metric: roas | cpa | ctr | spend | clicks | purchases
    """
    allowed = {"roas", "cpa", "ctr", "spend", "clicks", "purchases", "impressions"}
    if metric not in allowed:
        raise ValueError(f"metric must be one of {allowed}")

    order_dir = "ASC" if metric == "cpa" else "DESC"

    sql = f"""
        SELECT
            c.id                AS creative_id,
            c.ad_id,
            c.ad_name,
            c.campaign_name,
            c.format,
            p.spend,
            p.impressions,
            p.clicks,
            p.ctr,
            p.cpc,
            p.cpm,
            p.purchases,
            p.roas,
            p.cpa,
            p.frequency
        FROM creatives c
        JOIN creative_performance p ON p.creative_id = c.id
        WHERE p.date_range = ?
          AND p.spend >= ?
          AND p.{metric} IS NOT NULL
        ORDER BY p.{metric} {order_dir}
        LIMIT ?
    """
    db = conn or _conn()
    rows = db.execute(sql, (date_range, min_spend, limit)).fetchall()
    return [dict(r) for r in rows]


def classify_winners(
    date_range: str = "7d",
    top_pct: float = 0.25,
    min_spend: float = MIN_SPEND_THRESHOLD,
    conn: sqlite3.Connection | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Split creatives into winners / mid-tier / losers based on ROAS + CPA.

    Returns dict with keys: 'winners', 'mid', 'losers'.
    Uses top_pct to define winner/loser buckets (default top/bottom 25%).
    """
    sql = """
        SELECT
            c.id AS creative_id,
            c.ad_name,
            c.campaign_name,
            p.spend,
            p.roas,
            p.cpa,
            p.ctr,
            p.purchases
        FROM creatives c
        JOIN creative_performance p ON p.creative_id = c.id
        WHERE p.date_range = ? AND p.spend >= ?
        ORDER BY p.spend DESC
    """
    db = conn or _conn()
    rows = [dict(r) for r in db.execute(sql, (date_range, min_spend)).fetchall()]

    if not rows:
        return {"winners": [], "mid": [], "losers": []}

    # Score each row: prefer ROAS, fall back to CPA ranking.
    scored_roas = [(r, r.get("roas") or 0) for r in rows if r.get("roas") is not None]
    scored_cpa  = [(r, r.get("cpa") or 999999) for r in rows if r.get("roas") is None and r.get("cpa")]

    # Sort: high ROAS = good, low CPA = good.
    scored_roas.sort(key=lambda x: x[1], reverse=True)
    scored_cpa.sort(key=lambda x: x[1])

    def bucket(scored: list[tuple[dict, float]], invert: bool = False) -> tuple[list, list, list]:
        n = len(scored)
        if n == 0:
            return [], [], []
        top_n = max(1, int(n * top_pct))
        bot_n = max(1, int(n * top_pct))
        winners = [r for r, _ in scored[:top_n]]
        losers  = [r for r, _ in scored[-bot_n:]]
        mid     = [r for r, _ in scored[top_n: n - bot_n]]
        return winners, mid, losers

    roas_w, roas_m, roas_l = bucket(scored_roas)
    cpa_w,  cpa_m,  cpa_l  = bucket(scored_cpa)

    return {
        "winners": roas_w + cpa_w,
        "mid":     roas_m + cpa_m,
        "losers":  roas_l + cpa_l,
    }


def spend_weighted_averages(
    date_range: str = "7d",
    group_by: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Compute spend-weighted average metrics.

    If group_by is a tag_type (e.g., 'hook_type'), returns per-group averages.
    Otherwise returns a single aggregate row.
    """
    db = conn or _conn()

    if group_by:
        sql = """
            SELECT
                t.tag_value                                    AS group_value,
                COUNT(DISTINCT c.id)                          AS creative_count,
                ROUND(SUM(p.spend), 2)                        AS total_spend,
                ROUND(AVG(p.ctr), 4)                          AS avg_ctr,
                ROUND(SUM(p.spend * COALESCE(p.cpc,0)) /
                      NULLIF(SUM(p.spend), 0), 4)             AS wavg_cpc,
                ROUND(SUM(p.spend * COALESCE(p.roas,0)) /
                      NULLIF(SUM(p.spend * (p.roas IS NOT NULL)), 0), 4) AS wavg_roas,
                ROUND(AVG(p.cpa), 4)                          AS avg_cpa,
                ROUND(AVG(p.frequency), 4)                    AS avg_frequency,
                SUM(p.purchases)                               AS total_purchases
            FROM creatives c
            JOIN creative_performance p ON p.creative_id = c.id
            JOIN creative_tags t ON t.creative_id = c.id AND t.tag_type = ?
            WHERE p.date_range = ?
            GROUP BY t.tag_value
            ORDER BY total_spend DESC
        """
        rows = db.execute(sql, (group_by, date_range)).fetchall()
    else:
        sql = """
            SELECT
                'all'                                          AS group_value,
                COUNT(DISTINCT c.id)                          AS creative_count,
                ROUND(SUM(p.spend), 2)                        AS total_spend,
                ROUND(AVG(p.ctr), 4)                          AS avg_ctr,
                ROUND(AVG(p.cpc), 4)                          AS wavg_cpc,
                ROUND(AVG(p.roas), 4)                         AS wavg_roas,
                ROUND(AVG(p.cpa), 4)                          AS avg_cpa,
                ROUND(AVG(p.frequency), 4)                    AS avg_frequency,
                SUM(p.purchases)                               AS total_purchases
            FROM creatives c
            JOIN creative_performance p ON p.creative_id = c.id
            WHERE p.date_range = ?
        """
        rows = db.execute(sql, (date_range,)).fetchall()

    return [dict(r) for r in rows]


def creative_fatigue_flags(
    frequency_threshold: float = 3.5,
    date_range: str = "7d",
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Return creatives showing signs of frequency fatigue."""
    sql = """
        SELECT
            c.id AS creative_id,
            c.ad_name,
            c.campaign_name,
            p.spend,
            p.frequency,
            p.ctr,
            p.cpm
        FROM creatives c
        JOIN creative_performance p ON p.creative_id = c.id
        WHERE p.date_range = ?
          AND p.frequency >= ?
        ORDER BY p.frequency DESC
    """
    db = conn or _conn()
    return [dict(r) for r in db.execute(sql, (date_range, frequency_threshold)).fetchall()]
