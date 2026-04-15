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
    "minimal",           # clean product photography, zero text
    "bold_type",         # text IS the hero — massive display font, product secondary
    "benefit_stack",     # stacked trust/benefit lines on a solid colour-block background
    "social_proof",      # review card overlay: stars + customer quote + name
    "origin_story",      # farm/orchard scene, provenance headline
    "lifestyle_tagline", # single emotional brand statement, clean background
    "direct_response",   # full conversion toolkit: headline + body + CTA button
    "premium",           # pure white bg, product centered, small elegant label
)

VARIANT_RATIONALES: dict[str, str] = {
    "minimal": (
        "Pure product photography with zero text. "
        "Lets the product stop the scroll on its own — ideal for retargeting and repeat exposure."
    ),
    "bold_type": (
        "The typography IS the visual. Oversized condensed display text grabs attention "
        "before the product is even processed. Best for cold-audience pattern interrupts."
    ),
    "benefit_stack": (
        "Solid colour block with stacked trust signals in large text. "
        "Addresses purchase objections upfront — shipping, guarantee, sourcing in one frame."
    ),
    "social_proof": (
        "Review card with stars and customer quote overlaid on the product. "
        "Third-party validation at the moment of first impression reduces purchase hesitation."
    ),
    "origin_story": (
        "Farm or orchard background grounds the product in a real place. "
        "Provenance sells premium quality — this variant targets quality-conscious buyers."
    ),
    "lifestyle_tagline": (
        "Emotional brand statement on a clean background. No hard sell — the tagline "
        "creates identity resonance. Works for top-of-funnel awareness."
    ),
    "direct_response": (
        "Full conversion layout: bold headline, benefit body copy, and a prominent CTA button. "
        "Maximises bottom-of-funnel click-to-purchase intent."
    ),
    "premium": (
        "Pure white studio shot with a single product and minimal label. "
        "Elevates perceived quality — ideal for first-touch premium positioning."
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

    One StaticAdBrief → up to 8 RenderSpecs (one per VARIANT_STRATEGIES entry).

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
