"""
report_data.py
Loads the latest raw JSON files and builds report-ready data structures.
No API calls. No mutation of raw files. No markdown rendering.
"""

import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

RAW_DIR = Path("data/raw")
URL_HEALTH_DIR = Path("outputs/url_health")

# ---------------------------------------------------------------------------
# Unit economics config
# ---------------------------------------------------------------------------

UNIT_ECONOMICS_SHEET_ID = os.getenv("UNIT_ECONOMICS_SHEET_ID", "")
SHEETS_CREDENTIALS_PATH = "credentials/sheets_service_account.json"
ECONOMICS_SNAPSHOT_DIR = "outputs/economics_snapshots"


# ---------------------------------------------------------------------------
# Reporting thresholds — single config block.
# Edit values here; do not scatter magic numbers across the codebase.
# ---------------------------------------------------------------------------
THRESHOLDS: dict = {
    # ── Volume / confidence gates ────────────────────────────────────────
    "confidence_high_spend":       500.0,   # 7d spend for HIGH confidence
    "confidence_high_purchases":   15,      # 7d purchases for HIGH confidence
    "confidence_medium_spend":     150.0,   # 7d spend for MEDIUM confidence
    "confidence_medium_purchases": 5,       # 7d purchases for MEDIUM confidence
    "confidence_low_spend":        50.0,    # 7d spend for LOW confidence
    "confidence_low_purchases":    2,       # 7d purchases for LOW confidence

    # ── Profitability — ROAS is the primary anchor ───────────────────────
    # PROFITABLE  : ROAS >= breakeven × profitable_margin
    # BREAKEVEN   : ROAS within ±breakeven_band of breakeven
    # UNPROFITABLE: ROAS < breakeven × (1 - breakeven_band)
    # CRITICAL    : ROAS < breakeven × critical_floor
    "profitable_margin":  1.10,   # ROAS >= breakeven × 1.10 = PROFITABLE
    "breakeven_band":     0.10,   # ±10% of breakeven = BREAKEVEN band
    "critical_floor":     0.70,   # ROAS < breakeven × 0.70 = CRITICAL

    # CPA is a supporting signal; only overrides ROAS when ROAS confirms.
    # A campaign is NOT healthy from CPA alone if ROAS < breakeven.
    "cpa_target_overage_pct": 1.20,   # CPA > target × 1.20 triggers alert

    # ── Trend thresholds (7d vs prior 7d, % change) ──────────────────────
    "trend_improving_roas": 15.0,   # ROAS ▲ >= 15% → IMPROVING
    "trend_worsening_roas": 15.0,   # ROAS ▼ >= 15% → WORSENING (as absolute)
    "trend_improving_cpa":  15.0,   # CPA ▼ >= 15% → IMPROVING (as absolute)
    "trend_worsening_cpa":  15.0,   # CPA ▲ >= 15% → WORSENING (as absolute)

    # ── FIX / WATCH / SCALE signals (% change vs prior period) ──────────
    "fix_cpa_spike":    40.0,   # CPA ▲ >= 40% → FIX signal
    "fix_roas_drop":    30.0,   # ROAS ▼ >= 30% → FIX signal (use as absolute)
    "fix_ctr_drop":     30.0,   # CTR ▼ >= 30% → FIX signal (use as absolute)
    "fix_spend_spike":  100.0,  # Spend ▲ >= 100% + efficiency worsening → FIX
    "watch_cpa_spike":  20.0,   # CPA ▲ 20–40% → WATCH
    "watch_ctr_drop":   20.0,   # CTR ▼ 20–30% → WATCH (use as absolute)
    "watch_spend_rise": 50.0,   # Spend ▲ >= 50% (neutral efficiency) → WATCH
    "scale_roas_lift":  30.0,   # ROAS ▲ >= 30% → SCALE signal
    "scale_cpa_ratio":  25.0,   # CPA ▼ >= 25% → SCALE signal (use as absolute)
    "scale_ctr_lift":   25.0,   # CTR ▲ >= 25% → SCALE signal
    "scale_cpc_stable": 10.0,   # CPC change within ±10% = "stable"

    # ── Minimum spend / volume for signals (daily equivalents) ──────────
    "fix_min_spend_daily":    25.0,  # minimum daily spend for campaign FIX
    "fix_min_imp":          1000,    # minimum impressions for campaign FIX
    "watch_min_spend_daily":  25.0,  # minimum daily spend for WATCH
    "watch_min_imp":        1000,
    "scale_min_spend_daily":  50.0,  # minimum daily spend for SCALE
    "fix_ad_spend_min_daily":  5.0,  # minimum ad spend for ad-level FIX
    "fix_zero_hist_min":       5.0,  # adset 7d avg to flag zero-spend

    # ── SCALE eligibility extras ─────────────────────────────────────────
    "scale_min_purchases":  2,     # minimum purchases to trust ROAS/CPA for SCALE
    "scale_max_freq":       3.5,   # max campaign frequency for SCALE eligibility
    "scale_budget_pct":     20,    # default budget increase % for strong SCALE
    "scale_budget_pct_mild": 10,   # default budget increase % for mild SCALE

    # ── FIX / frequency thresholds ───────────────────────────────────────
    "fix_freq":      3.0,   # campaign-level frequency → FIX
    "watch_freq_lo": 2.0,   # frequency WATCH lower bound

    # ── Creative fatigue — require ALL THREE core signals ────────────────
    # Core: freq saturation + engagement decline + efficiency deterioration
    "fatigue_freq":           2.5,    # frequency saturation threshold
    "fatigue_ctr_drop":       25.0,   # CTR ▼ >= 25% (use as absolute)
    "fatigue_cpa_rise":       15.0,   # CPA ▲ >= 15% = efficiency deterioration
    "fatigue_roas_drop":      15.0,   # ROAS ▼ >= 15% = efficiency deterioration (alt)
    "fatigue_min_spend":      50.0,   # minimum spend for creative classification
    "fatigue_material_spend": 200.0,  # material spend → upgrade to REFRESH_NOW
    # REFRESH_NOW upgrade thresholds (above REFRESH_SOON)
    "fatigue_now_freq":       3.5,    # freq >= 3.5 → REFRESH_NOW
    "fatigue_now_ctr_drop":   35.0,   # CTR ▼ >= 35% (absolute)
    "fatigue_now_cpa_rise":   25.0,   # CPA ▲ >= 25%

    # ── Budget plan ──────────────────────────────────────────────────────
    "reduce_min_spend":         75.0,   # minimum spend for REDUCE recommendation
    "reduce_strong_cpa":        60.0,   # CPA ▲ >= 60% → -20% reduction
    "reduce_mild_cpa":          40.0,   # CPA ▲ 40–60% → -15% reduction
    "reduce_strong_roas":       40.0,   # ROAS ▼ >= 40% → -20% reduction (absolute)
    "reduce_mild_roas":         25.0,   # ROAS ▼ 25–40% → -15% reduction (absolute)
    "reduce_budget_pct_strong": 20,     # budget decrease % for strong REDUCE
    "reduce_budget_pct_mild":   15,     # budget decrease % for mild REDUCE
    "budget_min_purchases":     3,      # min purchases for INCREASE confidence
    "budget_min_spend":         150.0,  # min spend for INCREASE confidence (alt)
    "budget_min_clicks":        200,    # min clicks for INCREASE confidence (alt)
}


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _latest(prefix: str) -> Optional[Path]:
    """Return the most recently dated raw file matching {prefix}_YYYY-MM-DD.json exactly."""
    files = sorted(RAW_DIR.glob(f"{prefix}_????-??-??.json"), reverse=True)
    return files[0] if files else None


def _load(prefix: str) -> list:
    path = _latest(prefix)
    if path is None:
        print(f"[WARN] No {prefix} file found in {RAW_DIR}. Section will be empty.")
        return []
    print(f"  Loaded {path.name}")
    return json.loads(path.read_text())


def _prev_latest(prefix: str) -> Optional[Path]:
    """Return the second-most-recently dated raw file matching {prefix}_YYYY-MM-DD.json."""
    files = sorted(RAW_DIR.glob(f"{prefix}_????-??-??.json"), reverse=True)
    return files[1] if len(files) >= 2 else None


def _load_prev(prefix: str) -> list:
    path = _prev_latest(prefix)
    if path is None:
        return []
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _action_value(actions: list, action_type: str) -> float:
    """Extract the numeric value for a specific action_type from an actions list."""
    for a in (actions or []):
        if a.get("action_type") == action_type:
            return _float(a.get("value"))
    return 0.0


def _sum_action_values(rows: list, action_type: str, field: str = "actions") -> float:
    """Sum a specific action_type value across a list of insight rows."""
    return sum(_action_value(r.get(field, []), action_type) for r in rows)


def _weighted_frequency(rows: list) -> Optional[float]:
    """Impression-weighted average frequency across a list of insight rows."""
    total_impressions = sum(_float(r.get("impressions")) for r in rows)
    if not total_impressions:
        return None
    weighted = sum(_float(r.get("frequency")) * _float(r.get("impressions")) for r in rows)
    return round(weighted / total_impressions, 2)


def _derived(spend: float, impressions: float, clicks: float) -> dict:
    """Compute CTR, CPC, CPM from totals. Returns None for undefined metrics."""
    return {
        "ctr": round(clicks / impressions * 100, 2) if impressions else None,
        "cpc": round(spend / clicks, 2) if clicks else None,
        "cpm": round(spend / impressions * 1000, 2) if impressions else None,
    }


def _sum_insights(rows: list) -> dict:
    """Aggregate a list of insight rows into spend/impressions/clicks totals + derived metrics."""
    spend = sum(_float(r.get("spend")) for r in rows)
    impressions = sum(_float(r.get("impressions")) for r in rows)
    clicks = sum(_float(r.get("clicks")) for r in rows)
    return {
        "spend": round(spend, 2),
        "impressions": int(impressions),
        "clicks": int(clicks),
        **_derived(spend, impressions, clicks),
    }


def _pct_change(current, baseline) -> Optional[float]:
    if baseline and baseline > 0 and current is not None:
        return round((current - baseline) / baseline * 100, 1)
    return None


def _confidence_level(spend: float, purchases: int, strong_delta: bool = False) -> str:
    """Return HIGH / MEDIUM / LOW confidence based on data volume."""
    if purchases >= 5 or spend >= 150 or (strong_delta and spend >= 100):
        return "HIGH"
    if purchases >= 2 or spend >= 75:
        return "MEDIUM"
    return "LOW"


def _verdict_summary(
    verdict: str,
    econ_status: str,
    trend_dir: str,
    confidence: str,
    disqualifiers: list,
    reasons: Optional[list] = None,
) -> str:
    """One-line human-readable explanation assembled from verdict fields."""
    _econ = {
        "PROFITABLE": "profitable",
        "ON_TARGET": "on target",
        "BREAKEVEN": "near breakeven",
        "UNPROFITABLE": "below breakeven",
        "CRITICAL": "critically below breakeven",
        "UNKNOWN": "economics unclear",
    }.get(econ_status, econ_status.lower())
    _trend = {
        "IMPROVING": "trend improving",
        "WORSENING": "trend worsening",
        "STABLE": "performance stable",
        "UNKNOWN": "trend unknown",
    }.get(trend_dir, trend_dir.lower())
    _conf_note = " — low volume" if confidence in ("LOW", "INSUFFICIENT") else ""
    if verdict == "SCALE":
        return f"SCALE — {_econ}, {_trend}"
    elif verdict == "HOLD":
        return f"HOLD — {_econ}{_conf_note}"
    elif verdict == "MONITOR":
        return f"MONITOR — {_econ}, {_trend}"
    elif verdict == "REDUCE":
        return f"REDUCE — {_econ} with sufficient volume"
    elif verdict == "PAUSE":
        return f"PAUSE — {_econ}, persistent"
    elif verdict == "CREATIVE_REFRESH":
        return f"CREATIVE REFRESH — {_econ}, creative fatigue"
    elif verdict == "INVESTIGATE":
        _inv_reason = next(
            (r.replace("investigate_", "").replace("_", " ") for r in (reasons or []) if r.startswith("investigate_")),
            None,
        )
        _disq = disqualifiers[0].replace("_", " ") if disqualifiers else None
        _detail = _inv_reason or _disq or "anomaly"
        return f"INVESTIGATE — {_detail}"
    elif verdict == "WATCH_ONLY":
        return "WATCH ONLY — insufficient volume"
    return f"{verdict} — {_econ}"


def classify_campaign(
    cid: str,
    enriched: dict,
    trend: dict,
    campaign_freq: Optional[float],
    period_days: int = 7,
    creative_status: str = "HEALTHY",
    is_learning: bool = False,
) -> dict:
    """
    Unified campaign verdict engine. Returns a normalized classification object.

    Verdict vocabulary:
      SCALE           — profitable/on-target + STABLE or IMPROVING + confidence >= MEDIUM + no constraints
      HOLD            — profitable or near-breakeven, stable/improving with LOW confidence or constrained
      MONITOR         — profitable but worsening; or near-breakeven mixed; or LOW confidence below breakeven
      REDUCE          — below breakeven + MEDIUM/HIGH confidence + no conflicting improvement signal
      PAUSE           — CRITICAL + MEDIUM/HIGH confidence + persistence confirmed from prior period data
      CREATIVE_REFRESH— REFRESH_NOW creative fatigue overrides HOLD
      INVESTIGATE     — specific anomaly: missing economics / delivery constrained+efficient / conflicting signals
      WATCH_ONLY      — insufficient data or learning phase

    Reason codes (machine-friendly, structured):
      econ_{profitable|on_target|breakeven|unprofitable|critical|unknown}
      trend_{improving|worsening|stable|unknown}_{roas|cpa}:{pct}pct
      confidence_{high|medium|low|insufficient}
      scale_{improving|stable}
      monitor_worsening | monitor_low_confidence | monitor_near_breakeven
      reduce_{high|medium} | pause_critical_persistent
      investigate_{missing_economics|delivery_constraint|conflicting_signals|insufficient_confidence}
      delivery_high_frequency:{freq:.1f}x
      learning_phase | pause_persistence_unconfirmed
      creative_{refresh_now|refresh_soon|high_frequency}

    Period scope: "daily" = yesterday vs 7d avg; "7d" = last 7d vs prior 7d.
    """
    T = THRESHOLDS
    spend = trend.get("spend_yesterday", 0) or 0
    purchases = trend.get("purchases_yesterday", 0) or 0
    roas_now = trend.get("roas_yesterday")
    roas_chg = trend.get("roas_change_pct")
    cpa_chg = trend.get("cpa_change_pct")
    pl_status = enriched.get("profitability_status", "UNKNOWN")
    breakeven = enriched.get("breakeven_roas")
    period_scope = "daily" if period_days == 1 else "7d"

    reasons: list = []
    disqualifiers: list = []

    # ── Confidence ─────────────────────────────────────────────────────────
    if purchases >= T["confidence_high_purchases"] or spend >= T["confidence_high_spend"]:
        confidence = "HIGH"
    elif purchases >= T["confidence_medium_purchases"] or spend >= T["confidence_medium_spend"]:
        confidence = "MEDIUM"
    elif purchases >= T["confidence_low_purchases"] or spend >= T["confidence_low_spend"]:
        confidence = "LOW"
    else:
        confidence = "INSUFFICIENT"

    reasons.append(f"confidence_{confidence.lower()}")
    reasons.append(f"econ_{pl_status.lower()}")

    # ── Trend direction ────────────────────────────────────────────────────
    _has_prior_data = roas_chg is not None or cpa_chg is not None

    roas_improving = roas_chg is not None and roas_chg >= T["trend_improving_roas"]
    roas_worsening = roas_chg is not None and roas_chg <= -T["trend_worsening_roas"]
    cpa_improving  = cpa_chg is not None and cpa_chg <= -T["trend_improving_cpa"]
    cpa_worsening  = cpa_chg is not None and cpa_chg >= T["trend_worsening_cpa"]

    if roas_improving or cpa_improving:
        trend_dir = "IMPROVING"
        if roas_improving:
            reasons.append(f"trend_improving_roas:{roas_chg:.0f}pct")
        if cpa_improving:
            reasons.append(f"trend_improving_cpa:{cpa_chg:.0f}pct")
    elif roas_worsening or cpa_worsening:
        trend_dir = "WORSENING"
        if roas_worsening:
            reasons.append(f"trend_worsening_roas:{roas_chg:.0f}pct")
        if cpa_worsening:
            reasons.append(f"trend_worsening_cpa:{cpa_chg:.0f}pct")
    elif _has_prior_data:
        trend_dir = "STABLE"
        reasons.append("trend_stable")
    else:
        trend_dir = "UNKNOWN"
        reasons.append("trend_unknown")

    # ── Delivery constraints ───────────────────────────────────────────────
    _high_freq_constrained = campaign_freq is not None and campaign_freq > T["scale_max_freq"]
    if _high_freq_constrained:
        disqualifiers.append(f"delivery_high_frequency:{campaign_freq:.1f}x")
    if is_learning:
        disqualifiers.append("learning_phase")

    # ── Persistence check for PAUSE ────────────────────────────────────────
    # PAUSE requires evidence of persistence: prior-period data must exist AND
    # the campaign was already bad in the prior period (not improving from bad).
    # If no prior data, we cannot confirm persistence → default to REDUCE.
    _persistence_confirmed = _has_prior_data and trend_dir in ("WORSENING", "STABLE")

    # ── Verdict logic ──────────────────────────────────────────────────────

    # WATCH_ONLY: no data + no economics → pure blind spot
    if confidence == "INSUFFICIENT" and pl_status == "UNKNOWN":
        verdict = "WATCH_ONLY"
        reasons.append("insufficient_volume")
        recommended_action = "Insufficient data — observe only."
        recommended_budget_change_pct = None

    # WATCH_ONLY: learning phase — never touch budget
    elif is_learning:
        verdict = "WATCH_ONLY"
        recommended_action = "Campaign in learning phase — do not adjust budget."
        recommended_budget_change_pct = None

    # INVESTIGATE: economics missing but campaign has spend data
    elif pl_status == "UNKNOWN":
        if confidence == "INSUFFICIENT":
            verdict = "WATCH_ONLY"
            reasons.append("insufficient_volume")
            recommended_action = "No meaningful data — observe only."
        else:
            verdict = "INVESTIGATE"
            reasons.append("investigate_missing_economics")
            recommended_action = (
                "Economics not configured — set up unit economics before acting on this campaign."
            )
        recommended_budget_change_pct = None

    # PAUSE / REDUCE: CRITICAL economics
    elif pl_status == "CRITICAL":
        if confidence in ("MEDIUM", "HIGH") and _persistence_confirmed:
            verdict = "PAUSE"
            reasons.append("pause_critical_persistent")
            if roas_now and breakeven:
                recommended_action = (
                    f"Pause or reduce to minimum — ROAS {roas_now:.2f}x well below "
                    f"breakeven {breakeven:.2f}x, confirmed across multiple periods."
                )
            else:
                recommended_action = "Pause — critically below breakeven across multiple periods."
            recommended_budget_change_pct = -100
        elif confidence in ("MEDIUM", "HIGH") and not _persistence_confirmed:
            # Have confidence but persistence unconfirmed (no prior data or improving) → REDUCE
            verdict = "REDUCE"
            disqualifiers.append("pause_persistence_unconfirmed")
            reasons.append("reduce_high")
            recommended_action = (
                f"Reduce budget 20% — ROAS {roas_now:.2f}x critically below breakeven "
                f"{breakeven:.2f}x. Pause if this persists next period."
                if (roas_now and breakeven) else
                "Reduce budget 20% — critical economics. Pause if persists."
            )
            recommended_budget_change_pct = -20
        else:
            # LOW confidence critical → MONITOR
            verdict = "MONITOR"
            reasons.append("monitor_low_confidence")
            recommended_action = "CRITICAL economics — low confidence; monitor before acting."
            recommended_budget_change_pct = None

    # REDUCE: UNPROFITABLE — check for conflicting improvement signal first
    elif pl_status == "UNPROFITABLE":
        if trend_dir == "IMPROVING" and confidence in ("MEDIUM", "HIGH"):
            # Conflicting signal: below breakeven but showing strong improvement
            # Could be recovering — investigate rather than reduce
            verdict = "INVESTIGATE"
            reasons.append("investigate_conflicting_signals")
            recommended_action = (
                "Improving trend while below breakeven — may be recovering. "
                "Do not reduce; confirm ROAS crosses breakeven before acting."
            )
            recommended_budget_change_pct = None
        elif confidence == "HIGH":
            verdict = "REDUCE"
            reasons.append("reduce_high")
            recommended_action = (
                f"Reduce budget 20% — ROAS {roas_now:.2f}x below breakeven {breakeven:.2f}x."
                if (roas_now and breakeven) else "Reduce budget 20% — unprofitable."
            )
            recommended_budget_change_pct = -20
        elif confidence == "MEDIUM":
            verdict = "REDUCE"
            reasons.append("reduce_medium")
            recommended_action = (
                f"Reduce budget 15% — below breakeven {breakeven:.2f}x."
                if breakeven else "Reduce budget 15% — unprofitable."
            )
            recommended_budget_change_pct = -15
        else:
            verdict = "MONITOR"
            reasons.append("monitor_low_confidence")
            recommended_action = "Below breakeven — insufficient confidence. Monitor closely."
            recommended_budget_change_pct = None

    # BREAKEVEN: near-breakeven — tracked separately from profitable
    elif pl_status == "BREAKEVEN":
        if trend_dir == "WORSENING":
            verdict = "MONITOR"
            reasons.append("monitor_near_breakeven")
            recommended_action = "Near-breakeven and worsening — watch closely; hold budget."
            recommended_budget_change_pct = None
        elif trend_dir in ("STABLE", "IMPROVING") and confidence in ("MEDIUM", "HIGH") and not _high_freq_constrained:
            verdict = "SCALE"
            if trend_dir == "IMPROVING":
                reasons.append("scale_improving")
                recommended_action = f"Near-breakeven and improving — small budget test (+{T['scale_budget_pct_mild']}%)."
                recommended_budget_change_pct = T["scale_budget_pct_mild"]
            else:
                reasons.append("scale_stable")
                recommended_action = f"Near-breakeven and stable — conservative test (+{T['scale_budget_pct_mild']}%)."
                recommended_budget_change_pct = T["scale_budget_pct_mild"]
        else:
            verdict = "HOLD"
            recommended_action = "Near-breakeven — hold budget, focus on creative optimisation."
            recommended_budget_change_pct = None

    # PROFITABLE / ON_TARGET
    elif pl_status in ("PROFITABLE", "ON_TARGET"):
        if trend_dir == "WORSENING":
            verdict = "MONITOR"
            reasons.append("monitor_worsening")
            recommended_action = "Profitable but efficiency declining — monitor; review creative and audience."
            recommended_budget_change_pct = None
        elif trend_dir in ("STABLE", "IMPROVING") and confidence in ("MEDIUM", "HIGH"):
            if _high_freq_constrained:
                # Profitable and scalable signal, but delivery constrained — INVESTIGATE
                verdict = "INVESTIGATE"
                reasons.append("investigate_delivery_constraint")
                recommended_action = (
                    f"Profitable with scale signal but frequency {campaign_freq:.1f}x constrained — "
                    "refresh creative to unlock scaling."
                )
                recommended_budget_change_pct = None
            else:
                # SCALE — use larger % for IMPROVING, smaller for STABLE
                verdict = "SCALE"
                if trend_dir == "IMPROVING":
                    reasons.append("scale_improving")
                    recommended_action = f"Profitable and improving — increase budget +{T['scale_budget_pct']}%."
                    recommended_budget_change_pct = T["scale_budget_pct"]
                else:
                    reasons.append("scale_stable")
                    recommended_action = f"Profitable and stable — test budget increase +{T['scale_budget_pct_mild']}%."
                    recommended_budget_change_pct = T["scale_budget_pct_mild"]
        else:
            # LOW confidence, or UNKNOWN trend (no prior data) → HOLD
            verdict = "HOLD"
            recommended_action = "Profitable — hold budget."
            recommended_budget_change_pct = None

    else:
        verdict = "WATCH_ONLY"
        recommended_action = "Unclassified state — observe only."
        recommended_budget_change_pct = None

    # ── Creative override ──────────────────────────────────────────────────
    # REFRESH_NOW on a HOLD (economics ok, but creative fatigue urgent) → CREATIVE_REFRESH
    if creative_status == "REFRESH_NOW" and verdict == "HOLD":
        verdict = "CREATIVE_REFRESH"
        reasons.append("creative_refresh_now")
        recommended_action = "Refresh creative immediately — fatigue present; economics ok."
        recommended_budget_change_pct = None
    elif creative_status == "REFRESH_SOON":
        reasons.append("creative_refresh_soon")
    elif creative_status == "HIGH_FREQUENCY":
        reasons.append("creative_high_frequency")

    # ── Daily-mode action translation ──────────────────────────────────────
    # In daily mode, never use budget-action language. Translate to monitoring/investigation.
    _daily_translations = {
        "SCALE":    "Monitor — trend improving; confirm over 7-day window before scaling budget.",
        "HOLD":     "Monitor — stable; no action required today.",
        "MONITOR":  recommended_action,   # already monitor language
        "REDUCE":   "Investigate — efficiency declining. Do not reduce until confirmed over 7-day window.",
        "PAUSE":    "Investigate — persistent underperformance flagged. Review before pausing.",
        "CREATIVE_REFRESH": "Refresh creative immediately — fatigue signals detected.",
        "INVESTIGATE": recommended_action,  # already investigate language
        "WATCH_ONLY": recommended_action,
    }
    daily_action = _daily_translations.get(verdict, recommended_action)

    verdict_summary = _verdict_summary(verdict, pl_status, trend_dir, confidence, disqualifiers, reasons)

    return {
        "verdict": verdict,
        "econ_status": pl_status,
        "trend": trend_dir,
        "confidence": confidence,
        "creative_status": creative_status,
        "reasons": reasons,
        "disqualifiers": disqualifiers,
        "recommended_action": recommended_action,
        "daily_action": daily_action,
        "recommended_budget_change_pct": recommended_budget_change_pct,
        "period_scope": period_scope,
        "verdict_summary": verdict_summary,
    }


# ---------------------------------------------------------------------------
# Index builders
# ---------------------------------------------------------------------------

def _index_by(items: list, key: str) -> dict:
    """Build a dict keyed by a field value, taking the first match per key."""
    return {item[key]: item for item in items if key in item}


def _group_by(items: list, key: str) -> dict:
    """Group a list into a dict of lists keyed by a field value."""
    groups = defaultdict(list)
    for item in items:
        if key in item:
            groups[item[key]].append(item)
    return groups


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def account_overview(insights: list, campaigns: list, adsets: list, ads: list) -> dict:
    totals = _sum_insights(insights)
    spend = totals["spend"]

    purchases = _sum_action_values(insights, "purchase", field="actions")
    revenue = _sum_action_values(insights, "purchase", field="action_values")
    roas = round(revenue / spend, 2) if spend and revenue else None
    cpa = round(spend / purchases, 2) if purchases else None

    return {
        **totals,
        "purchases": int(purchases),
        "revenue": round(revenue, 2),
        "roas": roas,
        "cpa": cpa,
        "frequency": _weighted_frequency(insights),
        "active_campaigns": sum(1 for c in campaigns if c.get("status") == "ACTIVE"),
        "active_adsets": sum(1 for a in adsets if a.get("status") == "ACTIVE"),
        "active_ads": sum(1 for a in ads if a.get("status") == "ACTIVE"),
    }


def strategy_snapshot(campaigns: list, adsets: list) -> dict:
    objectives = sorted({c.get("objective") for c in campaigns if c.get("objective")})

    budget_types = {}
    for c in campaigns:
        budget_types[c["id"]] = "CBO" if c.get("daily_budget") else "Ad set level"

    bid_strategies = sorted({
        a.get("bid_strategy")
        for a in adsets
        if a.get("bid_strategy")
    })

    return {
        "objectives": objectives,
        "budget_types": budget_types,
        "bid_strategies": bid_strategies,
        "total_campaigns": len(campaigns),
        "total_adsets": len(adsets),
    }


def campaign_rows(campaigns: list, insights: list, ads: list) -> list:
    insights_by_campaign = _group_by(insights, "campaign_id")
    ads_by_campaign = _group_by(ads, "campaign_id")

    rows = []
    for c in campaigns:
        cid = c.get("id")
        campaign_insights = insights_by_campaign.get(cid, [])
        campaign_ads = ads_by_campaign.get(cid, [])

        totals = _sum_insights(campaign_insights)
        has_disapproved = any(a.get("status") == "DISAPPROVED" for a in campaign_ads)

        purchases = _sum_action_values(campaign_insights, "purchase", field="actions")
        revenue = _sum_action_values(campaign_insights, "purchase", field="action_values")
        roas = round(revenue / totals["spend"], 2) if revenue and totals["spend"] else None
        cpa = round(totals["spend"] / purchases, 2) if purchases else None

        rows.append({
            "id": cid,
            "name": c.get("name"),
            "objective": c.get("objective"),
            "status": c.get("status"),
            "buying_type": c.get("buying_type"),
            "bid_strategy": c.get("bid_strategy"),
            "daily_budget": c.get("daily_budget"),
            "has_disapproved_ads": has_disapproved,
            "purchases": int(purchases),
            "roas": roas,
            "cpa": cpa,
            **totals,
        })

    return rows


def adset_bidding_rows(adsets: list, campaigns: list, insights: list) -> list:
    insights_by_adset = _group_by(insights, "adset_id")
    campaign_by_id = _index_by(campaigns, "id")

    rows = []
    for a in adsets:
        aid = a.get("id")
        adset_insights = insights_by_adset.get(aid, [])
        totals = _sum_insights(adset_insights)

        purchases = sum(_action_value(r.get("actions"), "purchase") for r in adset_insights)
        cpa = round(totals["spend"] / purchases, 2) if purchases else None

        campaign = campaign_by_id.get(a.get("campaign_id"), {})

        rows.append({
            "id": aid,
            "name": a.get("name"),
            "campaign_id": a.get("campaign_id"),
            "campaign_name": campaign.get("name"),
            "status": a.get("status"),
            "bid_strategy": a.get("bid_strategy"),
            "bid_amount": a.get("bid_amount"),
            "optimization_goal": a.get("optimization_goal"),
            "daily_budget": a.get("daily_budget"),
            "lifetime_budget": a.get("lifetime_budget"),
            "cpa": cpa,
            **totals,
        })

    return rows


def ad_creative_rows(ads: list, adsets: list, insights: list) -> list:
    insights_by_ad = _index_by(insights, "ad_id")
    adset_by_id = _index_by(adsets, "id")

    rows = []
    for ad in ads:
        ad_id = ad.get("id")
        insight = insights_by_ad.get(ad_id, {})
        adset = adset_by_id.get(ad.get("adset_id"), {})

        impressions = _float(insight.get("impressions"))
        frequency = _float(insight.get("frequency"))
        status = ad.get("status")
        adset_active = adset.get("status") == "ACTIVE"

        rows.append({
            "id": ad_id,
            "name": ad.get("name"),
            "adset_id": ad.get("adset_id"),
            "adset_name": adset.get("name"),
            "campaign_id": adset.get("campaign_id"),
            "status": status,
            "impressions": int(impressions),
            "clicks": int(_float(insight.get("clicks"))),
            "ctr": _float(insight.get("ctr")) or None,
            "cpc": _float(insight.get("cpc")) or None,
            "cpm": _float(insight.get("cpm")) or None,
            "frequency": round(frequency, 2) if frequency else None,
            "spend": round(_float(insight.get("spend")), 2),
            "is_disapproved": status == "DISAPPROVED",
            "high_frequency": frequency > 3.0 if frequency else False,
            "zero_impressions_while_active": impressions == 0 and adset_active,
        })

    return rows


# ---------------------------------------------------------------------------
# Trend computation (requires insights_30d with time_increment=1)
# ---------------------------------------------------------------------------

def compute_trends(insights_30d: list) -> dict:
    """
    Compute last-7-days vs prior-7-days trends at campaign and ad level.
    Metrics: spend, CTR, CPC, CPA, ROAS (ROAS only if action_values present in 30d data).
    Requires insights fetched with time_increment=1.
    Returns {"by_campaign": {...}, "by_ad": {...}}.

    Field naming: "_yesterday" fields hold the 7-day current period value (total or ratio).
    "_7d_avg" fields hold the prior-7-day benchmark (daily average for spend, ratio for rates).
    Change_pct compares daily averages so spend scale doesn't skew the signal.
    """
    if not insights_30d:
        return {"by_campaign": {}, "by_ad": {}}

    dates = sorted({r.get("date_start") for r in insights_30d if r.get("date_start")})
    if len(dates) < 2:
        return {"by_campaign": {}, "by_ad": {}}

    # Current period = last 7 days; prior period = the 7 days before that
    current_dates = set(dates[-7:])
    prior_dates = set(dates[-14:-7]) if len(dates) >= 14 else set(dates[:-7])

    def _campaign_trend(rows: list, name: str) -> dict:
        curr_rows = [r for r in rows if r.get("date_start") in current_dates]
        prior_rows = [r for r in rows if r.get("date_start") in prior_dates]

        if not curr_rows:
            return {}

        curr = _sum_insights(curr_rows)
        n_curr = len({r.get("date_start") for r in curr_rows})
        n_prior = len({r.get("date_start") for r in prior_rows})

        # CPA: total spend / total purchases over the period
        curr_purchases = _sum_action_values(curr_rows, "purchase", field="actions")
        curr_cpa = round(curr["spend"] / curr_purchases, 2) if curr_purchases else None

        # ROAS: total revenue / total spend over the period
        curr_revenue = _sum_action_values(curr_rows, "purchase", field="action_values")
        curr_roas = round(curr_revenue / curr["spend"], 2) if curr_revenue and curr["spend"] else None

        # Daily avg spend for change_pct comparison (same unit as prior period)
        curr_spend_daily = round(curr["spend"] / n_curr, 2) if n_curr else curr["spend"]

        if n_prior >= 3:
            prior = _sum_insights(prior_rows)
            prior_spend_daily = round(prior["spend"] / n_prior, 2)
            prior_ctr = prior["ctr"]
            prior_cpc = prior["cpc"]

            prior_purchases = _sum_action_values(prior_rows, "purchase", field="actions")
            prior_cpa = round(prior["spend"] / prior_purchases, 2) if prior_purchases else None

            prior_revenue = _sum_action_values(prior_rows, "purchase", field="action_values")
            prior_roas = round(prior_revenue / prior["spend"], 2) if prior_revenue and prior["spend"] else None
        else:
            prior_spend_daily = prior_ctr = prior_cpc = prior_cpa = prior_roas = None

        return {
            "campaign_name": name,
            # "yesterday" fields = 7-day current period totals/ratios (kept for downstream compat)
            "spend_yesterday": curr["spend"],        # 7d total spend
            "spend_7d_avg": prior_spend_daily,       # prior-7d daily avg (benchmark)
            "spend_change_pct": _pct_change(curr_spend_daily, prior_spend_daily),
            "impressions_yesterday": curr["impressions"],
            "clicks_yesterday": curr["clicks"],
            "purchases_yesterday": int(curr_purchases),
            "ctr_yesterday": curr["ctr"],
            "ctr_7d_avg": prior_ctr,
            "ctr_change_pct": _pct_change(curr["ctr"], prior_ctr),
            "cpc_yesterday": curr["cpc"],
            "cpc_7d_avg": prior_cpc,
            "cpc_change_pct": _pct_change(curr["cpc"], prior_cpc),
            "cpa_yesterday": curr_cpa,
            "cpa_7d_avg": prior_cpa,
            "cpa_change_pct": _pct_change(curr_cpa, prior_cpa),
            "roas_yesterday": curr_roas,
            "roas_7d_avg": prior_roas,
            "roas_change_pct": _pct_change(curr_roas, prior_roas),
        }

    def _ad_trend(rows: list) -> dict:
        curr_rows = [r for r in rows if r.get("date_start") in current_dates]
        prior_rows = [r for r in rows if r.get("date_start") in prior_dates]

        if not curr_rows or len({r.get("date_start") for r in prior_rows}) < 3:
            return {}

        curr = _sum_insights(curr_rows)
        prior = _sum_insights(prior_rows)
        n_curr = len({r.get("date_start") for r in curr_rows})
        n_prior = len({r.get("date_start") for r in prior_rows})
        curr_spend_daily = round(curr["spend"] / n_curr, 2) if n_curr else curr["spend"]
        prior_spend_daily = round(prior["spend"] / n_prior, 2) if n_prior else prior["spend"]

        curr_purchases = _sum_action_values(curr_rows, "purchase", field="actions")
        curr_cpa = round(curr["spend"] / curr_purchases, 2) if curr_purchases else None
        prior_purchases = _sum_action_values(prior_rows, "purchase", field="actions")
        prior_cpa = round(prior["spend"] / prior_purchases, 2) if prior_purchases else None

        ref = curr_rows[0]
        return {
            "ad_name": ref.get("ad_name"),
            "adset_name": ref.get("adset_name"),
            "campaign_name": ref.get("campaign_name"),
            "spend_yesterday": curr["spend"],
            "spend_7d_avg": prior_spend_daily,
            "spend_change_pct": _pct_change(curr_spend_daily, prior_spend_daily),
            "impressions_yesterday": curr["impressions"],
            "impressions_window": prior["impressions"],
            "ctr_yesterday": curr["ctr"],
            "ctr_7d_avg": prior["ctr"],
            "ctr_change_pct": _pct_change(curr["ctr"], prior["ctr"]),
            "cpc_yesterday": curr["cpc"],
            "cpc_7d_avg": prior["cpc"],
            "cpc_change_pct": _pct_change(curr["cpc"], prior["cpc"]),
            "cpa_yesterday": curr_cpa,
            "cpa_7d_avg": prior_cpa,
            "cpa_change_pct": _pct_change(curr_cpa, prior_cpa),
        }

    by_campaign_raw = _group_by(insights_30d, "campaign_id")
    by_ad_raw = _group_by(insights_30d, "ad_id")

    campaign_names = {}
    for r in insights_30d:
        cid = r.get("campaign_id")
        if cid and cid not in campaign_names and r.get("campaign_name"):
            campaign_names[cid] = r["campaign_name"]

    campaign_trends = {
        cid: t
        for cid, rows in by_campaign_raw.items()
        if (t := _campaign_trend(rows, campaign_names.get(cid, "")))
    }

    ad_trends = {
        ad_id: t
        for ad_id, rows in by_ad_raw.items()
        if (t := _ad_trend(rows))
    }

    # Account-level 7d vs prior-7d totals (used for the overview comparison row)
    all_curr = [r for r in insights_30d if r.get("date_start") in current_dates]
    all_prior = [r for r in insights_30d if r.get("date_start") in prior_dates]
    account_totals: dict = {}
    if all_curr:
        curr_acc = _sum_insights(all_curr)
        acc_purchases = _sum_action_values(all_curr, "purchase", field="actions")
        acc_revenue = _sum_action_values(all_curr, "purchase", field="action_values")
        acc_roas = round(acc_revenue / curr_acc["spend"], 2) if acc_revenue and curr_acc["spend"] else None
        acc_cpa = round(curr_acc["spend"] / acc_purchases, 2) if acc_purchases else None

        prior_roas = prior_cpa = prior_spend = prior_purchases = None
        if all_prior:
            prior_acc = _sum_insights(all_prior)
            prior_purch = _sum_action_values(all_prior, "purchase", field="actions")
            prior_rev = _sum_action_values(all_prior, "purchase", field="action_values")
            prior_roas = round(prior_rev / prior_acc["spend"], 2) if prior_rev and prior_acc["spend"] else None
            prior_cpa = round(prior_acc["spend"] / prior_purch, 2) if prior_purch else None
            prior_spend = prior_acc["spend"]
            prior_purchases = int(prior_purch)

        account_totals = {
            "curr_spend": curr_acc["spend"],
            "curr_roas": acc_roas,
            "curr_cpa": acc_cpa,
            "curr_purchases": int(acc_purchases),
            "prior_spend": prior_spend,
            "prior_roas": prior_roas,
            "prior_cpa": prior_cpa,
            "prior_purchases": prior_purchases,
            "roas_change_pct": _pct_change(acc_roas, prior_roas),
            "cpa_change_pct": _pct_change(acc_cpa, prior_cpa),
            "spend_change_pct": _pct_change(
                curr_acc["spend"] / len(current_dates) if current_dates else None,
                prior_spend / len(prior_dates) if prior_spend and prior_dates else None,
            ),
        }

    return {"by_campaign": campaign_trends, "by_ad": ad_trends, "account_totals": account_totals}


def compute_trends_daily(insights_30d: list) -> dict:
    """
    Compute yesterday-vs-prior-7-day-average trends at campaign and ad level.
    Used for the daily report: yesterday is the current value, prior 7 days is the benchmark.
    Requires insights fetched with time_increment=1.
    Returns {"by_campaign": {...}, "by_ad": {...}}.
    """
    if not insights_30d:
        return {"by_campaign": {}, "by_ad": {}}

    dates = sorted({r.get("date_start") for r in insights_30d if r.get("date_start")})
    if len(dates) < 2:
        return {"by_campaign": {}, "by_ad": {}}

    yesterday = dates[-1]
    window_dates = set(dates[-8:-1])  # 7 days before yesterday

    def _campaign_trend(rows: list, name: str) -> dict:
        y_rows = [r for r in rows if r.get("date_start") == yesterday]
        w_rows = [r for r in rows if r.get("date_start") in window_dates]

        if not y_rows:
            return {}

        y = _sum_insights(y_rows)
        n_days = len({r.get("date_start") for r in w_rows})

        y_purchases = _sum_action_values(y_rows, "purchase", field="actions")
        y_cpa = round(y["spend"] / y_purchases, 2) if y_purchases else None

        y_revenue = _sum_action_values(y_rows, "purchase", field="action_values")
        y_roas = round(y_revenue / y["spend"], 2) if y_revenue and y["spend"] else None

        if n_days >= 3:
            w = _sum_insights(w_rows)
            w_spend_avg = round(w["spend"] / n_days, 2)
            w_ctr = w["ctr"]
            w_cpc = w["cpc"]

            w_purchases = _sum_action_values(w_rows, "purchase", field="actions")
            w_cpa = round(w["spend"] / w_purchases, 2) if w_purchases else None

            w_revenue = _sum_action_values(w_rows, "purchase", field="action_values")
            w_roas = round(w_revenue / w["spend"], 2) if w_revenue and w["spend"] else None
        else:
            w_spend_avg = w_ctr = w_cpc = w_cpa = w_roas = None

        return {
            "campaign_name": name,
            "spend_yesterday": y["spend"],
            "spend_7d_avg": w_spend_avg,
            "spend_change_pct": _pct_change(y["spend"], w_spend_avg),
            "impressions_yesterday": y["impressions"],
            "clicks_yesterday": y["clicks"],
            "purchases_yesterday": int(y_purchases),
            "ctr_yesterday": y["ctr"],
            "ctr_7d_avg": w_ctr,
            "ctr_change_pct": _pct_change(y["ctr"], w_ctr),
            "cpc_yesterday": y["cpc"],
            "cpc_7d_avg": w_cpc,
            "cpc_change_pct": _pct_change(y["cpc"], w_cpc),
            "cpa_yesterday": y_cpa,
            "cpa_7d_avg": w_cpa,
            "cpa_change_pct": _pct_change(y_cpa, w_cpa),
            "roas_yesterday": y_roas,
            "roas_7d_avg": w_roas,
            "roas_change_pct": _pct_change(y_roas, w_roas),
        }

    def _ad_trend(rows: list) -> dict:
        y_rows = [r for r in rows if r.get("date_start") == yesterday]
        w_rows = [r for r in rows if r.get("date_start") in window_dates]

        if not y_rows or len({r.get("date_start") for r in w_rows}) < 3:
            return {}

        y = _sum_insights(y_rows)
        w = _sum_insights(w_rows)
        n_days = len({r.get("date_start") for r in w_rows})
        w_spend_avg = round(w["spend"] / n_days, 2)

        y_purchases = _sum_action_values(y_rows, "purchase", field="actions")
        y_cpa = round(y["spend"] / y_purchases, 2) if y_purchases else None
        w_purchases = _sum_action_values(w_rows, "purchase", field="actions")
        w_cpa = round(w["spend"] / w_purchases, 2) if w_purchases else None

        ref = y_rows[0]
        return {
            "ad_name": ref.get("ad_name"),
            "adset_name": ref.get("adset_name"),
            "campaign_name": ref.get("campaign_name"),
            "spend_yesterday": y["spend"],
            "spend_7d_avg": w_spend_avg,
            "spend_change_pct": _pct_change(y["spend"], w_spend_avg),
            "impressions_yesterday": y["impressions"],
            "impressions_window": w["impressions"],
            "ctr_yesterday": y["ctr"],
            "ctr_7d_avg": w["ctr"],
            "ctr_change_pct": _pct_change(y["ctr"], w["ctr"]),
            "cpc_yesterday": y["cpc"],
            "cpc_7d_avg": w["cpc"],
            "cpc_change_pct": _pct_change(y["cpc"], w["cpc"]),
            "cpa_yesterday": y_cpa,
            "cpa_7d_avg": w_cpa,
            "cpa_change_pct": _pct_change(y_cpa, w_cpa),
        }

    by_campaign_raw = _group_by(insights_30d, "campaign_id")
    by_ad_raw = _group_by(insights_30d, "ad_id")

    campaign_names = {}
    for r in insights_30d:
        cid = r.get("campaign_id")
        if cid and cid not in campaign_names and r.get("campaign_name"):
            campaign_names[cid] = r["campaign_name"]

    campaign_trends = {
        cid: t
        for cid, rows in by_campaign_raw.items()
        if (t := _campaign_trend(rows, campaign_names.get(cid, "")))
    }
    ad_trends = {
        ad_id: t
        for ad_id, rows in by_ad_raw.items()
        if (t := _ad_trend(rows))
    }
    return {"by_campaign": campaign_trends, "by_ad": ad_trends}


# ---------------------------------------------------------------------------
# Winner / opportunity detection
# ---------------------------------------------------------------------------

_MIN_SPEND_FOR_WINNERS = 50.0  # minimum daily spend for winner detection (×period_days for 7d)
_ROAS_OPPORTUNITY_FLOOR = 5.0  # yesterday ROAS above this → flag as scaling candidate
_CPA_IMPROVEMENT_THRESHOLD = 0.80   # yesterday CPA < 7d avg * this → efficiency winner
_CTR_STRENGTH_THRESHOLD = 1.30      # yesterday CTR > 7d avg * this → CTR strength
_ROAS_TREND_THRESHOLD = 1.30        # yesterday ROAS > 7d avg * this → ROAS improving


def detect_winners(trends: dict, c_rows: list, period_days: int = 1) -> list:
    """
    Rule-based detection of scaling opportunities and efficiency winners.
    Rules:
    - Efficiency Winner: CPA yesterday < 7d avg * 0.80
    - CTR Strength: CTR yesterday > 7d avg * 1.30
    - Scaling Opportunity: yesterday ROAS > 5x (from daily data)
    - ROAS Improving: yesterday ROAS > 7d avg ROAS * 1.30 (requires historical action_values)
    Returns up to 8 opportunities ordered by type priority.
    """
    winners = []
    by_campaign = trends.get("by_campaign", {})
    campaign_by_id = {c["id"]: c for c in c_rows}
    seen: set = set()

    priority = {
        "Scaling Opportunity": 0,
        "ROAS Improving": 1,
        "Efficiency Winner": 2,
        "CTR Strength": 3,
    }

    min_spend = _MIN_SPEND_FOR_WINNERS * period_days
    for cid, t in by_campaign.items():
        spend_yday = t.get("spend_yesterday") or 0
        if spend_yday < min_spend:
            continue

        name = t.get("campaign_name", "")
        y_purchases = t.get("purchases_yesterday", 0)

        # CPA improvement — require >= 2 purchases to avoid noise from single conversions
        cpa_yday = t.get("cpa_yesterday")
        cpa_7d = t.get("cpa_7d_avg")
        if cpa_yday and cpa_7d and cpa_yday < cpa_7d * _CPA_IMPROVEMENT_THRESHOLD and y_purchases >= 2:
            key = (cid, "cpa")
            if key not in seen:
                seen.add(key)
                improvement = round((1 - cpa_yday / cpa_7d) * 100, 1)
                winners.append({
                    "type": "Efficiency Winner",
                    "where": name,
                    "metric": f"CPA ${cpa_yday:.2f} vs prior week ${cpa_7d:.2f} (-{improvement}%)",
                    "action": "CPA well below average. Consider increasing budget allocation.",
                })

        # CTR strength
        ctr_yday = t.get("ctr_yesterday")
        ctr_7d = t.get("ctr_7d_avg")
        if ctr_yday and ctr_7d and ctr_yday > ctr_7d * _CTR_STRENGTH_THRESHOLD:
            key = (cid, "ctr")
            if key not in seen:
                seen.add(key)
                lift = round((ctr_yday / ctr_7d - 1) * 100, 1)
                winners.append({
                    "type": "CTR Strength",
                    "where": name,
                    "metric": f"CTR {ctr_yday:.2f}% vs prior week {ctr_7d:.2f}% (+{lift}%)",
                    "action": "Creative resonating above average. Monitor for further scaling.",
                })

        # ROAS strength (from yesterday's daily data — absolute threshold)
        # Require >= 2 purchases to avoid noise from single high-value conversions
        campaign = campaign_by_id.get(cid, {})
        roas_daily = campaign.get("roas")
        campaign_purchases = campaign.get("purchases", 0)
        if roas_daily and roas_daily > _ROAS_OPPORTUNITY_FLOOR and spend_yday >= 50 and campaign_purchases >= 2:
            key = (cid, "roas_abs")
            if key not in seen:
                seen.add(key)
                winners.append({
                    "type": "Scaling Opportunity",
                    "where": name,
                    "metric": f"ROAS {roas_daily:.2f}x",
                    "action": "Strong ROAS. Strong candidate for budget increase.",
                })

        # ROAS trend (requires 30d data with action_values — may be None)
        roas_yday = t.get("roas_yesterday")
        roas_7d = t.get("roas_7d_avg")
        if roas_yday and roas_7d and roas_yday > roas_7d * _ROAS_TREND_THRESHOLD:
            key = (cid, "roas_trend")
            if key not in seen:
                seen.add(key)
                lift = round((roas_yday / roas_7d - 1) * 100, 1)
                winners.append({
                    "type": "ROAS Improving",
                    "where": name,
                    "metric": f"ROAS {roas_yday:.2f}x vs prior week {roas_7d:.2f}x (+{lift}%)",
                    "action": "ROAS trending up. Review what changed — consider scaling.",
                })

    winners.sort(key=lambda w: priority.get(w["type"], 99))
    return winners[:8]


# ---------------------------------------------------------------------------
# Prioritization — Scale / Watch / Fix
# ---------------------------------------------------------------------------

# Scale thresholds (daily values; multiply by period_days when period > 1)
_PRI_SCALE_MIN_SPEND = 50.0    # minimum daily spend for Scale eligibility
_PRI_SCALE_MIN_PURCHASES = 2   # minimum purchases to trust CPA/ROAS signal
_PRI_SCALE_ROAS_LIFT = 30.0    # roas_change_pct >= 30 → ROAS is >= 1.3x prior period
_PRI_SCALE_CPA_RATIO = -25.0   # cpa_change_pct  <= -25 → CPA is <= 0.75x prior period
_PRI_SCALE_CTR_LIFT = 25.0     # ctr_change_pct  >= 25 → CTR is >= 1.25x prior period
_PRI_SCALE_CPC_STABLE = 10.0   # cpc_change_pct  <= 10 → CPC considered stable

# Watch thresholds (mild deterioration — not yet Fix-level)
_PRI_WATCH_MIN_SPEND = 25.0    # minimum daily spend
_PRI_WATCH_MIN_IMP = 1000
_PRI_WATCH_CTR_DROP = -20.0    # ctr_change_pct between -30 and -20
_PRI_WATCH_CPA_SPIKE = 20.0    # cpa_change_pct between 20 and 40
_PRI_WATCH_SPEND_RISE = 50.0   # spend up > 50% with neutral efficiency
_PRI_WATCH_FREQ_LO = 2.0       # frequency Watch lower bound (2.0 – 3.0)

# Fix thresholds (daily values; multiply by period_days when period > 1)
_PRI_FIX_MIN_SPEND = 25.0      # minimum daily spend
_PRI_FIX_MIN_IMP = 1000
_PRI_FIX_CTR_DROP = -30.0      # ctr_change_pct < -30
_PRI_FIX_CPA_SPIKE = 40.0      # cpa_change_pct > 40
_PRI_FIX_ROAS_DROP = -30.0     # roas_change_pct < -30 → ROAS < 70% of prior period
_PRI_FIX_SPEND_SPIKE = 100.0   # spend > 2x prior period (+ worsening efficiency)
_PRI_FIX_FREQ = 3.0            # frequency > 3.0 at ad level
_PRI_FIX_ZERO_HIST_MIN = 5.0   # ad set prior-period avg spend >= this → zero-spend is a Fix
_PRI_FIX_AD_SPEND_MIN = 5.0    # minimum daily ad spend to flag high-frequency Fix


def _compute_campaign_frequency(cr_rows: list, adset_rows: list) -> dict:
    """
    Impression-weighted average frequency per campaign_id.
    Uses ad_creative_rows (which have adset_id) + adset_bidding_rows (adset_id → campaign_id).
    """
    adset_to_campaign = {
        a["id"]: a.get("campaign_id")
        for a in adset_rows
        if "id" in a and a.get("campaign_id")
    }
    totals: dict = defaultdict(lambda: {"impressions": 0.0, "weighted": 0.0})
    for ad in cr_rows:
        cid = adset_to_campaign.get(ad.get("adset_id"))
        if not cid:
            continue
        imp = _float(ad.get("impressions"))
        freq = _float(ad.get("frequency"))
        if imp > 0 and freq > 0:
            totals[cid]["impressions"] += imp
            totals[cid]["weighted"] += imp * freq
    return {
        cid: round(d["weighted"] / d["impressions"], 2)
        for cid, d in totals.items()
        if d["impressions"] > 0
    }


def _adset_7d_avg_spend(insights_30d: list) -> dict:
    """
    Average daily spend per adset_id over the prior 7-day period (days 8–14 back).
    Used to detect zero-spend anomalies vs historical baseline.
    Requires at least 3 days of data in the window to return a value.
    """
    if not insights_30d:
        return {}
    dates = sorted({r.get("date_start") for r in insights_30d if r.get("date_start")})
    if len(dates) < 2:
        return {}
    window_dates = set(dates[-14:-7]) if len(dates) >= 14 else set(dates[:-7])  # prior 7d
    by_adset: dict = defaultdict(list)
    for r in insights_30d:
        if r.get("date_start") in window_dates and r.get("adset_id"):
            by_adset[r["adset_id"]].append(_float(r.get("spend")))
    return {
        aid: round(sum(spends) / len(spends), 2)
        for aid, spends in by_adset.items()
        if len(spends) >= 3 and sum(spends) > 0
    }


def build_priorities(
    trends: dict,
    c_rows: list,
    adset_rows: list,
    cr_rows: list,
    insights_30d: list,
    period_days: int = 1,
) -> dict:
    """
    Deterministic Scale / Watch / Fix prioritization.

    Logic applied at campaign level (for performance trends) and ad/ad-set level
    (for disapprovals, frequency, and CBO zero-spend delivery issues).

    Fix is evaluated first. Campaigns already in Fix are excluded from Watch and Scale.
    Watch is evaluated next. Campaigns in Watch are excluded from Scale.
    Scale is evaluated last, with additional eligibility guards.

    Each bucket is sorted by spend_impact (descending) and capped at 3 items.
    Item keys: entity_name, entity_type, reason, metrics, action, spend_impact.
    """
    by_campaign = trends.get("by_campaign", {})
    campaign_by_id = {c["id"]: c for c in c_rows}
    campaign_freq = _compute_campaign_frequency(cr_rows, adset_rows)
    adset_hist_spend = _adset_7d_avg_spend(insights_30d)

    # Build adset → campaign lookup for disapproval spend attribution
    adset_to_campaign = {
        a["id"]: a.get("campaign_id")
        for a in adset_rows
        if "id" in a and a.get("campaign_id")
    }
    campaign_spend_map = {c["id"]: (c.get("spend") or 0) for c in c_rows}

    fix_items: list = []
    watch_items: list = []
    scale_items: list = []
    fix_campaign_ids: set = set()
    watch_campaign_ids: set = set()

    # Spend/impression thresholds scaled to the reporting period
    _fix_min_spend = _PRI_FIX_MIN_SPEND * period_days
    _fix_min_imp = _PRI_FIX_MIN_IMP * period_days
    _watch_min_spend = _PRI_WATCH_MIN_SPEND * period_days
    _watch_min_imp = _PRI_WATCH_MIN_IMP * period_days
    _scale_min_spend = _PRI_SCALE_MIN_SPEND * period_days
    _fix_ad_spend_min = _PRI_FIX_AD_SPEND_MIN * period_days

    # Build lookup from c_rows by campaign id for economics enrichment
    _c_rows_by_id = {r.get("id"): r for r in c_rows if r.get("id")}

    # -----------------------------------------------------------------------
    # FIX — campaign-level performance signals (verdict-gated)
    # Routing: classify_campaign() verdict gates inclusion; trend metrics build display.
    # REDUCE / PAUSE → Fix bucket. INVESTIGATE also surfaced here (anomaly-level urgency).
    # -----------------------------------------------------------------------
    for cid, t in by_campaign.items():
        spend_yday = t.get("spend_yesterday") or 0
        impressions_yday = t.get("impressions_yesterday") or 0

        if spend_yday < _fix_min_spend and impressions_yday < _fix_min_imp:
            continue

        # ── Shared verdict gate ─────────────────────────────────────────────
        enriched = _c_rows_by_id.get(cid, {})
        _verdict = enriched.get("verdict", "WATCH_ONLY")

        if _verdict not in ("REDUCE", "PAUSE", "INVESTIGATE"):
            continue

        name = t.get("campaign_name", "")
        ctr_chg = t.get("ctr_change_pct")
        cpa_chg = t.get("cpa_change_pct")
        roas_chg = t.get("roas_change_pct")
        spend_chg = t.get("spend_change_pct")
        cpa_yday = t.get("cpa_yesterday")
        roas_yday = t.get("roas_yesterday")
        ctr_yday = t.get("ctr_yesterday")
        cpa_7d = t.get("cpa_7d_avg")
        roas_7d = t.get("roas_7d_avg")
        ctr_7d = t.get("ctr_7d_avg")
        spend_7d = t.get("spend_7d_avg")
        pl_status = enriched.get("profitability_status", "UNKNOWN")
        be_roas_val = enriched.get("breakeven_roas")

        _is_daily = period_days == 1
        reason = metrics = action = ""

        # ── Display text selection — best available metric ──────────────────
        # Priority: absolute economics > CPA % > ROAS % > CTR % > spend spike > fallback

        # 1. Absolute economics (most authoritative)
        if pl_status in ("UNPROFITABLE", "CRITICAL") and spend_yday >= 25:
            if roas_yday and be_roas_val:
                reason = f"Unprofitable — ROAS {roas_yday:.2f}x vs breakeven {be_roas_val:.2f}x"
                metrics = f"ROAS {roas_yday:.2f}x (breakeven: {be_roas_val:.2f}x) | Spend ${spend_yday:.0f}"
                if _verdict == "PAUSE":
                    if _is_daily:
                        action = f"Investigate — ROAS {roas_yday:.2f}x critically below {be_roas_val:.2f}x breakeven across multiple periods."
                    else:
                        action = f"Pause or reduce to minimum — ROAS {roas_yday:.2f}x well below breakeven {be_roas_val:.2f}x, persistent."
                else:
                    if _is_daily:
                        action = f"ROAS {roas_yday:.2f}x is below breakeven {be_roas_val:.2f}x — monitor. If this persists 3+ days, reduce budget."
                    else:
                        action = f"Reduce budget 20–30% until ROAS recovers above {be_roas_val:.2f}x — review audience and creative mix."
            else:
                reason = "Unprofitable per unit economics (no ROAS data)"
                metrics = f"Spend ${spend_yday:.0f}"
                action = "Review bid strategy and creative mix — campaign unprofitable with no ROAS signal."

        # 2. CPA spike (below breakeven context)
        elif cpa_chg is not None and cpa_chg > _PRI_FIX_CPA_SPIKE:
            reason = f"CPA +{cpa_chg:.0f}% vs prior week"
            cpa_7d_str = f" vs prior week ${cpa_7d:.2f}" if cpa_7d else ""
            metrics = f"CPA ${cpa_yday:.2f}{cpa_7d_str} | Spend ${spend_yday:.0f}"
            cpa_7d_fmt = f"${cpa_7d:.0f}" if cpa_7d else "baseline"
            if _is_daily:
                action = f"CPA trending up — monitor over 2–3 days. CPA ${cpa_yday:.0f} is +{cpa_chg:.0f}% above {cpa_7d_fmt} avg."
            else:
                action = f"Sort ads by CPA and pause bottom performers. CPA ${cpa_yday:.0f} is +{cpa_chg:.0f}% above the {cpa_7d_fmt} 7-day avg."

        # 3. ROAS drop
        elif roas_chg is not None and roas_chg < _PRI_FIX_ROAS_DROP and roas_yday:
            reason = f"ROAS {roas_chg:.0f}% vs prior week"
            roas_7d_str = f" vs prior week {roas_7d:.2f}x" if roas_7d else ""
            metrics = f"ROAS {roas_yday:.2f}x{roas_7d_str} | Spend ${spend_yday:.0f}"
            roas_7d_fmt = f"{roas_7d:.2f}x" if roas_7d else "baseline"
            if _is_daily:
                action = f"ROAS declining — watch trend. ROAS {roas_yday:.2f}x is {abs(roas_chg):.0f}% below {roas_7d_fmt} avg."
            else:
                action = f"Reduce budget 20–30% until ROAS recovers. ROAS {roas_yday:.2f}x is {abs(roas_chg):.0f}% below {roas_7d_fmt} avg."

        # 4. CTR drop
        elif ctr_chg is not None and ctr_chg < _PRI_FIX_CTR_DROP and ctr_yday:
            reason = f"CTR {ctr_chg:.0f}% vs prior week"
            ctr_7d_str = f" vs prior week {ctr_7d:.2f}%" if ctr_7d else ""
            metrics = f"CTR {ctr_yday:.2f}%{ctr_7d_str} | Spend ${spend_yday:.0f}"
            ctr_7d_fmt = f"{ctr_7d:.2f}%" if ctr_7d else "baseline"
            action = f"Refresh creative — CTR {ctr_yday:.2f}% is {abs(ctr_chg):.0f}% below {ctr_7d_fmt} avg."

        # 5. Spend spike
        elif spend_chg is not None and spend_chg > _PRI_FIX_SPEND_SPIKE:
            reason = f"Spend +{spend_chg:.0f}% vs prior week with worsening efficiency"
            spend_7d_str = f" vs prior week ${spend_7d:.2f}" if spend_7d else ""
            metrics = f"Spend ${spend_yday:.0f}{spend_7d_str}"
            if _is_daily:
                action = f"Spend spike — investigate budget/bid changes. Spend +{spend_chg:.0f}% vs prior week."
            else:
                action = f"Verify budget and bid changes — spend +{spend_chg:.0f}% vs prior week. Reduce if efficiency doesn't stabilize."

        # 6. Fallback: use verdict recommended action
        else:
            reason = f"Below breakeven — {pl_status.lower()}"
            metrics = f"Spend ${spend_yday:.0f}"
            action = enriched.get(
                "verdict_daily_action" if _is_daily else "verdict_recommended_action",
                "Review campaign economics — spending without sufficient ROAS signal.",
            )

        if not reason:
            continue

        fix_items.append({
            "entity_name": name,
            "entity_id": cid,
            "campaign_id": cid,
            "entity_type": "campaign",
            "reason": reason,
            "metrics": metrics,
            "action": action,
            "spend_impact": spend_yday,
            "profitability_status": pl_status,
            "verdict": _verdict,
            "verdict_summary": enriched.get("verdict_summary", ""),
            "breakeven_roas": enriched.get("breakeven_roas"),
            "estimated_daily_profit": enriched.get("estimated_daily_profit"),
            "cpa_target_7dc": enriched.get("cpa_target_7dc"),
        })
        fix_campaign_ids.add(cid)

    # -----------------------------------------------------------------------
    # FIX — high-frequency ads (ad-level; not tied to a single campaign Fix)
    # -----------------------------------------------------------------------
    for ad in cr_rows:
        freq = _float(ad.get("frequency"))
        ad_spend = _float(ad.get("spend"))
        if freq > _PRI_FIX_FREQ and ad_spend >= _fix_ad_spend_min:
            fix_items.append({
                "entity_name": ad.get("name", ""),
                "entity_type": "ad",
                "reason": f"High frequency {freq:.1f}x",
                "metrics": f"Ad Set: {ad.get('adset_name', '')} | Freq {freq:.1f} | Spend ${ad_spend:.0f}",
                "action": "Rotate or refresh this creative to reduce audience fatigue.",
                "spend_impact": ad_spend,
            })

    # -----------------------------------------------------------------------
    # FIX — disapproved ads (no spend threshold; structural issue)
    # -----------------------------------------------------------------------
    for ad in cr_rows:
        if ad.get("is_disapproved"):
            adset_id = ad.get("adset_id")
            camp_id = adset_to_campaign.get(adset_id)
            camp_spend = campaign_spend_map.get(camp_id, 0) if camp_id else 0
            fix_items.append({
                "entity_name": ad.get("name", ""),
                "entity_type": "ad",
                "reason": "Ad disapproved — delivery blocked",
                "metrics": f"Ad Set: {ad.get('adset_name', '')} | Campaign spend ${camp_spend:.0f}",
                "action": "Fix policy violation or replace with a compliant creative immediately.",
                "spend_impact": camp_spend,
            })

    # -----------------------------------------------------------------------
    # FIX — zero-spend active ad sets with meaningful historical spend (CBO)
    # -----------------------------------------------------------------------
    for adset in adset_rows:
        if adset.get("status") == "ACTIVE" and (adset.get("spend") or 0) == 0:
            hist_avg = adset_hist_spend.get(adset.get("id"), 0)
            if hist_avg >= _PRI_FIX_ZERO_HIST_MIN:
                fix_items.append({
                    "entity_name": adset.get("name", ""),
                    "entity_type": "adset",
                    "reason": f"Zero spend despite ${hist_avg:.0f}/day 7-day avg",
                    "metrics": f"Campaign: {adset.get('campaign_name', '')} | 7d avg ${hist_avg:.0f}/day",
                    "action": "Investigate audience, bid competitiveness, and creative approval status.",
                    "spend_impact": hist_avg,
                })

    # Sort Fix by spend_impact desc, cap at 3
    fix_items.sort(key=lambda x: x["spend_impact"], reverse=True)
    fix_items = fix_items[:3]

    # -----------------------------------------------------------------------
    # WATCH — monitoring signals (verdict-gated on MONITOR + frequency/spend anomalies)
    # Routing: verdict == MONITOR gates inclusion. Metric detection provides display text.
    # Also accepts campaigns with frequency anomalies regardless of verdict.
    # -----------------------------------------------------------------------
    for cid, t in by_campaign.items():
        if cid in fix_campaign_ids:
            continue

        spend_yday = t.get("spend_yesterday") or 0
        impressions_yday = t.get("impressions_yesterday") or 0
        if spend_yday < _watch_min_spend and impressions_yday < _watch_min_imp:
            continue

        enriched = _c_rows_by_id.get(cid, {})
        _verdict = enriched.get("verdict", "WATCH_ONLY")
        freq = campaign_freq.get(cid)

        # ── Verdict gate ────────────────────────────────────────────────────
        # Include if MONITOR verdict, INVESTIGATE verdict, or frequency anomaly.
        _freq_anomaly = freq is not None and _PRI_WATCH_FREQ_LO <= freq < _PRI_FIX_FREQ
        if _verdict not in ("MONITOR", "INVESTIGATE") and not _freq_anomaly:
            continue

        name = t.get("campaign_name", "")
        ctr_chg = t.get("ctr_change_pct")
        cpa_chg = t.get("cpa_change_pct")
        spend_chg = t.get("spend_change_pct")
        cpa_yday = t.get("cpa_yesterday")
        ctr_yday = t.get("ctr_yesterday")
        cpa_7d = t.get("cpa_7d_avg")
        ctr_7d = t.get("ctr_7d_avg")
        spend_7d = t.get("spend_7d_avg")
        reason = metrics = action = ""

        # Display text: pick best available signal
        # CPA mildly up (20–40%)
        if cpa_chg is not None and _PRI_WATCH_CPA_SPIKE <= cpa_chg < _PRI_FIX_CPA_SPIKE:
            reason = f"CPA +{cpa_chg:.0f}% vs prior week — watch"
            cpa_7d_str = f" vs prior week ${cpa_7d:.2f}" if cpa_7d else ""
            metrics = f"CPA ${cpa_yday:.2f}{cpa_7d_str} | Spend ${spend_yday:.0f}"
            cpa_7d_fmt = f"${cpa_7d:.0f}" if cpa_7d else "baseline"
            action = f"Monitor daily. CPA ${cpa_yday:.0f} is +{cpa_chg:.0f}% above the {cpa_7d_fmt} avg — refresh creative or tighten audience if it persists for 2+ days."

        # CTR mildly down (20–30%)
        elif ctr_chg is not None and _PRI_WATCH_CTR_DROP <= ctr_chg < _PRI_FIX_CTR_DROP:
            reason = f"CTR {ctr_chg:.0f}% vs prior week — watch"
            ctr_7d_str = f" vs prior week {ctr_7d:.2f}%" if ctr_7d else ""
            metrics = f"CTR {ctr_yday:.2f}%{ctr_7d_str} | Spend ${spend_yday:.0f}"
            ctr_7d_fmt = f"{ctr_7d:.2f}%" if ctr_7d else "baseline"
            action = f"Stage new creative variants now — CTR {ctr_yday:.2f}% is {abs(ctr_chg):.0f}% below the {ctr_7d_fmt} avg. Rotate if decline continues tomorrow."

        # Spend up > 50% with neutral efficiency
        elif (
            spend_chg is not None and spend_chg > _PRI_WATCH_SPEND_RISE
            and (cpa_chg is None or abs(cpa_chg) < 20)
        ):
            _spend_signal_prefix = (
                f"{_verdict} candidate — " if _verdict in ("SCALE", "HOLD") else ""
            )
            reason = f"{_spend_signal_prefix}Spend +{spend_chg:.0f}% vs prior week — confirming efficiency holds"
            spend_7d_str = f" vs prior week ${spend_7d:.2f}" if spend_7d else ""
            metrics = f"Spend ${spend_yday:.0f}{spend_7d_str}"
            _be = enriched.get("breakeven_roas")
            _cpa_t = enriched.get("cpa_target_7dc")
            if _be and _cpa_t:
                action = (
                    f"Monitor daily. Alert if ROAS drops below {_be:.2f}x (breakeven) "
                    f"or CPA exceeds ${_cpa_t:.0f} (7DC target). Budget is scaling — confirm efficiency holds."
                )
            elif _be:
                action = f"Monitor daily. Alert if ROAS drops below {_be:.2f}x (breakeven). Budget is scaling — confirm efficiency holds."
            else:
                action = "Monitor daily. Set a ROAS or CPA alert — budget is scaling and efficiency must be confirmed."

        # Frequency approaching fatigue (2.0–3.0) — frequency-anomaly path
        elif _freq_anomaly:
            reason = f"Frequency {freq:.1f}x — approaching fatigue threshold"
            metrics = f"Campaign avg frequency {freq:.1f} | Spend ${spend_yday:.0f}"
            action = "Prepare new creative variants now. Fatigue typically accelerates within 2–3 days."

        # Fallback: use MONITOR verdict reason
        elif _verdict in ("MONITOR", "INVESTIGATE"):
            reason = enriched.get("verdict_recommended_action", f"Monitor — {_verdict.lower()}")
            metrics = f"Spend ${spend_yday:.0f}"
            action = reason

        else:
            continue

        watch_items.append({
            "entity_name": name,
            "entity_id": cid,
            "campaign_id": cid,
            "entity_type": "campaign",
            "reason": reason,
            "metrics": metrics,
            "action": action,
            "spend_impact": spend_yday,
            "profitability_status": enriched.get("profitability_status", "UNKNOWN"),
            "verdict_summary": enriched.get("verdict_summary", ""),
            "breakeven_roas": enriched.get("breakeven_roas"),
            "estimated_daily_profit": enriched.get("estimated_daily_profit"),
            "cpa_target_7dc": enriched.get("cpa_target_7dc"),
        })
        watch_campaign_ids.add(cid)

    watch_items.sort(key=lambda x: x["spend_impact"], reverse=True)
    watch_items = watch_items[:3]

    # -----------------------------------------------------------------------
    # SCALE — positive signals (campaigns not in Fix or Watch)
    # -----------------------------------------------------------------------
    scale_candidates: list = []
    for cid, t in by_campaign.items():
        if cid in fix_campaign_ids or cid in watch_campaign_ids:
            continue

        spend_yday = t.get("spend_yesterday") or 0
        if spend_yday < _scale_min_spend:
            continue

        # Gate: verdict must be SCALE (economics, volume, freq checks are all in classify_campaign)
        enriched_scale = _c_rows_by_id.get(cid, {})
        _verdict_scale = enriched_scale.get("verdict", "WATCH_ONLY")

        if _verdict_scale != "SCALE":
            continue

        _conf = enriched_scale.get("verdict_confidence", "LOW")
        roas_yday = t.get("roas_yesterday")
        purchases_yday = t.get("purchases_yesterday") or 0
        cpa_yday = t.get("cpa_yesterday")

        _is_daily_scale = period_days == 1

        name = t.get("campaign_name", "")
        roas_chg = t.get("roas_change_pct")
        cpa_chg = t.get("cpa_change_pct")
        ctr_chg = t.get("ctr_change_pct")
        cpc_chg = t.get("cpc_change_pct")
        roas_7d = t.get("roas_7d_avg")
        cpa_7d = t.get("cpa_7d_avg")
        ctr_7d = t.get("ctr_7d_avg")
        ctr_yday = t.get("ctr_yesterday")

        reason = metrics = action = ""
        _score = 0.0

        # ROAS >= 1.30x prior period
        if roas_chg is not None and roas_chg >= _PRI_SCALE_ROAS_LIFT and roas_yday:
            reason = f"ROAS +{roas_chg:.0f}% vs prior week"
            roas_7d_str = f" vs prior week {roas_7d:.2f}x" if roas_7d else ""
            metrics = (
                f"ROAS {roas_yday:.2f}x{roas_7d_str}"
                f" | {purchases_yday} purchases | Spend ${spend_yday:.0f}"
            )
            if _is_daily_scale:
                action = f"Strong ROAS trend — confirm over full 7-day window before increasing budget. ROAS +{roas_chg:.0f}% vs prior period."
            else:
                action = f"Increase daily budget by {THRESHOLDS['scale_budget_pct']}%. Monitor ROAS stability over the next 48 hours."
            _score = roas_chg

        # CPA <= 0.75x prior period (requires >= 2 purchases)
        elif (
            cpa_chg is not None and cpa_chg <= -_PRI_SCALE_CPA_RATIO
            and cpa_yday and purchases_yday >= _PRI_SCALE_MIN_PURCHASES
        ):
            improvement = -cpa_chg
            reason = f"CPA -{improvement:.0f}% vs prior week"
            cpa_7d_str = f" vs prior week ${cpa_7d:.2f}" if cpa_7d else ""
            metrics = (
                f"CPA ${cpa_yday:.2f}{cpa_7d_str}"
                f" | {purchases_yday} purchases | Spend ${spend_yday:.0f}"
            )
            if _is_daily_scale:
                action = f"CPA improving — confirm trend over 7-day window before scaling budget. CPA -{improvement:.0f}% vs prior period."
            else:
                action = f"Increase budget allocation. CPA is well below prior period — clear room to scale."
            _score = improvement

        # CTR >= 1.25x prior period with stable CPC (signal, not a budget trigger alone)
        elif (
            ctr_chg is not None and ctr_chg >= _PRI_SCALE_CTR_LIFT
            and (cpc_chg is None or cpc_chg <= _PRI_SCALE_CPC_STABLE)
        ):
            reason = f"CTR +{ctr_chg:.0f}% vs prior week, CPC stable"
            ctr_7d_str = f" vs prior week {ctr_7d:.2f}%" if ctr_7d else ""
            metrics = f"CTR {ctr_yday:.2f}%{ctr_7d_str} | Spend ${spend_yday:.0f}"
            if _is_daily_scale:
                action = "Engagement improving — watch for CPA/ROAS confirmation before increasing budget."
            else:
                action = "Creative outperforming. Duplicate ad set at higher budget or expand audience."
            _score = ctr_chg

        else:
            continue

        scale_candidates.append({
            "entity_name": name,
            "entity_id": cid,
            "campaign_id": cid,
            "entity_type": "campaign",
            "reason": reason,
            "metrics": metrics,
            "action": action,
            "spend_impact": spend_yday,
            "confidence": _conf,
            "_score": _score,
            "profitability_status": enriched_scale.get("profitability_status", "UNKNOWN"),
            "verdict_summary": enriched_scale.get("verdict_summary", ""),
            "breakeven_roas": enriched_scale.get("breakeven_roas"),
            "estimated_daily_profit": enriched_scale.get("estimated_daily_profit"),
            "roas_target_7dc": enriched_scale.get("roas_target_7dc"),
        })

    # Sort Scale by improvement score desc
    scale_candidates.sort(key=lambda x: x.get("_score", 0), reverse=True)
    for item in scale_candidates:
        item.pop("_score", None)
    scale_items = scale_candidates[:3]

    return {
        "scale": scale_items,
        "watch": watch_items,
        "fix": fix_items,
    }


# ---------------------------------------------------------------------------
# Health score
# ---------------------------------------------------------------------------

def compute_health_score(priorities: dict) -> dict:
    """
    Rule-based account health score derived from prioritized Scale/Watch/Fix items.

    Scoring logic (deterministic, documented):
    - AT RISK  : (>= 2 major Fix items OR fix spend >= $500) AND no Scale items
                 Scale items existing means the account is mixed, not purely at risk.
    - HEALTHY  : 0 major Fix items (spend_impact >= $25) AND >= 1 Scale item
    - MIXED    : everything else (has both fix and scale, or minor fix items, etc.)
    """
    fix_items = priorities.get("fix", [])
    watch_items = priorities.get("watch", [])
    scale_items = priorities.get("scale", [])

    major_fix = [f for f in fix_items if f.get("spend_impact", 0) >= 25]
    total_fix_spend = sum(f.get("spend_impact", 0) for f in fix_items)

    if (len(major_fix) >= 2 or total_fix_spend >= 500) and not scale_items:
        score = "AT RISK"
    elif not major_fix and scale_items:
        score = "HEALTHY"
    else:
        score = "MIXED"

    return {
        "score": score,
        "fix_count": len(fix_items),
        "watch_count": len(watch_items),
        "scale_count": len(scale_items),
        "major_fix_count": len(major_fix),
        "major_fix_spend": round(total_fix_spend, 2),
    }


# ---------------------------------------------------------------------------
# Creative intelligence
# ---------------------------------------------------------------------------

_MIN_SPEND_FOR_RANKING = 5.0


def creative_intelligence(
    ad_creative_rows: list,
    insights_daily: list,
    insights_30d: list,
    ad_trends: dict,
) -> dict:
    """
    Per-ad performance metrics and creative health signals.
    Requires daily insights (with action_values) and optionally 30d for trend and new-creative data.
    """
    insights_by_ad = _index_by(insights_daily, "ad_id")

    ads_with_spend = []
    for ad in ad_creative_rows:
        ad_id = ad.get("id")
        r = insights_by_ad.get(ad_id, {})
        spend = _float(r.get("spend"))
        if spend == 0:
            continue

        purchases = _action_value(r.get("actions", []), "purchase")
        revenue = _action_value(r.get("action_values", []), "purchase")
        cpa = round(spend / purchases, 2) if purchases else None
        roas = round(revenue / spend, 2) if revenue and spend else None

        frequency = _float(r.get("frequency")) or None
        ctr = _float(r.get("ctr")) or None
        trend = ad_trends.get(ad_id, {})
        ctr_change_pct = trend.get("ctr_change_pct")
        cpa_change_pct = trend.get("cpa_change_pct")

        # ── Creative fatigue classification ──────────────────────────────────
        # REFRESH_NOW / REFRESH_SOON: all three signals present + material spend
        #   1. Frequency saturation  (freq >= fatigue_freq = 2.5)
        #   2. Engagement decline    (CTR down >= 25%)
        #   3. Efficiency deterioration (CPA up >= 15%)
        # MONITOR_ENGAGEMENT: CTR declining >= 25% with spend, full fatigue not met
        # HIGH_FREQUENCY:     freq >= 3.0 with spend, no efficiency deterioration yet
        # HEALTHY:            none of the above
        _freq_saturated    = bool(frequency and frequency >= THRESHOLDS["fatigue_freq"])
        _high_freq         = bool(frequency and frequency >= THRESHOLDS["fix_freq"])  # 3.0
        _ctr_declining     = bool(ctr_change_pct is not None and ctr_change_pct <= -THRESHOLDS["fatigue_ctr_drop"])
        _eff_deteriorating = bool(cpa_change_pct is not None and cpa_change_pct >= THRESHOLDS["fatigue_cpa_rise"])
        _core_fatigue      = _freq_saturated and _ctr_declining and _eff_deteriorating

        if _core_fatigue and spend >= THRESHOLDS["fatigue_material_spend"]:
            # Full fatigue with material spend — REFRESH_NOW if signals at elevated thresholds
            if (
                frequency >= THRESHOLDS["fatigue_now_freq"]
                and ctr_change_pct is not None and ctr_change_pct <= -THRESHOLDS["fatigue_now_ctr_drop"]
                and (cpa_change_pct or 0) >= THRESHOLDS["fatigue_now_cpa_rise"]
            ):
                fatigue_status = "REFRESH_NOW"
            else:
                fatigue_status = "REFRESH_SOON"
            is_fatigued = True
        elif _ctr_declining and spend >= THRESHOLDS["fatigue_min_spend"]:
            # CTR declining with enough spend — engagement warning (full fatigue not met)
            fatigue_status = "MONITOR_ENGAGEMENT"
            is_fatigued = False
        elif _high_freq and spend >= THRESHOLDS["fatigue_min_spend"] and not _eff_deteriorating:
            # Frequency elevated (>= 3.0) with spend, no efficiency deterioration yet
            fatigue_status = "HIGH_FREQUENCY"
            is_fatigued = False
        else:
            fatigue_status = "HEALTHY"
            is_fatigued = False

        fatigue_reasons = []
        if _freq_saturated:
            fatigue_reasons.append(f"Freq {frequency:.1f}x")
        if _ctr_declining:
            fatigue_reasons.append(f"CTR {ctr_change_pct:.0f}%")
        if _eff_deteriorating:
            fatigue_reasons.append(f"CPA +{cpa_change_pct:.0f}%")

        ads_with_spend.append({
            "id": ad_id,
            "name": ad.get("name"),
            "adset_id": ad.get("adset_id"),
            "adset_name": ad.get("adset_name"),
            "campaign_id": ad.get("campaign_id"),
            "campaign_name": trend.get("campaign_name") or "",
            "spend": round(spend, 2),
            "ctr": ctr,
            "cpc": _float(r.get("cpc")) or None,
            "cpm": _float(r.get("cpm")) or None,
            "frequency": round(frequency, 2) if frequency else None,
            "purchases": int(purchases),
            "cpa": cpa,
            "roas": roas,
            "ctr_change_pct": ctr_change_pct,
            "cpa_change_pct": cpa_change_pct,
            "fatigue_status": fatigue_status,
            "is_fatigued": is_fatigued,
            "fatigue_reasons": fatigue_reasons,
        })

    ads_with_spend.sort(key=lambda a: a["spend"], reverse=True)

    rankable = [a for a in ads_with_spend if a["spend"] >= _MIN_SPEND_FOR_RANKING]
    by_roas = [a for a in rankable if a.get("roas") is not None]
    by_ctr = [a for a in rankable if a.get("ctr") is not None]

    top_creative = (
        max(by_roas, key=lambda a: a["roas"]) if by_roas
        else max(by_ctr, key=lambda a: a["ctr"]) if by_ctr
        else None
    )
    worst_creative = (
        min(by_roas, key=lambda a: a["roas"]) if by_roas
        else min(by_ctr, key=lambda a: a["ctr"]) if by_ctr
        else None
    )

    new_creative_count = 0
    if insights_30d:
        dates = sorted({r.get("date_start") for r in insights_30d if r.get("date_start")})
        if len(dates) >= 7:
            cutoff = dates[-7]
            first_seen: dict = {}
            for r in insights_30d:
                aid = r.get("ad_id")
                d = r.get("date_start")
                if aid and d:
                    if aid not in first_seen or d < first_seen[aid]:
                        first_seen[aid] = d
            new_creative_count = sum(1 for d in first_seen.values() if d >= cutoff)

    # Creative action buckets derived from fatigue_status (not CTR alone)
    _rankable = [a for a in ads_with_spend if a["spend"] >= _MIN_SPEND_FOR_RANKING]
    creative_actions = {
        "refresh_immediately": [a for a in _rankable if a.get("fatigue_status") == "REFRESH_NOW"][:5],
        "refresh_soon":        [a for a in _rankable if a.get("fatigue_status") == "REFRESH_SOON"][:5],
        "monitor":             [a for a in _rankable if a.get("fatigue_status") == "MONITOR_ENGAGEMENT"][:5],
        "high_frequency":      [a for a in _rankable if a.get("fatigue_status") == "HIGH_FREQUENCY"][:5],
    }

    return {
        "top_creatives": ads_with_spend[:10],
        "top_creative": top_creative,
        "worst_creative": worst_creative,
        "fatigued": sorted(
            [a for a in _rankable if a["is_fatigued"]],
            key=lambda a: a["spend"],
            reverse=True,
        )[:15],
        "monitor_engagement": sorted(
            [a for a in _rankable if a.get("fatigue_status") == "MONITOR_ENGAGEMENT"],
            key=lambda a: a["spend"],
            reverse=True,
        )[:10],
        "high_frequency_ads": sorted(
            [a for a in _rankable if a.get("fatigue_status") == "HIGH_FREQUENCY"],
            key=lambda a: a["spend"],
            reverse=True,
        )[:10],
        "new_creative_count": new_creative_count,
        "total_with_spend": len(ads_with_spend),
        "creative_actions": creative_actions,
    }


# ---------------------------------------------------------------------------
# Campaign intelligence
# ---------------------------------------------------------------------------

_CAMPAIGN_TYPE_SIGNALS = [
    ("Catalog (DPA)",  ["dpa", "catalog", "dynamic product", "dynamic ad"]),
    ("Retargeting",    ["retarget", "retention", "remarket", "remarketing", "rtg", "website visitor"]),
    ("Testing",        ["test", "testing", "creative test", "crt", "experiment"]),
    ("Prospecting",    ["prospect", "prosp", "acquisition", "cold"]),
]


def _classify_campaign(name: str, campaign_ads: list) -> str:
    name_lower = name.lower()
    for campaign_type, keywords in _CAMPAIGN_TYPE_SIGNALS:
        if any(k in name_lower for k in keywords):
            return campaign_type
    if any(ad.get("creative", {}).get("product_catalog_id") for ad in campaign_ads):
        return "Catalog (DPA)"
    return "Unknown"


def campaign_intelligence(campaign_rows: list, ads: list) -> dict:
    ads_by_campaign = _group_by(ads, "campaign_id")
    total_spend = sum(c.get("spend") or 0 for c in campaign_rows)

    classified = []
    for c in campaign_rows:
        campaign_ads = ads_by_campaign.get(c.get("id"), [])
        classified.append({**c, "type": _classify_campaign(c.get("name", ""), campaign_ads)})

    types = ["Prospecting", "Retargeting", "Catalog (DPA)", "Testing", "Unknown"]
    distribution = {}
    for t in types:
        group = [c for c in classified if c["type"] == t]
        spend = sum(c.get("spend") or 0 for c in group)
        distribution[t] = {
            "campaigns": len(group),
            "active": sum(1 for c in group if c.get("status") == "ACTIVE"),
            "spend": round(spend, 2),
            "spend_pct": round(spend / total_spend * 100, 1) if total_spend else 0,
        }

    return {
        "classified_campaigns": classified,
        "distribution": distribution,
        "total_spend": round(total_spend, 2),
    }


# ---------------------------------------------------------------------------
# Campaign performance filtering
# ---------------------------------------------------------------------------

def filter_campaign_performance(trends: dict, priorities: dict, max_shown: int = 12) -> dict:
    """
    Returns top campaigns by spend, always including any Fix/Watch/Scale campaigns.
    Caps output at max_shown rows.
    """
    by_campaign = trends.get("by_campaign", {})
    active = [t for t in by_campaign.values() if (t.get("spend_yesterday") or 0) > 0]
    active.sort(key=lambda t: t.get("spend_yesterday") or 0, reverse=True)

    priority_names: set = set()
    for bucket in ("fix", "watch", "scale"):
        for item in priorities.get(bucket, []):
            if item.get("entity_type") == "campaign":
                priority_names.add(item.get("entity_name", ""))

    shown = []
    seen_names: set = set()

    # Always include priority campaigns first
    for t in active:
        name = t.get("campaign_name", "")
        if name in priority_names:
            shown.append(t)
            seen_names.add(name)

    # Fill remaining slots with top spenders
    for t in active:
        if len(shown) >= max_shown:
            break
        name = t.get("campaign_name", "")
        if name not in seen_names:
            shown.append(t)
            seen_names.add(name)

    shown.sort(key=lambda t: t.get("spend_yesterday") or 0, reverse=True)
    return {"rows": shown, "total": len(active), "shown": len(shown)}


# ---------------------------------------------------------------------------
# Budget allocation plan
# ---------------------------------------------------------------------------

def build_budget_plan(
    trends: dict,
    c_rows: list,
    adset_rows: list,
    cr_rows: list,
) -> dict:
    """
    Rule-based budget allocation recommendations at campaign level.

    Increase: strong positive efficiency signal + confidence check (purchases >= 3
              OR spend >= $150 OR clicks >= 200), no high frequency.
    Reduce:   clear efficiency deterioration, spend >= $75.
    Hold:     meaningful spend with no strong signal, or signal without enough confidence.

    Budget cap detection: flags campaigns spending at/near their CBO daily_budget
    while showing positive ROAS vs prior week.

    Each bucket sorted by signal_strength × spend, capped at 3 items.
    Item keys: campaign_name, reason, metrics, change_pct, spend_yesterday,
               budget_delta, budget_capped.
    Return also includes: total_increase_delta, total_reduce_delta, net_delta.
    """
    by_campaign = trends.get("by_campaign", {})
    campaign_freq = _compute_campaign_frequency(cr_rows, adset_rows)

    # Build daily_budget lookup (CBO campaigns only; value in cents → convert to dollars)
    campaign_daily_budget: dict = {}
    campaign_bid_strategy: dict = {}
    for c in c_rows:
        raw = c.get("daily_budget")
        if raw:
            try:
                campaign_daily_budget[c["id"]] = int(raw) / 100.0
            except (ValueError, TypeError):
                pass
        if c.get("bid_strategy"):
            campaign_bid_strategy[c["id"]] = c["bid_strategy"]

    # Build adset lookup: campaign_id -> list of adset dicts with spend
    # Used to find the correct adset entity for non-CBO budget recommendations
    adsets_by_campaign: dict = {}
    for a in adset_rows:
        cid_a = a.get("campaign_id")
        if cid_a:
            adsets_by_campaign.setdefault(cid_a, []).append(a)

    increase_items: list = []
    reduce_items: list = []
    hold_items: list = []
    classified: set = set()

    # Profitability lookup for budget gating
    _econ_by_id = {r.get("id"): r for r in c_rows if r.get("id")}

    for cid, t in by_campaign.items():
        spend_yday = t.get("spend_yesterday") or 0
        impressions_yday = t.get("impressions_yesterday") or 0
        clicks_yday = t.get("clicks_yesterday") or 0
        purchases_yday = t.get("purchases_yesterday") or 0
        name = t.get("campaign_name", "")

        cpa_yday = t.get("cpa_yesterday")
        cpa_7d = t.get("cpa_7d_avg")
        cpa_chg = t.get("cpa_change_pct")
        roas_yday = t.get("roas_yesterday")
        roas_7d = t.get("roas_7d_avg")
        roas_chg = t.get("roas_change_pct")
        ctr_chg = t.get("ctr_change_pct")
        cpc_chg = t.get("cpc_change_pct")
        spend_chg = t.get("spend_change_pct")
        freq = campaign_freq.get(cid)
        _econ_bp = _econ_by_id.get(cid, {})
        _verdict_bp = _econ_bp.get("verdict", "WATCH_ONLY")

        # Determine budget entity: CBO campaigns use campaign budget; others use adset budget
        _is_cbo = bool(campaign_daily_budget.get(cid))
        if _is_cbo:
            _entity_type = "campaign"
            _entity_id = cid
            _current_budget = campaign_daily_budget.get(cid)
        else:
            # Find highest-spend adset in this campaign
            _camp_adsets = sorted(
                adsets_by_campaign.get(cid, []),
                key=lambda a: a.get("spend") or 0,
                reverse=True,
            )
            _top_adset = _camp_adsets[0] if _camp_adsets else {}
            _entity_type = "adset"
            _entity_id = _top_adset.get("id")
            _adset_budget_raw = _top_adset.get("daily_budget")
            try:
                _current_budget = int(_adset_budget_raw) / 100.0 if _adset_budget_raw else None
            except (ValueError, TypeError):
                _current_budget = None

        # Budget cap detection: spending ≥ 90% of CBO daily budget while ROAS > 7d avg
        daily_budget = campaign_daily_budget.get(cid)
        budget_capped = bool(
            daily_budget
            and spend_yday >= daily_budget * 0.9
            and roas_chg is not None and roas_chg > 0
        )

        # Confidence check for Increase (at least one criterion)
        increase_confident = (
            purchases_yday >= 3
            or spend_yday >= 150
            or clicks_yday >= 200
        )

        # ---------------------------------------------------------------
        # INCREASE — gate on SCALE verdict
        # ---------------------------------------------------------------
        if _verdict_bp == "SCALE":
            change_pct = 0
            reason = ""
            score = 0.0

            # Very strong: ROAS >= 1.5x 7d avg with purchases
            if roas_chg is not None and roas_chg >= 50 and purchases_yday >= 2:
                change_pct = 20
                reason = f"ROAS +{roas_chg:.0f}% vs prior week — very strong efficiency"
                score = roas_chg * spend_yday
            # Very strong: CPA <= 0.60x 7d avg with volume
            elif cpa_chg is not None and cpa_chg <= -40 and purchases_yday >= 3:
                change_pct = 20
                reason = f"CPA {-cpa_chg:.0f}% below 7-day avg on {purchases_yday} purchases"
                score = abs(cpa_chg) * spend_yday
            # Clearly strong: ROAS >= 1.3x
            elif roas_chg is not None and roas_chg >= 30 and purchases_yday >= 2:
                change_pct = 15
                reason = f"ROAS +{roas_chg:.0f}% vs prior week — clearly stronger"
                score = roas_chg * spend_yday
            # Clearly strong: CPA <= 0.75x
            elif cpa_chg is not None and cpa_chg <= -25 and purchases_yday >= 2:
                change_pct = 15
                reason = f"CPA {-cpa_chg:.0f}% below 7-day avg on {purchases_yday} purchases"
                score = abs(cpa_chg) * spend_yday
            # Mildly strong: CTR >= 1.25x with stable CPC
            elif (
                ctr_chg is not None and ctr_chg >= 25
                and (cpc_chg is None or cpc_chg <= 10)
            ):
                change_pct = 10
                reason = f"CTR +{ctr_chg:.0f}% vs prior week with stable CPC"
                score = ctr_chg * spend_yday
            # Mildly strong: ROAS up 10–30%
            elif roas_chg is not None and 10 <= roas_chg < 30:
                change_pct = 10
                reason = f"ROAS +{roas_chg:.0f}% vs prior week — mild improvement"
                score = roas_chg * spend_yday
            # Mildly strong: CPA down 10–25% with purchases
            elif cpa_chg is not None and -25 < cpa_chg <= -10 and purchases_yday >= 2:
                change_pct = 10
                reason = f"CPA {-cpa_chg:.0f}% below 7-day avg — mild efficiency gain"
                score = abs(cpa_chg) * spend_yday

            if change_pct > 0:
                metrics_parts = [f"Spend ${spend_yday:.0f}"]
                if roas_yday is not None:
                    roas_7d_str = f" vs {roas_7d:.2f}x avg" if roas_7d else ""
                    metrics_parts.append(f"ROAS {roas_yday:.2f}x{roas_7d_str}")
                if cpa_yday is not None:
                    cpa_7d_str = f" vs ${cpa_7d:.0f} avg" if cpa_7d else ""
                    metrics_parts.append(f"CPA ${cpa_yday:.0f}{cpa_7d_str}")
                if purchases_yday:
                    metrics_parts.append(f"{purchases_yday} purchases")

                if increase_confident:
                    increase_items.append({
                        "campaign_name": name,
                        "campaign_id": cid,
                        "bid_strategy": campaign_bid_strategy.get(cid, ""),
                        "entity_type": _entity_type,
                        "entity_id": _entity_id,
                        "current_budget": _current_budget,
                        "reason": reason,
                        "metrics": " | ".join(metrics_parts),
                        "change_pct": change_pct,
                        "budget_delta": round(spend_yday * change_pct / 100.0),
                        "budget_capped": budget_capped,
                        "spend_yesterday": spend_yday,
                        "purchases_yesterday": purchases_yday,
                        "confidence": _confidence_level(spend_yday, purchases_yday, change_pct >= 30),
                        "trigger_rule": {
                            "primary_metric": "roas_change_pct" if roas_chg is not None else "cpa_change_pct",
                            "roas_change_pct": roas_chg,
                            "cpa_change_pct": cpa_chg,
                            "ctr_change_pct": ctr_chg,
                            "min_spend_threshold": 50,
                            "confidence_threshold": "purchases >= 3 OR spend >= 150 OR clicks >= 200",
                        },
                        "_score": score,
                    })
                    classified.add(cid)
                else:
                    # Signal present but insufficient data confidence → Hold
                    hold_items.append({
                        "campaign_name": name,
                        "reason": f"Positive signal ({reason.split('—')[0].strip()}) but low data volume — monitoring",
                        "metrics": " | ".join(metrics_parts),
                        "change_pct": 0,
                        "budget_delta": 0,
                        "budget_capped": budget_capped,
                        "spend_yesterday": spend_yday,
                        "_score": spend_yday,
                    })
                    classified.add(cid)

        # ---------------------------------------------------------------
        # REDUCE — gate on REDUCE/PAUSE verdict
        # ---------------------------------------------------------------
        if cid not in classified and spend_yday >= 75:
            # Gate on verdict
            _skip_reduce = _verdict_bp not in ("REDUCE", "PAUSE")

            change_pct = 0
            reason = ""
            score = 0.0

            if not _skip_reduce:
                # Strong: CPA > 1.6x prior week
                if cpa_chg is not None and cpa_chg > 60:
                    change_pct = 20
                    reason = f"CPA +{cpa_chg:.0f}% vs prior week — serious efficiency drop"
                    score = cpa_chg * spend_yday
                # Strong: ROAS < 0.60x prior week
                elif roas_chg is not None and roas_chg < -40:
                    change_pct = 20
                    reason = f"ROAS {roas_chg:.0f}% vs prior week — significant decline"
                    score = abs(roas_chg) * spend_yday
                # Meaningful: CPA > 1.4x prior week
                elif cpa_chg is not None and 40 <= cpa_chg <= 60:
                    change_pct = 15
                    reason = f"CPA +{cpa_chg:.0f}% vs prior week — efficiency deteriorating"
                    score = cpa_chg * spend_yday
                # Meaningful: ROAS < 0.75x prior week
                elif roas_chg is not None and -40 <= roas_chg < -25:
                    change_pct = 15
                    reason = f"ROAS {roas_chg:.0f}% vs prior week — declining"
                    score = abs(roas_chg) * spend_yday
                # Mild: CTR down > 30% with volume
                elif (
                    ctr_chg is not None and ctr_chg < -30
                    and impressions_yday >= 1000
                ):
                    change_pct = 10
                    reason = f"CTR {ctr_chg:.0f}% vs prior week on meaningful volume"
                    score = abs(ctr_chg) * spend_yday
                # Mild: spend spike with worsening efficiency
                elif (
                    spend_chg is not None and spend_chg > 50
                    and (
                        (cpa_chg is not None and cpa_chg > 20)
                        or (ctr_chg is not None and ctr_chg < -20)
                    )
                ):
                    change_pct = 10
                    reason = f"Spend +{spend_chg:.0f}% vs avg while efficiency worsened"
                    score = spend_chg * spend_yday

            if change_pct > 0:
                metrics_parts = [f"Spend ${spend_yday:.0f}"]
                if roas_yday is not None and roas_7d is not None:
                    metrics_parts.append(f"ROAS {roas_yday:.2f}x vs {roas_7d:.2f}x avg")
                if cpa_yday is not None and cpa_7d is not None:
                    metrics_parts.append(f"CPA ${cpa_yday:.0f} vs ${cpa_7d:.0f} avg")
                reduce_items.append({
                    "campaign_name": name,
                    "campaign_id": cid,
                    "bid_strategy": campaign_bid_strategy.get(cid, ""),
                    "entity_type": _entity_type,
                    "entity_id": _entity_id,
                    "current_budget": _current_budget,
                    "reason": reason,
                    "metrics": " | ".join(metrics_parts),
                    "change_pct": -change_pct,
                    "budget_delta": -round(spend_yday * change_pct / 100.0),
                    "budget_capped": False,
                    "spend_yesterday": spend_yday,
                    "purchases_yesterday": purchases_yday,
                    "confidence": _confidence_level(spend_yday, purchases_yday, change_pct >= 40),
                    "trigger_rule": {
                        "primary_metric": "cpa_change_pct" if cpa_chg is not None else "roas_change_pct",
                        "roas_change_pct": roas_chg,
                        "cpa_change_pct": cpa_chg,
                        "ctr_change_pct": ctr_chg,
                        "min_spend_threshold": 75,
                        "impressions_threshold": 1000 if (ctr_chg is not None and ctr_chg < -30) else None,
                    },
                    "_score": score,
                })
                classified.add(cid)

        # ---------------------------------------------------------------
        # HOLD — meaningful spend, no strong directional signal
        # ---------------------------------------------------------------
        if cid not in classified and spend_yday >= 50 and _verdict_bp in ("HOLD", "MONITOR", "CREATIVE_REFRESH", "INVESTIGATE"):
            if cpa_chg is not None and abs(cpa_chg) < 15:
                hold_reason = "Performance stable vs prior week"
            elif roas_chg is not None and abs(roas_chg) < 15:
                hold_reason = "ROAS stable vs prior week"
            elif cpa_chg is None and roas_chg is None:
                hold_reason = "No 7-day historical comparison available yet"
            else:
                hold_reason = "Mixed signals — no clear action"

            metrics_parts = [f"Spend ${spend_yday:.0f}"]
            if cpa_yday is not None and cpa_7d is not None:
                metrics_parts.append(f"CPA ${cpa_yday:.0f} vs ${cpa_7d:.0f} avg")
            elif roas_yday is not None:
                roas_7d_str = f" vs {roas_7d:.2f}x avg" if roas_7d else ""
                metrics_parts.append(f"ROAS {roas_yday:.2f}x{roas_7d_str}")

            hold_items.append({
                "campaign_name": name,
                "reason": hold_reason,
                "metrics": " | ".join(metrics_parts),
                "change_pct": 0,
                "budget_delta": 0,
                "budget_capped": budget_capped,
                "spend_yesterday": spend_yday,
                "_score": spend_yday,
            })

    # Sort and cap each bucket
    increase_items.sort(key=lambda x: x["_score"], reverse=True)
    reduce_items.sort(key=lambda x: x["_score"], reverse=True)
    hold_items.sort(key=lambda x: x["_score"], reverse=True)

    def _strip(items):
        for item in items:
            item.pop("_score", None)
        return items

    increase_final = _strip(increase_items[:3])
    reduce_final = _strip(reduce_items[:3])
    hold_final = _strip(hold_items[:3])

    total_increase = sum(i["budget_delta"] for i in increase_final)
    total_reduce = sum(i["budget_delta"] for i in reduce_final)  # already negative

    return {
        "increase": increase_final,
        "reduce": reduce_final,
        "hold": hold_final,
        "total_increase_delta": round(total_increase),
        "total_reduce_delta": round(total_reduce),
        "net_delta": round(total_increase + total_reduce),
    }


# ---------------------------------------------------------------------------
# Proposed changes
# ---------------------------------------------------------------------------

def build_proposed_changes(
    budget_plan: dict,
    priorities: dict,
    creative_intelligence: dict,
    trends: dict,
) -> dict:
    """
    Consolidates the highest-confidence, approval-ready actions into one section.

    Increase / Reduce: pulled directly from budget_plan (already ranked).
    Pause candidates:  Fix-bucket campaigns with CPA or ROAS deterioration and
                       spend >= $50. Phrased conservatively.
    Creative refresh:  Top fatigued ads by spend, supplemented by campaign-level
                       CTR drops if fewer than 3 ad-level items.

    Each bucket capped at 3 items.
    Item keys: name, action, reason, entity_type.
    """
    # --- Increase: pull straight from budget plan ---
    increase = []
    for item in budget_plan.get("increase", [])[:3]:
        delta = item.get("budget_delta", 0)
        chg = item.get("change_pct", 0)
        increase.append({
            "name": item["campaign_name"],
            "action": f"+{chg}% (+${int(delta)})",
            "reason": item.get("metrics", ""),
            "entity_type": "campaign",
            "confidence": item.get("confidence", "MEDIUM"),
        })

    # --- Reduce: pull straight from budget plan ---
    reduce = []
    for item in budget_plan.get("reduce", [])[:3]:
        delta = item.get("budget_delta", 0)  # already negative
        chg = item.get("change_pct", 0)
        reduce.append({
            "name": item["campaign_name"],
            "action": f"{chg}% (-${abs(int(delta))})",
            "reason": item.get("metrics", ""),
            "entity_type": "campaign",
            "confidence": item.get("confidence", "MEDIUM"),
        })

    # --- Pause candidates: Fix items with PAUSE verdict, spend >= $50 ---
    _PAUSE_MIN_SPEND = 50.0
    pause = []
    for item in priorities.get("fix", []):
        if len(pause) >= 3:
            break
        if item.get("entity_type") != "campaign":
            continue
        if item.get("spend_impact", 0) < _PAUSE_MIN_SPEND:
            continue
        _item_verdict = item.get("verdict")
        if _item_verdict != "PAUSE":
            continue
        reason_text = item.get("reason", "")
        metrics = item.get("metrics", "")
        pause.append({
            "name": item["entity_name"],
            "entity_id": item.get("entity_id"),
            "campaign_id": item.get("campaign_id"),
            "action": "Reduce budget to minimum or pause if underperformance continues for 2+ more days",
            "reason": f"{reason_text} | {metrics}" if metrics else reason_text,
            "entity_type": "campaign",
            "trigger_rule": {
                "condition": "verdict == PAUSE: CRITICAL + MEDIUM/HIGH confidence + persistence confirmed",
                "min_spend_threshold": 50,
                "confirmation": "prior-period WORSENING or STABLE at CRITICAL level",
            },
        })

    # --- Creative refresh: fatigued ads first, then campaign CTR drops ---
    creative = []
    seen_names: set = set()

    for ad in creative_intelligence.get("fatigued", []):
        if len(creative) >= 3:
            break
        name = ad.get("name", "")
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        parts = []
        freq = ad.get("frequency")
        ctr_chg = ad.get("ctr_change_pct")
        spend = ad.get("spend", 0)
        if freq:
            parts.append(f"Freq {freq:.1f}x")
        if ctr_chg is not None:
            parts.append(f"CTR {ctr_chg:+.0f}% vs avg")
        parts.append(f"Spend ${spend:.0f}")
        creative.append({
            "name": name,
            "action": "Refresh or rotate creative",
            "reason": " | ".join(parts),
            "entity_type": "ad",
        })

    # Supplement with campaign-level CTR drops
    if len(creative) < 3:
        by_campaign = trends.get("by_campaign", {})
        ctr_drops = sorted(
            [
                t for t in by_campaign.values()
                if (
                    t.get("ctr_change_pct") is not None
                    and t.get("ctr_change_pct") < -30
                    and (t.get("impressions_yesterday") or 0) >= 1000
                    and t.get("campaign_name", "") not in seen_names
                )
            ],
            key=lambda t: t.get("spend_yesterday") or 0,
            reverse=True,
        )
        for t in ctr_drops:
            if len(creative) >= 3:
                break
            name = t.get("campaign_name", "")
            seen_names.add(name)
            ctr_chg = t.get("ctr_change_pct")
            spend = t.get("spend_yesterday", 0)
            creative.append({
                "name": name,
                "action": "Refresh creatives in this campaign",
                "reason": f"CTR {ctr_chg:+.0f}% vs prior week | Spend ${spend:.0f}",
                "entity_type": "campaign",
            })

    return {
        "increase": increase,
        "reduce": reduce,
        "pause_candidates": pause,
        "creative_refresh": creative,
    }


# ---------------------------------------------------------------------------
# Account changes detection
# ---------------------------------------------------------------------------

_PERF_CHANGE_THRESHOLD = 15.0  # minimum % change to report


def build_account_changes(
    campaigns: list,
    adsets: list,
    ads: list,
    insights: list,
) -> dict:
    """Compare today's snapshots against the previous day's to detect changes."""
    prev_campaigns = _load_prev("campaigns")
    prev_adsets = _load_prev("adsets")
    prev_ads = _load_prev("ads")
    prev_insights = _load_prev("insights")

    if not prev_campaigns and not prev_adsets and not prev_ads and not prev_insights:
        return {"available": False}

    prev_camp_by_id = {c["id"]: c for c in prev_campaigns}
    curr_camp_by_id = {c["id"]: c for c in campaigns}
    prev_adset_by_id = {a["id"]: a for a in prev_adsets}
    curr_adset_by_id = {a["id"]: a for a in adsets}
    prev_ad_by_id = {a["id"]: a for a in prev_ads}
    curr_ad_by_id = {a["id"]: a for a in ads}

    # Campaign structural changes
    campaigns_activated = []
    campaigns_paused = []
    for cid, curr in curr_camp_by_id.items():
        prev = prev_camp_by_id.get(cid)
        curr_status = curr.get("status")
        prev_status = prev.get("status") if prev else None
        if prev is None:
            if curr_status == "ACTIVE":
                campaigns_activated.append(curr.get("name", cid))
        elif prev_status != "ACTIVE" and curr_status == "ACTIVE":
            campaigns_activated.append(curr.get("name", cid))
        elif prev_status == "ACTIVE" and curr_status == "PAUSED":
            campaigns_paused.append(curr.get("name", cid))

    # Ad set structural changes
    adsets_activated = []
    adsets_paused = []
    for aid, curr in curr_adset_by_id.items():
        prev = prev_adset_by_id.get(aid)
        curr_status = curr.get("status")
        prev_status = prev.get("status") if prev else None
        if prev is None:
            if curr_status == "ACTIVE":
                adsets_activated.append(curr.get("name", aid))
        elif prev_status != "ACTIVE" and curr_status == "ACTIVE":
            adsets_activated.append(curr.get("name", aid))
        elif prev_status == "ACTIVE" and curr_status == "PAUSED":
            adsets_paused.append(curr.get("name", aid))

    # Creative changes
    new_ads = []
    ads_paused = []
    for ad_id, curr in curr_ad_by_id.items():
        prev = prev_ad_by_id.get(ad_id)
        curr_status = curr.get("status")
        prev_status = prev.get("status") if prev else None
        if prev is None:
            new_ads.append(curr.get("name", ad_id))
        elif prev_status == "ACTIVE" and curr_status == "PAUSED":
            ads_paused.append(curr.get("name", ad_id))

    # Account-level performance comparison
    def _sum_account(rows: list) -> dict:
        spend = sum(_float(r.get("spend")) for r in rows)
        impressions = sum(_float(r.get("impressions")) for r in rows)
        clicks = sum(_float(r.get("clicks")) for r in rows)
        purchases = sum(_action_value(r.get("actions", []), "purchase") for r in rows)
        revenue = sum(_action_value(r.get("action_values", []), "purchase") for r in rows)
        return {
            "spend": spend,
            "roas": round(revenue / spend, 2) if spend and revenue else None,
            "cpa": round(spend / purchases, 2) if purchases else None,
            "ctr": round(clicks / impressions * 100, 2) if impressions else None,
        }

    curr_perf = _sum_account(insights)
    prev_perf = _sum_account(prev_insights) if prev_insights else {}

    perf_changes = []
    metric_config = [
        ("spend", "Spend", lambda v: f"${v:,.0f}", "increased", "decreased"),
        ("roas", "ROAS", lambda v: f"{v:.2f}x", "improved", "declined"),
        ("cpa", "CPA", lambda v: f"${v:.2f}", "declined", "improved"),
        ("ctr", "CTR", lambda v: f"{v:.2f}%", "improved", "declined"),
    ]
    for metric, label, fmt, pos_word, neg_word in metric_config:
        curr_val = curr_perf.get(metric)
        prev_val = prev_perf.get(metric)
        if curr_val is None or prev_val is None or prev_val == 0:
            continue
        pct = (curr_val - prev_val) / prev_val * 100
        if abs(pct) >= _PERF_CHANGE_THRESHOLD:
            direction = pos_word if pct > 0 else neg_word
            # CPA is "good" when down, "bad" when up — swap
            if metric == "cpa":
                direction = neg_word if pct > 0 else pos_word
            perf_changes.append({
                "metric": label,
                "direction": direction,
                "prev_fmt": fmt(prev_val),
                "curr_fmt": fmt(curr_val),
                "pct": round(pct, 1),
            })

    return {
        "available": True,
        "campaigns_activated": campaigns_activated,
        "campaigns_paused": campaigns_paused,
        "adsets_activated": adsets_activated,
        "adsets_paused": adsets_paused,
        "new_ads": new_ads,
        "ads_paused": ads_paused,
        "perf_changes": perf_changes,
    }


# ---------------------------------------------------------------------------
# Account momentum (3-day trend)
# ---------------------------------------------------------------------------

def build_account_momentum() -> dict:
    """Compute 3-day account-level spend/ROAS/CPA trend from daily insight snapshots."""
    files = sorted(RAW_DIR.glob("insights_????-??-??.json"), reverse=True)
    if len(files) < 2:
        return {"available": False}

    snapshots = []
    for f in files[:3]:
        snapshots.append(json.loads(f.read_text()))
    snapshots.reverse()  # oldest first → newest last

    def _perf(rows: list) -> dict:
        spend = sum(_float(r.get("spend")) for r in rows)
        purchases = sum(_action_value(r.get("actions", []), "purchase") for r in rows)
        revenue = sum(_action_value(r.get("action_values", []), "purchase") for r in rows)
        return {
            "spend": spend,
            "roas": round(revenue / spend, 2) if spend and revenue else None,
            "cpa": round(spend / purchases, 2) if purchases else None,
        }

    perfs = [_perf(rows) for rows in snapshots]

    def _trend(values, fmt_fn: object) -> str:
        return " → ".join(fmt_fn(v) if v is not None else "N/A" for v in values)

    def _direction(old_val, new_val, higher_is_better: bool = True) -> str:
        if old_val is None or new_val is None or old_val == 0:
            return "flat"
        pct = (new_val - old_val) / old_val * 100
        if abs(pct) < 5:
            return "flat"
        improving = pct > 0 if higher_is_better else pct < 0
        return "improving" if improving else "worsening"

    oldest = perfs[0]
    newest = perfs[-1]
    prev_day = perfs[-2] if len(perfs) >= 2 else None  # day immediately before latest

    return {
        "available": True,
        "days": len(perfs),
        "spend_trend": _trend([p["spend"] for p in perfs], lambda v: f"${v:,.0f}"),
        "roas_trend": _trend([p["roas"] for p in perfs], lambda v: f"{v:.2f}"),
        "cpa_trend": _trend([p["cpa"] for p in perfs], lambda v: f"${v:.0f}"),
        "roas_direction": _direction(oldest["roas"], newest["roas"], higher_is_better=True),
        "cpa_direction": _direction(oldest["cpa"], newest["cpa"], higher_is_better=False),
        "spend_direction": (
            "increasing" if newest["spend"] > oldest["spend"] * 1.05
            else "decreasing" if newest["spend"] < oldest["spend"] * 0.95
            else "flat"
        ),
        "roas_latest": newest["roas"],
        "roas_prev": oldest["roas"],
        "cpa_latest": newest["cpa"],
        "cpa_prev": oldest["cpa"],
        "spend_latest": newest["spend"],
        # Day-over-day (latest vs the day immediately before it)
        "roas_prev_day": prev_day["roas"] if prev_day else None,
        "cpa_prev_day": prev_day["cpa"] if prev_day else None,
        "roas_dod_direction": _direction(prev_day["roas"] if prev_day else None, newest["roas"], higher_is_better=True),
        "cpa_dod_direction": _direction(prev_day["cpa"] if prev_day else None, newest["cpa"], higher_is_better=False),
    }


# ---------------------------------------------------------------------------
# Spend concentration
# ---------------------------------------------------------------------------

def build_spend_concentration(c_rows: list) -> dict:
    """How concentrated is spend across campaigns? Returns top-3 and top-5 percentages."""
    by_spend = sorted(
        [(c.get("name", ""), c.get("spend") or 0) for c in c_rows],
        key=lambda x: x[1],
        reverse=True,
    )
    total = sum(s for _, s in by_spend)
    if not total:
        return {"available": False}

    top3 = sum(s for _, s in by_spend[:3])
    top5 = sum(s for _, s in by_spend[:5])

    top3_pct = round(top3 / total * 100, 1)
    if top3_pct <= 50:
        interpretation = "healthy"
    elif top3_pct <= 60:
        interpretation = "moderate concentration"
    else:
        interpretation = "high concentration risk"

    return {
        "available": True,
        "top_3_spend_pct": top3_pct,
        "top_5_spend_pct": round(top5 / total * 100, 1),
        "top_3_concentrated": top3_pct > 60,
        "top_campaigns": [{"name": n, "spend": round(s, 2)} for n, s in by_spend[:5]],
        "interpretation": interpretation,
    }


# ---------------------------------------------------------------------------
# Campaign efficiency map
# ---------------------------------------------------------------------------

def build_efficiency_map(c_rows: list, trends: dict) -> dict:
    """
    Top 10 campaigns by spend with unified verdict status.
    Uses the verdict already attached by classify_campaign() rather than recomputing
    an independent ROAS/CPA comparison that would conflict with the shared verdict engine.
    """
    rows = []
    for c in sorted(c_rows, key=lambda x: x.get("spend") or 0, reverse=True)[:10]:
        rows.append({
            "name": c.get("name", ""),
            "spend": c.get("spend") or 0,
            "roas": c.get("roas"),
            "cpa": c.get("cpa"),
            "status": c.get("verdict", "WATCH_ONLY"),
            # profitability fields (populated by enrich_campaigns_with_economics)
            "breakeven_roas": c.get("breakeven_roas"),
            "profitability_status": c.get("profitability_status", "UNKNOWN"),
            "vs_7dc_target_pct": c.get("vs_7dc_target_pct"),
        })

    return {"rows": rows}


# ---------------------------------------------------------------------------
# Efficiency leakage (wasted spend)
# ---------------------------------------------------------------------------

def build_efficiency_leakage(c_rows: list, trends: dict) -> dict:
    """
    Shows unprofitable vs profitable spend using unit economics when available.
    Falls back to 7-day-average comparison when economics are absent.
    """
    name_to_trend = {
        t.get("campaign_name"): t
        for t in trends.get("by_campaign", {}).values()
        if t.get("campaign_name")
    }
    total_spend = sum(c.get("spend") or 0 for c in c_rows)

    unprofitable = []
    profitable = []

    for c in c_rows:
        name = c.get("name", "")
        spend = c.get("spend") or 0
        if spend == 0:
            continue

        roas = c.get("roas")
        cpa = c.get("cpa")
        pl_status = c.get("profitability_status", "UNKNOWN")
        estimated_profit = c.get("estimated_daily_profit")
        breakeven = c.get("breakeven_roas")
        product_key = c.get("product_key", "UNKNOWN")
        t = name_to_trend.get(name, {})
        roas_7d = t.get("roas_7d_avg")
        cpa_7d = t.get("cpa_7d_avg")

        if pl_status in ("UNPROFITABLE", "CRITICAL"):
            # BREAKEVEN is NOT leakage — it's spending at cost.
            # Only campaigns genuinely below breakeven are "at-risk."
            reason = ""
            if breakeven and roas:
                reason = f"ROAS {roas:.2f}x vs breakeven {breakeven:.2f}x"
            elif roas and roas_7d and roas < roas_7d * 0.8:
                reason = f"ROAS {roas:.2f}x vs {roas_7d:.2f}x avg"
            elif cpa and cpa_7d and cpa > cpa_7d * 1.5:
                reason = f"CPA ${cpa:.2f} vs ${cpa_7d:.2f} avg"
            else:
                reason = f"Below breakeven ({pl_status.lower()})"
            unprofitable.append({
                "name": name,
                "spend": spend,
                "reason": reason,
                "estimated_daily_profit": estimated_profit,
                "product_key": product_key,
            })
        elif pl_status in ("PROFITABLE", "ON_TARGET"):
            profitable.append({
                "name": name,
                "spend": spend,
                "estimated_daily_profit": estimated_profit,
                "product_key": product_key,
            })
        elif pl_status == "BREAKEVEN":
            # Spending at cost — not profitable, not a loss. Tracked separately.
            unprofitable.append({
                "name": name,
                "spend": spend,
                "reason": f"Breakeven — ROAS {roas:.2f}x vs breakeven {breakeven:.2f}x" if (roas and breakeven) else "Near breakeven",
                "estimated_daily_profit": estimated_profit,
                "product_key": product_key,
                "_is_breakeven": True,  # used downstream to separate from true at-risk
            })
        else:
            # UNKNOWN — fall back to 7d trend comparison only (no economics anchor)
            roas_under = roas is not None and roas_7d is not None and roas < roas_7d * 0.8
            cpa_over = cpa is not None and cpa_7d is not None and cpa > cpa_7d * 1.5
            if roas_under or cpa_over:
                reason = f"ROAS {roas:.2f}x vs {roas_7d:.2f}x avg" if roas_under else f"CPA ${cpa:.2f} vs ${cpa_7d:.2f} avg"
                unprofitable.append({
                    "name": name,
                    "spend": spend,
                    "reason": reason,
                    "estimated_daily_profit": estimated_profit,
                    "product_key": product_key,
                })

    unprofitable.sort(key=lambda x: x["spend"], reverse=True)
    profitable.sort(key=lambda x: x["spend"], reverse=True)

    total_unprofitable_spend = sum(u["spend"] for u in unprofitable)
    total_profitable_spend = sum(p["spend"] for p in profitable)
    unprofitable_pct = round(total_unprofitable_spend / total_spend * 100, 1) if total_spend else 0
    profitable_pct = round(total_profitable_spend / total_spend * 100, 1) if total_spend else 0

    # Sum estimated daily profit/loss
    estimated_daily_loss = sum(
        u["estimated_daily_profit"] for u in unprofitable
        if u.get("estimated_daily_profit") is not None
    )
    estimated_daily_profit = sum(
        p["estimated_daily_profit"] for p in profitable
        if p.get("estimated_daily_profit") is not None
    )

    # Separate true at-risk from near-breakeven (they have different response urgency)
    at_risk = [u for u in unprofitable if not u.get("_is_breakeven")]
    near_breakeven = [u for u in unprofitable if u.get("_is_breakeven")]
    at_risk_spend = sum(u["spend"] for u in at_risk)
    near_breakeven_spend = sum(u["spend"] for u in near_breakeven)
    at_risk_pct = round(at_risk_spend / total_spend * 100, 1) if total_spend else 0
    near_breakeven_pct = round(near_breakeven_spend / total_spend * 100, 1) if total_spend else 0

    return {
        "available": bool(at_risk),  # only "available" when there's genuine at-risk spend
        "total_unprofitable_spend": round(total_unprofitable_spend, 2),
        "total_profitable_spend": round(total_profitable_spend, 2),
        "at_risk_spend": round(at_risk_spend, 2),
        "near_breakeven_spend": round(near_breakeven_spend, 2),
        "unprofitable_pct": unprofitable_pct,
        "at_risk_pct": at_risk_pct,
        "near_breakeven_pct": near_breakeven_pct,
        "profitable_pct": profitable_pct,
        "estimated_period_loss": round(estimated_daily_loss, 2),    # renamed from daily to period
        "estimated_period_profit": round(estimated_daily_profit, 2),
        "at_risk_campaigns": at_risk[:5],
        "near_breakeven_campaigns": near_breakeven[:3],
        "profitable_campaigns": profitable[:3],
        # Backward compat aliases (used by existing report.py sections)
        "unprofitable_campaigns": unprofitable[:5],
        "total_wasted_spend": round(at_risk_spend, 2),  # was total_unprofitable_spend; now only true at-risk
        "pct_of_account_spend": at_risk_pct,            # same fix
        "top_contributors": at_risk[:3],
    }


# ---------------------------------------------------------------------------
# Decision summary
# ---------------------------------------------------------------------------

def build_decision_summary(budget_plan: dict, priorities: dict, ci: dict) -> dict:
    """
    Scannable top-level summary with Increase/Reduce/Pause/Creative buckets.
    Pulls from budget_plan (already confidence-labeled) and creative_actions.
    """
    increase = budget_plan.get("increase", [])[:3]
    reduce = budget_plan.get("reduce", [])[:3]

    # Pause: Fix campaigns with PAUSE verdict and spend >= $50
    pause = []
    for item in priorities.get("fix", []):
        if len(pause) >= 2:
            break
        if item.get("entity_type") != "campaign":
            continue
        if item.get("spend_impact", 0) < 50:
            continue
        _ds_verdict = item.get("verdict")
        if _ds_verdict != "PAUSE":
            continue
        pause.append({
            "campaign_name": item["entity_name"],
            "reason": item.get("reason", ""),
        })

    # Creative actions summary (count-based)
    creative_actions = ci.get("creative_actions", {})
    refresh_immediately_count = len(creative_actions.get("refresh_immediately", []))
    refresh_soon_count = len(creative_actions.get("refresh_soon", []))

    total_increase_delta = sum(item.get("budget_delta", 0) for item in increase)
    total_reduce_delta = sum(item.get("budget_delta", 0) for item in reduce)

    return {
        "increase": [
            {
                "campaign_name": item["campaign_name"],
                "bid_strategy": item.get("bid_strategy", ""),
                "change_pct": item.get("change_pct", 0),
                "budget_delta": item.get("budget_delta", 0),
                "confidence": item.get("confidence", "MEDIUM"),
                "metrics": item.get("metrics", ""),
            }
            for item in increase
        ],
        "reduce": [
            {
                "campaign_name": item["campaign_name"],
                "bid_strategy": item.get("bid_strategy", ""),
                "change_pct": item.get("change_pct", 0),
                "budget_delta": item.get("budget_delta", 0),
                "confidence": item.get("confidence", "MEDIUM"),
                "metrics": item.get("metrics", ""),
            }
            for item in reduce
        ],
        "pause": pause,
        "creative_refresh_immediately": refresh_immediately_count,
        "creative_refresh_soon": refresh_soon_count,
        "estimated_budget_shift": round(total_increase_delta + total_reduce_delta, 2),
        "total_increase_delta": round(total_increase_delta, 2),
        "total_reduce_delta": round(total_reduce_delta, 2),
    }


# ---------------------------------------------------------------------------
# Creative concept insights
# ---------------------------------------------------------------------------

_CREATIVE_CONCEPTS = [
    ("Longform",          ["longform", "long form"]),
    ("Avatar",            ["avatar", "ai avatar"]),
    ("Stills",            ["stills", "still", "socially native still"]),
    ("End Cards",         ["end card", "end cards"]),
    ("UGC",               ["ugc"]),
    ("Hooks/Curiosity",   ["curiosity", "hooks", "hook"]),
    ("Product/Screenshot", ["screenshot", "product screenshot"]),
    ("Flexible",          ["flexible"]),
]


def build_creative_concept_insights(ci: dict) -> dict:
    """
    Group ads by concept keyword found in ad name. Compute avg ROAS per group.
    Returns winning_concept, weak_concept, top/worst creative per concept.
    """
    top_creatives = ci.get("top_creatives", [])

    def _match_concept(name: str) -> Optional[str]:
        name_lower = name.lower()
        for concept, keywords in _CREATIVE_CONCEPTS:
            if any(k in name_lower for k in keywords):
                return concept
        return None

    groups: dict = defaultdict(list)
    for ad in top_creatives:
        concept = _match_concept(ad.get("name", ""))
        if concept:
            groups[concept].append(ad)

    if not groups:
        return {"available": False}

    concept_stats = []
    for concept, ads in groups.items():
        roas_vals = [a["roas"] for a in ads if a.get("roas") is not None]
        avg_roas = round(sum(roas_vals) / len(roas_vals), 2) if roas_vals else None
        total_spend = round(sum(a.get("spend", 0) for a in ads), 2)
        by_roas = [a for a in ads if a.get("roas") is not None]
        concept_stats.append({
            "concept": concept,
            "ad_count": len(ads),
            "avg_roas": avg_roas,
            "total_spend": total_spend,
            "top_ad": max(by_roas, key=lambda a: a["roas"]) if by_roas else None,
            "worst_ad": min(by_roas, key=lambda a: a["roas"]) if by_roas else None,
        })

    with_roas = [c for c in concept_stats if c["avg_roas"] is not None]
    winning = max(with_roas, key=lambda c: c["avg_roas"]) if with_roas else None
    weak = min(with_roas, key=lambda c: c["avg_roas"]) if len(with_roas) >= 2 else None

    return {
        "available": True,
        "concepts": concept_stats,
        "winning_concept": winning,
        "weak_concept": weak,
    }


# ---------------------------------------------------------------------------
# Creative lifecycle
# ---------------------------------------------------------------------------

def build_creative_lifecycle(ci: dict) -> dict:
    """
    Re-structures creative_intelligence into lifecycle counts for reporting.
    """
    creative_actions = ci.get("creative_actions", {})
    return {
        "new_count": ci.get("new_creative_count", 0),
        "with_spend": ci.get("total_with_spend", 0),
        "fatigued_count": len(ci.get("fatigued", [])),
        "refresh_immediately": len(creative_actions.get("refresh_immediately", [])),
        "refresh_soon": len(creative_actions.get("refresh_soon", [])),
        "monitor": len(creative_actions.get("monitor", [])),
    }


# ---------------------------------------------------------------------------
# Strategic signals
# ---------------------------------------------------------------------------

def build_strategic_signals(
    camp_intel: dict,
    sc: dict,
    el: dict,
    priorities: dict,
    ci: dict,
) -> list:
    """
    Returns 3-5 rule-based strategic insight bullet strings.
    """
    signals = []

    # Spend concentration signal
    if sc.get("available"):
        top3 = sc.get("top_3_spend_pct", 0)
        interp = sc.get("interpretation", "")
        if top3 > 60:
            signals.append(
                f"Spend concentration is high — top 3 campaigns absorb {top3}% of budget "
                f"({interp}). Diversification may reduce systemic risk."
            )

    # Efficiency leakage signal
    if el.get("available"):
        wasted_pct = el.get("pct_of_account_spend", 0)
        wasted_amt = el.get("total_wasted_spend", 0)
        if wasted_pct >= 15:
            signals.append(
                f"${wasted_amt:,.0f} ({wasted_pct}% of spend) is flowing to underperforming campaigns. "
                f"Reducing or pausing these would free up budget for scaling winners."
            )

    # Creative fatigue signal
    creative_actions = ci.get("creative_actions", {})
    refresh_count = len(creative_actions.get("refresh_immediately", []))
    if refresh_count > 0:
        signals.append(
            f"{refresh_count} ad{'s' if refresh_count != 1 else ''} showing >50% CTR decline — "
            f"immediate creative refresh recommended to prevent audience fatigue drag."
        )

    # Scale/Fix balance signal
    scale_count = len(priorities.get("scale", []))
    fix_count = len(priorities.get("fix", []))
    if scale_count > 0 and fix_count == 0:
        signals.append(
            f"{scale_count} scaling opportunit{'ies' if scale_count != 1 else 'y'} identified "
            f"with no critical Fix items. Account is in growth mode — increase budgets on Scale campaigns."
        )
    elif fix_count > scale_count and fix_count >= 3:
        signals.append(
            f"{fix_count} Fix items outnumber {scale_count} Scale items — "
            f"account efficiency is under pressure. Prioritize fixing underperformers before scaling."
        )

    # Campaign type distribution signal
    dist = camp_intel.get("distribution", {})
    unknown_pct = dist.get("Unknown", {}).get("spend_pct", 0)
    if unknown_pct > 25:
        signals.append(
            f"{unknown_pct}% of spend is in uncategorized campaigns. "
            f"Adding naming conventions (e.g., PROSP_, RTG_) will improve reporting and oversight."
        )

    return signals[:5]


# ---------------------------------------------------------------------------
# Strategy note (AI-style rule-based summary)
# ---------------------------------------------------------------------------

def build_strategy_note(
    account_momentum: dict,
    priorities: dict,
    el: dict,
    bp: dict,
) -> list:
    """
    Returns 3-5 sentence strings forming a short strategic narrative.
    """
    sentences = []

    # Sentence 1: Performance direction
    # Use day-over-day (latest vs previous day) not oldest-to-newest over 3 days,
    # so a recovering account is not described as "worsening."
    if account_momentum.get("available"):
        roas_dir = account_momentum.get("roas_dod_direction") or account_momentum.get("roas_direction", "flat")
        cpa_dir = account_momentum.get("cpa_dod_direction") or account_momentum.get("cpa_direction", "flat")
        roas_latest = account_momentum.get("roas_latest")
        cpa_latest = account_momentum.get("cpa_latest")
        roas_str = f"{roas_latest:.2f}x" if roas_latest else "N/A"
        cpa_str = f"${cpa_latest:.0f}" if cpa_latest else "N/A"

        if roas_dir == "improving" and cpa_dir == "improving":
            sentences.append(
                f"Account performance is trending positively — ROAS is {roas_str} and improving, "
                f"while CPA has dropped to {cpa_str}."
            )
        elif roas_dir == "worsening" or cpa_dir == "worsening":
            sentences.append(
                f"Performance signals are mixed or declining — ROAS is {roas_str} ({roas_dir}), "
                f"CPA is {cpa_str} ({cpa_dir}). Close attention to underperformers is warranted."
            )
        else:
            sentences.append(
                f"Account performance is holding steady — ROAS at {roas_str}, CPA at {cpa_str}."
            )
    else:
        sentences.append("Insufficient historical data to compute multi-day momentum.")

    # Sentence 2: Top scaling candidates
    scale_items = [i for i in priorities.get("scale", []) if i.get("entity_type") == "campaign"][:2]
    increase_items = bp.get("increase", [])[:2]
    if scale_items:
        names = " and ".join(f'"{i["entity_name"]}"' for i in scale_items)
        sentences.append(f"Top scaling candidates are {names} — consider increasing daily budgets.")
    elif increase_items:
        names = " and ".join(f'"{i["campaign_name"]}"' for i in increase_items)
        sentences.append(f"Budget increase recommendations exist for {names}.")

    # Sentence 3: At-risk spend (only genuine below-breakeven spend, not breakeven)
    if el.get("available"):
        at_risk_pct = el.get("at_risk_pct") or el.get("pct_of_account_spend", 0)
        at_risk_amt = el.get("at_risk_spend") or el.get("total_wasted_spend", 0)
        top = el.get("top_contributors", [])
        top_name = _short_name_simple(top[0]["name"]) if top else "unknown campaigns"
        if at_risk_pct >= 10:
            sentences.append(
                f"${at_risk_amt:,.0f} ({at_risk_pct}% of spend) is going to campaigns below breakeven "
                f"— the largest is {top_name}."
            )

    # Sentence 4: Today's focus
    fix_count = len(priorities.get("fix", []))
    watch_count = len(priorities.get("watch", []))
    if fix_count > 0:
        sentences.append(
            f"Today's focus: address {fix_count} Fix item{'s' if fix_count != 1 else ''} "
            f"and monitor {watch_count} Watch item{'s' if watch_count != 1 else ''}."
        )
    else:
        sentences.append("No critical Fix items today — focus on scaling and creative refresh.")

    return sentences[:5]


def _short_name_simple(name: str) -> str:
    """Return last meaningful segment of a campaign name (simple version for strategy note)."""
    parts = [p.strip() for p in name.replace("|", " | ").split("|")]
    for p in reversed(parts):
        if p and len(p) > 3:
            return p[:40]
    return name[:40]


# ---------------------------------------------------------------------------
# Meta automation status
# ---------------------------------------------------------------------------

_UNDERPERFORM_DAYS = 3          # consecutive days below threshold
_UNDERPERFORM_ROAS_RATIO = 0.75 # below 75% of 7d avg counts as underperforming
_UNDERPERFORM_MIN_SPEND = 20.0  # minimum daily spend to count the day
_UNDERPERFORM_MIN_TOTAL = 50.0  # minimum 3-day spend to flag
_SLOW_SCALE_ROAS_MULT = 1.5     # ROAS > this * 7d avg = winner
_SLOW_SCALE_SPEND_GROWTH = 1.15 # spend growth below this = scaling slowly
_SLOW_SCALE_MIN_SPEND = 50.0
_ADSET_FATIGUE_MIN_ADS = 2
_ADSET_FATIGUE_PCT = 0.70       # >= 70% of ads fatigued = whole adset fatigued


def build_meta_automation_status(
    insights_30d: list,
    c_rows: list,
    campaigns: list,
    trends: dict,
    ci: dict,
    bp: dict,
) -> dict:
    """
    Detects when Meta's own automation is not enough and manual intervention is required.

    Meta handles automatically:
    - Creative suppression / rotation within ad sets
    - CBO budget distribution across ad sets
    - Bid optimization within strategy
    - Delivery and audience optimization

    This function detects the 4 cases where Meta automation fails:
    1. Persistent underperformers (3+ days below ROAS threshold)
    2. Budget capped winners (Meta can't increase the campaign budget)
    3. Scaling winners slowly (high ROAS but spend not growing)
    4. Creative fatigue across entire ad set (nothing left to rotate to)

    Uses insights_30d for trend analysis across multiple days.
    """
    # ---- Automation active flags ----
    cbo_count = sum(1 for c in campaigns if c.get("daily_budget"))
    budget_optimization_active = cbo_count > 0

    # Creative suppression: active if ads within same adset have very different spend
    # (Meta concentrates on winners). Proxy: any adset has at least one zero-spend ad
    # while others have spend.
    adset_spend_map: dict = {}
    for ad in ci.get("top_creatives", []):
        asn = ad.get("adset_name", "")
        if asn:
            adset_spend_map.setdefault(asn, []).append(ad.get("spend", 0))

    suppression_evident = any(
        min(spends) == 0 and max(spends) > 0
        for spends in adset_spend_map.values()
        if len(spends) >= 2
    )
    creative_suppression_active = suppression_evident

    # ---- 1. Persistent underperformers (3+ days ROAS below 75% of 7d avg) ----
    dates = sorted({r.get("date_start") for r in insights_30d if r.get("date_start")})
    last_7 = set(dates[-7:]) if len(dates) >= 7 else set(dates)
    last_3_dates = sorted(last_7)[-3:]

    # Group insights by campaign_id+date
    daily: dict = {}  # {campaign_id: {date: {spend, revenue, name}}}
    for r in insights_30d:
        cid = r.get("campaign_id")
        d = r.get("date_start")
        if not cid or d not in last_7:
            continue
        daily.setdefault(cid, {}).setdefault(d, {"spend": 0.0, "revenue": 0.0, "name": ""})
        daily[cid][d]["spend"] += _float(r.get("spend"))
        daily[cid][d]["revenue"] += _action_value(r.get("action_values", []), "purchase")
        if r.get("campaign_name") and not daily[cid][d]["name"]:
            daily[cid][d]["name"] = r["campaign_name"]

    persistent_underperformers = []
    for cid, by_date in daily.items():
        sorted_d = sorted(by_date.keys())

        # 7d avg ROAS
        roas_vals = []
        for d in sorted_d:
            s = by_date[d]["spend"]
            rev = by_date[d]["revenue"]
            if s >= _UNDERPERFORM_MIN_SPEND:
                roas_vals.append(rev / s)
        if len(roas_vals) < 3 or not any(r > 0 for r in roas_vals):
            continue
        avg_roas = sum(roas_vals) / len(roas_vals)
        if avg_roas < 0.3:
            continue  # no meaningful ROAS baseline

        threshold = avg_roas * _UNDERPERFORM_ROAS_RATIO

        # Check last 3 dates
        below = 0
        total_spend_3d = 0.0
        for d in last_3_dates:
            row = by_date.get(d, {})
            s = row.get("spend", 0)
            rev = row.get("revenue", 0)
            if s >= _UNDERPERFORM_MIN_SPEND:
                total_spend_3d += s
                if (rev / s) < threshold:
                    below += 1

        if below >= _UNDERPERFORM_DAYS and total_spend_3d >= _UNDERPERFORM_MIN_TOTAL:
            name = by_date[sorted_d[-1]]["name"] or by_date[sorted_d[0]]["name"]
            persistent_underperformers.append({
                "campaign_name": name,
                "avg_roas_7d": round(avg_roas, 2),
                "days_below": below,
                "spend_3d": round(total_spend_3d, 2),
            })

    persistent_underperformers.sort(key=lambda x: -x["spend_3d"])

    # ---- 2. Budget capped winners ----
    budget_capped_winners = [
        {
            "campaign_name": item["campaign_name"],
            "spend_yesterday": item.get("spend_yesterday", 0),
            "roas_signal": item.get("metrics", ""),
        }
        for item in bp.get("increase", [])
        if item.get("budget_capped")
    ]

    # ---- 3. Scaling winners slowly ----
    by_campaign = trends.get("by_campaign", {})
    scaling_slowly = []
    capped_names = {w["campaign_name"] for w in budget_capped_winners}

    for t in by_campaign.values():
        name = t.get("campaign_name", "")
        if name in capped_names:
            continue  # already in budget_capped bucket
        roas_yday = t.get("roas_yesterday")
        roas_7d = t.get("roas_7d_avg")
        spend_yday = t.get("spend_yesterday") or 0
        spend_7d = t.get("spend_7d_avg") or 0

        if spend_yday < _SLOW_SCALE_MIN_SPEND:
            continue
        if not roas_yday or not roas_7d or roas_7d == 0:
            continue
        if roas_yday < roas_7d * _SLOW_SCALE_ROAS_MULT:
            continue  # not a clear winner
        if spend_7d > 0 and spend_yday > spend_7d * _SLOW_SCALE_SPEND_GROWTH:
            continue  # spend already growing — Meta is scaling it

        scaling_slowly.append({
            "campaign_name": name,
            "roas_yesterday": roas_yday,
            "roas_7d_avg": roas_7d,
            "spend_yesterday": spend_yday,
            "spend_7d_avg": spend_7d,
        })

    scaling_slowly.sort(key=lambda x: -(x["roas_yesterday"] / x["roas_7d_avg"]))

    # ---- 4. Creative fatigue across entire ad set ----
    adset_all: dict = {}   # adset_name -> set of ad ids with spend
    adset_fat: dict = {}   # adset_name -> set of fatigued ad ids

    for ad in ci.get("top_creatives", []):
        asn = ad.get("adset_name", "")
        if asn:
            adset_all.setdefault(asn, set()).add(ad.get("id"))

    for ad in ci.get("fatigued", []):
        asn = ad.get("adset_name", "")
        if asn:
            adset_fat.setdefault(asn, set()).add(ad.get("id"))

    fatigued_adsets = []
    for asn, fat_ids in adset_fat.items():
        total = len(adset_all.get(asn, set()))
        if total < _ADSET_FATIGUE_MIN_ADS:
            continue
        pct = len(fat_ids) / total
        if pct >= _ADSET_FATIGUE_PCT:
            fatigued_adsets.append({
                "adset_name": asn,
                "fatigued_count": len(fat_ids),
                "total_count": total,
                "pct_fatigued": round(pct * 100),
            })

    fatigued_adsets.sort(key=lambda x: -x["pct_fatigued"])

    # ---- Intervention list (ordered by severity) ----
    interventions = []
    for item in budget_capped_winners:
        interventions.append({
            "type": "budget_capped_winner",
            "severity": "high",
            "campaign_name": item["campaign_name"],
            "reason": f"Winner hitting budget cap — Meta cannot scale further. Increase campaign budget manually.",
            "detail": item.get("roas_signal", ""),
        })
    for item in persistent_underperformers:
        interventions.append({
            "type": "persistent_underperformer",
            "severity": "high",
            "campaign_name": item["campaign_name"],
            "reason": (
                f"ROAS below {round(_UNDERPERFORM_ROAS_RATIO*100)}% of avg for "
                f"{item['days_below']} consecutive days (${item['spend_3d']:.0f} spent). "
                f"Meta has not reacted — reduce budget or pause."
            ),
            "detail": f"7d avg ROAS: {item['avg_roas_7d']}x",
        })
    for item in fatigued_adsets[:3]:
        interventions.append({
            "type": "creative_fatigue_adset",
            "severity": "medium",
            "adset_name": item["adset_name"],
            "reason": (
                f"{item['fatigued_count']}/{item['total_count']} ads fatigued "
                f"({item['pct_fatigued']}%) — Meta has no fresh creative to rotate to."
            ),
            "detail": "Add new creatives to this ad set.",
        })
    for item in scaling_slowly[:3]:
        mult = round(item["roas_yesterday"] / item["roas_7d_avg"], 1)
        interventions.append({
            "type": "scaling_slowly",
            "severity": "medium",
            "campaign_name": item["campaign_name"],
            "reason": (
                f"ROAS {item['roas_yesterday']:.2f}x ({mult}x above avg) but spend not growing. "
                f"Meta's automation isn't scaling this fast enough."
            ),
            "detail": f"Spend yesterday ${item['spend_yesterday']:.0f} vs prior week ${item['spend_7d_avg']:.0f}",
        })

    # ---- Overall score ----
    high_count = sum(1 for i in interventions if i["severity"] == "high")
    med_count = sum(1 for i in interventions if i["severity"] == "medium")
    if high_count > 0:
        score = "ACTION NEEDED"
    elif med_count > 0:
        score = "REVIEW NEEDED"
    else:
        score = "ON TRACK"

    return {
        "available": bool(insights_30d),
        "automation_active": {
            "creative_suppression": creative_suppression_active,
            "budget_optimization": budget_optimization_active,
            "cbo_campaign_count": cbo_count,
            "delivery_optimization": True,
        },
        "interventions": interventions,
        "budget_capped_winners": budget_capped_winners,
        "persistent_underperformers": persistent_underperformers,
        "fatigued_adsets": fatigued_adsets,
        "scaling_slowly": scaling_slowly,
        "score": score,
        "high_count": high_count,
        "medium_count": med_count,
    }


# ---------------------------------------------------------------------------
# URL health (from pre-run check_landing_pages.py output)
# ---------------------------------------------------------------------------

_SEV_RANK = {"high": 0, "medium": 1, "low": 2, "ok": 3}


def build_url_health() -> dict:
    """
    Load the latest URL health check output from outputs/url_health/.
    Returns a report-ready summary dict, or {available: False} if no file exists.
    Run check_landing_pages.py first to generate the health data.
    """
    from datetime import date as _date
    files = sorted(URL_HEALTH_DIR.glob("????-??-??.json"), reverse=True)
    if not files:
        return {"available": False}

    latest_file = files[0]
    # Warn if the file is stale (not from today)
    file_date_str = latest_file.stem  # YYYY-MM-DD
    stale = file_date_str != _date.today().isoformat()

    raw = json.loads(latest_file.read_text())
    results = raw.get("results", [])
    summary = raw.get("summary", {})

    if not results:
        return {"available": False}

    broken = [r for r in results if r.get("is_broken") or r.get("is_redirect_loop")]
    slow = [r for r in results if r.get("is_slow") and not r.get("is_broken")]
    oos = [r for r in results if r.get("is_out_of_stock")]

    # Top 5 issues sorted by severity then spend at risk
    top_issues = sorted(
        [r for r in results if r.get("severity") not in ("ok", None)],
        key=lambda r: (_SEV_RANK.get(r.get("severity", "low"), 2), -(r.get("total_spend") or 0)),
    )[:5]

    return {
        "available": True,
        "stale": stale,
        "stale_date": file_date_str if stale else None,
        "checked_at": raw.get("checked_at"),
        "total_checked": summary.get("total_checked", len(results)),
        "broken_count": len(broken),
        "slow_count": len(slow),
        "oos_count": len(oos),
        "top_issues": top_issues,
    }


# ---------------------------------------------------------------------------
# Automation action schema
# ---------------------------------------------------------------------------

ACTIONS_DIR = Path("outputs/actions")


def build_automation_actions(
    bp: dict,
    proposed: dict,
    ci: dict,
    url_health: dict,
    priorities: dict,
    c_rows: list = None,
) -> dict:
    """
    Consolidates all recommendations into a structured, automation-ready action schema.
    Each action includes entity IDs, current/recommended values, confidence, and trigger rule.
    Saves the action list to outputs/actions/YYYY-MM-DD-actions.json.
    Returns the action list for inclusion in the report data.
    """
    from datetime import date as _date, datetime as _datetime, timezone as _tz

    # Build economics lookup from enriched c_rows
    _c_row_lookup = {}
    if c_rows:
        for r in c_rows:
            if r.get("id"):
                _c_row_lookup[r["id"]] = r
            if r.get("name"):
                _c_row_lookup.setdefault(r["name"], r)

    def _econ_fields(campaign_id=None, campaign_name=None, ad_id=None, adset_id=None):
        """Look up economics fields from enriched c_rows for an action."""
        row = None
        if campaign_id:
            row = _c_row_lookup.get(campaign_id)
        if row is None and campaign_name:
            row = _c_row_lookup.get(campaign_name)
        if row is None:
            return {}
        return {
            "breakeven_roas": row.get("breakeven_roas"),
            "roas_floor": row.get("roas_floor"),
            "profitability_status": row.get("profitability_status", "UNKNOWN"),
            "estimated_daily_profit": row.get("estimated_daily_profit"),
            "cpa_target_7dc": row.get("cpa_target_7dc"),
            "automation_safe": row.get("automation_safe", False),
            "automation_safe_reason": row.get("automation_safe_reason"),
        }

    actions = []

    # ---- Budget adjustments (increase / reduce) from budget_plan ----
    for item in bp.get("increase", []):
        if not item.get("entity_id"):
            continue
        current = item.get("current_budget")
        chg = item.get("change_pct", 0)
        recommended = round(current * (1 + chg / 100), 2) if current else None
        _etype = item.get("entity_type", "campaign")
        _econ = _econ_fields(campaign_id=item.get("campaign_id"), campaign_name=item.get("campaign_name"))
        actions.append({
            "action_type": "adjust_budget",
            "direction": "increase",
            "entity_type": _etype,
            "entity_id": item["entity_id"],
            "entity_name": item["campaign_name"],
            "campaign_id": item.get("campaign_id"),
            "campaign_name": item.get("campaign_name"),
            "adset_id": item["entity_id"] if _etype == "adset" else None,
            "adset_name": "",
            "budget_level": "Campaign (CBO)" if _etype == "campaign" else "AdSet",
            "current_value": current,
            "recommended_value": recommended,
            "change_pct": chg,
            "confidence": item.get("confidence", "MEDIUM"),
            "trigger": item.get("reason", ""),
            "trigger_rule": item.get("trigger_rule", {}),
            "priority": "high" if item.get("confidence") == "HIGH" else "medium",
            **_econ,
        })

    for item in bp.get("reduce", []):
        if not item.get("entity_id"):
            continue
        current = item.get("current_budget")
        chg = item.get("change_pct", 0)  # already negative
        recommended = round(current * (1 + chg / 100), 2) if current else None
        _etype = item.get("entity_type", "campaign")
        _econ = _econ_fields(campaign_id=item.get("campaign_id"), campaign_name=item.get("campaign_name"))
        actions.append({
            "action_type": "adjust_budget",
            "direction": "reduce",
            "entity_type": _etype,
            "entity_id": item["entity_id"],
            "entity_name": item["campaign_name"],
            "campaign_id": item.get("campaign_id"),
            "campaign_name": item.get("campaign_name"),
            "adset_id": item["entity_id"] if _etype == "adset" else None,
            "adset_name": "",
            "budget_level": "Campaign (CBO)" if _etype == "campaign" else "AdSet",
            "current_value": current,
            "recommended_value": recommended,
            "change_pct": chg,
            "confidence": item.get("confidence", "MEDIUM"),
            "trigger": item.get("reason", ""),
            "trigger_rule": item.get("trigger_rule", {}),
            "priority": "high" if item.get("confidence") == "HIGH" else "medium",
            **_econ,
        })

    # ---- Pause candidates ----
    for item in proposed.get("pause_candidates", []):
        _etype = item.get("entity_type", "campaign")
        _eid = item.get("entity_id")
        _cid = item.get("campaign_id")
        _econ = _econ_fields(campaign_id=_cid if _etype != "campaign" else _eid, campaign_name=item.get("name", ""))
        actions.append({
            "action_type": "pause_or_reduce",
            "direction": "pause",
            "entity_type": _etype,
            "entity_id": _eid,
            "entity_name": item.get("name", ""),
            "campaign_id": _cid if _etype != "campaign" else _eid,
            "campaign_name": item.get("name", ""),
            "adset_id": _eid if _etype == "adset" else None,
            "adset_name": "",
            "current_value": None,
            "recommended_value": None,
            "change_pct": None,
            "confidence": "MEDIUM",
            "trigger": item.get("reason", ""),
            "trigger_rule": item.get("trigger_rule", {}),
            "priority": "high",
            **_econ,
        })

    # ---- Creative refresh (fatigued ads) ----
    _refresh_immediately = ci.get("creative_actions", {}).get("refresh_immediately", [])
    _refresh_soon = ci.get("creative_actions", {}).get("refresh_soon", [])

    for ad in _refresh_immediately:
        ctr_chg = ad.get("ctr_change_pct")
        _econ = _econ_fields(campaign_id=ad.get("campaign_id"), campaign_name=ad.get("campaign_name", ""))
        purchases = ad.get("purchases", 0)
        if purchases < 3 and _econ:
            _econ["automation_safe"] = False
            _econ["automation_safe_reason"] = "purchases < 3 on scale recommendation"
        actions.append({
            "action_type": "pause_ad",
            "direction": "pause",
            "entity_type": "ad",
            "entity_id": ad.get("id"),
            "entity_name": ad.get("name", ""),
            "adset_id": ad.get("adset_id"),
            "adset_name": ad.get("adset_name", ""),
            "campaign_id": ad.get("campaign_id"),
            "campaign_name": ad.get("campaign_name", ""),
            "current_value": None,
            "recommended_value": None,
            "change_pct": ctr_chg,
            "confidence": "HIGH",
            "trigger": f"CTR drop {ctr_chg:+.0f}% vs prior week" if ctr_chg is not None else "CTR drop > 50%",
            "trigger_rule": {
                "metric": "ctr_change_pct",
                "operator": "<=",
                "threshold": -50,
                "window": "7d",
                "min_spend": 0,
            },
            "priority": "high",
            **_econ,
        })

    for ad in _refresh_soon:
        ctr_chg = ad.get("ctr_change_pct")
        _econ = _econ_fields(campaign_id=ad.get("campaign_id"), campaign_name=ad.get("campaign_name", ""))
        purchases = ad.get("purchases", 0)
        if purchases < 3 and _econ:
            _econ["automation_safe"] = False
            _econ["automation_safe_reason"] = "purchases < 3 on scale recommendation"
        actions.append({
            "action_type": "pause_ad",
            "direction": "pause",
            "entity_type": "ad",
            "entity_id": ad.get("id"),
            "entity_name": ad.get("name", ""),
            "adset_id": ad.get("adset_id"),
            "adset_name": ad.get("adset_name", ""),
            "campaign_id": ad.get("campaign_id"),
            "campaign_name": ad.get("campaign_name", ""),
            "current_value": None,
            "recommended_value": None,
            "change_pct": ctr_chg,
            "confidence": "MEDIUM",
            "trigger": f"CTR drop {ctr_chg:+.0f}% vs prior week" if ctr_chg is not None else "CTR drop 30-50%",
            "trigger_rule": {
                "metric": "ctr_change_pct",
                "operator": "<=",
                "threshold": -30,
                "window": "7d",
                "min_spend": 0,
            },
            "priority": "medium",
            **_econ,
        })

    # ---- Broken URLs ----
    for result in url_health.get("top_issues", []):
        if not result.get("is_broken"):
            continue
        for ad in result.get("ads", [])[:3]:
            actions.append({
                "action_type": "fix_url",
                "direction": "fix",
                "entity_type": "ad",
                "entity_id": ad.get("ad_id"),
                "entity_name": ad.get("ad_name", ""),
                "adset_id": ad.get("adset_id"),
                "adset_name": ad.get("adset_name", ""),
                "campaign_id": ad.get("campaign_id"),
                "campaign_name": ad.get("campaign_name", ""),
                "current_value": result.get("url"),
                "recommended_value": None,
                "change_pct": None,
                "confidence": "HIGH",
                "trigger": f"HTTP {result.get('status_code', '4xx')} — destination URL is broken",
                "trigger_rule": {
                    "metric": "http_status_code",
                    "operator": ">=",
                    "threshold": 400,
                },
                "priority": "high",
            })

    # Sort: high priority first, then by action_type
    _priority_rank = {"high": 0, "medium": 1, "low": 2}
    actions.sort(key=lambda a: (_priority_rank.get(a.get("priority", "medium"), 1), a.get("action_type", "")))

    # Save to file
    today = _date.today().isoformat()
    ACTIONS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = ACTIONS_DIR / f"{today}-actions.json"
    output = {
        "generated_at": _datetime.now(_tz.utc).isoformat(),
        "report_date": today,
        "action_count": len(actions),
        "actions": actions,
    }
    out_path.write_text(json.dumps(output, indent=2))

    return {
        "available": True,
        "action_count": len(actions),
        "actions": actions,
        "saved_to": str(out_path),
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Unit economics enrichment
# ---------------------------------------------------------------------------

def _load_economics_with_fallback() -> tuple:
    """
    Try to load live unit economics from Google Sheets.
    Falls back to the most recent local snapshot on any failure.

    Returns (economics_dict, warning_str_or_None).
    economics_dict is {} if no data is available at all.
    """
    from unit_economics import load_unit_economics

    creds_path = SHEETS_CREDENTIALS_PATH
    snap_dir = Path(ECONOMICS_SNAPSHOT_DIR)

    # Check credentials file exists before attempting API call
    if not Path(creds_path).exists():
        # No credentials — go straight to snapshot
        snapshots = sorted(snap_dir.glob("economics_????-??-??.json"), reverse=True)
        if snapshots:
            snap_date = snapshots[0].stem.replace("economics_", "")
            print(f"  [Sheets] No credentials file found — using snapshot {snap_date}")
            economics = json.loads(snapshots[0].read_text())
            warning = (
                f"Unit economics loaded from snapshot dated {snap_date} — "
                "credentials file missing. Verify figures before acting on any recommendation."
            )
            return economics, warning
        print("  [Sheets] No credentials and no snapshot — profitability data unavailable")
        return {}, (
            "Unit economics unavailable — credentials/sheets_service_account.json not found "
            "and no snapshot exists."
        )

    try:
        economics = load_unit_economics(UNIT_ECONOMICS_SHEET_ID, creds_path)
        if not economics:
            raise ValueError("Empty response from Sheets API")
        print(f"  [Sheets] Loaded economics for {len(economics)} product(s)")
        return economics, None
    except Exception as exc:
        print(f"  [WARN] Google Sheets unavailable ({exc.__class__.__name__}: {exc}) — loading snapshot")
        snapshots = sorted(snap_dir.glob("economics_????-??-??.json"), reverse=True)
        if snapshots:
            snap_date = snapshots[0].stem.replace("economics_", "")
            economics = json.loads(snapshots[0].read_text())
            warning = (
                f"Unit economics loaded from snapshot dated {snap_date} — "
                "live sheet unavailable. Verify figures before acting on any recommendation."
            )
            print(f"  [WARN] Using snapshot: {snapshots[0].name}")
            return economics, warning
        print("  [WARN] No economics snapshot found — profitability data unavailable")
        return {}, (
            "Unit economics unavailable — Google Sheets unreachable and no snapshot found."
        )


def enrich_campaigns_with_economics(c_rows: list, economics: dict) -> list:
    """Add profitability scoring to each campaign row using campaign_profitability.score_campaign()."""
    from campaign_profitability import score_campaign

    for row in c_rows:
        score = score_campaign(
            {
                "name": row.get("name", ""),
                "spend": row.get("spend") or 0,
                "roas": row.get("roas"),
                "cpa": row.get("cpa"),
                "purchases": row.get("purchases") or 0,
                "revenue": row.get("revenue") or 0,
            },
            economics,
        )
        # Merge all score fields directly into the row
        row.update(score)
        # Keep matched_product for backward compat (use tab_name or product_key)
        if "matched_product" not in row:
            row["matched_product"] = row.get("tab_name")

    return c_rows


def _add_economics_to_proposed(proposed: dict, c_rows: list) -> dict:
    """Add profitability context to proposed_changes items from enriched c_rows."""
    row_by_id = {r["id"]: r for r in c_rows if r.get("id")}
    row_by_name = {r["name"]: r for r in c_rows if r.get("name")}

    for bucket in ("increase", "reduce", "pause_candidates"):
        for item in proposed.get(bucket, []):
            cid = item.get("campaign_id") or item.get("entity_id")
            row = row_by_id.get(cid) or row_by_name.get(item.get("name", ""))
            if row:
                item["profitability_status"] = row.get("profitability_status", "UNKNOWN")
                item["matched_product"] = row.get("matched_product")
                item["breakeven_roas"] = row.get("breakeven_roas")
                item["roas_target_7dc"] = row.get("roas_target_7dc")
                item["vs_breakeven_pct"] = row.get("vs_breakeven_pct")
    return proposed


def build_unit_economics_summary(c_rows: list, economics: dict, changes: list) -> dict:
    """
    Build the data structure for the Unit Economics report section.
    """
    if not economics:
        return {"available": False}

    # Profitability breakdown across active campaigns with spend
    active_with_spend = [
        r for r in c_rows
        if r.get("status") == "ACTIVE" and (r.get("spend") or 0) > 0
    ]
    status_counts: dict = {}
    for r in active_with_spend:
        s = r.get("profitability_status", "UNKNOWN")
        status_counts[s] = status_counts.get(s, 0) + 1

    # Per-product spend + profitability breakdown
    product_spend: dict = {}
    for r in active_with_spend:
        prod = r.get("product_key") or r.get("matched_product") or "Unmatched"
        status = r.get("profitability_status", "UNKNOWN")
        if prod not in product_spend:
            product_spend[prod] = {"spend": 0.0, "campaigns": 0, "statuses": {}}
        product_spend[prod]["spend"] += r.get("spend") or 0
        product_spend[prod]["campaigns"] += 1
        product_spend[prod]["statuses"][status] = (
            product_spend[prod]["statuses"].get(status, 0) + 1
        )

    return {
        "available": True,
        "economics": economics,
        "changes": changes,
        "status_counts": status_counts,
        "active_campaign_count": len(active_with_spend),
        "product_spend": product_spend,
    }


def build_report_data(mode: str = "7d") -> dict:
    """
    mode="7d"    — last 7 days as primary metrics, 7d vs prior-7d trend signals (default)
    mode="daily" — yesterday as primary metrics, yesterday vs 7d-avg trend signals
    """
    print(f"Loading raw data... (mode: {mode})\n")

    campaigns = _load("campaigns")
    adsets = _load("adsets")
    ads = _load("ads")
    insights_daily = _load("insights")   # yesterday snapshot
    insights_30d = _load("insights_30d")

    # Select primary insights source and trend engine based on mode
    if mode == "daily":
        insights = insights_daily
        trends = compute_trends_daily(insights_30d)
        period_days = 1
    else:
        insights = _load("insights_7d")  # 7-day aggregated
        trends = compute_trends(insights_30d)
        period_days = 7

    print()

    c_rows = campaign_rows(campaigns, insights, ads)
    cr_rows = ad_creative_rows(ads, adsets, insights)
    adset_rows = adset_bidding_rows(adsets, campaigns, insights)

    # Load unit economics early so enrichment can inform priorities
    print("\nLoading unit economics...")
    economics, economics_warning = _load_economics_with_fallback()

    # Enrich campaign rows with profitability fields before building priorities
    # so that SCALE/FIX/WATCH logic can filter on profitability_status
    c_rows = enrich_campaigns_with_economics(c_rows, economics)

    winners = detect_winners(trends, c_rows, period_days=period_days)

    ci = creative_intelligence(cr_rows, insights, insights_30d, trends.get("by_ad", {}))

    # Attach classify_campaign() verdict to each campaign row BEFORE build_priorities()
    # so that FIX/WATCH/SCALE verdict gates operate on real verdicts, not defaults.
    # Requires: economics enriched (done above) + creative intelligence (ci, done above).
    _camp_freq_map = _compute_campaign_frequency(cr_rows, adset_rows)
    _fatigue_rank = {
        "REFRESH_NOW": 0, "REFRESH_SOON": 1,
        "MONITOR_ENGAGEMENT": 2, "HIGH_FREQUENCY": 3, "HEALTHY": 4,
    }
    _camp_creative_status: dict = {}
    for _ad in ci.get("top_creatives", []):
        _cid_ad = _ad.get("campaign_id")
        _fs = _ad.get("fatigue_status", "HEALTHY")
        if _cid_ad and _fatigue_rank.get(_fs, 4) < _fatigue_rank.get(
            _camp_creative_status.get(_cid_ad, "HEALTHY"), 4
        ):
            _camp_creative_status[_cid_ad] = _fs
    for _crow in c_rows:
        _cid_v = _crow.get("id")
        _t_v = trends.get("by_campaign", {}).get(_cid_v, {})
        _freq_v = _camp_freq_map.get(_cid_v)
        _cs_v = _camp_creative_status.get(_cid_v, "HEALTHY")
        _clf = classify_campaign(_cid_v, _crow, _t_v, _freq_v, period_days, _cs_v)
        _crow["verdict"] = _clf["verdict"]
        _crow["verdict_trend"] = _clf["trend"]
        _crow["verdict_confidence"] = _clf["confidence"]
        _crow["verdict_reasons"] = _clf["reasons"]
        _crow["verdict_disqualifiers"] = _clf["disqualifiers"]
        _crow["verdict_recommended_action"] = _clf["recommended_action"]
        _crow["verdict_daily_action"] = _clf["daily_action"]
        _crow["verdict_budget_change_pct"] = _clf["recommended_budget_change_pct"]
        _crow["verdict_summary"] = _clf["verdict_summary"]

    priorities = build_priorities(trends, c_rows, adset_rows, cr_rows, insights_30d, period_days=period_days)

    bp = build_budget_plan(trends, c_rows, adset_rows, cr_rows)
    camp_intel = campaign_intelligence(c_rows, ads)
    sc = build_spend_concentration(c_rows)
    momentum = build_account_momentum()

    # Build efficiency leakage after enrichment so profitability fields are available
    el = build_efficiency_leakage(c_rows, trends)

    # Detect changes vs last snapshot
    economics_changes: list = []
    if economics:
        from unit_economics import detect_economics_changes
        campaign_names = [c.get("name", "") for c in campaigns]
        try:
            economics_changes = detect_economics_changes(
                economics, ECONOMICS_SNAPSHOT_DIR, campaign_names
            )
            if economics_changes:
                print(f"  [Sheets] {len(economics_changes)} economics change(s) detected")
        except Exception as exc:
            print(f"  [WARN] Change detection failed: {exc}")

    proposed = build_proposed_changes(bp, priorities, ci, trends)
    # Add profitability context to proposed changes items
    proposed = _add_economics_to_proposed(proposed, c_rows)

    data = {
        "account_overview": account_overview(insights, campaigns, adsets, ads),
        "account_comparison": trends.get("account_totals", {}),  # 7d vs prior-7d (mode="7d" only)
        "account_changes": build_account_changes(campaigns, adsets, ads, insights_daily),
        "account_momentum": momentum,
        "strategy_snapshot": strategy_snapshot(campaigns, adsets),
        "campaign_rows": c_rows,
        "campaign_intelligence": camp_intel,
        "campaign_performance": filter_campaign_performance(trends, priorities),
        # (profitability fields merged into campaign_performance rows below)
        "adset_bidding_rows": adset_rows,
        "ad_creative_rows": cr_rows,
        "creative_intelligence": ci,
        "trends": trends,
        "winners": winners,
        "priorities": priorities,
        "budget_plan": bp,
        "proposed_changes": proposed,
        "spend_concentration": sc,
        "efficiency_map": build_efficiency_map(c_rows, trends),
        "efficiency_leakage": el,
        "decision_summary": build_decision_summary(bp, priorities, ci),
        "creative_concept_insights": build_creative_concept_insights(ci),
        "creative_lifecycle": build_creative_lifecycle(ci),
        "strategic_signals": build_strategic_signals(camp_intel, sc, el, priorities, ci),
        "strategy_note": build_strategy_note(momentum, priorities, el, bp),
        "url_health": build_url_health(),
        "meta_automation_status": build_meta_automation_status(
            insights_30d, c_rows, campaigns, trends, ci, bp
        ),
        "unit_economics": build_unit_economics_summary(c_rows, economics, economics_changes),
        "economics_warning": economics_warning,
        "economics_changes": economics_changes,
    }

    # In daily mode: suppress budget-action outputs.
    # The daily report is anomaly detection only — no budget recommendations.
    # Zero out the sections that produce Increase / Reduce / Pause actions so
    # those sections render as empty rather than misleading.
    if mode == "daily":
        data["proposed_changes"] = {
            "increase": [], "reduce": [], "hold": [], "pause_candidates": [],
            "creative": [],
        }
        data["decision_summary"] = {
            "increase": [], "reduce": [], "pause": [], "creative": [],
            "total_increase_delta": 0, "total_reduce_delta": 0, "net_delta": 0,
        }
        # Keep budget_plan for internal calculations but clear its action buckets
        data["budget_plan"] = {**bp, "increase": [], "reduce": [], "hold": []}

    # Merge profitability fields into campaign_performance rows
    _econ_by_name = {r["name"]: r for r in c_rows if r.get("name")}
    for perf_row in data["campaign_performance"].get("rows", []):
        cname = perf_row.get("campaign_name", "")
        crow = _econ_by_name.get(cname, {})
        perf_row["profitability_status"] = crow.get("profitability_status", "UNKNOWN")
        perf_row["roas_target_7dc"] = crow.get("roas_target_7dc")
        perf_row["breakeven_roas"] = crow.get("breakeven_roas")

    # Build automation actions last (depends on other keys)
    data["automation_actions"] = build_automation_actions(
        bp, data["proposed_changes"], ci, data["url_health"], priorities, c_rows
    )

    # Save economics changes snapshot
    import datetime as _dt
    today_str = _dt.date.today().isoformat()
    Path(ECONOMICS_SNAPSHOT_DIR).mkdir(parents=True, exist_ok=True)
    changes_path = Path(ECONOMICS_SNAPSHOT_DIR) / f"economics_changes_{today_str}.json"
    try:
        changes_path.write_text(json.dumps(economics_changes, indent=2, default=str))
    except Exception as exc:
        print(f"  [WARN] Could not save economics changes: {exc}")

    # Save product economics summary
    try:
        summary_data = {
            "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "source": "live" if not economics_warning else "snapshot",
            "products": economics,
            "campaign_profitability": {
                r.get("name", ""): {
                    "product_key": r.get("product_key"),
                    "profitability_status": r.get("profitability_status"),
                    "breakeven_roas": r.get("breakeven_roas"),
                    "vs_breakeven_pct": r.get("vs_breakeven_pct"),
                }
                for r in c_rows if r.get("status") == "ACTIVE"
            }
        }
        Path("outputs").mkdir(exist_ok=True)
        Path("outputs/product_economics_summary.json").write_text(
            json.dumps(summary_data, indent=2, default=str)
        )
    except Exception as exc:
        print(f"  [WARN] Could not save product economics summary: {exc}")

    return data


# ---------------------------------------------------------------------------
# Pre-render validator
# ---------------------------------------------------------------------------

_ALLOWED_VERDICTS = frozenset({
    "SCALE", "HOLD", "MONITOR", "REDUCE", "PAUSE",
    "CREATIVE_REFRESH", "INVESTIGATE", "WATCH_ONLY",
})


def validate_report_data(data: dict, period_days: int) -> dict:
    """
    Lightweight pre-render validation checklist.

    Checks:
      1. all_active_have_verdict    — every active campaign has exactly one verdict
      2. verdicts_all_valid         — every verdict is in the allowed vocabulary
      3. daily_no_budget_actions    — daily mode suppresses increase/reduce outputs
      4. pause_candidates_verdict   — pause candidates are only PAUSE-verdict campaigns
      5. scale_candidates_verdict   — scale bucket is only SCALE-verdict campaigns
      6. spend_buckets_coverage     — informational: classified vs total active spend

    Returns dict with "passed" bool, "failed_checks" list, and per-check "details".
    """
    checks: dict = {}
    c_rows = data.get("campaign_rows", [])
    priorities = data.get("priorities", {})
    budget_plan = data.get("budget_plan", {})
    proposed = data.get("proposed_changes", {})

    _c_by_id = {c.get("id"): c for c in c_rows if c.get("id")}

    # 1. Every active campaign has a verdict
    no_verdict = [
        c.get("name", c.get("id", "?"))
        for c in c_rows
        if c.get("status") == "ACTIVE" and not c.get("verdict")
    ]
    checks["all_active_have_verdict"] = (
        len(no_verdict) == 0,
        f"{len(no_verdict)} missing: {no_verdict[:3]}" if no_verdict else "OK",
    )

    # 2. Every verdict is in the allowed vocabulary
    bad_verdicts = [
        (c.get("name", "?"), c.get("verdict"))
        for c in c_rows
        if c.get("verdict") and c.get("verdict") not in _ALLOWED_VERDICTS
    ]
    checks["verdicts_all_valid"] = (
        len(bad_verdicts) == 0,
        f"Invalid: {bad_verdicts[:3]}" if bad_verdicts else "OK",
    )

    # 3. Daily mode suppresses direct budget actions
    if period_days == 1:
        _inc = budget_plan.get("increase", [])
        _red = budget_plan.get("reduce", [])
        _has_actions = len(_inc) > 0 or len(_red) > 0
        checks["daily_no_budget_actions"] = (
            not _has_actions,
            f"Daily has {len(_inc)} increase and {len(_red)} reduce items" if _has_actions else "OK",
        )
    else:
        checks["daily_no_budget_actions"] = (True, "N/A — 7d mode")

    # 4. Pause candidates are only PAUSE-verdict campaigns
    fix_items = priorities.get("fix", [])
    pause_candidates = proposed.get("pause", [])
    pause_fix_count = sum(1 for i in fix_items if i.get("verdict") == "PAUSE")
    bad_pause = len(pause_candidates) > pause_fix_count
    checks["pause_candidates_verdict"] = (
        not bad_pause,
        f"pause_candidates={len(pause_candidates)}, PAUSE fix_items={pause_fix_count}",
    )

    # 5. Scale bucket contains only SCALE-verdict campaigns
    scale_items = priorities.get("scale", [])
    scale_bad = [
        i.get("entity_name", "?")
        for i in scale_items
        if _c_by_id.get(i.get("campaign_id"), {}).get("verdict") not in ("SCALE", None)
    ]
    checks["scale_candidates_verdict"] = (
        len(scale_bad) == 0,
        f"Non-SCALE in scale bucket: {scale_bad}" if scale_bad else "OK",
    )

    # 6. Spend coverage (informational)
    bp_all = budget_plan.get("increase", []) + budget_plan.get("reduce", []) + budget_plan.get("hold", [])
    bp_spend = sum(i.get("spend_yesterday", 0) for i in bp_all)
    active_spend = sum((c.get("spend") or 0) for c in c_rows if c.get("status") == "ACTIVE")
    checks["spend_buckets_coverage"] = (
        True,
        f"Budget plan covers ${bp_spend:.0f} of ${active_spend:.0f} active spend",
    )

    failed = [k for k, (passed, _) in checks.items() if not passed]
    return {
        "passed": len(failed) == 0,
        "failed_checks": failed,
        "details": {k: {"passed": p, "detail": d} for k, (p, d) in checks.items()},
    }


if __name__ == "__main__":
    import pprint
    data = build_report_data()
    pprint.pprint(data, sort_dicts=False)
