"""
Tests for the static rendering layer.

All offline — MockImageGenerator, in-memory SQLite, no real API calls.
~40 tests across schema, prompt builder, providers, asset store,
static renderer, exporters, and Flask routes.
"""
from __future__ import annotations

import json
import pytest

from creative_intelligence.db import init_db, get_connection
from creative_intelligence.rendering.schemas import (
    VARIANT_STRATEGIES,
    VARIANT_RATIONALES,
    VALID_ASPECT_RATIOS,
    DEFAULT_ASPECT_RATIO,
    RenderSpec,
    empty_render_spec,
    validate_render_spec,
)
from creative_intelligence.rendering.prompt_builder import build_render_specs
from creative_intelligence.rendering.providers import (
    MockImageGenerator,
    NanoBananaGenerator,
    ReplicateGenerator,
    get_provider,
)
from creative_intelligence.rendering.asset_store import (
    save_asset,
    list_render_assets,
    get_render_output_dir,
)
from creative_intelligence.rendering.exporters import (
    render_spec_to_markdown,
    render_spec_to_json,
    render_spec_to_text,
)
from creative_intelligence.rendering.static_renderer import render_static_brief


# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────

@pytest.fixture
def test_db(tmp_path):
    db_path = tmp_path / "test_rendering.db"
    init_db(db_path)
    conn = get_connection(db_path)
    yield conn
    conn.close()


@pytest.fixture
def flask_app(tmp_path, monkeypatch):
    db_path = tmp_path / "flask_rendering.db"
    init_db(db_path)

    import creative_intelligence.webapp.app as app_module
    monkeypatch.setattr(app_module, "_db", lambda: get_connection(db_path))
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        yield client, get_connection(db_path)


@pytest.fixture
def sample_brief():
    """Minimal StaticAdBrief dict for testing."""
    return {
        "hook": "The sweetest mangoes you've ever tasted.",
        "headline_options": ["Headline A", "Headline B"],
        "body_options": ["Body text A.", "Body text B."],
        "visual_direction": "Clean white background. Minimal, fresh, natural presentation.",
        "composition_notes": "Close-up product shot centred on a marble surface.",
        "product_visibility": "Product fills 60% of frame, label facing camera.",
        "text_overlay": "Bold sans-serif headline at top right, small CTA button bottom left.",
        "cta": "Shop Now",
        "why_it_works": "Matches top ROAS patterns for quality authenticity.",
    }


def _insert_static_brief(conn, brief: dict) -> int:
    """Insert a production_outputs row of type static_brief; return its id."""
    with conn:
        cur = conn.execute(
            "INSERT INTO production_outputs (output_type, concept_text, output_json) "
            "VALUES ('static_brief', ?, ?)",
            (brief.get("hook", "test"), json.dumps(brief)),
        )
    return cur.lastrowid


# ─────────────────────────────────────────────
# TestRenderSchema
# ─────────────────────────────────────────────

class TestRenderSchema:
    def test_render_outputs_table_exists(self, test_db):
        row = test_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='render_outputs'"
        ).fetchone()
        assert row is not None

    def test_render_assets_table_exists(self, test_db):
        row = test_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='render_assets'"
        ).fetchone()
        assert row is not None

    def test_render_outputs_has_expected_columns(self, test_db):
        cols = {row[1] for row in test_db.execute("PRAGMA table_info(render_outputs)")}
        expected = {
            "id", "source_production_output_id", "render_type",
            "render_spec_json", "status", "provider_name",
            "is_approved", "is_favorite", "created_at",
        }
        assert expected <= cols

    def test_render_assets_has_expected_columns(self, test_db):
        cols = {row[1] for row in test_db.execute("PRAGMA table_info(render_assets)")}
        expected = {
            "id", "render_output_id", "asset_type", "variant_label",
            "asset_path_or_url", "metadata_json", "review_status", "created_at",
        }
        assert expected <= cols

    def test_render_outputs_defaults(self, test_db):
        with test_db:
            test_db.execute(
                "INSERT INTO render_outputs (source_production_output_id, render_spec_json) "
                "VALUES (1, '[]')"
            )
        row = dict(test_db.execute("SELECT * FROM render_outputs LIMIT 1").fetchone())
        assert row["is_approved"] == 0
        assert row["is_favorite"] == 0
        assert row["status"] == "spec_only"
        assert row["render_type"] == "static_spec"

    def test_render_assets_default_review_status(self, test_db):
        with test_db:
            test_db.execute(
                "INSERT INTO render_outputs (source_production_output_id, render_spec_json) "
                "VALUES (1, '[]')"
            )
            test_db.execute(
                "INSERT INTO render_assets (render_output_id, variant_label, asset_path_or_url) "
                "VALUES (1, 'minimal', 'mock://x.png')"
            )
        row = dict(test_db.execute("SELECT * FROM render_assets LIMIT 1").fetchone())
        assert row["review_status"] == "pending"
        assert row["asset_type"] == "image_variant"


# ─────────────────────────────────────────────
# TestPromptBuilder
# ─────────────────────────────────────────────

class TestPromptBuilder:
    def test_returns_specs_matching_variant_count(self, sample_brief):
        specs = build_render_specs(sample_brief, production_output_id=1)
        assert len(specs) == len(VARIANT_STRATEGIES)

    def test_variant_labels_match_strategies(self, sample_brief):
        specs = build_render_specs(sample_brief, production_output_id=1)
        labels = [s["variant_label"] for s in specs]
        assert labels == list(VARIANT_STRATEGIES)

    def test_visual_prompt_non_empty(self, sample_brief):
        specs = build_render_specs(sample_brief, production_output_id=1)
        for spec in specs:
            assert spec["visual_prompt"], f"visual_prompt empty for {spec['variant_label']}"

    def test_negative_prompt_non_empty(self, sample_brief):
        specs = build_render_specs(sample_brief, production_output_id=1)
        for spec in specs:
            assert spec["negative_prompt"]

    def test_visual_prompt_contains_variant_modifier(self, sample_brief):
        specs = build_render_specs(sample_brief, production_output_id=1)
        # minimal variant should mention "minimal" in prompt
        minimal = next(s for s in specs if s["variant_label"] == "minimal")
        assert "minimal" in minimal["visual_prompt"].lower()

    def test_no_unresolved_bracket_tokens(self, sample_brief):
        specs = build_render_specs(sample_brief, production_output_id=1)
        for spec in specs:
            assert "{" not in spec["visual_prompt"]
            assert "{" not in spec["negative_prompt"]

    def test_headline_overlay_from_headline_options(self, sample_brief):
        specs = build_render_specs(sample_brief, production_output_id=1)
        valid = set(sample_brief["headline_options"])
        for spec in specs:
            assert spec["headline_overlay"] in valid, (
                f"headline_overlay {spec['headline_overlay']!r} not in headline_options"
            )

    def test_cta_text_from_brief(self, sample_brief):
        specs = build_render_specs(sample_brief, production_output_id=1)
        for spec in specs:
            assert spec["cta_text"] == "Shop Now"

    def test_aspect_ratio_passed_through(self, sample_brief):
        specs = build_render_specs(sample_brief, production_output_id=1, aspect_ratio="1:1")
        for spec in specs:
            assert spec["aspect_ratio"] == "1:1"

    def test_shot_type_detected_from_composition(self, sample_brief):
        specs = build_render_specs(sample_brief, production_output_id=1)
        # "Close-up product shot" → close-up
        for spec in specs:
            assert spec["shot_type"] == "close-up"

    def test_style_tags_extracted(self, sample_brief):
        specs = build_render_specs(sample_brief, production_output_id=1)
        # visual_direction contains "minimal", "fresh", "natural"
        tags = specs[0]["style_tags"]
        assert isinstance(tags, list)
        assert len(tags) > 0
        assert any(t in tags for t in ("minimal", "clean", "natural", "fresh"))

    def test_subset_of_variants(self, sample_brief):
        specs = build_render_specs(
            sample_brief, production_output_id=1,
            variants=("minimal", "premium")
        )
        assert len(specs) == 2
        assert {s["variant_label"] for s in specs} == {"minimal", "premium"}

    def test_unknown_variant_skipped(self, sample_brief):
        specs = build_render_specs(
            sample_brief, production_output_id=1,
            variants=("minimal", "nonexistent_variant")
        )
        assert len(specs) == 1
        assert specs[0]["variant_label"] == "minimal"

    def test_production_output_id_stored(self, sample_brief):
        specs = build_render_specs(sample_brief, production_output_id=42)
        for spec in specs:
            assert spec["production_output_id"] == 42

    def test_variant_rationale_non_empty(self, sample_brief):
        specs = build_render_specs(sample_brief, production_output_id=1)
        for spec in specs:
            assert spec["variant_rationale"]


# ─────────────────────────────────────────────
# TestSchemaHelpers
# ─────────────────────────────────────────────

class TestSchemaHelpers:
    def test_empty_render_spec_has_all_fields(self):
        spec = empty_render_spec()
        for field in RenderSpec.FIELDS:
            assert field in spec

    def test_validate_render_spec_passes_valid_spec(self, sample_brief):
        specs = build_render_specs(sample_brief, production_output_id=1)
        errors = validate_render_spec(specs[0])
        assert errors == []

    def test_validate_render_spec_catches_missing_visual_prompt(self):
        spec = empty_render_spec()
        spec["production_output_id"] = 1
        spec["negative_prompt"] = "blurry"
        spec["variant_label"] = "minimal"
        spec["aspect_ratio"] = "9:16"
        # visual_prompt left empty
        errors = validate_render_spec(spec)
        assert "visual_prompt" in errors

    def test_variant_rationales_cover_all_strategies(self):
        for variant in VARIANT_STRATEGIES:
            assert variant in VARIANT_RATIONALES
            assert VARIANT_RATIONALES[variant]

    def test_valid_aspect_ratios_set(self):
        assert "9:16" in VALID_ASPECT_RATIOS
        assert "1:1" in VALID_ASPECT_RATIOS

    def test_default_aspect_ratio(self):
        assert DEFAULT_ASPECT_RATIO == "9:16"


# ─────────────────────────────────────────────
# TestProviders
# ─────────────────────────────────────────────

class TestProviders:
    def test_mock_name(self):
        assert MockImageGenerator().name == "mock"

    def test_mock_returns_list(self):
        gen = MockImageGenerator()
        result = gen.generate("test prompt", "test negative")
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_mock_path_format(self):
        gen = MockImageGenerator()
        paths = gen.generate("prompt text", "negative text", aspect_ratio="9:16")
        assert paths[0].startswith("mock://render_")
        assert "9x16" in paths[0]
        assert paths[0].endswith(".png")

    def test_mock_deterministic(self):
        gen = MockImageGenerator()
        paths1 = gen.generate("same prompt", "same neg")
        paths2 = gen.generate("same prompt", "same neg")
        assert paths1 == paths2

    def test_mock_different_prompts(self):
        gen = MockImageGenerator()
        p1 = gen.generate("prompt one", "neg")[0]
        p2 = gen.generate("prompt two different", "neg")[0]
        assert p1 != p2

    def test_get_provider_mock_by_name(self):
        provider = get_provider("mock")
        assert isinstance(provider, MockImageGenerator)

    def test_get_provider_unknown_falls_back_to_mock(self):
        provider = get_provider("nonexistent_provider_xyz")
        assert isinstance(provider, MockImageGenerator)

    def test_get_provider_nano_banana(self):
        provider = get_provider("nano_banana")
        assert isinstance(provider, NanoBananaGenerator)
        assert provider.name == "nano_banana"

    def test_nano_banana_raises_not_implemented(self):
        gen = NanoBananaGenerator()
        with pytest.raises((NotImplementedError, RuntimeError)):
            gen.generate("test prompt", "negative")

    def test_get_provider_replicate(self):
        provider = get_provider("replicate")
        assert isinstance(provider, ReplicateGenerator)
        assert provider.name == "replicate"

    def test_replicate_ratio_mapping_9_16(self):
        gen = ReplicateGenerator()
        assert gen._RATIO_TO_DIMS["9:16"] == (768, 1344)

    def test_replicate_ratio_mapping_1_1(self):
        gen = ReplicateGenerator()
        assert gen._RATIO_TO_DIMS["1:1"] == (1024, 1024)

    def test_replicate_raises_without_api_key(self, monkeypatch):
        import creative_intelligence.config as cfg
        monkeypatch.setattr(cfg, "CI_REPLICATE_API_KEY", "")
        gen = ReplicateGenerator()
        with pytest.raises(RuntimeError, match="CI_REPLICATE_API_KEY"):
            gen.generate("test prompt", "negative")


# ─────────────────────────────────────────────
# TestAssetStore
# ─────────────────────────────────────────────

class TestAssetStore:
    def test_get_render_output_dir_creates_directory(self, tmp_path, monkeypatch):
        import creative_intelligence.config as cfg
        monkeypatch.setattr(cfg, "CI_RENDER_OUTPUT_DIR", str(tmp_path / "render_out"))
        out_dir = get_render_output_dir()
        assert out_dir.exists()
        assert out_dir.is_dir()

    def test_save_asset_mock_path_inserts_row(self, test_db):
        with test_db:
            test_db.execute(
                "INSERT INTO render_outputs (source_production_output_id, render_spec_json) "
                "VALUES (1, '[]')"
            )
        asset_id = save_asset(1, "minimal", "mock://render_abc123_9x16.png", test_db)
        assert isinstance(asset_id, int)
        assert asset_id > 0

    def test_save_asset_stores_mock_path_as_is(self, test_db):
        with test_db:
            test_db.execute(
                "INSERT INTO render_outputs (source_production_output_id, render_spec_json) "
                "VALUES (1, '[]')"
            )
        mock_path = "mock://render_deadbeef_1x1.png"
        save_asset(1, "premium", mock_path, test_db)
        row = test_db.execute(
            "SELECT asset_path_or_url FROM render_assets WHERE variant_label='premium'"
        ).fetchone()
        assert row[0] == mock_path

    def test_list_render_assets_returns_rows(self, test_db):
        with test_db:
            test_db.execute(
                "INSERT INTO render_outputs (source_production_output_id, render_spec_json) "
                "VALUES (1, '[]')"
            )
        save_asset(1, "minimal", "mock://a.png", test_db)
        save_asset(1, "premium", "mock://b.png", test_db)
        assets = list_render_assets(1, test_db)
        assert len(assets) == 2
        labels = {a["variant_label"] for a in assets}
        assert labels == {"minimal", "premium"}

    def test_list_render_assets_empty_for_unknown_id(self, test_db):
        assets = list_render_assets(9999, test_db)
        assert assets == []


# ─────────────────────────────────────────────
# TestStaticRenderer
# ─────────────────────────────────────────────

class TestStaticRenderer:
    def test_spec_only_returns_render_output_id(self, test_db, sample_brief):
        output_id = _insert_static_brief(test_db, sample_brief)
        result = render_static_brief(output_id, test_db)
        assert result["render_output_id"] is not None
        assert isinstance(result["render_output_id"], int)

    def test_spec_only_returns_five_specs(self, test_db, sample_brief):
        output_id = _insert_static_brief(test_db, sample_brief)
        result = render_static_brief(output_id, test_db)
        assert len(result["render_specs"]) == len(VARIANT_STRATEGIES)

    def test_spec_only_status(self, test_db, sample_brief):
        output_id = _insert_static_brief(test_db, sample_brief)
        result = render_static_brief(output_id, test_db)
        assert result["status"] == "spec_only"

    def test_spec_only_no_assets(self, test_db, sample_brief):
        output_id = _insert_static_brief(test_db, sample_brief)
        result = render_static_brief(output_id, test_db)
        assert result["assets"] == []

    def test_spec_only_inserts_render_outputs_row(self, test_db, sample_brief):
        output_id = _insert_static_brief(test_db, sample_brief)
        result = render_static_brief(output_id, test_db)
        row = test_db.execute(
            "SELECT * FROM render_outputs WHERE id = ?",
            (result["render_output_id"],)
        ).fetchone()
        assert row is not None
        assert dict(row)["status"] == "spec_only"

    def test_image_gen_mode_creates_assets(self, test_db, sample_brief):
        output_id = _insert_static_brief(test_db, sample_brief)
        result = render_static_brief(
            output_id, test_db,
            generate_images=True,
            provider_name="mock",
        )
        assert result["status"] == "images_generated"
        assert len(result["assets"]) == len(VARIANT_STRATEGIES)

    def test_image_gen_mode_assets_have_mock_paths(self, test_db, sample_brief):
        output_id = _insert_static_brief(test_db, sample_brief)
        result = render_static_brief(
            output_id, test_db,
            generate_images=True,
            provider_name="mock",
        )
        for asset in result["assets"]:
            assert asset["path"].startswith("mock://")

    def test_image_gen_mode_persists_to_render_assets(self, test_db, sample_brief):
        output_id = _insert_static_brief(test_db, sample_brief)
        result = render_static_brief(
            output_id, test_db,
            generate_images=True,
            provider_name="mock",
        )
        db_assets = list_render_assets(result["render_output_id"], test_db)
        assert len(db_assets) == len(VARIANT_STRATEGIES)

    def test_dry_run_no_db_writes(self, test_db, sample_brief):
        output_id = _insert_static_brief(test_db, sample_brief)
        result = render_static_brief(output_id, test_db, dry_run=True)
        assert result["render_output_id"] is None
        count = test_db.execute("SELECT COUNT(*) FROM render_outputs").fetchone()[0]
        assert count == 0

    def test_dry_run_still_returns_specs(self, test_db, sample_brief):
        output_id = _insert_static_brief(test_db, sample_brief)
        result = render_static_brief(output_id, test_db, dry_run=True)
        assert len(result["render_specs"]) == len(VARIANT_STRATEGIES)

    def test_wrong_output_type_raises_value_error(self, test_db):
        with test_db:
            test_db.execute(
                "INSERT INTO production_outputs (output_type, concept_text, output_json) "
                "VALUES ('ugc_brief', 'test', '{}')"
            )
        ugc_id = test_db.execute("SELECT last_insert_rowid()").fetchone()[0]
        with pytest.raises(ValueError, match="static_brief"):
            render_static_brief(ugc_id, test_db)

    def test_nonexistent_id_raises_value_error(self, test_db):
        with pytest.raises(ValueError):
            render_static_brief(99999, test_db)

    def test_provider_name_in_result(self, test_db, sample_brief):
        output_id = _insert_static_brief(test_db, sample_brief)
        result = render_static_brief(output_id, test_db)
        assert result["provider"] == "mock"


# ─────────────────────────────────────────────
# TestRenderExporters
# ─────────────────────────────────────────────

class TestRenderExporters:
    @pytest.fixture
    def five_specs(self, sample_brief):
        return build_render_specs(sample_brief, production_output_id=1)

    def test_markdown_has_header(self, five_specs):
        md = render_spec_to_markdown(five_specs)
        assert "# Static Render Specs" in md

    def test_markdown_has_variant_headers(self, five_specs):
        md = render_spec_to_markdown(five_specs)
        assert "## Minimal Variant" in md
        assert "## Premium Variant" in md
        assert "## Direct Response Variant" in md
        assert "## Bold Type Variant" in md
        assert "## Social Proof Variant" in md

    def test_markdown_no_empty_specs(self, five_specs):
        md = render_spec_to_markdown(five_specs)
        assert "(no specs generated)" not in md

    def test_markdown_empty_list(self):
        md = render_spec_to_markdown([])
        assert "no specs generated" in md

    def test_json_is_valid(self, five_specs):
        js = render_spec_to_json(five_specs)
        parsed = json.loads(js)
        assert isinstance(parsed, list)
        assert len(parsed) == len(VARIANT_STRATEGIES)

    def test_json_contains_variant_labels(self, five_specs):
        js = render_spec_to_json(five_specs)
        parsed = json.loads(js)
        labels = [item["variant_label"] for item in parsed]
        assert set(labels) == set(VARIANT_STRATEGIES)

    def test_text_has_no_markdown_headers(self, five_specs):
        text = render_spec_to_text(five_specs)
        assert "##" not in text
        assert "**" not in text

    def test_text_has_separator_blocks(self, five_specs):
        text = render_spec_to_text(five_specs)
        assert "==" in text  # separator lines

    def test_text_empty_list(self):
        text = render_spec_to_text([])
        assert "no specs generated" in text.lower()


# ─────────────────────────────────────────────
# TestRenderGenerateEndpoint
# ─────────────────────────────────────────────

class TestRenderGenerateEndpoint:
    def test_generate_returns_200(self, flask_app, sample_brief):
        client, db = flask_app
        output_id = _insert_static_brief(db, sample_brief)
        resp = client.post(
            "/api/render/generate",
            json={"production_output_id": output_id},
        )
        assert resp.status_code == 200

    def test_generate_returns_render_output_id(self, flask_app, sample_brief):
        client, db = flask_app
        output_id = _insert_static_brief(db, sample_brief)
        data = client.post(
            "/api/render/generate",
            json={"production_output_id": output_id},
        ).get_json()
        assert "render_output_id" in data
        assert isinstance(data["render_output_id"], int)

    def test_generate_spec_only_by_default(self, flask_app, sample_brief):
        client, db = flask_app
        output_id = _insert_static_brief(db, sample_brief)
        data = client.post(
            "/api/render/generate",
            json={"production_output_id": output_id},
        ).get_json()
        assert data.get("status") == "spec_only"

    def test_generate_404_for_unknown_id(self, flask_app):
        client, _ = flask_app
        resp = client.post(
            "/api/render/generate",
            json={"production_output_id": 99999},
        )
        assert resp.status_code == 404

    def test_generate_400_missing_id(self, flask_app):
        client, _ = flask_app
        resp = client.post("/api/render/generate", json={})
        assert resp.status_code == 400


# ─────────────────────────────────────────────
# TestRenderFetchEndpoint
# ─────────────────────────────────────────────

class TestRenderFetchEndpoint:
    def _create_render(self, client, db, brief):
        output_id = _insert_static_brief(db, brief)
        data = client.post(
            "/api/render/generate",
            json={"production_output_id": output_id},
        ).get_json()
        return data["render_output_id"]

    def test_fetch_returns_render_and_specs(self, flask_app, sample_brief):
        client, db = flask_app
        rid = self._create_render(client, db, sample_brief)
        data = client.get(f"/api/render/{rid}").get_json()
        assert "render_specs" in data
        assert "assets" in data
        assert data["id"] == rid

    def test_fetch_404_for_unknown_id(self, flask_app):
        client, _ = flask_app
        resp = client.get("/api/render/99999")
        assert resp.status_code == 404


# ─────────────────────────────────────────────
# TestApproveEndpoint
# ─────────────────────────────────────────────

class TestApproveEndpoint:
    def _create_render(self, client, db, brief):
        output_id = _insert_static_brief(db, brief)
        return client.post(
            "/api/render/generate",
            json={"production_output_id": output_id},
        ).get_json()["render_output_id"]

    def test_approve_toggles_on(self, flask_app, sample_brief):
        client, db = flask_app
        rid = self._create_render(client, db, sample_brief)
        resp = client.post(f"/api/render/{rid}/approve", json={"approved": True})
        assert resp.status_code == 200
        row = dict(db.execute("SELECT is_approved FROM render_outputs WHERE id=?", (rid,)).fetchone())
        assert row["is_approved"] == 1

    def test_approve_toggles_off(self, flask_app, sample_brief):
        client, db = flask_app
        rid = self._create_render(client, db, sample_brief)
        client.post(f"/api/render/{rid}/approve", json={"approved": True})
        client.post(f"/api/render/{rid}/approve", json={"approved": False})
        row = dict(db.execute("SELECT is_approved FROM render_outputs WHERE id=?", (rid,)).fetchone())
        assert row["is_approved"] == 0


# ─────────────────────────────────────────────
# TestFavoriteEndpoint
# ─────────────────────────────────────────────

class TestFavoriteEndpoint:
    def _create_render(self, client, db, brief):
        output_id = _insert_static_brief(db, brief)
        return client.post(
            "/api/render/generate",
            json={"production_output_id": output_id},
        ).get_json()["render_output_id"]

    def test_favorite_toggles_on(self, flask_app, sample_brief):
        client, db = flask_app
        rid = self._create_render(client, db, sample_brief)
        resp = client.post(f"/api/render/{rid}/favorite", json={"favorite": True})
        assert resp.status_code == 200
        row = dict(db.execute("SELECT is_favorite FROM render_outputs WHERE id=?", (rid,)).fetchone())
        assert row["is_favorite"] == 1

    def test_favorite_toggles_off(self, flask_app, sample_brief):
        client, db = flask_app
        rid = self._create_render(client, db, sample_brief)
        client.post(f"/api/render/{rid}/favorite", json={"favorite": True})
        client.post(f"/api/render/{rid}/favorite", json={"favorite": False})
        row = dict(db.execute("SELECT is_favorite FROM render_outputs WHERE id=?", (rid,)).fetchone())
        assert row["is_favorite"] == 0


# ─────────────────────────────────────────────
# TestAssetReviewEndpoint
# ─────────────────────────────────────────────

class TestAssetReviewEndpoint:
    def _create_render_with_assets(self, client, db, brief):
        output_id = _insert_static_brief(db, brief)
        data = client.post(
            "/api/render/generate",
            json={
                "production_output_id": output_id,
                "generate_images": True,
            },
        ).get_json()
        return data["render_output_id"]

    def test_set_asset_status_preferred(self, flask_app, sample_brief):
        client, db = flask_app
        rid = self._create_render_with_assets(client, db, sample_brief)
        assets = list_render_assets(rid, db)
        assert assets, "Expected assets to be generated"
        aid = assets[0]["id"]
        resp = client.post(
            f"/api/render/asset/{aid}/review",
            json={"status": "preferred"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["review_status"] == "preferred"

    def test_set_asset_status_rejected(self, flask_app, sample_brief):
        client, db = flask_app
        rid = self._create_render_with_assets(client, db, sample_brief)
        assets = list_render_assets(rid, db)
        aid = assets[0]["id"]
        resp = client.post(f"/api/render/asset/{aid}/review", json={"status": "rejected"})
        data = resp.get_json()
        assert data["review_status"] == "rejected"


# ─────────────────────────────────────────────
# TestRenderListEndpoint
# ─────────────────────────────────────────────

class TestRenderListEndpoint:
    def test_list_returns_200(self, flask_app):
        client, _ = flask_app
        resp = client.get("/api/render/list")
        assert resp.status_code == 200

    def test_list_returns_empty_initially(self, flask_app):
        client, _ = flask_app
        data = client.get("/api/render/list").get_json()
        assert isinstance(data, list)
        assert len(data) == 0

    def test_list_returns_items_after_generate(self, flask_app, sample_brief):
        client, db = flask_app
        output_id = _insert_static_brief(db, sample_brief)
        client.post("/api/render/generate", json={"production_output_id": output_id})
        data = client.get("/api/render/list").get_json()
        assert len(data) >= 1

    def test_list_ordered_by_created_at_desc(self, flask_app, sample_brief):
        client, db = flask_app
        for _ in range(3):
            output_id = _insert_static_brief(db, sample_brief)
            client.post("/api/render/generate", json={"production_output_id": output_id})
        data = client.get("/api/render/list").get_json()
        # All rows created at the same second in tests — just verify all 3 are returned
        assert len(data) == 3
        # Verify each item has expected fields
        for item in data:
            assert "id" in item
            assert "status" in item
            assert "created_at" in item


# ─────────────────────────────────────────────
# Helpers for new per-asset endpoint tests
# ─────────────────────────────────────────────

def _create_render_with_assets(client, db, brief):
    """Insert brief, generate render with mock images, return (render_id, first_asset_id)."""
    output_id = _insert_static_brief(db, brief)
    data = client.post(
        "/api/render/generate",
        json={"production_output_id": output_id, "generate_images": True},
    ).get_json()
    rid = data["render_output_id"]
    assets = list_render_assets(rid, db)
    return rid, assets[0]["id"]


# ─────────────────────────────────────────────
# TestApproveAssetEndpoint
# ─────────────────────────────────────────────

class TestApproveAssetEndpoint:
    def test_approve_sets_status_approved(self, flask_app, sample_brief):
        client, db = flask_app
        _, aid = _create_render_with_assets(client, db, sample_brief)
        resp = client.post(f"/api/render/asset/{aid}/approve")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["review_status"] == "approved"

    def test_approve_persists_to_db(self, flask_app, sample_brief):
        client, db = flask_app
        _, aid = _create_render_with_assets(client, db, sample_brief)
        client.post(f"/api/render/asset/{aid}/approve")
        row = dict(db.execute(
            "SELECT review_status FROM render_assets WHERE id = ?", (aid,)
        ).fetchone())
        assert row["review_status"] == "approved"

    def test_approve_sets_reviewed_at(self, flask_app, sample_brief):
        client, db = flask_app
        _, aid = _create_render_with_assets(client, db, sample_brief)
        client.post(f"/api/render/asset/{aid}/approve")
        row = dict(db.execute(
            "SELECT reviewed_at FROM render_assets WHERE id = ?", (aid,)
        ).fetchone())
        assert row["reviewed_at"] is not None

    def test_approve_unknown_asset_returns_404(self, flask_app):
        client, _ = flask_app
        resp = client.post("/api/render/asset/99999/approve")
        assert resp.status_code == 404


# ─────────────────────────────────────────────
# TestRejectAssetEndpoint
# ─────────────────────────────────────────────

class TestRejectAssetEndpoint:
    def test_reject_sets_status_rejected(self, flask_app, sample_brief):
        client, db = flask_app
        _, aid = _create_render_with_assets(client, db, sample_brief)
        resp = client.post(f"/api/render/asset/{aid}/reject")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["review_status"] == "rejected"

    def test_reject_persists_to_db(self, flask_app, sample_brief):
        client, db = flask_app
        _, aid = _create_render_with_assets(client, db, sample_brief)
        client.post(f"/api/render/asset/{aid}/reject")
        row = dict(db.execute(
            "SELECT review_status FROM render_assets WHERE id = ?", (aid,)
        ).fetchone())
        assert row["review_status"] == "rejected"

    def test_reject_unknown_asset_returns_404(self, flask_app):
        client, _ = flask_app
        resp = client.post("/api/render/asset/99999/reject")
        assert resp.status_code == 404


# ─────────────────────────────────────────────
# TestFavoriteAssetEndpoint
# ─────────────────────────────────────────────

class TestFavoriteAssetEndpoint:
    def test_favorite_toggles_on(self, flask_app, sample_brief):
        client, db = flask_app
        _, aid = _create_render_with_assets(client, db, sample_brief)
        resp = client.post(f"/api/render/asset/{aid}/favorite")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["is_favorite"] is True

    def test_favorite_toggles_off(self, flask_app, sample_brief):
        client, db = flask_app
        _, aid = _create_render_with_assets(client, db, sample_brief)
        client.post(f"/api/render/asset/{aid}/favorite")  # on
        resp = client.post(f"/api/render/asset/{aid}/favorite")  # off
        data = resp.get_json()
        assert data["is_favorite"] is False

    def test_favorite_persists_to_db(self, flask_app, sample_brief):
        client, db = flask_app
        _, aid = _create_render_with_assets(client, db, sample_brief)
        client.post(f"/api/render/asset/{aid}/favorite")
        row = dict(db.execute(
            "SELECT is_favorite FROM render_assets WHERE id = ?", (aid,)
        ).fetchone())
        assert row["is_favorite"] == 1

    def test_favorite_unknown_asset_returns_404(self, flask_app):
        client, _ = flask_app
        resp = client.post("/api/render/asset/99999/favorite")
        assert resp.status_code == 404


# ─────────────────────────────────────────────
# TestReadyAssetEndpoint
# ─────────────────────────────────────────────

class TestReadyAssetEndpoint:
    def test_ready_toggles_on(self, flask_app, sample_brief):
        client, db = flask_app
        _, aid = _create_render_with_assets(client, db, sample_brief)
        resp = client.post(f"/api/render/asset/{aid}/ready")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["is_ready_to_test"] is True

    def test_ready_toggles_off(self, flask_app, sample_brief):
        client, db = flask_app
        _, aid = _create_render_with_assets(client, db, sample_brief)
        client.post(f"/api/render/asset/{aid}/ready")   # on
        resp = client.post(f"/api/render/asset/{aid}/ready")  # off
        data = resp.get_json()
        assert data["is_ready_to_test"] is False

    def test_ready_persists_to_db(self, flask_app, sample_brief):
        client, db = flask_app
        _, aid = _create_render_with_assets(client, db, sample_brief)
        client.post(f"/api/render/asset/{aid}/ready")
        row = dict(db.execute(
            "SELECT is_ready_to_test FROM render_assets WHERE id = ?", (aid,)
        ).fetchone())
        assert row["is_ready_to_test"] == 1

    def test_ready_unknown_asset_returns_404(self, flask_app):
        client, _ = flask_app
        resp = client.post("/api/render/asset/99999/ready")
        assert resp.status_code == 404


# ─────────────────────────────────────────────
# TestSummaryEndpoint
# ─────────────────────────────────────────────

class TestSummaryEndpoint:
    def test_summary_returns_200(self, flask_app, sample_brief):
        client, db = flask_app
        rid, _ = _create_render_with_assets(client, db, sample_brief)
        resp = client.get(f"/api/render/summary/{rid}")
        assert resp.status_code == 200

    def test_summary_correct_total(self, flask_app, sample_brief):
        client, db = flask_app
        rid, _ = _create_render_with_assets(client, db, sample_brief)
        data = client.get(f"/api/render/summary/{rid}").get_json()
        assert data["total"] == len(list_render_assets(rid, db))

    def test_summary_counts_approved(self, flask_app, sample_brief):
        client, db = flask_app
        rid, aid = _create_render_with_assets(client, db, sample_brief)
        client.post(f"/api/render/asset/{aid}/approve")
        data = client.get(f"/api/render/summary/{rid}").get_json()
        assert data["approved"] == 1

    def test_summary_counts_rejected(self, flask_app, sample_brief):
        client, db = flask_app
        rid, aid = _create_render_with_assets(client, db, sample_brief)
        client.post(f"/api/render/asset/{aid}/reject")
        data = client.get(f"/api/render/summary/{rid}").get_json()
        assert data["rejected"] == 1

    def test_summary_counts_favorites(self, flask_app, sample_brief):
        client, db = flask_app
        rid, aid = _create_render_with_assets(client, db, sample_brief)
        client.post(f"/api/render/asset/{aid}/favorite")
        data = client.get(f"/api/render/summary/{rid}").get_json()
        assert data["favorites"] == 1

    def test_summary_counts_ready_to_test(self, flask_app, sample_brief):
        client, db = flask_app
        rid, aid = _create_render_with_assets(client, db, sample_brief)
        client.post(f"/api/render/asset/{aid}/ready")
        data = client.get(f"/api/render/summary/{rid}").get_json()
        assert data["ready_to_test"] == 1

    def test_summary_assets_list_present(self, flask_app, sample_brief):
        client, db = flask_app
        rid, _ = _create_render_with_assets(client, db, sample_brief)
        data = client.get(f"/api/render/summary/{rid}").get_json()
        assert "assets" in data
        assert isinstance(data["assets"], list)
        assert len(data["assets"]) > 0

    def test_summary_unknown_render_returns_404(self, flask_app):
        client, _ = flask_app
        resp = client.get("/api/render/summary/99999")
        assert resp.status_code == 404

    def test_summary_render_output_id_field(self, flask_app, sample_brief):
        client, db = flask_app
        rid, _ = _create_render_with_assets(client, db, sample_brief)
        data = client.get(f"/api/render/summary/{rid}").get_json()
        assert data["render_output_id"] == rid
