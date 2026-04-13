"""
Shopify product catalog adapter — read-only.

Fetch priority (auto-detected, first to succeed wins):

  1. Public storefront JSON  (no credentials required)
       https://{store}/products.json  — page-based pagination

  2. Admin REST API  (token prefix shpat_ / shpca_ / legacy)
       https://{store}.myshopify.com/admin/api/{version}/products.json
       Header: X-Shopify-Access-Token: {token}

  3. Storefront GraphQL API  (token prefix shpss_ / shpsa_)
       https://{store}.myshopify.com/api/{version}/graphql.json
       Header: X-Shopify-Storefront-Access-Token: {token}
       Protocol: GraphQL (cursor-paginated)

Requirements:
  - SHOPIFY_STORE_URL  e.g. "mystore.myshopify.com" or "https://shop.example.com"
  - SHOPIFY_ACCESS_TOKEN  (optional — only needed if public endpoint is disabled)
  - CI_SHOPIFY_ENABLED=1
"""
from __future__ import annotations

import html as _html
import json
import re
import sqlite3
import time
from typing import Any

import requests

from creative_intelligence import config
from creative_intelligence.db import get_connection


# ─────────────────────────────────────────────
# Config helpers
# ─────────────────────────────────────────────

def _admin_base_url() -> str:
    """https://{store}.myshopify.com/admin/api/{version}"""
    return f"{_store_base()}/admin/api/{config.SHOPIFY_API_VERSION}"


def _storefront_graphql_url() -> str:
    """https://{store}.myshopify.com/api/{version}/graphql.json"""
    return f"{_store_base()}/api/{config.SHOPIFY_API_VERSION}/graphql.json"


def _is_storefront_token(token: str) -> bool:
    """Storefront tokens start with shpss_ or shpsa_; Admin tokens with shpat_ / shpca_."""
    return token.startswith(("shpss_", "shpsa_"))


def _store_base() -> str:
    """Return https://{store} (no trailing slash or path).

    Accepts both myshopify.com domains and custom storefront domains
    (e.g. 'shop.goodhillfarms.com' or 'goodhillfarms.myshopify.com').
    """
    raw = (config.SHOPIFY_STORE_URL or "").strip().rstrip("/")
    if not raw:
        raise RuntimeError("SHOPIFY_STORE_URL is not configured.")
    if not raw.startswith("http"):
        raw = f"https://{raw}"
    return raw


def _headers() -> dict[str, str]:
    """Headers for Admin REST API."""
    if not config.SHOPIFY_ACCESS_TOKEN:
        raise RuntimeError("SHOPIFY_ACCESS_TOKEN is not configured.")
    return {
        "X-Shopify-Access-Token": config.SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json",
    }


def _storefront_headers() -> dict[str, str]:
    """Headers for Storefront GraphQL API."""
    if not config.SHOPIFY_ACCESS_TOKEN:
        raise RuntimeError("SHOPIFY_ACCESS_TOKEN is not configured.")
    return {
        "X-Shopify-Storefront-Access-Token": config.SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json",
    }


# ─────────────────────────────────────────────
# HTML / text utilities
# ─────────────────────────────────────────────

def _strip_html(raw: str) -> str:
    """Remove HTML tags, decode entities, collapse whitespace."""
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = _html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_list_items(body_html: str) -> list[str]:
    """Return plain-text contents of all <li> elements."""
    items = re.findall(r"<li[^>]*>(.*?)</li>", body_html or "", re.IGNORECASE | re.DOTALL)
    return [_strip_html(item).strip() for item in items if _strip_html(item).strip()]


def _extract_paragraphs(body_html: str) -> list[str]:
    """Return plain-text contents of all <p> elements."""
    paras = re.findall(r"<p[^>]*>(.*?)</p>", body_html or "", re.IGNORECASE | re.DOTALL)
    return [_strip_html(p).strip() for p in paras if _strip_html(p).strip()]


def _classify_benefit(text: str) -> str:
    """Heuristically classify a claim as feature / pain_point / desired_outcome."""
    t = text.lower()
    pain_kw = [
        "struggle", "problem", "tired of", "frustrat", "disappoint",
        "never tastes", "picked green", "doesn't", "won't", "can't",
        "poor quality", "artificial", "watered down",
    ]
    outcome_kw = [
        "feel ", "experience", "enjoy", "achieve", "get the", "become",
        "discover", "transform", "taste the difference", "notice the",
    ]
    if any(k in t for k in pain_kw):
        return "pain_point"
    if any(k in t for k in outcome_kw):
        return "desired_outcome"
    return "feature"


# ─────────────────────────────────────────────
# Shopify REST API fetching
# ─────────────────────────────────────────────

_ADMIN_PRODUCT_FIELDS = (
    "id,title,body_html,tags,product_type,status,"
    "variants,images,handle"
)

# GraphQL query for Storefront API — fetches equivalent fields
_STOREFRONT_PRODUCTS_QUERY = """
query GetProducts($cursor: String) {
  products(first: 250, after: $cursor) {
    edges {
      node {
        id
        title
        descriptionHtml
        tags
        productType
        handle
        variants(first: 5) {
          edges {
            node {
              price { amount }
              compareAtPrice { amount }
            }
          }
        }
        images(first: 3) {
          edges { node { url altText } }
        }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


def _fetch_products_public(limit: int = 250) -> list[dict[str, Any]]:
    """Fetch via the public storefront products.json endpoint (no credentials needed).

    Shopify stores expose this by default for active/published products.
    Uses old-style page-based pagination (?page=N&limit=250).
    Raises requests.HTTPError if the endpoint is unavailable (e.g. password-protected store).
    """
    base = _store_base()
    all_products: list[dict[str, Any]] = []
    page = 1
    while True:
        url = f"{base}/products.json?limit={limit}&page={page}"
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        batch = resp.json().get("products", [])
        all_products.extend(batch)
        if len(batch) < limit:
            break  # last page
        page += 1
        time.sleep(0.3)
    return all_products


def _fetch_products_admin(limit: int = 250) -> list[dict[str, Any]]:
    """Fetch via Admin REST API with Link-header pagination."""
    base = _admin_base_url()
    hdrs = _headers()
    url: str | None = (
        f"{base}/products.json"
        f"?status=active&limit={limit}&fields={_ADMIN_PRODUCT_FIELDS}"
    )
    all_products: list[dict[str, Any]] = []
    page = 0
    while url:
        page += 1
        resp = requests.get(url, headers=hdrs, timeout=30)
        resp.raise_for_status()
        batch = resp.json().get("products", [])
        all_products.extend(batch)
        url = None
        for part in resp.headers.get("Link", "").split(","):
            part = part.strip()
            if 'rel="next"' in part:
                m = re.search(r"<([^>]+)>", part)
                if m:
                    url = m.group(1)
                break
        if url and page > 1:
            time.sleep(0.5)
    return all_products


def _normalize_storefront_product(node: dict[str, Any]) -> dict[str, Any]:
    """Reshape a Storefront API product node to match Admin API field names."""
    # Storefront global ID looks like "gid://shopify/Product/12345678"
    raw_id = node.get("id", "")
    numeric_id = int(raw_id.split("/")[-1]) if "/" in raw_id else 0

    # Flatten variants
    variants = []
    for edge in (node.get("variants") or {}).get("edges", []):
        v = edge.get("node", {})
        variants.append({
            "price":            (v.get("price") or {}).get("amount", "0"),
            "compare_at_price": (v.get("compareAtPrice") or {}).get("amount"),
        })

    return {
        "id":          numeric_id,
        "title":       node.get("title", ""),
        "body_html":   node.get("descriptionHtml", ""),
        "tags":        ", ".join(node.get("tags") or []),
        "product_type": node.get("productType", ""),
        "status":      "active",
        "handle":      node.get("handle", ""),
        "variants":    variants,
        "images":      [
            {"src": e["node"]["url"]}
            for e in (node.get("images") or {}).get("edges", [])
        ],
    }


def _fetch_products_storefront() -> list[dict[str, Any]]:
    """Fetch via Storefront GraphQL API with cursor pagination."""
    url    = _storefront_graphql_url()
    hdrs   = _storefront_headers()
    cursor: str | None = None
    all_products: list[dict[str, Any]] = []
    page = 0

    while True:
        page += 1
        payload: dict[str, Any] = {
            "query":     _STOREFRONT_PRODUCTS_QUERY,
            "variables": {"cursor": cursor},
        }
        resp = requests.post(url, headers=hdrs, json=payload, timeout=30)
        resp.raise_for_status()

        body = resp.json()
        if body.get("errors"):
            raise RuntimeError(f"Storefront API errors: {body['errors']}")

        products_data = body.get("data", {}).get("products", {})
        for edge in products_data.get("edges", []):
            node = edge.get("node", {})
            all_products.append(_normalize_storefront_product(node))

        page_info = products_data.get("pageInfo", {})
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        if page > 1:
            time.sleep(0.5)

    return all_products


def fetch_shopify_products(limit: int = 250) -> list[dict[str, Any]]:
    """Fetch all active products from Shopify.

    Tries in order:
      1. Public storefront /products.json (no credentials needed)
      2. Admin REST API (token prefix shpat_ / shpca_ / legacy)
      3. Storefront GraphQL API (token prefix shpss_ / shpsa_)

    Returns list of normalised product dicts (same shape regardless of method used).
    Raises RuntimeError only if all methods fail.
    """
    # 1. Public storefront endpoint — works for most stores, no auth needed
    try:
        return _fetch_products_public(limit)
    except requests.HTTPError as exc:
        # 401/403 means store is password-protected; fall through to auth'd methods
        if exc.response is not None and exc.response.status_code not in (401, 403):
            raise
    except Exception:
        pass  # network error, unexpected response — try auth'd methods

    # 2 & 3. Auth'd fallback
    token = config.SHOPIFY_ACCESS_TOKEN or ""
    if not token:
        raise RuntimeError(
            "Public Shopify endpoint unavailable and SHOPIFY_ACCESS_TOKEN is not configured."
        )

    if _is_storefront_token(token):
        return _fetch_products_storefront()

    try:
        return _fetch_products_admin(limit)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 401:
            return _fetch_products_storefront()
        raise


def _fetch_metafields_for_product(shopify_id: int) -> list[dict[str, Any]]:
    """Fetch metafields for one product via Admin API (best-effort, [] on error)."""
    try:
        base = _admin_base_url()
        hdrs = _headers()
        resp = requests.get(
            f"{base}/products/{shopify_id}/metafields.json",
            headers=hdrs,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("metafields", [])
    except Exception:  # noqa: BLE001
        return []


# ─────────────────────────────────────────────
# Shopify → ProductKnowledgeBase mapping
# ─────────────────────────────────────────────

def _extract_price_data(variants: list[dict]) -> dict[str, Any]:
    """Pull price and compare_at_price from the first variant."""
    if not variants:
        return {"price": 0.0, "compare_at_price": None}
    v = variants[0]
    price = float(v.get("price") or 0)
    cap = v.get("compare_at_price")
    compare = float(cap) if cap else None
    return {"price": price, "compare_at_price": compare}


def _parse_claims_from_html(body_html: str) -> list[dict[str, Any]]:
    """
    Extract structured claims from Shopify body_html.

    Strategy:
      1. List items (<li>) → usually features/benefits — high priority
      2. Short sentences from <p> tags → lower priority
    """
    claims: list[dict[str, Any]] = []
    seen: set[str] = set()

    # 1. List items (priority 10 → 8)
    for i, item in enumerate(_extract_list_items(body_html)):
        if len(item) < 5 or item.lower() in seen:
            continue
        seen.add(item.lower())
        claims.append({
            "content": item,
            "benefit_type": _classify_benefit(item),
            "priority": max(10 - i, 1),
        })

    # 2. Paragraph sentences as lower-priority claims (skip if same as list items)
    for para in _extract_paragraphs(body_html)[:4]:
        # Split on sentence boundaries
        sentences = re.split(r"(?<=[.!?])\s+", para)
        for sent in sentences:
            sent = sent.strip()
            if len(sent) < 15 or len(sent) > 250:
                continue
            if sent.lower() in seen:
                continue
            seen.add(sent.lower())
            claims.append({
                "content": sent,
                "benefit_type": _classify_benefit(sent),
                "priority": 3,
            })
            if len(claims) >= 20:  # cap to keep it clean
                break

    return claims


def _metafields_to_kb_docs(
    shopify_id: int,
    product_db_id: str,
    metafields: list[dict],
) -> list[dict[str, Any]]:
    """Convert relevant Shopify metafields to product_kb_documents rows."""
    docs = []
    useful_namespaces = {"product_copy", "copy", "marketing", "seo", "custom"}
    useful_keys = {"hook", "tagline", "headline", "benefits", "claims", "description", "usp"}

    for mf in metafields:
        ns = (mf.get("namespace") or "").lower()
        key = (mf.get("key") or "").lower()
        value = (mf.get("value") or "").strip()
        if not value:
            continue
        if ns not in useful_namespaces and key not in useful_keys:
            continue
        docs.append({
            "product_id": product_db_id,
            "doc_type": key if key in useful_keys else "description",
            "title": f"[Shopify metafield] {ns}.{key}",
            "content": value,
            "source_file": f"shopify:product:{shopify_id}",
        })
    return docs


def map_shopify_product(
    raw: dict[str, Any],
    metafields: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Map a raw Shopify product response to a ProductKnowledgeBase entry.

    Returns:
      {
        "product": {id, name, category, price, compare_at_price,
                     description, short_description, tags, source, shopify_product_id},
        "benefits": [{content, benefit_type, priority}, ...],
        "offers":   [{offer_type, offer_description, discount_pct}, ...],
        "kb_documents": [{doc_type, title, content, source_file}, ...],
      }
    """
    shopify_id = raw["id"]
    db_id = f"shopify-{shopify_id}"

    body_html = raw.get("body_html") or ""
    full_text = _strip_html(body_html)

    price_data = _extract_price_data(raw.get("variants") or [])
    price = price_data["price"]
    compare_at = price_data["compare_at_price"]

    # Short description: first 120 chars of plain text
    short_desc = full_text[:120].rsplit(" ", 1)[0] if len(full_text) > 120 else full_text

    # Tags: admin API returns comma-separated string; storefront returns list
    raw_tags = raw.get("tags") or ""
    tags = ", ".join(raw_tags) if isinstance(raw_tags, list) else raw_tags

    product = {
        "id":                  db_id,
        "name":                raw.get("title", ""),
        "category":            raw.get("product_type") or None,
        "price":               price,
        "description":         full_text,
        "short_description":   short_desc,
        "positioning":         None,       # not available from Shopify by default
        "target_persona":      None,
        "source":              "shopify",
        "shopify_product_id":  str(shopify_id),
        "active":              1,
        "tags":                tags,
    }

    benefits = _parse_claims_from_html(body_html)

    offers: list[dict[str, Any]] = []
    if compare_at and compare_at > price and price > 0:
        discount_pct = round((1 - price / compare_at) * 100, 1)
        offers.append({
            "offer_type":        "discount",
            "offer_description": f"Was ${compare_at:.2f}, now ${price:.2f} ({discount_pct:.0f}% off)",
            "discount_pct":      discount_pct,
        })

    kb_docs = _metafields_to_kb_docs(shopify_id, db_id, metafields or [])

    return {
        "product":      product,
        "benefits":     benefits,
        "offers":       offers,
        "kb_documents": kb_docs,
    }


# ─────────────────────────────────────────────
# In-memory ProductKnowledgeBase
# ─────────────────────────────────────────────

def build_product_knowledge_base(
    raw_products: list[dict[str, Any]],
    fetch_metafields: bool = False,
) -> dict[str, dict[str, Any]]:
    """Build an in-memory knowledge base from a list of raw Shopify products.

    Args:
        raw_products: List returned by fetch_shopify_products().
        fetch_metafields: If True, fetch metafields for each product (extra API calls).

    Returns:
        dict keyed by product DB id (e.g. "shopify-12345678").
    """
    pkb: dict[str, dict[str, Any]] = {}
    for raw in raw_products:
        mf = _fetch_metafields_for_product(raw["id"]) if fetch_metafields else []
        entry = map_shopify_product(raw, mf)
        pkb[entry["product"]["id"]] = entry
    return pkb


def get_product_context(
    product_id: str,
    pkb: dict[str, dict[str, Any]] | None = None,
    conn: sqlite3.Connection | None = None,
) -> str:
    """Return a prompt-ready context string for a product.

    Lookup order:
      1. In-memory pkb (if provided)
      2. Creative intelligence DB (if conn provided or default DB exists)

    Returns empty string if product not found anywhere.
    """
    # Try in-memory PKB first
    if pkb and product_id in pkb:
        entry = pkb[product_id]
        return _format_context_block(entry)

    # Fall back to DB
    try:
        from creative_intelligence.product_knowledge.enricher import build_prompt_context_block
        return build_prompt_context_block(product_id, conn)
    except Exception:  # noqa: BLE001
        return ""


def _format_context_block(entry: dict[str, Any]) -> str:
    """Format a PKB entry as a prompt-ready text block."""
    p = entry["product"]
    lines = [f"Product: {p['name']}"]

    if p.get("short_description"):
        lines.append(f"Description: {p['short_description']}")
    if p.get("category"):
        lines.append(f"Category: {p['category']}")
    if p.get("price"):
        price_str = f"${p['price']:.2f}"
        cap = p.get("compare_at_price") or 0
        if cap and cap > p["price"]:
            price_str += f" (was ${cap:.2f})"
        lines.append(f"Price: {price_str}")

    features = [b["content"] for b in entry.get("benefits", [])
                if b.get("benefit_type") == "feature"][:3]
    if features:
        lines.append("Key features: " + " | ".join(features))

    pain_points = [b["content"] for b in entry.get("benefits", [])
                   if b.get("benefit_type") == "pain_point"][:2]
    if pain_points:
        lines.append("Pain points solved: " + " | ".join(pain_points))

    outcomes = [b["content"] for b in entry.get("benefits", [])
                if b.get("benefit_type") == "desired_outcome"][:2]
    if outcomes:
        lines.append("Desired outcomes: " + " | ".join(outcomes))

    if entry.get("offers"):
        o = entry["offers"][0]
        lines.append(f"Current offer: {o.get('offer_description', o['offer_type'])}")

    # Tags as searchable context
    tags = (p.get("tags") or "").strip()
    if tags:
        lines.append(f"Product tags: {tags}")

    return "\n".join(lines)


# ─────────────────────────────────────────────
# DB sync (idempotent upsert)
# ─────────────────────────────────────────────

def sync_shopify_to_db(
    raw_products: list[dict[str, Any]] | None = None,
    fetch_metafields: bool = False,
    conn: sqlite3.Connection | None = None,
) -> list[str]:
    """Upsert Shopify products into the creative intelligence DB.

    Args:
        raw_products: Pass to avoid a second API call; if None, fetches automatically.
        fetch_metafields: Fetch metafields per product (extra API calls).
        conn: SQLite connection; opens default DB if None.

    Returns:
        List of product DB ids that were synced (e.g. ["shopify-123", ...]).
    """
    if raw_products is None:
        raw_products = fetch_shopify_products()

    pkb = build_product_knowledge_base(raw_products, fetch_metafields=fetch_metafields)
    db = conn or get_connection()
    synced: list[str] = []

    with db:
        for product_id, entry in pkb.items():
            p = entry["product"]

            # Upsert products row
            db.execute(
                """INSERT INTO products
                     (id, name, category, price, description, short_description,
                      positioning, target_persona, tags, source, shopify_product_id, active)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     name              = excluded.name,
                     category          = excluded.category,
                     price             = excluded.price,
                     description       = excluded.description,
                     short_description = excluded.short_description,
                     tags              = excluded.tags,
                     source            = excluded.source,
                     shopify_product_id= excluded.shopify_product_id,
                     active            = excluded.active,
                     updated_at        = datetime('now')
                """,
                (
                    p["id"], p["name"], p.get("category"), p.get("price"),
                    p.get("description"), p.get("short_description"),
                    p.get("positioning"), p.get("target_persona"),
                    p.get("tags"),
                    p["source"], p["shopify_product_id"], p["active"],
                ),
            )

            # Replace benefits (delete + insert for simplicity — idempotent)
            db.execute("DELETE FROM product_benefits WHERE product_id = ?", (product_id,))
            for b in entry.get("benefits", []):
                db.execute(
                    """INSERT INTO product_benefits (product_id, benefit_type, content, priority)
                       VALUES (?,?,?,?)""",
                    (product_id, b["benefit_type"], b["content"], b.get("priority", 0)),
                )

            # Replace offers
            db.execute("DELETE FROM product_offers WHERE product_id = ?", (product_id,))
            for o in entry.get("offers", []):
                db.execute(
                    """INSERT INTO product_offers
                         (product_id, offer_type, offer_description, discount_pct, active)
                       VALUES (?,?,?,?,1)""",
                    (product_id, o["offer_type"], o.get("offer_description"), o.get("discount_pct")),
                )

            # Insert KB documents (don't replace — accumulate)
            existing_titles = {
                r[0] for r in db.execute(
                    "SELECT title FROM product_kb_documents WHERE product_id = ? AND source_file LIKE 'shopify:%'",
                    (product_id,),
                ).fetchall()
            }
            for doc in entry.get("kb_documents", []):
                if doc.get("title") in existing_titles:
                    continue
                db.execute(
                    """INSERT INTO product_kb_documents
                         (product_id, doc_type, title, content, source_file)
                       VALUES (?,?,?,?,?)""",
                    (
                        product_id, doc["doc_type"], doc.get("title"),
                        doc["content"], doc.get("source_file"),
                    ),
                )

            synced.append(product_id)

    return synced


# ─────────────────────────────────────────────
# Convenience entry point (used by CLI)
# ─────────────────────────────────────────────

def run_sync(dry_run: bool = True, fetch_metafields: bool = False) -> dict[str, Any]:
    """Fetch from Shopify and optionally sync to DB.

    Args:
        dry_run: If True, fetch and map but do NOT write to DB.
        fetch_metafields: Also fetch metafields per product.

    Returns:
        {
          "product_count": int,
          "synced_ids": [str],       # empty on dry_run
          "pkb": {product_id: entry},
          "dry_run": bool,
        }
    """
    raw = fetch_shopify_products()
    pkb = build_product_knowledge_base(raw, fetch_metafields=fetch_metafields)

    synced: list[str] = []
    if not dry_run:
        synced = sync_shopify_to_db(raw_products=raw, fetch_metafields=False)
        # (metafields already fetched into pkb if needed; sync from raw is fine)

    return {
        "product_count": len(pkb),
        "synced_ids":    synced,
        "pkb":           pkb,
        "dry_run":       dry_run,
    }
