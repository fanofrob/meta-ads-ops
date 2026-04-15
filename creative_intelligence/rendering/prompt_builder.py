"""
Prompt builder for the static rendering layer.

Converts a StaticAdBrief dict into a list of RenderSpec dicts
(one per variant strategy). No LLM call — pure string construction.

Each variant has a structurally distinct layout template so the image
generator produces genuinely different ad formats — not the same photo
with different captions.

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
# Per-variant structural layout templates
#
# These define the LAYOUT of the ad — background, text placement,
# typographic treatment — independently of the brief's visual_direction.
# The brief's product description is injected via {product}.
# ─────────────────────────────────────────────

# Base negative prompt (quality/artifact guardrails only — no style overrides)
_BASE_NEGATIVE = (
    "blurry, low quality, distorted, out of focus, pixelated, jpeg artifacts, "
    "oversaturated, fake-looking, watermarks, logos, busy clutter"
)

# Per-variant negatives — layered on top of base
_VARIANT_NEGATIVES: dict[str, str] = {
    "minimal": (
        "text, typography, headline, body copy, CTA button, words, letters, "
        "captions, watermarks, any text whatsoever, props, accessories, busy background"
    ),
    "bold_type": (
        "small text, illegible font, weak contrast, body copy paragraphs, "
        "cluttered layout, small product that competes with text"
    ),
    "benefit_stack": (
        "photographic background, dark background, lifestyle scene, "
        "illegible small text, random decorative elements, gradient confusion"
    ),
    "social_proof": (
        "empty review card, no star rating, fake-looking stars, "
        "illegible quote text, cluttered card design, ugly drop shadow"
    ),
    "origin_story": (
        "indoor studio setting, white background, no farm or orchard, "
        "generic background, city background, abstract pattern"
    ),
    "lifestyle_tagline": (
        "busy background, multiple text blocks, CTA button, price tags, "
        "hard-sell language, cluttered design, dark moody tones"
    ),
    "direct_response": (
        "unclear CTA, illegible text, weak contrast, small button, "
        "unreadable body copy, overlapping text and product"
    ),
    "premium": (
        "coloured background, lifestyle scene, props, multiple products, "
        "text overlays except product name label, busy composition, dark tones"
    ),
}

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
    return tags[:8]


def _background_style(visual_direction: str) -> str:
    """First sentence of visual_direction describes the background/scene."""
    sentence = re.split(r"[.!?\n]", visual_direction.strip())[0]
    return sentence.strip()


def _truncate(text: str, max_chars: int) -> str:
    """Truncate text to max_chars, ending at a word boundary."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0].rstrip(" ,;:")


# ─────────────────────────────────────────────
# Per-variant visual prompt builders
# Each returns a self-contained prompt — the layout is dictated
# by the variant, not the brief's visual_direction.
# ─────────────────────────────────────────────

def _prompt_minimal(brief: dict, product: str, visual: str) -> str:
    """Pure product photography — zero text, generous space."""
    return (
        f"Clean minimal product photography. {product}. "
        f"{visual}. "
        "Soft neutral background, natural lighting, generous white space. "
        "Product is the sole subject. No text, no typography, no copy, no props. "
        "Studio-quality still life. Minimal composition."
    )


def _prompt_bold_type(
    brief: dict, product: str, headline: str, cta: str
) -> str:
    """Text IS the hero. Massive condensed display font dominates the frame."""
    headline_upper = headline.upper() if headline else product.upper()
    short = _truncate(headline_upper, 60)
    return (
        f"Bold typographic Meta ad. "
        f'Oversized condensed display font fills the upper 55% of the frame: "{short}". '
        f"Text colour contrasts strongly against the background. "
        f"{product} is positioned in the lower portion of the image. "
        "The typography IS the primary visual element — product is secondary. "
        "High contrast, strong black or brand-colour text on white or light background. "
        "No body copy. No CTA button. Pure typographic impact."
    )


def _prompt_benefit_stack(
    brief: dict, product: str, headline: str, body: str, cta: str
) -> str:
    """Solid colour-block background, stacked trust/benefit lines with + separators."""
    # Pull 2–3 short benefit fragments to stack
    benefits = []
    if headline:
        benefits.append(_truncate(headline, 40).upper())
    if body:
        first_clause = body.split(".")[0].strip()
        if first_clause:
            benefits.append(_truncate(first_clause, 40).upper())
    if cta:
        benefits.append(_truncate(cta, 30).upper())
    if not benefits:
        benefits = ["GROWER-DIRECT", "FREE SHIPPING", "100% GUARANTEE"]

    stacked = " + ".join(benefits)
    return (
        "Direct response Meta ad with solid warm colour-block background "
        "(bright orange, deep red, or warm terracotta). "
        f"{product} centred in the frame. "
        f'Stacked bold white benefit lines: "{stacked}". '
        "Each benefit on its own line, separated by bold + symbols. "
        "Trust badges and shield icons alongside the text. "
        "Flat design, no photography background, solid colour only. "
        "Large readable typography, high contrast."
    )


def _prompt_social_proof(
    brief: dict, product: str, headline: str, review: str
) -> str:
    """Review card overlay: stars + customer quote + name on product background."""
    product_name = headline or product
    quote = review or (
        "Absolutely incredible — the best I've ever tasted. "
        "Will be ordering again every season."
    )
    short_quote = _truncate(quote, 120)
    return (
        f"Social proof Meta ad. {product} as full-bleed background photography. "
        "White rounded review card overlay covering the lower two-thirds of the image. "
        f'Five gold stars (★★★★★) at the top of the card. '
        f'Bold product name "{product_name}" below the stars. '
        f'Customer quote in smaller text: "{short_quote}". '
        "Customer name below the quote. "
        "Clean trust-building layout, soft drop shadow on card. "
        "Professional, credible, review-focused design."
    )


def _prompt_origin_story(
    brief: dict, product: str, visual: str, headline: str
) -> str:
    """Farm/orchard scenic background with provenance headline."""
    origin_headline = headline or f"FARM-FRESH {product.upper()}"
    short_headline = _truncate(origin_headline.upper(), 50)
    return (
        "Provenance Meta ad. Scenic farm or orchard background — "
        "rows of trees under warm golden light, or rolling fields at harvest. "
        f"{product} in the foreground, freshly harvested. "
        f'Bold headline "{short_headline}" prominently displayed. '
        "Warm authentic farm atmosphere, golden hour lighting. "
        "Origin story composition: background tells the story of where the product grows. "
        "Photography or illustrated/painterly style. "
        "Natural colours, earthy tones, genuine farm aesthetic."
    )


def _prompt_lifestyle_tagline(
    brief: dict, product: str, headline: str
) -> str:
    """Emotional brand statement on clean background — no hard sell."""
    tagline = headline or "Self Care Tastes Good"
    short = _truncate(tagline, 50)
    return (
        f"Lifestyle brand Meta ad. Clean cream or soft white background. "
        f"{product} artfully positioned — cross-section, bowl, or styled arrangement. "
        f'Large elegant brand statement: "{short}". '
        "Clean serif or bold sans-serif typography. "
        "Warm minimal aesthetic, no CTA button, no body copy. "
        "Emotional lifestyle appeal — beauty over conversion. "
        "Single tagline only, generous white space, refined colour palette."
    )


def _prompt_direct_response(
    brief: dict, product: str, headline: str, body: str, cta: str
) -> str:
    """Full conversion layout: bold headline + body + prominent CTA button."""
    headline_upper = (headline or "Order Now").upper()
    cta_upper = (cta or "Shop Now").upper()
    body_short = _truncate(body or "", 80)
    return (
        f"Conversion-focused Meta ad. {product} product photography. "
        f'Large bold headline at the top: "{headline_upper}". '
        + (f'Benefit body copy below: "{body_short}". ' if body_short else "")
        + f'Prominent high-contrast CTA button at bottom center: "{cta_upper}". '
        "Strong visual hierarchy — headline → product → CTA. "
        "Bold typography, high contrast between text and background. "
        "Conversion-optimised layout, text is legible at small sizes."
    )


def _prompt_premium(
    brief: dict, product: str, headline: str
) -> str:
    """Pure white background, product centered, single elegant label."""
    label = _truncate(headline or f"Premium Quality {product}", 50)
    return (
        f"Premium catalog Meta ad. Pure white background. "
        f"{product} perfectly centered, professional studio lighting, "
        "pristine appearance, sharp focus. "
        f'Small elegant label in refined typography: "{label}". '
        "Generous white space, no busy elements. "
        "Luxury catalog aesthetic — like a high-end grocery or specialty food brand. "
        "Minimal, elevated, sophisticated. Single product, single label."
    )


# ─────────────────────────────────────────────
# Dispatcher
# ─────────────────────────────────────────────

def _build_visual_prompt(
    brief: dict[str, Any],
    variant: str,
    headline: str = "",
    body: str = "",
    cta: str = "",
) -> str:
    """Build a structurally distinct image-generation prompt for the given variant."""
    product = brief.get("product_visibility", "").strip()
    visual  = brief.get("visual_direction", "").strip()

    if variant == "minimal":
        return _prompt_minimal(brief, product, visual)
    if variant == "bold_type":
        return _prompt_bold_type(brief, product, headline, cta)
    if variant == "benefit_stack":
        return _prompt_benefit_stack(brief, product, headline, body, cta)
    if variant == "social_proof":
        review = brief.get("social_proof_snippet", "") or ""
        return _prompt_social_proof(brief, product, headline, review)
    if variant == "origin_story":
        return _prompt_origin_story(brief, product, visual, headline)
    if variant == "lifestyle_tagline":
        return _prompt_lifestyle_tagline(brief, product, headline)
    if variant == "direct_response":
        return _prompt_direct_response(brief, product, headline, body, cta)
    if variant == "premium":
        return _prompt_premium(brief, product, headline)

    # Fallback for any unknown variant
    return f"{visual}. {product}. {headline}."


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

    Each variant uses a structurally distinct layout template — background type,
    typography treatment, and text density differ completely across variants.

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
