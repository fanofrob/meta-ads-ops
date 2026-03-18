"""
fetch_campaigns.py
Fetches all campaigns for the ad account and saves raw JSON to data/raw/.
Read-only. No data transformation.
"""

from datetime import date

from api import base_url, load_env, paginated_get, save_raw

FIELDS = "id,name,objective,status,buying_type,bid_strategy,daily_budget"


def main():
    print("Fetching campaigns...\n")

    env = load_env()
    print(f"  Account ID : {env['META_AD_ACCOUNT_ID']}")
    print(f"  API version: {env['META_API_VERSION']}\n")

    campaigns = paginated_get(
        url=base_url(env, "campaigns"),
        params={"fields": FIELDS, "limit": 100, "access_token": env["META_ACCESS_TOKEN"]},
        label="campaigns",
    )

    out_path = save_raw(campaigns, f"campaigns_{date.today().isoformat()}.json")

    print(f"\n[OK] Fetched {len(campaigns)} campaigns.")
    print(f"  Saved to {out_path}")


if __name__ == "__main__":
    main()
