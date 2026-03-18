"""
report.py
Generates a daily markdown report from report_data.py output.
No API calls. No mutation of raw data.
"""

from datetime import date, datetime, timezone
from pathlib import Path

from report_data import build_report_data, compute_health_score, validate_report_data

OUTPUT_DIR = Path("outputs/reports")
HIGH_FREQUENCY_THRESHOLD = 3.0
LOW_IMPRESSIONS_THRESHOLD = 1000


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_money(value) -> str:
    if value is None:
        return "N/A"
    return f"${float(value):,.2f}"


def _fmt_pct(value) -> str:
    if value is None:
        return "N/A"
    return f"{float(value):.2f}%"


def _fmt_num(value) -> str:
    if value is None:
        return "N/A"
    return f"{int(value):,}"


def _fmt_float(value, decimals: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"{float(value):.{decimals}f}"


def _fmt_roas(value) -> str:
    """Format ROAS with 'x' suffix; returns '—' (not 'N/Ax') when value is None."""
    if value is None:
        return "—"
    return f"{float(value):.2f}x"


import re as _re

def _clean_product_name(name: str) -> str:
    """Strip parenthetical qualifiers from product tab names for display.
    e.g. 'Bing Cherry (BC)' → 'Bing Cherry', 'Cotton Candy Grape (needs price per lb)' → 'Cotton Candy Grape'
    """
    return _re.sub(r'\s*\(.*?\)\s*$', '', name).strip()


def _na(value) -> str:
    if value is None or value == "" or value == []:
        return "N/A"
    return str(value)


def _fmt_change(pct) -> str:
    if pct is None:
        return "N/A"
    arrow = "▲" if pct > 0 else "▼"
    return f"{pct:+.1f}% {arrow}"


def _status_flag(row: dict) -> str:
    flags = []
    if row.get("status") == "PAUSED":
        flags.append("⏸ Paused")
    if row.get("has_disapproved_ads"):
        flags.append("⚠ Disapproved ads")
    return ", ".join(flags) if flags else ""


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def _section_metadata(report_date: date, mode: str = "7d") -> str:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if mode == "daily":
        title = f"Meta Ads Daily Report — {report_date}"
        period_label = f"{report_date} (yesterday — trend-based signals vs 7-day avg)"
    else:
        title = f"Meta Ads 7-Day Report — {report_date}"
        period_label = f"Week ending {report_date} (last 7 days vs prior week)"
    return f"""\
# {title}

| | |
|---|---|
| Report date | {period_label} |
| Generated | {generated_at} |
| Source | Meta Marketing API |

---
"""


def _section_economics_warning(warning: str) -> str:
    """Renders a prominent warning banner when economics data is from a snapshot."""
    if not warning:
        return ""
    return f"""\
> **⚠ Unit Economics Warning**
> {warning}

---
"""


_BULK_UPDATE_THRESHOLD = 5  # distinct products changed → treat as monthly bulk update


def _section_economics_changes(changes: list) -> str:
    """
    Renders unit economics changes since last run.
    Returns empty string if no changes exist.
    Position: after Executive Summary (§1), before Strategy Note (§2).
    If 5+ distinct products changed, shows a simple monthly-update note instead
    of listing every field change.
    """
    if not changes:
        return ""

    distinct_products = {ch.get("product", "") for ch in changes}
    lines = ["## Unit Economics Changes (Since Last Run)", ""]

    # Bulk monthly update — just note it, don't list every field
    if len(distinct_products) >= _BULK_UPDATE_THRESHOLD:
        lines.append(
            f"*Unit economics updated across {len(distinct_products)} products "
            f"(monthly refresh). New thresholds are active in this report.*"
        )
        # Still flag any profitability flips even in bulk mode
        flips = [ch for ch in changes if ch.get("profitability_flip") and ch.get("affected_campaigns")]
        if flips:
            lines.append("")
            lines.append("**Profitability threshold changes requiring attention:**")
            lines.append("")
            seen = set()
            for ch in flips:
                prod = ch.get("product", "")
                if prod in seen:
                    continue
                seen.add(prod)
                impact = ch.get("impact_summary", "")
                affected = ch.get("affected_campaigns", [])
                camp_str = ", ".join(affected[:2])
                if len(affected) > 2:
                    camp_str += f" (+{len(affected) - 2} more)"
                lines.append(f"- **{prod}**: {impact} — {camp_str}")
        lines.append("")
        lines.append("---")
        return "\n".join(lines)

    # Targeted changes (< 5 products) — show full detail
    for ch in changes:
        product = ch.get("product", "")
        field = ch.get("field", "")
        old_val = ch.get("old_value")
        new_val = ch.get("new_value")
        impact = ch.get("impact_summary", "")
        flip = ch.get("profitability_flip", False)
        affected = ch.get("affected_campaigns", [])

        old_str = f"{old_val:.2f}" if isinstance(old_val, float) else str(old_val)
        new_str = f"{new_val:.2f}" if isinstance(new_val, float) else str(new_val)

        lines.append(f"- **{product}** — {field}: {old_str} → {new_str}")
        if affected:
            lines.append(f"  Affected campaigns: {', '.join(affected[:3])}")
        if flip and impact:
            lines.append(f"  ⚠ Profitability threshold changed: {impact}")

    lines.append("")
    lines.append("---")
    return "\n".join(lines)


def _section_account_overview(overview: dict, account_comparison: dict = None) -> str:
    lines = ["## 7. Account Overview", ""]

    cmp = account_comparison or {}
    has_comparison = bool(cmp.get("prior_roas") or cmp.get("prior_spend"))

    def _delta(change_pct, higher_is_better: bool = True) -> str:
        if change_pct is None:
            return ""
        arrow = "▲" if change_pct > 0 else "▼"
        good = (change_pct > 0) == higher_is_better
        sign = "+" if change_pct > 0 else ""
        return f" {sign}{change_pct:.1f}% {arrow}" + (" ✓" if good else " ✗")

    if has_comparison:
        lines.append("| Metric | This Week | Prior Week | Change |")
        lines.append("|--------|-----------|------------|--------|")
        lines.append(f"| Spend | {_fmt_money(cmp.get('curr_spend'))} | {_fmt_money(cmp.get('prior_spend'))} | {_delta(cmp.get('spend_change_pct'))} |")
        lines.append(f"| ROAS | {_fmt_roas(cmp.get('curr_roas'))} | {_fmt_roas(cmp.get('prior_roas'))} | {_delta(cmp.get('roas_change_pct'))} |")
        lines.append(f"| CPA | {_fmt_money(cmp.get('curr_cpa'))} | {_fmt_money(cmp.get('prior_cpa'))} | {_delta(cmp.get('cpa_change_pct'), higher_is_better=False)} |")
        lines.append(f"| Purchases | {_fmt_num(cmp.get('curr_purchases'))} | {_fmt_num(cmp.get('prior_purchases'))} | — |")
        lines.append("")
        lines.append("*From the 7-day aggregated file (Revenue, CTR, CPC, CPM, Frequency below):*")
        lines.append("")

    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Spend | {_fmt_money(overview.get('spend'))} |")
    lines.append(f"| Revenue (Purchase Value) | {_fmt_money(overview.get('revenue'))} |")
    lines.append(f"| ROAS | {_fmt_roas(overview.get('roas'))} |")
    lines.append(f"| Purchases | {_fmt_num(overview.get('purchases'))} |")
    lines.append(f"| CPA | {_fmt_money(overview.get('cpa'))} |")
    lines.append(f"| CTR | {_fmt_pct(overview.get('ctr'))} |")
    lines.append(f"| CPC | {_fmt_money(overview.get('cpc'))} |")
    lines.append(f"| CPM | {_fmt_money(overview.get('cpm'))} |")
    lines.append(f"| Frequency (weighted avg) | {_fmt_float(overview.get('frequency'))} |")
    lines.append(f"| Active Campaigns | {_na(overview.get('active_campaigns'))} |")
    lines.append(f"| Active Ad Sets | {_na(overview.get('active_adsets'))} |")
    lines.append(f"| Active Ads | {_na(overview.get('active_ads'))} |")
    lines.append("")
    lines.append(
        "*Metric definitions: "
        "**ROAS** = Revenue ÷ Spend (higher is better). "
        "**CPA** = Spend ÷ Purchases (lower is better). "
        "**CTR** = Clicks ÷ Impressions. "
        "**CPC** = Spend ÷ Clicks. "
        "**CPM** = Cost per 1,000 impressions. "
        "**Frequency** = Avg times a person saw an ad (>3 signals creative fatigue).*"
    )
    lines.append("")
    lines.append("---")
    return "\n".join(lines)


def _section_health_score(health: dict) -> str:
    score = health.get("score", "UNKNOWN")
    score_icon = {"AT RISK": "🔴", "HEALTHY": "🟢", "GOOD": "🟢", "MIXED": "🟡"}.get(score, "⚪")
    lines = ["## 10. Account Health", ""]
    lines.append(f"**{score_icon} {score}**")
    lines.append("")
    lines.append("| | |")
    lines.append("|---|---|")
    lines.append(f"| Fix items | {health.get('fix_count', 0)} ({health.get('major_fix_count', 0)} with spend ≥ $25) |")
    lines.append(f"| Watch items | {health.get('watch_count', 0)} |")
    lines.append(f"| Scale opportunities | {health.get('scale_count', 0)} |")
    major_spend = health.get("major_fix_spend", 0)
    if major_spend > 0:
        lines.append(f"| Spend in Fix items | ${major_spend:.0f} |")
    lines.append("")
    if score == "AT RISK":
        lines.append("*Fix items with significant spend impact and no scaling opportunities. Prioritize the Fix bucket in Today's Priorities.*")
    elif score in ("HEALTHY", "GOOD"):
        lines.append("*No high-spend Fix items and scaling opportunities are present. Focus on scaling winners.*")
    else:
        lines.append("*Mix of Fix items and scaling opportunities. Address Fix items first, then scale winners.*")
    lines.append("")
    lines.append("---")
    return "\n".join(lines)


_PL_SUMMARY = {
    "ON_TARGET":    "On Target",
    "PROFITABLE":   "Profitable",
    "BREAKEVEN":    "Breakeven",
    "UNPROFITABLE": "Unprofitable",
    "CRITICAL":     "Critical — below ROAS floor",
    "UNKNOWN":      None,
}


def _render_priority_item(item: dict) -> list:
    """Render a single Scale/Watch/Fix item as markdown bullet lines."""
    name = item.get("entity_name", "")
    entity_type = item.get("entity_type", "campaign")
    reason = item.get("reason", "")
    metrics = item.get("metrics", "")
    action = item.get("action", "")
    label = {"campaign": "Campaign", "adset": "Ad Set", "ad": "Ad"}.get(entity_type, entity_type.title())
    lines = [f"- **{name}** ({label})  "]
    lines.append(f"  {reason}  ")
    if metrics:
        lines.append(f"  {metrics}  ")

    # Profitability context line (only for campaign-level items with economics data)
    pl_status = item.get("profitability_status")
    breakeven = item.get("breakeven_roas")
    daily_profit = item.get("estimated_daily_profit")
    pl_label = _PL_SUMMARY.get(pl_status) if pl_status else None
    if pl_label:
        pl_parts = [f"P/L: {pl_label}"]
        if breakeven:
            pl_parts.append(f"Breakeven: {breakeven:.2f}x")
        if daily_profit is not None:
            sign = "+" if daily_profit >= 0 else ""
            pl_parts.append(f"Est. P/L: {sign}${daily_profit:,.0f}")
        lines.append(f"  *{' | '.join(pl_parts)}*  ")

    # Verdict explanation tag (campaign-level items only)
    verdict_summary = item.get("verdict_summary")
    if verdict_summary and entity_type == "campaign":
        lines.append(f"  *Classification: {verdict_summary}*  ")

    lines.append(f"  → {action}")
    return lines


def _section_priorities(priorities: dict) -> str:
    fix_items = priorities.get("fix", [])
    watch_items = priorities.get("watch", [])
    scale_items = priorities.get("scale", [])

    lines = ["## 8. Today's Priorities", ""]

    if not fix_items and not watch_items and not scale_items:
        lines.append("Nothing to flag today.")
        lines.append("")
        lines.append("---")
        return "\n".join(lines)

    if fix_items:
        lines.append(f"**Fix** ({len(fix_items)} item{'s' if len(fix_items) != 1 else ''})")
        lines.append("")
        for item in fix_items:
            lines.extend(_render_priority_item(item))
            lines.append("")

    if watch_items:
        lines.append(f"**Watch** ({len(watch_items)} item{'s' if len(watch_items) != 1 else ''})")
        lines.append("")
        for item in watch_items:
            lines.extend(_render_priority_item(item))
            lines.append("")

    if scale_items:
        lines.append(f"**Scale** ({len(scale_items)} item{'s' if len(scale_items) != 1 else ''})")
        lines.append("")
        for item in scale_items:
            lines.extend(_render_priority_item(item))
            lines.append("")

    lines.append("---")
    return "\n".join(lines)


def _section_strategy_snapshot(snapshot: dict, campaign_rows: list) -> str:
    total = snapshot.get("total_campaigns", 0)
    active = sum(1 for c in campaign_rows if c.get("status") == "ACTIVE")
    paused = total - active

    objectives = snapshot.get("objectives") or []
    bid_strategies = snapshot.get("bid_strategies") or []
    budget_types = snapshot.get("budget_types") or {}

    cbo_count = sum(1 for v in budget_types.values() if v == "CBO")
    adset_level_count = len(budget_types) - cbo_count

    obj_str = ", ".join(objectives) if objectives else "N/A"
    bid_str = ", ".join(bid_strategies) if bid_strategies else "N/A"

    budget_str = []
    if cbo_count:
        budget_str.append(f"{cbo_count} CBO")
    if adset_level_count:
        budget_str.append(f"{adset_level_count} ad set–level")

    lines = ["## 21. Strategy Snapshot", ""]
    lines.append(
        f"The account has {total} campaign{'s' if total != 1 else ''} "
        f"({active} active, {paused} paused) "
        f"with objective{'s' if len(objectives) != 1 else ''}: {obj_str}. "
        f"Budget is managed {' and '.join(budget_str) if budget_str else 'N/A'}. "
        f"Bidding strateg{'ies' if len(bid_strategies) != 1 else 'y'} in use: {bid_str}. "
        f"Ad sets total: {snapshot.get('total_adsets', 'N/A')}."
    )
    lines.append("")
    lines.append("---")
    return "\n".join(lines)


def _section_campaign_intelligence(intel: dict) -> str:
    dist = intel.get("distribution", {})
    total_spend = intel.get("total_spend", 0)

    lines = ["## 22. Campaign Intelligence", ""]
    lines.append("| Type | Campaigns | Active | Spend | % of Spend |")
    lines.append("|------|-----------|--------|-------|------------|")

    for campaign_type, d in dist.items():
        if d["campaigns"] == 0:
            continue
        lines.append(
            f"| {campaign_type} "
            f"| {d['campaigns']} "
            f"| {d['active']} "
            f"| {_fmt_money(d['spend'])} "
            f"| {d['spend_pct']}% |"
        )

    lines.append("")

    # Health note based on distribution
    prosp_pct = dist.get("Prospecting", {}).get("spend_pct", 0)
    retarg_pct = dist.get("Retargeting", {}).get("spend_pct", 0)
    unknown_pct = dist.get("Unknown", {}).get("spend_pct", 0)

    notes = []
    if unknown_pct > 20:
        notes.append(f"⚠ {unknown_pct}% of spend is in unclassified campaigns — review naming conventions.")
    if retarg_pct > 40:
        notes.append(f"⚠ Retargeting is {retarg_pct}% of spend. High retargeting share can indicate over-reliance on existing demand.")
    if prosp_pct < 40 and total_spend > 0:
        notes.append(f"⚠ Prospecting is only {prosp_pct}% of spend. Low prospecting limits audience growth.")
    if not notes:
        notes.append("Spend distribution looks balanced.")

    for n in notes:
        lines.append(f"*{n}*")
        lines.append("")

    lines.append("---")
    return "\n".join(lines)


def _warn_flags(t: dict) -> str:
    flags = []
    spend_chg = t.get("spend_change_pct")
    ctr_chg = t.get("ctr_change_pct")
    cpa_chg = t.get("cpa_change_pct")
    roas_chg = t.get("roas_change_pct")
    if spend_chg is not None and spend_chg > 100:
        flags.append(f"Spend spike +{spend_chg:.0f}%")
    if ctr_chg is not None and ctr_chg < -30:
        flags.append(f"CTR drop {ctr_chg:.0f}%")
    if cpa_chg is not None and cpa_chg > 40:
        flags.append(f"CPA spike +{cpa_chg:.0f}%")
    if roas_chg is not None and roas_chg < -20:
        flags.append(f"ROAS drop {roas_chg:.0f}%")
    return " | ".join(flags) if flags else "—"


def _section_campaign_performance(campaign_perf: dict) -> str:
    rows = campaign_perf.get("rows", [])
    total = campaign_perf.get("total", 0)
    shown = campaign_perf.get("shown", 0)

    lines = ["## 31. Campaign Performance (vs 7-Day Avg)", ""]

    if not rows:
        lines.append("No trend data available. Run `fetch_insights.py --historical` to enable this section.")
        lines.append("")
        lines.append("---")
        return "\n".join(lines)

    hidden = total - shown
    note = f"*Showing {shown} of {total} campaign(s) with spend in last 7 days"
    if hidden > 0:
        note += f" — {hidden} lower-spend campaign(s) hidden"
    note += ". Priority campaigns (Fix/Watch/Scale) are always included.*"
    lines.append(note)
    lines.append("")
    lines.append("| Campaign | Spend | Δ% | CPA | CPA Δ% | CTR | CTR Δ% | ROAS | ROAS Target | P/L | Warnings |")
    lines.append("|----------|-------|-----|-----|--------|-----|--------|------|-------------|-----|----------|")

    _PL_SHORT = {
        "ON_TARGET":    "✓ On Target",
        "PROFITABLE":   "✓ Profitable",
        "UNPROFITABLE": "✗ Unprofitable",
        "CRITICAL":     "✗ Critical",
        "UNKNOWN":      "—",
    }

    for t in rows:
        # Look up profitability from campaign_rows via campaign_id
        pl_status = t.get("profitability_status", "UNKNOWN")
        roas_target = t.get("roas_target_7dc")
        target_str = f"{roas_target:.2f}x" if roas_target else "—"
        pl_str = _PL_SHORT.get(pl_status, "—")
        lines.append(
            f"| {_na(t.get('campaign_name'))} "
            f"| {_fmt_money(t.get('spend_yesterday'))} "
            f"| {_fmt_change(t.get('spend_change_pct'))} "
            f"| {_fmt_money(t.get('cpa_yesterday'))} "
            f"| {_fmt_change(t.get('cpa_change_pct'))} "
            f"| {_fmt_pct(t.get('ctr_yesterday'))} "
            f"| {_fmt_change(t.get('ctr_change_pct'))} "
            f"| {_fmt_roas(t.get('roas_yesterday'))} "
            f"| {target_str} "
            f"| {pl_str} "
            f"| {_warn_flags(t)} |"
        )

    lines.append("")
    lines.append("---")
    return "\n".join(lines)


def _section_campaign_structure(rows: list) -> str:
    # Show: active campaigns, paused with spend > 0, campaigns with disapproved ads
    shown = [
        r for r in rows
        if r.get("status") == "ACTIVE"
        or (r.get("spend") or 0) > 0
        or r.get("has_disapproved_ads")
    ]
    hidden = len(rows) - len(shown)

    lines = ["## 32. Campaign Structure", ""]
    if hidden:
        lines.append(f"*Showing {len(shown)} of {len(rows)} campaigns. {hidden} paused campaigns with no spend and no issues are hidden.*")
        lines.append("")

    lines.append("| Campaign | Objective | Status | Budget Type | Daily Budget | Spend | ROAS | CPA | Notes |")
    lines.append("|----------|-----------|--------|-------------|--------------|-------|------|-----|-------|")

    for r in shown:
        budget_type = "CBO" if r.get("daily_budget") else "Ad set"
        daily_budget = _fmt_money(int(r["daily_budget"]) / 100) if r.get("daily_budget") else "N/A"
        notes = _status_flag(r)
        lines.append(
            f"| {_na(r.get('name'))} "
            f"| {_na(r.get('objective'))} "
            f"| {_na(r.get('status'))} "
            f"| {budget_type} "
            f"| {daily_budget} "
            f"| {_fmt_money(r.get('spend'))} "
            f"| {_fmt_roas(r.get('roas'))} "
            f"| {_fmt_money(r.get('cpa'))} "
            f"| {notes} |"
        )

    lines.append("")
    lines.append("---")
    return "\n".join(lines)


def _section_bidding_overview(rows: list) -> str:
    # Show only active ad sets that spent yesterday — paused and zero-spend are noise here
    shown = [
        r for r in rows
        if r.get("status") == "ACTIVE" and (r.get("spend") or 0) > 0
    ]
    hidden = len(rows) - len(shown)

    lines = ["## 33. Bidding Overview", ""]
    if hidden:
        lines.append(f"*Showing {len(shown)} of {len(rows)} ad sets. {hidden} paused or zero-spend ad sets are hidden.*")
        lines.append("")

    if not shown:
        lines.append("No active ad sets with spend in last 7 days.")
        lines.append("")
        lines.append("---")
        return "\n".join(lines)

    lines.append("| Ad Set | Campaign | Bid Strategy | Bid / Target | Spend | CPA | CTR |")
    lines.append("|--------|----------|--------------|--------------|-------|-----|-----|")

    for r in shown:
        bid_amount = _fmt_money(int(r["bid_amount"]) / 100) if r.get("bid_amount") else "N/A"
        lines.append(
            f"| {_na(r.get('name'))} "
            f"| {_na(r.get('campaign_name'))} "
            f"| {_na(r.get('bid_strategy'))} "
            f"| {bid_amount} "
            f"| {_fmt_money(r.get('spend'))} "
            f"| {_fmt_money(r.get('cpa'))} "
            f"| {_fmt_pct(r.get('ctr'))} |"
        )

    lines.append("")
    lines.append("---")
    return "\n".join(lines)


def _section_creative_health(rows: list) -> str:
    flagged = [
        r for r in rows
        if r.get("is_disapproved") or r.get("high_frequency")
    ]
    lines = ["## 23. Creative Health", ""]
    lines.append(f"*Showing {len(flagged)} flagged ads (of {len(rows)} total). Only disapproved or high-frequency ads are listed.*")
    lines.append("")

    if not flagged:
        lines.append("No disapproved or high-frequency ads detected. See Creative Actions (§15) for CTR monitoring signals.")
        lines.append("")
        lines.append("---")
        return "\n".join(lines)

    lines.append("| Ad | Ad Set | Status | Impressions | CTR | CPC | Frequency | Flags |")
    lines.append("|----|--------|--------|-------------|-----|-----|-----------|-------|")

    for r in flagged:
        flags = []
        if r.get("is_disapproved"):
            flags.append("Disapproved")
        if r.get("high_frequency"):
            flags.append(f"High frequency ({_fmt_float(r.get('frequency'))})")
        if r.get("zero_impressions_while_active"):
            flags.append("No impressions (ad set active)")

        lines.append(
            f"| {_na(r.get('name'))} "
            f"| {_na(r.get('adset_name'))} "
            f"| {_na(r.get('status'))} "
            f"| {_fmt_num(r.get('impressions'))} "
            f"| {_fmt_pct(r.get('ctr'))} "
            f"| {_fmt_money(r.get('cpc'))} "
            f"| {_fmt_float(r.get('frequency'))} "
            f"| {', '.join(flags) if flags else '—'} |"
        )

    lines.append("")
    lines.append("---")
    return "\n".join(lines)


def _detect_anomalies(data: dict) -> list:
    """
    Return a list of anomaly dicts.
    Keys: what, where, severity, why, spend_impact, critical, category.
    - critical=True: High severity AND spend_impact >= $25 (used for AT RISK scoring)
    - category: "critical" or "watchlist"
    Thresholds: campaign trends require impressions >= 1000 OR spend >= $25.
                ad-level CTR drops require window impressions >= 500 AND yesterday >= 100.
    """
    anomalies = []

    # Build spend lookup: campaign_id → spend, adset_id → campaign_id
    campaign_spend = {c["id"]: (c.get("spend") or 0) for c in data["campaign_rows"]}
    adset_to_campaign = {a["id"]: a.get("campaign_id") for a in data["adset_bidding_rows"]}

    def _add(what, where, severity, why, spend_impact=0.0):
        critical = severity == "High" and spend_impact >= 25.0
        anomalies.append({
            "what": what,
            "where": where,
            "severity": severity,
            "why": why,
            "spend_impact": spend_impact,
            "critical": critical,
            "category": "critical" if critical else "watchlist",
        })

    # --- Structural issues ---

    # Campaigns with disapproved ads
    for c in data["campaign_rows"]:
        if c.get("has_disapproved_ads"):
            _add(
                what="Campaign contains disapproved ads",
                where=c.get("name"),
                severity="High",
                why="Disapproved ads reduce available creative and limit delivery. Fix or replace the flagged creatives.",
                spend_impact=c.get("spend") or 0,
            )

    # Individual disapproved ads (only if not already captured at campaign level)
    disapproved_campaigns = {c["id"] for c in data["campaign_rows"] if c.get("has_disapproved_ads")}
    for ad in data["ad_creative_rows"]:
        if ad.get("is_disapproved"):
            adset_id = ad.get("adset_id")
            camp_id = adset_to_campaign.get(adset_id)
            if camp_id not in disapproved_campaigns:
                camp_spend = campaign_spend.get(camp_id, 0)
                _add(
                    what="Disapproved ad",
                    where=f"{ad.get('name')} (Ad Set: {ad.get('adset_name')})",
                    severity="High",
                    why="Disapproved ad may block delivery for this ad set if no other active creative exists.",
                    spend_impact=camp_spend,
                )

    # Zero impressions while ad set is active — summarised
    zero_imp_ads = [ad for ad in data["ad_creative_rows"] if ad.get("zero_impressions_while_active")]
    if zero_imp_ads:
        _add(
            what=f"{len(zero_imp_ads)} active ads had zero impressions in the last 7 days",
            where="Various ad sets",
            severity="Medium",
            why="Active but unserved ads are common in CBO accounts where budget concentrates on winners. Flag if delivery is expected across all ad sets.",
            spend_impact=0.0,
        )

    # High frequency
    for ad in data["ad_creative_rows"]:
        if ad.get("high_frequency"):
            _add(
                what=f"High frequency ({_fmt_float(ad.get('frequency'))}x)",
                where=f"{ad.get('name')} (Ad Set: {ad.get('adset_name')})",
                severity="Low",
                why="Frequency above 3.0 indicates repeated exposure — risk of creative fatigue, rising CPC, and declining CTR.",
                spend_impact=ad.get("spend") or 0,
            )

    # Paused campaigns with spend (data lag or mid-day pause)
    for c in data["campaign_rows"]:
        if c.get("status") == "PAUSED" and (c.get("spend") or 0) > 0:
            _add(
                what="Paused campaign recorded spend in the last 7 days",
                where=c.get("name"),
                severity="Low",
                why="Spend on a paused campaign usually means it was paused mid-day. Confirm spend is expected.",
                spend_impact=c.get("spend") or 0,
            )

    # Active ad sets with zero spend — summarised
    zero_spend_adsets = [
        a for a in data["adset_bidding_rows"]
        if a.get("status") == "ACTIVE" and (a.get("spend") or 0) == 0
    ]
    if zero_spend_adsets:
        _add(
            what=f"{len(zero_spend_adsets)} active ad sets had zero spend in the last 7 days",
            where="Various campaigns",
            severity="Medium",
            why="Normal in CBO campaigns where budget focuses on top performers. Investigate if these ad sets are expected to receive budget.",
            spend_impact=0.0,
        )

    # --- Trend-based anomalies (require 30d historical data) ---
    by_campaign = data.get("trends", {}).get("by_campaign", {})
    by_ad = data.get("trends", {}).get("by_ad", {})

    for t in by_campaign.values():
        name = t.get("campaign_name", "Unknown campaign")
        spend_yday = t.get("spend_yesterday") or 0
        impressions_yday = t.get("impressions_yesterday") or 0

        # Minimum signal threshold: skip low-volume campaigns to reduce noise
        if impressions_yday < 1000 and spend_yday < 25:
            continue

        spend_chg = t.get("spend_change_pct")
        ctr_chg = t.get("ctr_change_pct")
        cpc_chg = t.get("cpc_change_pct")
        cpa_chg = t.get("cpa_change_pct")

        if spend_chg is not None and spend_chg > 100:
            _add(
                what=f"Spend spike: +{spend_chg:.1f}% vs 7-day average",
                where=name,
                severity="High",
                why=f"Yesterday spend was {spend_chg:.0f}% above the 7-day daily average. Check for unintended budget or bid changes.",
                spend_impact=spend_yday,
            )

        if ctr_chg is not None and ctr_chg < -30:
            _add(
                what=f"CTR drop: {ctr_chg:.1f}% vs 7-day average",
                where=name,
                severity="Medium",
                why="Significant CTR decline. Likely causes: creative fatigue, audience saturation, or increased ad frequency.",
                spend_impact=spend_yday,
            )

        if cpc_chg is not None and cpc_chg > 30:
            _add(
                what=f"CPC spike: +{cpc_chg:.1f}% vs 7-day average",
                where=name,
                severity="Medium",
                why="CPC rose significantly — often follows a CTR drop or increased auction competition.",
                spend_impact=spend_yday,
            )

        if cpa_chg is not None and cpa_chg > 40:
            _add(
                what=f"CPA spike: +{cpa_chg:.1f}% vs 7-day average",
                where=name,
                severity="Medium",
                why="CPA rose significantly vs recent average. Review creative performance and audience overlap for signs of exhaustion.",
                spend_impact=spend_yday,
            )

    # Ad-level: severe CTR drops with impression thresholds to avoid noise
    for t in by_ad.values():
        ctr_chg = t.get("ctr_change_pct")
        impressions_yday = t.get("impressions_yesterday") or 0
        impressions_window = t.get("impressions_window") or 0
        spend_yday = t.get("spend_yesterday") or 0

        # Require sufficient volume in both windows to trust the signal
        if impressions_window < 500 or impressions_yday < 100:
            continue

        if ctr_chg is not None and ctr_chg < -50 and spend_yday > 1:
            _add(
                what=f"Severe CTR drop: {ctr_chg:.1f}% vs 7-day average",
                where=f"{t.get('ad_name')} (Ad Set: {t.get('adset_name')})",
                severity="Medium",
                why="This ad's CTR more than halved vs its 7-day average with sufficient impression volume — strong signal of creative fatigue.",
                spend_impact=spend_yday,
            )

    return anomalies


def _section_creative_intelligence(ci: dict) -> str:
    lines = ["## 24. Creative Intelligence", ""]

    # Summary cards
    top = ci.get("top_creative")
    worst = ci.get("worst_creative")
    fatigued = ci.get("fatigued", [])
    new_count = ci.get("new_creative_count", 0)
    total = ci.get("total_with_spend", 0)

    lines.append("| | |")
    lines.append("|---|---|")

    if top:
        roas_or_ctr = f"ROAS {_fmt_float(top.get('roas'))}x" if top.get("roas") else f"CTR {_fmt_pct(top.get('ctr'))}"
        lines.append(f"| **Top Creative** | {top['name'][:60]} — {roas_or_ctr}, Spend {_fmt_money(top['spend'])} |")
    else:
        lines.append("| **Top Creative** | N/A |")

    if worst and worst.get("id") != (top or {}).get("id"):
        roas_or_ctr = f"ROAS {_fmt_float(worst.get('roas'))}x" if worst.get("roas") else f"CTR {_fmt_pct(worst.get('ctr'))}"
        lines.append(f"| **Worst Creative** | {worst['name'][:60]} — {roas_or_ctr}, Spend {_fmt_money(worst['spend'])} |")
    else:
        lines.append("| **Worst Creative** | N/A |")

    lines.append(f"| **Creative Fatigue Risk** | {len(fatigued)} ad{'s' if len(fatigued) != 1 else ''} (frequency >3 or CTR drop >25%) |")
    lines.append(f"| **New Creatives Launched (7d)** | {new_count} |")
    lines.append(f"| **Ads with Spend Yesterday** | {total} |")
    lines.append("")

    # Top 10 by spend
    top_creatives = ci.get("top_creatives", [])
    if top_creatives:
        lines.append("**Top Creatives by Spend**")
        lines.append("")
        lines.append("| Ad | Spend | CTR | CTR Δ% | CPA | ROAS | Freq |")
        lines.append("|----|-------|-----|--------|-----|------|------|")
        for a in top_creatives:
            lines.append(
                f"| {a['name'][:55]} "
                f"| {_fmt_money(a['spend'])} "
                f"| {_fmt_pct(a.get('ctr'))} "
                f"| {_fmt_change(a.get('ctr_change_pct'))} "
                f"| {_fmt_money(a.get('cpa'))} "
                f"| {_fmt_roas(a.get('roas'))} "
                f"| {_fmt_float(a.get('frequency'))} |"
            )
        lines.append("")

    # Fatigued creatives
    if fatigued:
        lines.append("**Fatigued Creatives**")
        lines.append("")
        lines.append("| Ad | Ad Set | Freq | CTR Δ% | Reasons |")
        lines.append("|----|--------|------|--------|---------|")
        for a in fatigued:
            lines.append(
                f"| {a['name'][:50]} "
                f"| {_na(a.get('adset_name'))[:40]} "
                f"| {_fmt_float(a.get('frequency'))} "
                f"| {_fmt_change(a.get('ctr_change_pct'))} "
                f"| {', '.join(a.get('fatigue_reasons', []))} |"
            )
        lines.append("")

    lines.append("---")
    return "\n".join(lines)


def _section_opportunities(winners: list) -> str:
    lines = ["## 25. Performance Winners", ""]

    if not winners:
        lines.append("No positive trend signals detected today.")
        lines.append("")
        lines.append("---")
        return "\n".join(lines)

    lines.append(f"*{len(winners)} campaign(s) showing positive trend signals (ROAS improving or efficiency winning vs prior period). "
                 "These are signal-based, not verdict-based — cross-reference with Today's Priorities Scale section.*")
    lines.append("")
    for w in winners:
        lines.append(f"- **[{w['type']}]** {w['where']}  ")
        lines.append(f"  {w['metric']}  ")
        lines.append(f"  → {w['action']}")
        lines.append("")

    lines.append("---")
    return "\n".join(lines)


def _section_anomalies(anomalies: list) -> str:
    lines = ["## 26. Issues & Watchlist", ""]

    critical = [a for a in anomalies if a.get("category") == "critical"]
    watchlist = [a for a in anomalies if a.get("category") != "critical"]

    if not anomalies:
        lines.append("No anomalies detected.")
        lines.append("")
        lines.append("---")
        return "\n".join(lines)

    if critical:
        lines.append("### Critical Issues")
        lines.append("")
        for a in critical:
            spend_note = f" (${a['spend_impact']:.0f} spend affected)" if a.get("spend_impact", 0) >= 25 else ""
            lines.append(
                f"- **[{a['severity']}]** **{a['what']}**{spend_note} — {a['where']}  \n"
                f"  {a['why']}"
            )
            lines.append("")

    if watchlist:
        if critical:
            lines.append("### Watchlist")
            lines.append("")
        for a in watchlist:
            lines.append(
                f"- **[{a['severity']}]** **{a['what']}** — {a['where']}  \n"
                f"  {a['why']}"
            )
            lines.append("")

    lines.append("---")
    return "\n".join(lines)


def _derive_recommendations(priorities: dict) -> list:
    """
    Generate up to 5 entity-specific recommendations from structured priorities.
    Fix items first (highest urgency), then Watch, then Scale.
    Action text already contains metric values from build_priorities.
    """
    recs = []
    seen: set = set()

    for item in priorities.get("fix", []):
        name = item.get("entity_name", "")
        action = item.get("action", "")
        reason = item.get("reason", "")
        key = f"fix_{name}"
        if key not in seen and action:
            seen.add(key)
            recs.append({
                "action": action,
                "outcome": f"Addresses '{name}': {reason}.",
                "effort": "Medium",
            })

    for item in priorities.get("watch", []):
        name = item.get("entity_name", "")
        action = item.get("action", "")
        reason = item.get("reason", "")
        key = f"watch_{name}"
        if key not in seen and action:
            seen.add(key)
            recs.append({
                "action": action,
                "outcome": f"Early intervention on '{name}': {reason}.",
                "effort": "Low",
            })

    for item in priorities.get("scale", [])[:2]:
        name = item.get("entity_name", "")
        action = item.get("action", "")
        metrics = item.get("metrics", "")
        key = f"scale_{name}"
        if key not in seen and action:
            seen.add(key)
            recs.append({
                "action": f"{action} — '{name}'",
                "outcome": f"Capitalizes on: {metrics}.",
                "effort": "Low",
            })

    return recs[:5]


def _section_recommendations(priorities: dict) -> str:
    recs = _derive_recommendations(priorities)
    lines = ["## 18. Recommended Actions", ""]

    if not recs:
        lines.append("No specific actions required today.")
        lines.append("")
        lines.append("---")
        return "\n".join(lines)

    for i, r in enumerate(recs, 1):
        lines.append(f"{i}. **{r['action']}**  ")
        lines.append(f"   Outcome: {r['outcome']}  ")
        lines.append(f"   Effort: {r['effort']}")
        lines.append("")

    lines.append("---")
    return "\n".join(lines)


def _section_confidence_notes(data: dict) -> str:
    notes = []
    overview = data["account_overview"]
    trends = data.get("trends", {})

    if overview.get("impressions", 0) < LOW_IMPRESSIONS_THRESHOLD:
        notes.append(
            f"Low impression volume ({_fmt_num(overview.get('impressions'))}) — "
            "CTR, CPC, and CPM are statistically unreliable at this volume."
        )

    if not overview.get("spend"):
        notes.append("No spend recorded in the last 7 days. All performance metrics are zero or N/A.")

    if not overview.get("purchases"):
        notes.append(
            "No purchase conversions recorded. ROAS and CPA are unavailable. "
            "Verify the Meta Pixel or Conversions API is correctly configured and attributed."
        )
    elif overview.get("roas") is None:
        notes.append(
            "Revenue data is missing despite recorded purchases. "
            "ROAS cannot be computed — check that `action_values` is included in the insights fetch."
        )

    if not trends.get("by_campaign"):
        notes.append(
            "No 30-day historical data found. Trend comparisons, anomaly detection thresholds, "
            "and winner identification are unavailable. Run `python src/fetch_insights.py --historical` "
            "to enable these features."
        )
    else:
        campaign_trend_list = list(trends["by_campaign"].values())
        has_roas_trend = any(t.get("roas_7d_avg") is not None for t in campaign_trend_list)
        if not has_roas_trend:
            notes.append(
                "Historical ROAS trend is unavailable — the 30-day file was fetched before "
                "`action_values` was added to FIELDS. Re-run `fetch_insights.py --historical` to fix."
            )

    zero_spend_adsets = [
        a for a in data["adset_bidding_rows"]
        if a.get("status") == "ACTIVE" and (a.get("spend") or 0) == 0
    ]
    if zero_spend_adsets:
        notes.append(
            f"{len(zero_spend_adsets)} active ad set(s) had zero spend in the last 7 days. "
            "In CBO campaigns, Meta concentrates budget on top performers — this is expected. "
            "Ad sets with no spend for several consecutive days may need audience or creative review."
        )

    lines = ["## 27. Confidence Notes", ""]
    if notes:
        for n in notes:
            lines.append(f"- {n}")
    else:
        lines.append("All key metrics are available and data quality looks good.")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Top-of-report insight sections
# ---------------------------------------------------------------------------

_GENERIC_SEGMENTS = {"scale", "psp", "pdp", "cbo", "asc", "test", "scale - pdp", "psp - pdp"}


def _short_name(name: str) -> str:
    """
    Extract the last meaningful segment(s) from a dash-separated campaign name.
    Skips generic segments like Scale, PDP, CBO that don't identify the product.
    """
    parts = [p.strip() for p in name.split(" - ")]
    if len(parts) <= 2:
        return name

    # Walk from the end, skip generic-only trailing parts
    meaningful = []
    for part in reversed(parts):
        if part.lower() not in _GENERIC_SEGMENTS:
            meaningful.insert(0, part)
        if len(meaningful) >= 2:
            break

    if not meaningful:
        meaningful = parts[-2:]

    short = " - ".join(meaningful)
    return short if len(short) <= 52 else short[:49] + "..."


def _section_account_changes(changes: dict) -> str:
    if not changes.get("available"):
        return "## 5. Account Changes Since Last Report\n\n*No previous snapshot available for comparison.*\n"

    lines = ["## 5. Account Changes Since Last Report", ""]
    has_content = False

    # Campaign Changes
    camp_lines = []
    for name in changes.get("campaigns_activated", []):
        camp_lines.append(f"- **{name}** activated")
    for name in changes.get("campaigns_paused", []):
        camp_lines.append(f"- **{name}** paused")
    if camp_lines:
        lines.append("**Campaign Changes**")
        lines.append("")
        lines.extend(camp_lines)
        lines.append("")
        has_content = True

    # Ad Set Changes
    adset_lines = []
    n_act = len(changes.get("adsets_activated", []))
    n_pau = len(changes.get("adsets_paused", []))
    if n_act:
        adset_lines.append(f"- {n_act} ad set{'s' if n_act != 1 else ''} activated")
    if n_pau:
        adset_lines.append(f"- {n_pau} ad set{'s' if n_pau != 1 else ''} paused")
    if adset_lines:
        lines.append("**Ad Set Changes**")
        lines.append("")
        lines.extend(adset_lines)
        lines.append("")
        has_content = True

    # Creative Changes
    creative_lines = []
    n_new = len(changes.get("new_ads", []))
    n_pau = len(changes.get("ads_paused", []))
    if n_new:
        creative_lines.append(f"- {n_new} new creative{'s' if n_new != 1 else ''} launched")
    if n_pau:
        creative_lines.append(f"- {n_pau} creative{'s' if n_pau != 1 else ''} paused")
    if creative_lines:
        lines.append("**Creative Changes**")
        lines.append("")
        lines.extend(creative_lines)
        lines.append("")
        has_content = True

    # Performance Changes
    perf_changes = changes.get("perf_changes", [])
    if perf_changes:
        lines.append("**Performance Changes**")
        lines.append("")
        for pc in perf_changes:
            sign = "+" if pc["pct"] > 0 else ""
            lines.append(
                f"- {pc['metric']} {pc['direction']}: {pc['prev_fmt']} → {pc['curr_fmt']} ({sign}{pc['pct']:.1f}%)"
            )
        lines.append("")
        has_content = True

    if not has_content:
        lines.append("*No significant changes detected since last report.*")
        lines.append("")

    return "\n".join(lines)


def _section_executive_summary(data: dict, priorities: dict, health: dict, anomalies: list) -> str:
    overview = data["account_overview"]
    ci = data.get("creative_intelligence", {})
    momentum = data.get("account_momentum", {})

    spend = overview.get("spend") or 0
    revenue = overview.get("revenue") or 0
    roas = overview.get("roas")
    cpa = overview.get("cpa")
    purchases = overview.get("purchases") or 0

    score = health.get("score", "MIXED")
    fix_count = health.get("fix_count", 0)
    watch_count = health.get("watch_count", 0)
    scale_count = health.get("scale_count", 0)

    fix_items = priorities.get("fix", [])
    scale_items = priorities.get("scale", [])
    fatigued_count = len(ci.get("fatigued", []))

    lines = ["## 1. Executive Summary", ""]
    lines.append("**Account Summary**")
    lines.append("")

    # Performance block
    lines.append("**Performance**")
    lines.append("")
    lines.append(f"Spend {_fmt_money(spend)} → Revenue {_fmt_money(revenue)}")
    perf_parts = []
    if roas is not None:
        perf_parts.append(f"ROAS {roas:.2f}x")
    if cpa is not None:
        perf_parts.append(f"CPA ${cpa:.0f}")
    if purchases:
        perf_parts.append(f"Purchases {purchases}")
    if perf_parts:
        lines.append(" | ".join(perf_parts))
    lines.append("")

    # Day-over-day improvement note
    roas_dod = momentum.get("roas_dod_direction")
    cpa_dod = momentum.get("cpa_dod_direction")
    roas_prev_day = momentum.get("roas_prev_day")
    cpa_prev_day = momentum.get("cpa_prev_day")
    roas_latest = momentum.get("roas_latest")
    cpa_latest = momentum.get("cpa_latest")
    _dod_improving = roas_dod == "improving" or cpa_dod == "improving"
    _dod_both = roas_dod == "improving" and cpa_dod == "improving"
    if _dod_improving and momentum.get("available"):
        dod_parts = []
        if roas_dod == "improving" and roas_prev_day and roas_latest:
            dod_parts.append(f"ROAS {roas_prev_day:.2f}x → {roas_latest:.2f}x")
        if cpa_dod == "improving" and cpa_prev_day and cpa_latest:
            dod_parts.append(f"CPA ${cpa_prev_day:.0f} → ${cpa_latest:.0f}")
        qualifier = "Full recovery" if _dod_both else "Partial recovery"
        lines.append(f"**↑ Day-over-day: {qualifier}** — {' | '.join(dod_parts)}")
        lines.append("")

    # Status block
    lines.append("**Status**")
    lines.append("")
    lines.append(f"Account health: **{score}**")
    lines.append(f"Fix items: {fix_count}")
    lines.append(f"Watch items: {watch_count}")
    lines.append(f"Scale opportunities: {scale_count}")
    lines.append("")

    # Biggest Risk
    if fix_items:
        top = fix_items[0]
        short = _short_name(top.get("entity_name", ""))
        reason = top.get("reason", "")
        lines.append(f"**Biggest Risk** {short} — {reason}")
        lines.append("")

    # Best Opportunity
    if scale_items:
        top = scale_items[0]
        short = _short_name(top.get("entity_name", ""))
        metrics = top.get("metrics", "")
        lines.append(f"**Best Opportunity** {short} — {metrics}")
        lines.append("")

    # Creative Signals
    if fatigued_count > 0:
        lines.append(f"**Creative Signals** {fatigued_count} creative{'s' if fatigued_count != 1 else ''} showing fatigue")
        lines.append("")

    # Profitability breakdown (from unit economics)
    ue = data.get("unit_economics", {})
    if ue.get("available"):
        sc = ue.get("status_counts", {})
        total_campaigns = ue.get("active_campaign_count", 0)
        if total_campaigns > 0:
            _PL_ORDER = ["ON_TARGET", "PROFITABLE", "BREAKEVEN", "UNPROFITABLE", "CRITICAL", "UNKNOWN"]
            _PL_LABELS = {
                "ON_TARGET":    "On Target",
                "PROFITABLE":   "Profitable",
                "BREAKEVEN":    "Breakeven",
                "UNPROFITABLE": "Unprofitable",
                "CRITICAL":     "Critical",
                "UNKNOWN":      "Unmatched",
            }
            parts = [
                f"{_PL_LABELS[s]}: {sc.get(s, 0)}"
                for s in _PL_ORDER
                if sc.get(s, 0) > 0
            ]
            lines.append(f"**Profitability** (vs breakeven, {total_campaigns} active campaigns)")
            lines.append("")
            lines.append(" | ".join(parts))
            lines.append("")
            critical = sc.get("CRITICAL", 0)
            unprofitable = sc.get("UNPROFITABLE", 0)
            if critical > 0:
                lines.append(
                    f"⚠ {critical} campaign{'s' if critical != 1 else ''} below ROAS floor — "
                    "spending significantly below breakeven"
                )
                lines.append("")

    # Profitability flip alert (skip detail on bulk monthly updates)
    econ_changes = data.get("economics_changes", [])
    distinct_changed = {c.get("product", "") for c in econ_changes}
    if len(distinct_changed) >= _BULK_UPDATE_THRESHOLD:
        lines.append("*Unit economics updated (monthly refresh) — new thresholds active.*")
        lines.append("")
    else:
        flip_changes = [c for c in econ_changes if c.get("profitability_flip") and c.get("affected_campaigns")]
        if flip_changes:
            lines.append("**ALERT: Unit Economics Changed — Profitability Thresholds Shifted**")
            lines.append("")
            for ch in flip_changes:
                product = ch.get("product", "")
                impact = ch.get("impact_summary", "")
                affected = ch.get("affected_campaigns", [])
                camp_str = ", ".join(affected[:2])
                if len(affected) > 2:
                    camp_str += f" (+{len(affected) - 2} more)"
                lines.append(f"- **{product}**: {impact} — affects {camp_str}")
            lines.append("")

    # Today's Focus
    focus = ""
    if fix_items:
        focus = fix_items[0].get("action", "")
    elif scale_items:
        focus = scale_items[0].get("action", "")
    if focus:
        lines.append("**Today's Focus**")
        lines.append("")
        lines.append(focus)
        lines.append("")

    lines.append("---")
    return "\n".join(lines)


def _section_key_opportunities(priorities: dict, winners: list) -> str:
    scale_items = priorities.get("scale", [])
    lines = ["## 19. Key Opportunities", ""]

    if not scale_items and not winners:
        lines.append("No strong scaling signals detected today.")
        lines.append("")
        lines.append("---")
        return "\n".join(lines)

    bullets = []
    seen_names: set = set()

    for item in scale_items:
        name = item.get("entity_name", "")
        short = _short_name(name)
        metrics = item.get("metrics", "")
        action = item.get("action", "")
        seen_names.add(name)
        bullets.append(f"**{short}** — {metrics}. {action}")

    # Fill from winners if under 5, deduplicating by entity name
    for w in winners:
        name = w.get("where", "")
        if name not in seen_names and len(bullets) < 5:
            seen_names.add(name)
            short = _short_name(name)
            metric = w.get("metric", "")
            action = w.get("action", "")
            bullets.append(f"**{short}** — {metric}. {action}")

    for b in bullets[:5]:
        lines.append(f"- {b}")

    lines.append("")
    lines.append("---")
    return "\n".join(lines)


def _section_key_risks(priorities: dict, anomalies: list) -> str:
    fix_items = priorities.get("fix", [])
    lines = ["## 20. Key Risks", ""]

    bullets = []
    seen_names: set = set()

    # Fix items first — already sorted by spend impact
    for item in fix_items:
        name = item.get("entity_name", "")
        short = _short_name(name)
        reason = item.get("reason", "")
        metrics = item.get("metrics", "")
        seen_names.add(name)
        bullets.append(f"**{short}** — {reason} ({metrics})")

    # Fill from critical anomalies not already covered
    critical = sorted(
        [a for a in anomalies if a.get("critical") and a.get("where", "") not in seen_names],
        key=lambda a: a.get("spend_impact", 0),
        reverse=True,
    )
    for a in critical[: max(0, 5 - len(bullets))]:
        where = a.get("where", "")
        short = _short_name(where)
        what = a.get("what", "")
        spend = a.get("spend_impact", 0)
        spend_note = f" (${spend:.0f} spend)" if spend >= 25 else ""
        bullets.append(f"**{short}** — {what}{spend_note}")

    if not bullets:
        lines.append("No high-impact risks detected today.")
    else:
        for b in bullets[:5]:
            lines.append(f"- {b}")

    lines.append("")
    lines.append("---")
    return "\n".join(lines)


def _section_proposed_changes(proposed: dict) -> str:
    increase = proposed.get("increase", [])
    reduce = proposed.get("reduce", [])
    pause = proposed.get("pause_candidates", [])
    creative = proposed.get("creative_refresh", [])

    lines = ["## 9. Proposed Changes", ""]

    if not any([increase, reduce, pause, creative]):
        lines.append("No proposed changes today.")
        lines.append("")
        lines.append("---")
        return "\n".join(lines)

    _PL_ICONS = {
        "ON_TARGET":    "✓ On Target",
        "PROFITABLE":   "✓ Profitable",
        "UNPROFITABLE": "✗ Unprofitable",
        "CRITICAL":     "✗ Critical",
        "UNKNOWN":      "? Unknown",
    }

    def _render_bucket(items: list, label: str) -> None:
        if not items:
            return
        lines.append(f"**{label}**")
        lines.append("")
        for item in items:
            short = _short_name(item["name"])
            conf = item.get("confidence", "")
            conf_str = f" — Confidence: **{conf}**" if conf else ""
            lines.append(f"- **{short}**{conf_str}")
            lines.append(f"  Action: {item['action']}")
            lines.append(f"  Reason: {item['reason']}")

            # Profitability context (if economics matched)
            pl_status = item.get("profitability_status")
            breakeven = item.get("breakeven_roas")
            target = item.get("roas_target_7dc")
            vs_be = item.get("vs_breakeven_pct")
            product = item.get("matched_product")
            if pl_status and pl_status != "UNKNOWN":
                pl_icon = _PL_ICONS.get(pl_status, pl_status)
                econ_parts = [f"P/L: **{pl_icon}**"]
                if product:
                    econ_parts.append(f"Product: {product}")
                if breakeven is not None:
                    econ_parts.append(f"Breakeven: {breakeven:.2f}x")
                if target is not None:
                    econ_parts.append(f"Target (7DC): {target:.2f}x")
                if vs_be is not None:
                    sign = "+" if vs_be >= 0 else ""
                    econ_parts.append(f"vs breakeven: {sign}{vs_be:.1f}%")
                lines.append(f"  *{' | '.join(econ_parts)}*")
            lines.append("")

    _render_bucket(increase, "Increase budget")
    _render_bucket(reduce, "Reduce budget")
    _render_bucket(pause, "Pause candidates")
    _render_bucket(creative, "Creative refresh needed")

    lines.append("---")
    return "\n".join(lines)


def _section_budget_plan(plan: dict) -> str:
    increase = plan.get("increase", [])
    reduce = plan.get("reduce", [])
    hold = plan.get("hold", [])

    lines = ["## 5. Budget Allocation Plan", ""]

    if not increase and not reduce and not hold:
        lines.append("Insufficient trend data to generate budget recommendations.")
        lines.append("")
        lines.append("---")
        return "\n".join(lines)

    def _render_item(item: dict) -> list:
        short = _short_name(item.get("campaign_name", ""))
        reason = item.get("reason", "")
        metrics = item.get("metrics", "")
        chg = item.get("change_pct", 0)
        delta = item.get("budget_delta", 0)
        capped = item.get("budget_capped", False)

        chg_str = f"+{chg}%" if chg > 0 else f"{chg}%" if chg < 0 else "Hold"
        delta_str = f" (+${delta:.0f})" if delta > 0 else f" (-${abs(delta):.0f})" if delta < 0 else ""

        out = [f"- **{short}** — Suggested: **{chg_str}**{delta_str}"]
        if metrics:
            out.append(f"  {metrics}")
        out.append(f"  *{reason}*")
        if capped:
            out.append("  ⚠ *Budget capped — scaling opportunity constrained by daily budget*")
        return out

    if increase:
        lines.append("**↑ Increase**")
        lines.append("")
        for item in increase:
            lines.extend(_render_item(item))
            lines.append("")

    if reduce:
        lines.append("**↓ Reduce**")
        lines.append("")
        for item in reduce:
            lines.extend(_render_item(item))
            lines.append("")

    if hold:
        lines.append("**→ Hold**")
        lines.append("")
        for item in hold:
            lines.extend(_render_item(item))
            lines.append("")

    # Reallocation summary
    total_inc = plan.get("total_increase_delta", 0)
    total_red = plan.get("total_reduce_delta", 0)
    net = plan.get("net_delta", 0)
    if total_inc or total_red:
        lines.append("**Estimated Budget Reallocation**")
        lines.append("")
        lines.append("| | |")
        lines.append("|---|---|")
        lines.append(f"| Total increases | +${total_inc:.0f} |")
        lines.append(f"| Total reductions | -${abs(total_red):.0f} |")
        net_str = f"+${net:.0f}" if net >= 0 else f"-${abs(net):.0f}"
        lines.append(f"| Net change | {net_str} |")
        lines.append("")

    lines.append("---")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# New AI media buyer sections
# ---------------------------------------------------------------------------

def _section_strategy_note(note: list) -> str:
    lines = ["## 2. Strategy Note", ""]
    if not note:
        lines.append("Insufficient data for strategy note.")
        lines.append("")
        lines.append("---")
        return "\n".join(lines)
    lines.append(" ".join(note))
    lines.append("")
    lines.append("---")
    return "\n".join(lines)


def _section_decision_summary(decision: dict) -> str:
    lines = ["## 3. Decision Summary", ""]

    increase = decision.get("increase", [])
    reduce = decision.get("reduce", [])
    pause = decision.get("pause", [])
    refresh_immediately = decision.get("creative_refresh_immediately", 0)
    refresh_soon = decision.get("creative_refresh_soon", 0)
    total_inc = decision.get("total_increase_delta", 0)
    total_red = decision.get("total_reduce_delta", 0)

    def _decision_label(item: dict) -> str:
        short = _short_name(item["campaign_name"])
        bid = item.get("bid_strategy", "")
        bid_tag = f" ({bid})" if bid else ""
        return f"{short}{bid_tag}"

    if increase:
        lines.append("**Increase**")
        lines.append("")
        for item in increase:
            label = _decision_label(item)
            chg = item.get("change_pct", 0)
            delta = item.get("budget_delta", 0)
            conf = item.get("confidence", "")
            delta_str = f" (+${int(delta)})" if delta > 0 else ""
            conf_str = f" [{conf}]" if conf else ""
            lines.append(f"- **{label}** +{chg}%{delta_str}{conf_str}")
        lines.append("")

    if reduce:
        lines.append("**Reduce**")
        lines.append("")
        for item in reduce:
            label = _decision_label(item)
            chg = item.get("change_pct", 0)
            delta = item.get("budget_delta", 0)
            conf = item.get("confidence", "")
            delta_str = f" (-${abs(int(delta))})" if delta < 0 else ""
            conf_str = f" [{conf}]" if conf else ""
            lines.append(f"- **{label}** {chg}%{delta_str}{conf_str}")
        lines.append("")

    if pause:
        lines.append("**Pause Candidates**")
        lines.append("")
        for item in pause:
            short = _short_name(item["campaign_name"])
            lines.append(f"- **{short}** — {item.get('reason', '')}")
        lines.append("")

    if refresh_immediately > 0 or refresh_soon > 0:
        lines.append("**Creative Actions**")
        lines.append("")
        if refresh_immediately > 0:
            lines.append(f"- {refresh_immediately} ad{'s' if refresh_immediately != 1 else ''} need immediate refresh (>50% CTR drop)")
        if refresh_soon > 0:
            lines.append(f"- {refresh_soon} ad{'s' if refresh_soon != 1 else ''} need refresh soon (30–50% CTR drop)")
        lines.append("")

    if total_inc or total_red:
        net = total_inc + total_red
        net_str = f"+${net:,.0f}" if net >= 0 else f"-${abs(net):,.0f}"
        lines.append(f"**Estimated Budget Shift:** +${total_inc:,.0f} in / -${abs(total_red):,.0f} out / Net {net_str}")
        lines.append("")

    lines.append("---")
    return "\n".join(lines)


def _section_strategic_signals(signals: list) -> str:
    lines = ["## 11. Strategic Signals", ""]
    if not signals:
        lines.append("No strategic signals to report today.")
        lines.append("")
        lines.append("---")
        return "\n".join(lines)
    for s in signals:
        lines.append(f"- {s}")
    lines.append("")
    lines.append("---")
    return "\n".join(lines)


def _section_creative_lifecycle(lifecycle: dict) -> str:
    lines = ["## 16. Creative Lifecycle", ""]
    lines.append("| Status | Count |")
    lines.append("|--------|-------|")
    lines.append(f"| New (last 7d) | {lifecycle.get('new_count', 0)} |")
    lines.append(f"| Active (with spend) | {lifecycle.get('with_spend', 0)} |")
    lines.append(f"| Fatigued (frequency or CTR drop) | {lifecycle.get('fatigued_count', 0)} |")
    lines.append(f"| Refresh Immediately (>50% CTR drop) | {lifecycle.get('refresh_immediately', 0)} |")
    lines.append(f"| Refresh Soon (30–50% CTR drop) | {lifecycle.get('refresh_soon', 0)} |")
    lines.append(f"| Monitor (20–30% CTR drop) | {lifecycle.get('monitor', 0)} |")
    lines.append("")
    lines.append("---")
    return "\n".join(lines)


def _section_creative_concept_insights(concepts: dict) -> str:
    lines = ["## 17. Creative Concept Insights", ""]

    if not concepts.get("available"):
        lines.append("*No concept keywords found in ad names. Add labels like 'UGC', 'Avatar', 'Longform', etc. to enable this section.*")
        lines.append("")
        lines.append("---")
        return "\n".join(lines)

    winning = concepts.get("winning_concept")
    weak = concepts.get("weak_concept")

    if winning:
        top_ad = winning.get("top_ad")
        top_name = top_ad.get("name", "")[:50] if top_ad else "N/A"
        lines.append(f"**Winning concept:** {winning['concept']} — avg ROAS {winning.get('avg_roas', 'N/A')}x across {winning['ad_count']} ads")
        if top_ad:
            lines.append(f"  Best ad: {top_name}")
        lines.append("")

    if weak:
        lines.append(f"**Weak concept:** {weak['concept']} — avg ROAS {weak.get('avg_roas', 'N/A')}x across {weak['ad_count']} ads")
        lines.append("")

    all_concepts = concepts.get("concepts", [])
    if all_concepts:
        lines.append("| Concept | Ads | Avg ROAS | Total Spend |")
        lines.append("|---------|-----|----------|-------------|")
        for c in sorted(all_concepts, key=lambda x: x.get("avg_roas") or 0, reverse=True):
            roas_str = f"{c['avg_roas']:.2f}x" if c.get("avg_roas") is not None else "N/A"
            lines.append(
                f"| {c['concept']} "
                f"| {c['ad_count']} "
                f"| {roas_str} "
                f"| {_fmt_money(c.get('total_spend'))} |"
            )
        lines.append("")

    lines.append("---")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Decision-oriented section wrappers (original)
# ---------------------------------------------------------------------------

def _section_account_momentum(momentum: dict) -> str:
    lines = ["## 6. Account Momentum", ""]

    if not momentum.get("available"):
        lines.append("*No previous snapshots available for trend computation.*")
        lines.append("")
        lines.append("---")
        return "\n".join(lines)

    days = momentum.get("days", 0)
    days_label = f"({days}d)"

    lines.append(f"| Metric | Trend {days_label} | Direction |")
    lines.append("|--------|--------|-----------|")
    lines.append(f"| ROAS | {momentum.get('roas_trend', 'N/A')} | {momentum.get('roas_direction', 'flat')} |")
    lines.append(f"| CPA | {momentum.get('cpa_trend', 'N/A')} | {momentum.get('cpa_direction', 'flat')} |")
    lines.append(f"| Spend | {momentum.get('spend_trend', 'N/A')} | {momentum.get('spend_direction', 'flat')} |")
    lines.append("")
    lines.append("---")
    return "\n".join(lines)


def _section_efficiency_map(emap: dict) -> str:
    lines = ["## 12. Campaign Efficiency Map", ""]
    rows = emap.get("rows", [])

    if not rows:
        lines.append("Insufficient data to build efficiency map.")
        lines.append("")
        lines.append("---")
        return "\n".join(lines)

    lines.append("| Campaign | Spend | ROAS | CPA | Breakeven | Profit | vs Target | Verdict |")
    lines.append("|----------|-------|------|-----|-----------|--------|-----------|---------|")
    status_labels = {
        "SCALE": "SCALE ↑",
        "HOLD": "HOLD →",
        "MONITOR": "MONITOR →",
        "REDUCE": "REDUCE ↓",
        "PAUSE": "PAUSE ✗",
        "INVESTIGATE": "INVESTIGATE ?",
        "CREATIVE_REFRESH": "REFRESH !",
        "WATCH_ONLY": "WATCH —",
    }
    _pl_icons = {
        "PROFITABLE": "PROFITABLE",
        "UNPROFITABLE": "UNPROFITABLE",
        "BREAKEVEN": "BREAKEVEN",
        "UNKNOWN": "UNKNOWN",
        "ON_TARGET": "ON TARGET",
        "CRITICAL": "CRITICAL",
    }
    for r in rows:
        short = _short_name(r.get("name", ""))
        status = r.get("status", "STABLE")
        breakeven = r.get("breakeven_roas")
        pl_status = r.get("profitability_status", "UNKNOWN")
        vs_target = r.get("vs_7dc_target_pct")

        be_str = f"{breakeven:.2f}x" if breakeven else "—"
        pl_str = _pl_icons.get(pl_status, pl_status)
        vs_str = f"{vs_target:+.1f}%" if vs_target is not None else "—"

        lines.append(
            f"| {short} "
            f"| {_fmt_money(r.get('spend'))} "
            f"| {_fmt_roas(r.get('roas'))} "
            f"| {_fmt_money(r.get('cpa'))} "
            f"| {be_str} "
            f"| {pl_str} "
            f"| {vs_str} "
            f"| {status_labels.get(status, status)} |"
        )

    lines.append("")
    lines.append("*Profit = absolute profitability vs breakeven ROAS. Verdict = unified classification from classify_campaign() — same verdict used in Today's Priorities.*")
    lines.append("")
    lines.append("---")
    return "\n".join(lines)


def _section_spend_concentration(conc: dict) -> str:
    lines = ["## 13. Spend Concentration", ""]

    if not conc.get("available"):
        lines.append("No spend data available.")
        lines.append("")
        lines.append("---")
        return "\n".join(lines)

    top3 = conc.get("top_3_spend_pct", 0)
    top5 = conc.get("top_5_spend_pct", 0)
    interpretation = conc.get("interpretation", "")

    lines.append(f"- Top 3 campaigns: **{top3}%** of spend")
    lines.append(f"- Top 5 campaigns: **{top5}%** of spend")
    if interpretation:
        lines.append(f"- Concentration level: **{interpretation}**")
    lines.append("")

    if conc.get("top_3_concentrated"):
        lines.append("*Top 3 campaigns account for over 60% of budget. Review diversification risk.*")
        lines.append("")

    lines.append("---")
    return "\n".join(lines)


def _section_efficiency_leakage(leakage: dict) -> str:
    lines = ["## 14. Efficiency Leakage", ""]

    if not leakage.get("available"):
        lines.append("No underperforming campaigns detected.")
        lines.append("")
        lines.append("---")
        return "\n".join(lines)

    # Use only true at-risk spend (excludes breakeven, which is spending at cost, not losing money)
    at_risk_spend = leakage.get("at_risk_spend", leakage.get("total_wasted_spend", 0))
    at_risk_pct = leakage.get("at_risk_pct", leakage.get("pct_of_account_spend", 0))
    at_risk_campaigns = leakage.get("at_risk_campaigns", leakage.get("top_contributors", []))
    near_breakeven_campaigns = leakage.get("near_breakeven_campaigns", [])
    near_breakeven_spend = leakage.get("near_breakeven_spend", 0)

    total_profitable = leakage.get("total_profitable_spend", 0)
    profitable_pct = leakage.get("profitable_pct", 0)
    profitable_campaigns = leakage.get("profitable_campaigns", [])

    lines.append(f"**${at_risk_spend:,.0f} ({at_risk_pct}% of spend)** below breakeven.")
    lines.append("")

    if at_risk_campaigns:
        lines.append("Below-breakeven campaigns:")
        lines.append("")
        for c in at_risk_campaigns[:5]:
            short = _short_name(c.get("name", ""))
            reason = c.get("reason", "")
            spend = c.get("spend", 0)
            est = c.get("estimated_daily_profit")
            est_str = f" | Est. P/L: -${abs(est):,.0f}" if est is not None and est < 0 else ""
            lines.append(f"- **{short}** — {reason} | Spend ${spend:,.0f}{est_str}")
        lines.append("")

    if near_breakeven_campaigns and near_breakeven_spend > 0:
        nb_pct = leakage.get("near_breakeven_pct", 0)
        lines.append(f"**${near_breakeven_spend:,.0f} ({nb_pct}% of spend)** near breakeven — spending at cost, not a loss.")
        lines.append("")
        for c in near_breakeven_campaigns[:3]:
            short = _short_name(c.get("name", ""))
            spend = c.get("spend", 0)
            reason = c.get("reason", "")
            lines.append(f"- **{short}** — {reason} | Spend ${spend:,.0f}")
        lines.append("")

    if profitable_campaigns and total_profitable > 0:
        lines.append(f"**${total_profitable:,.0f} ({profitable_pct}% of spend)** allocated to profitable campaigns.")
        lines.append("")
        for c in profitable_campaigns[:3]:
            short = _short_name(c.get("name", ""))
            spend = c.get("spend", 0)
            lines.append(f"- **{short}** | Spend ${spend:,.0f}")
        lines.append("")

    lines.append("---")
    return "\n".join(lines)


def _section_creative_actions(ci: dict) -> str:
    lines = ["## 15. Creative Actions", ""]

    creative_actions = ci.get("creative_actions", {})
    immediately = creative_actions.get("refresh_immediately", [])
    soon = creative_actions.get("refresh_soon", [])
    monitor = creative_actions.get("monitor", [])

    if not immediately and not soon and not monitor:
        lines.append("No creative fatigue signals detected today.")
        lines.append("")
        lines.append("---")
        return "\n".join(lines)

    def _render_group(items: list, label: str, description: str) -> None:
        if not items:
            return
        lines.append(f"**{label}**")
        lines.append(f"*{description}*")
        lines.append("")
        for a in items:
            name = a.get("name", "")[:60]
            ctr_chg = a.get("ctr_change_pct")
            lines.append(f"- {name}")
            if ctr_chg is not None:
                lines.append(f"  CTR {ctr_chg:+.0f}% vs 7-day avg")
        lines.append("")

    _render_group(immediately, "Refresh Immediately", "CTR drop > 50%")
    _render_group(soon, "Refresh Soon", "CTR drop 30–50%")
    _render_group(monitor, "Monitor", "CTR drop 20–30%")

    lines.append("---")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Meta automation status section
# ---------------------------------------------------------------------------

_META_HANDLES = [
    "Rotating away from low-performing creatives within ad sets",
    "Shifting CBO budget toward better-performing ad sets",
    "Bid micro-adjustments within your chosen strategy (tROAS / Lowest Cost)",
    "Audience optimization and delivery scheduling",
    "Learning phase improvements (first ~50 events per ad set)",
]

_META_CANNOT = [
    "Increase a campaign's daily budget — that's a manual decision",
    "Add new creative — when all ads are fatigued, there's nothing left to rotate",
    "Pause a campaign that's been underperforming for 3+ days (it reacts slowly)",
    "Resolve audience overlap between campaigns",
    "Override your business strategy with its own efficiency logic",
]


def _section_meta_automation(status: dict) -> str:
    lines = ["## 4. Meta Automation Status", ""]

    if not status.get("available"):
        lines.append("*No 30-day insight data available. Run `fetch_insights.py --historical` to enable.*")
        lines.append("")
        lines.append("---")
        return "\n".join(lines)

    auto = status.get("automation_active", {})
    score = status.get("score", "UNKNOWN")
    score_icons = {"ACTION NEEDED": "🔴", "REVIEW NEEDED": "🟡", "ON TRACK": "🟢"}
    score_icon = score_icons.get(score, "⚪")

    # What Meta is doing
    lines.append("**What Meta is handling automatically**")
    lines.append("")
    supp = "ACTIVE" if auto.get("creative_suppression") else "not detected"
    cbo_n = auto.get("cbo_campaign_count", 0)
    budg = f"ACTIVE ({cbo_n} CBO campaigns)" if auto.get("budget_optimization") else "none (no CBO)"
    lines.append(f"- Creative suppression / rotation: **{supp}**")
    lines.append(f"- Budget optimization (CBO): **{budg}**")
    lines.append(f"- Bid optimization & delivery: **ACTIVE**")
    lines.append("")

    for item in _META_HANDLES:
        lines.append(f"  ✓ {item}")
    lines.append("")

    # Overall score
    h = status.get("high_count", 0)
    m = status.get("medium_count", 0)
    lines.append(f"**Automation health: {score_icon} {score}**")
    if h or m:
        lines.append(f"*{h} high-priority and {m} medium-priority interventions require human action.*")
    else:
        lines.append("*Meta automation is handling current conditions. No manual intervention required.*")
    lines.append("")

    # Interventions
    interventions = status.get("interventions", [])
    if interventions:
        lines.append("**Manual intervention needed**")
        lines.append("")

        high_items = [i for i in interventions if i["severity"] == "high"]
        med_items = [i for i in interventions if i["severity"] == "medium"]

        if high_items:
            lines.append("*High priority — act today:*")
            lines.append("")
            for item in high_items:
                label = item.get("campaign_name") or item.get("adset_name", "")
                short = _short_name(label)
                itype = {
                    "budget_capped_winner": "Budget cap",
                    "persistent_underperformer": "Persistent underperformer",
                }.get(item["type"], item["type"].replace("_", " ").title())
                lines.append(f"- **{short}** ({itype})")
                lines.append(f"  {item['reason']}")
                if item.get("detail"):
                    lines.append(f"  *{item['detail']}*")
            lines.append("")

        if med_items:
            lines.append("*Medium priority — review this week:*")
            lines.append("")
            for item in med_items:
                label = item.get("campaign_name") or item.get("adset_name", "")
                short = _short_name(label)
                itype = {
                    "creative_fatigue_adset": "Creative fatigue (entire ad set)",
                    "scaling_slowly": "Scaling slowly",
                }.get(item["type"], item["type"].replace("_", " ").title())
                lines.append(f"- **{short}** ({itype})")
                lines.append(f"  {item['reason']}")
                if item.get("detail"):
                    lines.append(f"  *{item['detail']}*")
            lines.append("")

    # What Meta cannot fix
    lines.append("**What Meta cannot do for you**")
    lines.append("")
    for item in _META_CANNOT:
        lines.append(f"  ✗ {item}")
    lines.append("")

    lines.append("---")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# URL health section
# ---------------------------------------------------------------------------

def _section_url_health(url_health: dict) -> str:
    lines = ["## 28. Landing Page / URL Health", ""]

    if not url_health.get("available"):
        lines.append(
            "*No URL health data available. "
            "Run `python src/check_landing_pages.py` to generate this section.*"
        )
        lines.append("")
        return "\n".join(lines)

    if url_health.get("stale"):
        lines.append(f"> ⚠ **Stale data** — URL health was last checked on {url_health.get('stale_date')}. Results below may not reflect current ad status. Run `check_landing_pages.py` to refresh.")
        lines.append("")

    checked_at = url_health.get("checked_at", "")
    if checked_at:
        try:
            dt = checked_at[:19].replace("T", " ")
            lines.append(f"*Checked at: {dt} UTC*")
            lines.append("")
        except Exception:
            pass

    total = url_health.get("total_checked", 0)
    broken = url_health.get("broken_count", 0)
    slow = url_health.get("slow_count", 0)
    oos = url_health.get("oos_count", 0)

    lines.append("| Metric | Count |")
    lines.append("|--------|-------|")
    lines.append(f"| URLs checked | {total} |")
    broken_flag = " ⚠" if broken > 0 else ""
    lines.append(f"| Broken (4xx/5xx/loop) | {broken}{broken_flag} |")
    slow_flag = " ⚠" if slow > 0 else ""
    lines.append(f"| Slow (>{_SLOW_THRESHOLD_DISPLAY}s) | {slow}{slow_flag} |")
    oos_flag = " ⚠" if oos > 0 else ""
    lines.append(f"| Out of stock / sold out | {oos}{oos_flag} |")
    lines.append("")

    top_issues = url_health.get("top_issues", [])
    if top_issues:
        lines.append("**Top Priority Issues**")
        lines.append("")
        _SEV_LABELS = {"high": "HIGH", "medium": "MED", "low": "LOW"}
        for issue in top_issues:
            url = issue.get("url", "")
            short_url = url[:65] + "..." if len(url) > 65 else url
            sev = _SEV_LABELS.get(issue.get("severity", ""), "")
            spend = issue.get("total_spend") or 0
            ad_count = issue.get("ad_count", 0)
            issue_list = issue.get("issues", [])
            issue_str = " | ".join(issue_list[:3]) if issue_list else "Unknown issue"

            spend_note = f" — ${spend:,.0f} spend at risk across {ad_count} ad{'s' if ad_count != 1 else ''}" if spend > 0 else ""
            lines.append(f"- **[{sev}]** `{short_url}`{spend_note}")
            lines.append(f"  {issue_str}")

            # Show ad attribution for broken URLs
            if issue.get("is_broken") or issue.get("is_redirect_loop"):
                for ad in (issue.get("ads") or [])[:2]:
                    ad_id = ad.get("ad_id", "")
                    ad_name = (ad.get("ad_name") or "")[:40]
                    asn = (ad.get("adset_name") or ad.get("adset_id") or "")[:35]
                    cname = _short_name(ad.get("campaign_name") or "")
                    sp_yday = ad.get("spend_yesterday", 0)
                    sp_7d = ad.get("spend_7d", 0)
                    lines.append(
                        f"  → Ad `{ad_id}` {ad_name} | AdSet: {asn} | Campaign: {cname}"
                        f" | ${ sp_yday:.0f} yday / ${sp_7d:.0f} 7d"
                    )
        lines.append("")

    lines.append("---")
    return "\n".join(lines)


_SLOW_THRESHOLD_DISPLAY = 3.0


# ---------------------------------------------------------------------------
# Unit economics section
# ---------------------------------------------------------------------------

_PL_STATUS_LABEL = {
    "ON_TARGET":    "✓ On Target",
    "PROFITABLE":   "✓ Profitable",
    "UNPROFITABLE": "✗ Unprofitable",
    "CRITICAL":     "✗✗ Critical",
    "UNKNOWN":      "— Unknown",
}


def _section_unit_economics(ue: dict) -> str:
    lines = ["## 30. Unit Economics", ""]

    if not ue.get("available"):
        lines.append(
            "*Unit economics not available. "
            "Add `credentials/sheets_service_account.json` and set "
            "`UNIT_ECONOMICS_SHEET_ID` to enable this section.*"
        )
        lines.append("")
        lines.append("---")
        return "\n".join(lines)

    economics = ue.get("economics", {})
    changes = ue.get("changes", [])
    status_counts = ue.get("status_counts", {})
    product_spend = ue.get("product_spend", {})

    # --- Economics changes since last run ---
    if changes:
        lines.append(f"**⚠ {len(changes)} Economics Change{'s' if len(changes) != 1 else ''} Since Last Run**")
        lines.append("")
        for ch in changes:
            product = ch.get("product", "")
            impact = ch.get("impact_summary", "")
            flip = ch.get("profitability_flip", False)
            flip_flag = " ⚠ Profitability affected" if flip else ""
            lines.append(f"- **{product}**: {impact}{flip_flag}")
            affected = ch.get("affected_campaigns", [])
            if affected:
                for cname in affected[:3]:
                    lines.append(f"  → {cname[:70]}")
        lines.append("")

    # --- Per-product economics table ---
    lines.append("**Product Economics Reference**")
    lines.append("")
    lines.append("| Product | AOV | COGS | Margin | Breakeven ROAS | Floor (−20%) | Target 7DC | CPA Target 7DC |")
    lines.append("|---------|-----|------|--------|----------------|--------------|------------|----------------|")

    for tab_name, econ in sorted(economics.items(), key=lambda x: _clean_product_name(x[0])):
        aov = econ.get("aov")
        cogs = econ.get("cogs_per_unit")
        margin = econ.get("margin_pct")
        breakeven = econ.get("breakeven_roas")
        floor_roas = econ.get("roas_floor")
        target_7dc = econ.get("roas_target_7dc")
        cpa_7dc = econ.get("cpa_target_7dc")

        aov_str = f"${aov:,.2f}" if aov else "—"
        cogs_str = f"${cogs:,.2f}" if cogs else "—"
        margin_str = f"{margin*100:.1f}%" if margin else "—"
        be_str = f"{breakeven:.2f}x" if breakeven else "—"
        floor_str = f"{floor_roas:.2f}x" if floor_roas else "—"
        t7_str = f"{target_7dc:.2f}x" if target_7dc else "—"
        cpa_str = f"${cpa_7dc:.2f}" if cpa_7dc else "—"

        display_name = _clean_product_name(tab_name)
        lines.append(
            f"| {display_name} | {aov_str} | {cogs_str} | {margin_str} "
            f"| {be_str} | {floor_str} | {t7_str} | {cpa_str} |"
        )

    lines.append("")

    # --- Profitability breakdown by product ---
    if product_spend:
        lines.append("**Active Campaign Profitability by Product**")
        lines.append("")
        lines.append("| Product | Spend | Campaigns | On Target | Profitable | Unprofitable | Critical | Unknown |")
        lines.append("|---------|-------|-----------|-----------|------------|--------------|----------|---------|")

        for prod, info in sorted(product_spend.items(), key=lambda x: -x[1]["spend"]):
            spend = info["spend"]
            camps = info["campaigns"]
            sts = info["statuses"]
            prod_display = _clean_product_name(prod)
            lines.append(
                f"| {prod_display} "
                f"| ${spend:,.0f} "
                f"| {camps} "
                f"| {sts.get('ON_TARGET', 0)} "
                f"| {sts.get('PROFITABLE', 0)} "
                f"| {sts.get('UNPROFITABLE', 0)} "
                f"| {sts.get('CRITICAL', 0)} "
                f"| {sts.get('UNKNOWN', 0)} |"
            )
        lines.append("")

    lines.append("---")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Automation action schema section
# ---------------------------------------------------------------------------

def _section_automation_actions(actions_data: dict) -> str:
    lines = ["## 29. Automation Action Schema", ""]

    if not actions_data.get("available"):
        lines.append("*No automation actions generated.*")
        lines.append("")
        return "\n".join(lines)

    actions = actions_data.get("actions", [])
    saved_to = actions_data.get("saved_to", "")
    total = actions_data.get("action_count", 0)

    lines.append(f"*{total} actions generated. Full schema saved to `{saved_to}`.*")
    lines.append("")

    high = [a for a in actions if a.get("priority") == "high"]
    medium = [a for a in actions if a.get("priority") == "medium"]

    _ACTION_LABELS = {
        "adjust_budget": "Budget",
        "pause_ad": "Pause Ad",
        "pause_or_reduce": "Pause/Reduce",
        "fix_url": "Fix URL",
    }

    def _render_action_group(items: list, label: str) -> None:
        if not items:
            return
        lines.append(f"**{label}**")
        lines.append("")
        for a in items:
            atype = _ACTION_LABELS.get(a.get("action_type", ""), a.get("action_type", ""))
            eid = a.get("entity_id") or "—"
            ename = (a.get("entity_name") or "")[:45]
            conf = a.get("confidence", "")
            trigger = (a.get("trigger") or "")[:80]
            curr = a.get("current_value")
            rec = a.get("recommended_value")
            budget_str = ""
            if curr is not None and rec is not None:
                try:
                    budget_str = f" | ${float(curr):,.0f} → ${float(rec):,.0f}"
                except (ValueError, TypeError):
                    budget_str = f" | {curr} → {rec}"
            elif a.get("change_pct") is not None:
                budget_str = f" | {a['change_pct']:+.0f}%"
            lines.append(f"- **[{atype}]** `{eid}` {ename}{budget_str} [{conf}]")
            lines.append(f"  {trigger}")

            # Hierarchy context line
            ctx_parts = []
            entity_type = a.get("entity_type", "")
            action_type = a.get("action_type", "")

            if action_type == "adjust_budget":
                budget_level = a.get("budget_level", "")
                if budget_level:
                    ctx_parts.append(f"Budget level: {budget_level}")
                cid = a.get("campaign_id")
                if cid and entity_type == "campaign":
                    ctx_parts.append(f"Campaign ID: `{cid}` (entity = campaign budget)")
                elif cid:
                    ctx_parts.append(f"Campaign ID: `{cid}`")
                asid = a.get("adset_id")
                if asid:
                    ctx_parts.append(f"AdSet ID: `{asid}`")
            elif action_type in ("pause_ad", "pause_or_reduce"):
                asid = a.get("adset_id")
                cid = a.get("campaign_id")
                if asid:
                    ctx_parts.append(f"AdSet ID: `{asid}`")
                if cid:
                    ctx_parts.append(f"Campaign ID: `{cid}`")
            elif action_type == "fix_url":
                asid = a.get("adset_id")
                cid = a.get("campaign_id")
                if asid:
                    ctx_parts.append(f"AdSet ID: `{asid}`")
                if cid:
                    ctx_parts.append(f"Campaign ID: `{cid}`")

            if ctx_parts:
                lines.append(f"  *{' | '.join(ctx_parts)}*")

            # Economics context line
            be = a.get("breakeven_roas")
            rf = a.get("roas_floor")
            pl = a.get("profitability_status")
            est = a.get("estimated_daily_profit")
            auto_safe = a.get("automation_safe")
            auto_reason = a.get("automation_safe_reason")

            econ_parts = []
            if be is not None:
                econ_parts.append(f"Breakeven {be:.2f}x")
            if rf is not None:
                econ_parts.append(f"Floor {rf:.2f}x")
            if pl:
                econ_parts.append(f"P/L: {pl}")
            if est is not None:
                sign = "+" if est >= 0 else ""
                econ_parts.append(f"Est. daily: {sign}${est:,.0f}")
            if econ_parts:
                lines.append(f"  *Economics: {' | '.join(econ_parts)}*")

            if auto_safe is not None:
                auto_str = "YES" if auto_safe else "NO"
                reason_str = f" — {auto_reason}" if auto_reason else ""
                lines.append(f"  *Automation: {auto_str}{reason_str}*")

        lines.append("")

    _render_action_group(high, "High Priority")
    _render_action_group(medium, "Medium Priority")

    lines.append("---")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_report(data: dict, report_date: date, mode: str = "7d") -> str:
    anomalies = _detect_anomalies(data)
    winners = data.get("winners", [])
    priorities = data.get("priorities", {"scale": [], "watch": [], "fix": []})
    health = compute_health_score(priorities)

    sections = [
        _section_metadata(report_date, mode),
        _section_economics_warning(data.get("economics_warning") or ""),              # banner
        _section_executive_summary(data, priorities, health, anomalies),              # §1
        _section_economics_changes(data.get("economics_changes", [])),               # §1b
        _section_strategy_note(data.get("strategy_note", [])),                       # §2
        _section_decision_summary(data.get("decision_summary", {})),                 # §3
        _section_meta_automation(data.get("meta_automation_status", {})),            # §4
        _section_account_changes(data.get("account_changes", {})),                   # §5
        _section_account_momentum(data.get("account_momentum", {})),                 # §6
        _section_account_overview(data["account_overview"], data.get("account_comparison")),  # §7
        _section_priorities(priorities),                                               # §8
        _section_proposed_changes(data.get("proposed_changes", {})),                 # §9
        _section_health_score(health),                                                # §10
        _section_strategic_signals(data.get("strategic_signals", [])),               # §11
        _section_efficiency_map(data.get("efficiency_map", {})),                     # §12
        _section_spend_concentration(data.get("spend_concentration", {})),           # §13
        _section_efficiency_leakage(data.get("efficiency_leakage", {})),             # §14
        _section_creative_actions(data.get("creative_intelligence", {})),            # §15
        _section_creative_lifecycle(data.get("creative_lifecycle", {})),             # §16
        _section_creative_concept_insights(data.get("creative_concept_insights", {})),  # §17
        _section_recommendations(priorities),                                          # §18
        _section_key_opportunities(priorities, winners),                               # §19
        _section_key_risks(priorities, anomalies),                                    # §20
        _section_strategy_snapshot(data["strategy_snapshot"], data["campaign_rows"]),  # §21
        _section_campaign_intelligence(data.get("campaign_intelligence", {})),         # §22
        _section_creative_health(data["ad_creative_rows"]),                           # §23
        _section_creative_intelligence(data.get("creative_intelligence", {})),        # §24
        _section_opportunities(winners),                                               # §25
        _section_anomalies(anomalies),                                                # §26
        _section_confidence_notes(data),                                              # §27
        _section_url_health(data.get("url_health", {})),                              # §28
        _section_automation_actions(data.get("automation_actions", {})),              # §29
        _section_unit_economics(data.get("unit_economics", {})),                      # §30
        _section_campaign_performance(data.get("campaign_performance", {})),           # §31
        _section_campaign_structure(data["campaign_rows"]),                            # §32
        _section_bidding_overview(data["adset_bidding_rows"]),                        # §33
    ]
    return "\n".join(sections)


def main():
    import argparse
    from datetime import timedelta

    parser = argparse.ArgumentParser(description="Generate Meta Ads report.")
    parser.add_argument(
        "--daily",
        action="store_true",
        help="Generate daily report (yesterday metrics, trend-based signals vs 7-day avg).",
    )
    args = parser.parse_args()

    mode = "daily" if args.daily else "7d"
    report_date = date.today() - timedelta(days=1)

    print(f"Generating {mode} report for {report_date}...\n")

    data = build_report_data(mode=mode)

    # Pre-render validation
    _period_days = 1 if mode == "daily" else 7
    _validation = validate_report_data(data, _period_days)
    if _validation["passed"]:
        print("[VALIDATE] OK — all checks passed")
    else:
        print(f"[VALIDATE] WARN — failed checks: {_validation['failed_checks']}")
    for _k, _v in _validation["details"].items():
        print(f"  [{_k}] {_v['detail']}")

    report_md = build_report(data, report_date, mode=mode)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "-daily" if mode == "daily" else ""
    out_path = OUTPUT_DIR / f"{report_date}{suffix}.md"
    out_path.write_text(report_md)

    print(f"\n[OK] Report written to {out_path}")


if __name__ == "__main__":
    main()
