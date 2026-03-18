"""
fetch_ads.py
Fetches all ads for the ad account and saves raw JSON to data/raw/.
Read-only. No data transformation.
"""

from datetime import date

from api import base_url, load_env, paginated_get, save_raw

FIELDS = "id,name,adset_id,status,creative"


def main():
    print("Fetching ads...\n")

    env = load_env()
    print(f"  Account ID : {env['META_AD_ACCOUNT_ID']}")
    print(f"  API version: {env['META_API_VERSION']}\n")

    ads = paginated_get(
        url=base_url(env, "ads"),
        params={"fields": FIELDS, "limit": 100, "access_token": env["META_ACCESS_TOKEN"]},
        label="ads",
    )

    out_path = save_raw(ads, f"ads_{date.today().isoformat()}.json")

    print(f"\n[OK] Fetched {len(ads)} ads.")
    print(f"  Saved to {out_path}")


if __name__ == "__main__":
    main()
