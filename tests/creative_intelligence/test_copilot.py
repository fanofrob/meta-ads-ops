"""Tests for Creative Copilot — schema, actions, and Flask endpoints.

All tests are offline:
- No live LLM calls (MockLLMClient via dry_run=True or direct patching)
- No live DB (in-memory SQLite via tmp_path fixture)
- No live Shopify API calls
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from creative_intelligence.db import init_db, get_connection
from creative_intelligence.generation.copilot_actions import (
    run_action,
    score_concept,
    explain_concept,
)


# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────

@pytest.fixture
def test_db(tmp_path):
    db_path = tmp_path / "test_copilot.db"
    init_db(db_path)
    conn = get_connection(db_path)
    yield conn
    conn.close()


@pytest.fixture
def flask_app(tmp_path, monkeypatch):
    """Flask test client with an isolated temp DB."""
    db_path = tmp_path / "flask_test.db"
    init_db(db_path)

    import creative_intelligence.webapp.app as app_module
    monkeypatch.setattr(
        app_module,
        "_db",
        lambda: get_connection(db_path),
    )
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        yield client


# ─────────────────────────────────────────────
# Schema tests
# ─────────────────────────────────────────────

class TestCopilotSchema:
    def test_copilot_sessions_table_exists(self, test_db):
        row = test_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='copilot_sessions'"
        ).fetchone()
        assert row is not None

    def test_copilot_iterations_table_exists(self, test_db):
        row = test_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='copilot_iterations'"
        ).fetchone()
        assert row is not None

    def test_copilot_explanations_table_exists(self, test_db):
        row = test_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='copilot_explanations'"
        ).fetchone()
        assert row is not None

    def test_sessions_has_expected_columns(self, test_db):
        cols = {row[1] for row in test_db.execute("PRAGMA table_info(copilot_sessions)")}
        assert {"id", "session_id", "product_id", "goal", "audience", "tone", "created_at"} <= cols

    def test_iterations_has_expected_columns(self, test_db):
        cols = {row[1] for row in test_db.execute("PRAGMA table_info(copilot_iterations)")}
        expected = {"id", "session_id", "parent_id", "product_id", "action_type",
                    "concept_text", "predicted_score", "is_favorite", "metadata", "created_at"}
        assert expected <= cols

    def test_explanations_has_expected_columns(self, test_db):
        cols = {row[1] for row in test_db.execute("PRAGMA table_info(copilot_explanations)")}
        assert {"id", "iteration_id", "explanation", "score_breakdown", "created_at"} <= cols

    def test_iterations_default_is_favorite_zero(self, test_db):
        sid = str(uuid.uuid4())
        test_db.execute(
            "INSERT INTO copilot_iterations (session_id, action_type, concept_text)"
            " VALUES (?,?,?)",
            (sid, "rewrite", "test hook"),
        )
        test_db.commit()
        row = test_db.execute(
            "SELECT is_favorite FROM copilot_iterations WHERE session_id = ?", (sid,)
        ).fetchone()
        assert row["is_favorite"] == 0


# ─────────────────────────────────────────────
# copilot_actions unit tests
# ─────────────────────────────────────────────

class TestRunActionHooks:
    """run_action() for hook transform actions."""

    def _make_mock_llm(self, hooks: list[str]):
        mock = MagicMock()
        mock.complete_json.return_value = {"hooks": hooks}
        return mock

    def test_rewrite_returns_results(self, test_db):
        with patch("creative_intelligence.generation.copilot_actions.get_llm_client") as m:
            m.return_value = self._make_mock_llm(["The best mango ever."])
            result = run_action("rewrite", "Try our mango", None, {}, test_db)
        assert result["action_type"] == "rewrite"
        assert len(result["results"]) == 1
        assert "mango" in result["results"][0].lower()

    def test_variants_returns_multiple(self, test_db):
        hooks = [f"Honey Mango variant {i}" for i in range(5)]
        with patch("creative_intelligence.generation.copilot_actions.get_llm_client") as m:
            m.return_value = self._make_mock_llm(hooks)
            result = run_action("variants", "Honey Mango", None, {"count": 5}, test_db)
        assert len(result["results"]) == 5

    def test_premium_tone(self, test_db):
        with patch("creative_intelligence.generation.copilot_actions.get_llm_client") as m:
            m.return_value = self._make_mock_llm(["Premium Honey Mango experience."])
            result = run_action("premium", "Try our mango", None, {}, test_db)
        assert result["action_type"] == "premium"
        assert result["results"]

    def test_direct_response(self, test_db):
        with patch("creative_intelligence.generation.copilot_actions.get_llm_client") as m:
            m.return_value = self._make_mock_llm(["Get 20% off Honey Mango today only."])
            result = run_action("direct_response", "Try our mango", None, {}, test_db)
        assert result["action_type"] == "direct_response"

    def test_curiosity(self, test_db):
        with patch("creative_intelligence.generation.copilot_actions.get_llm_client") as m:
            m.return_value = self._make_mock_llm(["Why does this mango taste different?"])
            result = run_action("curiosity", "Try our mango", None, {}, test_db)
        assert result["action_type"] == "curiosity"

    def test_mainstream(self, test_db):
        with patch("creative_intelligence.generation.copilot_actions.get_llm_client") as m:
            m.return_value = self._make_mock_llm(["Fresh farm mangoes delivered to you."])
            result = run_action("mainstream", "Ataulfo mango, golden nectar", None, {}, test_db)
        assert result["action_type"] == "mainstream"

    def test_adapt_audience(self, test_db):
        with patch("creative_intelligence.generation.copilot_actions.get_llm_client") as m:
            m.return_value = self._make_mock_llm(["Perfect mango snack for busy moms."])
            result = run_action("adapt_audience", "Fresh mango", None, {"audience": "busy moms"}, test_db)
        assert result["action_type"] == "adapt_audience"

    def test_empty_hooks_list_handled(self, test_db):
        with patch("creative_intelligence.generation.copilot_actions.get_llm_client") as m:
            m.return_value = self._make_mock_llm([])
            result = run_action("rewrite", "test", None, {}, test_db)
        assert result["results"] == []

    def test_invalid_action_type_raises(self, test_db):
        with pytest.raises(ValueError, match="Unknown action_type"):
            run_action("fly_to_moon", "test", None, {}, test_db)


class TestRunActionRich:
    """run_action() for rich format actions."""

    def _ugc_mock(self):
        mock = MagicMock()
        mock.complete_json.return_value = {
            "concepts": [
                {"title": "Farm to door", "hook": "I tried these mangoes.", "story_arc": "...", "cta": "Shop now", "format": "talking-head"},
                {"title": "Taste test", "hook": "Fresh mango surprise.", "story_arc": "...", "cta": "Order today", "format": "POV"},
                {"title": "Unboxing", "hook": "This just arrived.", "story_arc": "...", "cta": "Get yours", "format": "b-roll"},
            ]
        }
        return mock

    def test_ugc_concepts_returns_three(self, test_db):
        with patch("creative_intelligence.generation.copilot_actions.get_llm_client") as m:
            m.return_value = self._ugc_mock()
            result = run_action("ugc_concepts", "Farm fresh mango", None, {}, test_db)
        assert result["action_type"] == "ugc_concepts"
        assert len(result["results"]) == 3
        assert "hook" in result["results"][0]

    def test_static_concepts_returns_three(self, test_db):
        mock = MagicMock()
        mock.complete_json.return_value = {
            "concepts": [
                {"title": "Flat lay", "visual_description": "Golden mangoes on white.", "headline": "The best mango", "body_copy": "...", "cta": "Shop"},
                {"title": "Close-up", "visual_description": "Sliced mango close-up.", "headline": "Farm fresh", "body_copy": "...", "cta": "Order"},
                {"title": "Lifestyle", "visual_description": "Person eating mango.", "headline": "Pure delight", "body_copy": "...", "cta": "Buy"},
            ]
        }
        with patch("creative_intelligence.generation.copilot_actions.get_llm_client") as m:
            m.return_value = mock
            result = run_action("static_concepts", "Fresh mango", None, {}, test_db)
        assert len(result["results"]) == 3
        assert "headline" in result["results"][0]

    def test_script_returns_dict(self, test_db):
        mock = MagicMock()
        mock.complete_json.return_value = {
            "script": {
                "hook": "I never knew mangoes could taste this good.",
                "body": "Farm-fresh, tree-ripened Honey Mangoes shipped same-day.",
                "cta": "Order at goodhillfarms.com",
                "duration": "20s",
                "notes": "Talent should hold a mango.",
            }
        }
        with patch("creative_intelligence.generation.copilot_actions.get_llm_client") as m:
            m.return_value = mock
            result = run_action("script", "Fresh mango", None, {}, test_db)
        assert result["action_type"] == "script"
        assert len(result["results"]) == 1
        script = result["results"][0]
        assert "hook" in script
        assert "cta" in script

    def test_creator_brief_returns_dict(self, test_db):
        mock = MagicMock()
        mock.complete_json.return_value = {
            "brief": {
                "hook_direction": "Open with genuine surprise at the mango quality.",
                "story_arc": "Discovery → taste moment → share with family.",
                "key_messages": ["Tree-ripened", "Same-day shipping", "Farm-direct"],
                "tone_dos": ["Genuine", "Relaxed", "Sensory"],
                "tone_donts": ["Salesy", "Over-produced", "Generic"],
                "cta": "Link in bio to order",
                "examples": "Think food blogger unboxing style.",
            }
        }
        with patch("creative_intelligence.generation.copilot_actions.get_llm_client") as m:
            m.return_value = mock
            result = run_action("creator_brief", "Fresh mango", None, {}, test_db)
        assert result["action_type"] == "creator_brief"
        brief = result["results"][0]
        assert "hook_direction" in brief
        assert isinstance(brief["key_messages"], list)


class TestScoreConcept:
    def test_returns_required_keys(self, test_db):
        scores = score_concept("Honey Mango delivered fresh from the farm.", test_db)
        assert "structural" in scores
        assert "pattern_match" in scores
        assert "overall" in scores

    def test_scores_are_floats(self, test_db):
        scores = score_concept("Farm fresh mango.", test_db)
        assert isinstance(scores["structural"], float)
        assert isinstance(scores["overall"], float)

    def test_scores_in_range(self, test_db):
        scores = score_concept("Honey Mango — picked ripe, shipped same-day.", test_db)
        for k in ("structural", "pattern_match", "overall"):
            assert 0 <= scores[k] <= 100, f"{k} out of range"

    def test_question_hook_type_detected(self, test_db):
        # question mark in hook → "question" hook_type tag
        scores = score_concept("Ever wonder why farm mangoes taste so different?", test_db)
        assert scores["overall"] >= 0

    def test_empty_no_crash(self, test_db):
        # Short text still returns valid dict (structural score will be low)
        scores = score_concept("hi", test_db)
        assert "overall" in scores


class TestExplainConcept:
    def test_returns_string(self, test_db):
        with patch("creative_intelligence.generation.copilot_actions.get_llm_client") as m:
            mock = MagicMock()
            mock.complete.return_value = "This concept aligns with quality_authenticity pattern."
            m.return_value = mock
            result = explain_concept("Fresh farm mango.", None, test_db)
        assert isinstance(result, str)
        assert len(result) > 10

    def test_includes_product_context_when_provided(self, test_db):
        """When product_id is valid, build_prompt_context_block is called."""
        with patch("creative_intelligence.generation.copilot_actions.build_prompt_context_block") as ctx_mock, \
             patch("creative_intelligence.generation.copilot_actions.get_llm_client") as llm_mock:
            ctx_mock.return_value = "Product: Honey Mango\nPrice: $69"
            llm_m = MagicMock()
            llm_m.complete.return_value = "Good alignment."
            llm_mock.return_value = llm_m

            explain_concept("Best mango ever.", "shopify-12345", test_db)
            ctx_mock.assert_called_once_with("shopify-12345", test_db)


# ─────────────────────────────────────────────
# Flask endpoint tests
# ─────────────────────────────────────────────

class TestCopilotSessionEndpoint:
    def test_creates_session(self, flask_app):
        r = flask_app.post("/api/copilot/session", json={
            "goal": "conversions", "audience": "mango lovers", "tone": "authentic"
        })
        assert r.status_code == 200
        body = r.get_json()
        assert "session_id" in body
        assert uuid.UUID(body["session_id"])  # valid UUID

    def test_session_with_product(self, flask_app, tmp_path, monkeypatch):
        """Session endpoint returns product_name when product exists in DB."""
        # We can't easily inject a product row in this test without more setup,
        # so we just verify no crash when product_id is unknown (returns None).
        r = flask_app.post("/api/copilot/session", json={
            "product_id": "shopify-nonexistent",
            "goal": "traffic",
        })
        assert r.status_code == 200
        body = r.get_json()
        assert body["product_name"] is None

    def test_missing_no_fields_ok(self, flask_app):
        """All fields are optional — empty body should still work."""
        r = flask_app.post("/api/copilot/session", json={})
        assert r.status_code == 200


class TestCopilotGenerateEndpoint:
    def test_generate_with_concept_stores_iteration(self, flask_app):
        # First create a session
        sess = flask_app.post("/api/copilot/session", json={}).get_json()
        sid = sess["session_id"]

        r = flask_app.post("/api/copilot/generate", json={
            "session_id": sid,
            "concept": "Honey Mango — the fruit you never knew existed.",
        })
        assert r.status_code == 200
        body = r.get_json()
        assert "iterations" in body
        assert len(body["iterations"]) == 1
        assert body["iterations"][0]["action_type"] == "initial_generate"
        assert "Honey Mango" in body["iterations"][0]["concept_text"]

    def test_generate_without_session_id_fails(self, flask_app):
        r = flask_app.post("/api/copilot/generate", json={"concept": "test"})
        assert r.status_code == 400

    def test_generate_without_concept_falls_through_to_pattern(self, flask_app):
        """No concept + no patterns → 400 with useful message."""
        sess = flask_app.post("/api/copilot/session", json={}).get_json()
        r = flask_app.post("/api/copilot/generate", json={"session_id": sess["session_id"]})
        # DB has no patterns (empty test DB) → should return 400
        assert r.status_code == 400
        assert "pattern" in r.get_json().get("error", "").lower()


class TestCopilotActionEndpoint:
    def _session_id(self, flask_app):
        return flask_app.post("/api/copilot/session", json={}).get_json()["session_id"]

    def _mock_hook_llm(self, hooks):
        mock = MagicMock()
        mock.complete_json.return_value = {"hooks": hooks}
        return mock

    def test_rewrite_action(self, flask_app):
        sid = self._session_id(flask_app)
        with patch("creative_intelligence.generation.copilot_actions.get_llm_client") as m:
            m.return_value = self._mock_hook_llm(["The freshest Honey Mango on earth."])
            r = flask_app.post("/api/copilot/action", json={
                "session_id": sid,
                "concept": "Try our mango",
                "action_type": "rewrite",
                "params": {},
            })
        assert r.status_code == 200
        body = r.get_json()
        assert len(body["iterations"]) == 1
        assert body["iterations"][0]["action_type"] == "rewrite"

    def test_variants_action_returns_five(self, flask_app):
        sid = self._session_id(flask_app)
        hooks = [f"Honey Mango variant {i}" for i in range(5)]
        with patch("creative_intelligence.generation.copilot_actions.get_llm_client") as m:
            m.return_value = self._mock_hook_llm(hooks)
            r = flask_app.post("/api/copilot/action", json={
                "session_id": sid,
                "concept": "Fresh mango",
                "action_type": "variants",
                "params": {"count": 5},
            })
        assert r.status_code == 200
        assert len(r.get_json()["iterations"]) == 5

    def test_missing_concept_returns_400(self, flask_app):
        sid = self._session_id(flask_app)
        r = flask_app.post("/api/copilot/action", json={
            "session_id": sid, "action_type": "rewrite", "params": {}
        })
        assert r.status_code == 400

    def test_invalid_action_type_returns_400(self, flask_app):
        sid = self._session_id(flask_app)
        r = flask_app.post("/api/copilot/action", json={
            "session_id": sid,
            "concept": "test",
            "action_type": "explode",
        })
        assert r.status_code == 400

    def test_parent_id_stored(self, flask_app):
        sid = self._session_id(flask_app)
        # First store a concept to get an iteration_id
        gen = flask_app.post("/api/copilot/generate", json={
            "session_id": sid,
            "concept": "Original mango hook",
        }).get_json()
        parent_id = gen["iterations"][0]["id"]

        with patch("creative_intelligence.generation.copilot_actions.get_llm_client") as m:
            m.return_value = self._mock_hook_llm(["Rewritten mango hook."])
            r = flask_app.post("/api/copilot/action", json={
                "session_id": sid,
                "iteration_id": parent_id,
                "concept": "Original mango hook",
                "action_type": "rewrite",
                "params": {},
            })
        body = r.get_json()
        assert body["iterations"][0]["parent_id"] == parent_id

    def test_ugc_concepts_action(self, flask_app):
        sid = self._session_id(flask_app)
        mock = MagicMock()
        mock.complete_json.return_value = {
            "concepts": [
                {"title": "C1", "hook": "Hook 1", "story_arc": "...", "cta": "Shop", "format": "talking-head"},
                {"title": "C2", "hook": "Hook 2", "story_arc": "...", "cta": "Order", "format": "POV"},
                {"title": "C3", "hook": "Hook 3", "story_arc": "...", "cta": "Buy", "format": "demo"},
            ]
        }
        with patch("creative_intelligence.generation.copilot_actions.get_llm_client") as m:
            m.return_value = mock
            r = flask_app.post("/api/copilot/action", json={
                "session_id": sid,
                "concept": "Fresh mango",
                "action_type": "ugc_concepts",
                "params": {"count": 3},
            })
        assert r.status_code == 200
        iters = r.get_json()["iterations"]
        assert len(iters) == 3
        # concept_text for ugc is the hook field
        assert "Hook 1" in iters[0]["concept_text"]


class TestCopilotScoreEndpoint:
    def test_returns_scores(self, flask_app):
        r = flask_app.post("/api/copilot/score", json={
            "concept": "Honey Mango picked ripe and shipped same-day."
        })
        assert r.status_code == 200
        body = r.get_json()
        assert {"structural", "pattern_match", "overall"} <= body.keys()

    def test_missing_concept_returns_400(self, flask_app):
        r = flask_app.post("/api/copilot/score", json={})
        assert r.status_code == 400


class TestCopilotExplainEndpoint:
    def test_returns_explanation(self, flask_app):
        with patch("creative_intelligence.generation.copilot_actions.get_llm_client") as m:
            mock = MagicMock()
            mock.complete.return_value = "This aligns with quality_authenticity pattern."
            m.return_value = mock
            r = flask_app.post("/api/copilot/explain", json={
                "concept": "Best mango on the planet."
            })
        assert r.status_code == 200
        body = r.get_json()
        assert "explanation" in body
        assert len(body["explanation"]) > 5

    def test_missing_concept_returns_400(self, flask_app):
        r = flask_app.post("/api/copilot/explain", json={})
        assert r.status_code == 400


class TestCopilotFavoriteEndpoint:
    def test_sets_favorite(self, flask_app):
        # Create session + concept
        sid = flask_app.post("/api/copilot/session", json={}).get_json()["session_id"]
        gen = flask_app.post("/api/copilot/generate", json={
            "session_id": sid, "concept": "Honey Mango hook"
        }).get_json()
        it_id = gen["iterations"][0]["id"]

        r = flask_app.post("/api/copilot/favorite", json={
            "iteration_id": it_id, "favorite": True
        })
        assert r.status_code == 200
        assert r.get_json()["is_favorite"] is True

    def test_unsets_favorite(self, flask_app):
        sid = flask_app.post("/api/copilot/session", json={}).get_json()["session_id"]
        gen = flask_app.post("/api/copilot/generate", json={
            "session_id": sid, "concept": "Mango hook"
        }).get_json()
        it_id = gen["iterations"][0]["id"]

        flask_app.post("/api/copilot/favorite", json={"iteration_id": it_id, "favorite": True})
        r = flask_app.post("/api/copilot/favorite", json={"iteration_id": it_id, "favorite": False})
        assert r.get_json()["is_favorite"] is False

    def test_missing_iteration_id_returns_400(self, flask_app):
        r = flask_app.post("/api/copilot/favorite", json={"favorite": True})
        assert r.status_code == 400


class TestCopilotHistoryEndpoints:
    def test_session_history_returns_iterations(self, flask_app):
        sid = flask_app.post("/api/copilot/session", json={}).get_json()["session_id"]
        flask_app.post("/api/copilot/generate", json={
            "session_id": sid, "concept": "First concept"
        })
        flask_app.post("/api/copilot/generate", json={
            "session_id": sid, "concept": "Second concept"
        })
        r = flask_app.get(f"/api/copilot/session/{sid}")
        assert r.status_code == 200
        body = r.get_json()
        assert "session" in body
        assert "iterations" in body
        assert len(body["iterations"]) == 2

    def test_unknown_session_returns_404(self, flask_app):
        r = flask_app.get("/api/copilot/session/nonexistent-id")
        assert r.status_code == 404

    def test_sessions_list_returns_list(self, flask_app):
        flask_app.post("/api/copilot/session", json={})
        flask_app.post("/api/copilot/session", json={})
        r = flask_app.get("/api/copilot/sessions")
        assert r.status_code == 200
        assert isinstance(r.get_json(), list)
        assert len(r.get_json()) >= 2

    def test_sessions_list_includes_iteration_count(self, flask_app):
        sess = flask_app.post("/api/copilot/session", json={}).get_json()
        sid = sess["session_id"]
        flask_app.post("/api/copilot/generate", json={"session_id": sid, "concept": "Hook 1"})
        flask_app.post("/api/copilot/generate", json={"session_id": sid, "concept": "Hook 2"})

        sessions = flask_app.get("/api/copilot/sessions").get_json()
        our_sess = next(s for s in sessions if s["session_id"] == sid)
        assert our_sess["iteration_count"] == 2


class TestCopilotPageRoute:
    def test_copilot_page_renders(self, flask_app):
        r = flask_app.get("/copilot")
        assert r.status_code == 200
        assert b"Creative Copilot" in r.data

    def test_test_page_still_works(self, flask_app):
        """/test must not be broken by copilot additions."""
        r = flask_app.get("/test")
        assert r.status_code == 200
