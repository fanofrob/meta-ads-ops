"""
Tests for Ad Studio core photos + box-size infographics.

No live APIs: Shopify fetch, image downloads and Nano Banana are all mocked;
the DB and output files live in tmp_path.
"""
from __future__ import annotations

import pytest
from PIL import Image

from creative_intelligence.adstudio import core as C
from creative_intelligence.adstudio.infographic import render_bab, render_pdp
from creative_intelligence.db import get_connection, init_db

SHOP_VARIANTS = [
    {"title": "2lb | 2-3 pcs", "available": True},
    {"title": "5lb | 6-8 pcs", "available": True},
    {"title": "8lb | 10-14 pcs", "available": True},
    {"title": "16lb | 20-30 pcs", "available": False},
    {"title": "4lb box", "available": False},
]


# ── variant parsing ────────────────────────────────────────────────────────
class TestVariants:
    def test_parse_range(self):
        r = C.parse_variant("5lb | 6-8 pcs")
        assert (r["size"], r["weight"], r["pieces"]) == ("MEDIUM", "5 LBS", "6–8")
        assert not r["weight_varies"]

    def test_parse_approx_count(self):
        r = C.parse_variant("2lb | ~50 pcs")
        assert (r["size"], r["pieces"]) == ("SAMPLE", "~50")

    def test_parse_cacao_sold_by_piece(self):
        r = C.parse_variant("~4lb | 2 pcs")
        assert (r["size"], r["weight"], r["pieces"]) == ("2 PC", "~4 LBS", "2")
        assert r["weight_varies"]

    def test_legacy_title_ignored(self):
        assert C.parse_variant("4lb box") is None

    def test_default_rows_are_available_2_5_8(self):
        rows, est = C.rows_from_variants(SHOP_VARIANTS)
        assert [r["lb"] for r in rows] == [2, 5, 8, 16]
        assert [r["on"] for r in rows] == [True, True, True, False]
        assert est == "pieces"

    def test_weight_estimate_when_approx(self):
        _, est = C.rows_from_variants([{"title": "~2lb | 1 pcs", "available": True}])
        assert est == "weight"

    def test_bab_row_is_smallest(self):
        rows, _ = C.rows_from_variants(SHOP_VARIANTS)
        assert C.bab_row(rows) == {"size": "SMALL", "weight": "2 LBS", "pieces": "2–3"}

    def test_shopify_js_url(self):
        u = "https://shop.goodhillfarms.com/products/lychee?_pos=15&_sid=x"
        assert C.shopify_js_url(u) == "https://shop.goodhillfarms.com/products/lychee.js"
        with pytest.raises(ValueError):
            C.shopify_js_url("https://example.com/")

    def test_short_name(self):
        assert C.short_name("Large Cherimoya (Custard Apple)") == "Large Cherimoya"


# ── prompts ───────────────────────────────────────────────────────────────
class TestPrompts:
    prod = {"name": "Pink Guava", "physical_desc": "green skin, pink flesh",
            "varieties": "Ruby — purple skin — magenta flesh", "notes": ""}

    def test_core1_has_box_and_board(self):
        p = C.build_core_prompt(self.prod, "core1")
        assert "white corrugated" in p and "BOARD" in p and "VARIETY BOX" in p

    def test_core1_is_eye_level_box_propped_up(self):
        p = C.build_core_prompt(self.prod, "core1")
        assert "EYE LEVEL" in p and "PROPPED UP" in p and "SYMMETRICALLY" in p

    def test_reference_labels_match_counts(self):
        p = C.build_core_prompt(self.prod, "core1", brief="two halves", n_layout=1, n_fruit=3)
        assert "Reference image 1 = the LAYOUT" in p
        assert "Reference images 2, 3, 4 = the FRUIT" in p
        assert "SHOT BRIEF" in p and "two halves" in p

    def test_no_reference_section_without_refs(self):
        assert "REFERENCE IMAGES" not in C.build_core_prompt(self.prod, "core2")

    def test_core2_is_close_up_with_blur(self):
        p = C.build_core_prompt(self.prod, "core2")
        assert "DIAGONAL" in p and "ASYMMETRIC" in p and "OUR BOX" not in p

    def test_core3_rotates_and_has_hand(self):
        a = C.build_core_prompt(self.prod, "core3", 0)
        b = C.build_core_prompt(self.prod, "core3", 1)
        assert a != b and "hand" in a

    def test_corrections_first(self):
        p = C.build_core_prompt(self.prod, "core1", corrections="deeper pink", brief="b")
        assert p.index("CORRECTIONS") < p.index("SHOT BRIEF") < p.index("OUR BOX")

    def test_unknown_kind(self):
        with pytest.raises(ValueError):
            C.build_core_prompt(self.prod, "core9")


# ── infographic rendering ─────────────────────────────────────────────────
class TestInfographics:
    rows = [{"size": "SAMPLE", "weight": "2 LBS", "pieces": "4–6"},
            {"size": "MEDIUM", "weight": "5 LBS", "pieces": "10–15"}]

    def test_pdp_renders_square(self, tmp_path):
        thumb = tmp_path / "t.png"
        Image.new("RGB", (300, 400), (0, 200, 0)).save(thumb)
        out = render_pdp("Dragon Fruit Variety Box", self.rows, str(tmp_path / "p.png"),
                         thumb_path=str(thumb), size=1024)
        im = Image.open(out)
        assert im.size == (1024, 1024)
        assert im.getpixel((200, 100))[1] > 150  # thumbnail pasted top-left

    def test_pdp_needs_rows(self, tmp_path):
        with pytest.raises(ValueError):
            render_pdp("X", [], str(tmp_path / "p.png"))

    def test_bab_renders(self, tmp_path):
        out = render_bab("Persimmon, Fuyu", self.rows[0], str(tmp_path / "b.png"))
        assert Image.open(out).size == (1080, 1080)


# ── routes ────────────────────────────────────────────────────────────────
@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "t.db"
    init_db(db_path)
    import creative_intelligence.webapp.app as app_module
    from creative_intelligence.adstudio import store
    monkeypatch.setattr(app_module, "_db", lambda: get_connection(db_path))
    monkeypatch.setattr(store, "_base_dir", lambda: tmp_path)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _png_bytes() -> bytes:
    import io
    buf = io.BytesIO()
    Image.new("RGB", (400, 400), (200, 80, 120)).save(buf, "PNG")
    return buf.getvalue()


class _FakeResp:
    def __init__(self, data): self._d = data
    def read(self): return self._d


def test_import_queue_run_and_infographic(client, tmp_path, monkeypatch):
    monkeypatch.setattr(C, "fetch_shopify_product", lambda url: {
        "handle": "pink-guava", "title": "Red Guava (Pink)", "description": "sweet",
        "variants": SHOP_VARIANTS, "images": ["https://cdn/x.jpg", "https://cdn/y.jpg"]})
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _FakeResp(_png_bytes()))
    monkeypatch.setattr(C, "classify_photos", lambda name, uris: ["infographic", "core1"][:len(uris)])
    briefs = []
    monkeypatch.setattr(C, "write_shot_brief",
                        lambda prod, kind, variant, layout, fruit: briefs.append(
                            (kind, len(layout), len(fruit))) or f"brief-{kind}")

    r = client.post("/api/adstudio/import-shopify",
                    json={"url": "https://shop.goodhillfarms.com/products/pink-guava"}).get_json()
    pid = r["id"]
    assert r["photos_added"] == 2 and r["info"]["name"] == "Red Guava"
    assert sorted(r["roles"].values()) == ["core1", "infographic"]
    prod = client.get(f"/api/adstudio/product/{pid}").get_json()["product"]
    assert prod["product_type"] == "fruit"

    # re-import refreshes the same product rather than duplicating it
    r2 = client.post("/api/adstudio/import-shopify",
                     json={"url": "https://shop.goodhillfarms.com/products/pink-guava"}).get_json()
    assert r2["id"] == pid and r2["photos_added"] == 0

    q = client.post(f"/api/adstudio/product/{pid}/core/queue",
                    json={"kinds": ["core1", "core3"], "n": 2}).get_json()["queued"]
    assert [x["kind"] for x in q] == ["core1", "core1", "core3", "core3"]
    # one brief for core1 (shared), one per lifestyle setup; infographic never a ref
    assert briefs == [("core1", 1, 0), ("core3", 0, 1), ("core3", 0, 1)]

    gen = tmp_path / "gen.png"
    import os
    Image.frombytes("RGB", (200, 200), os.urandom(200 * 200 * 3)).save(gen)  # >5KB
    seen = {}

    def fake_generate(prompt, uris, aspect):
        seen["n_refs"], seen["aspect"] = len(uris), aspect
        seen["prompt"] = prompt
        return str(gen)
    monkeypatch.setattr("creative_intelligence.adstudio.generate.generate_ad", fake_generate)
    cid = q[0]["id"]
    assert client.post(f"/api/adstudio/core/{cid}/run").get_json()["status"] == "done"
    assert (seen["n_refs"], seen["aspect"]) == (1, "1:1")
    assert "brief-core1" in seen["prompt"] and "Reference image 1 = the LAYOUT" in seen["prompt"]

    # a fix note is stored and folded into the rebuilt prompt
    assert client.post(f"/api/adstudio/core/{cid}/regenerate",
                       json={"fix": "box upright"}).get_json()["status"] == "done"
    assert "CORRECTIONS" in seen["prompt"] and "box upright" in seen["prompt"]
    assert client.get(f"/api/adstudio/core/{cid}/image").status_code == 200

    info = r["info"]
    res = client.post(f"/api/adstudio/product/{pid}/infographic",
                      json={"kind": "pdp", "info": info, "thumb": f"core:{cid}"}).get_json()
    assert "id" in res
    res = client.post(f"/api/adstudio/product/{pid}/infographic",
                      json={"kind": "bab", "info": info, "thumb": ""}).get_json()
    assert "id" in res

    items = client.get(f"/api/adstudio/core?product_id={pid}").get_json()["items"]
    assert {"info_pdp", "info_bab"} <= {i["kind"] for i in items}

    z = client.get(f"/api/adstudio/export-core?product_id={pid}")
    assert z.status_code == 200 and z.data[:2] == b"PK"


def test_infographic_rejects_bad_kind(client):
    pid = client.post("/api/adstudio/products", json={"name": "Lychee"}).get_json()["id"]
    assert client.post(f"/api/adstudio/product/{pid}/infographic",
                       json={"kind": "nope"}).status_code == 400


def test_new_fruit_borrows_layout_from_another_product(client, tmp_path, monkeypatch):
    import creative_intelligence.webapp.app as app_module
    a = client.post("/api/adstudio/products", json={"name": "Guava"}).get_json()["id"]
    b = client.post("/api/adstudio/products", json={"name": "Lychee"}).get_json()["id"]
    for pid in (a, b):
        client.post(f"/api/adstudio/product/{pid}/photo",
                    data={"file": (__import__("io").BytesIO(_png_bytes()), "x.png")},
                    content_type="multipart/form-data")
    pa = client.get(f"/api/adstudio/product/{a}").get_json()["photos"][0]["id"]
    client.post(f"/api/adstudio/photo/{pa}/role", json={"role": "core1"})
    assert client.post(f"/api/adstudio/photo/{pa}/role", json={"role": "nope"}).status_code == 400
    conn = app_module._db()
    layout, fruit = app_module._core_refs(conn, b, "core1")
    conn.close()
    assert len(layout) == 1 and len(fruit) == 1   # guava's core1 as layout, lychee photo as fruit


def test_core_shots_compose_like_images(client, tmp_path):
    """A finished Core shot can be a Compose base; its mirror stays out of the Images tab."""
    import sqlite3
    import creative_intelligence.webapp.app as app_module
    pid = client.post("/api/adstudio/products", json={"name": "Mango", "product_type": "fruit"}).get_json()["id"]
    shot = tmp_path / "core1.png"
    Image.new("RGB", (600, 600), (240, 200, 60)).save(shot)
    conn = app_module._db()
    with conn:
        cid = conn.execute("INSERT INTO adstudio_core (product_id, kind, status, image_path)"
                           " VALUES (?, 'core1', 'done', ?)", (pid, str(shot))).lastrowid
        info = conn.execute("INSERT INTO adstudio_core (product_id, kind, status, image_path)"
                            " VALUES (?, 'info_pdp', 'done', ?)", (pid, str(shot))).lastrowid
    conn.close()

    r = client.post("/api/adstudio/compose", json={"core_id": cid, "headline": "Tree-ripened"}).get_json()
    assert "id" in r
    assert client.get(f"/api/adstudio/composite/{r['id']}/image").status_code == 200
    # infographics are not ad bases
    assert client.post("/api/adstudio/compose", json={"core_id": info, "headline": "x"}).status_code == 404

    assert client.get(f"/api/adstudio/images?product_id={pid}").get_json()["images"] == []
    b = client.post("/api/adstudio/compose-bulk", json={
        "product_id": pid, "core_ids": [cid], "copies": [{"headline": "A"}, {"headline": "B"}]}).get_json()
    assert b == {"made": 2, "total": 2}
    # the same shot reuses one mirror row
    conn = app_module._db()
    assert conn.execute("SELECT COUNT(*) FROM adstudio_images WHERE core_id=?", (cid,)).fetchone()[0] == 1
    conn.close()


def test_gallery_review_and_launch_kit(client, tmp_path):
    """Approve / reject / ready (as in Render), filtered counts, and the ready-only launch kit."""
    import csv, io, zipfile
    import creative_intelligence.webapp.app as app_module
    pid = client.post("/api/adstudio/products", json={"name": "Pink Mango", "product_type": "fruit"}).get_json()["id"]
    hid = client.post(f"/api/adstudio/product/{pid}/hook", json={
        "headline": "Tastes like honey", "body": "Tree-ripened, shipped overnight.",
        "archetype": "Social proof hook · Fear angle", "source": "pattern"}).get_json()["id"]
    base = tmp_path / "base.png"
    Image.new("RGB", (500, 500), (240, 180, 60)).save(base)
    conn = app_module._db()
    with conn:
        iid = conn.execute("INSERT INTO adstudio_images (product_id, status, image_path)"
                           " VALUES (?, 'done', ?)", (pid, str(base))).lastrowid
    conn.close()
    made = client.post("/api/adstudio/compose-bulk", json={
        "product_id": pid, "image_ids": [iid], "hook_ids": [hid]}).get_json()
    assert made["made"] == 1
    extra = client.post("/api/adstudio/compose-bulk", json={
        "product_id": pid, "image_ids": [iid], "copies": [{"headline": "B"}, {"headline": "C"}]}).get_json()
    assert extra["made"] == 2
    a, b, c = sorted(x["id"] for x in client.get(f"/api/adstudio/composites?product_id={pid}").get_json()["composites"])

    review = lambda cid, **kw: client.post(f"/api/adstudio/composite/{cid}/review", json=kw).get_json()
    assert review(a, action="ready")["is_ready"] == 1 and review(a, notes="lead ad")["review_notes"] == "lead ad"
    assert review(b, action="approve")["review_status"] == "approved"
    assert review(b, action="approve")["review_status"] == ""          # toggles off
    assert client.post("/api/adstudio/composites/bulk", json={"action": "reject", "ids": [b, c]}).get_json()["affected"] == 2

    d = client.get(f"/api/adstudio/composites?product_id={pid}&review=ready").get_json()
    assert [x["id"] for x in d["composites"]] == [a]
    assert d["counts"] == {"all": 3, "unreviewed": 0, "approved": 1, "ready": 1, "rejected": 2}
    assert d["composites"][0]["hook_source"] == "pattern"

    z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/adstudio/export-composites?ready=1&product_id={pid}").data))
    assert sorted(z.namelist()) == ["manifest.csv", f"pink-mango_ad_{a}.png"]
    row = next(csv.DictReader(io.StringIO(z.read("manifest.csv").decode())))
    assert (row["headline"], row["primary_text"], row["pattern"], row["notes"]) == (
        "Tastes like honey", "Tree-ripened, shipped overnight.", "Social proof hook · Fear angle", "lead ad")
