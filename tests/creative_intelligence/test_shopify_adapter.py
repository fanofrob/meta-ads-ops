"""Tests for creative_intelligence.product_knowledge.shopify_adapter.

All tests are offline — no live Shopify API calls are made.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from creative_intelligence.product_knowledge.shopify_adapter import (
    _classify_benefit,
    _extract_list_items,
    _extract_paragraphs,
    _extract_price_data,
    _format_context_block,
    _parse_claims_from_html,
    _strip_html,
    build_product_knowledge_base,
    get_product_context,
    map_shopify_product,
    sync_shopify_to_db,
)
from creative_intelligence.db import init_db, get_connection


# ─────────────────────────────────────────────
# HTML utilities
# ─────────────────────────────────────────────

class TestStripHtml:
    def test_removes_tags(self):
        assert _strip_html("<p>Hello <b>world</b></p>") == "Hello world"

    def test_decodes_entities(self):
        assert _strip_html("&amp; &lt;b&gt;") == "& <b>"

    def test_collapses_whitespace(self):
        assert _strip_html("<p>  hello   world  </p>") == "hello world"

    def test_empty_string(self):
        assert _strip_html("") == ""

    def test_none_input(self):
        assert _strip_html(None) == ""


class TestExtractListItems:
    def test_basic_list(self):
        html = "<ul><li>Item one</li><li>Item two</li></ul>"
        items = _extract_list_items(html)
        assert items == ["Item one", "Item two"]

    def test_nested_tags_in_items(self):
        html = "<li><strong>Bold</strong> text</li>"
        items = _extract_list_items(html)
        assert items == ["Bold text"]

    def test_empty_list_items_skipped(self):
        html = "<li></li><li>  </li><li>Real item</li>"
        items = _extract_list_items(html)
        assert items == ["Real item"]

    def test_no_list_items(self):
        assert _extract_list_items("<p>No items here</p>") == []


class TestExtractParagraphs:
    def test_basic_paragraphs(self):
        html = "<p>First paragraph.</p><p>Second paragraph.</p>"
        paras = _extract_paragraphs(html)
        assert len(paras) == 2
        assert paras[0] == "First paragraph."

    def test_empty_paragraphs_skipped(self):
        html = "<p>  </p><p>Real content.</p>"
        paras = _extract_paragraphs(html)
        assert paras == ["Real content."]


class TestClassifyBenefit:
    def test_feature_classification(self):
        assert _classify_benefit("Harvested at peak ripeness") == "feature"
        assert _classify_benefit("Ships same-day from the farm") == "feature"

    def test_pain_point_classification(self):
        assert _classify_benefit("Tired of grocery store fruit that's picked green") == "pain_point"
        assert _classify_benefit("Stop eating disappointing produce") == "pain_point"

    def test_desired_outcome_classification(self):
        assert _classify_benefit("Experience true tree-ripened taste") == "desired_outcome"
        assert _classify_benefit("Enjoy the difference of real farm-fresh flavor") == "desired_outcome"

    def test_case_insensitive(self):
        # "problem" and "frustrat" keywords match after lowercasing
        assert _classify_benefit("This is a PROBLEM for everyone") == "pain_point"
        assert _classify_benefit("So FRUSTRATED by grocery stores") == "pain_point"


# ─────────────────────────────────────────────
# Price extraction
# ─────────────────────────────────────────────

class TestExtractPriceData:
    def test_basic_price(self):
        variants = [{"price": "12.99", "compare_at_price": None}]
        data = _extract_price_data(variants)
        assert data["price"] == 12.99
        assert data["compare_at_price"] is None

    def test_with_compare_at_price(self):
        variants = [{"price": "9.99", "compare_at_price": "14.99"}]
        data = _extract_price_data(variants)
        assert data["price"] == 9.99
        assert data["compare_at_price"] == 14.99

    def test_empty_variants(self):
        data = _extract_price_data([])
        assert data["price"] == 0.0
        assert data["compare_at_price"] is None

    def test_null_price_defaults_to_zero(self):
        variants = [{"price": None, "compare_at_price": None}]
        data = _extract_price_data(variants)
        assert data["price"] == 0.0


# ─────────────────────────────────────────────
# Parse claims from HTML
# ─────────────────────────────────────────────

class TestParseClaimsFromHtml:
    BODY_HTML = """
    <p>Farm-fresh mangoes shipped directly to your door.</p>
    <ul>
      <li>Harvested at peak ripeness, never picked green</li>
      <li>Ships same-day from the farm</li>
      <li>Tired of tasteless grocery store fruit? This is different.</li>
    </ul>
    """

    def test_extracts_list_items_as_claims(self):
        claims = _parse_claims_from_html(self.BODY_HTML)
        assert len(claims) >= 3

    def test_list_items_have_high_priority(self):
        claims = _parse_claims_from_html(self.BODY_HTML)
        list_claims = [c for c in claims if c["priority"] >= 8]
        assert len(list_claims) >= 2

    def test_pain_point_classified_correctly(self):
        claims = _parse_claims_from_html(self.BODY_HTML)
        pain_points = [c for c in claims if c["benefit_type"] == "pain_point"]
        assert len(pain_points) >= 1

    def test_feature_classified_correctly(self):
        claims = _parse_claims_from_html(self.BODY_HTML)
        features = [c for c in claims if c["benefit_type"] == "feature"]
        assert len(features) >= 1

    def test_empty_html(self):
        assert _parse_claims_from_html("") == []

    def test_no_duplicates(self):
        claims = _parse_claims_from_html(self.BODY_HTML)
        contents = [c["content"].lower() for c in claims]
        assert len(contents) == len(set(contents))


# ─────────────────────────────────────────────
# map_shopify_product
# ─────────────────────────────────────────────

def _make_raw_product(overrides: dict | None = None) -> dict:
    raw = {
        "id": 12345678,
        "title": "Farm Fresh Mango",
        "body_html": "<p>Premium mangoes.</p><ul><li>Tree ripened</li><li>Ships same-day</li></ul>",
        "tags": "fruit, mango, fresh",
        "product_type": "Fresh Produce",
        "status": "active",
        "handle": "farm-fresh-mango",
        "variants": [{"price": "12.99", "compare_at_price": "16.99"}],
        "images": [],
    }
    if overrides:
        raw.update(overrides)
    return raw


class TestMapShopifyProduct:
    def test_id_is_prefixed(self):
        entry = map_shopify_product(_make_raw_product())
        assert entry["product"]["id"] == "shopify-12345678"

    def test_name_from_title(self):
        entry = map_shopify_product(_make_raw_product())
        assert entry["product"]["name"] == "Farm Fresh Mango"

    def test_category_from_product_type(self):
        entry = map_shopify_product(_make_raw_product())
        assert entry["product"]["category"] == "Fresh Produce"

    def test_price_extracted(self):
        entry = map_shopify_product(_make_raw_product())
        assert entry["product"]["price"] == 12.99

    def test_source_is_shopify(self):
        entry = map_shopify_product(_make_raw_product())
        assert entry["product"]["source"] == "shopify"

    def test_benefits_extracted(self):
        entry = map_shopify_product(_make_raw_product())
        assert len(entry["benefits"]) >= 1

    def test_offer_created_when_discount(self):
        entry = map_shopify_product(_make_raw_product())
        assert len(entry["offers"]) == 1
        assert entry["offers"][0]["offer_type"] == "discount"
        assert entry["offers"][0]["discount_pct"] > 0

    def test_no_offer_when_no_compare_price(self):
        raw = _make_raw_product()
        raw["variants"][0]["compare_at_price"] = None
        entry = map_shopify_product(raw)
        assert len(entry["offers"]) == 0

    def test_short_description_truncated(self):
        long_body = "<p>" + "word " * 100 + "</p>"
        raw = _make_raw_product({"body_html": long_body})
        entry = map_shopify_product(raw)
        assert len(entry["product"]["short_description"]) <= 120

    def test_shopify_product_id_stored_as_string(self):
        entry = map_shopify_product(_make_raw_product())
        assert entry["product"]["shopify_product_id"] == "12345678"

    def test_tags_stored(self):
        entry = map_shopify_product(_make_raw_product())
        assert "mango" in entry["product"]["tags"]

    def test_metafields_converted_to_kb_docs(self):
        mf = [{"namespace": "product_copy", "key": "hook", "value": "The best mango you'll ever taste."}]
        entry = map_shopify_product(_make_raw_product(), metafields=mf)
        assert len(entry["kb_documents"]) == 1
        assert entry["kb_documents"][0]["content"] == "The best mango you'll ever taste."


# ─────────────────────────────────────────────
# build_product_knowledge_base
# ─────────────────────────────────────────────

class TestBuildProductKnowledgeBase:
    def test_keyed_by_db_id(self):
        raw_list = [_make_raw_product(), _make_raw_product({"id": 99999})]
        pkb = build_product_knowledge_base(raw_list)
        assert "shopify-12345678" in pkb
        assert "shopify-99999" in pkb

    def test_each_entry_has_required_keys(self):
        pkb = build_product_knowledge_base([_make_raw_product()])
        entry = pkb["shopify-12345678"]
        assert "product" in entry
        assert "benefits" in entry
        assert "offers" in entry
        assert "kb_documents" in entry

    def test_empty_list_returns_empty_dict(self):
        assert build_product_knowledge_base([]) == {}


# ─────────────────────────────────────────────
# get_product_context (in-memory PKB path)
# ─────────────────────────────────────────────

class TestGetProductContext:
    def test_returns_string_for_known_product(self):
        pkb = build_product_knowledge_base([_make_raw_product()])
        ctx = get_product_context("shopify-12345678", pkb=pkb)
        assert isinstance(ctx, str)
        assert len(ctx) > 10

    def test_contains_product_name(self):
        pkb = build_product_knowledge_base([_make_raw_product()])
        ctx = get_product_context("shopify-12345678", pkb=pkb)
        assert "Farm Fresh Mango" in ctx

    def test_contains_price_when_set(self):
        pkb = build_product_knowledge_base([_make_raw_product()])
        ctx = get_product_context("shopify-12345678", pkb=pkb)
        assert "12.99" in ctx

    def test_contains_discount_when_compare_price_higher(self):
        pkb = build_product_knowledge_base([_make_raw_product()])
        ctx = get_product_context("shopify-12345678", pkb=pkb)
        assert "was" in ctx.lower() or "%" in ctx

    def test_unknown_product_returns_empty(self):
        pkb = build_product_knowledge_base([_make_raw_product()])
        ctx = get_product_context("shopify-nonexistent", pkb=pkb)
        assert ctx == ""

    def test_empty_pkb_falls_back_gracefully(self):
        # Falls back to DB path which returns "" for unknown product
        ctx = get_product_context("shopify-12345678", pkb={})
        assert isinstance(ctx, str)


# ─────────────────────────────────────────────
# sync_shopify_to_db
# ─────────────────────────────────────────────

@pytest.fixture
def test_db(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    conn = get_connection(db_path)
    yield conn
    conn.close()


class TestSyncShopifyToDb:
    def test_inserts_product_row(self, test_db):
        raw_list = [_make_raw_product()]
        synced = sync_shopify_to_db(raw_products=raw_list, conn=test_db)
        assert "shopify-12345678" in synced
        row = test_db.execute("SELECT * FROM products WHERE id = 'shopify-12345678'").fetchone()
        assert row is not None
        assert row["name"] == "Farm Fresh Mango"

    def test_inserts_benefits(self, test_db):
        raw_list = [_make_raw_product()]
        sync_shopify_to_db(raw_products=raw_list, conn=test_db)
        benefits = test_db.execute(
            "SELECT * FROM product_benefits WHERE product_id = 'shopify-12345678'"
        ).fetchall()
        assert len(benefits) >= 1

    def test_inserts_offer_when_discount(self, test_db):
        raw_list = [_make_raw_product()]
        sync_shopify_to_db(raw_products=raw_list, conn=test_db)
        offers = test_db.execute(
            "SELECT * FROM product_offers WHERE product_id = 'shopify-12345678'"
        ).fetchall()
        assert len(offers) == 1

    def test_idempotent_on_second_sync(self, test_db):
        raw_list = [_make_raw_product()]
        sync_shopify_to_db(raw_products=raw_list, conn=test_db)
        sync_shopify_to_db(raw_products=raw_list, conn=test_db)  # second sync
        count = test_db.execute(
            "SELECT COUNT(*) FROM products WHERE id = 'shopify-12345678'"
        ).fetchone()[0]
        assert count == 1

    def test_updates_name_on_re_sync(self, test_db):
        raw_list = [_make_raw_product()]
        sync_shopify_to_db(raw_products=raw_list, conn=test_db)

        updated = _make_raw_product({"title": "Premium Ataulfo Mango"})
        sync_shopify_to_db(raw_products=[updated], conn=test_db)

        row = test_db.execute("SELECT name FROM products WHERE id = 'shopify-12345678'").fetchone()
        assert row["name"] == "Premium Ataulfo Mango"

    def test_empty_list_syncs_nothing(self, test_db):
        synced = sync_shopify_to_db(raw_products=[], conn=test_db)
        assert synced == []

    def test_returns_list_of_synced_ids(self, test_db):
        raw_list = [_make_raw_product(), _make_raw_product({"id": 99})]
        synced = sync_shopify_to_db(raw_products=raw_list, conn=test_db)
        assert set(synced) == {"shopify-12345678", "shopify-99"}

    def test_source_set_to_shopify(self, test_db):
        sync_shopify_to_db(raw_products=[_make_raw_product()], conn=test_db)
        row = test_db.execute("SELECT source FROM products WHERE id = 'shopify-12345678'").fetchone()
        assert row["source"] == "shopify"
