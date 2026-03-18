# CLAUDE.md — Instructions for Claude Code

## Project: meta-ads-ops

This file defines how Claude should behave when working in this repository.

## Context

This project connects to a live Meta Ads account. Mistakes can have real financial consequences. All actions involving the Marketing API must be treated with care.

## Rules

### General
- Read existing files before editing them.
- Do not create files unless necessary. Prefer editing existing ones.
- Keep code minimal and focused. Do not add features that weren't requested.

### API & Automation
- **Never call a write/mutate Meta API endpoint** (create, update, delete campaigns/ad sets/ads/budgets) unless the user has explicitly confirmed the action in this session.
- All automation scripts must have a `--dry-run` flag that is on by default.
- Log every API call with timestamp and endpoint to `outputs/api.log`.

### Data
- Raw API responses go in `data/raw/` as timestamped JSON files. Never overwrite them.
- Never commit `.env` or any file containing credentials.

### Reporting
- Reports go in `outputs/reports/` with the filename format `YYYY-MM-DD.md`.
- Reports must include: summary, campaign structure, bidding overview, anomalies/risks, and recommended actions.

### Testing
- Tests go in `tests/`. Run tests before marking a task complete.
- Mock all external API calls in tests — never hit the live API in tests.

## Key Files

| File | Purpose |
|------|---------|
| `.env` | Credentials (not committed) |
| `.env.example` | Template for credentials |
| `docs/goals.md` | Project goals and automation policy |
| `docs/reporting-spec.md` | Report format specification |
| `docs/access-checklist.md` | API access prerequisites |
