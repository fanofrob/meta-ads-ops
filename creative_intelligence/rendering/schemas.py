"""
TypedDict schema and constants for the static rendering layer.

No runtime dependencies — safe to import anywhere.
"""
from __future__ import annotations

from typing import Any


# ─────────────────────────────────────────────
# Variant strategies
# ─────────────────────────────────────────────

VARIANT_STRATEGIES: tuple[str, ...] = (
    "minimal",          # clean, uncluttered, product-forward
    "premium",          # luxury aesthetic, elevated lifestyle
    "direct_response",  # text-heavy, CTA prominent, conversion-oriented
    "reveal",           # comparison or before/after angle
    "product_hero",     # full-frame product, lifestyle secondary
)

VARIANT_RATIONALES: dict[str, str] = {
    "minimal": (
        "Removes visual noise to let the product speak. "
        "Works well for premium positioning and repeat exposure."
    ),
    "premium": (
        "Elevates perceived value through environmental context and colour palette. "
        "Targets quality-conscious buyers."
    ),
    "direct_response": (
        "Maximises text density and CTA prominence for conversion campaigns. "
        "Best for bottom-of-funnel traffic."
    ),
    "reveal": (
        "Uses contrast or comparison structure to drive curiosity and scroll-stop. "
        "Effective for new-to-brand audiences."
    ),
    "product_hero": (
        "Full-focus on the product itself — ideal for first-touch awareness "
        "where the product needs to be the hero."
    ),
}

VALID_ASPECT_RATIOS: frozenset[str] = frozenset({"9:16", "1:1", "4:5", "16:9"})
DEFAULT_ASPECT_RATIO: str = "9:16"


# ─────────────────────────────────────────────
# RenderSpec
# ─────────────────────────────────────────────

class RenderSpec:
    """
    Schema for a single static render variant.

    One StaticAdBrief → up to 5 RenderSpecs (one per VARIANT_STRATEGIES entry).

    Fields
    ------
    production_output_id : Source production_outputs.id (always static_brief).
    concept_title        : First headline option — used as card title.
    visual_prompt        : Positive image generation prompt.
    negative_prompt      : What to avoid (quality, style, content guardrails).
    headline_overlay     : Headline text to render as image overlay.
    body_overlay         : Primary body text for overlay (short).
    cta_text             : Button/call-to-action copy.
    shot_type            : Detected shot framing (close-up / overhead / lifestyle / …).
    composition          : Verbatim composition_notes from the brief.
    background_style     : Background description derived from visual_direction.
    product_focus        : How/where the product appears — from product_visibility.
    text_hierarchy       : On-image text placement guidance — from text_overlay.
    style_tags           : List of visual style descriptors.
    aspect_ratio         : "9:16" | "1:1" | "4:5" | "16:9".
    variant_label        : One of VARIANT_STRATEGIES.
    variant_rationale    : One sentence on why this variant exists.
    """

    FIELDS = (
        "production_output_id",
        "concept_title",
        "visual_prompt",
        "negative_prompt",
        "headline_overlay",
        "body_overlay",
        "cta_text",
        "shot_type",
        "composition",
        "background_style",
        "product_focus",
        "text_hierarchy",
        "style_tags",
        "aspect_ratio",
        "variant_label",
        "variant_rationale",
    )


def empty_render_spec() -> dict[str, Any]:
    return {
        "production_output_id": None,
        "concept_title":    "",
        "visual_prompt":    "",
        "negative_prompt":  "",
        "headline_overlay": "",
        "body_overlay":     "",
        "cta_text":         "",
        "shot_type":        "",
        "composition":      "",
        "background_style": "",
        "product_focus":    "",
        "text_hierarchy":   "",
        "style_tags":       [],
        "aspect_ratio":     DEFAULT_ASPECT_RATIO,
        "variant_label":    "",
        "variant_rationale": "",
    }


def validate_render_spec(spec: dict[str, Any]) -> list[str]:
    """Return list of missing required fields."""
    required = (
        "production_output_id", "visual_prompt", "negative_prompt",
        "variant_label", "aspect_ratio",
    )
    return [f for f in required if not spec.get(f)]
