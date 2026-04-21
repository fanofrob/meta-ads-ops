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
    "oversaturated, fake-looking, watermarks, logos, busy clutter, "
    "illustration, digital painting, cartoon, anime, drawn, painted, "
    "watercolor, gouache, vector art, clip art, CGI render, 3D render, "
    # Packaging / boxes — product photos should show the fruit, not the shipping box
    "cardboard box, shipping box, delivery box, kraft box, packaging box, "
    "food packaging, corrugated box, brown box, paper bag, plastic bag, "
    # Platform names — never write 'Meta', 'Facebook', 'Instagram' on the image
    "Meta, Facebook, Instagram, TikTok, platform logo, social media logo, "
    "website URL, .com, brand watermark, "
    # Generic text noise the image model may hallucinate
    "price tag, barcode, QR code, stock photo watermark, sample text, "
    "lorem ipsum, placeholder text"
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
        "generic background, city background, abstract pattern, "
        "illustration, painting, cartoon, digital art, drawn style"
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
        "busy background, lifestyle scene, colourful props, multiple products, "
        "CTA button, price tags, badges, body copy paragraph, cluttered design, "
        "low-end photography, harsh lighting, busy typography"
    ),
    "scroll_stopping": (
        "generic layout, polished corporate design, serious tone, "
        "expected composition, boring headline, professional stock photo feel"
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
    # First try to cut at a sentence boundary (., !, ?) within the limit
    window = text[:max_chars]
    for ch in (".", "!", "?"):
        idx = window.rfind(ch)
        if idx > max_chars // 2:          # sentence is at least half the limit — keep it
            return window[:idx + 1].strip()
    # Fall back to word boundary, but strip trailing function words that read as incomplete
    clipped = window.rsplit(" ", 1)[0].rstrip(" ,;:")
    # Drop trailing prepositions / conjunctions / articles that would leave dangling text
    _DANGLERS = re.compile(
        r"\s+(of|with|in|on|at|by|to|a|an|the|and|or|but|for|nor|so|yet|from|"
        r"into|onto|upon|that|which|who|whose|about|than|as|if|whether|"
        r"via|per|vs|plus|minus|x|times)$",
        re.IGNORECASE,
    )
    clipped = _DANGLERS.sub("", clipped).rstrip(" ,;:")
    return clipped


def _clean_body_for_overlay(body: str, max_chars: int = 90) -> str:
    """Extract the first complete sentence from body copy, suitable for image overlay text.

    Image generators render text literally — incomplete clauses look broken on the ad.
    Takes the first sentence (split on .!?) and truncates if needed.
    """
    if not body:
        return ""
    # Take first complete sentence
    first_sentence = re.split(r"(?<=[.!?])\s+", body.strip())[0].strip()
    # If the sentence itself is too long, truncate it cleanly
    return _truncate(first_sentence, max_chars)


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
        f"Bold typographic ad. {bg}. "
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

    # Extract 2 complete, self-contained benefit lines
    benefit1 = _truncate(headline, 45).upper() if headline else product.upper()
    benefit2 = ""
    if body:
        first = _clean_body_for_overlay(body, max_chars=50)
        if first:
            benefit2 = first.upper()

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
        f"Direct response ad. {bg}. "
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
        f"Social proof ad. {product} as full-bleed background photography. "
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
    """Real farm/orchard photo background with provenance headline overlay."""
    origin_headline = headline or f"FARM-FRESH {product.upper()}"
    short_headline = _truncate(origin_headline.upper(), 50)

    # All photorealistic — no illustration options
    scene_styles = [
        "real farm photography: golden hour orchard rows, warm late-afternoon sunlight, "
        "rows of fruit trees receding into the distance, photorealistic",
        "real farm photography: misty morning harvest field, cool blue-green early light, "
        "dew on leaves, authentic farm workers or baskets in background",
        "real farm photography: sun-drenched hillside grove, bright midday light, "
        "rich green foliage, blue sky, genuine outdoor farming scene",
        "real outdoor photography: farm stand or crate of fresh-picked fruit, "
        "natural wood textures, golden outdoor light",
        "real farm photography: aerial-style view of lush green crop rows, "
        "geometric farm patterns, vibrant natural colours, genuine landscape",
    ]
    scene = scene_styles[style_seed % len(scene_styles)]

    typo = _style(style_seed, _TYPO_MOODS)

    return (
        f"Origin story ad. {scene}. "
        f"Foreground: {product}, freshly harvested, photorealistic. "
        f'{typo}. Bold white or light headline text overlaid: "{short_headline}". '
        "The background scene tells the story of where the product is grown. "
        "Warm, authentic, real-world farm atmosphere. No cartoon, no illustration."
    )


def _prompt_lifestyle_tagline(
    brief: dict, product: str, headline: str, style_seed: int
) -> str:
    """Emotional brand statement — large dominant text, product as supporting element."""
    tagline = headline or "Self Care Tastes Good"
    short = _truncate(tagline, 55)

    # Varied but always clean, minimal backgrounds
    lifestyle_bgs = [
        "warm cream background (#F5EDD8), soft natural window light from the side",
        "clean white background, diffused studio light, gentle product shadow",
        "soft warm beige background, bright airy feel",
        "pale sage green background (#E8EDE3), soft natural light",
        "light warm grey background, clean minimal studio atmosphere",
    ]
    bg = lifestyle_bgs[style_seed % len(lifestyle_bgs)]

    # Typography: the tagline should be the hero, large and dominant
    typo_styles = [
        "oversized bold sans-serif, the tagline fills the top 60% of the frame",
        "large condensed serif, two or three lines stacked, dominant text presence",
        "massive clean sans-serif, first line very large, second line slightly smaller",
        "bold editorial headline font, tagline split across two lines, left-aligned",
        "tall bold condensed font, tagline stacked line by line, text-forward layout",
    ]
    typo = typo_styles[style_seed % len(typo_styles)]

    # Product positioning: supporting, not competing with the text
    product_pos = [
        "product in the lower third, small and clean",
        "product partially cropped at bottom edge, supporting the text above",
        "product as a small styled accent in one corner",
        "product centered below the tagline with generous white space",
    ]
    pos = product_pos[style_seed % len(product_pos)]

    return (
        f"Lifestyle brand ad. {bg}. "
        f"{typo}. The brand statement \"{short}\" is the primary visual element — bold and dominant. "
        f"Photorealistic {product}, {pos}. "
        "No CTA button, no body copy paragraph, no badges, no price. "
        "Generous white space. Emotional, aspirational, brand-building."
    )


def _prompt_direct_response(
    brief: dict, product: str, headline: str, body: str, cta: str, style_seed: int
) -> str:
    """Full conversion layout: bold headline + benefit body + prominent CTA button."""
    headline_upper = (headline or "Order Now").upper()
    cta_upper = (cta or "Shop Now").upper()

    bg = _style(style_seed + 2, _BG_PALETTES)
    typo = _style(style_seed + 2, _TYPO_MOODS)

    body_clean = _clean_body_for_overlay(body or "", max_chars=90)

    # Pick a high-contrast CTA button colour based on background
    dark_bgs = {"forest", "navy", "charcoal", "rust"}
    label = _bg_label(style_seed + 2)
    cta_colour = (
        "bright orange (#FF6B2B) filled rectangle" if label not in dark_bgs
        else "white filled rectangle"
    )

    return (
        f"Conversion-focused ad. {bg}. "
        f"Photorealistic {product} product photography in the centre of the frame. "
        f'{typo}. Large bold headline at the top: "{headline_upper}". '
        + (f'Body copy below the headline: "{body_clean}". ' if body_clean else "")
        + f'At the very bottom of the image: a prominent {cta_colour} CTA button '
        f'with bold text "{cta_upper}" — this button must be clearly visible and '
        f'stand out from the rest of the image. '
        "Strong visual hierarchy — headline at top, product in middle, CTA button at bottom. "
        "Bold typography, high contrast, legible at small sizes. No badges or icons."
    )


def _prompt_premium(
    brief: dict, product: str, headline: str, style_seed: int
) -> str:
    """White/cream background, product as hero, single elegant label — clean premium catalog."""
    label = _truncate(headline or product, 50)

    # Clean, bright, airy backgrounds — white or near-white
    bg_options = [
        "pure white background, soft diffused studio lighting, clean product shadow beneath",
        "warm cream background (#F5EDD8), gentle natural window light from the side",
        "bright white background, single directional soft-box light, crisp minimal shadow",
        "off-white background with subtle warm tone, soft ambient studio light",
    ]
    bg = bg_options[style_seed % len(bg_options)]

    # Product treatment — clean, intentional, lots of space
    product_treatment = [
        "product centered with generous white space on all sides",
        "product in lower two-thirds, elegant label text above with breathing room",
        "product slightly off-center right, text anchored to left with white space",
        "product cross-section as hero, centered, small whole fruit beside it",
    ]
    treatment = product_treatment[style_seed % len(product_treatment)]

    # Typography: refined, light, small — a label not a headline
    typo_options = [
        "small refined serif label below the product, elegant letter-spacing",
        "clean light-weight sans-serif label, uppercase, generous tracking",
        "two lines: product name in medium serif above, short descriptor in small caps below",
        "single centered label in tall elegant serif, understated and luxury",
    ]
    typo = typo_options[style_seed % len(typo_options)]

    return (
        f"Premium product catalog ad. {bg}. "
        f"Photorealistic {product}, {treatment}. "
        f'{typo}. Label text: "{label}". '
        "Generous white space throughout. High-end specialty food brand aesthetic. "
        "Professional studio photography, pristine clean look. "
        "No CTA button, no body copy paragraph, no badges, no busy elements."
    )


def _prompt_scroll_stopping(
    brief: dict, product: str, headline: str, body: str, style_seed: int
) -> str:
    """Pattern-interrupt format: wild claim, absurd 1-star review, or ridiculous statement."""

    # Three structural sub-formats, rotated via style_seed
    fmt = style_seed % 3

    if fmt == 0:
        # ── 1-star review format ────────────────────────────────────────
        # White card with a single star, absurd complaint that's actually a compliment
        complaints = [
            "1 star. I ate the whole box before my family got home.",
            "1 star. I can't stop ordering this and my bank account hates it.",
            "1 star. Now I can't eat supermarket fruit anymore. Thanks a lot.",
            "1 star. My nutritionist told me to stop, I told her to try it first.",
            "1 star. I drove 40 minutes to get more after the first bite.",
            "1 star. My children look at me differently now. Worth it.",
        ]
        complaint = complaints[style_seed % len(complaints)]
        star_text = "★☆☆☆☆"

        bg_options = [
            "clean white background",
            "warm cream background (#F5EDD8)",
            "very pale yellow background",
        ]
        bg = bg_options[style_seed % len(bg_options)]

        return (
            f"Scroll-stopping ad. {bg}. Photorealistic {product} in the upper portion. "
            f"Large single gold star {star_text} prominently displayed — makes the viewer do a double-take. "
            f'Below the star: bold text "{complaint}". '
            "Clean card layout, high contrast black text on white/cream. "
            "The joke lands instantly — unexpected review format forces a re-read. "
            "No CTA button, no badges. The surprise IS the hook."
        )

    elif fmt == 1:
        # ── Wild/ridiculous claim format ────────────────────────────────
        # Bold oversized claim that's absurd but true / exaggerated
        claim = headline or f"WARNING: Extremely good {product}"
        short_claim = _truncate(claim, 60).upper()

        # High-contrast, slightly chaotic backgrounds
        chaos_bgs = [
            "bright acid yellow background (#FFE600), high energy",
            "electric lime green background (#CCFF00), bold and loud",
            "hot coral background (#FF4D4D), attention-grabbing red-orange",
            "vivid cyan background (#00E5FF), unexpected pop of colour",
        ]
        bg = chaos_bgs[style_seed % len(chaos_bgs)]

        fine_prints = [
            "*results may vary. They might be even better.",
            "*not responsible for repeat purchases.",
            "*warning: highly habit-forming.",
            "*side effects: happiness, re-ordering, telling everyone.",
        ]
        fine_print = fine_prints[style_seed % len(fine_prints)]

        return (
            f"Scroll-stopping ad. {bg}. Photorealistic {product}, bold and prominent. "
            f"Massive all-caps bold condensed typography: \"{short_claim}\" fills most of the frame. "
            f'Very small fine-print text at the bottom: "{fine_print}". '
            "Unexpectedly bold colour, oversized text creates a visual shock. "
            "Feels like a parody warning label but the product looks genuinely incredible. "
            "No traditional CTA button. The energy IS the call to action."
        )

    else:
        # ── Absurd stat / ridiculous fact format ────────────────────────
        # "97% of people who tried this" style wild stat with product hero
        stat = body or headline or "People who tried this never went back to store-bought"
        short_stat = _clean_body_for_overlay(stat, max_chars=70)

        stat_bgs = [
            "deep black background, single dramatic spotlight on product",
            "very dark navy background (#0A0E1A), moody and dramatic",
            "rich dark forest green (#0D1F14), bold and unexpected",
        ]
        bg = stat_bgs[style_seed % len(stat_bgs)]

        return (
            f"Scroll-stopping ad. {bg}. Photorealistic {product}, dramatically lit. "
            f'Oversized bold number or statement dominates the upper frame: "{short_stat}". '
            "Typography: massive, punchy, white on dark — impossible to ignore. "
            "Dark dramatic background makes the product and text glow. "
            "Unexpected contrast between the dramatic visual and the absurd/funny claim. "
            "No CTA button. No badges. Pure pattern interrupt."
        )


# ─────────────────────────────────────────────
# Dispatcher
# ─────────────────────────────────────────────

def _clean_product_desc(product_visibility: str) -> str:
    """Strip packaging/box references from product_visibility before using in image prompts.

    Prevents the image model from generating cardboard boxes, kraft packaging,
    or shipping containers in product shots.
    """
    text = product_visibility

    # Pass 1: strip "... [preposition] [article] [adjective] box/packaging/crate/..."
    # The pattern handles: "in its kraft delivery box", "next to their shipping box", etc.
    text = re.sub(
        r"[,\s]*(?:(?:in|inside|with|next to|alongside|beside|and)\s+)?"
        r"(?:(?:its|their|a|an|the)\s+)?"
        r"(?:(?:kraft|shipping|delivery|cardboard|corrugated|paper|gift|wooden)\s+)*"
        r"(?:box(?:es)?|packaging|package|crate|container|bags?)"
        r"(?:\s+(?:packaging|box))?",  # handle "packaging box"
        "",
        text,
        flags=re.IGNORECASE,
    )

    # Pass 2: strip trailing dangling prepositions/articles
    text = re.sub(
        r"[,\s]*\b(?:in|inside|with|next\s+to|next|alongside|beside|and|"
        r"its|their|a|an|the|to|of|from)\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    )

    # Tidy up
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r",\s*,", ",", text)
    text = text.strip().rstrip(",;: ")
    return text or product_visibility  # fallback to original if completely stripped


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
    headline_options  = brief.get("headline_options") or []
    variant_headlines = brief.get("variant_headlines") or {}
    body_options      = brief.get("body_options") or []
    hook              = brief.get("hook", "")

    shot_type        = _detect_shot_type(brief.get("composition_notes", ""))
    background_style = _background_style(brief.get("visual_direction", ""))
    style_tags       = _extract_style_tags(brief.get("visual_direction", ""))

    # Base style seed from production_output_id — different for every product/brief
    base_seed = production_output_id if production_output_id else 0

    # Build learnings suffix — injected into every variant's negative/positive prompt
    _avoid_suffix = ""
    _prefer_suffix = ""
    if product_learnings:
        avoids  = [l for l in product_learnings if l.get("type") == "avoid"]
        prefers = [l for l in product_learnings if l.get("type") == "prefer"]
        if avoids:
            _avoid_suffix = " Avoid: " + "; ".join(l["summary"] for l in avoids) + "."
        if prefers:
            _prefer_suffix = " Prefer: " + "; ".join(l["summary"] for l in prefers) + "."

    # Filter to only known variants
    selected = [v for v in variants if v in VARIANT_STRATEGIES]

    specs: list[dict[str, Any]] = []
    for i, variant in enumerate(selected):
        # Prefer a variant-specific headline written for this format;
        # fall back to rotating through general headline_options, then the hook.
        if variant_headlines.get(variant):
            headline_overlay = variant_headlines[variant]
        elif headline_options:
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
