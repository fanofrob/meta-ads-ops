"""
Ad formats + prompt assembly for Ad Studio.

The 8 formats are the fruit render variants generalised to any product. Each
format assembles a Nano Banana Pro prompt from (product, hook/brief, format),
folding the locked craft blocks and the "do not include" list inline (the model
has no separate negative_prompt input).
"""
from __future__ import annotations

from typing import Any


# ─────────────────────────────────────────────
# Locked craft blocks — the "house look", pasted every run.
# ─────────────────────────────────────────────

LIGHT_MOOD = (
    "Soft, broad, diffused key light from the upper left at ~45 degrees, with "
    "gentle fill from the right so shadows stay open and creamy. Light falls off "
    "gradually toward the bottom. Colors true-to-life. No harsh direct sun, no "
    "hard shadows, no HDR, no bloom, no oversaturation."
)

TECHNICAL = (
    "Photorealistic editorial product photography. Medium-format look, 100mm, "
    "f/4, ISO 100. Shallow depth of field: the product and its label are "
    "tack-sharp with true micro-texture; background falls gently soft. Natural "
    "film-like grain. No plastic CGI sheen, no over-smoothing."
)

NEGATIVE = (
    "Do NOT include: any redesign of the product or its label, altered or "
    "misspelled label text, extra units of the product, hands/fingers/people, "
    "clutter or props beyond what is specified, shipping or packaging boxes, "
    "bags, price tags, barcodes, QR codes, watermarks, brand or platform logos "
    "(Meta/Facebook/Instagram/TikTok), website URLs, stock-photo watermarks, "
    "cartoon/illustration/painterly/3D-render/AI-glossy look, harsh shadows, "
    "oversaturation, busy background, or anything competing with the headline "
    "space."
)

REFERENCE_LOCK = (
    "A reference photo of the product is attached. Reproduce the product EXACTLY "
    "as shown — its packaging, shape, colors, label artwork, wordmark, and every "
    "word of text on the label. Do not redesign the label, change the product, "
    "or alter any text on it. The attached photo defines the product."
)

ASPECT_RATIOS = ["4:5", "1:1", "9:16", "2:3", "3:4", "16:9"]
DEFAULT_ASPECT = "4:5"


# ─────────────────────────────────────────────
# Formats. `text` = whether the headline is rendered INTO the image.
# `staging` = the format-specific scene/composition instruction.
# ─────────────────────────────────────────────

FORMATS: dict[str, dict[str, Any]] = {
    "premium_studio": {
        "name": "Premium Studio",
        "blurb": "Product on a seamless white/tonal sweep. Elegant, minimal or no text.",
        "text": False,
        "staging": (
            "Studio product shot: the product centered on a seamless, out-of-focus "
            "pale gradient sweep (no visible surface edge, no props). Product occupies "
            "~55-65% of the frame. Quiet-luxury, elevated, catalog-grade. Reserve the "
            "top third as clean negative space."
        ),
    },
    "lifestyle": {
        "name": "Lifestyle Scene",
        "blurb": "Product in a styled real setting with scent/ingredient accents.",
        "text": True,
        "staging": (
            "The product as hero in a warm, styled real setting (e.g. a limewashed "
            "shelf or windowsill in soft daylight), ~50-55% of the frame, slightly "
            "off-center. A few real ingredient/scent accents rest naturally beside it, "
            "softly out of focus. Reserve the upper third as clean negative space for "
            "the headline."
        ),
    },
    "bold_type": {
        "name": "Bold Type",
        "blurb": "Oversized headline is the hero, product secondary.",
        "text": True,
        "staging": (
            "Typography-led layout: an oversized, high-contrast headline dominates the "
            "frame; the product sits smaller and secondary, lower or to one side on a "
            "clean solid or tonal background. The text is the primary visual."
        ),
    },
    "benefit_stack": {
        "name": "Benefit Stack",
        "blurb": "Solid color block, 3 stacked benefit/trust lines + product.",
        "text": True,
        "staging": (
            "The product on a solid, brand-appropriate color block, with three short "
            "stacked benefit/trust lines in clean large type beside or below it. "
            "Organized, confident, editorial. No decorative icons or badges."
        ),
    },
    "social_proof": {
        "name": "Social Proof",
        "blurb": "Review-card look: stars + short customer quote over the product.",
        "text": True,
        "staging": (
            "The product with a tasteful review-card overlay: five stars and a short "
            "customer quote in clean type on a semi-transparent card, positioned so it "
            "does not cover the product or label."
        ),
    },
    "direct_response": {
        "name": "Direct Response",
        "blurb": "Headline + body + prominent CTA button. Conversion layout.",
        "text": True,
        "staging": (
            "A conversion layout: bold headline top, one line of body copy, and a "
            "prominent rounded CTA button. The product sits clearly beside or below "
            "the copy. Clean, direct, high-legibility."
        ),
    },
    "sensory": {
        "name": "Sensory / Atmosphere",
        "blurb": "Extreme mood: glow, texture, scent cues. Little/no text.",
        "text": False,
        "staging": (
            "An atmospheric, sensory close-up: dramatic warm glow, rich texture, scent "
            "cues (soft smoke, dappled light, ingredient hints), the product intimate "
            "and tactile. Moody and immersive. No text."
        ),
    },
    "seasonal_drop": {
        "name": "Seasonal Drop",
        "blurb": "Dark dramatic background, 'in season / limited' urgency, FOMO.",
        "text": True,
        "staging": (
            "Product-drop energy: the product dramatically lit against a dark, "
            "atmospheric background, with a bold short season/urgency headline. "
            "Exclusive, time-scarce, like a limited release."
        ),
    },
}


def format_list() -> list[dict[str, Any]]:
    return [
        {"key": k, "name": v["name"], "blurb": v["blurb"], "text": v["text"]}
        for k, v in FORMATS.items()
    ]


def build_ad_prompt(product: dict[str, Any], hook: dict[str, Any],
                    format_key: str, aspect_ratio: str = DEFAULT_ASPECT) -> str:
    """
    Assemble the Nano Banana Pro prompt for one (product, hook, format).

    `product` : {name, description, scent_notes, physical_desc}
    `hook`    : {headline, subhead, body, cta, hook_text}
    """
    if format_key not in FORMATS:
        raise ValueError(f"Unknown format {format_key!r}. Valid: {', '.join(FORMATS)}")
    fmt = FORMATS[format_key]

    name = product.get("name", "the product")
    scent = (product.get("scent_notes") or "").strip()
    subject = f"{name}" + (f" ({scent})" if scent else "")

    headline = (hook.get("headline") or hook.get("hook_text") or "").strip()
    subhead = (hook.get("subhead") or "").strip()
    cta = (hook.get("cta") or "").strip()

    parts: list[str] = []
    parts.append(
        f"Create a premium Meta/Instagram {aspect_ratio} ad image. {REFERENCE_LOCK}"
    )
    parts.append(f"SUBJECT\n{subject}, the single hero of the image, sharp and forward.")
    parts.append(f"SCENE / COMPOSITION\n{fmt['staging']}")
    parts.append(f"LIGHT & MOOD\n{LIGHT_MOOD}")

    if fmt["text"] and headline:
        text_block = (
            "ON-IMAGE TEXT\n"
            f'Headline (clean sans-serif, generous margin, high legibility, clear of '
            f'the product): "{headline}"'
        )
        if subhead:
            text_block += f'\nSubhead, smaller weight below: "{subhead}"'
        if format_key == "direct_response" and cta:
            text_block += f'\nCTA button: "{cta}"'
        text_block += (
            "\nRender all text crisply and correctly spelled. Do not distort or "
            "misspell any words."
        )
        parts.append(text_block)
    else:
        parts.append(
            "ON-IMAGE TEXT\nNo text of any kind on the image — this is a clean "
            "product image; copy is added later in layout."
        )

    parts.append(f"TECHNICAL\n{TECHNICAL}")
    parts.append(NEGATIVE)

    return "\n\n".join(parts)
