"""
Product enricher.

Links creatives to products in the DB using keyword matching,
then pulls product context (benefits, angles, offers) for use in generation prompts.

Uses campaign_profitability.py matching logic as a reference pattern
but operates entirely within the creative intelligence DB — no modifications
to src/campaign_profitability.py.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from creative_intelligence.db import get_connection


def _conn() -> sqlite3.Connection:
    return get_connection()


def _get_all_products(db: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in db.execute(
        "SELECT id, name FROM products WHERE active = 1"
    ).fetchall()]


def _match_product(
    campaign_name: str,
    ad_name: str,
    products: list[dict[str, Any]],
) -> str | None:
    """Best-effort keyword match between creative names and product names.

    Returns product_id or None.
    """
    text = f"{campaign_name} {ad_name}".lower()
    # Exact substring match first.
    for p in products:
        if p["name"].lower() in text:
            return p["id"]
    # Token overlap match (product name tokens in text).
    for p in products:
        tokens = [t for t in p["name"].lower().split() if len(t) > 3]
        if tokens and all(t in text for t in tokens):
            return p["id"]
    return None


def link_creatives_to_products(conn: sqlite3.Connection | None = None) -> int:
    """Match all unlinked creatives to products and set product_id.

    Returns number of creatives updated.
    """
    db = conn or _conn()
    products = _get_all_products(db)
    if not products:
        return 0

    unlinked = db.execute(
        "SELECT id, ad_name, campaign_name FROM creatives WHERE product_id IS NULL"
    ).fetchall()

    updated = 0
    for row in unlinked:
        pid = _match_product(row["campaign_name"] or "", row["ad_name"] or "", products)
        if pid:
            db.execute("UPDATE creatives SET product_id = ? WHERE id = ?", (pid, row["id"]))
            updated += 1

    db.commit()
    return updated


def get_product_context(
    product_id: str,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Return full product context for use in generation prompts.

    Returns:
      {
        product: {...},
        benefits: [{type, content, priority}, ...],
        angles: [{name, description, emotional_trigger, proof_points}, ...],
        offers: [{type, description, discount_pct}, ...],
        kb_documents: [{doc_type, title, content}, ...]
      }
    """
    db = conn or _conn()

    product = db.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not product:
        return {}

    benefits = [dict(r) for r in db.execute(
        "SELECT benefit_type, content, priority FROM product_benefits WHERE product_id = ? ORDER BY priority DESC",
        (product_id,),
    ).fetchall()]

    angles = [dict(r) for r in db.execute(
        "SELECT angle_name, angle_description, target_persona, emotional_trigger, proof_points FROM product_angles WHERE product_id = ?",
        (product_id,),
    ).fetchall()]

    offers = [dict(r) for r in db.execute(
        "SELECT offer_type, offer_description, discount_pct FROM product_offers WHERE product_id = ? AND active = 1",
        (product_id,),
    ).fetchall()]

    kb_docs = [dict(r) for r in db.execute(
        "SELECT doc_type, title, content FROM product_kb_documents WHERE product_id = ? ORDER BY created_at DESC LIMIT 5",
        (product_id,),
    ).fetchall()]

    return {
        "product":      dict(product),
        "benefits":     benefits,
        "angles":       angles,
        "offers":       offers,
        "kb_documents": kb_docs,
    }


def build_prompt_context_block(product_id: str, conn: sqlite3.Connection | None = None) -> str:
    """Format product context as a text block suitable for injection into prompts."""
    ctx = get_product_context(product_id, conn)
    if not ctx:
        return ""

    p = ctx["product"]
    product_label = p.get("category") or p["name"]
    lines = [
        f"Name: {p['name']}",          # human name for use in copy
        f"Product type: {product_label}",  # category for context only, not for use in hooks
    ]

    if p.get("price"):
        lines.append(f"Price: ${p['price']:.2f}")

    if p.get("short_description"):
        lines.append(f"Description: {p['short_description']}")
    if p.get("positioning"):
        lines.append(f"Positioning: {p['positioning']}")
    if p.get("target_persona"):
        lines.append(f"Target persona: {p['target_persona']}")

    features = [b["content"] for b in ctx["benefits"] if b["benefit_type"] == "feature"][:5]
    if features:
        lines.append("Key features: " + " | ".join(features))

    pain_points = [b["content"] for b in ctx["benefits"] if b["benefit_type"] == "pain_point"][:2]
    if pain_points:
        lines.append("Pain points solved: " + " | ".join(pain_points))

    outcomes = [b["content"] for b in ctx["benefits"] if b["benefit_type"] == "desired_outcome"][:2]
    if outcomes:
        lines.append("Desired outcomes: " + " | ".join(outcomes))

    if ctx["offers"]:
        offer = ctx["offers"][0]
        lines.append(f"Current offer: {offer.get('offer_description', offer['offer_type'])}")

    # Only include q.* quality/flavour signals — skip all ops/logistics tags
    raw_tags = (p.get("tags") or "").strip()
    if raw_tags:
        quality_tags = [
            t.strip().removeprefix("q.")
            for t in raw_tags.split(",")
            if t.strip().startswith("q.")
        ]
        if quality_tags:
            lines.append("Quality signals: " + ", ".join(quality_tags))

    return "\n".join(lines)
