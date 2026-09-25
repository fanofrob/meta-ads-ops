"""
fetch_insights.py
Fetches ad-level insights and saves raw JSON to data/raw/.
Read-only. No data transformation.

Usage:
  python src/fetch_insights.py                # yesterday (daily snapshot)
  python src/fetch_insights.py --last7d       # last 7 days aggregated (primary metrics)
  python src/fetch_insights.py --historical   # last 30 days, one row per day per ad (trend engine)
"""

import argparse
from datetime import date

from api import base_url, load_env, paginated_get, save_raw

FIELDS = (
    "campaign_id,campaign_name,"
    "adset_id,adset_name,"
    "ad_id,ad_name,"
    "spend,impressions,clicks,ctr,cpc,cpm,frequency,"
    "actions,action_values,cost_per_action_type"
)


def fetch_daily(env: dict) -> None:
    """Fetch yesterday's data. One aggregated row per ad."""
    print(f"  Date preset: yesterday")
    print(f"  Level      : ad\n")

    insights = paginated_get(
        url=base_url(env, "insights"),
        params={
            "fields": FIELDS,
            "level": "ad",
            "date_preset": "yesterday",
            "limit": 100,
            "access_token": env["META_ACCESS_TOKEN"],
        },
        label="insight rows",
    )

    out_path = save_raw(insights, f"insights_{date.today().isoformat()}.json")
    print(f"\n[OK] Fetched {len(insights)} insight rows.")
    print(f"  Saved to {out_path}")


def fetch_7d(env: dict) -> None:
    """Fetch last 7 days aggregated — one row per ad covering the full 7-day period.
    Used as the primary metrics source for all report tables and priority signals.
    """
    print(f"  Date preset: last_7d")
    print(f"  Level      : ad\n")

    insights = paginated_get(
        url=base_url(env, "insights"),
        params={
            "fields": FIELDS,
            "level": "ad",
            "date_preset": "last_7d",
            "limit": 100,
            "access_token": env["META_ACCESS_TOKEN"],
        },
        label="insight rows",
    )

    out_path = save_raw(insights, f"insights_7d_{date.today().isoformat()}.json")
    print(f"\n[OK] Fetched {len(insights)} insight rows (7-day aggregated).")
    print(f"  Saved to {out_path}")


def fetch_90d(env: dict) -> None:
    """Fetch last 90 days aggregated — one row per ad. Feeds winning-pattern
    extraction, which needs more purchases than a 7- or 30-day window holds.
    """
    print(f"  Date preset: last_90d")
    print(f"  Level      : ad\n")

    insights = paginated_get(
        url=base_url(env, "insights"),
        params={
            "fields": FIELDS,
            "level": "ad",
            "date_preset": "last_90d",
            "limit": 100,
            "access_token": env["META_ACCESS_TOKEN"],
        },
        label="insight rows",
    )

    out_path = save_raw(insights, f"insights_90d_{date.today().isoformat()}.json")
    print(f"\n[OK] Fetched {len(insights)} insight rows (90-day aggregated).")
    print(f"  Saved to {out_path}")


def fetch_historical(env: dict) -> None:
    """Fetch last 30 days with time_increment=1 — one row per ad per day.
    Required for trend detection: CTR drops, CPA spikes, frequency growth, spend pacing.
    """
    print(f"  Date preset: last_30d")
    print(f"  Level      : ad")
    print(f"  Breakdown  : daily (time_increment=1)")
    print(f"  Note       : This may take several minutes for large accounts.\n")

    insights = paginated_get(
        url=base_url(env, "insights"),
        params={
            "fields": FIELDS,
            "level": "ad",
            "date_preset": "last_30d",
            "time_increment": 1,
            "limit": 100,
            "access_token": env["META_ACCESS_TOKEN"],
        },
        label="insight rows",
    )

    out_path = save_raw(insights, f"insights_30d_{date.today().isoformat()}.json")
    print(f"\n[OK] Fetched {len(insights)} insight rows ({len(insights)} ad-day combinations).")
    print(f"  Saved to {out_path}")


def main():
    parser = argparse.ArgumentParser(description="Fetch Meta Ads insights.")
    parser.add_argument(
        "--last7d",
        action="store_true",
        help="Fetch last 7 days aggregated (primary metrics source).",
    )
    parser.add_argument(
        "--last90d",
        action="store_true",
        help="Fetch last 90 days aggregated (winning-pattern extraction).",
    )
    parser.add_argument(
        "--historical",
        action="store_true",
        help="Fetch last 30 days with daily breakdowns (trend engine).",
    )
    args = parser.parse_args()

    print("Fetching insights...\n")

    env = load_env()
    print(f"  Account ID : {env['META_AD_ACCOUNT_ID']}")
    print(f"  API version: {env['META_API_VERSION']}")

    if args.last7d:
        fetch_7d(env)
    elif args.last90d:
        fetch_90d(env)
    elif args.historical:
        fetch_historical(env)
    else:
        fetch_daily(env)


if __name__ == "__main__":
    main()
