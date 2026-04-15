"""Tests for generation layer — uses MockLLMClient, no live API calls."""
import json
import pytest
import tempfile
from pathlib import Path

from creative_intelligence.db import init_db, get_connection
from creative_intelligence.generation.llm_client import MockLLMClient, get_llm_client


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    conn = get_connection(db_path)
    # Seed a pattern.
    conn.execute(
        """INSERT INTO creative_patterns
           (pattern_name, hook_type, angle, archetype, emotional_trigger,
            avg_ctr, avg_roas, creative_count, winner_count, example_creative_ids)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        ("curiosity × problem_solution", "curiosity", "problem_solution", "ugc",
         "curiosity", 0.025, 2.5, 10, 3, '["ad-1","ad-2"]'),
    )
    conn.commit()
    yield conn
    conn.close()


def test_mock_client_returns_json():
    client = MockLLMClient()
    result = client.complete_json("system", "user")
    assert isinstance(result, dict)
    assert "hooks" in result


def test_get_llm_client_dry_run():
    client = get_llm_client(dry_run=True)
    assert isinstance(client, MockLLMClient)


def test_generate_hooks_dry_run(db):
    from creative_intelligence.generation.hook_generator import generate_hooks_from_pattern

    pattern = db.execute("SELECT id FROM creative_patterns LIMIT 1").fetchone()
    assert pattern, "No pattern in test DB"

    result = generate_hooks_from_pattern(
        pattern_id=pattern["id"],
        count=3,
        dry_run=True,
        conn=db,
    )
    assert result["dry_run"] is True
    assert isinstance(result["hooks"], list)
    assert len(result["hooks"]) > 0
    # Dry run — nothing persisted.
    assert result["hook_ids"] == []


def test_generate_hooks_persisted(db, monkeypatch):
    from creative_intelligence.generation.hook_generator import generate_hooks_from_pattern
    from creative_intelligence.generation.llm_client import MockLLMClient
    import creative_intelligence.generation.hook_generator as hg
    # Always use mock client so tests don't hit live APIs even when keys are present.
    monkeypatch.setattr(hg, "get_llm_client", lambda **kwargs: MockLLMClient())

    pattern = db.execute("SELECT id FROM creative_patterns LIMIT 1").fetchone()
    result = generate_hooks_from_pattern(
        pattern_id=pattern["id"],
        count=3,
        dry_run=False,
        conn=db,
    )
    assert result["dry_run"] is False
    assert len(result["hook_ids"]) > 0
    # Verify DB row exists.
    row = db.execute(
        "SELECT * FROM generated_hooks WHERE id = ?", (result["hook_ids"][0],)
    ).fetchone()
    assert row is not None
    assert row["hook_text"]


def test_generate_ugc_brief_dry_run(db):
    from creative_intelligence.generation.brief_generator import generate_ugc_brief
    result = generate_ugc_brief(dry_run=True, conn=db)
    assert result["dry_run"] is True
    assert isinstance(result["brief"], dict)


def test_generate_test_matrix_dry_run(db):
    from creative_intelligence.generation.brief_generator import generate_test_matrix
    result = generate_test_matrix(dry_run=True, conn=db)
    assert result["dry_run"] is True
    assert isinstance(result.get("matrix"), dict)
