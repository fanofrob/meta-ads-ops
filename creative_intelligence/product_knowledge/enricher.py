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


_VARIETY_KEYWORDS = frozenset({
    "variety", "mixed", "mix", "collection", "assortment",
    "sampler", "bundle", "selection", "box", "crate",
})

_EXOTIC_FRUIT_COPY_NAMES = [
    "exotic fruit collection",
    "rare tropical fruits",
    "hand-picked exotic fruits",
    "tropical fruit selection",
    "rare & exotic fruits",
]

_EXOTIC_FRUIT_EXAMPLES = (
    "cherimoyas, dragonfruit, jackfruit, passion fruit, sapodilla, "
    "mamey sapote, longan, lychee, rambutan, starfruit, atemoya, feijoa"
)


def is_variety_product(name: str, category: str | None = None) -> bool:
    """Return True if the product is a mixed/variety collection rather than a single fruit."""
    text = f"{name} {category or ''}".lower()
    tokens = set(text.split())
    return bool(tokens & _VARIETY_KEYWORDS)


def _variety_copy_name(product_id: str | None, db: Any | None = None) -> str:
    """Return an evocative copy name for variety products — rotates so briefs vary."""
    # Use product_id as a deterministic seed so the same product gets the same name per session
    # but different products may vary.
    if product_id:
        try:
            idx = int(product_id) % len(_EXOTIC_FRUIT_COPY_NAMES)
        except (ValueError, TypeError):
            idx = hash(product_id) % len(_EXOTIC_FRUIT_COPY_NAMES)
        return _EXOTIC_FRUIT_COPY_NAMES[idx]
    return _EXOTIC_FRUIT_COPY_NAMES[0]


def _clean_product_name(raw: str, category: str | None = None) -> str:
    """Extract a clean fruit/product name from a Shopify listing name.

    Shopify names are often SKU-like: "1LB Kumquat 50% Off" or "Reed Avocado".
    We want just the core product name: "Kumquat" or "Reed Avocado".

    Strategy:
    1. If category is set (e.g. "Fruit: Kumquat"), extract the fruit name from it —
       strip the "Type: " prefix, un-invert comma parts, return the result.
    2. Otherwise strip leading weight tokens (1LB, 2oz, etc.) and trailing
       offer/pricing tokens (50% Off, Sale, etc.) from the raw name.
    """
    import re as _re

    # 1. Prefer category — it's already the canonical product name
    if category:
        colon = category.find(":")
        if colon != -1:
            rest  = category[colon + 1:].strip()
            parts = [p.strip() for p in rest.split(",") if p.strip()]
            return " ".join(reversed(parts)) if len(parts) > 1 else rest
        return category

    # 2. Strip common SKU noise from raw Shopify name
    name = raw
    # Remove leading weight/quantity: "1LB", "2 LB", "500g", "1.5kg", etc.
    name = _re.sub(r"^\d+(\.\d+)?\s*(lb|lbs|oz|g|kg|pound|pounds)\b[\s\-]*", "", name, flags=_re.I)
    # Remove trailing price/offer: "50% Off", "40% Off Sale", "- Sale", etc.
    name = _re.sub(r"[\s\-]*([\d]+%\s*off|sale|deal|promo|discount|free\s*shipping)[\s\S]*$", "", name, flags=_re.I)
    return name.strip()


def build_prompt_context_block(product_id: str, conn: sqlite3.Connection | None = None) -> str:
    """Format product context as a text block suitable for injection into prompts."""
    ctx = get_product_context(product_id, conn)
    if not ctx:
        return ""

    p = ctx["product"]
    clean_name    = _clean_product_name(p["name"], p.get("category"))
    product_label = p.get("category") or p["name"]

    # Detect variety/mixed-fruit products and override copy framing
    variety = is_variety_product(p["name"], p.get("category"))
    if variety:
        clean_name = _variety_copy_name(product_id)

    lines = [
        f"Name: {clean_name}",          # clean fruit name for use in copy — NO weight/price/SKU
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

    # Variety product framing — override generic "Variety Box" language
    if variety:
        lines.append(
            f"COPY RULE — this is a mixed exotic fruit collection. "
            f"NEVER call it 'Variety Box' or 'mixed box' in any hook or headline. "
            f"Instead use: '{clean_name}', 'exotic fruits', 'rare tropicals', "
            f"or name individual fruits from this list: {_EXOTIC_FRUIT_EXAMPLES}. "
            f"Hooks should mention 1-2 specific fruit names for credibility and curiosity."
        )

    return "\n".join(lines)
