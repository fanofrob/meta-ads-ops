# V2 Verdict Engine — Release Notes

**Frozen:** 2026-03-15
**Status:** FROZEN — no behavior changes after this point

---

## What Changed in V2

### Core: Unified Verdict Engine (`classify_campaign()`)
- Replaced four independent ad-hoc classifiers (Fix / Watch / Scale / Budget-plan) with a single
  shared verdict engine: `classify_campaign()`.
- 8-verdict vocabulary: `SCALE / HOLD / MONITOR / REDUCE / PAUSE / CREATIVE_REFRESH / INVESTIGATE / WATCH_ONLY`
- ROAS-primary economics: breakeven ROAS is the anchor signal; CPA is a supporting signal.
- Confidence levels (HIGH / MEDIUM / LOW / INSUFFICIENT) gate budget-action verdicts.
- Trend direction (IMPROVING / WORSENING / STABLE / UNKNOWN) informs routing.

### Critical Ordering Fix
- `build_priorities()` was being called before `classify_campaign()` verdicts were attached to
  campaign rows, causing all FIX and SCALE campaign-level items to default to WATCH_ONLY.
- Fixed by moving the verdict attachment block before `build_priorities()`.

### Verdict Explanation Layer
- `_verdict_summary()` produces a one-line human-readable classification note per campaign.
- Rendered in `_render_priority_item()` as an italic classification line beneath campaign name.

### Validator (`validate_report_data()`)
- Six-check pre-render safety gate added as a permanent check:
  1. All active campaigns have a verdict
  2. All verdicts are in the allowed vocabulary
  3. Daily mode contains no budget-action language
  4. Pause candidates have `verdict == PAUSE`
  5. Scale candidates have `verdict == SCALE`
  6. Spend-bucket coverage (informational)
- Wired into `report.py` `main()` for both modes; prints `[VALIDATE] OK` or lists failures.

### Dead Code Removal (Cleanup Pass)
- Removed old inline gating variables from `build_priorities()` SCALE, FIX, WATCH buckets.
- Removed old profitability gate block from `build_budget_plan()` REDUCE.
- Removed duplicate end-of-function parity cross-check loops from `build_priorities()` and
  `build_budget_plan()`.
- Removed orphaned constant `_PRI_SCALE_MAX_FREQ`.

### Post-Freeze Parity Scaffolding Removal (this release)
- All parity-only `[PARITY]` print blocks removed after observation pass confirmed stability.
- Verdict gate `continue` statements retained; only the debug instrumentation removed.

---

## Observation Evidence (pre-freeze)

- **8/8 validator passes** across 5 × 7d and 3 × daily runs
- **Parity stable:** 4 consecutive identical 7d runs; 3 consecutive identical daily runs
- **Daily mode safe:** no budget-change language; investigate/monitor language only
- **7-day action hierarchy clean:** Fix = REDUCE/INVESTIGATE; Watch = monitor signals; Scale = SCALE candidates

---

## Accepted Non-Blockers

| Issue | Classification | Reason |
|-------|---------------|--------|
| Data-fetch race (run 3) | Non-blocker | New 7d file appeared 4 min before 30d file mid-session; produces mismatched date windows → INVESTIGATE surge. Cannot occur in `run_daily.sh` (sequential fetches). |
| OLD_WATCH_NEW_SCALE (Cherry Plum) | Accepted tension | Freq-anomaly gate routes to Watch; budget economics independently support SCALE. Both signals are correct; they describe different dimensions. |

---

## Deferred to v2.1

| Item | Detail |
|------|--------|
| PAUSE persistence | Track prior-period verdict so genuine PAUSE fires instead of REDUCE fallback when a campaign has already been at REDUCE/PAUSE for a prior period. |
| WATCH/SCALE tension annotation | Add a flag or note when freq-anomaly and positive economics conflict (currently silently accepted as an expected tension). |
| Account momentum window | `build_account_momentum()` reads daily snapshot files; switch to `insights_30d` for a proper 7-day momentum signal. |
| `verdict_summary` render upgrade | Currently rendered as italic text below campaign name; promote to a distinct styled block in PDF/HTML output. |
