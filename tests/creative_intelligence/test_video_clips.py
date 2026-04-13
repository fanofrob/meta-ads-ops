"""
Tests for scene-level clip generation (v1.7).

Fully offline — uses MockSceneClipProvider and in-memory SQLite only.
No real API calls are made.
"""
import json
import sqlite3
from pathlib import Path

import pytest

from creative_intelligence.db import init_db, get_connection
from creative_intelligence.video.clip_providers import (
    MockSceneClipProvider,
    ReplicateSceneClipProvider,
    RunwaySceneClipProvider,
    get_clip_provider,
)
from creative_intelligence.video.clip_generator import (
    build_clip_prompt,
    generate_scene_clip,
    generate_storyboard_clips,
    get_scene_clips,
    _upsert_scene_clip,
)


# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    """In-memory SQLite with full schema."""
    db_path = tmp_path / "test_clips.db"
    init_db(db_path)
    conn = get_connection(db_path)
    yield conn
    conn.close()


@pytest.fixture
def storyboard_with_scenes(db):
    """Insert a minimal storyboard + 3 scenes. Returns storyboard_id."""
    with db:
        cur = db.execute(
            """INSERT INTO video_storyboards (concept_text, video_type, total_duration_seconds)
               VALUES (?, ?, ?)""",
            ("Test concept about cherries", "ugc", 30),
        )
        sb_id = cur.lastrowid

        for i, purpose in enumerate(["hook", "demo", "cta"]):
            db.execute(
                """INSERT INTO video_scenes
                   (storyboard_id, scene_index, purpose, visual_description,
                    text_overlay, duration_seconds, camera_type, framing,
                    movement, product_focus, lighting_style)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    sb_id, i, purpose,
                    f"Scene {i}: {purpose} visual description of the cherry product",
                    f"Overlay text {i}",
                    5.0, "handheld", "medium", "zoom in", "cherry jar", "golden hour",
                ),
            )
    return sb_id


# ─────────────────────────────────────────────
# TestClipProviders
# ─────────────────────────────────────────────

class TestClipProviders:
    def test_mock_returns_mock_path(self):
        p = MockSceneClipProvider()
        result = p.generate("A beautiful cherry farm", duration=5.0, scene_id=1)
        assert result is not None
        assert result.startswith("mock://")
        assert "clip_1" in result

    def test_mock_path_contains_aspect_ratio(self):
        p = MockSceneClipProvider()
        result = p.generate("prompt", aspect_ratio="1:1", scene_id=2)
        assert "1x1" in result

    def test_mock_is_deterministic(self):
        p = MockSceneClipProvider()
        r1 = p.generate("same prompt", scene_id=5)
        r2 = p.generate("same prompt", scene_id=5)
        assert r1 == r2

    def test_mock_different_prompts_differ(self):
        p = MockSceneClipProvider()
        r1 = p.generate("prompt A", scene_id=1)
        r2 = p.generate("prompt B", scene_id=1)
        assert r1 != r2

    def test_mock_name(self):
        assert MockSceneClipProvider().name == "mock"

    def test_replicate_name(self):
        assert ReplicateSceneClipProvider.__new__(ReplicateSceneClipProvider).name == "replicate"

    def test_runway_name(self):
        assert RunwaySceneClipProvider.__new__(RunwaySceneClipProvider).name == "runway"

    def test_runway_raises_not_implemented(self):
        p = RunwaySceneClipProvider()
        with pytest.raises(NotImplementedError):
            p.generate("prompt")

    def test_get_clip_provider_mock(self):
        p = get_clip_provider("mock")
        assert isinstance(p, MockSceneClipProvider)

    def test_get_clip_provider_replicate(self):
        p = get_clip_provider("replicate")
        assert isinstance(p, ReplicateSceneClipProvider)

    def test_get_clip_provider_runway(self):
        p = get_clip_provider("runway")
        assert isinstance(p, RunwaySceneClipProvider)

    def test_get_clip_provider_default_is_mock(self, monkeypatch):
        # CI_CLIP_PROVIDER defaults to "mock"
        monkeypatch.setenv("CI_CLIP_PROVIDER", "mock")
        import importlib
        import creative_intelligence.config as cfg
        importlib.reload(cfg)
        p = get_clip_provider()
        assert isinstance(p, MockSceneClipProvider)


# ─────────────────────────────────────────────
# TestBuildClipPrompt
# ─────────────────────────────────────────────

class TestBuildClipPrompt:
    def _scene(self, **kwargs):
        base = {
            "purpose": "demo",
            "visual_description": "Close-up of cherry jam",
            "product_focus": "cherry jar",
            "lighting_style": "golden hour",
        }
        base.update(kwargs)
        return base

    def test_prompt_is_nonempty(self):
        prompt = build_clip_prompt(self._scene(), "ugc")
        assert isinstance(prompt, str)
        assert len(prompt) > 20

    def test_prompt_contains_visual_description(self):
        prompt = build_clip_prompt(self._scene(visual_description="red cherry macro"), "ugc")
        assert "red cherry macro" in prompt

    def test_prompt_contains_type_prefix(self):
        prompt = build_clip_prompt(self._scene(), "farm_origin")
        assert "Cinematic" in prompt or "golden hour" in prompt

    def test_prompt_contains_purpose_hint(self):
        prompt = build_clip_prompt(self._scene(purpose="hook"), "ugc")
        assert any(w in prompt.lower() for w in ["grab", "attention", "eye", "bold"])

    def test_prompt_cta_hint(self):
        prompt = build_clip_prompt(self._scene(purpose="cta"), "ugc")
        assert any(w in prompt.lower() for w in ["forward", "energetic", "push", "bold"])

    def test_prompt_no_bracket_tokens(self):
        prompt = build_clip_prompt(self._scene(), "ugc")
        assert "[" not in prompt and "]" not in prompt

    def test_prompt_ends_with_quality_modifiers(self):
        prompt = build_clip_prompt(self._scene(), "ugc")
        assert "Photorealistic" in prompt or "high quality" in prompt

    def test_unknown_video_type_no_crash(self):
        prompt = build_clip_prompt(self._scene(), "unknown_type")
        assert len(prompt) > 10

    def test_missing_fields_no_crash(self):
        prompt = build_clip_prompt({}, "ugc")
        assert isinstance(prompt, str)


# ─────────────────────────────────────────────
# TestGenerateSceneClip
# ─────────────────────────────────────────────

class TestGenerateSceneClip:
    def _make_scene(self, scene_id=1, scene_index=0):
        return {
            "id": scene_id,
            "scene_index": scene_index,
            "purpose": "demo",
            "visual_description": "Cherry jar on wooden table",
            "product_focus": "cherry jar",
            "lighting_style": "natural",
            "duration_seconds": 5.0,
        }

    def test_mock_provider_returns_ok(self, db, monkeypatch):
        monkeypatch.setenv("CI_CLIP_PROVIDER", "mock")
        scene = self._make_scene(scene_id=1)
        result = generate_scene_clip(
            scene=scene, storyboard_id=1, conn=db,
            provider_name="mock", aspect_ratio="9:16",
        )
        assert result["status"] == "ok"
        assert result["clip_path"].startswith("mock://")
        assert result["scene_id"] == 1

    def test_dry_run_no_db_write(self, db):
        scene = self._make_scene(scene_id=99)
        result = generate_scene_clip(
            scene=scene, storyboard_id=1, conn=db,
            provider_name="mock", dry_run=True,
        )
        assert result["status"] == "dry_run"
        assert result["clip_path"] is None
        rows = db.execute("SELECT * FROM video_scene_clips WHERE scene_id=99").fetchall()
        assert len(rows) == 0

    def test_persists_to_db(self, db):
        scene = self._make_scene(scene_id=42, scene_index=2)
        generate_scene_clip(
            scene=scene, storyboard_id=10, conn=db,
            provider_name="mock",
        )
        row = db.execute(
            "SELECT * FROM video_scene_clips WHERE scene_id=42"
        ).fetchone()
        assert row is not None
        assert row["status"] == "ok"
        assert row["storyboard_id"] == 10
        assert row["scene_index"] == 2

    def test_prompt_in_result(self, db):
        scene = self._make_scene(scene_id=5)
        result = generate_scene_clip(
            scene=scene, storyboard_id=1, conn=db, provider_name="mock",
        )
        assert result["prompt"] and len(result["prompt"]) > 10

    def test_upsert_replaces_existing(self, db):
        scene = self._make_scene(scene_id=7)
        generate_scene_clip(scene=scene, storyboard_id=1, conn=db, provider_name="mock")
        generate_scene_clip(scene=scene, storyboard_id=1, conn=db, provider_name="mock")
        rows = db.execute(
            "SELECT COUNT(*) as cnt FROM video_scene_clips WHERE scene_id=7"
        ).fetchone()
        assert rows["cnt"] == 1


# ─────────────────────────────────────────────
# TestGenerateStoryboardClips
# ─────────────────────────────────────────────

class TestGenerateStoryboardClips:
    def test_generates_all_scenes(self, db, storyboard_with_scenes):
        result = generate_storyboard_clips(
            storyboard_id=storyboard_with_scenes,
            conn=db,
            provider_name="mock",
        )
        assert result["total_scenes"] == 3
        assert result["generated"] == 3
        assert result["failed"] == 0
        assert result["status"] == "ok"

    def test_skip_existing(self, db, storyboard_with_scenes):
        sb_id = storyboard_with_scenes
        # First pass
        generate_storyboard_clips(sb_id, db, provider_name="mock")
        # Second pass with skip_existing=True
        result = generate_storyboard_clips(
            sb_id, db, provider_name="mock", skip_existing=True
        )
        assert result["skipped"] == 3
        assert result["generated"] == 0

    def test_no_skip_existing(self, db, storyboard_with_scenes):
        sb_id = storyboard_with_scenes
        generate_storyboard_clips(sb_id, db, provider_name="mock")
        result = generate_storyboard_clips(
            sb_id, db, provider_name="mock", skip_existing=False
        )
        assert result["generated"] == 3

    def test_no_scenes_returns_no_scenes(self, db):
        with db:
            cur = db.execute(
                "INSERT INTO video_storyboards (concept_text, video_type) VALUES (?,?)",
                ("empty sb", "ugc"),
            )
            empty_sb_id = cur.lastrowid
        result = generate_storyboard_clips(empty_sb_id, db, provider_name="mock")
        assert result["status"] == "no_scenes"
        assert result["total_scenes"] == 0

    def test_results_sorted_by_scene_index(self, db, storyboard_with_scenes):
        result = generate_storyboard_clips(
            storyboard_with_scenes, db, provider_name="mock"
        )
        indices = [r["scene_index"] for r in result["results"]]
        assert indices == sorted(indices)

    def test_dry_run_no_db_writes(self, db, storyboard_with_scenes):
        sb_id = storyboard_with_scenes
        result = generate_storyboard_clips(sb_id, db, provider_name="mock", dry_run=True)
        assert result["generated"] == 3
        rows = db.execute(
            "SELECT COUNT(*) as cnt FROM video_scene_clips WHERE storyboard_id=?",
            (sb_id,),
        ).fetchone()
        assert rows["cnt"] == 0


# ─────────────────────────────────────────────
# TestGetSceneClips
# ─────────────────────────────────────────────

class TestGetSceneClips:
    def test_returns_empty_list_for_no_clips(self, db, storyboard_with_scenes):
        clips = get_scene_clips(storyboard_with_scenes, db)
        assert clips == []

    def test_returns_clips_after_generation(self, db, storyboard_with_scenes):
        sb_id = storyboard_with_scenes
        generate_storyboard_clips(sb_id, db, provider_name="mock")
        clips = get_scene_clips(sb_id, db)
        assert len(clips) == 3
        assert all(c["storyboard_id"] == sb_id for c in clips)

    def test_clips_ordered_by_scene_index(self, db, storyboard_with_scenes):
        sb_id = storyboard_with_scenes
        generate_storyboard_clips(sb_id, db, provider_name="mock")
        clips = get_scene_clips(sb_id, db)
        indices = [c["scene_index"] for c in clips]
        assert indices == sorted(indices)


# ─────────────────────────────────────────────
# TestVideoSceneClipsSchema
# ─────────────────────────────────────────────

class TestVideoSceneClipsSchema:
    def test_table_exists(self, db):
        rows = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='video_scene_clips'"
        ).fetchall()
        assert len(rows) == 1

    def test_required_columns_exist(self, db):
        cols = {row["name"] for row in db.execute("PRAGMA table_info(video_scene_clips)")}
        required = {
            "id", "storyboard_id", "scene_id", "scene_index",
            "provider", "model", "prompt", "clip_local_path", "clip_url",
            "duration_seconds", "aspect_ratio", "status", "error_message",
            "metadata_json", "created_at",
        }
        assert required.issubset(cols)

    def test_default_status_is_pending(self, db):
        with db:
            db.execute(
                """INSERT INTO video_scene_clips
                   (storyboard_id, scene_id, scene_index, provider)
                   VALUES (1, 1, 0, 'mock')"""
            )
        row = db.execute("SELECT status FROM video_scene_clips WHERE scene_id=1").fetchone()
        assert row["status"] == "pending"

    def test_index_on_storyboard_id(self, db):
        indexes = {
            row["name"]
            for row in db.execute("PRAGMA index_list(video_scene_clips)")
        }
        assert any("storyboard" in idx for idx in indexes)


# ─────────────────────────────────────────────
# TestClipRoutes (Flask app integration)
# ─────────────────────────────────────────────

@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("CI_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("CI_CLIP_PROVIDER", "mock")
    from creative_intelligence.webapp.app import app
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


@pytest.fixture
def seeded_storyboard(app_client, tmp_path, monkeypatch):
    """Create a storyboard with 2 scenes via the DB directly. Returns storyboard_id."""
    monkeypatch.setenv("CI_DB_PATH", str(tmp_path / "test.db"))
    from creative_intelligence.db import get_connection, init_db
    db_path = tmp_path / "test.db"
    init_db(db_path)
    conn = get_connection(db_path)
    with conn:
        cur = conn.execute(
            "INSERT INTO video_storyboards (concept_text, video_type) VALUES (?,?)",
            ("Cherry product concept", "ugc"),
        )
        sb_id = cur.lastrowid
        for i in range(2):
            conn.execute(
                """INSERT INTO video_scenes
                   (storyboard_id, scene_index, purpose, visual_description, duration_seconds)
                   VALUES (?, ?, ?, ?, ?)""",
                (sb_id, i, "demo", f"Visual description {i}", 5.0),
            )
    conn.close()
    return sb_id


class TestClipRoutes:
    def test_generate_clips_200(self, app_client, seeded_storyboard):
        r = app_client.post(
            f"/api/video/storyboard/{seeded_storyboard}/generate-clips",
            json={"provider": "mock"},
            content_type="application/json",
        )
        assert r.status_code == 200
        data = r.get_json()
        assert data["total_scenes"] == 2
        assert data["generated"] == 2
        assert data["status"] == "ok"

    def test_generate_clips_404_unknown_storyboard(self, app_client):
        r = app_client.post(
            "/api/video/storyboard/99999/generate-clips",
            json={},
            content_type="application/json",
        )
        assert r.status_code == 404

    def test_get_clips_empty(self, app_client, seeded_storyboard):
        r = app_client.get(f"/api/video/storyboard/{seeded_storyboard}/clips")
        assert r.status_code == 200
        assert r.get_json() == []

    def test_get_clips_after_generate(self, app_client, seeded_storyboard):
        app_client.post(
            f"/api/video/storyboard/{seeded_storyboard}/generate-clips",
            json={"provider": "mock"},
            content_type="application/json",
        )
        r = app_client.get(f"/api/video/storyboard/{seeded_storyboard}/clips")
        assert r.status_code == 200
        clips = r.get_json()
        assert len(clips) == 2
        assert all(c["status"] == "ok" for c in clips)

    def test_get_clips_404_unknown_storyboard(self, app_client):
        r = app_client.get("/api/video/storyboard/99999/clips")
        assert r.status_code == 404

    def test_dry_run_no_db_writes(self, app_client, seeded_storyboard):
        r = app_client.post(
            f"/api/video/storyboard/{seeded_storyboard}/generate-clips",
            json={"provider": "mock", "dry_run": True},
            content_type="application/json",
        )
        assert r.status_code == 200
        clips_r = app_client.get(f"/api/video/storyboard/{seeded_storyboard}/clips")
        assert clips_r.get_json() == []

    def test_skip_existing_true(self, app_client, seeded_storyboard):
        # Generate once
        app_client.post(
            f"/api/video/storyboard/{seeded_storyboard}/generate-clips",
            json={"provider": "mock"},
        )
        # Generate again with skip_existing
        r = app_client.post(
            f"/api/video/storyboard/{seeded_storyboard}/generate-clips",
            json={"provider": "mock", "skip_existing": True},
        )
        data = r.get_json()
        assert data["skipped"] == 2
        assert data["generated"] == 0
