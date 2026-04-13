"""
TypedDict schemas for all production output types.

These are the canonical shapes for:
  - JSON storage in production_outputs.output_json / production_packages.variants_json
  - Markdown rendering via exporters.to_markdown()
  - Future template/rendering system consumption

No runtime dependencies — safe to import anywhere.
"""
from __future__ import annotations

from typing import Any


# ─────────────────────────────────────────────
# Output type constants
# ─────────────────────────────────────────────

OUTPUT_TYPES = frozenset({
    "static_brief",
    "video_brief",
    # Legacy — kept for backward-compat with existing DB rows; hidden in UI
    "ugc_brief",
    "script_package",
})


# ─────────────────────────────────────────────
# Static Ad Brief
# For designers / creative production teams
# ─────────────────────────────────────────────

class StaticAdBrief:
    """
    Schema for a static ad brief.

    Fields
    ------
    hook                : The approved concept/hook (verbatim input).
    headline_options    : 3 headline variants, each ≤8 words.
    body_options        : 3 primary text variants, each 1-2 punchy sentences.
    visual_direction    : 1-2 sentences describing the overall visual concept.
    composition_notes   : Layout, framing, and white-space guidance.
    product_visibility  : How/where the product appears in the frame.
    text_overlay        : On-image text guidance (font feel, placement, copy).
    cta                 : Button CTA text (e.g., "Shop Now").
    why_it_works        : 2-3 sentences on pattern alignment and expected effectiveness.
    """
    FIELDS = (
        "hook",
        "headline_options",
        "body_options",
        "visual_direction",
        "composition_notes",
        "product_visibility",
        "text_overlay",
        "cta",
        "why_it_works",
    )

    SCHEMA_HINT = """{
  "hook": "verbatim input concept",
  "headline_options": ["Headline A (≤8 words)", "Headline B", "Headline C"],
  "body_options": ["Primary text A (1-2 sentences)", "Primary text B", "Primary text C"],
  "visual_direction": "1-2 sentences on the overall visual concept",
  "composition_notes": "Layout, framing, and white-space guidance",
  "product_visibility": "How and where the product appears in frame",
  "text_overlay": "On-image text: font feel, placement, and copy guidance",
  "cta": "Button CTA text",
  "why_it_works": "2-3 sentences on pattern alignment and expected effectiveness"
}"""


# ─────────────────────────────────────────────
# UGC Creator Brief
# For UGC creators / talent partners
# ─────────────────────────────────────────────

class UGCCreatorBrief:
    """
    Schema for a UGC creator brief.

    Fields
    ------
    creator_persona     : Creator energy/archetype (e.g., "Curious home cook").
    opening_line        : Exact or near-exact hook line to open with on camera.
    talking_points      : 3-5 ordered key messages to hit during the video.
    demo_beats          : Physical actions / what to show or do on camera.
    emotional_tone      : One sentence on the feeling/energy to project.
    scene_suggestions   : 2-3 environment/setting options.
    cta                 : Closing call-to-action line (verbal).
    no_go_notes         : Brand guardrails — what NOT to say or do.
    """
    FIELDS = (
        "creator_persona",
        "opening_line",
        "talking_points",
        "demo_beats",
        "emotional_tone",
        "scene_suggestions",
        "cta",
        "no_go_notes",
    )

    SCHEMA_HINT = """{
  "creator_persona": "Creator energy/archetype",
  "opening_line": "Exact or near-exact hook line to open with on camera",
  "talking_points": ["Key message 1", "Key message 2", "Key message 3-5"],
  "demo_beats": ["Physical action 1", "What to show 2", "Beat 3"],
  "emotional_tone": "One sentence on the feeling/energy to project",
  "scene_suggestions": ["Environment option 1", "Setting option 2"],
  "cta": "Closing call-to-action line (verbal)",
  "no_go_notes": ["Do NOT say X", "Avoid Y", "Never Z"]
}"""


# ─────────────────────────────────────────────
# Script Package
# For video creators / editors
# ─────────────────────────────────────────────

class ScriptPackage:
    """
    Schema for a short-form video script package.

    Fields
    ------
    hook                : Opening 1-2 sentences spoken on camera.
    body                : 2-4 sentences of value delivery / product story.
    cta                 : Closing call-to-action line.
    duration            : Estimated total duration (e.g., "25s").
    beat_structure      : Beat-by-beat breakdown [{beat, seconds, line, direction}].
    alternate_hooks     : 2-3 hook variations to test.
    alternate_ctas      : 2-3 CTA variations.
    production_notes    : Lighting, pacing, B-roll, and editing suggestions.
    """
    FIELDS = (
        "hook",
        "body",
        "cta",
        "duration",
        "beat_structure",
        "alternate_hooks",
        "alternate_ctas",
        "production_notes",
    )

    SCHEMA_HINT = """{
  "hook": "Opening 1-2 sentences spoken on camera",
  "body": "2-4 sentences of value delivery",
  "cta": "Closing call-to-action line",
  "duration": "Estimated total (e.g. '25s')",
  "beat_structure": [
    {"beat": "Hook", "seconds": "0-5s", "line": "spoken line", "direction": "camera/action note"},
    {"beat": "Value", "seconds": "5-18s", "line": "spoken line", "direction": "note"},
    {"beat": "CTA", "seconds": "18-25s", "line": "spoken line", "direction": "note"}
  ],
  "alternate_hooks": ["Hook variant A", "Hook variant B"],
  "alternate_ctas": ["CTA variant A", "CTA variant B"],
  "production_notes": "Lighting, pacing, B-roll suggestions"
}"""



# ─────────────────────────────────────────────
# Video Brief  (replaces ugc_brief + script_package)
# Unified brief for all video ad formats
# ─────────────────────────────────────────────

class VideoBrief:
    """
    Unified video production brief — replaces separate UGC Brief and Script Package.

    Combines the creator-facing clarity of a UGC brief with the beat-structure
    precision of a script package. Works with all video_type formats:
    ugc | farm_origin | product_hero | comparison_reveal

    Fields
    ------
    hook                : Opening line / hook text — exact words to open with.
    talking_points      : 3-5 ordered key messages (hook → value → proof → CTA).
    demo_beats          : Physical on-camera actions to show or do.
    beat_structure      : Scene-by-scene breakdown [{beat, seconds, line, direction}].
    cta                 : Closing call-to-action (verbal).
    duration            : Estimated total duration (e.g. "25-30s").
    creator_persona     : Creator energy/archetype — who delivers this.
    production_notes    : Shot type, pacing, lighting, B-roll, editing hints.
    no_go_notes         : Brand guardrails — what NOT to say or do.
    alternate_hooks     : 2-3 hook variants to test.
    """
    FIELDS = (
        "hook",
        "talking_points",
        "demo_beats",
        "beat_structure",
        "cta",
        "duration",
        "creator_persona",
        "production_notes",
        "no_go_notes",
        "alternate_hooks",
    )

    SCHEMA_HINT = """{
  "hook": "Exact opening line — what the creator says or what appears on screen first",
  "talking_points": ["Key message 1 (value)", "Key message 2 (proof)", "Key message 3 (benefit)"],
  "demo_beats": ["Physical action 1 to show on camera", "What to demonstrate 2", "Action 3"],
  "beat_structure": [
    {"beat": "Hook",  "seconds": "0-4s",  "line": "spoken line", "direction": "camera/action note"},
    {"beat": "Value", "seconds": "4-18s", "line": "spoken line", "direction": "camera/action note"},
    {"beat": "Proof", "seconds": "18-24s","line": "spoken line", "direction": "camera/action note"},
    {"beat": "CTA",   "seconds": "24-30s","line": "spoken line", "direction": "camera/action note"}
  ],
  "cta": "Closing call-to-action line (verbal)",
  "duration": "Estimated total e.g. '25-30s'",
  "creator_persona": "Creator energy/archetype e.g. 'Authentic home cook who discovers premium quality'",
  "production_notes": "Shot type, pacing, lighting, whether B-roll is needed, key visual moments",
  "no_go_notes": ["Avoid X", "Do NOT say Y", "Never Z"],
  "alternate_hooks": ["Hook variant A — different angle", "Hook variant B"]
}"""


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def empty_static_brief() -> dict[str, Any]:
    return {
        "hook": "",
        "headline_options": [],
        "body_options": [],
        "visual_direction": "",
        "composition_notes": "",
        "product_visibility": "",
        "text_overlay": "",
        "cta": "",
        "why_it_works": "",
    }


def empty_ugc_brief() -> dict[str, Any]:
    return {
        "creator_persona": "",
        "opening_line": "",
        "talking_points": [],
        "demo_beats": [],
        "emotional_tone": "",
        "scene_suggestions": [],
        "cta": "",
        "no_go_notes": [],
    }


def empty_script_package() -> dict[str, Any]:
    return {
        "hook": "",
        "body": "",
        "cta": "",
        "duration": "",
        "beat_structure": [],
        "alternate_hooks": [],
        "alternate_ctas": [],
        "production_notes": "",
    }


def validate_output(output_type: str, data: dict[str, Any]) -> list[str]:
    """Return list of missing required fields for the given output type.

    Returns empty list if all required fields are present and non-empty.
    """
    if output_type == "static_brief":
        required = StaticAdBrief.FIELDS
    elif output_type == "ugc_brief":
        required = UGCCreatorBrief.FIELDS
    elif output_type == "script_package":
        required = ScriptPackage.FIELDS
    elif output_type == "video_brief":
        required = VideoBrief.FIELDS
    else:
        return [f"unknown output_type: {output_type!r}"]

    return [f for f in required if not data.get(f)]
