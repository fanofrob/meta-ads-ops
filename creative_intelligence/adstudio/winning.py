"""
Winning patterns from our real Meta results, shaped for Ad Studio's hook writer.

creative_patterns is built by analysis/patterns.extract_patterns (top-25% ads
by ROAS/CPA, grouped by tag pairs). Here we pick the patterns from the latest
successful Meta sync, give them readable names, and attach a few of the real
winning ads behind each one so Claude can see what the pattern sounds like.
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any

TAG_LABELS = {
    "quality_authenticity": "Quality / authenticity",
    "authenticity_quality": "Authenticity",
    "direct_offer": "Direct offer",
    "social_proof": "Social proof",
    "authority_demo": "Authority / demo",
    "product_hero": "Product hero",
    "direct_response": "Direct response",
    "ugc": "UGC",
    "dco": "Dynamic creative",
}
DIM_LABELS = {
    "hook_type": "hook", "angle": "angle", "archetype": "creative",
    "format": "format", "emotional_trigger": "emotion", "offer_style": "offer",
}


def _tag(value: str) -> str:
    return TAG_LABELS.get(value, value.replace("_", " ").capitalize())


def pattern_label(pattern_name: str) -> str:
    """'hook_type × angle: hook_type:social_proof + angle:fear' → 'Social proof hook · Fear angle'."""
    _, _, body = pattern_name.partition(": ")
    parts = []
    for part in (body or pattern_name).split(" + "):
        dim, _, val = part.partition(":")
        parts.append(f"{_tag(val)} {DIM_LABELS.get(dim, dim)}" if val else part)
    return " · ".join(parts)


def _examples(conn: Any, ids_json: str, limit: int = 3,
              date_range: str = "30d") -> list[dict[str, Any]]:
    """
    The real winning ads behind a pattern. Joined through creative_performance
    by ad_id, which also works for legacy rows whose creatives.id was lost.
    """
    try:
        ids = json.loads(ids_json or "[]")
    except json.JSONDecodeError:
        return []
    out, seen = [], set()
    for cid in ids:
        r = conn.execute(
            "SELECT c.ad_name, c.headline, c.hook_text, c.primary_text, p.roas, p.cpa, p.spend"
            " FROM creative_performance p JOIN creatives c ON c.ad_id = p.ad_id"
            " WHERE p.creative_id = ? AND (c.primary_text <> '' OR c.headline <> '')"
            " ORDER BY p.date_range = ? DESC, p.snapshot_date DESC LIMIT 1",
            (cid, date_range)).fetchone()
        key = ((r["headline"] or "") + (r["primary_text"] or ""))[:200] if r else None
        if r and key not in seen:
            seen.add(key)
            out.append({
                "ad_name": r["ad_name"] or "",
                "headline": r["headline"] or "",
                "text": (r["primary_text"] or r["hook_text"] or "")[:320],
                "roas": r["roas"], "cpa": r["cpa"], "spend": r["spend"],
            })
        if len(out) >= limit:
            break
    return out


def data_status(conn: Any) -> dict[str, Any]:
    """Where the current patterns come from and how old that data is."""
    from creative_intelligence.ingest.meta_sync import latest_run
    last_ok = latest_run(conn, "done")
    last = latest_run(conn)
    rng = (last_ok or {}).get("date_range") or "7d"
    row = conn.execute(
        "SELECT MAX(snapshot_date) AS d, COUNT(*) AS n, COALESCE(SUM(spend),0) AS s,"
        " COALESCE(SUM(revenue),0) AS rev, COALESCE(SUM(purchases),0) AS pur"
        " FROM creative_performance WHERE date_range=? AND snapshot_date ="
        " (SELECT MAX(snapshot_date) FROM creative_performance WHERE date_range=?)",
        (rng, rng)).fetchone()
    age = None
    if row and row["d"]:
        age = (datetime.utcnow().date() - datetime.fromisoformat(row["d"]).date()).days
    return {
        "date_range": rng, "snapshot_date": row["d"] if row else None,
        "n_ads": row["n"] if row else 0, "spend": row["s"] if row else 0,
        "age_days": age, "last_run": last,
        "purchases": row["pur"] if row else 0,
        "account_roas": (row["rev"] / row["s"]) if row and row["s"] else None,
        **_creative_baseline(conn, rng),
    }


def _creative_baseline(conn: Any, date_range: str) -> dict[str, Any]:
    """The bar patterns are measured against: written-creative ads only."""
    from creative_intelligence.analysis.patterns import creative_rows
    _, info = creative_rows(date_range, conn)
    return info


def _strength(p: dict[str, Any]) -> float:
    """Rank key: lift over the account ROAS, scaled by how many purchases back it."""
    if p.get("roas_lift") is not None:
        return (p["roas_lift"] - 1) * math.sqrt(p.get("total_purchases") or 0)
    return float(p.get("winner_count") or 0)          # legacy (count-based) patterns


def current_patterns(conn: Any, limit: int = 12, with_examples: bool = True) -> list[dict[str, Any]]:
    """
    Patterns from the latest successful sync (all of them before the first
    sync). Patterns backed by exactly the same winning ads are one finding
    seen from two tag pairs — only the highest-ranked is kept.
    """
    from creative_intelligence.ingest.meta_sync import latest_run
    last_ok = latest_run(conn, "done")
    sql = "SELECT * FROM creative_patterns WHERE winner_count >= 1"
    params: list[Any] = []
    if last_ok:
        sql += " AND updated_at >= ?"
        params.append(last_ok["started_at"])
    out, seen = [], set()
    for p in sorted((dict(r) for r in conn.execute(sql, params).fetchall()),
                    key=lambda p: (_strength(p), p.get("avg_roas") or 0), reverse=True):
        key = tuple(sorted(json.loads(p.get("example_creative_ids") or "[]")))
        if key in seen:
            continue
        seen.add(key)
        if len(out) >= limit:
            break
        p["label"] = pattern_label(p["pattern_name"])
        if with_examples:
            p["examples"] = _examples(conn, p.get("example_creative_ids"),
                                      date_range=(last_ok or {}).get("date_range") or "7d")
        out.append(p)
    return out


def get_pattern(conn: Any, pattern_id: int) -> dict[str, Any] | None:
    r = conn.execute("SELECT * FROM creative_patterns WHERE id=?", (pattern_id,)).fetchone()
    if not r:
        return None
    p = dict(r)
    p["label"] = pattern_label(p["pattern_name"])
    p["examples"] = _examples(conn, p.get("example_creative_ids"))
    return p


def prompt_block(patterns: list[dict[str, Any]]) -> str:
    """The patterns + their real winning ads, as Claude sees them."""
    lines = ["WINNING PATTERNS FROM OUR REAL META RESULTS (tag pairs whose pooled, spend-weighted "
             "ROAS beats the average of all our written-creative ads). Each hook must follow "
             "exactly ONE of these — set its pattern_id."]
    for p in patterns:
        if p.get("roas_lift") is not None:
            stats = (f"${p['total_spend']:,.0f} spend, {p['total_purchases']:.0f} purchases, "
                     f"ROAS {p['avg_roas']:.2f} ({p['roas_lift']:.2f}× our written-creative average), "
                     f"CPA ${p['avg_cpa']:.0f}, across {p['creative_count']} ads")
            if (p.get("top_ad_share") or 0) >= 0.8:
                stats += " — mostly ONE ad, so treat it as that ad's approach"
        else:
            stats = f"{p['winner_count']} winning ads of {p.get('creative_count') or '?'}"
            if p.get("avg_roas"):
                stats += f", avg ROAS {p['avg_roas']:.2f}"
            if p.get("avg_cpa"):
                stats += f", avg CPA ${p['avg_cpa']:.0f}"
        lines.append(f"\n[pattern_id {p['id']}] {p['label']} — {stats}")
        for ex in p.get("examples") or []:
            copy = " / ".join(x for x in (ex["headline"], ex["text"]) if x)
            lines.append(f'  winning ad: "{copy[:260]}"')
    lines.append("\nUse the winning ads to understand the pattern's approach and voice — never "
                 "copy their wording, and never borrow claims about a different fruit or offer.")
    return "\n".join(lines)
