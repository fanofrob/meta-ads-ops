"""
Tag-dimension comparisons for creative analysis.

Answers questions like:
  - Which hook types have the best CTR / ROAS / CPA?
  - Which angles generate the most purchases?
  - Which formats are most efficient?
  - How do winners differ from losers by tag dimension?
"""
from __future__ import annotations

import sqlite3
from typing import Any

from creative_intelligence.db import get_connection
from creative_intelligence.analysis.performance import MIN_SPEND_THRESHOLD

_TAG_DIMENSIONS = [
    "hook_type",
    "angle",
    "format",
    "archetype",
    "emotional_trigger",
    "offer_style",
    "cta_type",
]


def _conn() -> sqlite3.Connection:
    return get_connection()


def compare_by_dimension(
    dimension: str,
    metric: str = "ctr",
    date_range: str = "7d",
    min_spend: float = MIN_SPEND_THRESHOLD,
    min_creatives: int = 1,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Compare all tag values for a given dimension by a performance metric.

    Returns rows sorted by the metric (best first).
    metric: ctr | cpa | roas | spend | purchases | impressions | clicks
    """
    if dimension not in _TAG_DIMENSIONS:
        raise ValueError(f"dimension must be one of {_TAG_DIMENSIONS}")

    allowed_metrics = {"ctr", "cpa", "roas", "spend", "purchases", "impressions", "clicks", "cpm", "cpc"}
    if metric not in allowed_metrics:
        raise ValueError(f"metric must be one of {allowed_metrics}")

    order_dir = "ASC" if metric == "cpa" else "DESC"
    agg = "MIN" if metric == "cpa" else "MAX"

    sql = f"""
        SELECT
            t.tag_value                         AS dimension_value,
            COUNT(DISTINCT c.id)                AS creative_count,
            ROUND(SUM(p.spend), 2)              AS total_spend,
            ROUND(AVG(p.ctr), 4)                AS avg_ctr,
            ROUND(AVG(p.cpc), 4)                AS avg_cpc,
            ROUND(AVG(p.cpm), 4)                AS avg_cpm,
            ROUND(AVG(p.roas), 4)               AS avg_roas,
            ROUND(AVG(p.cpa), 4)                AS avg_cpa,
            ROUND(AVG(p.frequency), 4)          AS avg_frequency,
            SUM(p.purchases)                    AS total_purchases,
            SUM(p.clicks)                       AS total_clicks,
            SUM(p.impressions)                  AS total_impressions
        FROM creatives c
        JOIN creative_performance p ON p.creative_id = c.id
        JOIN creative_tags t ON t.creative_id = c.id AND t.tag_type = ?
        WHERE p.date_range = ?
          AND p.spend >= ?
          AND t.tag_value != 'unknown'
        GROUP BY t.tag_value
        HAVING COUNT(DISTINCT c.id) >= ?
        ORDER BY avg_{metric} {order_dir}
    """
    db = conn or _conn()
    rows = db.execute(sql, (dimension, date_range, min_spend, min_creatives)).fetchall()
    return [dict(r) for r in rows]


def winner_vs_loser_by_dimension(
    dimension: str,
    date_range: str = "7d",
    min_spend: float = MIN_SPEND_THRESHOLD,
    top_pct: float = 0.25,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Compare the distribution of tag values between winners and losers.

    Returns:
      {
        'dimension': ...,
        'winners':   [{dimension_value, count, pct}, ...],
        'losers':    [{dimension_value, count, pct}, ...],
        'insight':   str  (e.g. "Winners skew 70% curiosity hooks vs 20% for losers")
      }
    """
    from creative_intelligence.analysis.performance import classify_winners

    db = conn or _conn()
    buckets = classify_winners(date_range=date_range, min_spend=min_spend, conn=db)

    def count_tags(creative_list: list[dict]) -> dict[str, int]:
        if not creative_list:
            return {}
        ids = [c["creative_id"] for c in creative_list]
        placeholders = ",".join("?" * len(ids))
        rows = db.execute(
            f"""SELECT tag_value, COUNT(*) AS cnt
                FROM creative_tags
                WHERE tag_type = ? AND creative_id IN ({placeholders})
                  AND tag_value != 'unknown'
                GROUP BY tag_value ORDER BY cnt DESC""",
            [dimension] + ids,
        ).fetchall()
        return {r["tag_value"]: r["cnt"] for r in rows}

    w_counts = count_tags(buckets["winners"])
    l_counts = count_tags(buckets["losers"])

    def to_pct_list(counts: dict[str, int]) -> list[dict[str, Any]]:
        total = sum(counts.values()) or 1
        return sorted(
            [{"dimension_value": k, "count": v, "pct": round(v / total * 100, 1)}
             for k, v in counts.items()],
            key=lambda x: x["count"],
            reverse=True,
        )

    w_list = to_pct_list(w_counts)
    l_list = to_pct_list(l_counts)

    # Build a simple insight string.
    insight = ""
    if w_list and l_list:
        top_w = w_list[0]
        top_l = l_list[0]
        if top_w["dimension_value"] != top_l["dimension_value"]:
            insight = (
                f"Winners skew toward '{top_w['dimension_value']}' ({top_w['pct']}%), "
                f"while losers skew toward '{top_l['dimension_value']}' ({top_l['pct']}%)."
            )
        else:
            insight = (
                f"Both winners and losers share '{top_w['dimension_value']}' as top tag "
                f"({top_w['pct']}% winners, {top_l['pct']}% losers)."
            )

    return {
        "dimension": dimension,
        "winners":   w_list,
        "losers":    l_list,
        "insight":   insight,
    }


def full_comparison_report(
    date_range: str = "7d",
    min_spend: float = MIN_SPEND_THRESHOLD,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Run comparisons across all tag dimensions and return a summary dict."""
    db = conn or _conn()
    report: dict[str, Any] = {}
    for dim in _TAG_DIMENSIONS:
        try:
            by_ctr  = compare_by_dimension(dim, "ctr",  date_range, min_spend, conn=db)
            by_roas = compare_by_dimension(dim, "roas", date_range, min_spend, conn=db)
            wvl     = winner_vs_loser_by_dimension(dim, date_range, min_spend, conn=db)
            report[dim] = {
                "by_ctr":           by_ctr,
                "by_roas":          by_roas,
                "winner_vs_loser":  wvl,
            }
        except Exception:
            report[dim] = {}
    return report
