"""
Pattern extraction from winning creatives.

Identifies recurring "creative DNA" combinations across top performers:
  - hook_type × angle combos
  - archetype × format combos
  - emotional_trigger × offer_style combos

Writes extracted patterns to the creative_patterns table.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from creative_intelligence.db import get_connection
from creative_intelligence.analysis.performance import classify_winners, MIN_SPEND_THRESHOLD

# Minimum number of creatives needed to declare a pattern.
MIN_PATTERN_SIZE = 2


def _conn() -> sqlite3.Connection:
    return get_connection()


def _get_tags_for_creatives(
    creative_ids: list[str],
    db: sqlite3.Connection,
) -> dict[str, dict[str, str]]:
    """Return {creative_id: {tag_type: tag_value}} for a list of creative_ids."""
    if not creative_ids:
        return {}
    placeholders = ",".join("?" * len(creative_ids))
    rows = db.execute(
        f"SELECT creative_id, tag_type, tag_value FROM creative_tags WHERE creative_id IN ({placeholders})",
        creative_ids,
    ).fetchall()
    result: dict[str, dict[str, str]] = {}
    for r in rows:
        result.setdefault(r["creative_id"], {})[r["tag_type"]] = r["tag_value"]
    return result


def _build_pattern_key(tags: dict[str, str], dimensions: list[str]) -> str:
    """Build a hashable pattern key from selected tag dimensions."""
    parts = []
    for dim in dimensions:
        val = tags.get(dim, "unknown")
        parts.append(f"{dim}:{val}")
    return "|".join(parts)


def extract_patterns(
    date_range: str = "7d",
    min_spend: float = MIN_SPEND_THRESHOLD,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Extract creative patterns from winner cohort and persist to DB.

    Returns list of pattern dicts that were upserted.
    """
    db = conn or _conn()
    buckets = classify_winners(date_range=date_range, min_spend=min_spend, conn=db)
    winners = buckets["winners"]

    if not winners:
        return []

    winner_ids = [w["creative_id"] for w in winners]
    all_ids    = [w["creative_id"] for w in winners + buckets["mid"] + buckets["losers"]]
    tag_map    = _get_tags_for_creatives(all_ids, db)

    # Dimension combos to analyze.
    combos: list[tuple[str, list[str]]] = [
        ("hook_type × angle",             ["hook_type", "angle"]),
        ("hook_type × format",            ["hook_type", "format"]),
        ("angle × archetype",             ["angle", "archetype"]),
        ("archetype × format",            ["archetype", "format"]),
        ("emotional_trigger × angle",     ["emotional_trigger", "angle"]),
        ("offer_style × hook_type",       ["offer_style", "hook_type"]),
    ]

    patterns_written = []

    for combo_label, dimensions in combos:
        # Count occurrences across all creatives.
        counts_all: dict[str, list[str]] = {}
        for cid in all_ids:
            key = _build_pattern_key(tag_map.get(cid, {}), dimensions)
            if "unknown" in key:
                continue
            counts_all.setdefault(key, []).append(cid)

        # Count occurrences across winners only.
        counts_winners: dict[str, list[str]] = {}
        for cid in winner_ids:
            key = _build_pattern_key(tag_map.get(cid, {}), dimensions)
            if "unknown" in key:
                continue
            counts_winners.setdefault(key, []).append(cid)

        for pattern_key, w_ids in counts_winners.items():
            if len(w_ids) < MIN_PATTERN_SIZE:
                continue

            all_pattern_ids = counts_all.get(pattern_key, [])

            # Parse tag values from key.
            tag_vals: dict[str, str] = {}
            for part in pattern_key.split("|"):
                k, v = part.split(":", 1)
                tag_vals[k] = v

            # Compute performance averages for these creatives.
            perf_sql = """
                SELECT
                    AVG(p.ctr)  AS avg_ctr,
                    AVG(p.cpa)  AS avg_cpa,
                    AVG(p.roas) AS avg_roas,
                    AVG(p.spend) AS avg_spend
                FROM creative_performance p
                WHERE p.creative_id IN ({placeholders})
                  AND p.date_range = ?
            """.format(placeholders=",".join("?" * len(all_pattern_ids)))

            perf = db.execute(perf_sql, all_pattern_ids + [date_range]).fetchone()

            pattern_name = f"{combo_label}: {pattern_key.replace('|', ' + ')}"

            pattern = {
                "pattern_name":         pattern_name,
                "description":          f"Pattern from {len(w_ids)} winners | {len(all_pattern_ids)} total",
                "hook_type":            tag_vals.get("hook_type"),
                "angle":                tag_vals.get("angle"),
                "format":               tag_vals.get("format"),
                "archetype":            tag_vals.get("archetype"),
                "emotional_trigger":    tag_vals.get("emotional_trigger"),
                "avg_ctr":              perf["avg_ctr"]   if perf else None,
                "avg_cpa":              perf["avg_cpa"]   if perf else None,
                "avg_roas":             perf["avg_roas"]  if perf else None,
                "avg_spend":            perf["avg_spend"] if perf else None,
                "creative_count":       len(all_pattern_ids),
                "winner_count":         len(w_ids),
                "example_creative_ids": json.dumps(w_ids[:5]),
            }
            _upsert_pattern(pattern, db)
            patterns_written.append(pattern)

    db.commit()
    return patterns_written


def _upsert_pattern(pattern: dict[str, Any], db: sqlite3.Connection) -> None:
    existing = db.execute(
        "SELECT id FROM creative_patterns WHERE pattern_name = ?",
        (pattern["pattern_name"],),
    ).fetchone()

    now = datetime.utcnow().isoformat()

    if existing:
        db.execute(
            """UPDATE creative_patterns SET
                description=?, hook_type=?, angle=?, format=?, archetype=?,
                emotional_trigger=?, avg_ctr=?, avg_cpa=?, avg_roas=?, avg_spend=?,
                creative_count=?, winner_count=?, example_creative_ids=?, updated_at=?
               WHERE pattern_name=?""",
            (
                pattern["description"], pattern.get("hook_type"), pattern.get("angle"),
                pattern.get("format"), pattern.get("archetype"), pattern.get("emotional_trigger"),
                pattern.get("avg_ctr"), pattern.get("avg_cpa"), pattern.get("avg_roas"),
                pattern.get("avg_spend"), pattern["creative_count"], pattern["winner_count"],
                pattern["example_creative_ids"], now, pattern["pattern_name"],
            ),
        )
    else:
        db.execute(
            """INSERT INTO creative_patterns
               (pattern_name, description, hook_type, angle, format, archetype,
                emotional_trigger, avg_ctr, avg_cpa, avg_roas, avg_spend,
                creative_count, winner_count, example_creative_ids, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                pattern["pattern_name"], pattern["description"], pattern.get("hook_type"),
                pattern.get("angle"), pattern.get("format"), pattern.get("archetype"),
                pattern.get("emotional_trigger"), pattern.get("avg_ctr"), pattern.get("avg_cpa"),
                pattern.get("avg_roas"), pattern.get("avg_spend"), pattern["creative_count"],
                pattern["winner_count"], pattern["example_creative_ids"], now,
            ),
        )


# Tag pairs analysed by both extractors.
_COMBOS: list[tuple[str, list[str]]] = [
    ("hook_type × angle",             ["hook_type", "angle"]),
    ("hook_type × format",            ["hook_type", "format"]),
    ("angle × archetype",             ["angle", "archetype"]),
    ("archetype × format",            ["archetype", "format"]),
    ("emotional_trigger × angle",     ["emotional_trigger", "angle"]),
    ("offer_style × hook_type",       ["offer_style", "hook_type"]),
]


def is_catalog_ad(headline: str | None, text: str | None) -> bool:
    """Catalog / dynamic-product ads ({{product.name}} templates) have no written
    concept to learn from, and their ROAS is not a creative's doing."""
    return "{{" in (headline or "") or "{{" in (text or "")


def creative_rows(date_range: str, db: sqlite3.Connection) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """Per-ad results for the window, minus catalog ads, plus what was excluded."""
    rows = [dict(r) for r in db.execute(
        "SELECT p.creative_id, p.spend, p.purchases, p.revenue, p.ctr, c.headline, c.primary_text"
        " FROM creative_performance p LEFT JOIN creatives c ON c.id = p.creative_id"
        " WHERE p.date_range=? AND p.spend > 0 AND p.snapshot_date = (SELECT MAX(snapshot_date)"
        " FROM creative_performance WHERE date_range=?)", (date_range, date_range)).fetchall()]
    keep = [r for r in rows if not is_catalog_ad(r["headline"], r["primary_text"])]
    dropped = [r for r in rows if is_catalog_ad(r["headline"], r["primary_text"])]
    spend = sum(r["spend"] or 0 for r in keep)
    return keep, {
        "catalog_ads": len(dropped),
        "catalog_spend": sum(r["spend"] or 0 for r in dropped),
        "creative_roas": (sum(r["revenue"] or 0 for r in keep) / spend) if spend else 0.0,
    }


def extract_weighted_patterns(
    date_range: str = "90d",
    min_spend: float | None = None,
    min_purchases: float = 8,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """
    Spend-weighted winning patterns.

    Instead of counting "winner" ads equally (where a $15 ad with one lucky
    sale weighs as much as a $13k ad with 179), pool every ad's real spend,
    purchases and revenue per tag pair. A pattern wins when its pooled ROAS
    beats the pooled ROAS of all written-creative ads in the window (catalog
    / {{product.name}} ads are left out of both — they have no concept to
    learn from), with enough spend and purchases behind it to mean something:

      min_spend      default: max($250, 3% of window spend)
      min_purchases  default: 8
      and at least 2 ads that spent.

    Stored per pattern: pooled avg_roas / avg_cpa, total spend / purchases /
    revenue, roas_lift (pooled ROAS ÷ account ROAS), top_ad_share (share of
    the pattern's spend from its single biggest ad — high means "this is
    mostly one ad"), winner_count (ads in it beating the account ROAS), and
    example_creative_ids (its top ads by revenue).
    """
    db = conn or _conn()
    rows, _ = creative_rows(date_range, db)
    total_spend = sum(r["spend"] or 0 for r in rows)
    total_rev = sum(r["revenue"] or 0 for r in rows)
    if not rows or not total_spend or not total_rev:
        return []
    base_roas = total_rev / total_spend
    min_spend = max(250.0, 0.03 * total_spend) if min_spend is None else min_spend
    run = datetime.utcnow().isoformat()
    tag_map = _get_tags_for_creatives([r["creative_id"] for r in rows], db)

    written: list[dict[str, Any]] = []
    for combo_label, dims in _COMBOS:
        groups: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            key = _build_pattern_key(tag_map.get(r["creative_id"], {}), dims)
            if "unknown" not in key:
                groups.setdefault(key, []).append(r)
        for key, ads in groups.items():
            spend = sum(a["spend"] or 0 for a in ads)
            purchases = sum(a["purchases"] or 0 for a in ads)
            revenue = sum(a["revenue"] or 0 for a in ads)
            if len(ads) < 2 or spend < min_spend or purchases < min_purchases:
                continue
            roas = revenue / spend
            if roas <= base_roas:
                continue
            tag_vals = dict(part.split(":", 1) for part in key.split("|"))
            top = sorted(ads, key=lambda a: ((a["revenue"] or 0), (a["spend"] or 0)), reverse=True)
            beating = [a for a in ads if (a["spend"] or 0) and (a["revenue"] or 0) / a["spend"] > base_roas]
            ctr_w = sum((a["ctr"] or 0) * a["spend"] for a in ads) / spend
            pattern = {
                "pattern_name":         f"{combo_label}: {key.replace('|', ' + ')}",
                "description":          (f"Pooled ROAS {roas:.2f} vs {base_roas:.2f} for all written-creative ads over "
                                         f"${spend:,.0f} / {purchases:.0f} purchases ({date_range})"),
                "hook_type":            tag_vals.get("hook_type"),
                "angle":                tag_vals.get("angle"),
                "format":               tag_vals.get("format"),
                "archetype":            tag_vals.get("archetype"),
                "emotional_trigger":    tag_vals.get("emotional_trigger"),
                "avg_ctr":              ctr_w,
                "avg_cpa":              spend / purchases,
                "avg_roas":             roas,
                "avg_spend":            spend / len(ads),
                "creative_count":       len(ads),
                "winner_count":         len(beating),
                "example_creative_ids": json.dumps([a["creative_id"] for a in top[:5]]),
                "total_spend":          spend,
                "total_purchases":      purchases,
                "total_revenue":        revenue,
                "roas_lift":            roas / base_roas,
                "top_ad_share":         max(a["spend"] or 0 for a in ads) / spend,
                "date_range":           date_range,
            }
            _upsert_pattern(pattern, db)
            db.execute(
                "UPDATE creative_patterns SET total_spend=?, total_purchases=?, total_revenue=?,"
                " roas_lift=?, top_ad_share=?, date_range=?, extract_run=? WHERE pattern_name=?",
                (spend, purchases, revenue, pattern["roas_lift"], pattern["top_ad_share"],
                 date_range, run, pattern["pattern_name"]))
            written.append(pattern)
    db.commit()
    return written


def get_patterns(
    min_winners: int = 1,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Return all patterns sorted by winner_count desc."""
    db = conn or _conn()
    rows = db.execute(
        "SELECT * FROM creative_patterns WHERE winner_count >= ? ORDER BY winner_count DESC",
        (min_winners,),
    ).fetchall()
    return [dict(r) for r in rows]
