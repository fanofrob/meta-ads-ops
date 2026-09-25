"""Product type (fruit | candle | other) drives Ad Studio's hooks and image prompts."""
from __future__ import annotations

import pytest

from creative_intelligence.adstudio.chat import _product_line, _system
from creative_intelligence.adstudio.concepts import build_image_prompt
from creative_intelligence.adstudio.profiles import PROFILES, normalize_type, profile
from creative_intelligence.db import get_connection, init_db


def _p(t, **kw):
    return {"name": "X", "product_type": t, "scent_notes": "notes",
            "physical_desc": "green skin, white flesh", **kw}


def test_normalize_type():
    assert normalize_type("Fruit") == "fruit"
    assert normalize_type("nope") == normalize_type(None) == "other"


def test_candle_prompt_unchanged_rules():
    s = build_image_prompt(_p("candle"), "SCENE")
    assert "LABEL" in s and "Rosé" in s and "hands/fingers/people" in s
    assert "WHAT IT LOOKS LIKE" not in s


def test_fruit_prompt_is_fruit_specific():
    s = build_image_prompt(_p("fruit", varieties="A — red — pink"), "SCENE")
    assert "how THIS fruit looks" in s and "WHAT IT LOOKS LIKE\ngreen skin" in s
    assert "VARIETIES" in s and "PLU" in s
    assert "jar" not in s and "Rosé" not in s and "hands/fingers/people" not in s


def test_fruit_menu_has_no_candle_archetypes():
    assert "flame_macro" not in PROFILES["fruit"]["formats"]
    assert "flame_macro" not in PROFILES["other"]["formats"]
    assert "flame_macro" in PROFILES["candle"]["formats"]


def test_hook_system_follows_type():
    assert "TASTE" in _system(_p("fruit")) and "ATMOSPHERE" not in _system(_p("fruit"))
    assert "ATMOSPHERE" in _system(_p("candle"))
    assert "Flavor notes: notes" in _product_line(_p("fruit"))
    assert "Scent notes (the ONLY ingredients): notes" in _product_line(_p("candle"))
    assert profile({})["label"] == "Other"


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "t.db"
    init_db(db_path)
    import creative_intelligence.webapp.app as app_module
    monkeypatch.setattr(app_module, "_db", lambda: get_connection(db_path))
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def test_create_and_change_type(client):
    pid = client.post("/api/adstudio/products",
                      json={"name": "Mango", "product_type": "fruit", "varieties": "v"}).get_json()["id"]
    p = client.get(f"/api/adstudio/product/{pid}").get_json()["product"]
    assert (p["product_type"], p["varieties"]) == ("fruit", "v")
    client.post(f"/api/adstudio/product/{pid}/update", json={"product_type": "bogus"})
    assert client.get(f"/api/adstudio/product/{pid}").get_json()["product"]["product_type"] == "other"
    cfg = client.get("/api/adstudio/config").get_json()["product_types"]
    assert set(cfg) == {"fruit", "candle", "other"} and "First Bite" in cfg["fruit"]["archetypes"]


def test_backfill_existing_products(tmp_path):
    db_path = tmp_path / "old.db"
    init_db(db_path)
    conn = get_connection(db_path)
    with conn:
        conn.execute("INSERT INTO adstudio_products (name, product_type) VALUES ('Guava Rosé Candle', '')")
        conn.execute("INSERT INTO adstudio_products (name, product_type) VALUES ('Kiwi berry', '')")
    conn.close()
    init_db(db_path)
    conn = get_connection(db_path)
    got = dict(conn.execute("SELECT name, product_type FROM adstudio_products").fetchall())
    conn.close()
    assert got == {"Guava Rosé Candle": "candle", "Kiwi berry": "fruit"}


def test_pages_and_nav(client):
    for path in ("/adstudio", "/core"):
        html = client.get(path).get_data(as_text=True)
        assert 'class="gnav"' in html and "Archive" in html
    core = client.get("/core").get_data(as_text=True)
    assert 'href="/core" title="Core" class="on"' in core
    assert 'data-pane="pCore"' not in client.get("/adstudio").get_data(as_text=True)
    labels = client.get("/labels").get_data(as_text=True)
    assert "Archive · Labels" in labels
