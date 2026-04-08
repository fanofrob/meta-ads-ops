"""
Prompt builder for the static rendering layer.

Converts a StaticAdBrief dict into a list of RenderSpec dicts
(one per variant strategy). No LLM call — pure string construction.

Public API
----------
build_render_specs(brief, production_output_id, variants, aspect_ratio)
    → list[dict]   — one RenderSpec dict per variant
"""
from __future__ import annotations

import re
from typing import Any

from creative_intelligence.rendering.schemas import (
    VARIANT_STRATEGIES,
    VARIANT_RATIONALES,
    DEFAULT_ASPECT_RATIO,
    empty_render_spec,
)


# ─────────────────────────────────────────────
# Variant modifiers — appended to visual_prompt
# ─────────────────────────────────────────────

_VARIANT_MODIFIERS: dict[str, str] = {
    "minimal": (
        "clean minimal composition, generous negative space, product-forward, "
        "uncluttered background, intentional whitespace"
    ),
    "premium": (
        "premium luxury aesthetic, warm ambient lighting, elevated lifestyle context, "
        "refined colour palette, soft shadows"
    ),
    "direct_response": (
        "bold typography emphasis, clear CTA placement, conversion-optimised layout, "
        "high contrast text areas, strong visual hierarchy"
    ),
    "reveal": (
        "split composition, comparison framing, before/after visual contrast, "
        "dramatic reveal structure, curiosity-driving"
    ),
    "product_hero": (
        "full-frame product focus, macro detail, hero shot, "
        "no distracting background elements, product fills frame"
    ),
}

# Negative prompt additions per variant (appended to base)
_VARIANT_NEGATIVES: dict[str, str] = {
    "minimal":         "complex backgrounds, busy patterns, excessive props",
    "premium":         "cheap materials, plastic surfaces, harsh lighting, cluttered scenes",
    "direct_response": "unclear focal point, weak contrast, illegible text areas",
    "reveal":          "confusing layout, single-subject only, no comparison element",
    "product_hero":    "environment-dominated scene, tiny product, background-forward",
}

# Base negative prompt applied to all variants
_BASE_NEGATIVE = (
    "text overlays, watermarks, blurry, low quality, distorted, out of focus, "
    "cluttered, generic stock photo, people looking at camera awkwardly, "
    "fake-looking, oversaturated, pixelated, jpeg artifacts"
)

# Known shot-type keywords to detect in composition notes
_SHOT_TYPE_PATTERNS: list[tuple[str, str]] = [
    (r"\bclose[- ]?up\b", "close-up"),
    (r"\bmacro\b", "macro"),
    (r"\boverhead\b|\btop[- ]?down\b|\bbird[- ]?s?[- ]?eye\b", "overhead"),
    (r"\bflat[- ]?lay\b", "flat lay"),
    (r"\blifestyle\b", "lifestyle"),
    (r"\bside[- ]?angle\b|\bside[- ]?view\b", "side angle"),
    (r"\bproduct[- ]?hero\b|\bhero[- ]?shot\b", "hero shot"),
    (r"\bportr?ait\b", "portrait"),
]

# Known visual style adjectives to extract as style_tags
_STYLE_ADJECTIVES = frozenset({
    "minimal", "minimalist", "clean", "premium", "luxury", "warm", "cool",
    "bright", "dark", "moody", "airy", "editorial", "lifestyle", "organic",
    "rustic", "modern", "sleek", "bold", "soft", "rich", "vibrant",
    "earthy", "natural", "fresh", "dramatic", "intimate", "candid",
})


# ─────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────

def _detect_shot_type(composition_notes: str) -> str:
    text = composition_notes.lower()
    for pattern, label in _SHOT_TYPE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return label
    return "product shot"


def _extract_style_tags(visual_direction: str) -> list[str]:
    """Tokenise visual_direction on commas/periods and extract style adjectives."""
    tokens = re.split(r"[,.\n]+", visual_direction.lower())
    tags: list[str] = []
    for token in tokens:
        words = re.findall(r"\b[a-z]+\b", token)
        for word in words:
            if word in _STYLE_ADJECTIVES and word not in tags:
                tags.append(word)
    return tags[:8]  # cap at 8


def _background_style(visual_direction: str) -> str:
    """First sentence of visual_direction describes the background/scene."""
    sentence = re.split(r"[.!?\n]", visual_direction.strip())[0]
    return sentence.strip()


def _build_visual_prompt(brief: dict[str, Any], variant: str) -> str:
    parts = [
        brief.get("visual_direction", "").strip(),
        brief.get("product_visibility", "").strip(),
        brief.get("composition_notes", "").strip(),
        _VARIANT_MODIFIERS[variant],
    ]
    return ". ".join(p for p in parts if p)


def _build_negative_prompt(variant: str) -> str:
    extra = _VARIANT_NEGATIVES.get(variant, "")
    if extra:
        return f"{_BASE_NEGATIVE}, {extra}"
    return _BASE_NEGATIVE


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def build_render_specs(
    brief: dict[str, Any],
    production_output_id: int,
    variants: tuple[str, ...] = VARIANT_STRATEGIES,
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
) -> list[dict[str, Any]]:
    """
    Build one RenderSpec dict per variant strategy.

    Parameters
    ----------
    brief                : StaticAdBrief output_json dict
    production_output_id : ID of the source production_outputs row
    variants             : subset of VARIANT_STRATEGIES to generate
    aspect_ratio         : "9:16" | "1:1" | "4:5" | "16:9"

    Returns
    -------
    list of RenderSpec dicts — one per requested variant
    """
    headline_options = brief.get("headline_options") or []
    body_options     = brief.get("body_options") or []
    headline_overlay = headline_options[0] if headline_options else brief.get("hook", "")
    body_overlay     = body_options[0]     if body_options     else ""

    shot_type       = _detect_shot_type(brief.get("composition_notes", ""))
    background_style = _background_style(brief.get("visual_direction", ""))
    style_tags      = _extract_style_tags(brief.get("visual_direction", ""))

    specs: list[dict[str, Any]] = []
    for variant in variants:
        if variant not in VARIANT_STRATEGIES:
            continue
        spec = empty_render_spec()
        spec.update({
            "production_output_id": production_output_id,
            "concept_title":     headline_overlay[:80],
            "visual_prompt":     _build_visual_prompt(brief, variant),
            "negative_prompt":   _build_negative_prompt(variant),
            "headline_overlay":  headline_overlay,
            "body_overlay":      body_overlay,
            "cta_text":          brief.get("cta", ""),
            "shot_type":         shot_type,
            "composition":       brief.get("composition_notes", ""),
            "background_style":  background_style,
            "product_focus":     brief.get("product_visibility", ""),
            "text_hierarchy":    brief.get("text_overlay", ""),
            "style_tags":        style_tags,
            "aspect_ratio":      aspect_ratio,
            "variant_label":     variant,
            "variant_rationale": VARIANT_RATIONALES[variant],
        })
        specs.append(spec)

    return specs
