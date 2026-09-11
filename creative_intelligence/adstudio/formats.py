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
    "A reference photo of the product is attached — it is the single source of "
    "truth for the product's appearance. Reproduce the product with PERFECT "
    "fidelity to that photo: the jar/packaging shape and color, and above all the "
    "LABEL — its exact illustration, layout, wordmark, and every line of text, "
    "rendered crisp and legible. Do NOT redraw, restyle, simplify, re-letter, or "
    "invent any part of the label, and do not change its wording. Keep the product "
    "prominent and in sharp focus so the label stays clearly readable — never "
    "shrink it so small, tilt it so far, or obscure it so much that the label "
    "becomes illegible or gets reinterpreted."
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

    # ── Diversified archetypes (added for Meta Entity-ID spread + surround-sound).
    # These are deliberately DIFFERENT composition families — flat-lay, human,
    # text-led, comparison, in-situ — so Meta fingerprints them as distinct
    # entities that can each reach fresh audiences, instead of collapsing into
    # one. See docs: minor tweaks = same entity; different archetype = new entity.
    "flat_lay": {
        "name": "Overhead Flat-Lay",
        "blurb": "Top-down styled arrangement — a distinct composition family.",
        "text": True,
        "staging": (
            "Shot from directly overhead: the product centered on a clean tonal "
            "surface, with scent/ingredient accents arranged in a balanced, styled "
            "flat-lay around it. Symmetrical, editorial, catalog-grade. Reserve clear "
            "space at the top for a headline."
        ),
    },
    "in_hand": {
        "name": "In-Hand / Human Touch",
        "blurb": "A real hand holding/lighting it — the 'human' entity, new-audience gold.",
        "text": True,
        "staging": (
            "A person's hand (only hand and wrist in frame, no face) holding or "
            "lighting the product in a warm, real home setting — natural skin, cozy "
            "morning or evening light. Human presence and scale. Reserve the upper "
            "area for a headline."
        ),
    },
    "room_scene": {
        "name": "Room In-Situ",
        "blurb": "The product living in a real room — a different scene = new entity.",
        "text": True,
        "staging": (
            "The product placed and lit in the real room it belongs in — a bathroom "
            "shelf, a bedside table, a coffee table — with soft ambient home light and "
            "lived-in, aspirational styling. Wider environmental shot, not a close-up."
        ),
    },
    "flame_macro": {
        "name": "Flame / Texture Macro",
        "blurb": "Extreme close-up of flame, wax, label texture. Pure sensory.",
        "text": False,
        "staging": (
            "An extreme macro close-up — the lit flame, molten wax pool, or the label "
            "texture — rich, tactile, glowing, shallow depth of field. Intimate and "
            "sensory. No text; a clean product image."
        ),
    },
    "color_pop": {
        "name": "Color Pop",
        "blurb": "Product on a bold saturated color. Graphic, thumb-stopping, distinct.",
        "text": True,
        "staging": (
            "The product centered on a single bold, saturated solid-color background "
            "(a vivid brand-adjacent hue), graphic and modern with a hard studio pop. "
            "A short punchy headline. Deliberately unlike any neutral-background shot."
        ),
    },
    "listicle": {
        "name": "Listicle (3 Reasons)",
        "blurb": "Numbered benefit list beside the product. Text-led concept.",
        "text": True,
        "staging": (
            "An editorial listicle layout: a bold headline (e.g. '3 reasons to light "
            "this') with three short numbered benefit lines cleanly stacked beside or "
            "below the product on a tonal panel. Structured and skimmable."
        ),
    },
    "pov": {
        "name": "POV Moment",
        "blurb": "First-person 'POV:' scene. Native, scroll-stopping concept.",
        "text": True,
        "staging": (
            "A first-person 'POV' moment: the product in a real, lived scene as if the "
            "viewer just walked in — glowing on a nightstand at night, or on a bathtub "
            "ledge — with a casual 'POV:' style headline. Native, immersive, phone-shot "
            "feel but still clean and on-brand."
        ),
    },
    "before_after": {
        "name": "Before / After",
        "blurb": "Split comparison — this vs the generic alternative. Visual argument.",
        "text": True,
        "staging": (
            "A split-frame comparison: one side shows the dull, generic, ordinary "
            "alternative; the other shows this product vivid, warm, and desirable. A "
            "short verdict headline. A clear visual before/after argument."
        ),
    },
    "qa": {
        "name": "Question / FAQ",
        "blurb": "Bold objection-question + answer. Handles one buying doubt.",
        "text": True,
        "staging": (
            "A bold question headline that names a real objection or curiosity, with a "
            "concise one-line answer beneath it, and the product beside the copy on a "
            "clean tonal panel. Reassuring and direct."
        ),
    },
    "gift": {
        "name": "Gift / Unboxing",
        "blurb": "Giftable/just-opened moment. Seasonal & occasion audiences.",
        "text": True,
        "staging": (
            "The product styled as a gift or a just-opened reveal — soft ribbon, tissue "
            "or kraft hints, a warm giftable moment, hands optional. Occasion energy "
            "(holiday, birthday, thank-you). Reserve space for a short headline."
        ),
    },
    "editorial": {
        "name": "Editorial / Magazine",
        "blurb": "High-end magazine spread. Aspirational, refined, minimal words.",
        "text": True,
        "staging": (
            "A high-end editorial magazine-spread look: generous dramatic negative "
            "space, refined fashion-shoot lighting, the product as a hero object of "
            "desire, one elegant line of copy. Aspirational and premium."
        ),
    },
    "ingredient_story": {
        "name": "Scent / Ingredient Story",
        "blurb": "The scent notes made visual as a still-life. Distinct storytelling.",
        "text": True,
        "staging": (
            "The scent made visual: the product surrounded by its actual scent "
            "ingredients arranged as a considered still-life 'scent map' — each "
            "ingredient identifiable and fresh — telling the fragrance story. One short "
            "headline naming the notes."
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
