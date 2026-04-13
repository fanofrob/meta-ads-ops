"""
Production output exporters.

Renders structured production output dicts into human-readable and
machine-consumable formats.

Public API
----------
to_markdown(output_type, data) → str   — copy-ready Markdown for Notion/Docs
to_json(data) → str                    — pretty-printed JSON for API/rendering
to_text_block(output_type, data) → str — plain text, no Markdown syntax
"""
from __future__ import annotations

import json
from typing import Any


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _bullet_list(items: list[str], indent: str = "") -> str:
    if not items:
        return f"{indent}—"
    return "\n".join(f"{indent}- {item}" for item in items)


def _beat_table_md(beats: list[dict]) -> str:
    if not beats:
        return "—"
    rows = ["| Beat | Time | Line | Direction |",
            "|------|------|------|-----------|"]
    for b in beats:
        beat = str(b.get("beat", "")).replace("|", "\\|")
        secs = str(b.get("seconds", "")).replace("|", "\\|")
        line = str(b.get("line", "")).replace("|", "\\|")
        dirn = str(b.get("direction", "")).replace("|", "\\|")
        rows.append(f"| {beat} | {secs} | {line} | {dirn} |")
    return "\n".join(rows)


def _beat_table_text(beats: list[dict]) -> str:
    if not beats:
        return "  —"
    lines = []
    for b in beats:
        beat = b.get("beat", "")
        secs = b.get("seconds", "")
        line = b.get("line", "")
        dirn = b.get("direction", "")
        lines.append(f"  [{beat} | {secs}]  {line}")
        if dirn:
            lines.append(f"    → {dirn}")
    return "\n".join(lines)


def _score_pill(score: float | None) -> str:
    if score is None:
        return ""
    tier = "HIGH" if score >= 70 else "MED" if score >= 50 else "LOW"
    return f"{score:.0f} {tier}"


# ─────────────────────────────────────────────
# Static Ad Brief
# ─────────────────────────────────────────────

def _static_brief_md(data: dict) -> str:
    lines = [
        "## Static Ad Brief",
        "",
        "### Hook",
        data.get("hook", ""),
        "",
        "### Headline Options",
        _bullet_list(data.get("headline_options") or []),
        "",
        "### Body / Primary Text Options",
        _bullet_list(data.get("body_options") or []),
        "",
        "### Visual Direction",
        data.get("visual_direction", ""),
        "",
        "### Composition Notes",
        data.get("composition_notes", ""),
        "",
        "### Product Visibility",
        data.get("product_visibility", ""),
        "",
        "### Text Overlay Guidance",
        data.get("text_overlay", ""),
        "",
        "### CTA",
        data.get("cta", ""),
        "",
        "### Why This Works",
        data.get("why_it_works", ""),
    ]
    return "\n".join(lines)


def _static_brief_text(data: dict) -> str:
    lines = [
        "STATIC AD BRIEF",
        "=" * 40,
        "",
        "HOOK",
        data.get("hook", ""),
        "",
        "HEADLINE OPTIONS",
    ]
    for h in (data.get("headline_options") or []):
        lines.append(f"  • {h}")
    lines += [
        "",
        "BODY / PRIMARY TEXT OPTIONS",
    ]
    for b in (data.get("body_options") or []):
        lines.append(f"  • {b}")
    lines += [
        "",
        "VISUAL DIRECTION",
        f"  {data.get('visual_direction', '')}",
        "",
        "COMPOSITION NOTES",
        f"  {data.get('composition_notes', '')}",
        "",
        "PRODUCT VISIBILITY",
        f"  {data.get('product_visibility', '')}",
        "",
        "TEXT OVERLAY",
        f"  {data.get('text_overlay', '')}",
        "",
        "CTA",
        f"  {data.get('cta', '')}",
        "",
        "WHY THIS WORKS",
        f"  {data.get('why_it_works', '')}",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────
# UGC Creator Brief
# ─────────────────────────────────────────────

def _ugc_brief_md(data: dict) -> str:
    lines = [
        "## UGC Creator Brief",
        "",
        "### Creator Persona",
        data.get("creator_persona", ""),
        "",
        "### Opening Line",
        f'"{data.get("opening_line", "")}"',
        "",
        "### Talking Points",
        _bullet_list(data.get("talking_points") or []),
        "",
        "### Demo Beats (what to show on camera)",
        _bullet_list(data.get("demo_beats") or []),
        "",
        "### Emotional Tone",
        data.get("emotional_tone", ""),
        "",
        "### Scene / Environment Suggestions",
        _bullet_list(data.get("scene_suggestions") or []),
        "",
        "### CTA (verbal close)",
        data.get("cta", ""),
        "",
        "### No-Go Notes",
        _bullet_list(data.get("no_go_notes") or []),
    ]
    return "\n".join(lines)


def _ugc_brief_text(data: dict) -> str:
    lines = [
        "UGC CREATOR BRIEF",
        "=" * 40,
        "",
        "CREATOR PERSONA",
        f"  {data.get('creator_persona', '')}",
        "",
        "OPENING LINE",
        f'  "{data.get("opening_line", "")}"',
        "",
        "TALKING POINTS",
    ]
    for tp in (data.get("talking_points") or []):
        lines.append(f"  • {tp}")
    lines += [
        "",
        "DEMO BEATS",
    ]
    for db in (data.get("demo_beats") or []):
        lines.append(f"  • {db}")
    lines += [
        "",
        "EMOTIONAL TONE",
        f"  {data.get('emotional_tone', '')}",
        "",
        "SCENE SUGGESTIONS",
    ]
    for ss in (data.get("scene_suggestions") or []):
        lines.append(f"  • {ss}")
    lines += [
        "",
        "CTA",
        f"  {data.get('cta', '')}",
        "",
        "NO-GO NOTES",
    ]
    for ng in (data.get("no_go_notes") or []):
        lines.append(f"  ✗ {ng}")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# Script Package
# ─────────────────────────────────────────────

def _script_package_md(data: dict) -> str:
    lines = [
        "## Script Package",
        "",
        f"**Duration:** {data.get('duration', '')}",
        "",
        "### Hook",
        data.get("hook", ""),
        "",
        "### Body",
        data.get("body", ""),
        "",
        "### CTA",
        data.get("cta", ""),
        "",
        "### Beat Structure",
        "",
        _beat_table_md(data.get("beat_structure") or []),
        "",
        "### Alternate Hooks",
        _bullet_list(data.get("alternate_hooks") or []),
        "",
        "### Alternate CTAs",
        _bullet_list(data.get("alternate_ctas") or []),
        "",
        "### Production Notes",
        data.get("production_notes", ""),
    ]
    return "\n".join(lines)


def _script_package_text(data: dict) -> str:
    lines = [
        "SCRIPT PACKAGE",
        "=" * 40,
        f"Duration: {data.get('duration', '')}",
        "",
        "HOOK",
        f"  {data.get('hook', '')}",
        "",
        "BODY",
        f"  {data.get('body', '')}",
        "",
        "CTA",
        f"  {data.get('cta', '')}",
        "",
        "BEAT STRUCTURE",
        _beat_table_text(data.get("beat_structure") or []),
        "",
        "ALTERNATE HOOKS",
    ]
    for ah in (data.get("alternate_hooks") or []):
        lines.append(f"  • {ah}")
    lines += [
        "",
        "ALTERNATE CTAs",
    ]
    for ac in (data.get("alternate_ctas") or []):
        lines.append(f"  • {ac}")
    lines += [
        "",
        "PRODUCTION NOTES",
        f"  {data.get('production_notes', '')}",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────
# Test Package
# ─────────────────────────────────────────────

def _test_package_md(data: dict) -> str:
    core_score = data.get("core_score") or {}
    pat = data.get("pattern_alignment") or {}
    summary = data.get("score_summary") or {}

    lines = [
        "## Test Package",
        "",
        f"**Goal:** {data.get('goal', 'conversions')}",
    ]
    if data.get("audience"):
        lines.append(f"**Audience:** {data['audience']}")
    lines += [
        "",
        "### Core Concept",
        f'"{data.get("core_concept", "")}"',
        "",
        f"**Score:** {_score_pill(core_score.get('overall'))} "
        f"(structural: {core_score.get('structural', 0):.0f}, "
        f"pattern: {core_score.get('pattern_match', 0):.0f})",
    ]

    variants = data.get("variants") or []
    if variants:
        lines += [
            "",
            "### Variants",
            "",
            "| # | Concept | Type | Score |",
            "|---|---------|------|-------|",
        ]
        for i, v in enumerate(variants, 1):
            text = str(v.get("concept_text", ""))[:80].replace("|", "\\|")
            atype = str(v.get("action_type", "")).replace("|", "\\|")
            score = v.get("predicted_score") or (v.get("scores") or {}).get("overall")
            score_str = f"{score:.0f}" if score is not None else "—"
            lines.append(f"| {i} | {text} | {atype} | {score_str} |")

    if summary:
        lines += [
            "",
            "### Score Summary",
            f"- Count: {summary.get('count', 0)}",
            f"- Average: {summary.get('avg', 0):.1f}",
            f"- Range: {summary.get('min', 0):.0f}–{summary.get('max', 0):.0f}",
        ]

    if pat.get("pattern_name"):
        lines += [
            "",
            "### Pattern Alignment",
            f"**{pat['pattern_name']}**",
            f"- Hook type: {pat.get('hook_type', '—')}",
            f"- Angle: {pat.get('angle', '—')}",
            f"- Winners: {pat.get('winner_count', 0)}",
        ]
        if pat.get("avg_roas"):
            lines.append(f"- Avg ROAS: {pat['avg_roas']:.2f}")

    if data.get("product_context"):
        lines += [
            "",
            "### Product Context",
            "```",
            data["product_context"],
            "```",
        ]

    return "\n".join(lines)


def _test_package_text(data: dict) -> str:
    core_score = data.get("core_score") or {}
    pat = data.get("pattern_alignment") or {}
    summary = data.get("score_summary") or {}

    lines = [
        "TEST PACKAGE",
        "=" * 40,
        f"Goal: {data.get('goal', 'conversions')}",
    ]
    if data.get("audience"):
        lines.append(f"Audience: {data['audience']}")
    lines += [
        "",
        "CORE CONCEPT",
        f'  "{data.get("core_concept", "")}"',
        f"  Score: {core_score.get('overall', 0):.0f}",
    ]

    variants = data.get("variants") or []
    if variants:
        lines += ["", "VARIANTS"]
        for i, v in enumerate(variants, 1):
            score = v.get("predicted_score") or (v.get("scores") or {}).get("overall")
            score_str = f"{score:.0f}" if score is not None else "—"
            lines.append(f"  {i}. [{score_str}] {v.get('concept_text', '')[:100]}")

    if summary:
        lines += [
            "",
            "SCORE SUMMARY",
            f"  Count: {summary.get('count', 0)}",
            f"  Average: {summary.get('avg', 0):.1f}",
            f"  Range: {summary.get('min', 0):.0f}–{summary.get('max', 0):.0f}",
        ]

    if pat.get("pattern_name"):
        lines += [
            "",
            "PATTERN ALIGNMENT",
            f"  {pat['pattern_name']}",
            f"  Hook type: {pat.get('hook_type', '—')}  Angle: {pat.get('angle', '—')}",
            f"  Winners: {pat.get('winner_count', 0)}",
        ]

    return "\n".join(lines)


# ─────────────────────────────────────────────
# Video Brief  (unified — replaces ugc_brief + script_package)
# ─────────────────────────────────────────────

def _video_brief_md(data: dict) -> str:
    video_type = data.get("video_type", "")
    type_label = f" — {video_type.replace('_', ' ').title()}" if video_type else ""
    lines = [
        f"## Video Brief{type_label}",
        "",
        f"**Duration:** {data.get('duration', '')}",
        f"**Creator Persona:** {data.get('creator_persona', '')}",
        "",
        "### Hook",
        data.get("hook", ""),
        "",
        "### Talking Points",
        _bullet_list(data.get("talking_points") or []),
        "",
        "### Demo Beats (what to show on camera)",
        _bullet_list(data.get("demo_beats") or []),
        "",
        "### Beat Structure",
        "",
        _beat_table_md(data.get("beat_structure") or []),
        "",
        "### CTA",
        data.get("cta", ""),
        "",
        "### Production Notes",
        data.get("production_notes", ""),
        "",
        "### No-Go Notes",
        _bullet_list(data.get("no_go_notes") or []),
        "",
        "### Alternate Hooks",
        _bullet_list(data.get("alternate_hooks") or []),
    ]
    return "\n".join(lines)


def _video_brief_text(data: dict) -> str:
    video_type = data.get("video_type", "")
    type_label = f" [{video_type.upper()}]" if video_type else ""
    lines = [
        f"VIDEO BRIEF{type_label}",
        "=" * 40,
        f"Duration: {data.get('duration', '')}",
        f"Creator Persona: {data.get('creator_persona', '')}",
        "",
        "HOOK",
        f"  {data.get('hook', '')}",
        "",
        "TALKING POINTS",
    ]
    for tp in (data.get("talking_points") or []):
        lines.append(f"  • {tp}")
    lines += [
        "",
        "DEMO BEATS",
    ]
    for db in (data.get("demo_beats") or []):
        lines.append(f"  • {db}")
    lines += [
        "",
        "BEAT STRUCTURE",
        _beat_table_text(data.get("beat_structure") or []),
        "",
        "CTA",
        f"  {data.get('cta', '')}",
        "",
        "PRODUCTION NOTES",
        f"  {data.get('production_notes', '')}",
        "",
        "NO-GO NOTES",
    ]
    for ng in (data.get("no_go_notes") or []):
        lines.append(f"  ✗ {ng}")
    lines += [
        "",
        "ALTERNATE HOOKS",
    ]
    for ah in (data.get("alternate_hooks") or []):
        lines.append(f"  • {ah}")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def to_markdown(output_type: str, data: dict[str, Any]) -> str:
    """Render a production output as copy-ready Markdown.

    Suitable for pasting into Notion, Google Docs, or a handoff email.
    """
    if output_type == "static_brief":
        return _static_brief_md(data)
    elif output_type == "video_brief":
        return _video_brief_md(data)
    elif output_type == "ugc_brief":
        return _ugc_brief_md(data)
    elif output_type == "script_package":
        return _script_package_md(data)
    elif output_type == "test_package":
        return _test_package_md(data)
    else:
        return f"# {output_type}\n\n{json.dumps(data, indent=2)}"


def to_json(data: dict[str, Any]) -> str:
    """Render as pretty-printed JSON.

    Suitable for API consumption, rendering pipelines, or template systems.
    """
    return json.dumps(data, indent=2, ensure_ascii=False)


def to_text_block(output_type: str, data: dict[str, Any]) -> str:
    """Render as plain text with no Markdown syntax.

    Suitable for pasting into Slack, email, or tools that don't render Markdown.
    """
    if output_type == "static_brief":
        return _static_brief_text(data)
    elif output_type == "video_brief":
        return _video_brief_text(data)
    elif output_type == "ugc_brief":
        return _ugc_brief_text(data)
    elif output_type == "script_package":
        return _script_package_text(data)
    elif output_type == "test_package":
        return _test_package_text(data)
    else:
        return json.dumps(data, indent=2, ensure_ascii=False)
