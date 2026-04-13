"""
Operator-facing creative intelligence reports.

Generates markdown reports suitable for a marketer/operator, covering:
  - top hook types by CTR / ROAS / CPA
  - top angles by metric
  - top formats
  - winner vs loser comparison by dimension
  - winning pattern summaries
  - generated hook bank summary
  - visual style performance (if vision data available)
  - recommended next creative tests

Reports are written to outputs/creative_reports/ with date-stamped filenames.
Never reads from or writes to the main reporting pipeline's output directory.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from creative_intelligence import config
from creative_intelligence.db import get_connection
from creative_intelligence.analysis import performance, patterns, comparisons
from creative_intelligence.validation import qa_checks


# ─────────────────────────────────────────────
# Formatting helpers
# ─────────────────────────────────────────────

def _fmt_pct(v: float | None) -> str:
    return f"{v:.2%}" if v is not None else "—"

def _fmt_money(v: float | None) -> str:
    return f"${v:,.2f}" if v is not None else "—"

def _fmt_num(v: float | None, decimals: int = 2) -> str:
    return f"{v:.{decimals}f}" if v is not None else "—"

def _fmt_roas(v: float | None) -> str:
    return f"{v:.2f}x" if v is not None else "—"


def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    header_row = " | ".join(headers)
    sep_row    = " | ".join("---" for _ in headers)
    data_rows  = "\n".join(" | ".join(str(c) for c in row) for row in rows)
    return f"| {header_row} |\n| {sep_row} |\n{data_rows}"


# ─────────────────────────────────────────────
# Report sections
# ─────────────────────────────────────────────

def _section_data_quality(date_range: str, db: sqlite3.Connection) -> str:
    """
    Data quality health section — shown at the top of every report.
    Gives the operator a quick signal on whether the underlying data
    is trustworthy before reading the analysis sections below.
    """
    checks = qa_checks.run_all_checks(date_range=date_range, conn=db)

    icons = {"ok": "✓", "warn": "⚠", "fail": "✗"}
    fails = [c for c in checks if c["status"] == "fail"]
    warns = [c for c in checks if c["status"] == "warn"]
    oks   = [c for c in checks if c["status"] == "ok"]

    # Headline status
    if fails:
        headline = f"🔴 **{len(fails)} check(s) failed — results below may be unreliable**"
    elif warns:
        headline = f"🟡 **{len(warns)} warning(s) — review before acting on recommendations**"
    else:
        headline = f"🟢 **All {len(oks)} checks passed — data looks good**"

    lines = [
        "## Data Quality",
        "",
        headline,
        "",
        "| Check | Status | Detail |",
        "| --- | --- | --- |",
    ]
    for c in checks:
        icon   = icons.get(c["status"], "?")
        detail = c["detail"].replace("|", "\\|")  # escape markdown pipes
        lines.append(f"| {c['name']} | {icon} {c['status']} | {detail} |")

    if fails:
        lines.append("")
        lines.append("> **Action required:** Fix failures before trusting analysis.")
        lines.append("> Run `python -m creative_intelligence.cli validate` for details.")
    elif warns:
        lines.append("")
        lines.append("> **Advisory:** Warnings are non-blocking. Review QA report with")
        lines.append("> `python -m creative_intelligence.cli qa-report` to improve coverage.")

    return "\n".join(lines) + "\n"


def _section_top_hook_types(date_range: str, db: sqlite3.Connection) -> str:
    rows_ctr  = comparisons.compare_by_dimension("hook_type", "ctr",  date_range, conn=db)
    rows_roas = comparisons.compare_by_dimension("hook_type", "roas", date_range, conn=db)
    rows_cpa  = comparisons.compare_by_dimension("hook_type", "cpa",  date_range, conn=db)

    if not rows_ctr:
        return "## Hook Type Performance\n\n_No data yet._\n"

    lines = ["## Hook Type Performance\n"]

    lines.append("### By CTR")
    table_rows = [
        [r["dimension_value"], _fmt_pct(r.get("avg_ctr")), r.get("creative_count", 0),
         _fmt_money(r.get("total_spend"))]
        for r in rows_ctr[:8]
    ]
    lines.append(_md_table(["Hook Type", "Avg CTR", "Creatives", "Total Spend"], table_rows))

    if rows_roas:
        lines.append("\n### By ROAS")
        table_rows = [
            [r["dimension_value"], _fmt_roas(r.get("avg_roas")), r.get("creative_count", 0),
             _fmt_money(r.get("total_spend"))]
            for r in rows_roas[:8] if r.get("avg_roas")
        ]
        if table_rows:
            lines.append(_md_table(["Hook Type", "Avg ROAS", "Creatives", "Total Spend"], table_rows))

    if rows_cpa:
        lines.append("\n### By CPA (lower = better)")
        table_rows = [
            [r["dimension_value"], _fmt_money(r.get("avg_cpa")), r.get("creative_count", 0)]
            for r in rows_cpa[:8] if r.get("avg_cpa")
        ]
        if table_rows:
            lines.append(_md_table(["Hook Type", "Avg CPA", "Creatives"], table_rows))

    return "\n".join(lines) + "\n"


def _section_top_angles(date_range: str, db: sqlite3.Connection) -> str:
    rows = comparisons.compare_by_dimension("angle", "roas", date_range, conn=db)
    if not rows:
        return "## Angle Performance\n\n_No data yet._\n"

    lines = ["## Angle Performance\n"]
    table_rows = [
        [r["dimension_value"], _fmt_roas(r.get("avg_roas")), _fmt_pct(r.get("avg_ctr")),
         _fmt_money(r.get("avg_cpa")), r.get("creative_count", 0), _fmt_money(r.get("total_spend"))]
        for r in rows[:10]
    ]
    lines.append(_md_table(
        ["Angle", "Avg ROAS", "Avg CTR", "Avg CPA", "Creatives", "Total Spend"],
        table_rows,
    ))

    # Winner vs loser insight
    wvl = comparisons.winner_vs_loser_by_dimension("angle", date_range, conn=db)
    if wvl.get("insight"):
        lines.append(f"\n**Insight:** {wvl['insight']}")

    return "\n".join(lines) + "\n"


def _section_top_formats(date_range: str, db: sqlite3.Connection) -> str:
    rows = comparisons.compare_by_dimension("format", "ctr", date_range, conn=db)
    if not rows:
        return "## Format Performance\n\n_No data yet._\n"

    lines = ["## Format Performance\n"]
    table_rows = [
        [r["dimension_value"], _fmt_pct(r.get("avg_ctr")), _fmt_roas(r.get("avg_roas")),
         r.get("creative_count", 0), _fmt_money(r.get("total_spend"))]
        for r in rows
    ]
    lines.append(_md_table(
        ["Format", "Avg CTR", "Avg ROAS", "Creatives", "Total Spend"],
        table_rows,
    ))
    return "\n".join(lines) + "\n"


def _section_visual_performance(date_range: str, db: sqlite3.Connection) -> str:
    """Visual style performance section — only rendered if vision data exists."""
    count = db.execute(
        "SELECT COUNT(*) AS n FROM creative_visual_attributes WHERE error IS NULL"
    ).fetchone()
    if not count or count["n"] == 0:
        return "## Visual Style Performance\n\n_No visual analysis data yet. Run `analyze-visuals` to populate._\n"

    lines = ["## Visual Style Performance\n"]

    # Performance by visual_format
    for dim, label in [("visual_format", "Visual Format"), ("shot_type", "Shot Type"),
                        ("face_presence", "Face Presence")]:
        sql = f"""
            SELECT va.{dim} AS dim_val,
                   COUNT(DISTINCT c.id) AS creative_count,
                   ROUND(AVG(p.ctr), 4) AS avg_ctr,
                   ROUND(AVG(p.roas), 4) AS avg_roas,
                   ROUND(AVG(p.cpa), 4) AS avg_cpa,
                   ROUND(SUM(p.spend), 2) AS total_spend
            FROM creatives c
            JOIN creative_performance p ON p.creative_id = c.id AND p.date_range = ?
            JOIN creative_visual_attributes va ON va.creative_id = c.id
            WHERE va.{dim} IS NOT NULL AND va.error IS NULL
            GROUP BY va.{dim}
            ORDER BY total_spend DESC
        """
        rows = db.execute(sql, (date_range,)).fetchall()
        if rows:
            lines.append(f"\n### By {label}")
            table_rows = [
                [r["dim_val"] if r["dim_val"] is not None else "no",
                 _fmt_pct(r.get("avg_ctr")),
                 _fmt_roas(r.get("avg_roas")),
                 _fmt_money(r.get("avg_cpa")),
                 r.get("creative_count", 0),
                 _fmt_money(r.get("total_spend"))]
                for r in rows
            ]
            lines.append(_md_table(
                [label, "Avg CTR", "Avg ROAS", "Avg CPA", "Creatives", "Total Spend"],
                table_rows,
            ))

    return "\n".join(lines) + "\n"


def _section_winning_patterns(db: sqlite3.Connection) -> str:
    pats = patterns.get_patterns(min_winners=1, conn=db)
    if not pats:
        return "## Winning Creative Patterns\n\n_No patterns extracted yet. Run `extract-patterns` first._\n"

    lines = ["## Winning Creative Patterns\n"]
    for i, p in enumerate(pats[:10], 1):
        lines.append(f"### {i}. {p['pattern_name']}")
        lines.append(f"{p.get('description', '')}")

        metrics = []
        if p.get("avg_ctr"):
            metrics.append(f"Avg CTR: {_fmt_pct(p['avg_ctr'])}")
        if p.get("avg_roas"):
            metrics.append(f"Avg ROAS: {_fmt_roas(p['avg_roas'])}")
        if p.get("avg_cpa"):
            metrics.append(f"Avg CPA: {_fmt_money(p['avg_cpa'])}")
        lines.append(f"**Winners:** {p.get('winner_count', 0)} of {p.get('creative_count', 0)} | " + " | ".join(metrics))
        lines.append("")

    return "\n".join(lines) + "\n"


def _section_top_creatives(date_range: str, db: sqlite3.Connection) -> str:
    top = performance.top_creatives("roas", date_range, limit=10, conn=db)
    if not top:
        top = performance.top_creatives("ctr", date_range, limit=10, conn=db)
    if not top:
        return "## Top Creatives\n\n_No performance data yet._\n"

    lines = ["## Top Creatives\n"]
    table_rows = [
        [
            (r.get("ad_name") or "")[:40],
            _fmt_roas(r.get("roas")),
            _fmt_pct(r.get("ctr")),
            _fmt_money(r.get("cpa")),
            _fmt_money(r.get("spend")),
        ]
        for r in top
    ]
    lines.append(_md_table(
        ["Ad Name", "ROAS", "CTR", "CPA", "Spend"],
        table_rows,
    ))
    return "\n".join(lines) + "\n"


def _section_generated_hooks(db: sqlite3.Connection) -> str:
    rows = db.execute(
        """SELECT h.hook_text, h.hook_type, h.angle, h.score, h.status
           FROM generated_hooks h
           WHERE h.status = 'draft'
           ORDER BY h.score DESC NULLS LAST, h.created_at DESC
           LIMIT 20"""
    ).fetchall()

    if not rows:
        return "## Generated Hook Bank\n\n_No hooks generated yet. Run `generate-hooks` to populate._\n"

    lines = ["## Generated Hook Bank\n"]
    table_rows = [
        [
            (r["hook_text"] or "")[:80],
            r.get("hook_type") or "—",
            r.get("angle") or "—",
            _fmt_num(r.get("score"), 0) if r.get("score") else "—",
        ]
        for r in rows
    ]
    lines.append(_md_table(["Hook", "Type", "Angle", "Score"], table_rows))
    return "\n".join(lines) + "\n"


def _section_recommendations(date_range: str, db: sqlite3.Connection) -> str:
    lines = ["## Recommended Next Creative Tests\n"]

    pats = patterns.get_patterns(min_winners=2, conn=db)
    fatigue = performance.creative_fatigue_flags(date_range=date_range, conn=db)

    recs = []

    if pats:
        top = pats[0]
        recs.append(
            f"1. **Scale winning pattern** — \"{top['pattern_name']}\": "
            f"{top['winner_count']} winners, avg ROAS {_fmt_roas(top.get('avg_roas'))}. "
            f"Generate 10 new hooks from this pattern."
        )

    if len(pats) > 1:
        second = pats[1]
        recs.append(
            f"2. **Test second pattern** — \"{second['pattern_name']}\": "
            f"{second['winner_count']} winners. Create 3–5 test creatives."
        )

    if fatigue:
        worst = fatigue[0]
        recs.append(
            f"3. **Refresh fatigued creative** — \"{(worst.get('ad_name') or '')[:40]}\" "
            f"has frequency {_fmt_num(worst.get('frequency'))}. Produce 3 variations."
        )

    recs.append(
        "4. **Test visual formats** — Run visual analysis on top 20 creatives, "
        "then compare CTR by visual_format and face_presence."
    )

    if not recs:
        recs.append("_Insufficient data for recommendations. Ingest more creative data first._")

    lines.extend(recs)
    return "\n".join(lines) + "\n"


# ─────────────────────────────────────────────
# Main report assembler
# ─────────────────────────────────────────────

def build_creative_report(
    date_range: str = "7d",
    conn: sqlite3.Connection | None = None,
) -> str:
    db = conn or get_connection()
    today = date.today().isoformat()

    sections = [
        f"# Creative Intelligence Report\n**Date:** {today} | **Period:** {date_range}\n\n---\n",
        _section_data_quality(date_range, db),
        _section_top_creatives(date_range, db),
        _section_top_hook_types(date_range, db),
        _section_top_angles(date_range, db),
        _section_top_formats(date_range, db),
        _section_visual_performance(date_range, db),
        _section_winning_patterns(db),
        _section_generated_hooks(db),
        _section_recommendations(date_range, db),
    ]

    return "\n---\n".join(s for s in sections if s)


def write_report(
    date_range: str = "7d",
    output_dir: Path | None = None,
    conn: sqlite3.Connection | None = None,
) -> Path:
    """Build and write the report to disk. Returns the output path."""
    content = build_creative_report(date_range, conn)
    directory = output_dir or Path(config.CI_REPORTS_DIR)
    directory.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    suffix = f"-{date_range}" if date_range != "7d" else ""
    path = directory / f"{today}{suffix}-creative-report.md"
    path.write_text(content, encoding="utf-8")
    return path
