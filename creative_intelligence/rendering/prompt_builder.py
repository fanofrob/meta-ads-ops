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

Anti-fatigue design principles:
- Every pool has ≥5 options; seed ensures no two adjacent variants match
- Fruit state rotates independently from background and layout
- Premium is ALWAYS on a dark/textured surface (never white like Minimal)
- Craving Macro is always extreme close-up (differentiates from Minimal)
- Supermarket Contrast always has a split composition (unique layout)

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
# Seeded deterministically via (production_output_id + variant_index * 3)
# so different products and runs get different looks, reproducibly.
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

# ── New pools for upgraded variants ──────────────────────────────────

_FRUIT_STATES = [
    "whole, pristine, at peak ripeness",
    "halved to reveal the vivid interior flesh",
    "cross-section showing the full interior texture and colour",
    "freshly cut, juice glistening on the cut surface",
    "a generous natural pile, abundant and inviting",
    "single perfect specimen held in an open hand",
    "sliced and fanned to reveal the interior",
    "slightly imperfect and authentic — picked directly from the tree",
]

_MINIMAL_SURFACES = [
    "pure white background, professional studio lighting, crisp clean shadow",
    "warm cream surface (#F5EDD8), soft window light from the side",
    "pale grey marble surface, subtle veining, diffused studio light",
    "dark grey slate surface, directional side light, strong shadow",
    "raw warm oak wood grain, natural light, organic texture",
    "textured linen or cotton cloth, soft natural fibre, gentle light",
    "terracotta tile, warm earthy texture, afternoon golden light",
    "soft sage green matte surface, diffused overhead light",
]

# Premium uses ONLY dark/textured surfaces — never white (that's Minimal)
_PREMIUM_SURFACES = [
    "dark grey marble with white veining, single directional studio light, "
    "deep shadow creating depth — luxury material grade",
    "polished black slate surface, single bright spotlight, Caravaggio-style "
    "shadow — maximum drama and premium feel",
    "raw walnut wood grain, warm directional light, rich dark tone",
    "deep charcoal matte surface (#1C1C1C), sharp single-source light",
    "warm dark linen cloth (#2A1F1A), soft directional light, "
    "organic luxury texture — like a high-end food magazine",
    "deep forest green matte surface, diffused warm light",
]

_MACRO_DETAILS = [
    "the glistening flesh interior, seeds and fibres visible in sharp detail",
    "juice droplets beading on the cut surface, backlit to catch the light",
    "the skin's texture and natural colour variation across the surface",
    "cross-section interior where colour, density, and moisture are all visible",
    "the stem end and crown, showing farm-fresh authenticity",
    "interior flesh filling the entire frame — colour and texture at maximum",
]

_MACRO_LIGHTING = [
    "dramatic single side-light that catches texture and creates deep shadow",
    "backlit to make juice and moisture glow, dark background",
    "soft overhead light for even colour rendering and clean detail",
    "warm directional window light, slight specular highlight on moisture",
    "studio ring-light close, maximum sharpness and minimal shadow",
]

_BOLD_TYPE_LAYOUTS = [
    # (layout_label, prompt_fragment)
    ("text_top",
     '{text_colour} text "{short}" fills the upper 60% of the frame in {typo}. '
     "{product} sits in the lower 35%, small and supporting."),
    ("text_bottom",
     "{product} fills the upper 60% of the frame — large and vivid. "
     'Bold {typo} text "{short}" anchored in the lower 35%, high contrast.'),
    ("full_bleed_text",
     'Oversized {typo} text "{short}" fills 85% of the entire frame. '
     "{product} ghosted or faded behind the text at 20% opacity — visible but subordinate."),
    ("single_word",
     'A single massive word from the headline — the most powerful one — rendered in {typo} '
     "at 90% frame height. {product} as a small accent element in one corner."),
    ("diagonal_split",
     "The frame split diagonally: {bg} on one side, {product} on the other. "
     '{typo} text "{short}" overlaid across the diagonal boundary.'),
    ("left_right_split",
     "{product} fills the left half of the frame. "
     '{typo} text "{short}" in {text_colour} stacked vertically on the right half, {bg_right}.'),
]

_SOCIAL_PROOF_FORMATS = [
    ("polished_card",
     "white card with subtle drop shadow overlaid in the lower two-thirds"),
    ("screenshot",
     "a raw, slightly imperfect screenshot-style frame — no fancy card, "
     "just authentic platform-agnostic review UI with stars and text"),
    ("pull_quote",
     "oversized pull-quote text fills 60% of the frame in bold italic — "
     "no card border, quote IS the visual hero, product small in corner"),
    ("multi_review",
     "three compact review cards stacked or arranged in a grid, "
     "each with 5 stars and a short quote — volume of proof"),
]

_ORIGIN_SCENES = [
    "real farm photography: golden hour orchard rows, warm late-afternoon sunlight, "
    "rows of fruit trees receding into the distance, photorealistic",
    "real farm photography: close-up of weathered human hands harvesting fruit — "
    "the most authentic farm-to-door proof possible, photorealistic",
    "real aerial photography: geometric farm rows viewed from above — "
    "vivid green crop patterns, sense of scale and abundance",
    "real farm photography: misty morning harvest field, cool blue-green early light, "
    "dew on leaves, authentic farm workers or baskets in background",
    "real outdoor photography: worn harvest crate or basket overflowing with "
    "just-picked fruit, natural outdoor light, farm context behind",
    "real farm photography: sun-drenched hillside grove, bright midday light, "
    "rich green foliage, blue sky, genuine outdoor farming scene",
]

_LIFESTYLE_MOMENTS = [
    "product in a clean bowl on a white marble kitchen counter, soft morning window light",
    "a single hand holding the fruit against a blurred warm kitchen background",
    "fruit on a clean wooden cutting board mid-preparation, knife beside it",
    "fruit arranged naturally on a linen napkin beside a glass of water, "
    "soft natural light, breakfast table setting",
    "fruit in an open box just arrived — hands lifting it, first-reveal moment",
    "plain clean background, product floating with generous space",  # fallback — current style
]

_URGENCY_SIGNALS = [
    "In season now — ships today",
    "While the season lasts",
    "Limited — while in season",
    "Tree-ripened. Ships within 24 hours.",
    "Harvest window closing",
    "Only available this season",
    "Ships at peak ripeness — order today",
]

_SEASON_LABELS = [
    "SEASON STARTS NOW",
    "NOW IN SEASON",
    "LIMITED SEASON",
    "FINAL HARVEST WEEKS",
    "HARVEST IS OPEN",
    "IN SEASON THIS WEEK",
    "LAST CHANCE THIS SEASON",
]

_SEASON_DARK_BGS = [
    "deep forest green (#0D1F14), rich dark organic atmosphere",
    "dark charcoal black (#0F0F0F), stark and dramatic",
    "deep midnight navy (#070D1A), cool premium darkness",
    "rich burgundy-brown (#1A0A05), warm and earthy depth",
    "very dark sage green (#0A140B), moody natural atmosphere",
]


def _style(seed: int, pool: list) -> str:
    """Pick a style from a pool using a deterministic seed."""
    return pool[seed % len(pool)][1] if isinstance(pool[0], tuple) else pool[seed % len(pool)]


def _bg_label(seed: int) -> str:
    return _BG_PALETTES[seed % len(_BG_PALETTES)][0]


def _fruit_state(seed: int) -> str:
    return _FRUIT_STATES[seed % len(_FRUIT_STATES)]


# ─────────────────────────────────────────────
# Per-variant negative prompts
# ─────────────────────────────────────────────

_BASE_NEGATIVE = (
    "blurry, low quality, distorted, out of focus, pixelated, jpeg artifacts, "
    "oversaturated, fake-looking, watermarks, logos, busy clutter, "
    "illustration, digital painting, cartoon, anime, drawn, painted, "
    "watercolor, gouache, vector art, clip art, CGI render, 3D render, "
    # Packaging / boxes — product photos should show the fruit, not the shipping box
    "cardboard box, shipping box, delivery box, kraft box, packaging box, "
    "food packaging, corrugated box, brown box, paper bag, plastic bag, "
    # Platform names — never write platform names on the image
    "Meta, Facebook, Instagram, TikTok, platform logo, social media logo, "
    "website URL, .com, brand watermark, "
    # Generic text noise the image model may hallucinate
    "price tag, barcode, QR code, stock photo watermark, sample text, "
    "lorem ipsum, placeholder text"
)

_VARIANT_NEGATIVES: dict[str, str] = {
    "minimal": (
        "text, typography, headline, body copy, CTA button, words, letters, "
        "captions, watermarks, any text whatsoever, busy background, "
        "multiple props, accessories, food packaging"
    ),
    "bold_type": (
        "small text, illegible font, weak contrast, body copy paragraphs, "
        "cluttered layout, badges, icons, shield symbols, centred vertical poster "
        "with text top and product bottom as the only layout"
    ),
    "benefit_stack": (
        "shield badges, checkmark icons, bullet points, plus symbols (+), "
        "decorative icons, ornamental elements, "
        "illegible small text, gradients that reduce contrast"
    ),
    "social_proof": (
        "empty review card, fake-looking stars, "
        "illegible quote text, cluttered card design, ugly drop shadow, "
        "generic review ('amazing product', 'love it'), no product mention in review"
    ),
    "origin_story": (
        "indoor studio setting, white background, no farm or orchard, "
        "generic background, city background, abstract pattern, "
        "illustration, painting, cartoon, digital art, drawn style, "
        "generic stock photo farm with no authentic detail"
    ),
    "lifestyle_tagline": (
        "busy background, multiple text blocks, CTA button, price tags, "
        "badges, icons, hard-sell language, cluttered design, "
        "product floating on plain white with no context"
    ),
    "direct_response": (
        "unclear CTA, illegible text, weak contrast, unreadable body copy, "
        "overlapping text and product, shield badges, decorative icons, "
        "no urgency or scarcity signal anywhere"
    ),
    "premium": (
        "white background, cream background, light background of any kind, "
        "busy lifestyle scene, colourful props, multiple products, "
        "CTA button, price tags, badges, body copy paragraph, cluttered design, "
        "low-end photography, harsh flash lighting, busy typography"
    ),
    "scroll_stopping": (
        "generic layout, polished corporate design, serious tone, "
        "expected composition, boring headline, professional stock photo feel, "
        "normal ad that looks like every other ad"
    ),
    "craving_macro": (
        "text, typography, headline, body copy, CTA, words, letters, "
        "packaging, boxes, wide shot, full product visible, "
        "white studio background, styled food photography props, "
        "perfect symmetry, product floating in centre, generic composition"
    ),
    "supermarket_contrast": (
        "single product only, no comparison, no split frame, "
        "generic product shot, uniform colour temperature across whole image, "
        "both halves looking the same quality"
    ),
    "seasonal_drop": (
        "bright cheerful background, white background, light background, "
        "generic product shot, no urgency language, no season reference, "
        "cluttered text, multiple competing elements, soft pastel tones"
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
    """Truncate at max_chars, preferring a sentence boundary over a raw word cut."""
    if len(text) <= max_chars:
        return text
    window = text[:max_chars]
    for ch in (".", "!", "?"):
        idx = window.rfind(ch)
        if idx > max_chars // 2:
            return window[:idx + 1].strip()
    clipped = window.rsplit(" ", 1)[0].rstrip(" ,;:")
    _DANGLERS = re.compile(
        r"\s+(of|with|in|on|at|by|to|a|an|the|and|or|but|for|nor|so|yet|from|"
        r"into|onto|upon|that|which|who|whose|about|than|as|if|whether|"
        r"via|per|vs|plus|minus|x|times)$",
        re.IGNORECASE,
    )
    clipped = _DANGLERS.sub("", clipped).rstrip(" ,;:")
    return clipped


def _clean_body_for_overlay(body: str, max_chars: int = 90) -> str:
    """Extract the first complete sentence from body copy for image overlay text."""
    if not body:
        return ""
    first_sentence = re.split(r"(?<=[.!?])\s+", body.strip())[0].strip()
    return _truncate(first_sentence, max_chars)


def _clean_product_desc(product_visibility: str) -> str:
    """Strip packaging/box references from product_visibility before using in image prompts."""
    text = product_visibility

    # Strip "... [preposition] [article] [adjective] box/packaging/crate/..."
    text = re.sub(
        r"[,\s]*(?:(?:in|inside|with|next to|alongside|beside|and)\s+)?"
        r"(?:(?:its|their|a|an|the)\s+)?"
        r"(?:(?:kraft|shipping|delivery|cardboard|corrugated|paper|gift|wooden)\s+)*"
        r"(?:box(?:es)?|packaging|package|crate|container|bags?)"
        r"(?:\s+(?:packaging|box))?",
        "",
        text,
        flags=re.IGNORECASE,
    )

    # Strip trailing dangling prepositions/articles
    text = re.sub(
        r"[,\s]*\b(?:in|inside|with|next\s+to|next|alongside|beside|and|"
        r"its|their|a|an|the|to|of|from)\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r",\s*,", ",", text)
    text = text.strip().rstrip(",;: ")
    return text or product_visibility


# ─────────────────────────────────────────────
# Per-variant visual prompt builders
# ─────────────────────────────────────────────

def _prompt_minimal(brief: dict, product: str, visual: str, style_seed: int) -> str:
    """
    Pure product photography. No text. Maximum product desirability.
    Surface, crop depth, and fruit state all rotate to prevent repetition.
    """
    surface = _MINIMAL_SURFACES[style_seed % len(_MINIMAL_SURFACES)]
    state   = _fruit_state(style_seed + 2)

    # Vary crop type every 3 seeds
    crop_options = [
        "product fills 60–75% of frame, slight negative space around it",
        "tight product close-up — fills 80–90% of frame, minimal background visible",
        "generous composition — product occupies 40% of frame, surface texture dominant",
        "overhead flat-lay composition, product centred from directly above",
        "slight elevated angle, soft 3D perspective, product as still-life hero",
    ]
    crop = crop_options[style_seed % len(crop_options)]

    return (
        f"Clean minimal product photography. {product}, {state}. "
        f"{surface}. {crop}. "
        "Product is the sole subject — no text, no props, no packaging. "
        "Studio-quality still life. Sharp focus, natural colour accuracy. "
        "Every visual detail should create craving. "
        f"{visual}."
    )


def _prompt_bold_type(
    brief: dict, product: str, headline: str, cta: str, style_seed: int
) -> str:
    """
    Text IS the hero. Massive display font dominates. 6 layout modes rotate.
    """
    headline_upper = headline.upper() if headline else product.upper()
    short = _truncate(headline_upper, 55)

    typo  = _style(style_seed, _TYPO_MOODS)
    bg    = _style(style_seed + 1, _BG_PALETTES)
    dark_bgs = {"forest", "navy", "charcoal", "rust"}
    label = _bg_label(style_seed + 1)
    text_colour = "white" if label in dark_bgs else "near-black"

    # bg_right for left-right split
    bg_right_label = _bg_label(style_seed + 2)
    bg_right = _style(style_seed + 2, _BG_PALETTES)

    # Pick layout mode — 6 options, never defaults to same one
    layout_idx = style_seed % len(_BOLD_TYPE_LAYOUTS)
    _, layout_template = _BOLD_TYPE_LAYOUTS[layout_idx]

    layout = layout_template.format(
        short=short,
        typo=typo,
        product=product,
        bg=bg,
        bg_right=bg_right,
        text_colour=text_colour,
    )

    return (
        f"Bold typographic ad. {bg}. "
        f"{layout} "
        "Typography IS the primary visual element — high contrast, "
        "impossible to miss at thumbnail size. "
        "No body copy, no badges, no icons. Pure typographic impact."
    )


def _prompt_benefit_stack(
    brief: dict, product: str, headline: str, body: str, cta: str, style_seed: int
) -> str:
    """
    Fast objection elimination. 3 modes: vertical stack (current),
    product-as-background, horizontal rails.
    """
    benefit1 = _truncate(headline, 40).upper() if headline else product.upper()
    benefit2 = ""
    if body:
        first = _clean_body_for_overlay(body, max_chars=45)
        if first:
            benefit2 = first.upper()

    cta_text = _truncate(cta, 28).upper() if cta else "ORDER NOW"
    bg = _style(style_seed, _BG_PALETTES)
    typo = _style(style_seed, _TYPO_MOODS)
    dark_bgs = {"forest", "navy", "charcoal", "rust"}
    lbl = _bg_label(style_seed)
    cta_box = (
        "white filled rectangle button" if lbl in dark_bgs
        else "dark charcoal filled rectangle button"
    )

    # 3 layout modes
    layout_mode = style_seed % 3
    if layout_mode == 0:
        # Vertical centred stack (classic)
        benefit_block = (
            f'Two bold benefit lines stacked centrally: '
            f'"{benefit1}" on the first line'
            + (f', "{benefit2}" on the second line' if benefit2 else "")
            + f'. Prominent {cta_box} at the very bottom reading "{cta_text}".'
        )
        product_placement = f"{product} in the upper portion of the frame."
    elif layout_mode == 1:
        # Product as large background, text column on right side
        benefit_block = (
            f'{product} fills the full frame as the background — vivid, '
            f'slightly darkened on the right half with a colour overlay. '
            f'Bold benefit text column on the right: "{benefit1}"'
            + (f' / "{benefit2}"' if benefit2 else "")
            + f'. {cta_box} reading "{cta_text}" at the bottom of the text column.'
        )
        product_placement = ""
    else:
        # Horizontal benefit rail — benefits as compact pills/bars
        benefit_block = (
            f'{product} centred large as the hero. '
            f'Below the product: three horizontal benefit bars stacked, '
            f'each in {cta_box[:4]} with high-contrast text. '
            f'First bar: "{benefit1}". '
            + (f'Second bar: "{benefit2}". ' if benefit2 else "")
            + f'Bottom: CTA bar with "{cta_text}".'
        )
        product_placement = ""

    return (
        f"Direct response ad. {bg}. "
        + (f"{product_placement} " if product_placement else "")
        + f"{typo}. {benefit_block} "
        "No badge icons. No shield symbols. No plus signs. "
        "Clean design with maximum legibility."
    )


def _prompt_social_proof(
    brief: dict, product: str, headline: str, review: str, style_seed: int
) -> str:
    """
    Third-party trust. 4 format modes: polished card, screenshot, pull-quote, multi-review.
    """
    product_name = headline or product
    quote = review or (
        "Absolutely incredible — the best I've ever tasted. "
        "Nothing from a grocery store comes close. Ordering every season."
    )
    short_quote = _truncate(quote, 120)
    short_quote_punchy = _truncate(quote, 60)  # for pull-quote and screenshot

    fmt_idx = style_seed % len(_SOCIAL_PROOF_FORMATS)
    fmt_label, fmt_desc = _SOCIAL_PROOF_FORMATS[fmt_idx]

    if fmt_label == "polished_card":
        return (
            f"Social proof ad. {product} as full-bleed background photography, vivid. "
            f"{fmt_desc} covering the lower two-thirds of the image. "
            f"Five gold stars (★★★★★) at the top of the card. "
            f'Bold product name "{product_name}" below the stars. '
            f'Customer quote: "{short_quote}". '
            "Customer name below in smaller italic text. "
            "Clean trust-building layout. No badges, no shield icons."
        )
    elif fmt_label == "screenshot":
        return (
            f"Social proof ad — screenshot format. "
            f"{product} as the full-bleed background, vivid and desirable. "
            f"{fmt_desc} overlaid. "
            f"Five star rating clearly shown. "
            f'Review text: "{short_quote_punchy}". '
            "Looks like a real customer review screenshot, not a polished brand design. "
            "Imperfect authenticity is the point. No professional card styling."
        )
    elif fmt_label == "pull_quote":
        return (
            f"Social proof ad — oversized pull-quote format. "
            f"{product} visible but small, in one corner. "
            f'{fmt_desc}: large bold italic quote text fills the frame: '
            f'"{short_quote_punchy}". '
            "Five stars above the quote. The quote IS the dominant visual. "
            "High contrast — quote is the hero, product is the proof."
        )
    else:  # multi_review
        return (
            f"Social proof ad — multiple reviews. "
            f"{product} fills the background. "
            f"{fmt_desc}. "
            f'Each card shows ★★★★★ and a short quote. '
            f'Main quote: "{short_quote_punchy}". '
            "Volume of social proof is the visual strategy. "
            "Feels like everyone loves this product."
        )


def _prompt_origin_story(
    brief: dict, product: str, visual: str, headline: str, style_seed: int
) -> str:
    """
    Farm provenance trust. Rotates through: orchard rows, human hands,
    aerial geometry, harvest baskets. All photorealistic.
    """
    origin_headline = headline or f"TREE-RIPENED {product.upper()}"
    short_headline = _truncate(origin_headline.upper(), 50)
    scene = _ORIGIN_SCENES[style_seed % len(_ORIGIN_SCENES)]
    typo = _style(style_seed, _TYPO_MOODS)

    return (
        f"Origin story ad. {scene}. "
        f"Foreground: {product}, freshly harvested, photorealistic. "
        f'{typo}. Bold white or light headline text overlaid: "{short_headline}". '
        "The background scene tells the authentic story of where this product is grown. "
        "Real, specific, believable — NOT a generic stock farm photo. "
        "Warm, authentic, farm atmosphere. No cartoon, no illustration, no studio setting."
    )


def _prompt_lifestyle_tagline(
    brief: dict, product: str, headline: str, style_seed: int
) -> str:
    """
    Emotional brand resonance. Product is now IN a lifestyle moment —
    not floating on a plain colour.
    """
    tagline = headline or "Self Care Tastes Good"
    short = _truncate(tagline, 55)

    moment = _LIFESTYLE_MOMENTS[style_seed % len(_LIFESTYLE_MOMENTS)]

    typo_styles = [
        "oversized bold sans-serif, the tagline fills the top 55% of the frame",
        "large condensed serif, two or three lines stacked, dominant text presence",
        "massive clean sans-serif, tagline split across two lines, editorial feel",
        "bold editorial headline font, tagline left-aligned, dominant but airy",
        "tall bold condensed font, tagline stacked line by line, text-forward layout",
    ]
    typo = typo_styles[style_seed % len(typo_styles)]

    return (
        f"Lifestyle brand ad. {moment}. "
        f"The hero of this scene is {product} — it must be immediately recognisable and beautiful. "
        f"Show {product} specifically, not generic mixed fruit. "
        f"The {product} is the centrepiece of the lifestyle moment — prominent, detailed, photorealistic. "
        f'{typo}. The brand statement "{short}" overlaid — bold and emotionally resonant. '
        "No CTA button, no body copy paragraph, no badges, no price. "
        "Generous breathing room. Aspirational, emotional, brand-building."
    )


def _prompt_direct_response(
    brief: dict, product: str, headline: str, body: str, cta: str, style_seed: int
) -> str:
    """
    Full conversion toolkit. Always includes a real urgency/scarcity signal.
    """
    headline_upper = (headline or "Order Now").upper()
    cta_upper = (cta or "Shop Now").upper()

    bg   = _style(style_seed + 2, _BG_PALETTES)
    typo = _style(style_seed + 2, _TYPO_MOODS)
    dark_bgs = {"forest", "navy", "charcoal", "rust"}
    lbl = _bg_label(style_seed + 2)
    cta_colour = (
        "bright orange (#FF6B2B) filled rectangle" if lbl not in dark_bgs
        else "white filled rectangle"
    )

    body_clean = _clean_body_for_overlay(body or "", max_chars=80)
    urgency = _URGENCY_SIGNALS[style_seed % len(_URGENCY_SIGNALS)]

    return (
        f"Conversion-focused ad. {bg}. "
        f"Photorealistic {product} product photography in the centre of the frame. "
        f'{typo}. Large bold headline at the top: "{headline_upper}". '
        + (f'Benefit copy below headline: "{body_clean}". ' if body_clean else "")
        + f'Urgency line in smaller text: "{urgency}". '
        + f'Prominent {cta_colour} CTA button at the very bottom: "{cta_upper}". '
        "Strong visual hierarchy — headline → product → urgency → CTA button. "
        "CTA button must be clearly visible and high-contrast. "
        "Bold typography, legible at small sizes. No badges or decorative icons."
    )


def _prompt_premium(
    brief: dict, product: str, headline: str, style_seed: int
) -> str:
    """
    Price justification through luxury aesthetic.
    ALWAYS on dark/textured surface — never white (that's Minimal).
    """
    label = _truncate(headline or product, 50)
    surface = _PREMIUM_SURFACES[style_seed % len(_PREMIUM_SURFACES)]
    state   = _fruit_state(style_seed + 1)

    # Prefer cross-section for interior proof — alternate with whole
    product_treatment = [
        f"{product}, cross-section showing full interior, centred with generous space",
        f"{product}, {state}, offset slightly for visual tension",
        f"{product} cross-section as the hero — interior visible, beautifully lit",
        f"single perfect {product} specimen, {state}, product fills 50% of frame",
        f"{product} cross-section beside a whole specimen — duality shot",
    ]
    treatment = product_treatment[style_seed % len(product_treatment)]

    typo_options = [
        "small refined serif label below the product, elegant letter-spacing",
        "clean lightweight sans-serif, uppercase, generous tracking, small size",
        "two lines: product name in small medium serif above, descriptor in small caps below",
        "single centred label in tall elegant serif, understated luxury",
    ]
    typo = typo_options[style_seed % len(typo_options)]

    return (
        f"Premium product catalog ad. {surface}. "
        f"Photorealistic {treatment}. "
        f'{typo}. Label text: "{label}". '
        "Generous breathing space throughout. "
        "High-end specialty food brand aesthetic — think luxury fruit gifting. "
        "Professional studio photography, dramatic shadows, pristine product. "
        "No CTA button, no body copy, no badges. No white backgrounds."
    )


def _prompt_scroll_stopping(
    brief: dict, product: str, headline: str, body: str, style_seed: int
) -> str:
    """
    Pattern interrupt. 5 sub-formats including WARNING label and rotated text.
    All copy is fruit-specific — not generic.
    """
    fmt = style_seed % 5

    if fmt == 0:
        # ── 1-star review format ──────────────────────────────────────
        complaints = [
            f"1 star. I ate the entire {product} before my family got home.",
            f"1 star. I can't stop ordering {product} and my bank account hates it.",
            "1 star. Now I can't eat supermarket fruit anymore. Thanks a lot.",
            "1 star. My nutritionist said to slow down. I showed her the fruit first.",
            "1 star. I drove 40 minutes to get more after the first bite.",
            "1 star. Tasted like candy. This is not normal fruit behaviour.",
            "1 star. My children look at me differently now. Worth it.",
        ]
        complaint = complaints[style_seed % len(complaints)]
        bg_options = [
            "clean white background",
            "warm cream background (#F5EDD8)",
            "very pale yellow background",
        ]
        bg = bg_options[style_seed % len(bg_options)]
        return (
            f"Scroll-stopping ad. {bg}. Photorealistic {product} in the upper portion. "
            f"A single large gold star ★ prominently displayed — forces a double-take. "
            f'Below the star: bold text "{complaint}". '
            "Clean layout, high-contrast black text on light background. "
            "The joke lands instantly — unexpected 1-star review format. "
            "No CTA, no badges. The surprise IS the hook."
        )

    elif fmt == 1:
        # ── Wild/ridiculous claim ─────────────────────────────────────
        claim = headline or f"WARNING: Dangerously good {product}"
        short_claim = _truncate(claim, 60).upper()
        chaos_bgs = [
            "bright acid yellow (#FFE600), high energy",
            "electric lime green (#CCFF00), bold and loud",
            "hot coral (#FF4D4D), attention-grabbing",
            "vivid cyan (#00E5FF), unexpected pop of colour",
        ]
        bg = chaos_bgs[style_seed % len(chaos_bgs)]
        fine_prints = [
            "*not responsible for repeat purchases.",
            "*side effects: happiness, re-ordering, telling everyone.",
            "*warning: highly habit-forming. You were warned.",
            "*results may vary. They might be even better.",
        ]
        fine_print = fine_prints[style_seed % len(fine_prints)]
        return (
            f"Scroll-stopping ad. {bg}. Photorealistic {product}, bold and prominent. "
            f'Massive all-caps bold condensed typography: "{short_claim}" fills 70% of the frame. '
            f'Very small fine-print at the bottom: "{fine_print}". '
            "Unexpectedly bold colour creates visual shock. "
            "Feels like a parody warning label — product looks genuinely incredible. "
            "No traditional CTA. The energy IS the call to action."
        )

    elif fmt == 2:
        # ── Absurd stat / ridiculous fact ────────────────────────────
        stat = body or headline or f"97% of people who tried {product} never went back to supermarkets"
        short_stat = _clean_body_for_overlay(stat, max_chars=70)
        stat_bgs = [
            "deep black, single dramatic spotlight on product",
            "very dark navy (#0A0E1A), moody and cinematic",
            "rich dark forest green (#0D1F14), bold and unexpected",
        ]
        bg = stat_bgs[style_seed % len(stat_bgs)]
        return (
            f"Scroll-stopping ad. {bg}. Photorealistic {product}, dramatically lit. "
            f'Oversized bold stat dominates the upper frame: "{short_stat}". '
            "Typography: massive, punchy, white on dark — impossible to ignore. "
            "Dark dramatic background makes the product and text glow. "
            "No CTA, no badges. Pure pattern interrupt."
        )

    elif fmt == 3:
        # ── WARNING label format ──────────────────────────────────────
        warning = headline or f"EXTREMELY ADDICTIVE {product.upper()}"
        short_warning = _truncate(warning.upper(), 50)
        return (
            f"Scroll-stopping ad. Black and yellow hazard-stripe background — "
            f"caution tape aesthetic, high-energy warning label design. "
            f"Photorealistic {product} as the hero visual. "
            f'Massive bold text: "⚠️ WARNING" above the product. '
            f'Below the product: "{short_warning}". '
            "Feels like a health warning but the message is positive and funny. "
            "Unexpected industrial design language applied to premium fruit. "
            "No CTA button. The format IS the hook."
        )

    else:
        # ── Rotated / tilted sticker format ──────────────────────────
        claim = headline or f"Just tried {product} for the first time"
        short_claim = _truncate(claim, 55)
        return (
            f"Scroll-stopping ad. Clean white or cream background. "
            f"Photorealistic {product} in the centre. "
            f'A bold sticker-style text label applied at a slight angle (10–15° tilt): '
            f'"{short_claim}". '
            "The sticker feels hand-placed, organic, like user-generated content. "
            "Unexpected informal energy against a clean product shot. "
            "Bold marker-style font or condensed display font for the sticker text."
        )


# ─────────────────────────────────────────────
# NEW: Three high-impact variants
# ─────────────────────────────────────────────

def _prompt_craving_macro(
    brief: dict, product: str, style_seed: int
) -> str:
    """
    Extreme close-up. Pure sensory desire. No text whatsoever.
    Interior texture, juice, flesh — the most visceral food photography possible.
    Differentiates from Minimal by being RAW, macro, and always showing interior.
    """
    detail = _MACRO_DETAILS[style_seed % len(_MACRO_DETAILS)]
    lighting = _MACRO_LIGHTING[style_seed % len(_MACRO_LIGHTING)]

    # Alternate between dark and natural backgrounds for variety
    bg_options = [
        "very dark background, almost black, product lit from the side",
        "dark slate surface, product lit dramatically",
        "out-of-focus natural foliage in the background, product sharp in foreground",
        "deep forest green background, soft backlight catching the moisture",
        "clean white background — only option for extreme macro skin texture shots",
    ]
    bg = bg_options[style_seed % len(bg_options)]

    # Fruit state — always interior or texture for this variant
    macro_states = [
        "cut open, interior facing the camera",
        "halved, flesh visible, juice present",
        "cross-section, interior filling the frame",
        "freshly sliced, juice on the cut surface",
        "pulled apart to reveal interior, organic breaking point visible",
    ]
    state = macro_states[style_seed % len(macro_states)]

    return (
        f"Extreme macro food photography. {product}, {state}. "
        f"Camera extremely close — {detail}. "
        f"{lighting}. {bg}. "
        "The image is pure sensory desire — texture, colour, moisture, and freshness "
        "at maximum resolution. Shoot as if the viewer can almost taste it. "
        "No text. No typography. No props. No packaging. No branding. "
        "This is food photography as desire ignition — nothing else."
    )


def _prompt_supermarket_contrast(
    brief: dict, product: str, headline: str, style_seed: int
) -> str:
    """
    Split-frame visual argument. Left: dull supermarket fruit. Right: vivid ripe ours.
    Destroys the 'I can just get this at the store' objection visually.
    """
    comparison_headlines = [
        f"Not all {product} are equal",
        f"This is what ripe actually looks like",
        f"Your supermarket {product} vs ours",
        f"Same fruit. Different world.",
        f"The difference is visible before you taste it",
    ]
    comp_headline = headline or comparison_headlines[style_seed % len(comparison_headlines)]
    short = _truncate(comp_headline, 55)

    # Divide options
    divide_styles = [
        "sharp vertical line dividing the frame exactly in half",
        "hard diagonal divide from top-left to bottom-right",
        "horizontal divide — top half vs bottom half",
    ]
    divide = divide_styles[style_seed % len(divide_styles)]

    left_labels = ["STORE-BOUGHT", "SUPERMARKET", "AVERAGE", "WHAT YOU'RE USED TO"]
    right_labels = ["OURS", "TREE-RIPENED", "FARM-DIRECT", "WHAT RIPE TASTES LIKE"]
    left_lbl = left_labels[style_seed % len(left_labels)]
    right_lbl = right_labels[style_seed % len(right_labels)]

    return (
        f"Split-frame comparison ad. {divide}. "
        f"LEFT HALF: {product} — desaturated, pale, slightly waxy, over-bright flat lighting — "
        f"the dull underwhelming version. Small label at top: \"{left_lbl}\". "
        f"RIGHT HALF: {product} — vivid, deeply saturated, rich colour, glistening with moisture, "
        f"warm directional light — this is fruit at perfect ripeness. "
        f"Small label at top: \"{right_lbl}\". "
        f'Headline text below both halves or across the divide: "{short}". '
        "The visual contrast must be immediately legible at thumbnail size. "
        "Left side: cool, flat, desaturated. Right side: warm, vivid, dramatic. "
        "No body copy. The image makes the argument."
    )


def _prompt_seasonal_drop(
    brief: dict, product: str, headline: str, body: str, style_seed: int
) -> str:
    """
    Product-drop energy. Limited season = immediate FOMO.
    Dark atmospheric background, bold season signal, urgency copy.
    Differentiates from Direct Response by being time-specific and emotionally charged.
    """
    season_label = _SEASON_LABELS[style_seed % len(_SEASON_LABELS)]
    dark_bg = _SEASON_DARK_BGS[style_seed % len(_SEASON_DARK_BGS)]
    urgency = _URGENCY_SIGNALS[(style_seed + 2) % len(_URGENCY_SIGNALS)]

    season_headline = headline or season_label

    # Body copy for urgency
    urgency_copy = _clean_body_for_overlay(body or "", max_chars=60) or urgency

    typo_options = [
        "ultra-bold condensed display font, all caps",
        "massive heavy sans-serif, aggressive weight",
        "bold slab-serif, punchy and authoritative",
    ]
    typo = typo_options[style_seed % len(typo_options)]

    return (
        f"Seasonal drop product ad. {dark_bg}. "
        f"Photorealistic {product}, dramatically lit — product glows against the dark background. "
        f"Atmosphere: premium, urgent, exclusive — like a limited product release. "
        f'{typo}. Bold season announcement at top: "{season_headline.upper()}". '
        f'Product as the central hero visual, lit to look extraordinary. '
        f'Urgency copy at bottom: "{urgency_copy}". '
        "Strong visual hierarchy: announcement → product → urgency. "
        "Dark, moody, atmospheric — this fruit is seasonal and rare. "
        "No white backgrounds. No cheerful colours. Dramatic and exclusive."
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
    product = _clean_product_desc(brief.get("product_visibility", "").strip())
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
    if variant == "scroll_stopping":
        return _prompt_scroll_stopping(brief, product, headline, body, style_seed)
    if variant == "craving_macro":
        return _prompt_craving_macro(brief, product, style_seed)
    if variant == "supermarket_contrast":
        return _prompt_supermarket_contrast(brief, product, headline, style_seed)
    if variant == "seasonal_drop":
        return _prompt_seasonal_drop(brief, product, headline, body, style_seed)

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
    product_learnings: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Build one RenderSpec dict per variant strategy.

    Style seed: derived from production_output_id + variant index so
    different products and different generations get different visual styles.
    """
    headline_options  = brief.get("headline_options") or []
    variant_headlines = brief.get("variant_headlines") or {}
    body_options      = brief.get("body_options") or []
    hook              = brief.get("hook", "")

    shot_type        = _detect_shot_type(brief.get("composition_notes", ""))
    background_style = _background_style(brief.get("visual_direction", ""))
    style_tags       = _extract_style_tags(brief.get("visual_direction", ""))

    base_seed = production_output_id if production_output_id else 0

    _avoid_suffix = ""
    _prefer_suffix = ""
    if product_learnings:
        avoids  = [l for l in product_learnings if l.get("type") == "avoid"]
        prefers = [l for l in product_learnings if l.get("type") == "prefer"]
        if avoids:
            _avoid_suffix = " Avoid: " + "; ".join(l["summary"] for l in avoids) + "."
        if prefers:
            _prefer_suffix = " Prefer: " + "; ".join(l["summary"] for l in prefers) + "."

    selected = [v for v in variants if v in VARIANT_STRATEGIES]

    specs: list[dict[str, Any]] = []
    for i, variant in enumerate(selected):
        # Prefer variant-specific headline; fall back to rotating headline_options, then hook
        if variant_headlines.get(variant):
            headline_overlay = variant_headlines[variant]
        elif headline_options:
            headline_overlay = headline_options[i % len(headline_options)]
        else:
            headline_overlay = hook

        body_overlay = body_options[i % len(body_options)] if body_options else ""

        # Style seed: product-level base + within-product variant offset (×3 for wider spread)
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
            ) + _prefer_suffix,
            "negative_prompt":   _build_negative_prompt(variant) + _avoid_suffix,
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
