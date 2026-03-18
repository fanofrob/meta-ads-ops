# meta-ads-ops

Analyzes a Meta Ads account via the Marketing API and generates daily reports covering campaign strategy, bidding, structure, and risks. A guarded automation layer will be added incrementally.

## Goals

- Pull daily snapshots of account data (campaigns, ad sets, ads, metrics)
- Generate structured reports for human review
- Identify risks, anomalies, and optimization opportunities
- Automate low-risk changes with explicit approval gates

## Stack

- Python 3.11+
- Meta Marketing API (v19+)
- Output: Markdown + JSON reports

## Quickstart

```bash
cp .env.example .env
# Fill in META_ACCESS_TOKEN, META_AD_ACCOUNT_ID, META_API_VERSION

pip install -r requirements.txt
python src/check_access.py   # Verify credentials and API connectivity
```

## Project Structure

```
meta-ads-ops/
  src/          # Scripts for fetching and reporting
  data/raw/     # Raw API responses (JSON)
  outputs/      # Generated reports
  docs/         # Specs, goals, access checklist
  tests/        # Unit and integration tests
```

## Safety

No automated changes are made without a human approval step. See `docs/goals.md` for the automation policy.
