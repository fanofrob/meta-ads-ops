"""Winning patterns from real Meta data → Ad Studio hooks, and the Meta sync runner.

No live API: the sync runner and Claude client are faked.
"""
from __future__ import annotations

import json

import pytest

from creative_intelligence.adstudio import chat, winning
from creative_intelligence.db import get_connection, init_db
from creative_intelligence.ingest import meta_sync


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.db"
    init_db(path)
    return lambda: get_connection(path)


def _seed(conn, updated_at="2026-09-01T00:00:00"):
    with conn:
        conn.execute("INSERT INTO creatives (id, ad_id, ad_name, headline, primary_text)"
                     " VALUES ('c1','a1','Mango ad','Best Fruit On Earth','REAL honey mangos')")
        conn.execute("INSERT INTO creative_performance (creative_id, ad_id, date_range, snapshot_date,"
                     " spend, roas, cpa) VALUES ('c1','a1','30d','2026-09-01', 500, 3.1, 40)")
        for i, (name, ids) in enumerate([
                ("hook_type × angle: hook_type:social_proof + angle:fear", ["c1", "c2"]),
                ("angle × archetype: angle:fear + archetype:direct_response", ["c2", "c1"]),  # same winners
                ("hook_type × format: hook_type:quality_authenticity + format:image", ["c1"])]):
            conn.execute("INSERT INTO creative_patterns (pattern_name, winner_count, creative_count,"
                         " avg_roas, example_creative_ids, updated_at) VALUES (?,?,?,?,?,?)",
                         (name, 5 - i, 20, 2.5, json.dumps(ids), updated_at))


def test_pattern_label():
    assert winning.pattern_label(
        "hook_type × angle: hook_type:quality_authenticity + angle:authenticity_quality"
    ) == "Quality / authenticity hook · Authenticity angle"


def test_current_patterns_dedupes_and_has_real_examples(db):
    conn = db(); _seed(conn)
    ps = winning.current_patterns(conn)
    assert [p["label"] for p in ps] == ["Social proof hook · Fear angle",
                                        "Quality / authenticity hook · Image format"]
    assert ps[0]["examples"][0]["headline"] == "Best Fruit On Earth"
    block = winning.prompt_block(ps)
    assert f"[pattern_id {ps[0]['id']}]" in block and "REAL honey mangos" in block


def test_only_patterns_from_latest_sync(db):
    conn = db(); _seed(conn, updated_at="2026-04-07T00:00:00")
    with conn:
        conn.execute("INSERT INTO meta_sync_runs (started_at, status) VALUES ('2026-09-20T00:00:00','done')")
    assert winning.current_patterns(conn) == []


def _fake_runner(results):
    def run(argv):
        return results.get(argv[-1], (0, "ok"))
    return run


def test_sync_success_records_summary(db):
    conn = db(); _seed(conn, updated_at="2099-01-01T00:00:00")
    with conn:
        rid = conn.execute("INSERT INTO meta_sync_runs (started_at) VALUES ('2026-09-24T00:00:00')").lastrowid
    meta_sync.run_sync(db, rid, runner=_fake_runner({}))
    run = meta_sync.latest_run(db())
    assert run["status"] == "done" and run["n_ads"] == 1 and run["spend"] == 500
    assert run["n_patterns"] == 3


def test_sync_token_error_is_actionable(db):
    conn = db()
    with conn:
        rid = conn.execute("INSERT INTO meta_sync_runs (started_at) VALUES ('2026-09-24')").lastrowid
    out = "[ERROR] API error (code 190): Error validating access token: The session has been invalidated"
    meta_sync.run_sync(db, rid, runner=_fake_runner({"src/fetch_campaigns.py": (1, out)}))
    run = meta_sync.latest_run(db())
    assert run["status"] == "failed" and "META_ACCESS_TOKEN" in run["message"]


def test_repair_null_ids_only_drops_replaced_rows(db):
    conn = db()
    with conn:
        conn.execute("INSERT INTO creatives (id, ad_id) VALUES (NULL, 'a1')")
        conn.execute("INSERT INTO creatives (id, ad_id) VALUES (NULL, 'a2')")
        conn.execute("INSERT INTO creatives (id, ad_id) VALUES ('c1', 'a1')")
    assert meta_sync.repair_null_ids(conn) == 1
    rows = sorted((r[0] or "", r[1]) for r in conn.execute("SELECT id, ad_id FROM creatives"))
    assert rows == [("", "a2"), ("c1", "a1")]


class _FakeClient:
    def __init__(self, reply): self.reply, self.sent = reply, None
    @property
    def messages(self): return self
    def create(self, **kw):
        self.sent = kw
        block = type("B", (), {"type": "text", "text": self.reply})
        return type("R", (), {"content": [block]})


def test_suggest_hooks_uses_patterns_and_idea(monkeypatch):
    reply = json.dumps({"hooks": [{"hook_text": "a", "headline": "A", "pattern_id": 7},
                                  {"hook_text": "b", "headline": "B", "pattern_id": 999}]})
    fake = _FakeClient(reply)
    monkeypatch.setattr(chat, "_client", lambda: fake)
    pats = [{"id": 7, "label": "Social proof hook · Fear angle", "winner_count": 3,
             "creative_count": 9, "avg_roas": 2.0, "avg_cpa": 50,
             "examples": [{"headline": "Best Fruit", "text": "real mangos"}]}]
    hooks = chat.suggest_hooks({"name": "Mango", "product_type": "fruit"}, 2,
                               patterns=pats, idea="Mother's Day gift")
    sent = fake.sent["messages"][0]["content"][-1]["text"]
    assert "Best Fruit" in sent and "Mother's Day gift" in sent and "pattern_id" in sent
    assert [h["pattern_id"] for h in hooks] == [7, None]   # unknown ids are dropped


@pytest.fixture
def client(db, monkeypatch):
    import creative_intelligence.webapp.app as app_module
    monkeypatch.setattr(app_module, "_db", db)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def test_suggest_route_labels_sources(client, db, monkeypatch):
    conn = db(); _seed(conn)
    pid = client.post("/api/adstudio/products", json={"name": "Mango", "product_type": "fruit"}).get_json()["id"]
    seen = {}

    def fake(prod, n, brief, uris, patterns=None, idea=""):
        seen["patterns"], seen["idea"] = patterns, idea
        return [{"hook_text": "h", "headline": "H",
                 "pattern_id": patterns[0]["id"] if patterns else None}]
    monkeypatch.setattr(chat, "suggest_hooks", fake)

    h = client.post(f"/api/adstudio/product/{pid}/suggest-hooks", json={"pattern": "auto"}).get_json()["hooks"][0]
    assert len(seen["patterns"]) == 2 and h["source"] == "pattern" and h["archetype"] == h["pattern_label"]
    h = client.post(f"/api/adstudio/product/{pid}/suggest-hooks",
                    json={"pattern": "none", "idea": "gift"}).get_json()["hooks"][0]
    assert seen["patterns"] == [] and h["source"] == "idea"

    # saving keeps the pattern link
    pat_id = winning.current_patterns(conn)[0]["id"]
    client.post(f"/api/adstudio/product/{pid}/hook",
                json={"headline": "H", "source": "pattern", "pattern_id": pat_id})
    hooks = client.get(f"/api/adstudio/product/{pid}").get_json()["hooks"]
    assert hooks[0]["pattern_id"] == pat_id

    st = client.get("/api/adstudio/patterns").get_json()
    assert st["status"]["n_ads"] == 0 and len(st["patterns"]) == 2   # 7d window (no sync yet)


def test_no_patterns_and_no_idea_is_rejected(client):
    pid = client.post("/api/adstudio/products", json={"name": "Mango"}).get_json()["id"]
    r = client.post(f"/api/adstudio/product/{pid}/suggest-hooks", json={"pattern": "auto"})
    assert r.status_code == 400 and "sync from Meta" in r.get_json()["error"]


def test_sync_steps_read_env_fresh(tmp_path, monkeypatch):
    """A token pasted into .env while the server runs beats the stale inherited one."""
    (tmp_path / ".env").write_text("META_ACCESS_TOKEN=new-token\n")
    monkeypatch.setattr(meta_sync, "ROOT", tmp_path)
    monkeypatch.setenv("META_ACCESS_TOKEN", "old-token")
    assert meta_sync._step_env()["META_ACCESS_TOKEN"] == "new-token"
