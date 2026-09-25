"""
Product-type profiles — what makes a fruit ad different from a candle ad.

Every product in Ad Studio has a `product_type` (fruit | candle | other). The
type picks the hook brief Claude writes to, the archetype menu Claude invents
image concepts from, and the reference-lock / do-not-include blocks pasted into
every Nano Banana prompt. The Setup → Images → Compose → Gallery flow is the
same for every type; what flows through it is not.

Candle keeps the original (approved) blocks from formats.py verbatim.
"""
from __future__ import annotations

from typing import Any

from creative_intelligence.adstudio import formats as F
from creative_intelligence.adstudio.core import BOX, TECHNICAL as FOOD_TECHNICAL

DEFAULT_TYPE = "other"

# ─────────────────────────────────────────────────────────────────────────────
# Fruit
# ─────────────────────────────────────────────────────────────────────────────
FRUIT_FORMATS: dict[str, dict[str, str]] = {
    "cut_reveal": {"name": "Cross-Section Reveal",
                   "blurb": "The fruit halved, flesh face-on and filling the frame — the 'what's inside' moment."},
    "first_bite": {"name": "First Bite",
                   "blurb": "A bite just taken or a spoon mid-scoop; juice and texture, human and craveable."},
    "in_hand": {"name": "In-Hand Scale",
                "blurb": "A hand holding whole and cut fruit — shows real size and feels personal."},
    "unboxing": {"name": "Unboxing",
                 "blurb": "Our white box just opened on a doorstep or counter, hands lifting the first fruit."},
    "orchard": {"name": "Farm / Orchard Origin",
                "blurb": "On the tree, in a harvest crate, or in a grower's hands — provenance and freshness."},
    "juice_macro": {"name": "Juice / Texture Macro",
                    "blurb": "Extreme close-up of flesh fibres, seeds, a drip of juice. Pure appetite appeal."},
    "flat_lay": {"name": "Overhead Flat-Lay",
                 "blurb": "Top-down arrangement of whole, halved and sliced fruit on a styled surface."},
    "kitchen_prep": {"name": "Kitchen Prep",
                     "blurb": "Mid-cut on a wooden board, knife in frame, morning window light."},
    "plated": {"name": "Plated / Served",
               "blurb": "Served the way it's best eaten — a bowl, breakfast plate, dessert or smoothie."},
    "variety_lineup": {"name": "Variety Lineup",
                       "blurb": "Several varieties or ripeness stages side by side, each cut to show the flesh."},
    "color_pop": {"name": "Color Pop",
                  "blurb": "The fruit on a bold saturated backdrop that complements its flesh colour."},
    "outdoor_life": {"name": "Outdoor Lifestyle",
                     "blurb": "Picnic, pool, beach or patio — the fruit as part of a sunny moment."},
    "sharing": {"name": "Sharing / Family",
                "blurb": "Several hands reaching for cut pieces on a shared plate — social and warm."},
    "pov": {"name": "POV Moment",
            "blurb": "First-person view: holding a cut half over a table, about to eat. Native, scroll-stopping."},
    "vs_supermarket": {"name": "Ours vs Supermarket",
                       "blurb": "Split comparison: our peak-ripe fruit beside a pale, generic store version."},
    "gift": {"name": "Gift Moment",
             "blurb": "The box being handed over or opened as a gift — ribbon, card, a delighted reaction."},
    "seasonal_drop": {"name": "Seasonal Drop",
                      "blurb": "Dark, dramatic still life with 'in season now, not for long' energy."},
    "editorial": {"name": "Editorial Still Life",
                  "blurb": "Painterly, magazine-grade still life — dramatic light, generous negative space."},
    "minimal_studio": {"name": "Minimal Studio",
                       "blurb": "Whole + halved fruit on a seamless tonal sweep. Clean and premium."},
    "ingredient_use": {"name": "Recipe / Pairing",
                       "blurb": "The fruit paired with what it's great with — yogurt, cheese, cocktails, salads."},
}

FRUIT_REFERENCE_LOCK = (
    "Reference photos of the fruit are attached — they are the single source of "
    "truth for how THIS fruit looks, whole and cut: skin colour, texture and "
    "markings, shape and size, flesh colour, fibre and seed pattern. Reproduce "
    "the fruit with PERFECT fidelity to those photos. Never substitute a generic, "
    "look-alike or more common variety, and never 'improve' it into a different "
    "fruit."
)

FRUIT_NEGATIVE = (
    "Do NOT include: any fruit that is not this product (unless the scene "
    "explicitly pairs it), generic or look-alike varieties, bruised, rotten, "
    "shrivelled or unripe-looking fruit, supermarket PLU stickers, plastic "
    "clamshells or foam netting, extra or malformed fingers, distorted hands, "
    "cartoon/illustration/3D-render/AI-glossy look, oversaturation, harsh "
    "shadows, cluttered or messy props, watermarks, brand or platform logos, "
    "website URLs."
)

FRUIT_CONCEPT_RULES = (
    "FRUIT ACCURACY: the fruit's true appearance comes from the reference photos "
    "and the appearance notes — describe it concretely (skin, flesh colour, "
    "seeds) in each scene so the render never drifts to a generic version. Show "
    "the fruit CUT in most concepts; the flesh is what sells it. The fruit must "
    "look perfectly ripe and irresistibly fresh.\n"
    "PACKAGING: if a scene shows packaging, it is our box — " + BOX + "\n"
    "HANDS & PEOPLE are welcome (a hand in frame reaches new audiences) but "
    "must be anatomically perfect; keep faces out of frame or softly blurred."
)

FRUIT_HOOK_DESIRE = (
    "For fresh fruit the Desire is usually TASTE and DISCOVERY: a flavour they've "
    "never had, peak-ripe quality they can't get at the supermarket, farm-direct "
    "freshness, rarity and short seasons, gifting. Be sensory and specific about "
    "flavour and texture (e.g. 'tastes like pineapple-banana custard'), and "
    "honest about what the fruit actually is."
)

# ─────────────────────────────────────────────────────────────────────────────
# Candle (original Ad Studio behaviour)
# ─────────────────────────────────────────────────────────────────────────────
CANDLE_CONCEPT_RULES = """\
INGREDIENT ACCURACY: when a scene uses the product's scent/flavor ingredients as props, name the EXACT variety and describe its true real-world appearance so the render doesn't default to a generic look — e.g. a "Rainier cherry" is blush-yellow with a red cheek (NOT a solid-red cherry); a "pink guava" has green-yellow skin and rose-pink flesh. Never substitute a generic version of a named specialty ingredient.

THE NAME IS NOT AN INGREDIENT LIST: use ONLY the product's explicitly named scent/flavor notes as ingredient props. Do NOT invent ingredients from wordplay in the product's NAME. Critically, "Rosé" means rosé WINE — a blush-pink, fruity, lightly effervescent, celebratory character — it is NOT the rose flower. Treat such wordplay as a palette/mood cue (blush and wine tones, an effervescent celebratory feel), NEVER as a literal ingredient. Do not add roses, rose petals, or rose blossoms unless the named notes actually list rose (they may be used occasionally as a deliberate accent, but never as an automatic default or a stand-in for the real ingredients)."""

CANDLE_HOOK_DESIRE = (
    "For a candle/home product the Desire is usually ATMOSPHERE, MEMORY, or "
    "FEELING — not taste. Keep a quiet-luxury, warm, real tone. Be concrete and "
    "physical."
)

# ─────────────────────────────────────────────────────────────────────────────
# Other — generic product
# ─────────────────────────────────────────────────────────────────────────────
_CANDLE_ONLY = {"flame_macro", "ingredient_story"}

OTHER_REFERENCE_LOCK = (
    "A reference photo of the product is attached — it is the single source of "
    "truth for the product's appearance. Reproduce it with PERFECT fidelity: its "
    "shape, materials, colours, packaging and any printed label or text, crisp "
    "and legible. Do NOT redesign, restyle, simplify or re-letter any part of it. "
    "Keep the product prominent and in sharp focus."
)

OTHER_NEGATIVE = (
    "Do NOT include: any redesign of the product or its packaging, altered or "
    "misspelled printed text, extra units of the product, extra or malformed "
    "fingers, clutter or props beyond what is specified, price tags, barcodes, "
    "QR codes, watermarks, brand or platform logos, website URLs, "
    "cartoon/illustration/3D-render/AI-glossy look, harsh shadows, "
    "oversaturation, or a busy background."
)

OTHER_HOOK_DESIRE = (
    "Find the product's single strongest, most specific benefit and make the "
    "Desire concrete and physical — what life looks or feels like with it."
)


def _menu(formats: dict[str, dict[str, Any]], drop: set[str] = frozenset()) -> dict[str, dict]:
    return {k: {"name": v["name"], "blurb": v["blurb"]} for k, v in formats.items() if k not in drop}


PROFILES: dict[str, dict[str, Any]] = {
    "fruit": {
        "label": "Fruit",
        "notes_label": "Flavor notes",
        "hook_desire": FRUIT_HOOK_DESIRE,
        "concept_rules": FRUIT_CONCEPT_RULES,
        "reference_lock": FRUIT_REFERENCE_LOCK,
        "negative": FRUIT_NEGATIVE,
        "technical": FOOD_TECHNICAL,
        "formats": FRUIT_FORMATS,
    },
    "candle": {
        "label": "Candle",
        "notes_label": "Scent notes (the ONLY ingredients)",
        "hook_desire": CANDLE_HOOK_DESIRE,
        "concept_rules": CANDLE_CONCEPT_RULES,
        "reference_lock": F.REFERENCE_LOCK,
        "negative": F.NEGATIVE,
        "technical": F.TECHNICAL,
        "formats": _menu(F.FORMATS),
    },
    "other": {
        "label": "Other",
        "notes_label": "Key features",
        "hook_desire": OTHER_HOOK_DESIRE,
        "concept_rules": "",
        "reference_lock": OTHER_REFERENCE_LOCK,
        "negative": OTHER_NEGATIVE,
        "technical": F.TECHNICAL,
        "formats": _menu(F.FORMATS, _CANDLE_ONLY),
    },
}


def normalize_type(value: Any) -> str:
    v = (str(value or "")).strip().lower()
    return v if v in PROFILES else DEFAULT_TYPE


def profile(product: dict[str, Any] | None) -> dict[str, Any]:
    """The profile for a product row (unknown/missing type → 'other')."""
    return PROFILES[normalize_type((product or {}).get("product_type"))]
