"""
fetch_adsets.py
Fetches all ad sets for the ad account and saves raw JSON to data/raw/.
Read-only. No data transformation.
"""

from datetime import date

from api import base_url, load_env, paginated_get, save_raw

FIELDS = "id,name,campaign_id,status,bid_strategy,bid_amount,optimization_goal,daily_budget,lifetime_budget"


def main():
    print("Fetching ad sets...\n")

    env = load_env()
    print(f"  Account ID : {env['META_AD_ACCOUNT_ID']}")
    print(f"  API version: {env['META_API_VERSION']}\n")

    adsets = paginated_get(
        url=base_url(env, "adsets"),
        params={"fields": FIELDS, "limit": 100, "access_token": env["META_ACCESS_TOKEN"]},
        label="ad sets",
    )

    out_path = save_raw(adsets, f"adsets_{date.today().isoformat()}.json")

    print(f"\n[OK] Fetched {len(adsets)} ad sets.")
    print(f"  Saved to {out_path}")


if __name__ == "__main__":
    main()
