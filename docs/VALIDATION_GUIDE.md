# Creative Intelligence — V1 Validation Guide

> **Goal:** Before trusting any analysis or generation output, confirm that
> ingest, tagging, and pattern extraction are all working correctly.
> This guide tells you exactly what to run, what to look at, and what to fix.

---

## The principle

**Don't generate until you trust the intelligence layer.**

Every analysis or hook generated is only as good as the data underneath it.
Work through this guide top-to-bottom before running any generation commands.

---

## Recommended review sequence

Run these commands in order. Each step gates the next.

```
Step 1 → status        — confirm DB is alive
Step 2 → validate      — run all QA checks
Step 3 → qa-report     — inspect tag distribution and unknowns
Step 4 → inspect-tag   — drill into specific dimensions
Step 5 → report        — generate the full report (includes QA health header)
```

---

## Step 1 — Confirm the DB is alive

```bash
python -m creative_intelligence.cli status
```

**What to look for:**
- All tables are listed with row counts > 0
- `creatives` should have rows; if it shows 0, run ingest first

**If creatives = 0:**
```bash
python -m creative_intelligence.cli ingest --date-range 7d
```

---

## Step 2 — Run all QA checks

```bash
python -m creative_intelligence.cli validate --date-range 7d
```

**Output format:**
```
[✓] creative_count       ...   5 creatives ingested
[⚠] creatives_missing_performance_7d  ...  3 of 5 have no performance data
[✗] duplicate_creatives  ...  2 duplicate IDs found
```

**Icons:**
| Icon | Meaning |
|------|---------|
| ✓    | Good — no action needed |
| ⚠    | Warning — advisory, review before acting on recommendations |
| ✗    | Failure — must be resolved before trusting output |

**Critical failures to fix before proceeding:**

| Failure | Likely cause | Fix |
|---------|-------------|-----|
| `duplicate_creatives` | Ingest ran twice | Re-init DB: `cli init` then re-ingest |
| `performance_sanity` | Bad metric values | Check raw data files in `data/raw/` |
| `creative_count` = 0 | No ingest | Run `cli ingest` |

**Warnings that are OK to proceed with:**

| Warning | When it's fine |
|---------|---------------|
| `null_roas` / `null_cpa` all null | Normal if no purchase pixel or conversion event |
| `unmapped_creatives` | Expected until products are loaded |
| `copy_field_coverage` low | Some ad types don't have hook/primary text |

---

## Step 3 — Inspect tag distribution

```bash
python -m creative_intelligence.cli qa-report
```

**What to look for:**

### 3a. Copy field coverage
Are `hook_text`, `primary_text`, and `headline` populated for most creatives?
If coverage is below 50%, the tagging rules have limited text to work with
and unknown rates will be artificially high.

> **Action if low:** Re-run ingest with `--skip-copy-fetch=false` to pull
> ad copy from the Meta API.

### 3b. Tag distribution
Each dimension should show a spread of values, not a single dominant label.

**Healthy distribution example (hook_type):**
```
curiosity        12  (31%)  ████████
pain_point        8  (21%)  █████
question          6  (16%)  ████
social_proof      5  (13%)  ███
unknown           4  (10%)  ██
...
```

**Red flags:**
| Pattern | What it means |
|---------|--------------|
| `unknown` > 50% of a dimension | Keyword rules have poor coverage for your ad style |
| One value = 90%+ | Rules may be too broad / overfitted |
| All values near-zero except one | Not enough variation in your creative portfolio |

### 3c. Unknown rate by dimension
The `qa-report` prints a summary like:
```
hook_type         unknown:  4 /  38  (10.5%)
angle             unknown: 18 /  38  (47.4%)  ← HIGH
format            unknown:  2 /  38   (5.3%)
```

- **> 40% unknown for a dimension:** That dimension's rules need calibration.
- It does NOT mean your analysis is broken — it means that dimension is
  not yet reliable for filtering. Use other dimensions in the meantime.

---

## Step 4 — Drill into specific tag values

After reviewing the distribution, use `inspect-tag` to sample real creatives
behind each tag value and confirm they were correctly classified.

```bash
# See sample creatives tagged as "curiosity" hook type
python -m creative_intelligence.cli inspect-tag hook_type curiosity

# See what's falling through to "unknown" for the angle dimension
python -m creative_intelligence.cli inspect-tag angle unknown

# Check what "direct_offer" hooks look like
python -m creative_intelligence.cli inspect-tag hook_type direct_offer
```

**What to check for each tag value:**
1. Do the ads shown actually match the label?
2. If yes → rule is working. Move on.
3. If no → the rule is mis-firing. See Section 6 below (tagging calibration).

---

## Step 5 — Generate the full report

```bash
python -m creative_intelligence.cli report --date-range 7d
```

The report opens with a **Data Quality** section — a table of all QA checks.
Review this section first before reading the analysis sections.

If the data quality header shows ✓ all green, the rest of the report is
reliable. If it shows warnings, use the analysis sections directionally
but don't make spend decisions based on them yet.

Report is written to: `outputs/creative_reports/YYYY-MM-DD-creative-report.md`

---

## Section 6 — Tagging calibration workflow

Use this when unknown rates are high or tagging looks wrong.

### Step A — Understand why a specific creative was tagged that way

From a Python shell or notebook:
```python
from creative_intelligence.tagging.calibration import print_explanation
print_explanation("your-creative-id-here")
```

Output shows every dimension, which keyword fired, and all rules tested.

### Step B — Test a new keyword before adding it

```python
from creative_intelligence.tagging.calibration import print_keyword_test
print_keyword_test("obsessed", "hook_type")
```

This tells you:
- How many creatives would match (% of total)
- Sample ads that would be captured
- How many existing "unknowns" would be rescued

**Calibration rules of thumb:**
| Match % | Assessment |
|---------|-----------|
| < 5%    | Very specific — good for rare patterns, may be too narrow |
| 5–20%   | Good signal — safe to add |
| 20–35%  | Moderate — check samples carefully |
| > 35%   | Overly broad — likely to mislabel |

### Step C — Simulate the full effect of a rule change

Before editing `rule_tagger.py`, simulate the impact:
```python
from creative_intelligence.tagging.calibration import print_simulation

# Simulate adding a new "obsessed" keyword to hook_type curiosity
new_rules = {
    "hook_type": [
        ("question",    [r"\?"]),
        ("curiosity",   ["secret", "hidden", "obsessed", "most people don't", ...]),
        # ... rest of rules unchanged
    ]
}
print_simulation(new_rules)
```

Output shows before/after counts for every affected dimension.

### Step D — Apply the change to rule_tagger.py

Only after simulation confirms the change is safe:
1. Edit `creative_intelligence/tagging/rule_tagger.py`
2. Add the keyword to the relevant list
3. Re-run tagging: `python -m creative_intelligence.cli tag`
4. Re-run validate: `python -m creative_intelligence.cli validate`

### Calibration guardrails

- **Don't add more than 3–5 new keywords per session.** Add in small batches and
  re-validate between each batch.
- **Don't try to eliminate all unknowns in one pass.** Some creatives genuinely
  don't fit a category. Unknown is a valid state.
- **Don't fit rules to specific ad names.** Rules should generalise to creative
  copy, not internal naming conventions.
- **Don't change the order of rules without testing.** Rules fire on first match —
  order determines priority.

---

## Step 7 — Check the coverage of each rule

```python
from creative_intelligence.tagging.calibration import print_coverage_report
print_coverage_report()
```

Look for:
- **DEAD rules** (0 matches) — the keyword may be too specific for your account.
  Remove or replace these to keep rules clean.
- **OVERFIT rules** (> 50% match) — too broad. Narrow the keyword or move it
  lower in priority.

---

## Step 8 — Check the old ops pipeline is still intact

After any changes to the creative intelligence subsystem, always confirm
the original ops pipeline is unaffected:

```bash
python3 -m pytest tests/test_ops_pipeline_smoke.py -v
```

All tests must pass. If any fail, stop and investigate before proceeding.

---

## Step 9 — Run the full test suite

```bash
python3 -m pytest tests/ -q
```

All tests must pass. Look for:
- `tests/test_ops_pipeline_smoke.py` — original pipeline guard
- `tests/creative_intelligence/test_validation.py` — QA checks
- `tests/creative_intelligence/test_tagging.py` — tagging rules
- `tests/creative_intelligence/test_ingest.py` — ingest integrity

---

## Step 10 — First real-world test (after validation passes)

Once the validation guide confirms your data is trustworthy:

**Recommended first test — smallest high-signal option:**

```bash
# 1. Find the top-performing pattern
python -m creative_intelligence.cli report --date-range 7d
# → Note the top pattern name and its pattern_id from the DB

# 2. Generate 10 hooks from that pattern only (dry run first)
python -m creative_intelligence.cli generate-hooks \
    --max-patterns 1 \
    --count 10 \
    --date-range 7d
    # dry-run is ON by default

# 3. Review the output. If it looks good:
python -m creative_intelligence.cli generate-hooks \
    --max-patterns 1 \
    --count 10 \
    --date-range 7d \
    --no-dry-run
```

**What makes this the right first test:**
- Only uses the single best-validated pattern
- Dry run by default — nothing is written until you confirm
- Hooks are the lowest-risk creative asset (copy only, no spend involved)
- Results are easy to manually review for quality

**Do not run `generate-hooks --max-patterns 5` or `generate-brief` yet.**
Wait until at least 2 weeks of tag + pattern data has been validated.

---

## Validation checklist (quick reference)

Use this before any generation run:

- [ ] `cli status` — DB has rows in all expected tables
- [ ] `cli validate` — 0 failures, warnings reviewed
- [ ] `cli qa-report` — tag distribution looks sensible
- [ ] `cli inspect-tag hook_type unknown` — unknowns inspected
- [ ] `cli inspect-tag angle unknown` — unknowns inspected
- [ ] `cli report` — Data Quality header is ✓ or acceptable ⚠
- [ ] `pytest tests/test_ops_pipeline_smoke.py` — ops pipeline intact
- [ ] `pytest tests/` — all tests pass

---

## What "good enough" looks like for v1

You don't need perfection. Here is the minimum bar before generating:

| Metric | Minimum bar |
|--------|------------|
| Creatives ingested | ≥ 10 (ideally ≥ 30) |
| Performance linked | ≥ 70% of creatives have metrics |
| Tag coverage | ≥ 70% of creatives tagged |
| Unknown rate (hook_type) | < 40% |
| Unknown rate (angle) | < 50% |
| Patterns extracted | ≥ 3 with ≥ 2 creatives each |
| Ops pipeline smoke tests | 100% pass |
