"""
Creative scoring.

Scores both existing creatives (historical) and proposed/generated assets
(hooks, scripts) on a 0–100 scale across four dimensions:

  1. performance_score   — based on real CTR/ROAS/CPA relative to account averages
  2. pattern_match_score — similarity to winning patterns (tag overlap)
  3. structural_score    — copy quality heuristics (length, CTA presence, etc.)
  4. overall_score       — weighted composite of the above

Scores are persisted in creative_scores for trending and filtering.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from creative_intelligence.db import get_connection

# Weights for overall composite score.
_WEIGHTS = {
    "performance":    0.50,
    "pattern_match":  0.30,
    "structural":     0.20,
}


def _conn() -> sqlite3.Connection:
    return get_connection()


# ─────────────────────────────────────────────
# Performance scoring (historical creatives)
# ─────────────────────────────────────────────

def _performance_score(
    creative_id: str,
    date_range: str,
    account_averages: dict[str, float],
    db: sqlite3.Connection,
) -> float:
    """Score 0–100 based on performance vs account averages."""
    row = db.execute(
        """SELECT ctr, roas, cpa, spend FROM creative_performance
           WHERE creative_id = ? AND date_range = ?""",
        (creative_id, date_range),
    ).fetchone()

    if not row:
        return 0.0

    score = 0.0
    weight_sum = 0.0

    avg_ctr = account_averages.get("avg_ctr") or 0
    avg_roas = account_averages.get("avg_roas") or 0
    avg_cpa = account_averages.get("avg_cpa") or 0

    if row["ctr"] is not None and avg_ctr:
        ratio = row["ctr"] / avg_ctr
        score += min(ratio * 50, 50) * 0.4
        weight_sum += 0.4

    if row["roas"] is not None and avg_roas:
        ratio = row["roas"] / avg_roas
        score += min(ratio * 50, 50) * 0.4
        weight_sum += 0.4

    if row["cpa"] is not None and avg_cpa:
        ratio = avg_cpa / max(row["cpa"], 0.01)  # lower CPA = better
        score += min(ratio * 50, 50) * 0.2
        weight_sum += 0.2

    if weight_sum == 0:
        return 0.0

    # Scale to 0–100
    return round(min(score / weight_sum * 2, 100), 1)


# ─────────────────────────────────────────────
# Pattern match scoring
# ─────────────────────────────────────────────

def _pattern_match_score(
    tags: dict[str, str],
    winning_patterns: list[dict],
) -> float:
    """Score 0–100 based on how many winning pattern dimensions match."""
    if not winning_patterns or not tags:
        return 0.0

    best_score = 0.0
    for pattern in winning_patterns:
        matches = 0
        total   = 0
        for dim in ("hook_type", "angle", "format", "archetype", "emotional_trigger"):
            pval = pattern.get(dim)
            tval = tags.get(dim)
            if pval and pval != "unknown":
                total += 1
                if tval == pval:
                    matches += 1
        if total:
            s = matches / total * 100
            # Bonus for patterns with many winners.
            winner_bonus = min(pattern.get("winner_count", 1) * 2, 20)
            best_score = max(best_score, min(s + winner_bonus, 100))

    return round(best_score, 1)


# ─────────────────────────────────────────────
# Structural scoring (copy quality heuristics)
# ─────────────────────────────────────────────

def _structural_score(record: dict[str, Any]) -> float:
    """Score 0–100 based on copy completeness and quality signals."""
    score = 0.0

    hook = record.get("hook_text") or ""
    primary = record.get("primary_text") or record.get("content") or ""
    headline = record.get("headline") or ""
    cta = record.get("cta") or record.get("cta_type") or ""

    # Hook presence and length (ideal: 10–120 chars)
    if hook:
        score += 20
        if 10 <= len(hook) <= 120:
            score += 10

    # Primary text presence (ideal: 50–250 chars)
    if primary:
        score += 20
        if 50 <= len(primary) <= 250:
            score += 10

    # Headline presence
    if headline:
        score += 15

    # CTA presence
    if cta and cta not in ("unknown", "other", ""):
        score += 15

    # Question mark in hook (curiosity signal)
    if "?" in hook:
        score += 5

    # Number in hook or headline (specificity signal)
    import re
    if re.search(r"\d", hook + headline):
        score += 5

    return round(min(score, 100), 1)


# ─────────────────────────────────────────────
# Composite scoring
# ─────────────────────────────────────────────

def _composite(perf: float, pattern: float, structural: float) -> float:
    return round(
        perf * _WEIGHTS["performance"]
        + pattern * _WEIGHTS["pattern_match"]
        + structural * _WEIGHTS["structural"],
        1,
    )


def _get_account_averages(date_range: str, db: sqlite3.Connection) -> dict[str, float]:
    row = db.execute(
        """SELECT AVG(ctr) AS avg_ctr, AVG(roas) AS avg_roas, AVG(cpa) AS avg_cpa
           FROM creative_performance WHERE date_range = ?""",
        (date_range,),
    ).fetchone()
    return dict(row) if row else {}


def _get_tags(creative_id: str, db: sqlite3.Connection) -> dict[str, str]:
    rows = db.execute(
        "SELECT tag_type, tag_value FROM creative_tags WHERE creative_id = ?",
        (creative_id,),
    ).fetchall()
    return {r["tag_type"]: r["tag_value"] for r in rows}


def _save_score(
    score_type: str,
    score: float,
    rationale: str,
    creative_id: str | None,
    generated_hook_id: int | None,
    generated_script_id: int | None,
    metadata: dict,
    db: sqlite3.Connection,
) -> int:
    cursor = db.execute(
        """INSERT INTO creative_scores
           (creative_id, generated_hook_id, generated_script_id,
            score_type, score, rationale, metadata)
           VALUES (?,?,?,?,?,?,?)""",
        (
            creative_id, generated_hook_id, generated_script_id,
            score_type, score, rationale, json.dumps(metadata),
        ),
    )
    return cursor.lastrowid


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def score_creative(
    creative_id: str,
    date_range: str = "7d",
    persist: bool = True,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Score an existing creative. Returns all score components."""
    db = conn or _conn()

    rec = db.execute("SELECT * FROM creatives WHERE id = ?", (creative_id,)).fetchone()
    if not rec:
        return {"error": f"Creative {creative_id} not found"}

    record = dict(rec)
    account_avgs = _get_account_averages(date_range, db)
    tags         = _get_tags(creative_id, db)

    from creative_intelligence.analysis.patterns import get_patterns
    winning_patterns = get_patterns(min_winners=1, conn=db)

    perf_score      = _performance_score(creative_id, date_range, account_avgs, db)
    pattern_score   = _pattern_match_score(tags, winning_patterns)
    structural_score = _structural_score(record)
    overall         = _composite(perf_score, pattern_score, structural_score)

    rationale = (
        f"Performance: {perf_score}/100 | "
        f"Pattern match: {pattern_score}/100 | "
        f"Structural: {structural_score}/100"
    )

    if persist:
        with db:
            for stype, sval in [
                ("performance",   perf_score),
                ("pattern_match", pattern_score),
                ("structural",    structural_score),
                ("overall",       overall),
            ]:
                _save_score(stype, sval, rationale, creative_id, None, None,
                            {"date_range": date_range}, db)

    return {
        "creative_id":      creative_id,
        "performance":      perf_score,
        "pattern_match":    pattern_score,
        "structural":       structural_score,
        "overall":          overall,
        "rationale":        rationale,
    }


def score_generated_hook(
    hook_id: int,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Score a generated hook on structural and pattern match dimensions."""
    db = conn or _conn()

    row = db.execute("SELECT * FROM generated_hooks WHERE id = ?", (hook_id,)).fetchone()
    if not row:
        return {"error": f"Hook {hook_id} not found"}

    hook = dict(row)
    tags = {
        "hook_type": hook.get("hook_type") or "unknown",
        "angle":     hook.get("angle") or "unknown",
    }

    from creative_intelligence.analysis.patterns import get_patterns
    winning_patterns = get_patterns(min_winners=1, conn=db)

    pattern_score    = _pattern_match_score(tags, winning_patterns)
    structural_score = _structural_score({"hook_text": hook.get("hook_text", "")})
    overall          = round(pattern_score * 0.5 + structural_score * 0.5, 1)

    rationale = f"Pattern match: {pattern_score}/100 | Structural: {structural_score}/100"

    with db:
        for stype, sval in [
            ("pattern_match", pattern_score),
            ("structural",    structural_score),
            ("overall",       overall),
        ]:
            _save_score(stype, sval, rationale, None, hook_id, None, {}, db)
        db.execute("UPDATE generated_hooks SET score = ? WHERE id = ?", (overall, hook_id))

    return {
        "hook_id":       hook_id,
        "pattern_match": pattern_score,
        "structural":    structural_score,
        "overall":       overall,
    }


def score_all_creatives(
    date_range: str = "7d",
    min_spend: float = 10.0,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Score all creatives above min_spend. Returns list of score dicts."""
    db = conn or _conn()
    rows = db.execute(
        """SELECT DISTINCT c.id FROM creatives c
           JOIN creative_performance p ON p.creative_id = c.id
           WHERE p.date_range = ? AND p.spend >= ?""",
        (date_range, min_spend),
    ).fetchall()
    return [score_creative(r["id"], date_range, persist=True, conn=db) for r in rows]
