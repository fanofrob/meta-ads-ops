"""
Creative asset scoring engine.

Computes a 0–100 quality_score for render assets based on:
  - variant_affinity (0–40): how often this variant type gets favorited for this product
  - product_performance (0–30): overall approval/favorite rate across this product's assets
  - concept_strength (0–20): D+C+PS clarity score from the originating hook (if available)
  - freshness (0–10): slight recency bonus, decays linearly over 30 days

Public API
----------
score_asset(asset_id, conn)  → dict  — compute + persist score for one asset, return breakdown
score_product_assets(product_id, conn) → list[dict]  — rescore all assets for a product
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


# ─────────────────────────────────────────────
# Component scorers
# ─────────────────────────────────────────────

def _variant_affinity(variant_label: str, product_id: str | None, conn: sqlite3.Connection) -> float:
    """0–40: how often this variant is favorited for this product (or globally if no product)."""
    if product_id:
        row = conn.execute(
            """SELECT
                COUNT(*) AS total,
                SUM(ra.is_favorite) AS favs
               FROM render_assets ra
               JOIN render_outputs ro ON ro.id = ra.render_output_id
               JOIN production_outputs po ON po.id = ro.source_production_output_id
               WHERE ra.variant_label = ? AND po.product_id = ?""",
            (variant_label, product_id),
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT COUNT(*) AS total, SUM(is_favorite) AS favs
               FROM render_assets WHERE variant_label = ?""",
            (variant_label,),
        ).fetchone()

    total = row["total"] or 0
    favs  = row["favs"]  or 0
    if total < 3:
        # Not enough data — give neutral score of 20/40
        return 20.0
    rate = favs / total
    return round(min(40.0, rate * 40.0 * 1.2), 1)  # 1.2x multiplier rewards high-performing variants


def _product_performance(product_id: str | None, conn: sqlite3.Connection) -> float:
    """0–30: overall approval/favorite rate for this product."""
    if not product_id:
        return 15.0  # neutral

    row = conn.execute(
        """SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN ra.is_favorite = 1 OR ra.review_status = 'approved' THEN 1 ELSE 0 END) AS good
           FROM render_assets ra
           JOIN render_outputs ro ON ro.id = ra.render_output_id
           JOIN production_outputs po ON po.id = ro.source_production_output_id
           WHERE po.product_id = ?""",
        (product_id,),
    ).fetchone()

    total = row["total"] or 0
    good  = row["good"]  or 0
    if total < 5:
        return 15.0  # neutral
    rate = good / total
    return round(min(30.0, rate * 30.0 * 1.1), 1)


def _concept_strength(asset_id: int, conn: sqlite3.Connection) -> float:
    """0–20: D+C+PS clarity score from the originating hook, if traceable."""
    # Trace: render_asset → render_output → production_output → copilot_iteration → dcp_clarity
    try:
        row = conn.execute(
            """SELECT ci.dcp_clarity
               FROM render_assets ra
               JOIN render_outputs ro ON ro.id = ra.render_output_id
               JOIN production_outputs po ON po.id = ro.source_production_output_id
               JOIN copilot_iterations ci ON ci.id = po.source_iteration_id
               WHERE ra.id = ? AND ci.dcp_clarity IS NOT NULL
               LIMIT 1""",
            (asset_id,),
        ).fetchone()
        if not row or not row["dcp_clarity"]:
            return 10.0  # neutral

        clarity = str(row["dcp_clarity"]).lower()
        # Parse simple quality signals from the clarity text
        if any(w in clarity for w in ("strong", "excellent", "clear", "sharp", "compelling")):
            return 20.0
        if any(w in clarity for w in ("good", "solid", "works", "effective")):
            return 16.0
        if any(w in clarity for w in ("weak", "vague", "unclear", "poor", "missing")):
            return 5.0
        return 10.0
    except Exception:
        return 10.0


def _freshness(created_at: str | None) -> float:
    """0–10: recency bonus, decays linearly over 30 days."""
    if not created_at:
        return 5.0
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_days = max(0, (now - created).total_seconds() / 86400)
        return round(max(0.0, 10.0 * (1 - age_days / 30)), 1)
    except Exception:
        return 5.0


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def score_asset(asset_id: int, conn: sqlite3.Connection) -> dict[str, Any]:
    """Compute quality_score for one asset, persist it, and return the breakdown."""
    row = conn.execute(
        """SELECT ra.id, ra.variant_label, ra.created_at,
                  ro.source_production_output_id,
                  po.product_id
           FROM render_assets ra
           JOIN render_outputs ro ON ro.id = ra.render_output_id
           LEFT JOIN production_outputs po ON po.id = ro.source_production_output_id
           WHERE ra.id = ?""",
        (asset_id,),
    ).fetchone()

    if not row:
        return {"error": f"asset {asset_id} not found"}

    product_id    = row["product_id"]
    variant_label = row["variant_label"] or ""

    va = _variant_affinity(variant_label, product_id, conn)
    pp = _product_performance(product_id, conn)
    cs = _concept_strength(asset_id, conn)
    fr = _freshness(row["created_at"])

    total = round(va + pp + cs + fr, 1)
    breakdown = {
        "variant_affinity":    va,
        "product_performance": pp,
        "concept_strength":    cs,
        "freshness":           fr,
        "total":               total,
    }

    conn.execute(
        "UPDATE render_assets SET quality_score=?, score_breakdown_json=? WHERE id=?",
        (total, json.dumps(breakdown), asset_id),
    )
    conn.commit()

    return {"asset_id": asset_id, "quality_score": total, "breakdown": breakdown}


def score_product_assets(product_id: str, conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Recompute scores for every render_asset belonging to a product."""
    rows = conn.execute(
        """SELECT ra.id
           FROM render_assets ra
           JOIN render_outputs ro ON ro.id = ra.render_output_id
           JOIN production_outputs po ON po.id = ro.source_production_output_id
           WHERE po.product_id = ?""",
        (product_id,),
    ).fetchall()

    return [score_asset(r["id"], conn) for r in rows]
