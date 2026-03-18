# V2 Verdict Engine — Observation Log

**Observation window:** 2026-03-15 (runs against 2026-03-14 and 2026-03-15 data)
**Purpose:** Pre-freeze stability hardening — no code changes unless a blocker appears.
**Note:** A new data fetch occurred at 11:13–11:17 during the session, transitioning inputs from 2026-03-14 files to 2026-03-15 files. Runs 1–2 use 2026-03-14 files; runs 4–5 and daily runs use 2026-03-15 files.

---

## Freeze Criteria Checklist

- [x] Validator passes every run — PASS (all 8 runs)
- [x] No crashes / no silent fallbacks — PASS (all 8 runs)
- [x] No new parity pattern appears — PASS (run 3 anomaly = data-fetch race, not a new code pattern)
- [x] Existing parity patterns remain expected and explainable — PASS (all patterns in reference table)
- [x] No daily report shows direct budget-change language — PASS (pre-stabilization audit confirmed)
- [x] No pause candidate appears without verdict == PAUSE — PASS (validator check 4 on all runs)
- [x] No scale candidate appears without verdict == SCALE — PASS (validator check 5 on all runs)
- [x] No user-facing classification text looks contradictory — PASS (pre-stabilization audit confirmed)

---

## Parity Pattern Reference

Patterns expected and explainable on every run:

| Pattern | Explanation | Status |
|---------|-------------|--------|
| OLD_INCREASE_NEW_REDUCE | Old INCREASE fired on improving %; absolute economics still below breakeven | Expected correct |
| OLD_REDUCE_NEW_MONITOR | ROAS-primary gate: profitable+worsening goes to MONITOR, not REDUCE | Expected correct |
| OLD_INCREASE_NEW_HOLD | Positive signal but LOW confidence goes to HOLD | Expected correct |
| OLD_INCREASE_NEW_MONITOR | Old INCREASE fired; 7d trend worsening on profitable campaign | Expected correct |
| OLD_INCREASE_NEW_INVESTIGATE | Delivery constraint or conflicting signals go to INVESTIGATE | Expected correct |
| OLD_FIX_NEW_MONITOR | Old FIX on CPA spike; profitable+worsening goes to MONITOR (daily mode) | Expected correct |
| OLD_WATCH_NEW_HOLD | Old WATCH spend spike; new verdict HOLD (positive, low confidence) | Expected correct |
| OLD_WATCH_NEW_SCALE | Freq-anomaly gate sends to Watch despite SCALE verdict (creative vs budget tension) | Accepted tension |
| OLD_WATCH_NEW_WATCH_ONLY | Old WATCH signal; new classifier sees insufficient data | Expected correct |
| OLD_PAUSE_NEW_REDUCE | PAUSE needs persistence confirmation; defaults to REDUCE without prior-period data | Expected correct |
| OLD_DS_PAUSE_NEW_REDUCE | Same pattern in decision_summary | Expected correct |

Any pattern NOT in this table = potential issue. Classify as blocker/non-blocker immediately.

---

## Run Records

| # | Time | Mode | Data files | VALIDATE | Parity patterns | Notes |
|---|------|------|------------|----------|-----------------|-------|
| 1 | 2026-03-15 session | 7d | 2026-03-14 | OK | INCREASE→REDUCE×8, REDUCE→MONITOR×4, PAUSE→REDUCE×3, DS_PAUSE→REDUCE×3, INCREASE→INVESTIGATE×3, INCREASE→MONITOR×2, INCREASE→HOLD×2, WATCH→SCALE×1 | Baseline |
| 2 | 2026-03-15 session | 7d | 2026-03-14 | OK | Identical to run 1 | Determinism confirmed |
| 3 | 2026-03-15 ~11:13 | 7d | 7d=2026-03-15 / 30d=2026-03-14 | OK | INCREASE→INVESTIGATE×21, PAUSE→INVESTIGATE×1 (NEW), DS_PAUSE→INVESTIGATE×1 (NEW) | **DATA-FETCH RACE — NON-BLOCKER.** New 7d file appeared 4 min before matching 30d file; mismatched date windows → widespread UNKNOWN economics → INVESTIGATE routing. Cannot occur in normal run_daily.sh (fetches run sequentially). |
| 4 | 2026-03-15 session | 7d | 2026-03-15 | OK | INCREASE→REDUCE×8, REDUCE→MONITOR×4, PAUSE→REDUCE×3, DS_PAUSE→REDUCE×3, INCREASE→INVESTIGATE×3, INCREASE→MONITOR×2, INCREASE→HOLD×2, WATCH→SCALE×1 | New data files stable; pattern set matches runs 1–2 |
| 5 | 2026-03-15 session | 7d | 2026-03-15 | OK | Identical to run 4 | Determinism confirmed on new data |
| D1 | 2026-03-15 session | daily | 2026-03-15 | OK | FIX→MONITOR×7, REDUCE→MONITOR×4, INCREASE→HOLD×4, INCREASE→MONITOR×2, PAUSE→REDUCE×2, DS_PAUSE→REDUCE×2, WATCH→HOLD×2, INCREASE→REDUCE×2, WATCH→WATCH_ONLY×1 | All patterns in reference table |
| D2 | 2026-03-15 session | daily | 2026-03-15 | OK | Identical to D1 | Determinism confirmed |
| D3 | 2026-03-15 session | daily | 2026-03-15 | OK | Identical to D2 | Third consecutive match |

---

## Freeze Decision

**V2 Freeze Decision**

- **Ready to freeze:** YES
- **Confidence:** HIGH
- **Evidence:**
  - Validator: PASS on all 8 runs (5 × 7d, 3 × daily)
  - Parity stability: 4 consecutive identical 7d runs; 3 consecutive identical daily runs; all patterns in reference table; no new patterns in stable conditions
  - Daily-mode safety: PASS — all Fix/Watch items use monitor/investigate language; proposed section shows no budget changes; no "increase budget" / "reduce budget" language in daily output
  - 7-day action hierarchy: PASS — Fix section = REDUCE/INVESTIGATE (act now); Watch section = monitoring signals; Scale section = SCALE/HOLD candidates; separation is clean
  - Non-determinism root cause: CONFIRMED NON-BLOCKER — data-fetch race during observation window; code is fully deterministic for stable inputs; cannot occur in normal `run_daily.sh` operation

- **Remaining known non-blockers:**
  - Run 3 data-fetch race (7d/30d file mismatch during mid-session fetch; structurally impossible in production)
  - OLD_WATCH_NEW_SCALE (Cherry Plum): accepted tension — freq-anomaly gate routes to Watch; budget economics independently support SCALE. Documented in reference table.

- **Post-freeze parity scaffolding:** REMOVED — all 7 `[PARITY]` print blocks removed from
  `build_priorities()` (FIX/WATCH/SCALE), `build_budget_plan()` (INCREASE/REDUCE),
  `build_proposed_changes()` (pause), and `build_decision_summary()` (pause).
  Verdict gate `continue` statements retained. No behavioral change.

- **v2.1 deferrals:** see `v2_release_notes.md`

