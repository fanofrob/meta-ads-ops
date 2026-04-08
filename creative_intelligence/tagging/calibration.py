"""
Tagging calibration utilities.

Use these to:
  1. Inspect what keywords triggered (or failed to trigger) for real creatives.
  2. Identify which creatives fall through to "unknown" and why.
  3. Test new keyword rules against your live data before committing them.
  4. Detect overfitting — rules that match too many OR too few creatives.

SAFE: all operations are read-only unless you call save_updated_rules() explicitly.
"""
from __future__ import annotations

import re
import textwrap
from typing import Any

from creative_intelligence.db import get_connection
from creative_intelligence.tagging.rule_tagger import (
    _HOOK_TYPE_RULES,
    _ANGLE_RULES,
    _ARCHETYPE_RULES,
    _EMOTIONAL_TRIGGER_RULES,
    _OFFER_STYLE_RULES,
    _CTA_RULES,
    _combined_text,
    _first_match,
    tag_creative,
)

_RULE_SETS = {
    "hook_type":          _HOOK_TYPE_RULES,
    "angle":              _ANGLE_RULES,
    "archetype":          _ARCHETYPE_RULES,
    "emotional_trigger":  _EMOTIONAL_TRIGGER_RULES,
    "offer_style":        _OFFER_STYLE_RULES,
    "cta_type":           _CTA_RULES,
}


# ─────────────────────────────────────────────
# 1. Explain why a creative was tagged a certain way
# ─────────────────────────────────────────────

def explain_tagging(creative_id: str, conn=None) -> dict[str, Any]:
    """
    Return a full explanation of how every tag dimension was assigned
    for a given creative_id.

    Returns:
        {
          "creative_id": str,
          "ad_name": str,
          "combined_text": str,
          "dimensions": {
            "hook_type": {
              "assigned": "curiosity",
              "matched_pattern": "secret",
              "matched_rule_index": 1,
              "all_tested": [...],
            },
            ...
          }
        }
    """
    db = conn or get_connection()
    row = db.execute(
        "SELECT * FROM creatives WHERE id = ?", (creative_id,)
    ).fetchone()
    if not row:
        return {"error": f"Creative {creative_id!r} not found"}

    record  = dict(row)
    text    = _combined_text(record)
    fmt     = (record.get("format") or "").lower()

    dimensions: dict[str, Any] = {}

    for dim, rules in _RULE_SETS.items():
        tested = []
        matched_value   = "unknown"
        matched_pattern = None
        matched_idx     = None

        for i, (tag_value, patterns) in enumerate(rules):
            hit = None
            for pat in patterns:
                if re.search(pat, text, re.IGNORECASE):
                    hit = pat
                    break
            tested.append({
                "tag_value": tag_value,
                "matched":   hit is not None,
                "pattern":   hit,
            })
            if hit and matched_value == "unknown":
                matched_value   = tag_value
                matched_pattern = hit
                matched_idx     = i

        dimensions[dim] = {
            "assigned":          matched_value,
            "matched_pattern":   matched_pattern,
            "matched_rule_index": matched_idx,
            "all_tested":        tested,
        }

    return {
        "creative_id":   creative_id,
        "ad_name":       record.get("ad_name", ""),
        "combined_text": text[:400],
        "dimensions":    dimensions,
    }


def print_explanation(creative_id: str, conn=None) -> None:
    """Human-readable tagging explanation for a creative."""
    result = explain_tagging(creative_id, conn)
    if "error" in result:
        print(f"  Error: {result['error']}")
        return

    print(f"\nCreative: {result['creative_id']} — {result['ad_name']}")
    print(f"Text (first 400 chars):\n  {result['combined_text'][:400]}\n")

    for dim, info in result["dimensions"].items():
        status = "✓" if info["assigned"] != "unknown" else "—"
        print(f"  {status} {dim:<22} → {info['assigned']:<20}  "
              f"(matched: {info['matched_pattern'] or 'none'})")


# ─────────────────────────────────────────────
# 2. Test a new keyword before adding it to rules
# ─────────────────────────────────────────────

def test_keyword(
    keyword: str,
    dimension: str,
    limit: int = 20,
    conn=None,
) -> dict[str, Any]:
    """
    Test a regex keyword against all creatives and return:
      - how many would match
      - sample matches (first 5)
      - sample non-matches from 'unknown' pool (first 5)

    Use this BEFORE adding a keyword to rule_tagger.py.

    Example:
        test_keyword("obsessed", "hook_type")
    """
    db = conn or get_connection()
    rows = db.execute(
        "SELECT id, ad_name, hook_text, primary_text, headline FROM creatives"
    ).fetchall()

    matches     = []
    non_matches = []

    for row in rows:
        record = dict(row)
        text   = _combined_text(record)
        if re.search(keyword, text, re.IGNORECASE):
            matches.append(record)
        else:
            non_matches.append(record)

    pct = len(matches) / max(len(rows), 1) * 100

    # Check if any already-tagged "unknown" creatives would now be matched.
    unknowns_rescued = db.execute(
        """SELECT c.id, c.ad_name, c.hook_text FROM creatives c
           JOIN creative_tags t ON t.creative_id = c.id
                AND t.tag_type = ? AND t.tag_value = 'unknown'""",
        (dimension,),
    ).fetchall()
    rescued = [
        dict(r) for r in unknowns_rescued
        if re.search(keyword, _combined_text(dict(r)), re.IGNORECASE)
    ]

    return {
        "keyword":           keyword,
        "dimension":         dimension,
        "total_creatives":   len(rows),
        "would_match":       len(matches),
        "match_pct":         round(pct, 1),
        "rescued_unknowns":  len(rescued),
        "sample_matches":    matches[:5],
        "sample_non_matches": non_matches[:5],
        "sample_rescued":    rescued[:5],
        "assessment":        (
            "⚠ Overly broad — matches > 30% of creatives" if pct > 30 else
            "✓ Good signal" if pct < 15 else
            "OK — moderate coverage"
        ),
    }


def print_keyword_test(keyword: str, dimension: str, conn=None) -> None:
    result = test_keyword(keyword, dimension, conn=conn)
    print(f"\nKeyword test: {keyword!r}  dimension: {dimension}")
    print(f"  Would match:     {result['would_match']} of {result['total_creatives']} "
          f"({result['match_pct']}%)  {result['assessment']}")
    print(f"  Rescued unknowns: {result['rescued_unknowns']}")
    if result["sample_matches"]:
        print("\n  Sample matches:")
        for r in result["sample_matches"][:3]:
            hook = (r.get("hook_text") or "")[:80] or "(no hook)"
            print(f"    - {r.get('ad_name', '')[:40]}: {hook}")
    if result["sample_rescued"]:
        print("\n  Previously-unknown creatives this would fix:")
        for r in result["sample_rescued"][:3]:
            hook = (r.get("hook_text") or "")[:80] or "(no hook)"
            print(f"    - {r.get('ad_name', '')[:40]}: {hook}")


# ─────────────────────────────────────────────
# 3. Coverage summary: how many creatives each rule fires on
# ─────────────────────────────────────────────

def rule_coverage_report(conn=None) -> list[dict[str, Any]]:
    """
    For each rule in each dimension, count how many creatives it would fire on.
    Helps spot dead rules (0 matches) and overfitted rules (> 50% match).
    """
    db = conn or get_connection()
    rows = db.execute(
        "SELECT id, hook_text, primary_text, headline, ad_name, description FROM creatives"
    ).fetchall()
    records = [dict(r) for r in rows]
    total   = len(records)

    report = []
    for dim, rules in _RULE_SETS.items():
        for tag_value, patterns in rules:
            count = 0
            for rec in records:
                text = _combined_text(rec)
                for pat in patterns:
                    if re.search(pat, text, re.IGNORECASE):
                        count += 1
                        break
            pct = count / max(total, 1) * 100
            report.append({
                "dimension":  dim,
                "tag_value":  tag_value,
                "match_count": count,
                "match_pct":  round(pct, 1),
                "status": (
                    "dead"     if count == 0    else
                    "overfit"  if pct > 50      else
                    "ok"
                ),
            })

    return sorted(report, key=lambda r: (r["status"] != "dead", r["status"] != "overfit", -r["match_pct"]))


def print_coverage_report(conn=None) -> None:
    report = rule_coverage_report(conn)
    print("\nRule Coverage Report\n" + "=" * 50)
    current_dim = None
    for r in report:
        if r["dimension"] != current_dim:
            current_dim = r["dimension"]
            print(f"\n  {current_dim}:")
        flag = " ← DEAD" if r["status"] == "dead" else (" ← OVERFIT" if r["status"] == "overfit" else "")
        print(f"    {r['tag_value']:<25} {r['match_count']:>4} ({r['match_pct']:>5.1f}%){flag}")


# ─────────────────────────────────────────────
# 4. Simulate retagging with modified rules (without writing)
# ─────────────────────────────────────────────

def simulate_retag(
    new_rules: dict[str, list[tuple[str, list[str]]]],
    conn=None,
) -> dict[str, Any]:
    """
    Dry-run: simulate applying updated rules to all creatives and report
    how tag distributions would change.

    new_rules: {dimension: [(tag_value, [patterns]), ...]}
    Only dimensions included in new_rules are modified; others use current rules.

    Returns a diff of before/after tag distributions.
    """
    db = conn or get_connection()
    rows = db.execute(
        "SELECT id, hook_text, primary_text, headline, ad_name, description FROM creatives"
    ).fetchall()

    merged_rules = {**_RULE_SETS, **new_rules}

    before: dict[str, dict[str, int]] = {dim: {} for dim in merged_rules}
    after:  dict[str, dict[str, int]] = {dim: {} for dim in merged_rules}

    for row in rows:
        record = dict(row)
        text   = _combined_text(record)

        for dim, rules in merged_rules.items():
            # Before: use existing rules from _RULE_SETS
            b_val = _first_match(text, _RULE_SETS.get(dim, rules), "unknown")
            # After: use new rules
            a_val = _first_match(text, rules, "unknown")

            before[dim][b_val] = before[dim].get(b_val, 0) + 1
            after[dim][a_val]  = after[dim].get(a_val, 0) + 1

    # Compute diff
    diffs = {}
    for dim in merged_rules:
        dim_diff = {}
        all_vals = set(before[dim]) | set(after[dim])
        for val in all_vals:
            b = before[dim].get(val, 0)
            a = after[dim].get(val, 0)
            if b != a:
                dim_diff[val] = {"before": b, "after": a, "delta": a - b}
        if dim_diff:
            diffs[dim] = dim_diff

    total_unknown_before = sum(before[d].get("unknown", 0) for d in before)
    total_unknown_after  = sum(after[d].get("unknown", 0) for d in after)

    return {
        "total_creatives":      len(rows),
        "total_unknown_before": total_unknown_before,
        "total_unknown_after":  total_unknown_after,
        "unknown_delta":        total_unknown_after - total_unknown_before,
        "dimension_diffs":      diffs,
    }


def print_simulation(new_rules: dict, conn=None) -> None:
    result = simulate_retag(new_rules, conn)
    total = result["total_creatives"]
    ub = result["total_unknown_before"]
    ua = result["total_unknown_after"]
    delta = result["unknown_delta"]

    sign = "+" if delta > 0 else ""
    print(f"\nSimulation: {total} creatives")
    print(f"  Unknowns before: {ub} | after: {ua} | delta: {sign}{delta}")

    if not result["dimension_diffs"]:
        print("  No changes — new rules produce identical output.")
        return

    print("\n  Changes by dimension:")
    for dim, diffs in result["dimension_diffs"].items():
        print(f"\n  {dim}:")
        for val, d in sorted(diffs.items(), key=lambda x: abs(x[1]["delta"]), reverse=True):
            sign = "+" if d["delta"] > 0 else ""
            print(f"    {val:<25} {d['before']:>4} → {d['after']:>4}  ({sign}{d['delta']})")
