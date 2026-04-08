"""
Product knowledge loader.

Ingests product data from local files into the SQLite DB.
Supported formats:
  - JSON  (.json)  — single product or array of products
  - CSV   (.csv)   — one product per row
  - TXT/MD (.txt, .md) — treated as a raw KB document

Never reads from Shopify (that's shopify_adapter.py).
Never modifies data/raw/ or the main reporting pipeline.

Expected JSON structure (single product):
{
  "id": "cherry-plum-bc",        # required; slug used as PK
  "name": "Cherry Plum (BC)",    # required
  "category": "fresh-fruit",
  "price": 12.99,
  "description": "...",
  "short_description": "...",
  "positioning": "...",
  "target_persona": "...",
  "benefits": [
    {"type": "feature",          "content": "...", "priority": 10},
    {"type": "pain_point",       "content": "..."},
    {"type": "desired_outcome",  "content": "..."}
  ],
  "angles": [
    {"name": "Health angle", "description": "...", "emotional_trigger": "desire"}
  ],
  "offers": [
    {"type": "discount", "description": "10% off first order", "discount_pct": 10.0}
  ]
}

CSV expected headers (minimum):
  id, name
Optional: category, price, description, short_description, positioning, target_persona
"""
from __future__ import annotations

import csv
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from creative_intelligence.db import get_connection


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:80]


def _upsert_product(product: dict[str, Any], db: sqlite3.Connection) -> str:
    """Insert or update a product row. Returns product_id."""
    pid = product.get("id") or _slugify(product.get("name", "unknown"))
    now = datetime.utcnow().isoformat()

    existing = db.execute("SELECT id FROM products WHERE id = ?", (pid,)).fetchone()
    if existing:
        db.execute(
            """UPDATE products SET
               name=?, category=?, price=?, description=?, short_description=?,
               positioning=?, target_persona=?, tags=?, source=?, updated_at=?
               WHERE id=?""",
            (
                product.get("name", ""),
                product.get("category"),
                product.get("price"),
                product.get("description"),
                product.get("short_description"),
                product.get("positioning"),
                product.get("target_persona"),
                product.get("tags"),
                product.get("source", "manual"),
                now, pid,
            ),
        )
    else:
        db.execute(
            """INSERT INTO products
               (id, name, category, price, description, short_description,
                positioning, target_persona, tags, source, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                pid,
                product.get("name", ""),
                product.get("category"),
                product.get("price"),
                product.get("description"),
                product.get("short_description"),
                product.get("positioning"),
                product.get("target_persona"),
                product.get("tags"),
                product.get("source", "manual"),
                now, now,
            ),
        )
    return pid


def _insert_benefits(pid: str, benefits: list[dict], db: sqlite3.Connection) -> None:
    # Clear existing and re-insert (idempotent on full reload).
    db.execute("DELETE FROM product_benefits WHERE product_id = ?", (pid,))
    for b in benefits:
        db.execute(
            "INSERT INTO product_benefits (product_id, benefit_type, content, priority) VALUES (?,?,?,?)",
            (pid, b.get("type", "feature"), b.get("content", ""), b.get("priority", 0)),
        )


def _insert_angles(pid: str, angles: list[dict], db: sqlite3.Connection) -> None:
    db.execute("DELETE FROM product_angles WHERE product_id = ?", (pid,))
    for a in angles:
        db.execute(
            """INSERT INTO product_angles
               (product_id, angle_name, angle_description, target_persona, emotional_trigger, proof_points)
               VALUES (?,?,?,?,?,?)""",
            (
                pid,
                a.get("name", ""),
                a.get("description"),
                a.get("target_persona"),
                a.get("emotional_trigger"),
                json.dumps(a.get("proof_points", [])),
            ),
        )


def _insert_offers(pid: str, offers: list[dict], db: sqlite3.Connection) -> None:
    db.execute("DELETE FROM product_offers WHERE product_id = ?", (pid,))
    for o in offers:
        db.execute(
            """INSERT INTO product_offers
               (product_id, offer_type, offer_description, discount_pct, bundle_products, active)
               VALUES (?,?,?,?,?,?)""",
            (
                pid,
                o.get("type", "other"),
                o.get("description"),
                o.get("discount_pct"),
                json.dumps(o.get("bundle_products", [])),
                1,
            ),
        )


def load_product_json(path: Path, conn: sqlite3.Connection | None = None) -> list[str]:
    """Load products from a JSON file. Returns list of inserted product IDs."""
    db = conn or get_connection()
    raw = json.loads(path.read_text())
    products = raw if isinstance(raw, list) else [raw]
    ids = []
    for p in products:
        p["source"] = p.get("source", "json")
        pid = _upsert_product(p, db)
        _insert_benefits(pid, p.get("benefits", []), db)
        _insert_angles(pid, p.get("angles", []), db)
        _insert_offers(pid, p.get("offers", []), db)
        ids.append(pid)
    db.commit()
    return ids


def load_product_csv(path: Path, conn: sqlite3.Connection | None = None) -> list[str]:
    """Load products from a CSV file. Returns list of inserted product IDs."""
    db = conn or get_connection()
    ids = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row = {k.strip(): v.strip() for k, v in row.items()}
            if not row.get("name"):
                continue
            row["source"] = "csv"
            if row.get("price"):
                try:
                    row["price"] = float(row["price"])
                except ValueError:
                    row["price"] = None
            pid = _upsert_product(row, db)
            ids.append(pid)
    db.commit()
    return ids


def load_kb_document(
    path: Path,
    product_id: str | None = None,
    doc_type: str = "brief",
    conn: sqlite3.Connection | None = None,
) -> int:
    """Load a text/markdown file as a KB document. Returns inserted row id."""
    db = conn or get_connection()
    content = path.read_text(encoding="utf-8")
    title   = path.stem.replace("_", " ").replace("-", " ").title()
    cursor = db.execute(
        """INSERT INTO product_kb_documents
           (product_id, doc_type, title, content, source_file)
           VALUES (?,?,?,?,?)""",
        (product_id, doc_type, title, content, str(path)),
    )
    db.commit()
    return cursor.lastrowid


def ingest_directory(
    directory: Path | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Scan a directory and ingest all supported product files.

    Looks for:
      *.json → load_product_json
      *.csv  → load_product_csv
      *.txt, *.md → load_kb_document

    Returns summary dict.
    """
    from creative_intelligence import config
    directory = directory or Path(config.CI_PRODUCT_KB_DIR)
    directory.mkdir(parents=True, exist_ok=True)

    db = conn or get_connection()
    summary = {"json": [], "csv": [], "docs": [], "skipped": []}

    for path in sorted(directory.iterdir()):
        if path.suffix.lower() == ".json":
            try:
                ids = load_product_json(path, db)
                summary["json"].extend(ids)
            except Exception as e:
                summary["skipped"].append({"file": path.name, "error": str(e)})
        elif path.suffix.lower() == ".csv":
            try:
                ids = load_product_csv(path, db)
                summary["csv"].extend(ids)
            except Exception as e:
                summary["skipped"].append({"file": path.name, "error": str(e)})
        elif path.suffix.lower() in {".txt", ".md"}:
            try:
                row_id = load_kb_document(path, conn=db)
                summary["docs"].append({"file": path.name, "row_id": row_id})
            except Exception as e:
                summary["skipped"].append({"file": path.name, "error": str(e)})

    return summary
