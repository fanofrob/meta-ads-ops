"""Tests for the SQLite DB layer."""
import tempfile
from pathlib import Path
import pytest

from creative_intelligence.db import get_connection, init_db, db_exists


@pytest.fixture
def tmp_db(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    return db_path


def test_init_creates_file(tmp_path):
    db_path = tmp_path / "new.db"
    assert not db_path.exists()
    init_db(db_path)
    assert db_path.exists()


def test_tables_created(tmp_db):
    conn = get_connection(tmp_db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    expected = {
        "creatives", "creative_performance", "creative_tags",
        "creative_insights", "creative_patterns",
        "products", "product_benefits", "product_angles", "product_offers", "product_kb_documents",
        "generation_runs", "generated_hooks", "generated_scripts", "creative_scores",
        "creative_visual_attributes", "creative_visual_analysis_runs",
    }
    assert expected.issubset(tables), f"Missing tables: {expected - tables}"


def test_idempotent_init(tmp_db):
    """Running init_db twice should not raise."""
    init_db(tmp_db)
    init_db(tmp_db)


def test_foreign_keys_enforced(tmp_db):
    conn = get_connection(tmp_db)
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO creative_tags (creative_id, tag_type, tag_value) VALUES (?,?,?)",
            ("nonexistent_id", "hook_type", "curiosity"),
        )
        conn.commit()
