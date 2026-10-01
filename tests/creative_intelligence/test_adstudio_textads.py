"""AI Ads — briefs, the fixed formats, Claude scenes, and the Ad Gallery review gate.

No live APIs: the Claude client and Nano Banana are faked; the DB and images
live in tmp_path.
"""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from creative_intelligence.adstudio import textads as T
from creative_intelligence.db import get_connection, init_db
from creative_intelligence.rendering import prompt_builder as R

FRUIT = {"name": "Kiwi Berry", "product_type": "fruit",
         "physical_desc": "grape-sized, smooth green skin, green flesh, black seeds"}
CANDLE = {"name": "Guava Rosé Candle", "product_type": "candle", "scent_notes": "Pink Guava"}
BRIEF = {"visual_direction": "bright, fresh, morning light"}


class _FakeClient:
    def __init__(self, reply):
        self.reply, self.sent = reply, None
        self.messages = self

    def create(self, **kw):
        self.sent = kw
        block = type("B", (), {"type": "text", "text": self.reply})
        return type("R", (), {"content": [block]})


# ── which formats run ──────────────────────────────────────────────────────
def test_variants_for_fruit_and_candle():
    assert len(T.variants_for(FRUIT, has_review=True)) == 12
    assert "social_proof" not in T.variants_for(FRUIT)          # no real review → skipped
    candle = T.variants_for(CANDLE, has_review=True)
    assert len(candle) == 9
    assert not {"origin_story", "craving_macro", "supermarket_contrast"} & set(candle)


# ── prompts: every word on the ad is ours ──────────────────────────────────
_INJECTED = [*R._URGENCY_SIGNALS, *R._SEASON_LABELS, "1 star", "97%", "STORE-BOUGHT",
             "TREE-RIPENED", "Absolutely incredible", "WARNING", "not responsible"]


@pytest.mark.parametrize("variant", list(T.VARIANTS))
@pytest.mark.parametrize("seed", range(7))
def test_variant_prompts_print_only_our_copy(variant, seed):
    p = T.build_variant_prompt(FRUIT, BRIEF, variant, "Kiwi minus the fuzz", "Eat it skin and all",
                               "Shop Now", "Sweeter than candy", "Maya R.", style_seed=seed)
    layout = p.split("AD FORMAT")[1].split("WHAT IT LOOKS LIKE")[0]
    assert not [x for x in _INJECTED if x.lower() in layout.lower()]
    assert "grape-sized" in p and "Reference photos" in p
    if T.VARIANTS[variant]["fields"]:
        assert '- "Kiwi minus the fuzz"' in p and "No other words" in p
        assert "top 14%" in p                                   # 9:16 safe zone
    else:
        assert "NO TEXT" in p and "Kiwi minus the fuzz" not in p


def test_variant_text_lists_exactly_what_prints():
    assert T.variant_text("bold_type", "A", "B", "C") == ["A"]
    assert T.variant_text("direct_response", "A", "B", "C") == ["A", "B", "C"]
    assert T.variant_text("social_proof", "A", review="So good", author="Maya") == ["A", "“So good”", "— Maya"]
    assert T.variant_text("social_proof", "A", review="So good") == ["A", "“So good”"]
    assert T.variant_text("minimal", "A", "B", "C") == []


def test_bold_type_never_uses_single_word_layout():
    for s in range(12):
        p = T.build_variant_prompt(FRUIT, BRIEF, "bold_type", "Kiwi minus the fuzz", style_seed=s)
        assert "single massive word" not in p


def test_candle_prompts_drop_fruit_wording():
    for v in ("minimal", "premium", "lifestyle_tagline"):
        p = T.build_variant_prompt(CANDLE, BRIEF, v, "Light the season", style_seed=2)
        assert "ripe" not in p.lower() and "fruit" not in p.split("AD FORMAT")[1].split("\n\n")[0].lower()
        assert "PINK GUAVA" in p.upper()


def test_fix_note_goes_first_and_scene_prompt_has_copy():
    p = T.build_variant_prompt(FRUIT, BRIEF, "premium", "Rare", corrections="smooth skin, not fuzzy")
    assert p.index("CORRECTIONS") < p.index("AD FORMAT")
    s = T.build_scene_prompt(FRUIT, "A hand holding kiwi berries over a picnic blanket.",
                             "Tiny kiwis. Big flavour.", "", "Shop Now", "headline across the sky")
    assert "finished 9:16 static social ad" in s and '- "Tiny kiwis. Big flavour."' in s
    assert "TEXT LAYOUT" in s and "composited on later" not in s


# ── Claude: brief + scenes ────────────────────────────────────────────────
PATS = [{"id": 7, "label": "Social proof hook · Fear angle", "winner_count": 3, "creative_count": 9,
         "avg_roas": 2.0, "avg_cpa": 50, "examples": [{"headline": "Best Fruit", "text": "real mangos"}]}]


def test_draft_brief_uses_patterns_idea_and_rules(monkeypatch):
    reply = json.dumps({"angle": "Kiwi with none of the work", "hook": "h", "pattern_id": 7,
                        "primary_texts": ["one", "two"], "cta": "Shop Now", "visual_direction": "v",
                        "variants": {"bold_type": {"headline": "NO FUZZ"}, "bogus": {"headline": "x"},
                                     "benefit_stack": {"headline": "a", "sub": "b"}}})
    fake = _FakeClient(reply)
    monkeypatch.setattr(T, "_client", lambda: fake)
    b = T.draft_brief(FRUIT, PATS, idea="for people who hate peeling")
    sent = fake.sent["messages"][0]["content"][-1]["text"]
    assert "Best Fruit" in sent and "hate peeling" in sent and "Never write a customer review" in sent
    assert '"social_proof"' not in sent                          # no review → not asked for
    assert b["pattern_id"] == 7 and set(b["variants"]) == {"bold_type", "benefit_stack"}
    assert b["primary_texts"] == ["one", "two"]


def test_draft_brief_drops_unknown_pattern(monkeypatch):
    monkeypatch.setattr(T, "_client", lambda: _FakeClient(json.dumps({"pattern_id": 99, "variants": {}})))
    assert T.draft_brief(FRUIT, PATS)["pattern_id"] is None


def test_invent_scenes_sees_prior_and_brief(monkeypatch):
    reply = json.dumps({"concepts": [
        {"archetype": "pov", "title": "Picnic POV", "scene": "s", "headline": "H", "body": "b"},
        {"archetype": "pov", "title": "no headline", "scene": "s"}]})
    fake = _FakeClient(reply)
    monkeypatch.setattr(T, "_client", lambda: fake)
    out = T.invent_scenes(FRUIT, {"angle": "ANGLE-X"}, 2, [{"archetype": "x", "title": "Old Scene"}])
    sent = fake.sent["messages"][0]["content"][-1]["text"]
    assert "ANGLE-X" in sent and "Old Scene" in sent
    assert [o["title"] for o in out] == ["Picnic POV"]           # copy-less concepts dropped


# ── routes ───────────────────────────────────────────────────────────────
@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.db"
    init_db(path)
    return lambda: get_connection(path)


@pytest.fixture
def client(db, monkeypatch, tmp_path):
    import creative_intelligence.webapp.app as app_module
    from creative_intelligence.adstudio import generate, store
    monkeypatch.setattr(app_module, "_db", db)
    monkeypatch.setattr(generate, "generate_ad", lambda prompt, uris, aspect: "fake://img")

    def fake_save(src, pid, aid, folder=None):
        f = tmp_path / f"ad{aid}.png"
        f.write_bytes(b"\x89PNG" + b"0" * 6000)
        return str(f)
    monkeypatch.setattr(store, "save_generated_image", fake_save)
    monkeypatch.setattr(store, "textads_dir", lambda: tmp_path)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _brief(client, monkeypatch, review=""):
    pid = client.post("/api/adstudio/products", json={"name": "Kiwi Berry", "product_type": "fruit"}).get_json()["id"]
    variants = {k: {"headline": f"{k} line", "sub": f"{k} sub"} for k in T.VARIANTS}
    monkeypatch.setattr(T, "draft_brief", lambda *a, **k: T.normalize_brief(
        {"angle": "No fuzz", "cta": "Shop Now", "primary_texts": ["p1", "p2"], "variants": variants}))
    b = client.post(f"/api/adstudio/product/{pid}/brief/draft",
                    json={"pattern": "none", "idea": "no peeling", "review_text": review}).get_json()
    return pid, b


def test_brief_needs_patterns_or_idea(client):
    pid = client.post("/api/adstudio/products", json={"name": "Kiwi"}).get_json()["id"]
    r = client.post(f"/api/adstudio/product/{pid}/brief/draft", json={"pattern": "none"})
    assert r.status_code == 400


def test_variants_queue_run_review_and_launch_kit(client, monkeypatch):
    pid, b = _brief(client, monkeypatch)
    assert b["brief"]["angle"] == "No fuzz" and b["idea"] == "no peeling"
    q = client.post(f"/api/adstudio/brief/{b['id']}/variants", json={}).get_json()["queued"]
    assert len(q) == 11 and "social_proof" not in [x["variant"] for x in q]

    ads = client.get(f"/api/adstudio/textads?brief_id={b['id']}").get_json()["ads"]
    dr = next(a for a in ads if a["variant"] == "direct_response")
    assert (dr["headline"], dr["subhead"], dr["cta"], dr["aspect_ratio"]) == \
        ("direct_response line", "direct_response sub", "Shop Now", "9:16")
    assert next(a for a in ads if a["variant"] == "bold_type")["subhead"] == ""   # not a bold_type field

    assert client.post(f"/api/adstudio/textad/{dr['id']}/run").get_json()["status"] == "done"
    gal = client.get(f"/api/adstudio/textads?done=1&product_id={pid}").get_json()
    assert [a["id"] for a in gal["ads"]] == [dr["id"]] and gal["counts"]["unreviewed"] == 1

    # ready is gated on the text check
    r = client.post(f"/api/adstudio/textad/{dr['id']}/review", json={"action": "ready"})
    assert r.status_code == 400
    r = client.post(f"/api/adstudio/textad/{dr['id']}/review", json={"text_checked": True, "action": "ready"})
    assert r.get_json()["is_ready"] == 1
    # unticking the check takes it out of ready
    r = client.post(f"/api/adstudio/textad/{dr['id']}/review", json={"text_checked": False})
    assert r.get_json()["is_ready"] == 0
    client.post(f"/api/adstudio/textad/{dr['id']}/review", json={"text_checked": True, "action": "ready"})

    z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/adstudio/export-textads?ready=1&product_id={pid}").data))
    rows = z.read("manifest.csv").decode().splitlines()
    assert len(rows) == 2 and "direct_response line" in rows[1] and "No fuzz" in rows[1]


def test_bulk_ready_skips_unchecked(client, monkeypatch):
    _, b = _brief(client, monkeypatch)
    ids = [x["id"] for x in client.post(f"/api/adstudio/brief/{b['id']}/variants", json={}).get_json()["queued"][:2]]
    client.post(f"/api/adstudio/textad/{ids[0]}/review", json={"text_checked": True})
    r = client.post("/api/adstudio/textads/bulk", json={"action": "ready", "ids": ids}).get_json()
    assert r["affected"] == 1


def test_edit_text_rerenders_and_clears_review(client, monkeypatch):
    _, b = _brief(client, monkeypatch, review="Sweeter than candy")
    q = client.post(f"/api/adstudio/brief/{b['id']}/variants", json={}).get_json()["queued"]
    assert len(q) == 12                                          # real review → Social Proof runs
    aid = q[0]["id"]
    client.post(f"/api/adstudio/textad/{aid}/run")
    client.post(f"/api/adstudio/textad/{aid}/review", json={"text_checked": True, "action": "ready"})
    client.post(f"/api/adstudio/textad/{aid}/regenerate", json={"headline": "New line", "fix": "sharper"})
    a = next(x for x in client.get(f"/api/adstudio/textads?brief_id={b['id']}").get_json()["ads"] if x["id"] == aid)
    assert (a["headline"], a["fix"], a["is_ready"], a["text_checked"], a["status"]) == \
        ("New line", "sharper", 0, 0, "done")

    sp = next(x for x in q if x["variant"] == "social_proof")["id"]
    client.post(f"/api/adstudio/textad/{sp}/run")
    import creative_intelligence.webapp.app as app_module
    prompt = app_module._db().execute("SELECT prompt FROM adstudio_textads WHERE id=?", (sp,)).fetchone()[0]
    assert "Sweeter than candy" in prompt


def test_scenes_avoid_prior_concepts_from_both_libraries(client, monkeypatch):
    pid, b = _brief(client, monkeypatch)
    import creative_intelligence.webapp.app as app_module
    conn = app_module._db()
    with conn:
        conn.execute("INSERT INTO adstudio_images (product_id, archetype, title, concept)"
                     " VALUES (?, 'pov', 'Old Image Scene', 'c')", (pid,))
    seen = {}

    def fake(prod, brief, n, prior, patterns, idea, uris):
        seen.update(prior=[p["title"] for p in prior], angle=brief["angle"], idea=idea)
        return [{"archetype": "pov", "title": "New", "scene": "s", "headline": "H", "sub": "",
                 "cta": "", "body": "b", "text_layout": "top"}]
    monkeypatch.setattr(T, "invent_scenes", fake)
    q = client.post(f"/api/adstudio/brief/{b['id']}/scenes", json={"n": 1}).get_json()["queued"]
    assert seen == {"prior": ["Old Image Scene"], "angle": "No fuzz", "idea": "no peeling"}
    client.post(f"/api/adstudio/brief/{b['id']}/scenes", json={"n": 1})
    assert "New" in seen["prior"]                                # its own scenes are logged too
    a = client.get(f"/api/adstudio/textads?brief_id={b['id']}").get_json()["ads"]
    assert any(x["id"] == q[0]["id"] and x["kind"] == "scene" and x["body"] == "b" for x in a)
