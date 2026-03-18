# Project Goals

## Primary Objective

Provide daily visibility into the performance and health of a Meta Ads account, enabling faster and better-informed decisions.

## Phase 1 — Reporting (current)

- Connect to the Meta Marketing API and pull daily account snapshots
- Generate structured daily reports covering:
  - Campaign and ad set structure
  - Spend, impressions, clicks, CPC, CPM, CTR, ROAS
  - Bidding strategies and budget allocation
  - Anomalies (spend spikes, CTR drops, disapproved ads)
  - Recommended actions with rationale
- Reports are read-only. No changes to the account are made.

## Phase 2 — Guarded Automation (future)

Automate low-risk, high-confidence changes with mandatory human approval.

### Automation Policy

| Risk Level | Examples | Policy |
|------------|----------|--------|
| Low | Pause an underperforming ad (spend > threshold, ROAS < floor) | Propose + require explicit approval |
| Medium | Adjust daily budget ±10% | Propose + require explicit approval + 24h wait |
| High | New campaigns, audience changes, creative changes | Never automated — recommendation only |

**Default behavior: all automation scripts run in `--dry-run` mode.** A human must pass `--execute` and confirm the action to apply any change.

## Non-Goals

- This project does not replace a media buyer or strategist.
- It does not manage creative production.
- It does not connect to other ad platforms (for now).
