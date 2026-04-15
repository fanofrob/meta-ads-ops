"""
Prompt builder for the static rendering layer.

Converts a StaticAdBrief dict into a list of RenderSpec dicts
(one per variant strategy). No LLM call — pure string construction.

Each variant has a structurally distinct layout template so the image
generator produces genuinely different ad formats — not the same photo
with different captions.

Style rotation: production_output_id seeds a deterministic but varied
selection of background colours, typography moods, and layout energy
so the same product never gets identical-looking variants across runs.

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
# Style rotation pools
#
# Each pool is cycled via (production_output_id + variant_index) % len(pool)
# so different products and different generations get different looks.
# ─────────────────────────────────────────────

_BG_PALETTES = [
    # (label, hex-ish description for the prompt)
    ("cream",       "warm cream (#F5F0E8), off-white background"),
    ("terracotta",  "warm terracotta (#C4602C), earthy burnt-orange background"),
    ("forest",      "deep forest green (#1B3A2D), rich dark green background"),
    ("navy",        "deep navy (#0D1B2A), dark midnight-blue background"),
    ("honey",       "warm golden honey (#D4922A), amber-yellow background"),
    ("blush",       "dusty blush (#C8856A), muted rose-terracotta background"),
    ("sage",        "muted sage green (#7A9E7E), soft earthy-green background"),
    ("charcoal",    "dark charcoal (#1C1C1C), near-black background"),
    ("rust",        "deep rust red (#8B2E0D), rich burgundy-red background"),
    ("stone",       "warm stone grey (#8E8070), natural warm-grey background"),
]

_TYPO_MOODS = [
    "massive condensed sans-serif, all caps, bold slab-style typography",
    "clean geometric sans-serif, uppercase, modern Swiss style typography",
    "heavy italic condensed font, dynamic angled energy",
    "ultra-bold display font, strong weight contrast, editorial style",
    "tall condensed grotesque, tight tracking, minimal kerning",
    "bold block letters, thick strokes, punchy American diner style",
    "large clean sans-serif, mixed caps and lower case, contemporary feel",
    "strong condensed headline font, sharp edges, high legibility",
]

_LAYOUT_ENERGY = [
    "product centred, text above and below in equal weight",
    "product in upper half, all text stacked in lower half",
    "product as large background element, text overlaid with contrast panel",
    "product small and offset to right, text dominates left two-thirds",
    "product fills left half, bold text column on right",
    "product at bottom, large headline text fills top three-quarters",
    "product centred large, single line of text at very top and very bottom",
    "product partially cropped at edge, text on clean side",
]


def _style(seed: int, pool: list) -> str:
    """Pick a style from a pool using a deterministic seed."""
    return pool[seed % len(pool)][1] if isinstance(pool[0], tuple) else pool[seed % len(pool)]


def _bg_label(seed: int) -> str:
    return _BG_PALETTES[seed % len(_BG_PALETTES)][0]


# ─────────────────────────────────────────────
# Per-variant negative prompts
# ─────────────────────────────────────────────

_BASE_NEGATIVE = (
    "blurry, low quality, distorted, out of focus, pixelated, jpeg artifacts, "
    "oversaturated, fake-looking, watermarks, logos, busy clutter"
)

_VARIANT_NEGATIVES: dict[str, str] = {
    "minimal": (
        "text, typography, headline, body copy, CTA button, words, letters, "
        "captions, watermarks, any text whatsoever, props, accessories, busy background"
    ),
    "bold_type": (
        "small text, illegible font, weak contrast, body copy paragraphs, "
        "cluttered layout, badges, icons, shield symbols"
    ),
    "benefit_stack": (
        "shield badges, checkmark icons, bullet points, plus symbols (+), "
        "decorative icons, ornamental elements, photography background, "
        "illegible small text, gradients that reduce contrast"
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
        "badges, icons, hard-sell language, cluttered design"
    ),
    "direct_response": (
        "unclear CTA, illegible text, weak contrast, unreadable body copy, "
        "overlapping text and product, shield badges, decorative icons"
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
    tokens = re.split(r"[,.\n]+", visual_direction.lower())
    tags: list[str] = []
    for token in tokens:
        words = re.findall(r"\b[a-z]+\b", token)
        for word in words:
            if word in _STYLE_ADJECTIVES and word not in tags:
                tags.append(word)
    return tags[:8]


def _background_style(visual_direction: str) -> str:
    sentence = re.split(r"[.!?\n]", visual_direction.strip())[0]
    return sentence.strip()


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0].rstrip(" ,;:")


# ─────────────────────────────────────────────
# Per-variant visual prompt builders
# Each accepts a style_seed int to drive visual variety.
# ─────────────────────────────────────────────

def _prompt_minimal(brief: dict, product: str, visual: str, style_seed: int) -> str:
    """Pure product photography — zero text, generous space."""
    bg_options = [
        "pure white background, professional studio lighting",
        "soft cream background, warm natural light",
        "pale grey background, diffused studio light, subtle shadow",
        "muted sage background, soft directional light",
        "warm off-white background, window light, gentle shadows",
    ]
    bg = bg_options[style_seed % len(bg_options)]
    return (
        f"Clean minimal product photography. {product}. "
        f"{bg}. Generous white space. "
        "Product is the sole subject. No text, no typography, no copy, no props. "
        f"Studio-quality still life. {visual}."
    )


def _prompt_bold_type(
    brief: dict, product: str, headline: str, cta: str, style_seed: int
) -> str:
    """Text IS the hero. Massive condensed display font dominates the frame."""
    headline_upper = headline.upper() if headline else product.upper()
    short = _truncate(headline_upper, 55)

    typo = _style(style_seed, _TYPO_MOODS)
    bg = _style(style_seed + 1, _BG_PALETTES)

    # Vary text colour based on background darkness
    dark_bgs = {"forest", "navy", "charcoal", "rust"}
    label = _bg_label(style_seed + 1)
    text_colour = "white" if label in dark_bgs else "near-black"

    return (
        f"Bold typographic Meta ad. {bg}. "
        f"{typo}. "
        f'{text_colour} text "{short}" fills the upper 55% of the frame. '
        f"{product} is positioned in the lower portion of the image. "
        "Typography IS the primary visual element — product is secondary. "
        "No body copy, no badges, no icons. Pure typographic impact."
    )


def _prompt_benefit_stack(
    brief: dict, product: str, headline: str, body: str, cta: str, style_seed: int
) -> str:
    """Clean stacked benefit lines on solid colour background. No badges or plus signs.
    CTA stands out as a clearly distinct bottom element."""

    # Extract 2 short benefit lines from headline + body
    benefit1 = _truncate(headline, 40).upper() if headline else product.upper()
    benefit2 = ""
    if body:
        first = body.split(".")[0].strip()
        if first:
            benefit2 = _truncate(first, 40).upper()

    cta_text = _truncate(cta, 28).upper() if cta else "ORDER NOW"

    bg = _style(style_seed, _BG_PALETTES)
    typo = _style(style_seed, _TYPO_MOODS)
    dark_bgs = {"forest", "navy", "charcoal", "rust"}
    label = _bg_label(style_seed)

    # CTA treatment: white box on dark bg, dark box on light bg
    cta_box = (
        "white filled rectangle button" if label in dark_bgs
        else "dark charcoal filled rectangle button"
    )

    return (
        f"Direct response Meta ad. {bg}. "
        f"{product} in the upper portion of the frame. "
        f"{typo}. "
        f'Two clean lines of high-contrast text stacked centrally: '
        f'"{benefit1}" on the first line, '
        + (f'"{benefit2}" on the second line, ' if benefit2 else "")
        + f'then a prominent {cta_box} at the very bottom reading "{cta_text}". '
        "No badge icons. No shield symbols. No plus signs. No decorative elements. "
        "Clean solid background, bold readable text, CTA box clearly separated from benefits."
    )


def _prompt_social_proof(
    brief: dict, product: str, headline: str, review: str, style_seed: int
) -> str:
    """Review card overlay: stars + customer quote + name on product background."""
    product_name = headline or product
    quote = review or (
        "Absolutely incredible — the best I've ever tasted. Will be ordering again every season."
    )
    short_quote = _truncate(quote, 120)

    # Card colour varies
    card_styles = [
        "white card with subtle drop shadow",
        "cream card (#F5F0E8) with rounded corners",
        "very pale yellow card, minimal border",
        "white card, left border accent stripe in brand colour",
    ]
    card = card_styles[style_seed % len(card_styles)]

    return (
        f"Social proof Meta ad. {product} as full-bleed background photography. "
        f"{card} overlay covering the lower two-thirds of the image. "
        f"Five gold stars (★★★★★) at the top of the card. "
        f'Bold product name "{product_name}" below the stars. '
        f'Customer quote: "{short_quote}". '
        "Customer name below in smaller italic text. "
        "Clean trust-building layout. No badges, no shield icons."
    )


def _prompt_origin_story(
    brief: dict, product: str, visual: str, headline: str, style_seed: int
) -> str:
    """Farm/orchard scenic background with provenance headline."""
    origin_headline = headline or f"FARM-FRESH {product.upper()}"
    short_headline = _truncate(origin_headline.upper(), 50)

    scene_styles = [
        "golden hour orchard rows, warm late-afternoon light, rows of trees receding into the distance",
        "misty morning farm field, cool blue-green light, dew on the leaves",
        "sun-drenched hillside grove, bright midday light, rich green foliage",
        "illustrated painterly orchard, warm gouache style, nostalgic farm aesthetic",
        "aerial view of farm rows, geometric patterns, lush green overhead shot",
    ]
    scene = scene_styles[style_seed % len(scene_styles)]

    typo = _style(style_seed, _TYPO_MOODS)

    return (
        f"Provenance Meta ad. {scene}. "
        f"{product} in the foreground, freshly harvested. "
        f'{typo}. Bold headline "{short_headline}" prominently overlaid. '
        "Origin story composition: the background tells where the product grows. "
        "Warm authentic farm atmosphere. Natural colours, genuine farm aesthetic."
    )


def _prompt_lifestyle_tagline(
    brief: dict, product: str, headline: str, style_seed: int
) -> str:
    """Emotional brand statement on clean background — no hard sell."""
    tagline = headline or "Self Care Tastes Good"
    short = _truncate(tagline, 55)

    # Vary background and typography style
    lifestyle_bgs = [
        "clean white background, soft natural light",
        "warm cream background (#F5EDD8), window light",
        "soft blush background, minimal shadows",
        "pale sage green background, airy natural feel",
        "pale warm grey background, clean studio light",
    ]
    bg = lifestyle_bgs[style_seed % len(lifestyle_bgs)]

    typo_styles = [
        "large elegant serif typography",
        "clean bold sans-serif, generous tracking",
        "mixed scale: very large first word, smaller rest of phrase",
        "two-line layout, first line large, second line thin and elegant",
        "single large word per line, stacked vertically",
    ]
    typo = typo_styles[style_seed % len(typo_styles)]

    return (
        f"Lifestyle brand Meta ad. {bg}. "
        f"{product} artfully positioned — cross-section, bowl, or styled arrangement. "
        f'{typo}. Brand statement: "{short}". '
        "No CTA button, no body copy, no badges. "
        "Generous white space, refined and beautiful. Emotional over transactional."
    )


def _prompt_direct_response(
    brief: dict, product: str, headline: str, body: str, cta: str, style_seed: int
) -> str:
    """Full conversion layout: bold headline + benefit body + prominent CTA button."""
    headline_upper = (headline or "Order Now").upper()
    cta_upper = (cta or "Shop Now").upper()
    body_short = _truncate(body or "", 75)

    bg = _style(style_seed + 2, _BG_PALETTES)
    typo = _style(style_seed + 2, _TYPO_MOODS)

    return (
        f"Conversion-focused Meta ad. {bg}. "
        f"{product} product photography. "
        f'{typo}. Large bold headline: "{headline_upper}". '
        + (f'Benefit copy below: "{body_short}". ' if body_short else "")
        + f'Prominent high-contrast CTA button at bottom: "{cta_upper}". '
        "Strong visual hierarchy — headline → product → CTA. "
        "Bold typography, high contrast, legible at small sizes. No badges or icons."
    )


def _prompt_premium(
    brief: dict, product: str, headline: str, style_seed: int
) -> str:
    """Pure white background, product centered, single elegant label."""
    label = _truncate(headline or f"Premium Quality {product}", 50)

    premium_styles = [
        "pure white background, professional studio lighting with soft shadow",
        "pure white background, single directional light, crisp shadow",
        "near-white warm background, floating product with clean shadow",
        "clean white background, backlit glow effect, minimalist",
    ]
    style = premium_styles[style_seed % len(premium_styles)]

    return (
        f"Premium catalog Meta ad. {style}. "
        f"{product} perfectly centered, professional studio lighting, pristine appearance. "
        f'Small elegant label in refined typography: "{label}". '
        "Generous white space, no busy elements. "
        "Luxury catalog aesthetic — high-end specialty food brand. Single product, single label."
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
    style_seed: int = 0,
) -> str:
    """Build a structurally distinct image-generation prompt for the given variant."""
    product = brief.get("product_visibility", "").strip()
    visual  = brief.get("visual_direction", "").strip()

    if variant == "minimal":
        return _prompt_minimal(brief, product, visual, style_seed)
    if variant == "bold_type":
        return _prompt_bold_type(brief, product, headline, cta, style_seed)
    if variant == "benefit_stack":
        return _prompt_benefit_stack(brief, product, headline, body, cta, style_seed)
    if variant == "social_proof":
        review = brief.get("social_proof_snippet", "") or ""
        return _prompt_social_proof(brief, product, headline, review, style_seed)
    if variant == "origin_story":
        return _prompt_origin_story(brief, product, visual, headline, style_seed)
    if variant == "lifestyle_tagline":
        return _prompt_lifestyle_tagline(brief, product, headline, style_seed)
    if variant == "direct_response":
        return _prompt_direct_response(brief, product, headline, body, cta, style_seed)
    if variant == "premium":
        return _prompt_premium(brief, product, headline, style_seed)

    # Fallback
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

    Style seed: derived from production_output_id + variant index so
    different products and different generations get different visual styles.

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

    # Base style seed from production_output_id — different for every product/brief
    base_seed = production_output_id if production_output_id else 0

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

        # Style seed: product-level variation (base_seed) + within-product variant offset
        # This ensures: same product always gets same base palette cluster,
        # but each variant within that product picks a different style index.
        style_seed = base_seed + i * 3

        spec = empty_render_spec()
        spec.update({
            "production_output_id": production_output_id,
            "concept_title":     headline_overlay[:80],
            "visual_prompt":     _build_visual_prompt(
                brief, variant,
                headline=headline_overlay,
                body=body_overlay,
                cta=brief.get("cta", ""),
                style_seed=style_seed,
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
