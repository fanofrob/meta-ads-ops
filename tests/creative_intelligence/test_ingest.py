"""Tests for ingest adapters (file-based, no live API calls)."""
import json
import pytest
from pathlib import Path

from creative_intelligence.ingest.ads_reader import (
    _latest_file, load_ads, load_campaigns, build_creative_records,
)
from creative_intelligence.ingest.insights_reader import parse_insights


@pytest.fixture
def raw_data_dir(tmp_path):
    """Populate a temp directory with minimal fixture raw data files."""
    # campaigns
    campaigns = [
        {"id": "camp-1", "name": "Cherry Plum Campaign", "status": "ACTIVE",
         "objective": "CONVERSIONS"},
    ]
    (tmp_path / "campaigns_2026-04-07.json").write_text(json.dumps(campaigns))

    # adsets
    adsets = [
        {"id": "adset-1", "name": "Adset One", "campaign_id": "camp-1",
         "status": "ACTIVE"},
    ]
    (tmp_path / "adsets_2026-04-07.json").write_text(json.dumps(adsets))

    # ads
    ads = [
        {"id": "ad-1", "name": "Test Ad 1", "adset_id": "adset-1",
         "status": "ACTIVE", "creative": {"id": "creative-1"}},
        {"id": "ad-2", "name": "Test Ad 2", "adset_id": "adset-1",
         "status": "PAUSED", "creative": {"id": "creative-2"}},
    ]
    (tmp_path / "ads_2026-04-07.json").write_text(json.dumps(ads))

    # insights 7d
    insights = [
        {"ad_id": "ad-1", "spend": "50.00", "impressions": "10000",
         "clicks": "200", "ctr": "2.0", "cpc": "0.25", "cpm": "5.0",
         "frequency": "1.5", "actions": [{"action_type": "omni_purchase", "value": "5"}]},
        {"ad_id": "ad-2", "spend": "25.00", "impressions": "5000",
         "clicks": "80", "ctr": "1.6", "cpc": "0.31", "cpm": "5.0",
         "frequency": "1.2"},
    ]
    (tmp_path / "insights_7d_2026-04-07.json").write_text(json.dumps(insights))

    return tmp_path


def test_latest_file_found(raw_data_dir):
    path = _latest_file("ads", raw_data_dir)
    assert path is not None
    assert path.name == "ads_2026-04-07.json"


def test_latest_file_missing(tmp_path):
    path = _latest_file("ads", tmp_path)
    assert path is None


def test_load_ads(raw_data_dir):
    ads = load_ads(raw_data_dir)
    assert len(ads) == 2
    assert ads[0]["id"] == "ad-1"


def test_load_campaigns(raw_data_dir):
    camps = load_campaigns(raw_data_dir)
    assert len(camps) == 1
    assert camps[0]["name"] == "Cherry Plum Campaign"


def test_build_creative_records(raw_data_dir):
    records = build_creative_records(raw_data_dir)
    assert len(records) == 2
    r = records[0]
    assert r["creative_id"] == "creative-1"
    assert r["ad_id"] == "ad-1"
    assert r["campaign_id"] == "camp-1"
    assert r["campaign_name"] == "Cherry Plum Campaign"


def test_build_creative_records_empty_dir(tmp_path):
    records = build_creative_records(tmp_path)
    assert records == []


def test_parse_insights(raw_data_dir):
    perf = parse_insights("7d", raw_data_dir)
    assert len(perf) == 2

    by_id = {p["ad_id"]: p for p in perf}
    assert "ad-1" in by_id
    assert by_id["ad-1"]["spend"] == 50.0
    assert by_id["ad-1"]["purchases"] == 5
    assert by_id["ad-1"]["impressions"] == 10000


def test_parse_insights_missing_file(tmp_path):
    perf = parse_insights("7d", tmp_path)
    assert perf == []
