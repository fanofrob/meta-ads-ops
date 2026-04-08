"""Tests for the vision analysis layer — uses MockVisionAnalyzer, no API calls."""
import json
import pytest

from creative_intelligence.db import init_db, get_connection
from creative_intelligence.vision.image_analyzer import (
    MockVisionAnalyzer,
    OpenAIVisionAnalyzer,
    get_analyzer,
    save_visual_attributes,
    analyze_creatives_batch,
    ANALYSIS_VERSION,
)
from creative_intelligence.vision.vision_prompts import ALLOWED_VALUES


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "vision_test.db"
    init_db(db_path)
    conn = get_connection(db_path)
    # Seed a creative.
    conn.execute(
        """INSERT INTO creatives
           (id, ad_id, ad_name, campaign_id, campaign_name, status)
           VALUES (?,?,?,?,?,?)""",
        ("creative-1", "ad-1", "Test Ad", "camp-1", "Test Campaign", "ACTIVE"),
    )
    conn.commit()
    yield conn
    conn.close()


def test_mock_analyzer_returns_dict():
    analyzer = MockVisionAnalyzer()
    result = analyzer.analyze_url("https://example.com/image.jpg")
    assert isinstance(result, dict)
    assert "visual_format" in result
    assert "visual_summary" in result


def test_mock_analyzer_valid_values():
    analyzer = MockVisionAnalyzer()
    result = analyzer.analyze_url("https://example.com/image.jpg")
    for field, allowed in ALLOWED_VALUES.items():
        val = result.get(field)
        if val is not None:
            assert val in allowed, f"{field}={val!r} not in allowed {allowed}"


def test_mock_analyzer_booleans():
    analyzer = MockVisionAnalyzer()
    result = analyzer.analyze_url("https://example.com/image.jpg")
    assert isinstance(result.get("text_overlay_presence"), bool)
    assert isinstance(result.get("face_presence"), bool)


def test_get_analyzer_dry_run_returns_mock():
    analyzer = get_analyzer("mock")
    assert isinstance(analyzer, MockVisionAnalyzer)


def test_save_visual_attributes(db):
    analyzer = MockVisionAnalyzer()
    result = analyzer.analyze_url("https://example.com/img.jpg")
    save_visual_attributes(
        creative_id="creative-1",
        asset_url="https://example.com/img.jpg",
        asset_type="image",
        result=result,
        run_id=None,
        analyzer=analyzer,
        conn=db,
    )
    row = db.execute(
        "SELECT * FROM creative_visual_attributes WHERE creative_id = ?",
        ("creative-1",),
    ).fetchone()
    assert row is not None
    assert row["visual_format"] == "ugc"
    assert row["analysis_provider"] == "mock"


def test_idempotent_save(db):
    """Saving twice for same creative+version should not create duplicates."""
    analyzer = MockVisionAnalyzer()
    result = analyzer.analyze_url("https://example.com/img.jpg")
    for _ in range(2):
        save_visual_attributes(
            "creative-1", "https://example.com/img.jpg", "image",
            result, None, analyzer, db,
        )
    count = db.execute(
        "SELECT COUNT(*) FROM creative_visual_attributes WHERE creative_id = ?",
        ("creative-1",),
    ).fetchone()[0]
    assert count == 1


def test_analyze_batch_dry_run(db):
    records = [{"creative_id": "creative-1", "ad_name": "Test"}]
    url_map = {"creative-1": ("https://example.com/img.jpg", "image")}

    summary = analyze_creatives_batch(
        records, url_map, dry_run=True, conn=db
    )
    assert summary["dry_run"] is True
    assert summary["processed"] == 1

    # Dry run — nothing should be persisted.
    count = db.execute("SELECT COUNT(*) FROM creative_visual_attributes").fetchone()[0]
    assert count == 0


def test_analyze_batch_persists_when_not_dry_run(db):
    records = [{"creative_id": "creative-1", "ad_name": "Test"}]
    url_map = {"creative-1": ("https://example.com/img.jpg", "image")}

    analyze_creatives_batch(records, url_map, dry_run=False, provider="mock", conn=db)

    count = db.execute("SELECT COUNT(*) FROM creative_visual_attributes").fetchone()[0]
    assert count == 1


def test_analyze_batch_skips_missing_url(db):
    records = [{"creative_id": "creative-1", "ad_name": "Test"}]
    url_map = {"creative-1": (None, "none")}  # No URL

    summary = analyze_creatives_batch(records, url_map, dry_run=False, provider="mock", conn=db)
    assert summary["skipped"] == 1
    assert summary["processed"] == 0
