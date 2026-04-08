"""
Render spec exporters for the static rendering layer.

Renders a list of RenderSpec dicts into human-readable and
machine-consumable formats.

Public API
----------
render_spec_to_markdown(specs) → str   — Notion/Docs-ready Markdown
render_spec_to_json(specs)     → str   — pretty-printed JSON
render_spec_to_text(specs)     → str   — plain text, no Markdown syntax
"""
from __future__ import annotations

import json
from typing import Any


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _badge_label(variant_label: str) -> str:
    return variant_label.replace("_", " ").title()


def _tags_line(tags: list[str]) -> str:
    if not tags:
        return "—"
    return ", ".join(tags)


# ─────────────────────────────────────────────
# Markdown
# ─────────────────────────────────────────────

def _spec_md(spec: dict[str, Any]) -> str:
    label = _badge_label(spec.get("variant_label", ""))
    lines = [
        f"## {label} Variant",
        "",
        f"**Concept:** {spec.get('concept_title', '')}",
        f"**Aspect ratio:** {spec.get('aspect_ratio', '')}",
        f"**Shot type:** {spec.get('shot_type', '')}",
        "",
        "### Visual Prompt",
        spec.get("visual_prompt", ""),
        "",
        "### Negative Prompt",
        spec.get("negative_prompt", ""),
        "",
        "### Overlay Copy",
        f"- **Headline:** {spec.get('headline_overlay', '')}",
        f"- **Body:** {spec.get('body_overlay', '')}",
        f"- **CTA:** {spec.get('cta_text', '')}",
        "",
        "### Composition",
        spec.get("composition", ""),
        "",
        "### Background Style",
        spec.get("background_style", ""),
        "",
        "### Product Focus",
        spec.get("product_focus", ""),
        "",
        "### Text Hierarchy",
        spec.get("text_hierarchy", ""),
        "",
        f"### Style Tags",
        _tags_line(spec.get("style_tags") or []),
        "",
        "### Rationale",
        spec.get("variant_rationale", ""),
        "",
        "---",
    ]
    return "\n".join(lines)


def render_spec_to_markdown(specs: list[dict[str, Any]]) -> str:
    """Render all variant specs as copy-ready Markdown."""
    if not specs:
        return "# Render Specs\n\n_(no specs generated)_"
    pid = specs[0].get("production_output_id", "")
    header = [
        "# Static Render Specs",
        "",
        f"**Source brief ID:** {pid}",
        f"**Variants:** {len(specs)}",
        "",
        "---",
        "",
    ]
    return "\n".join(header) + "\n".join(_spec_md(s) for s in specs)


# ─────────────────────────────────────────────
# JSON
# ─────────────────────────────────────────────

def render_spec_to_json(specs: list[dict[str, Any]]) -> str:
    """Render as pretty-printed JSON. Suitable for API consumption."""
    return json.dumps(specs, indent=2, ensure_ascii=False)


# ─────────────────────────────────────────────
# Plain text
# ─────────────────────────────────────────────

def _spec_text(spec: dict[str, Any]) -> str:
    label = _badge_label(spec.get("variant_label", "UNKNOWN"))
    sep = "=" * 50
    lines = [
        sep,
        f"{label.upper()} VARIANT",
        sep,
        "",
        f"Concept: {spec.get('concept_title', '')}",
        f"Aspect ratio: {spec.get('aspect_ratio', '')}",
        f"Shot type: {spec.get('shot_type', '')}",
        "",
        "VISUAL PROMPT",
        f"  {spec.get('visual_prompt', '')}",
        "",
        "NEGATIVE PROMPT",
        f"  {spec.get('negative_prompt', '')}",
        "",
        "OVERLAY COPY",
        f"  Headline : {spec.get('headline_overlay', '')}",
        f"  Body     : {spec.get('body_overlay', '')}",
        f"  CTA      : {spec.get('cta_text', '')}",
        "",
        "COMPOSITION",
        f"  {spec.get('composition', '')}",
        "",
        "BACKGROUND",
        f"  {spec.get('background_style', '')}",
        "",
        "PRODUCT FOCUS",
        f"  {spec.get('product_focus', '')}",
        "",
        "TEXT HIERARCHY",
        f"  {spec.get('text_hierarchy', '')}",
        "",
        "STYLE TAGS",
        f"  {_tags_line(spec.get('style_tags') or [])}",
        "",
        "RATIONALE",
        f"  {spec.get('variant_rationale', '')}",
        "",
    ]
    return "\n".join(lines)


def render_spec_to_text(specs: list[dict[str, Any]]) -> str:
    """Render as plain text — suitable for Slack, email, or non-Markdown tools."""
    if not specs:
        return "RENDER SPECS\n\n(no specs generated)"
    return "\n".join(_spec_text(s) for s in specs)
