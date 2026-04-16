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

---

## Creative Intelligence Subsystem

A secondary system that builds a creative intelligence layer on top of the Meta ads data.
Fully isolated from the main pipeline — never modifies `src/` or the existing reporting flow.

### Setup

```bash
# After filling .env with Meta credentials:
PYTHONPATH=. python creative_intelligence/cli.py init
PYTHONPATH=. python creative_intelligence/cli.py ingest
PYTHONPATH=. python creative_intelligence/cli.py tag
PYTHONPATH=. python creative_intelligence/cli.py extract-patterns
```

### CLI Commands

```
init                 Initialise the SQLite database
ingest               Import creatives + performance from raw data files
tag                  Tag all creatives (rule-based; add --ai for LLM tagging)
extract-patterns     Extract winning creative patterns
score                Score all creatives (0–100)
analyze-visuals      Run visual analysis on top creatives (needs OPENAI_API_KEY)
generate-hooks       Generate hook variants from winning patterns (dry-run by default)
generate-brief       Generate UGC or static brief
generate-test-matrix Generate A/B test matrix
load-products        Load product knowledge from JSON/CSV files
sync-shopify         Sync Shopify product catalog into DB (dry-run by default)
validate             Run all QA/integrity checks
qa-report            Full QA report: tag distribution, unknowns, scores
inspect-tag          Show sample creatives for a tag dimension/value
status               Show row counts for all tables
report               Generate the operator-facing creative report
```

### Shopify Product Sync

Connect to your Shopify store to pull live product data into the system.

1. Add to your `.env`:
   ```
   CI_SHOPIFY_ENABLED=1
   SHOPIFY_STORE_URL=mystore.myshopify.com
   SHOPIFY_ACCESS_TOKEN=shpat_...   # read_products scope only
   SHOPIFY_API_VERSION=2024-01
   ```
2. Preview what will be synced (no writes):
   ```bash
   PYTHONPATH=. python creative_intelligence/cli.py sync-shopify
   ```
3. Commit to DB:
   ```bash
   PYTHONPATH=. python creative_intelligence/cli.py sync-shopify --no-dry-run
   ```

---

## Creative Test Lab (local UI)

An internal web interface for generating, rating, and calibrating hooks against real product data.
**Not for production use** — runs only on localhost.

### Run

```bash
# Install Flask if not already installed:
pip install flask

# Start the server (from project root):
PYTHONPATH=. python creative_intelligence/webapp/app.py
```

Then open **http://localhost:5555/test**

The port can be changed via `CI_WEB_PORT=8080` in your `.env`.

### What it does

| Feature | Detail |
|---------|--------|
| Product selector | Populated from the `products` table (sync Shopify first, or load products manually) |
| Goal / Audience | Passed as context into every generation prompt |
| Angle override | Optional — filters patterns by angle before generating |
| Structural diversity | Low / Medium / High — controls how varied hook structures must be |
| Predicted score | Structural + pattern-match score (0–100) computed at generation time |
| Human rating | 1–5 stars — saved to `creative_tests` table immediately |
| Duplicate flag | Marks a hook as duplicate so it can be filtered from analysis |
| History panel | Last 20 hooks, sortable by: newest / highest rated / lowest predicted |

### Calibration signals

After rating, two files are auto-updated in `outputs/`:

| File | Condition | Meaning |
|------|-----------|---------|
| `outputs/pattern_gaps.json` | human ≥ 4 stars AND predicted < 30 | Winning hook the system missed — add as a new pattern |
| `outputs/false_positives.json` | human ≤ 2 stars AND predicted ≥ 70 | System over-scored — review pattern / scoring weights |

Use these two files as calibration input for the next session.
