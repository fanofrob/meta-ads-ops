"""Claude AI tagger: request shape, id mapping, caching, catalog skip, rule-tag replacement.
No live API — the Claude client is faked."""
from __future__ import annotations

import json

import pytest

from creative_intelligence.db import get_connection, init_db
from creative_intelligence.tagging import ai_tagger as T


class _Resp:
    def __init__(self, text, stop="end_turn"):
        self.stop_reason = stop
        self.content = [type("B", (), {"type": "text", "text": text})]


class _FakeClient:
    def __init__(self, stop="end_turn"):
        self.calls, self.stop = [], stop
        self.beta = self
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        ads = json.loads(kw["messages"][0]["content"].split("\n\n", 1)[1])
        out = [{"id": a["id"], "hook_type": "vs_supermarket", "angle": "tree_ripened_quality",
                "archetype": "avatar_talking_head", "emotional_trigger": "craving",
                "offer_style": "guarantee"} for a in ads]
        return _Resp(json.dumps({"ads": out}), self.stop)


def _rec(i, **kw):
    return {"id": f"c{i}", "ad_name": f"Concept {i}", "headline": "Best Fruit On Earth",
            "primary_text": f"This is how REAL cherries are supposed to taste {i}", **kw}


def test_classify_batch_request_and_mapping():
    fake = _FakeClient()
    recs = [_rec(1), _rec(2)]
    got = T.classify_batch(recs, client=fake)
    kw = fake.calls[0]
    assert kw["model"] == "claude-opus-5" and kw["fallbacks"] == "default"
    assert kw["betas"] == ["server-side-fallback-2026-07-01"]
    fmt = kw["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["properties"]["ads"]["items"]["properties"]["angle"]["enum"][0] == "flavor_experience"
    assert set(got) == {T.copy_key(r) for r in recs}          # short ids mapped back to copy keys
    assert got[T.copy_key(recs[0])]["hook_type"] == "vs_supermarket"


def test_refusal_returns_nothing():
    assert T.classify_batch([_rec(1)], client=_FakeClient(stop="refusal")) == {}


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "t.db"
    init_db(p)
    conn = get_connection(p)
    with conn:
        for cid in ("c1", "c2", "c3", "cat", "blank"):
            conn.execute("INSERT INTO creatives (id, ad_id) VALUES (?,?)", (cid, "a" + cid))
        conn.execute("INSERT INTO creative_tags (creative_id, tag_type, tag_value, source)"
                     " VALUES ('c1','hook_type','quality_authenticity','rule'),"
                     " ('c1','format','video','rule')")
    return conn


def test_tag_with_ai_caches_and_replaces_rule_tags(db):
    fake = _FakeClient()
    recs = [_rec(1), _rec(2), _rec(3, primary_text=_rec(1)["primary_text"], ad_name="Concept 1"),
            _rec(0, id="cat", headline="{{product.name}}"), _rec(0, id="blank", headline="", primary_text="")]
    stats = T.tag_with_ai(db, recs, classify=lambda b: T.classify_batch(b, client=fake), log=lambda m: None)
    assert stats == {"tagged": 3, "from_cache": 0, "classified": 2, "skipped": 2, "failed": 0}
    assert len(fake.calls) == 1                                   # c1 and c3 share copy → one entry
    tags = dict(db.execute("SELECT tag_type, tag_value FROM creative_tags WHERE creative_id='c1'").fetchall())
    assert tags["hook_type"] == "vs_supermarket" and tags["format"] == "video"   # format stays rule-based
    assert db.execute("SELECT COUNT(*) FROM creative_tags WHERE creative_id IN ('cat','blank')").fetchone()[0] == 0

    stats = T.tag_with_ai(db, recs, classify=lambda b: T.classify_batch(b, client=fake), log=lambda m: None)
    assert len(fake.calls) == 1 and stats["from_cache"] == 3 and stats["classified"] == 0


def test_failed_batch_keeps_rule_tags(db):
    def boom(batch):
        raise RuntimeError("overloaded")
    stats = T.tag_with_ai(db, [_rec(1)], classify=boom, log=lambda m: None)
    assert stats["failed"] == 1 and stats["tagged"] == 0
    assert db.execute("SELECT tag_value FROM creative_tags WHERE creative_id='c1' AND tag_type='hook_type'"
                      ).fetchone()[0] == "quality_authenticity"
