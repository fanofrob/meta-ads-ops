"""
Video type definitions for the creative intelligence video pipeline.

Each VideoTypeConfig defines the ideal scene structure, shot rules, pacing,
text overlay style, and CTA behavior for a specific ad format.

Supported types
---------------
ugc               — Creator-led, face-forward, authentic, caption-friendly
farm_origin       — Provenance story, cinematic, warm & premium
product_hero      — Macro beauty, product as the star, clean composition
comparison_reveal — Contrast hook, problem → solution structure

Public API
----------
VIDEO_TYPES           : frozenset of valid type identifiers
VIDEO_TYPE_LABELS     : human-readable labels
VIDEO_TYPE_CONFIGS    : dict[str, VideoTypeConfig]
get_config(name)      : returns VideoTypeConfig; falls back to ugc
"""
from __future__ import annotations

from dataclasses import dataclass, field


VIDEO_TYPES = frozenset({
    "ugc",
    "farm_origin",
    "product_hero",
    "comparison_reveal",
})

VIDEO_TYPE_LABELS: dict[str, str] = {
    "ugc":               "UGC",
    "farm_origin":       "Farm / Origin",
    "product_hero":      "Product Hero",
    "comparison_reveal": "Comparison / Reveal",
}


@dataclass
class VideoTypeConfig:
    """Opinionated template for a specific video ad format."""

    video_type:        str
    label:             str

    # Scene structure
    scene_count_min:   int = 4
    scene_count_max:   int = 6
    scene_purposes:    list[str] = field(default_factory=list)  # ordered sequence

    # Shot style
    default_camera:    str = "handheld"          # handheld | tripod | static
    default_framing:   str = "medium"            # macro | close-up | medium | wide
    default_movement:  str = "none"              # none | slow pan | zoom in | zoom out
    pacing:            str = "medium"            # fast | medium | slow_premium
    avg_scene_seconds: float = 5.0

    # Visual style
    opening_style:     str = "face_first"        # face_first | establishing | macro_reveal | contrast_hook
    lighting_tone:     str = "warm natural"
    color_grade:       str = "authentic"         # authentic | cinematic | clean | contrasty

    # Text overlay
    text_overlay_style: str = "caption"          # caption | title_card | minimal | bold_cta
    text_overlay_position: str = "bottom_third"  # bottom_third | center | top | none

    # CTA
    cta_style:         str = "direct_verbal"     # direct_verbal | brand_narrative | product_led | reveal_cta

    # Ken Burns hint per scene purpose
    ken_burns_map:     dict[str, str] = field(default_factory=dict)

    # System prompt addendum injected into storyboard generation
    system_addendum:   str = ""

    # Scene-structure guidance injected into user prompt
    scene_guidance:    str = ""


# ─────────────────────────────────────────────
# UGC
# Creator-forward, fast-paced, authentic feel
# ─────────────────────────────────────────────

_UGC_SYSTEM_ADDENDUM = """
VIDEO TYPE: UGC (User-Generated Content)
- Scene 1 MUST be the creator speaking directly to camera delivering the hook
- Pacing is fast — 3-5s per scene, quick cuts
- Handheld camera throughout — authentic, slightly imperfect is fine
- Text overlays use caption/subtitle style at bottom-third of frame
- Creator's face should be present in hook and CTA scenes
- Demo scenes show product in creator's hands, in real-use context
- CTA scene: creator speaks directly to camera with product visible
- Avoid static product photography or slow cinematic shots
"""

_UGC_SCENE_GUIDANCE = """
Use these scene purposes in order:
  1. hook         — Creator speaks to camera, delivers opening line word-for-word
  2. value_demo   — Creator shows product benefit (in-use or reaction)
  3. demo         — Close-up of product, creator's hands, real-use moment
  4. proof        — Text/overlay proof point or emotional reaction shot
  5. cta          — Creator closes with CTA, product held or visible

Scene durations: hook=3-4s, value_demo=4-5s, demo=4-5s, proof=3-4s, cta=3-4s
Camera: handheld throughout. Framing: close-up/medium mix.
"""

UGC_CONFIG = VideoTypeConfig(
    video_type="ugc",
    label="UGC",
    scene_count_min=4,
    scene_count_max=6,
    scene_purposes=["hook", "value_demo", "demo", "proof", "cta"],
    default_camera="handheld",
    default_framing="medium",
    default_movement="none",
    pacing="fast",
    avg_scene_seconds=4.0,
    opening_style="face_first",
    lighting_tone="warm natural daylight",
    color_grade="authentic",
    text_overlay_style="caption",
    text_overlay_position="bottom_third",
    cta_style="direct_verbal",
    ken_burns_map={
        "hook":       "zoom in",
        "value_demo": "none",
        "demo":       "zoom in",
        "proof":      "none",
        "cta":        "zoom out",
    },
    system_addendum=_UGC_SYSTEM_ADDENDUM,
    scene_guidance=_UGC_SCENE_GUIDANCE,
)


# ─────────────────────────────────────────────
# Farm / Origin
# Provenance story, cinematic, warm premium
# ─────────────────────────────────────────────

_FARM_SYSTEM_ADDENDUM = """
VIDEO TYPE: Farm / Origin Story
- Scene 1 MUST be a wide establishing shot of the farm, orchard, or growing environment
- Pacing is slow and premium — 5-8s per scene, smooth transitions
- Camera moves are deliberate: slow pans, smooth tripod, gentle zooms
- Emphasise provenance: where it grows, how it's harvested, hands-on quality care
- Warm cinematic tone throughout — golden hour or natural daylight
- Text overlays use elegant title-card style, centered or upper-third
- CTA is brand-narrative: origins lead naturally to quality, quality leads to purchase
- Avoid generic product photography; keep shots grounded in the farm/nature setting
"""

_FARM_SCENE_GUIDANCE = """
Use these scene purposes in order:
  1. establishing  — Wide shot: farm, orchard, or growing landscape (sets provenance)
  2. harvest       — Mid-shot: hands picking, cutting, or gathering the product
  3. quality       — Close-up: texture, colour, freshness detail of the product
  4. process       — Show care: sorting, packing, or craftsmanship moment
  5. product_hero  — Beauty shot: finished product in natural setting
  6. cta           — Brand closing: product + brief origin reference + CTA text

Scene durations: establishing=6-8s, harvest=5-7s, quality=4-6s, process=5-6s, product_hero=5s, cta=4-5s
Camera: tripod + slow pan/tilt. Framing: wide → medium → close-up arc.
"""

FARM_ORIGIN_CONFIG = VideoTypeConfig(
    video_type="farm_origin",
    label="Farm / Origin",
    scene_count_min=5,
    scene_count_max=7,
    scene_purposes=["establishing", "harvest", "quality", "process", "product_hero", "cta"],
    default_camera="tripod",
    default_framing="wide",
    default_movement="slow pan",
    pacing="slow_premium",
    avg_scene_seconds=6.0,
    opening_style="establishing_shot",
    lighting_tone="warm golden hour",
    color_grade="cinematic",
    text_overlay_style="title_card",
    text_overlay_position="center",
    cta_style="brand_narrative",
    ken_burns_map={
        "establishing":  "slow pan",
        "harvest":       "zoom in",
        "quality":       "zoom in",
        "process":       "slow pan",
        "product_hero":  "zoom out",
        "cta":           "none",
    },
    system_addendum=_FARM_SYSTEM_ADDENDUM,
    scene_guidance=_FARM_SCENE_GUIDANCE,
)


# ─────────────────────────────────────────────
# Product Hero
# Macro beauty, product is the star, minimal people
# ─────────────────────────────────────────────

_PRODUCT_HERO_SYSTEM_ADDENDUM = """
VIDEO TYPE: Product Hero
- Product is the undisputed star of every scene
- Scene 1 is a macro reveal — texture, surface, or cut-open detail
- Minimal or no human presence; hands may appear briefly to frame the product
- Slow, deliberate pacing — let the product breathe on screen
- Clean, premium composition: pure backgrounds, studio-quality light
- Text overlays are minimal — short, bold, product-specific claims only
- CTA is product-led: product name or benefit, direct action
- Every shot must make the product look beautiful and desirable
"""

_PRODUCT_HERO_SCENE_GUIDANCE = """
Use these scene purposes in order:
  1. macro_reveal  — Extreme close-up: texture, surface detail, colour of the product
  2. beauty_shot   — Product centred on clean background, perfect lighting
  3. detail        — Second macro angle: cut open, inside, or unique product feature
  4. benefit       — Show the benefit: e.g. juice, freshness, aroma (motion implied)
  5. cta           — Final product shot with CTA text overlay

Scene durations: macro_reveal=4-5s, beauty_shot=5-6s, detail=4-5s, benefit=4-5s, cta=3-4s
Camera: tripod/static. Framing: macro → close-up. Movement: minimal zoom-in only.
"""

PRODUCT_HERO_CONFIG = VideoTypeConfig(
    video_type="product_hero",
    label="Product Hero",
    scene_count_min=4,
    scene_count_max=5,
    scene_purposes=["macro_reveal", "beauty_shot", "detail", "benefit", "cta"],
    default_camera="tripod",
    default_framing="macro",
    default_movement="zoom in",
    pacing="slow_premium",
    avg_scene_seconds=5.0,
    opening_style="macro_reveal",
    lighting_tone="clean studio light",
    color_grade="clean",
    text_overlay_style="minimal",
    text_overlay_position="bottom_third",
    cta_style="product_led",
    ken_burns_map={
        "macro_reveal": "zoom in",
        "beauty_shot":  "zoom out",
        "detail":       "zoom in",
        "benefit":      "slow pan",
        "cta":          "none",
    },
    system_addendum=_PRODUCT_HERO_SYSTEM_ADDENDUM,
    scene_guidance=_PRODUCT_HERO_SCENE_GUIDANCE,
)


# ─────────────────────────────────────────────
# Comparison / Reveal
# Contrast hook, problem → solution structure
# ─────────────────────────────────────────────

_COMPARISON_SYSTEM_ADDENDUM = """
VIDEO TYPE: Comparison / Reveal
- Opens with a contrast hook: show the problem, the inferior alternative, or the common assumption
- Structure is problem → reveal → solution — every scene builds tension toward the payoff
- Text overlays are prominent: bold, short, high-contrast claims that drive the comparison
- Hook scene should feel slightly uncomfortable or surprising to stop the scroll
- Reveal moment (scene 3-4) is the payoff — make it visually striking
- CTA leverages the contrast: "stop settling for X — get Y"
- Pacing is medium-fast: hook is sharp, reveal has slight pause, CTA is direct
"""

_COMPARISON_SCENE_GUIDANCE = """
Use these scene purposes in order:
  1. hook          — Show the inferior alternative or the common-but-wrong choice (sharp, contrasting)
  2. contrast      — Amplify the gap: side-by-side framing or sequential before/after
  3. reveal        — Introduce the product as the clear superior answer
  4. proof         — One concrete benefit or sensory detail that seals the comparison
  5. cta           — Direct call-to-action leveraging the contrast ("don't settle for X")

Scene durations: hook=3-4s, contrast=4-5s, reveal=5-6s, proof=4s, cta=3-4s
Camera: handheld for hook/contrast (gritty), tripod/static for reveal/cta (confident).
Text overlays: prominent on all scenes, high contrast.
"""

COMPARISON_REVEAL_CONFIG = VideoTypeConfig(
    video_type="comparison_reveal",
    label="Comparison / Reveal",
    scene_count_min=4,
    scene_count_max=6,
    scene_purposes=["hook", "contrast", "reveal", "proof", "cta"],
    default_camera="handheld",
    default_framing="close-up",
    default_movement="none",
    pacing="medium",
    avg_scene_seconds=4.5,
    opening_style="contrast_hook",
    lighting_tone="natural, slight contrast boost",
    color_grade="contrasty",
    text_overlay_style="bold_cta",
    text_overlay_position="center",
    cta_style="reveal_cta",
    ken_burns_map={
        "hook":     "zoom in",
        "contrast": "none",
        "reveal":   "zoom out",
        "proof":    "zoom in",
        "cta":      "none",
    },
    system_addendum=_COMPARISON_SYSTEM_ADDENDUM,
    scene_guidance=_COMPARISON_SCENE_GUIDANCE,
)


# ─────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────

VIDEO_TYPE_CONFIGS: dict[str, VideoTypeConfig] = {
    "ugc":               UGC_CONFIG,
    "farm_origin":       FARM_ORIGIN_CONFIG,
    "product_hero":      PRODUCT_HERO_CONFIG,
    "comparison_reveal": COMPARISON_REVEAL_CONFIG,
}


def get_config(video_type: str | None) -> VideoTypeConfig:
    """Return the VideoTypeConfig for the given type, defaulting to UGC."""
    return VIDEO_TYPE_CONFIGS.get(video_type or "ugc", UGC_CONFIG)
