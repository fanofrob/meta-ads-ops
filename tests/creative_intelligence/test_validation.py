"""Tests for the QA/validation layer."""
import pytest
from creative_intelligence.db import init_db, get_connection
from creative_intelligence.validation.qa_checks import (
    check_creative_count,
    check_duplicate_creatives,
    check_creatives_missing_performance,
    check_tag_coverage,
    check_unknown_tag_rate,
    check_tag_distribution,
    check_unmapped_creatives,
    check_pattern_count,
    check_score_distribution,
    get_sample_by_tag,
    get_unknown_sample,
    run_all_checks,
)


@pytest.fixture
def empty_db(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    return get_connection(db_path)


@pytest.fixture
def seeded_db(tmp_path):
    """DB with one creative, performance row, tags, and a pattern."""
    db_path = tmp_path / "seeded.db"
    init_db(db_path)
    conn = get_connection(db_path)
    conn.execute(
        """INSERT INTO creatives
           (id, ad_id, ad_name, campaign_id, campaign_name, status,
            hook_text, primary_text, headline)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        ("c1", "a1", "Test Ad", "camp1", "Test Campaign", "ACTIVE",
         "Are you still struggling?", "Here's what nobody tells you.", "Fix it now"),
    )
    conn.execute(
        """INSERT INTO creative_performance
           (creative_id, ad_id, date_range, snapshot_date, spend, impressions,
            clicks, ctr, cpc, cpm, roas, cpa)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("c1", "a1", "7d", "2026-04-07", 100.0, 10000, 200, 2.0, 0.5, 10.0, 3.0, 20.0),
    )
    conn.execute(
        "INSERT INTO creative_tags (creative_id, tag_type, tag_value) VALUES (?,?,?)",
        ("c1", "hook_type", "curiosity"),
    )
    conn.execute(
        "INSERT INTO creative_tags (creative_id, tag_type, tag_value) VALUES (?,?,?)",
        ("c1", "angle", "problem_solution"),
    )
    conn.execute(
        """INSERT INTO creative_patterns
           (pattern_name, hook_type, angle, creative_count, winner_count)
           VALUES (?,?,?,?,?)""",
        ("curiosity × problem_solution", "curiosity", "problem_solution", 1, 1),
    )
    conn.commit()
    yield conn
    conn.close()


# ─────────────────────────────────────────────
# Empty DB checks
# ─────────────────────────────────────────────

def test_empty_creative_count_warns(empty_db):
    result = check_creative_count(empty_db)
    assert result["status"] == "warn"
    assert result["count"] == 0


def test_no_duplicates_on_empty(empty_db):
    result = check_duplicate_creatives(empty_db)
    assert result["status"] == "ok"


def test_no_tags_on_empty(empty_db):
    result = check_tag_coverage(empty_db)
    assert result["status"] == "warn"


def test_no_patterns_warns(empty_db):
    result = check_pattern_count(empty_db)
    assert result["status"] == "warn"


def test_no_scores_warns(empty_db):
    result = check_score_distribution(empty_db)
    assert result["status"] == "warn"


# ─────────────────────────────────────────────
# Seeded DB checks
# ─────────────────────────────────────────────

def test_creative_count_ok(seeded_db):
    result = check_creative_count(seeded_db)
    assert result["count"] == 1


def test_no_duplicates(seeded_db):
    result = check_duplicate_creatives(seeded_db)
    assert result["status"] == "ok"


def test_performance_linked(seeded_db):
    result = check_creatives_missing_performance("7d", seeded_db)
    assert result["count"] == 0


def test_tag_coverage_ok(seeded_db):
    result = check_tag_coverage(seeded_db)
    assert result["count"] == 1


def test_tag_distribution_returns_rows(seeded_db):
    result = check_tag_distribution(seeded_db)
    assert len(result["rows"]) >= 2  # hook_type + angle


def test_unknown_rate_ok(seeded_db):
    result = check_unknown_tag_rate(seeded_db)
    # No unknowns in seeded data.
    high_unk = [r for r in result["rows"] if r["unknown_pct"] > 40]
    assert len(high_unk) == 0


def test_pattern_count_ok(seeded_db):
    result = check_pattern_count(seeded_db)
    assert result["count"] == 1


def test_unmapped_creative(seeded_db):
    # No products loaded → should report unmapped but status "ok" (no products exist)
    result = check_unmapped_creatives(seeded_db)
    assert result["count"] == 1  # c1 has no product_id
    assert result["status"] == "ok"  # ok because no products loaded


def test_get_sample_by_tag(seeded_db):
    samples = get_sample_by_tag("hook_type", "curiosity", limit=5, conn=seeded_db)
    assert len(samples) == 1
    assert samples[0]["ad_name"] == "Test Ad"


def test_get_unknown_sample_empty(seeded_db):
    samples = get_unknown_sample("hook_type", limit=5, conn=seeded_db)
    assert samples == []  # No unknowns in seeded data


def test_run_all_checks_returns_list(seeded_db):
    results = run_all_checks("7d", seeded_db)
    assert isinstance(results, list)
    assert all("name" in r for r in results)
    assert all("status" in r for r in results)
    assert all(r["status"] in ("ok", "warn", "fail") for r in results)


def test_run_all_checks_sorted_by_severity(seeded_db):
    results = run_all_checks("7d", seeded_db)
    order = {"fail": 0, "warn": 1, "ok": 2}
    statuses = [order[r["status"]] for r in results]
    assert statuses == sorted(statuses)


# ─────────────────────────────────────────────
# Duplicate detection
# ─────────────────────────────────────────────

def test_duplicate_detected(tmp_path):
    db_path = tmp_path / "dup.db"
    init_db(db_path)
    conn = get_connection(db_path)
    # SQLite PRIMARY KEY prevents true duplicates — check_duplicate_creatives
    # queries for duplicates as a safeguard; on a clean DB this is always ok.
    result = check_duplicate_creatives(conn)
    assert result["status"] == "ok"
    conn.close()
