"""
The GHF candle-label variation grid.

BLOCK_0 / BASE_STYLE / NEGATIVE are LOCKED — they are pasted identically on
every run. The whole grid is calibrated against them as constants; if they
drift, the codes stop meaning what they meant last week.

Grid shape per SKU: 5 (count) x 3 (interior) x 3 (exterior) x 5 (hierarchy)
x 5 (palette) = 1125 permutations.
"""
from __future__ import annotations

from typing import Any

# ─────────────────────────────────────────────
# BLOCK 0 — reference PDF preamble (always included)
# ─────────────────────────────────────────────

PDF_FILENAME = "GHF_Candle_Labels_Selected_Reference.pdf"

# Image models (Replicate slugs).
#
# nano-banana-pro is the default for two reasons: it is the only model that
# currently runs on this Replicate account (SD 3.5 Turbo returns ModelError
# E002 for every request, including the ads pipeline's own 9:16 config), and
# being Gemini-based it can actually read the reference PDF that BLOCK_0
# describes — which diffusion models cannot.
DEFAULT_MODEL = "google/nano-banana-pro"

MODELS: list[dict[str, str]] = [
    {"slug": "google/nano-banana-pro",
     "label": "Google Nano Banana Pro ✦ (recommended — reads the PDF)"},
    {"slug": "black-forest-labs/flux-1.1-pro",       "label": "FLUX 1.1 Pro"},
    {"slug": "black-forest-labs/flux-dev",           "label": "FLUX Dev"},
    {"slug": "ideogram-ai/ideogram-v2",              "label": "Ideogram V2"},
    {"slug": "recraft-ai/recraft-v3",                "label": "Recraft V3"},
    {"slug": "stability-ai/stable-diffusion-3.5-large",
     "label": "SD 3.5 Large"},
    {"slug": "stability-ai/stable-diffusion-3.5-large-turbo",
     "label": "SD 3.5 Turbo (failing on this account)"},
]

BLOCK_0 = """**A reference PDF is attached. Read it before generating. This is the approved art direction — match it.**

The PDF is titled *Good Hill Farms — Approved Candle Label Direction (Selected SKUs)*. It contains four pages. Page 1 is an overview showing three approved label designs plus a list of their shared characteristics. Pages 2–4 show each design enlarged, one per page.

The three designs are: **Strawberry Guava** (scent concept "Pink Hour"), **Grapefruit + Mangosteen** ("The Queen's Grove"), and **Passion Fruit + Pineapple** ("Golden Drop").

Each design appears twice, side by side. **On the left** is the flat label artwork alone — a vertical 2:3 rectangle, 2" × 3" trim. **On the right** is the same label wrapped onto an amber glass candle jar mockup, which is why it looks smaller, warped at the edges, and surrounded by a copper-orange cylinder with a drop shadow.

**Critical:** the amber jar is packaging mockup context only. It is **not** part of the image you are generating. Do not render a jar, a candle, glass, wax, a wick, or a copper cylinder. Generate only the flat photographic artwork — the vertical 2:3 fruit photograph seen on the left of each pair.

**Also ignore all text in the PDF.** The labels carry a "GOOD HILL FARMS" wordmark at the top and a product name, "SCENTED CANDLE", and "8 oz · 226 g" across the bottom. That text is applied later in layout. Your output must contain **no text of any kind** — but it must reserve the clean, low-detail zones where that text will sit.

What to actually take from the PDF: the photographic treatment. The full-bleed crop with no border or margin. The way the fruit cluster is held in the upper-middle and cropped by the left and right edges. The seamless out-of-focus gradient background running pale gray at the top to warm shadow at the bottom. The soft diffused light. The absence of any prop, surface, hand, or scene. The restraint."""


# ─────────────────────────────────────────────
# BLOCK A — BASE STYLE (locked, verbatim)
# ─────────────────────────────────────────────

BASE_STYLE = """**Visual Direction**
Photorealistic editorial studio photograph of fresh fruit, styled for a premium scented-candle label. Quiet luxury-grocery aesthetic — the fruit is the entire subject and the only story. Soft, broad, diffused key light from the upper left at roughly 45 degrees, with gentle fill from the right so shadows stay open and creamy. Light falls off gradually toward the bottom of the frame. Colors are true-to-life, but the cut interiors are jewel-toned and saturated against a restrained, desaturated background. Everything reads fresh, dewy, just-cut — like opening a fresh delivery box. No stylization, no illustration, no heavy color grade.

**Background**
Seamless out-of-focus gradient sweep. No visible surface edge, no horizon line, no table, no cutting board, no linen, no wood grain, no scattered leaves, no props. The background exists only to isolate the fruit and carry text.

**Composition Notes**
Vertical 2:3 portrait crop (2" × 3" label trim — render at minimum 1200 × 1800 px). The fruit runs full-bleed with no border or margin, cropped slightly by the left and right edges so it reads abundant rather than floating. Keep the top ~15% low-contrast and uncluttered for a wordmark. Keep the bottom ~30% as clean gradient and shadow falloff — no critical fruit detail, no busy texture, nothing the eye needs. Camera at near eye level to a 30–45 degree angle, showing exterior ripeness and interior color saturation in the same frame.

**Product Visibility**
Every fruit named must be individually and unmistakably identifiable. Fruit occupies roughly 55–70% of the frame. At least one fruit is cut open with the cut face angled toward the camera. Ripeness must be visually obvious through color depth, skin tension, and texture. Cut surfaces read moist and freshly sliced — never dried, dulled, or oxidized.

**Technical**
Medium-format digital, 100mm macro, f/4, ISO 100. Shallow depth of field: front-facing cut surfaces tack sharp with visible micro-texture (pores, fibers, seed detail, faint condensation), back edges falling gently soft. Natural film-like grain. No plastic CGI sheen, no over-smoothed surfaces."""


# ─────────────────────────────────────────────
# BLOCK C — NEGATIVE (locked, verbatim, always last)
# ─────────────────────────────────────────────

NEGATIVE = """No text, letters, numbers, logos, labels, or watermarks. No hands, fingers, arms, or people. No candles, jars, glass, wax, wicks, flames, or copper cylinders. No bowls, plates, cutting boards, knives, napkins, cloth, baskets, or crates. No table or surface edges, no horizon line. No scattered debris, water splashes, or falling fruit. No harsh direct sunlight, no hard shadows, no HDR, no vignette, no oversaturation, no bloom. No cartoon, painterly, 3D-render, or AI-glossy look. No mismatched light sources. No busy background. Nothing in the bottom third of the frame that competes with a text overlay."""


# ─────────────────────────────────────────────
# B1 · COUNT & STATE (universal)
# ─────────────────────────────────────────────

COUNT_STATE: dict[str, dict[str, str]] = {
    "S1": {
        "name": "Minimal Pair",
        "desc": "Two pieces total: one hero fruit halved, cut face to camera, plus one whole fruit behind. Maximum negative space. The quietest option.",
    },
    "S2": {
        "name": "Classic Triangle",
        "desc": "Three pieces in a loose triangle: one halved hero front, one whole behind, one wedge or quarter at the side. Balanced, the safest bet.",
    },
    "S3": {
        "name": "Abundance",
        "desc": "Four to five pieces layered with overlap, some cropped by the frame edges. Reads generous and seasonal. Least room for text.",
    },
    "S4": {
        "name": "Deconstructed",
        "desc": "One halved hero plus separated segments, slices, or scooped pulp fanned beside it. Shows structure. Editorial.",
    },
    "S5": {
        "name": "Twin Halves",
        "desc": "Two halves of the hero fruit, one square to camera and one tilted three-quarters, with the secondary fruit whole behind. Graphic and symmetrical.",
    },
}


# ─────────────────────────────────────────────
# B4 · HIERARCHY (universal)
# ─────────────────────────────────────────────

HIERARCHY: dict[str, dict[str, str]] = {
    "H1": {
        "name": "Front-Left Hero",
        "desc": "Hero fruit forward and slightly left of center; secondary fruit set right and slightly back for depth.",
        "pdf": True,
    },
    "H2": {
        "name": "Centered Hero",
        "desc": "Hero dead center, secondary fruit split symmetrically behind on both sides. Formal, catalog-like.",
    },
    "H3": {
        "name": "Stacked Depth",
        "desc": "Hero low and forward, secondary raised and behind so the cluster climbs the frame. Strongest depth, most drama.",
    },
    "H4": {
        "name": "Side-by-Side Equals",
        "desc": "Both fruits on the same plane, equal weight, no hero. Use only when the SKU name reads as a true 50/50 blend.",
    },
    "H5": {
        "name": "Framed Entry",
        "desc": "Hero anchored center-low; secondary fruit or foliage enters from the top or side edge, partially cropped, framing the hero.",
    },
}


# ─────────────────────────────────────────────
# B5 · PALETTE & SWEEP (universal)
# ─────────────────────────────────────────────

PALETTE: dict[str, dict[str, str]] = {
    "P1": {
        "name": "Pale Gray → Warm Shadow",
        "desc": "Cool pale gray at top grading to a warm mid-gray shadow across the bottom third. The house look.",
        "pdf": True,
    },
    "P2": {
        "name": "High Key",
        "desc": "Near-white top, soft light gray base, minimal shadow. Bright and clean; text at the bottom will need to run dark.",
    },
    "P3": {
        "name": "Warm Sand",
        "desc": "Warm putty-beige top grading to soft terracotta shadow. Reads sun-warmed and orchard-adjacent.",
    },
    "P4": {
        "name": "Deep Moody",
        "desc": "Mid-gray top falling to a deep near-charcoal bottom. Fruit interiors glow against it. Best-case for white text.",
    },
    "P5": {
        "name": "Tonal Echo",
        "desc": "Background sweep desaturated to a whisper of the hero fruit's own color. Cohesive, but never let it compete with the fruit.",
    },
}


# ─────────────────────────────────────────────
# SKUs — B2 (interior) and B3 (exterior) are per-SKU
# ─────────────────────────────────────────────

SKUS: dict[str, dict[str, Any]] = {
    "SG": {
        "name": "Strawberry Guava",
        "scent": "Pink Hour",
        "hero": "strawberry guava (single-fruit SKU — the halved guava is the whole story)",
        "default": {"S": "S2", "I": "I1", "E": "E1", "H": "H1", "P": "P1"},
        "pdf_page": 2,
        "echo_color": "dusty rose",
        "interior": {
            "I1": {
                "name": "Granular Blush",
                "desc": "Cut face shows the dense granular flesh grading from coral at the rind to deep rose at the core, with a tight cluster of small pale seeds at center and a paler flesh ring just inside the skin. Surface faintly beaded with juice.",
                "pdf": True,
            },
            "I2": {
                "name": "Seed Core Macro",
                "desc": "Tighter read on the seed cavity — the gelatinous seed mass glistening, individual seeds distinct, surrounding flesh slightly translucent where the light passes through the cut edge.",
            },
            "I3": {
                "name": "Clean Slice",
                "desc": "Thin cross-section rather than a half, laid so light rakes across it. Emphasis on the radial pattern and the near-translucency of a thin cut. More graphic, less pulpy.",
            },
        },
        "exterior": {
            "E1": {
                "name": "Blush Skin",
                "desc": "Whole fruit shows blush-red skin fading to yellow-green, faint waxy sheen, small dried calyx at the crown.",
                "pdf": True,
            },
            "E2": {
                "name": "Full Ripe",
                "desc": "Deeper uniform crimson skin, higher gloss, skin under visible tension. Reads riper and sweeter.",
            },
            "E3": {
                "name": "Just-Picked",
                "desc": "A short stem and one or two clean green leaves still attached at the crown. Only greenery permitted in this SKU.",
            },
        },
    },
    "PP": {
        "name": "Passion Fruit + Pineapple",
        "scent": "Golden Drop",
        "hero": "passion fruit (named first). Pineapple is secondary.",
        "default": {"S": "S2", "I": "I1", "E": "E1", "H": "H5", "P": "P1"},
        "pdf_page": 4,
        "echo_color": "muted plum",
        "interior": {
            "I1": {
                "name": "Gold and Seed",
                "desc": "Passion fruit halved showing glossy gold-amber pulp with black seeds suspended in juice, framed by the thick cream inner wall; pineapple spear showing fibrous golden-yellow flesh with visible grain running lengthwise.",
                "pdf": True,
            },
            "I2": {
                "name": "Pulp Spill",
                "desc": "Passion fruit tilted so the pulp catches a specular highlight and reads wet and heavy, on the edge of spilling but contained. Pineapple cut face shows a bead of juice at the fiber ends.",
            },
            "I3": {
                "name": "Core and Grain",
                "desc": "Pineapple cut as a cross-section round, showing the pale fibrous core ring and radiating grain; passion fruit half beside it for the dark-to-gold contrast.",
            },
        },
        "exterior": {
            "E1": {
                "name": "Purple and Crown",
                "desc": "Passion fruit's deep purple shell, faintly wrinkled, taut not shriveled; pineapple rind retained on the spear showing the crosshatch of eyes, with a few spiky blue-green crown fronds entering from the top edge.",
                "pdf": True,
            },
            "E2": {
                "name": "Smooth Shell",
                "desc": "Passion fruit shell smooth and glossy with a subtle red-purple gradient; pineapple fully trimmed to bare golden flesh, no rind, no crown. Cleanest, most modern read.",
            },
            "E3": {
                "name": "Crown Forward",
                "desc": "Pineapple crown becomes a real compositional element — several fronds arcing into the upper third, adding blue-green against the purple and gold.",
            },
        },
    },
    "GM": {
        "name": "Grapefruit + Mangosteen",
        "scent": "The Queen's Grove",
        "hero": "grapefruit (named first). Mangosteen is secondary.",
        "default": {"S": "S2", "I": "I1", "E": "E1", "H": "H1", "P": "P1"},
        "pdf_page": 3,
        "echo_color": "warm blush",
        "interior": {
            "I1": {
                "name": "Ruby vs. Snow",
                "desc": "Grapefruit halved showing radiating segment walls and translucent ruby-pink vesicles with a bead of juice at the surface; mangosteen with its cap lifted to reveal snow-white segmented flesh nested in the purple rind. Maximum color contrast.",
                "pdf": True,
            },
            "I2": {
                "name": "Vesicle Macro",
                "desc": "Push closer on the grapefruit's cut face so individual juice vesicles read as distinct glassy beads; mangosteen flesh soft-focus behind, still legible as white segments.",
            },
            "I3": {
                "name": "Segments Freed",
                "desc": "One grapefruit supreme lifted clear of the half, plus a single mangosteen segment separated from its cluster. Shows how each fruit comes apart.",
            },
        },
        "exterior": {
            "E1": {
                "name": "Rind and Cap",
                "desc": "Grapefruit's dimpled yellow-pink peel visible at the cut edge, pith showing as a clean white band; whole mangosteen with deep aubergine rind and its green calyx crown intact.",
                "pdf": True,
            },
            "E2": {
                "name": "Matte Depth",
                "desc": "Mangosteen rind rendered matte and slightly dusty, almost velvet, against the grapefruit's waxy dimpled peel. Texture contrast is the point.",
            },
            "E3": {
                "name": "Cracked Rind",
                "desc": "Mangosteen rind broken open by hand rather than cut, leaving a rough torn edge and the purple stain the rind leaves. Rustic, more farm than studio. (No hand in frame.)",
            },
        },
    },
}


# ─────────────────────────────────────────────
# Conflict rules — surfaced as warnings, never auto-corrected.
# The operator asked for a recipe; they get that recipe plus a heads-up.
# ─────────────────────────────────────────────

def conflicts(sku: str, s: str, i: str, e: str, h: str, p: str) -> list[str]:
    """Return human-readable warnings for known awkward combinations."""
    out: list[str] = []

    if sku == "SG" and h == "H4":
        out.append(
            "H4 (Side-by-Side Equals) doesn't apply to Strawberry Guava — it's a "
            "single-fruit SKU with no second fruit to balance against. Did you mean "
            "S5 (Twin Halves)? That's the closest symmetrical read."
        )

    if sku == "PP" and e == "E2" and h == "H5":
        out.append(
            "E2 (Smooth Shell) removes the pineapple crown — which is the very thing "
            "H5 (Framed Entry) frames with. This leaves the top of the frame empty. "
            "E2 wants H1 or H3."
        )

    if sku == "GM" and p == "P2":
        out.append(
            "The white mangosteen flesh is the brightest thing in this SKU and can "
            "fall out against P2's near-white background. Consider sitting the "
            "mangosteen lower in the frame where the sweep has some tone, or use P1/P4."
        )

    if sku == "GM" and i == "I3" and s == "S4":
        out.append(
            "I3 (Segments Freed) and S4 (Deconstructed) say the same thing at "
            "different scales. Not a conflict — just know it doubles down."
        )

    return out
