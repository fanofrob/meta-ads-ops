# Daily Report Specification

## File Format

- Location: `outputs/reports/YYYY-MM-DD.md`
- Format: Markdown
- Generated once per day, covering the previous calendar day (account timezone)

## Required Sections

### 1. Account Overview

A single table of top-level account metrics for the day.

| Metric | Value |
|--------|-------|
| Total Spend | |
| Impressions | |
| Clicks | |
| CTR | |
| CPC | |
| CPM | |
| ROAS (if applicable) | |
| Active Campaigns | |
| Active Ad Sets | |
| Active Ads | |

Sourced from account-level insights endpoint. No computation required beyond formatting.

### 2. Strategy Snapshot

A short paragraph (3–5 sentences) describing the current account structure and strategy:
- How many campaigns are running and what their objectives are
- Whether budget is managed at campaign (CBO) or ad set level
- The dominant bidding strategy across ad sets
- Any structural issues (e.g., single ad set per campaign, too few ads per ad set)

Written in plain prose. Derived from campaign and ad set fields already fetched — no additional API calls.

### 3. Campaign Structure

Table of all campaigns (active and paused) with:

| Campaign | Objective | Status | Budget Type | Daily Budget | Spend | Impressions | Clicks | CTR | CPC | ROAS |
|----------|-----------|--------|-------------|--------------|-------|-------------|--------|-----|-----|------|

Flag inline (with a note column or symbol) any campaigns that are:
- Paused
- In learning phase
- Have disapproved ads
- Budget-limited (spend hit cap before end of day)

### 4. Bidding Overview

Table of ad sets with bidding details:

| Ad Set | Campaign | Bid Strategy | Bid/Target | Spend | CPA | Delivery |
|--------|----------|--------------|------------|-------|-----|----------|

Delivery column values: `Active`, `Learning`, `Learning Limited`, `Paused`, `Disapproved`.

No inference or scoring — just report what the API returns.

### 5. Creative Health

Table of active ads grouped by ad set:

| Ad | Ad Set | Status | Impressions | CTR | CPC | Frequency |
|----|--------|--------|-------------|-----|-----|-----------|

Flag any ads that are:
- Disapproved
- High frequency (> 3.0) — potential creative fatigue
- Zero impressions while the ad set is active — possible delivery issue

No creative scoring. Flags are binary (yes/no) based on thresholds.

### 6. Anomalies & Risks

A bulleted list of issues detected. Each item includes:
- **What**: description of the anomaly
- **Where**: campaign / ad set / ad name
- **Severity**: Low / Medium / High
- **Why it matters**: brief impact explanation

Anomalies to detect:
- Spend > 2× the 7-day daily average
- CTR dropped > 30% day-over-day
- Ad set in learning phase > 7 days
- Disapproved ads blocking delivery
- Budget-limited campaigns
- ROAS below account floor (if defined)
- All ads in an ad set have zero impressions

### 7. Recommended Actions

A numbered list of concrete next steps, ordered by priority. Each item:
- Action to take
- Expected outcome
- Effort: Low / Medium / High

Keep to ≤ 5 items. If there are no issues, say so explicitly.

### 8. Confidence Notes

A short section flagging anything that may affect report reliability:
- Missing data (e.g., no ROAS because pixel is not set up)
- Small sample sizes (< 1,000 impressions — metrics may be noisy)
- Attribution window caveats
- Any API errors or incomplete responses during fetch

If none, write: "No data quality issues detected."

---

## Data Sources

All data pulled from the Meta Marketing API using date range `yesterday`.

**Account-level insights** (`/act_{id}/insights`):
- `spend`, `impressions`, `clicks`, `reach`, `ctr`, `cpc`, `cpm`, `actions`, `cost_per_action_type`

**Campaigns** (`/act_{id}/campaigns`):
- `name`, `objective`, `status`, `daily_budget`, `budget_remaining`, `bid_strategy`

**Ad Sets** (`/act_{id}/adsets`):
- `name`, `campaign_id`, `status`, `bid_strategy`, `bid_amount`, `daily_budget`, `optimization_goal`
- Insights: `spend`, `impressions`, `clicks`, `ctr`, `cpc`, `actions`, `cost_per_action_type`

**Ads** (`/act_{id}/ads`):
- `name`, `adset_id`, `status`
- Insights: `impressions`, `clicks`, `ctr`, `cpc`, `frequency`

## Tone

- Direct and factual. No filler.
- Flag risks clearly — don't soften problems.
- Recommendations should be actionable, not vague.
- Confidence Notes should be honest about data gaps, not reassuring.
