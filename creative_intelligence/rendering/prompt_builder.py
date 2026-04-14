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
    # minimal: no text at all — reinforce strongly in negative
    "minimal": (
        "complex backgrounds, busy patterns, excessive props, "
        "text overlays, typography, headlines, body copy, CTA buttons, "
        "words, letters, captions, watermarks"
    ),
    # premium: no button, no body paragraph
    "premium": (
        "cheap materials, plastic surfaces, harsh lighting, cluttered scenes, "
        "CTA buttons, body copy paragraph, multiple text blocks, bold sans-serif fonts"
    ),
    # direct_response: needs clear legible text — reject illegibility
    "direct_response": (
        "unclear focal point, weak contrast, illegible text, "
        "overlapping text and product, hard-to-read typography"
    ),
    # reveal: needs the comparison structure
    "reveal": (
        "confusing layout, single-subject only, no comparison element, "
        "excessive body copy, paragraph text"
    ),
    # product_hero: absolutely no text
    "product_hero": (
        "environment-dominated scene, tiny product, background-forward, "
        "text overlays, typography, headlines, body copy, CTA buttons, "
        "words, letters, captions, watermarks, any text whatsoever"
    ),
}

# Base negative prompt applied to all variants
# NOTE: "text overlays" intentionally omitted — we want the AI to bake
# the headline and CTA into the image as styled ad copy.
_BASE_NEGATIVE = (
    "blurry, low quality, distorted, out of focus, "
    "cluttered, generic stock photo, people looking at camera awkwardly, "
    "fake-looking, oversaturated, pixelated, jpeg artifacts, "
    "watermarks, logos, illegible text, random scribbles"
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


def _build_visual_prompt(
    brief: dict[str, Any],
    variant: str,
    headline: str = "",
    body: str = "",
    cta: str = "",
) -> str:
    """Build the full image-generation prompt with variant-specific text treatment.

    Each variant has a distinct visual structure and text density:
      minimal        → product-only, NO text overlay
      premium        → single elegant tagline, NO body, NO button
      direct_response→ full headline + body paragraph + prominent CTA button
      reveal         → bold headline + short comparison subtext + CTA
      product_hero   → NO text at all — pure product photography
    """
    base_parts = [
        brief.get("visual_direction", "").strip(),
        brief.get("product_visibility", "").strip(),
        brief.get("composition_notes", "").strip(),
        _VARIANT_MODIFIERS[variant],
    ]

    text_parts = _build_text_treatment(variant, headline, body, cta)
    parts = base_parts + text_parts
    return ". ".join(p for p in parts if p)


def _build_text_treatment(
    variant: str,
    headline: str,
    body: str,
    cta: str,
) -> list[str]:
    """Return text-injection prompt fragments tailored per variant strategy.

    Returns an empty list for variants that should be text-free.
    """
    if variant == "minimal":
        # Clean product shot — no text, no distractions
        return [
            "no text overlays, no typography, no copy, purely visual product image",
        ]

    if variant == "premium":
        # One short elegant tagline only — top or bottom, no button
        if headline:
            short = headline[:50]  # trim to punchy tagline length
            return [
                f'single elegant tagline in refined serif typography: "{short}"',
                "no body copy, no CTA button, minimal text, luxury editorial style",
                "text placed in lower third with generous breathing room",
            ]
        return ["minimal text, editorial style, no CTA button"]

    if variant == "direct_response":
        # Text-heavy conversion ad — full headline + body + big CTA button
        parts = []
        if headline:
            parts.append(
                f'large bold headline text in upper portion: "{headline.upper()}"'
            )
        if body:
            parts.append(
                f'smaller body copy paragraph below headline: "{body}"'
            )
        if cta:
            parts.append(
                f'large high-contrast CTA button at bottom center with text "{cta.upper()}"'
            )
        parts.append(
            "strong visual hierarchy, conversion-optimised layout, "
            "text dominates lower two-thirds of the frame"
        )
        return parts

    if variant == "reveal":
        # Bold headline + very short punchy subline, CTA at bottom
        parts = []
        if headline:
            parts.append(
                f'bold centred headline: "{headline.upper()}"'
            )
        if body:
            # Use only the first sentence / first 60 chars as a punchy sub-line
            subline = body.split(".")[0].strip()[:60]
            if subline:
                parts.append(f'short punchy subline below headline: "{subline}"')
        if cta:
            parts.append(
                f'small CTA text at very bottom: "{cta}"'
            )
        parts.append(
            "dramatic split composition, text overlaid on high-contrast areas, "
            "headline is the dominant visual element"
        )
        return parts

    if variant == "product_hero":
        # Pure product photography — absolutely no text
        return [
            "no text, no copy, no typography, no overlays, "
            "pure product hero photography only",
        ]

    # Fallback for unknown variants — minimal text treatment
    if headline:
        return [f'headline text: "{headline}"']
    return []


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
    hook             = brief.get("hook", "")

    shot_type        = _detect_shot_type(brief.get("composition_notes", ""))
    background_style = _background_style(brief.get("visual_direction", ""))
    style_tags       = _extract_style_tags(brief.get("visual_direction", ""))

    # Filter to only known variants
    selected = [v for v in variants if v in VARIANT_STRATEGIES]

    specs: list[dict[str, Any]] = []
    for i, variant in enumerate(selected):
        # Rotate through available headline/body options so each variant
        # tests a different copy combination (wraps if fewer options than variants)
        if headline_options:
            headline_overlay = headline_options[i % len(headline_options)]
        else:
            headline_overlay = hook
        if body_options:
            body_overlay = body_options[i % len(body_options)]
        else:
            body_overlay = ""

        spec = empty_render_spec()
        spec.update({
            "production_output_id": production_output_id,
            "concept_title":     headline_overlay[:80],
            "visual_prompt":     _build_visual_prompt(
                brief, variant,
                headline=headline_overlay,
                body=body_overlay,
                cta=brief.get("cta", ""),
            ),
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
