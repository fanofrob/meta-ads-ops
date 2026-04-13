"""
QA and validation checks for the creative intelligence subsystem.

Designed to be run before leaning on generation or analysis.
All checks are read-only — no writes to the database.

Each check returns a dict:
  {
    "name":    str,          # check name
    "status":  "ok" | "warn" | "fail",
    "count":   int | None,
    "detail":  str,          # human-readable summary
    "rows":    list[dict],   # supporting data (may be empty)
  }
"""
from __future__ import annotations

import sqlite3
from typing import Any

from creative_intelligence.db import get_connection

# ─────────────────────────────────────────────
# Thresholds
# ─────────────────────────────────────────────

# Below this threshold, warn that tag coverage is low.
TAG_COVERAGE_WARN_PCT = 0.70  # 70% of creatives should have all tag dimensions
# If more than this fraction of tags are "unknown", warn.
UNKNOWN_TAG_WARN_PCT  = 0.40
# If creative count is below this, warn about statistical significance.
MIN_CREATIVE_COUNT    = 5


def _conn() -> sqlite3.Connection:
    return get_connection()


# ─────────────────────────────────────────────
# 1. Ingest integrity
# ─────────────────────────────────────────────

def check_creative_count(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    db = conn or _conn()
    n = db.execute("SELECT COUNT(*) FROM creatives").fetchone()[0]
    status = "ok" if n >= MIN_CREATIVE_COUNT else "warn"
    return {
        "name":   "creative_count",
        "status": status,
        "count":  n,
        "detail": f"{n} creatives ingested" + (f" — below minimum {MIN_CREATIVE_COUNT}" if status == "warn" else ""),
        "rows":   [],
    }


def check_duplicate_creatives(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    db = conn or _conn()
    rows = db.execute(
        """SELECT id, COUNT(*) AS cnt FROM creatives GROUP BY id HAVING cnt > 1"""
    ).fetchall()
    status = "fail" if rows else "ok"
    return {
        "name":   "duplicate_creatives",
        "status": status,
        "count":  len(rows),
        "detail": f"{len(rows)} duplicate creative IDs found" if rows else "No duplicates",
        "rows":   [dict(r) for r in rows],
    }


def check_creatives_missing_performance(
    date_range: str = "7d",
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    db = conn or _conn()
    rows = db.execute(
        """SELECT c.id, c.ad_name, c.campaign_name
           FROM creatives c
           LEFT JOIN creative_performance p
             ON p.creative_id = c.id AND p.date_range = ?
           WHERE p.id IS NULL
           ORDER BY c.ad_name
           LIMIT 20""",
        (date_range,),
    ).fetchall()
    total_creatives = db.execute("SELECT COUNT(*) FROM creatives").fetchone()[0]
    pct = len(rows) / max(total_creatives, 1)
    status = "warn" if pct > 0.5 else "ok"
    return {
        "name":   f"creatives_missing_performance_{date_range}",
        "status": status,
        "count":  len(rows),
        "detail": f"{len(rows)} of {total_creatives} creatives have no {date_range} performance data",
        "rows":   [dict(r) for r in rows],
    }


def check_performance_null_fields(
    date_range: str = "7d",
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    db = conn or _conn()
    stats = db.execute(
        """SELECT
               COUNT(*) AS total,
               SUM(CASE WHEN spend IS NULL OR spend = 0 THEN 1 ELSE 0 END) AS null_spend,
               SUM(CASE WHEN impressions IS NULL OR impressions = 0 THEN 1 ELSE 0 END) AS null_impressions,
               SUM(CASE WHEN ctr IS NULL THEN 1 ELSE 0 END) AS null_ctr,
               SUM(CASE WHEN roas IS NULL THEN 1 ELSE 0 END) AS null_roas,
               SUM(CASE WHEN cpa IS NULL THEN 1 ELSE 0 END) AS null_cpa
           FROM creative_performance WHERE date_range = ?""",
        (date_range,),
    ).fetchone()

    if not stats or stats["total"] == 0:
        return {
            "name": f"performance_null_fields_{date_range}",
            "status": "warn",
            "count": 0,
            "detail": f"No performance rows found for {date_range}",
            "rows": [],
        }

    total = stats["total"]
    issues = []
    if stats["null_spend"] == total:
        issues.append("spend all null/zero")
    if stats["null_roas"] == total:
        issues.append("roas all null (no purchase tracking — expected if no pixel)")
    if stats["null_cpa"] == total:
        issues.append("cpa all null (no purchase tracking)")

    status = "warn" if issues else "ok"
    return {
        "name":   f"performance_null_fields_{date_range}",
        "status": status,
        "count":  total,
        "detail": (
            f"{total} performance rows | "
            f"spend null: {stats['null_spend']} | "
            f"impressions null: {stats['null_impressions']} | "
            f"ROAS null: {stats['null_roas']} | "
            f"CPA null: {stats['null_cpa']}"
            + (f" — NOTE: {'; '.join(issues)}" if issues else "")
        ),
        "rows": [],
    }


def check_performance_sanity(
    date_range: str = "7d",
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Detect obviously wrong metric values (negative spend, CTR > 100%, etc.)."""
    db = conn or _conn()
    bad_rows = db.execute(
        """SELECT ad_id, spend, ctr, cpc, roas
           FROM creative_performance
           WHERE date_range = ?
             AND (spend < 0
               OR (ctr IS NOT NULL AND ctr > 100)
               OR (cpc IS NOT NULL AND cpc < 0)
               OR (roas IS NOT NULL AND roas < 0))
           LIMIT 10""",
        (date_range,),
    ).fetchall()
    status = "fail" if bad_rows else "ok"
    return {
        "name":   f"performance_sanity_{date_range}",
        "status": status,
        "count":  len(bad_rows),
        "detail": f"{len(bad_rows)} rows with out-of-range metric values" if bad_rows else "All metrics in plausible range",
        "rows":   [dict(r) for r in bad_rows],
    }


# ─────────────────────────────────────────────
# 2. Copy field coverage
# ─────────────────────────────────────────────

def check_copy_field_coverage(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Check what % of creatives have meaningful copy in key fields."""
    db = conn or _conn()
    total = db.execute("SELECT COUNT(*) FROM creatives").fetchone()[0]
    if total == 0:
        return {"name": "copy_field_coverage", "status": "warn", "count": 0,
                "detail": "No creatives ingested", "rows": []}

    stats = db.execute(
        """SELECT
               SUM(CASE WHEN hook_text    != '' AND hook_text    IS NOT NULL THEN 1 ELSE 0 END) AS has_hook,
               SUM(CASE WHEN primary_text != '' AND primary_text IS NOT NULL THEN 1 ELSE 0 END) AS has_primary,
               SUM(CASE WHEN headline     != '' AND headline     IS NOT NULL THEN 1 ELSE 0 END) AS has_headline,
               SUM(CASE WHEN destination_url != '' AND destination_url IS NOT NULL THEN 1 ELSE 0 END) AS has_url
           FROM creatives"""
    ).fetchone()

    rows = [
        {"field": "hook_text",      "with_value": stats["has_hook"],    "total": total, "pct": round(stats["has_hook"] / total * 100, 1)},
        {"field": "primary_text",   "with_value": stats["has_primary"], "total": total, "pct": round(stats["has_primary"] / total * 100, 1)},
        {"field": "headline",       "with_value": stats["has_headline"],"total": total, "pct": round(stats["has_headline"] / total * 100, 1)},
        {"field": "destination_url","with_value": stats["has_url"],     "total": total, "pct": round(stats["has_url"] / total * 100, 1)},
    ]

    low_coverage = [r for r in rows if r["pct"] < 50]
    status = "warn" if low_coverage else "ok"
    detail = " | ".join(f"{r['field']}: {r['pct']}%" for r in rows)
    if low_coverage:
        detail += f" — LOW: {', '.join(r['field'] for r in low_coverage)} < 50%"
        detail += " (run `ingest --skip-copy-fetch=false` to enrich from Meta API)"

    return {
        "name":   "copy_field_coverage",
        "status": status,
        "count":  total,
        "detail": detail,
        "rows":   rows,
    }


# ─────────────────────────────────────────────
# 3. Tagging quality
# ─────────────────────────────────────────────

def check_tag_coverage(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Check what % of creatives have been tagged."""
    db = conn or _conn()
    total = db.execute("SELECT COUNT(*) FROM creatives").fetchone()[0]
    tagged = db.execute(
        "SELECT COUNT(DISTINCT creative_id) FROM creative_tags"
    ).fetchone()[0]
    pct = tagged / max(total, 1)
    status = "ok" if pct >= TAG_COVERAGE_WARN_PCT else "warn"
    return {
        "name":   "tag_coverage",
        "status": status,
        "count":  tagged,
        "detail": f"{tagged} of {total} creatives tagged ({pct:.0%})" +
                  (" — run `tag` command" if status == "warn" else ""),
        "rows":   [],
    }


def check_unknown_tag_rate(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Report the % of tags that resolved to 'unknown' per dimension."""
    db = conn or _conn()
    rows = db.execute(
        """SELECT
               tag_type,
               COUNT(*) AS total,
               SUM(CASE WHEN tag_value = 'unknown' THEN 1 ELSE 0 END) AS unknown_count,
               ROUND(SUM(CASE WHEN tag_value = 'unknown' THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) AS unknown_pct
           FROM creative_tags
           GROUP BY tag_type
           ORDER BY unknown_pct DESC"""
    ).fetchall()

    high_unknown = [r for r in rows if r["unknown_pct"] > UNKNOWN_TAG_WARN_PCT * 100]
    status = "warn" if high_unknown else "ok"
    detail = (
        f"{len(high_unknown)} dimensions with > {UNKNOWN_TAG_WARN_PCT:.0%} unknown rate"
        if high_unknown else
        "Unknown rates within acceptable range"
    )
    return {
        "name":   "unknown_tag_rate",
        "status": status,
        "count":  len(high_unknown),
        "detail": detail,
        "rows":   [dict(r) for r in rows],
    }


def check_tag_distribution(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Return tag value distribution per dimension for human review."""
    db = conn or _conn()
    rows = db.execute(
        """SELECT tag_type, tag_value, COUNT(*) AS count,
                  ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (PARTITION BY tag_type), 1) AS pct
           FROM creative_tags
           GROUP BY tag_type, tag_value
           ORDER BY tag_type, count DESC"""
    ).fetchall()
    return {
        "name":   "tag_distribution",
        "status": "ok",
        "count":  len(rows),
        "detail": f"{len(rows)} tag type/value combinations",
        "rows":   [dict(r) for r in rows],
    }


def check_unmapped_creatives(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Creatives with no product_id match."""
    db = conn or _conn()
    total = db.execute("SELECT COUNT(*) FROM creatives").fetchone()[0]
    unmapped = db.execute(
        """SELECT id, ad_name, campaign_name FROM creatives
           WHERE product_id IS NULL ORDER BY ad_name LIMIT 20"""
    ).fetchall()
    pct = len(unmapped) / max(total, 1)
    # Unmapped is expected if no products loaded yet — warn only if products exist.
    product_count = db.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    status = "warn" if (pct > 0.5 and product_count > 0) else "ok"
    return {
        "name":   "unmapped_creatives",
        "status": status,
        "count":  len(unmapped),
        "detail": f"{len(unmapped)} of {total} creatives have no product match" +
                  (" (no products loaded yet)" if product_count == 0 else ""),
        "rows":   [dict(r) for r in unmapped],
    }


# ─────────────────────────────────────────────
# 4. Pattern quality
# ─────────────────────────────────────────────

def check_pattern_count(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    db = conn or _conn()
    n = db.execute("SELECT COUNT(*) FROM creative_patterns").fetchone()[0]
    status = "warn" if n == 0 else "ok"
    return {
        "name":   "pattern_count",
        "status": status,
        "count":  n,
        "detail": f"{n} patterns extracted" + (" — run `extract-patterns`" if n == 0 else ""),
        "rows":   [],
    }


def check_pattern_quality(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Patterns with no performance data or single creatives."""
    db = conn or _conn()
    rows = db.execute(
        """SELECT pattern_name, winner_count, creative_count, avg_ctr, avg_roas
           FROM creative_patterns
           ORDER BY winner_count DESC"""
    ).fetchall()

    low_quality = [r for r in rows if r["creative_count"] < 2 or r["avg_ctr"] is None]
    return {
        "name":   "pattern_quality",
        "status": "warn" if low_quality else "ok",
        "count":  len(rows),
        "detail": f"{len(rows)} patterns | {len(low_quality)} with low confidence (< 2 creatives or no metrics)",
        "rows":   [dict(r) for r in rows],
    }


# ─────────────────────────────────────────────
# 5. Score sanity
# ─────────────────────────────────────────────

def check_score_distribution(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Sanity check: scores should be spread across 0–100, not all at extremes."""
    db = conn or _conn()
    stats = db.execute(
        """SELECT
               score_type,
               COUNT(*) AS n,
               ROUND(MIN(score), 1) AS min_score,
               ROUND(MAX(score), 1) AS max_score,
               ROUND(AVG(score), 1) AS avg_score
           FROM creative_scores
           WHERE score_type = 'overall'
           GROUP BY score_type"""
    ).fetchone()

    if not stats:
        return {
            "name":   "score_distribution",
            "status": "warn",
            "count":  0,
            "detail": "No scores yet — run `score`",
            "rows":   [],
        }

    # Warn if all scores are the same (no discrimination) or all 0.
    if stats["min_score"] == stats["max_score"]:
        status = "warn"
        detail = f"All scores identical ({stats['avg_score']}) — tagging or performance data may be insufficient"
    elif stats["avg_score"] == 0:
        status = "warn"
        detail = "Average score is 0 — check that performance data is linked to creatives"
    else:
        status = "ok"
        detail = f"{stats['n']} scores | min: {stats['min_score']} | max: {stats['max_score']} | avg: {stats['avg_score']}"

    return {
        "name":   "score_distribution",
        "status": status,
        "count":  stats["n"],
        "detail": detail,
        "rows":   [dict(stats)],
    }


# ─────────────────────────────────────────────
# 6. Misclassification review helpers
# ─────────────────────────────────────────────

def get_sample_by_tag(
    tag_type: str,
    tag_value: str,
    limit: int = 5,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Return sample creatives for a given tag type/value for human review."""
    db = conn or _conn()
    rows = db.execute(
        """SELECT c.id, c.ad_name, c.hook_text, c.primary_text, c.headline,
                  t.tag_value, t.confidence, t.source,
                  p.spend, p.ctr, p.roas
           FROM creatives c
           JOIN creative_tags t ON t.creative_id = c.id
                AND t.tag_type = ? AND t.tag_value = ?
           LEFT JOIN creative_performance p ON p.creative_id = c.id AND p.date_range = '7d'
           ORDER BY COALESCE(p.spend, 0) DESC
           LIMIT ?""",
        (tag_type, tag_value, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def get_unknown_sample(
    tag_type: str,
    limit: int = 10,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Return sample creatives tagged 'unknown' for a given dimension — useful for rule calibration."""
    return get_sample_by_tag(tag_type, "unknown", limit, conn)


# ─────────────────────────────────────────────
# Full QA run
# ─────────────────────────────────────────────

def run_all_checks(
    date_range: str = "7d",
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Run all QA checks and return results sorted by severity."""
    db = conn or _conn()
    checks = [
        check_creative_count(db),
        check_duplicate_creatives(db),
        check_creatives_missing_performance(date_range, db),
        check_performance_null_fields(date_range, db),
        check_performance_sanity(date_range, db),
        check_copy_field_coverage(db),
        check_tag_coverage(db),
        check_unknown_tag_rate(db),
        check_unmapped_creatives(db),
        check_pattern_count(db),
        check_pattern_quality(db),
        check_score_distribution(db),
    ]
    # Sort: fail first, then warn, then ok.
    order = {"fail": 0, "warn": 1, "ok": 2}
    return sorted(checks, key=lambda c: order.get(c["status"], 3))
