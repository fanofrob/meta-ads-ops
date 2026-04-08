"""
Tests for the production handoff module.

All offline — mock LLM, in-memory SQLite.
Follows the same fixture pattern as test_copilot.py.
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock, patch

from creative_intelligence.db import init_db, get_connection


# ─────────────────────────────────────────────
# Fixtures (same pattern as test_copilot.py)
# ─────────────────────────────────────────────

@pytest.fixture
def test_db(tmp_path):
    db_path = tmp_path / "test_production.db"
    init_db(db_path)
    conn = get_connection(db_path)
    yield conn
    conn.close()


@pytest.fixture
def flask_app(tmp_path, monkeypatch):
    db_path = tmp_path / "flask_production.db"
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
# Mock LLM helpers
# ─────────────────────────────────────────────

def _mock_static_brief_llm():
    mock = MagicMock()
    mock.complete_json.return_value = {
        "brief": {
            "hook": "The sweetest mangoes you've ever tasted.",
            "headline_options": ["Headline A", "Headline B", "Headline C"],
            "body_options": ["Body A.", "Body B.", "Body C."],
            "visual_direction": "Close-up of sliced mango on a wooden board.",
            "composition_notes": "Rule of thirds, product centred.",
            "product_visibility": "Product fills 60% of frame.",
            "text_overlay": "Bold sans-serif at top right.",
            "cta": "Shop Now",
            "why_it_works": "Quality-authenticity angle matches top ROAS patterns.",
        }
    }
    return mock


def _mock_ugc_brief_llm():
    mock = MagicMock()
    mock.complete_json.return_value = {
        "brief": {
            "creator_persona": "Casual home cook",
            "opening_line": "I never knew fruit could taste like this.",
            "talking_points": ["Point 1", "Point 2", "Point 3"],
            "demo_beats": ["Hold up the fruit", "Take a bite", "Show reaction"],
            "emotional_tone": "Genuine surprise and delight.",
            "scene_suggestions": ["Kitchen counter", "Outdoor table"],
            "cta": "Get yours at the link in bio.",
            "no_go_notes": ["Don't compare to supermarkets", "No scripted delivery"],
        }
    }
    return mock


def _mock_script_llm():
    mock = MagicMock()
    mock.complete_json.return_value = {
        "script_package": {
            "hook": "This mango changed how I think about fruit.",
            "body": "It's tree-ripened and shipped same-day. You can taste the difference.",
            "cta": "Order now at the link.",
            "duration": "20s",
            "beat_structure": [
                {"beat": "Hook", "seconds": "0-5s", "line": "This mango...", "direction": "Close-up on face"},
                {"beat": "Value", "seconds": "5-18s", "line": "Tree-ripened...", "direction": "B-roll of fruit"},
                {"beat": "CTA", "seconds": "18-20s", "line": "Order now...", "direction": "Link overlay"},
            ],
            "alternate_hooks": ["Alt hook A", "Alt hook B"],
            "alternate_ctas": ["Alt CTA A", "Alt CTA B"],
            "production_notes": "Natural lighting preferred.",
        }
    }
    return mock


# ─────────────────────────────────────────────
# Schema tests
# ─────────────────────────────────────────────

class TestProductionSchema:
    def test_production_outputs_table_exists(self, test_db):
        row = test_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='production_outputs'"
        ).fetchone()
        assert row is not None

    def test_production_packages_table_exists(self, test_db):
        row = test_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='production_packages'"
        ).fetchone()
        assert row is not None

    def test_outputs_has_expected_columns(self, test_db):
        cols = {row[1] for row in test_db.execute("PRAGMA table_info(production_outputs)")}
        assert {"id", "output_type", "source_iteration_id", "session_id",
                "product_id", "concept_text", "output_json", "output_md",
                "is_approved", "is_favorite", "created_at"} <= cols

    def test_packages_has_expected_columns(self, test_db):
        cols = {row[1] for row in test_db.execute("PRAGMA table_info(production_packages)")}
        assert {"id", "session_id", "product_id", "core_concept",
                "core_iteration_id", "variants_json", "audience", "goal",
                "pattern_alignment_json", "score_summary_json", "output_md",
                "is_approved", "is_favorite", "created_at"} <= cols

    def test_outputs_default_is_approved_zero(self, test_db):
        test_db.execute(
            "INSERT INTO production_outputs (output_type, concept_text, output_json) "
            "VALUES ('static_brief', 'test', '{}')"
        )
        test_db.commit()
        row = test_db.execute(
            "SELECT is_approved, is_favorite FROM production_outputs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row["is_approved"] == 0
        assert row["is_favorite"] == 0

    def test_packages_default_is_approved_zero(self, test_db):
        test_db.execute(
            "INSERT INTO production_packages (core_concept) VALUES ('test concept')"
        )
        test_db.commit()
        row = test_db.execute(
            "SELECT is_approved, is_favorite FROM production_packages ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row["is_approved"] == 0
        assert row["is_favorite"] == 0


# ─────────────────────────────────────────────
# Exporter tests
# ─────────────────────────────────────────────

class TestExporters:
    def test_static_brief_markdown_has_sections(self):
        from creative_intelligence.production.exporters import to_markdown
        data = {
            "hook": "Sweet mango hook.",
            "headline_options": ["Headline A", "Headline B"],
            "body_options": ["Body A"],
            "visual_direction": "Close-up shot.",
            "composition_notes": "Rule of thirds.",
            "product_visibility": "Centred product.",
            "text_overlay": "White text top-right.",
            "cta": "Shop Now",
            "why_it_works": "Matches winning patterns.",
        }
        md = to_markdown("static_brief", data)
        assert "## Static Ad Brief" in md
        assert "### Hook" in md
        assert "### Headline Options" in md
        assert "Sweet mango hook." in md

    def test_ugc_brief_markdown_has_sections(self):
        from creative_intelligence.production.exporters import to_markdown
        data = {
            "creator_persona": "Casual home cook",
            "opening_line": "I never knew fruit...",
            "talking_points": ["Point 1"],
            "demo_beats": ["Beat 1"],
            "emotional_tone": "Genuine delight.",
            "scene_suggestions": ["Kitchen"],
            "cta": "Get yours.",
            "no_go_notes": ["No scripted delivery"],
        }
        md = to_markdown("ugc_brief", data)
        assert "## UGC Creator Brief" in md
        assert "Opening Line" in md
        assert "No-Go Notes" in md

    def test_script_package_markdown_has_beat_table(self):
        from creative_intelligence.production.exporters import to_markdown
        data = {
            "hook": "Hook line.",
            "body": "Body text.",
            "cta": "Order now.",
            "duration": "20s",
            "beat_structure": [
                {"beat": "Hook", "seconds": "0-5s", "line": "Hook line.", "direction": "Close-up"}
            ],
            "alternate_hooks": ["Alt A"],
            "alternate_ctas": ["Alt CTA"],
            "production_notes": "Natural light.",
        }
        md = to_markdown("script_package", data)
        assert "## Script Package" in md
        assert "Beat Structure" in md
        assert "Hook" in md

    def test_test_package_markdown_has_variants_section(self):
        from creative_intelligence.production.exporters import to_markdown
        data = {
            "core_concept": "The core hook.",
            "core_iteration_id": 1,
            "core_score": {"structural": 60.0, "pattern_match": 40.0, "overall": 52.0},
            "variants": [
                {"concept_text": "Variant A", "action_type": "rewrite", "predicted_score": 55.0, "iteration_id": 2},
            ],
            "audience": "Health-conscious adults",
            "goal": "conversions",
            "pattern_alignment": {"pattern_name": "quality_authenticity × authenticity_quality", "hook_type": "quality_authenticity", "angle": "authenticity_quality", "winner_count": 3},
            "score_summary": {"avg": 53.5, "min": 52.0, "max": 55.0, "count": 2},
            "product_context": "Name: Mango",
        }
        md = to_markdown("test_package", data)
        assert "## Test Package" in md
        assert "Variants" in md
        assert "Pattern Alignment" in md

    def test_to_json_is_valid_json(self):
        from creative_intelligence.production.exporters import to_json
        data = {"hook": "Test hook", "headline_options": ["A", "B"]}
        result = to_json(data)
        parsed = json.loads(result)
        assert parsed["hook"] == "Test hook"

    def test_to_text_block_no_markdown_syntax(self):
        from creative_intelligence.production.exporters import to_text_block
        data = {
            "hook": "Test hook.",
            "headline_options": ["H1", "H2"],
            "body_options": ["B1"],
            "visual_direction": "Close-up.",
            "composition_notes": "Thirds.",
            "product_visibility": "Centred.",
            "text_overlay": "White.",
            "cta": "Shop Now",
            "why_it_works": "It works.",
        }
        text = to_text_block("static_brief", data)
        assert "##" not in text    # no Markdown headers
        assert "STATIC AD BRIEF" in text
        assert "HOOK" in text


# ─────────────────────────────────────────────
# Builder unit tests
# ─────────────────────────────────────────────

class TestBuildStaticBrief:
    def test_required_fields_present(self, test_db):
        from creative_intelligence.production.brief_builder import build_static_brief
        with patch("creative_intelligence.production.brief_builder.get_llm_client") as m:
            m.return_value = _mock_static_brief_llm()
            result = build_static_brief("Sweet mango hook.", None, test_db, dry_run=True)
        assert result["output_type"] == "static_brief"
        data = result["data"]
        # hook may be LLM-generated or fallback to input concept — just must be non-empty
        assert data["hook"]
        assert isinstance(data["headline_options"], list)
        assert len(data["headline_options"]) >= 1
        assert isinstance(data["body_options"], list)
        assert data["cta"]

    def test_dry_run_skips_db(self, test_db):
        from creative_intelligence.production.brief_builder import build_static_brief
        with patch("creative_intelligence.production.brief_builder.get_llm_client") as m:
            m.return_value = _mock_static_brief_llm()
            result = build_static_brief("Hook.", None, test_db, dry_run=True)
        assert result["output_id"] is None
        count = test_db.execute("SELECT COUNT(*) FROM production_outputs").fetchone()[0]
        assert count == 0

    def test_persist_when_not_dry_run(self, test_db):
        from creative_intelligence.production.brief_builder import build_static_brief
        with patch("creative_intelligence.production.brief_builder.get_llm_client") as m:
            m.return_value = _mock_static_brief_llm()
            result = build_static_brief("Hook.", None, test_db, dry_run=False)
        assert result["output_id"] is not None
        row = test_db.execute(
            "SELECT * FROM production_outputs WHERE id = ?", (result["output_id"],)
        ).fetchone()
        assert row is not None
        assert row["output_type"] == "static_brief"

    def test_output_md_not_empty(self, test_db):
        from creative_intelligence.production.brief_builder import build_static_brief
        with patch("creative_intelligence.production.brief_builder.get_llm_client") as m:
            m.return_value = _mock_static_brief_llm()
            result = build_static_brief("Hook.", None, test_db, dry_run=True)
        assert len(result["output_md"]) > 50


class TestBuildUGCBrief:
    def test_required_fields_present(self, test_db):
        from creative_intelligence.production.brief_builder import build_ugc_brief
        with patch("creative_intelligence.production.brief_builder.get_llm_client") as m:
            m.return_value = _mock_ugc_brief_llm()
            result = build_ugc_brief("I never knew fruit...", None, test_db, dry_run=True)
        assert result["output_type"] == "ugc_brief"
        data = result["data"]
        assert isinstance(data["talking_points"], list)
        assert isinstance(data["no_go_notes"], list)
        assert data["opening_line"]

    def test_persist(self, test_db):
        from creative_intelligence.production.brief_builder import build_ugc_brief
        with patch("creative_intelligence.production.brief_builder.get_llm_client") as m:
            m.return_value = _mock_ugc_brief_llm()
            result = build_ugc_brief("Hook.", None, test_db, dry_run=False)
        assert result["output_id"] is not None
        row = test_db.execute(
            "SELECT output_type FROM production_outputs WHERE id = ?", (result["output_id"],)
        ).fetchone()
        assert row["output_type"] == "ugc_brief"


class TestBuildScriptPackage:
    def test_required_fields_present(self, test_db):
        from creative_intelligence.production.brief_builder import build_script_package
        with patch("creative_intelligence.production.brief_builder.get_llm_client") as m:
            m.return_value = _mock_script_llm()
            result = build_script_package("This mango changed everything.", None, test_db, dry_run=True)
        assert result["output_type"] == "script_package"
        data = result["data"]
        assert isinstance(data["beat_structure"], list)
        assert len(data["beat_structure"]) > 0
        assert isinstance(data["alternate_hooks"], list)
        assert isinstance(data["alternate_ctas"], list)
        assert data["hook"]

    def test_beat_structure_has_required_keys(self, test_db):
        from creative_intelligence.production.brief_builder import build_script_package
        with patch("creative_intelligence.production.brief_builder.get_llm_client") as m:
            m.return_value = _mock_script_llm()
            result = build_script_package("Hook.", None, test_db, dry_run=True)
        for beat in result["data"]["beat_structure"]:
            assert "beat" in beat
            assert "line" in beat


class TestBuildTestPackage:
    def _insert_session(self, conn, session_id="test-session"):
        conn.execute(
            "INSERT OR IGNORE INTO copilot_sessions (session_id) VALUES (?)", (session_id,)
        )
        conn.commit()

    def _insert_iteration(self, conn, session_id, concept, action_type="rewrite", is_favorite=0):
        cursor = conn.execute(
            """INSERT INTO copilot_iterations
               (session_id, action_type, concept_text, is_favorite)
               VALUES (?, ?, ?, ?)""",
            (session_id, action_type, concept, is_favorite),
        )
        conn.commit()
        return cursor.lastrowid

    def test_no_llm_call(self, test_db):
        """build_test_package must not call the LLM."""
        from creative_intelligence.production.brief_builder import build_test_package
        with patch("creative_intelligence.production.brief_builder.get_llm_client") as m:
            build_test_package("Core concept.", None, None, test_db, dry_run=True)
            m.assert_not_called()

    def test_returns_required_keys(self, test_db):
        from creative_intelligence.production.brief_builder import build_test_package
        result = build_test_package("Core concept.", None, None, test_db, dry_run=True)
        assert result["output_type"] == "test_package"
        data = result["data"]
        for key in ("core_concept", "core_score", "variants", "score_summary",
                    "pattern_alignment", "audience", "goal"):
            assert key in data

    def test_score_summary_keys(self, test_db):
        from creative_intelligence.production.brief_builder import build_test_package
        result = build_test_package("A hook.", None, None, test_db, dry_run=True)
        summary = result["data"]["score_summary"]
        assert all(k in summary for k in ("avg", "min", "max", "count"))
        assert summary["count"] >= 1

    def test_pulls_session_variants(self, test_db):
        """Variants should include sibling iterations from the same session."""
        from creative_intelligence.production.brief_builder import build_test_package
        self._insert_session(test_db, "sess-1")
        self._insert_iteration(test_db, "sess-1", "Variant A")
        self._insert_iteration(test_db, "sess-1", "Variant B", is_favorite=1)

        result = build_test_package("Core.", None, "sess-1", test_db, dry_run=True)
        assert len(result["data"]["variants"]) >= 1

    def test_variants_include_iteration_id(self, test_db):
        """Each variant must carry iteration_id for lineage tracking."""
        from creative_intelligence.production.brief_builder import build_test_package
        self._insert_session(test_db, "sess-2")
        self._insert_iteration(test_db, "sess-2", "Variant A")

        result = build_test_package("Core.", None, "sess-2", test_db, dry_run=True)
        for v in result["data"]["variants"]:
            assert "iteration_id" in v
            assert v["iteration_id"] is not None

    def test_favorites_preferred_in_variants(self, test_db):
        """Favorited iterations should appear in variants."""
        from creative_intelligence.production.brief_builder import build_test_package
        self._insert_session(test_db, "sess-3")
        iter_id = self._insert_iteration(test_db, "sess-3", "Fav concept", is_favorite=1)

        result = build_test_package("Core.", None, "sess-3", test_db, dry_run=True)
        iter_ids = [v.get("iteration_id") for v in result["data"]["variants"]]
        assert iter_id in iter_ids

    def test_persists_when_not_dry_run(self, test_db):
        from creative_intelligence.production.brief_builder import build_test_package
        result = build_test_package("Core.", None, None, test_db, dry_run=False)
        assert result["package_id"] is not None
        row = test_db.execute(
            "SELECT * FROM production_packages WHERE id = ?", (result["package_id"],)
        ).fetchone()
        assert row is not None
        assert row["core_concept"] == "Core."


# ─────────────────────────────────────────────
# Schema validate helper
# ─────────────────────────────────────────────

class TestSchemaValidate:
    def test_validate_missing_field(self):
        from creative_intelligence.production.schemas import validate_output
        data = {"hook": "Test", "headline_options": ["A"], "body_options": ["B"]}
        # Missing several fields — should report them
        missing = validate_output("static_brief", data)
        assert len(missing) > 0

    def test_validate_complete_static_brief(self):
        from creative_intelligence.production.schemas import validate_output
        data = {
            "hook": "H", "headline_options": ["A"], "body_options": ["B"],
            "visual_direction": "V", "composition_notes": "C",
            "product_visibility": "P", "text_overlay": "T",
            "cta": "Shop", "why_it_works": "W",
        }
        missing = validate_output("static_brief", data)
        assert missing == []

    def test_validate_unknown_type(self):
        from creative_intelligence.production.schemas import validate_output
        errors = validate_output("unknown_type", {})
        assert len(errors) > 0


# ─────────────────────────────────────────────
# Dispatcher
# ─────────────────────────────────────────────

class TestBuildOutputDispatcher:
    def test_dispatches_static_brief(self, test_db):
        from creative_intelligence.production.brief_builder import build_output
        with patch("creative_intelligence.production.brief_builder.get_llm_client") as m:
            m.return_value = _mock_static_brief_llm()
            result = build_output("static_brief", "Hook.", None, test_db, dry_run=True)
        assert result["output_type"] == "static_brief"

    def test_dispatches_ugc_brief(self, test_db):
        from creative_intelligence.production.brief_builder import build_output
        with patch("creative_intelligence.production.brief_builder.get_llm_client") as m:
            m.return_value = _mock_ugc_brief_llm()
            result = build_output("ugc_brief", "Hook.", None, test_db, dry_run=True)
        assert result["output_type"] == "ugc_brief"

    def test_dispatches_script_package(self, test_db):
        from creative_intelligence.production.brief_builder import build_output
        with patch("creative_intelligence.production.brief_builder.get_llm_client") as m:
            m.return_value = _mock_script_llm()
            result = build_output("script_package", "Hook.", None, test_db, dry_run=True)
        assert result["output_type"] == "script_package"

    def test_dispatches_test_package(self, test_db):
        from creative_intelligence.production.brief_builder import build_output
        result = build_output("test_package", "Hook.", None, test_db, dry_run=True)
        assert result["output_type"] == "test_package"

    def test_raises_on_unknown_type(self, test_db):
        from creative_intelligence.production.brief_builder import build_output
        with pytest.raises(ValueError, match="Unknown output_type"):
            build_output("bad_type", "Hook.", None, test_db, dry_run=True)


# ─────────────────────────────────────────────
# Flask endpoint tests
# ─────────────────────────────────────────────

class TestProductionGenerateEndpoint:
    def _call(self, client, payload):
        return client.post(
            "/api/production/generate",
            json=payload,
            content_type="application/json",
        )

    def test_static_brief_returns_200(self, flask_app):
        with patch("creative_intelligence.production.brief_builder.get_llm_client") as m:
            m.return_value = _mock_static_brief_llm()
            r = self._call(flask_app, {
                "output_type": "static_brief",
                "concept": "Sweet mango hook.",
            })
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data["output_type"] == "static_brief"
        assert "data" in data
        assert "output_md" in data

    def test_ugc_brief_returns_200(self, flask_app):
        with patch("creative_intelligence.production.brief_builder.get_llm_client") as m:
            m.return_value = _mock_ugc_brief_llm()
            r = self._call(flask_app, {
                "output_type": "ugc_brief",
                "concept": "Fruit hook.",
            })
        assert r.status_code == 200

    def test_script_package_returns_200(self, flask_app):
        with patch("creative_intelligence.production.brief_builder.get_llm_client") as m:
            m.return_value = _mock_script_llm()
            r = self._call(flask_app, {
                "output_type": "script_package",
                "concept": "Script hook.",
            })
        assert r.status_code == 200

    def test_test_package_returns_200(self, flask_app):
        r = self._call(flask_app, {
            "output_type": "test_package",
            "concept": "Package concept.",
        })
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data["output_type"] == "test_package"

    def test_missing_output_type_returns_400(self, flask_app):
        r = self._call(flask_app, {"concept": "Hook."})
        assert r.status_code == 400

    def test_missing_concept_returns_400(self, flask_app):
        r = self._call(flask_app, {"output_type": "static_brief"})
        assert r.status_code == 400

    def test_unknown_output_type_returns_400(self, flask_app):
        r = self._call(flask_app, {"output_type": "bad_type", "concept": "Hook."})
        assert r.status_code == 400

    def test_persists_to_db_by_default(self, flask_app):
        with patch("creative_intelligence.production.brief_builder.get_llm_client") as m:
            m.return_value = _mock_static_brief_llm()
            r = self._call(flask_app, {
                "output_type": "static_brief",
                "concept": "Hook.",
                "dry_run": False,
            })
        data = json.loads(r.data)
        assert data.get("output_id") is not None


class TestProductionFetchEndpoint:
    def test_fetch_returns_stored_output(self, flask_app):
        with patch("creative_intelligence.production.brief_builder.get_llm_client") as m:
            m.return_value = _mock_static_brief_llm()
            r = flask_app.post(
                "/api/production/generate",
                json={"output_type": "static_brief", "concept": "Hook.", "dry_run": False},
                content_type="application/json",
            )
        output_id = json.loads(r.data)["output_id"]

        r2 = flask_app.get(f"/api/production/{output_id}")
        assert r2.status_code == 200
        data = json.loads(r2.data)
        assert data["output_type"] == "static_brief"

    def test_unknown_id_returns_404(self, flask_app):
        r = flask_app.get("/api/production/99999")
        assert r.status_code == 404


class TestProductionApproveEndpoint:
    def _create_output(self, flask_app):
        with patch("creative_intelligence.production.brief_builder.get_llm_client") as m:
            m.return_value = _mock_static_brief_llm()
            r = flask_app.post(
                "/api/production/generate",
                json={"output_type": "static_brief", "concept": "Hook.", "dry_run": False},
                content_type="application/json",
            )
        return json.loads(r.data)["output_id"]

    def test_approve_toggles_flag(self, flask_app):
        oid = self._create_output(flask_app)
        r = flask_app.post(
            f"/api/production/{oid}/approve",
            json={"approved": True},
            content_type="application/json",
        )
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data["is_approved"] is True

    def test_unapprove_toggles_flag(self, flask_app):
        oid = self._create_output(flask_app)
        flask_app.post(f"/api/production/{oid}/approve", json={"approved": True}, content_type="application/json")
        r = flask_app.post(f"/api/production/{oid}/approve", json={"approved": False}, content_type="application/json")
        assert json.loads(r.data)["is_approved"] is False


class TestProductionFavoriteEndpoint:
    def _create_output(self, flask_app):
        with patch("creative_intelligence.production.brief_builder.get_llm_client") as m:
            m.return_value = _mock_ugc_brief_llm()
            r = flask_app.post(
                "/api/production/generate",
                json={"output_type": "ugc_brief", "concept": "Hook.", "dry_run": False},
                content_type="application/json",
            )
        return json.loads(r.data)["output_id"]

    def test_sets_favorite(self, flask_app):
        oid = self._create_output(flask_app)
        r = flask_app.post(f"/api/production/{oid}/favorite", json={"favorite": True}, content_type="application/json")
        assert r.status_code == 200
        assert json.loads(r.data)["is_favorite"] is True

    def test_unsets_favorite(self, flask_app):
        oid = self._create_output(flask_app)
        flask_app.post(f"/api/production/{oid}/favorite", json={"favorite": True}, content_type="application/json")
        r = flask_app.post(f"/api/production/{oid}/favorite", json={"favorite": False}, content_type="application/json")
        assert json.loads(r.data)["is_favorite"] is False


class TestProductionExportEndpoint:
    def _create_output(self, flask_app):
        with patch("creative_intelligence.production.brief_builder.get_llm_client") as m:
            m.return_value = _mock_static_brief_llm()
            r = flask_app.post(
                "/api/production/generate",
                json={"output_type": "static_brief", "concept": "Hook.", "dry_run": False},
                content_type="application/json",
            )
        return json.loads(r.data)["output_id"]

    def test_md_export_returns_text(self, flask_app):
        oid = self._create_output(flask_app)
        r = flask_app.get(f"/api/production/{oid}/export?format=md")
        assert r.status_code == 200
        assert b"Static Ad Brief" in r.data or b"Hook" in r.data

    def test_json_export_is_valid_json(self, flask_app):
        oid = self._create_output(flask_app)
        r = flask_app.get(f"/api/production/{oid}/export?format=json")
        assert r.status_code == 200
        parsed = json.loads(r.data)
        assert isinstance(parsed, dict)

    def test_text_export_returns_plain_text(self, flask_app):
        oid = self._create_output(flask_app)
        r = flask_app.get(f"/api/production/{oid}/export?format=text")
        assert r.status_code == 200
        text = r.data.decode()
        assert "##" not in text   # no Markdown


class TestProductionListEndpoint:
    def test_returns_list(self, flask_app):
        r = flask_app.get("/api/production/list")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert isinstance(data, list)

    def test_includes_created_outputs(self, flask_app):
        with patch("creative_intelligence.production.brief_builder.get_llm_client") as m:
            m.return_value = _mock_static_brief_llm()
            flask_app.post(
                "/api/production/generate",
                json={"output_type": "static_brief", "concept": "Listed hook.", "dry_run": False},
                content_type="application/json",
            )
        r = flask_app.get("/api/production/list")
        items = json.loads(r.data)
        assert isinstance(items, list)
        assert len(items) >= 1
        assert any(item["output_type"] == "static_brief" for item in items)

    def test_list_items_have_expected_fields(self, flask_app):
        with patch("creative_intelligence.production.brief_builder.get_llm_client") as m:
            m.return_value = _mock_ugc_brief_llm()
            flask_app.post(
                "/api/production/generate",
                json={"output_type": "ugc_brief", "concept": "UGC hook.", "dry_run": False},
                content_type="application/json",
            )
        r = flask_app.get("/api/production/list")
        items = json.loads(r.data)
        assert isinstance(items, list)
        if items:
            item = items[0]
            for key in ("id", "output_type", "is_approved", "is_favorite", "created_at"):
                assert key in item
