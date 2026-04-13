"""
Video storyboard builder.

Pipeline position:
  concept → production package → video storyboard → scene frames

Upstream inputs (in priority order):
  1. Script Package  (output_type='script_package')
     — has beat_structure, hook, CTA, production_notes. Best source for
       scene mapping: each beat becomes a scene.
  2. UGC Brief       (output_type='ugc_brief')
     — has opening_line, talking_points, demo_beats, creator_persona.
       Best source for creator-style content.
  3. Raw concept     (fallback when no package exists)
     — generates everything from scratch.

The source used is stored in video_storyboards.source_output_type and
video_storyboards.source_production_output_id for full lineage.

Public API
----------
build_storyboard(
    concept, product_id, conn,
    *,
    source_production_output_id=None,
    source_output_type=None,
    aspect_ratio="9:16",
    generate_frames=False,
    model=None,
    dry_run=False,
) -> dict
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from creative_intelligence.generation.llm_client import get_llm_client
from creative_intelligence.product_knowledge.enricher import build_prompt_context_block

# ─────────────────────────────────────────────────────────────────────
# System prompt (shared for all upstream sources)
# ─────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a direct-response video creative director specialising
in short-form social video (TikTok / Reels / Shorts / Meta Feed).

Your storyboards are:
- Hook-first: scene 1 MUST stop the scroll within 2 seconds
- Benefit-dense: every scene earns its screen time
- Creator-ready: scripts can be read by a UGC creator with no editing
- Specific over generic: name the product, the benefit, the sensation

Return ONLY valid JSON — no prose, no markdown fences."""

# ─────────────────────────────────────────────────────────────────────
# Output JSON schema (shared)
# ─────────────────────────────────────────────────────────────────────

_OUTPUT_SCHEMA = """{
  "scenes": [
    {
      "scene_id": 1,
      "purpose": "hook",
      "visual_description": "string — filmable description of what the camera sees",
      "text_overlay": "string — on-screen text (max 8 words) or empty string",
      "duration_seconds": 3,
      "shot": {
        "camera_type": "handheld" | "static" | "tripod",
        "framing": "macro" | "close-up" | "medium" | "wide",
        "movement": "none" | "slow pan" | "zoom in" | "zoom out" | "tilt down" | "tilt up",
        "product_focus": "string — how/where product appears in this shot",
        "lighting_style": "string — e.g. natural window light / warm overhead / backlit"
      }
    }
  ],
  "ugc_script": {
    "opening_line": "string — exact hook line spoken to camera (word-for-word)",
    "talking_points": ["string", "string", "string"],
    "demo_actions": ["string — physical action to show on camera"],
    "closing_cta": "string — exact closing line spoken to camera"
  },
  "total_duration_seconds": 30
}"""

# ─────────────────────────────────────────────────────────────────────
# User prompt builders — one per upstream source type
# ─────────────────────────────────────────────────────────────────────

def _prompt_from_script_package(pkg: dict, product_ctx: str) -> str:
    """Build a storyboard prompt from a Script Package.

    The Script Package already has beat_structure — map beats → scenes
    directly. Use hook, body, CTA, and production_notes verbatim.
    """
    beats = pkg.get("beat_structure") or []
    beats_fmt = "\n".join(
        f"  Beat {i+1}: [{b.get('beat','')}] {b.get('seconds','')} — "
        f'"{b.get("line","")}" | Direction: {b.get("direction","")}'
        for i, b in enumerate(beats)
    ) or "  (no beats defined)"

    alt_hooks = pkg.get("alternate_hooks") or []
    alt_ctas   = pkg.get("alternate_ctas")  or []

    return f"""Build a scene-by-scene video storyboard from this Script Package.

## Script Package (already approved)
Hook: {pkg.get('hook', '')}
Body: {pkg.get('body', '')}
CTA: {pkg.get('cta', '')}
Duration: {pkg.get('duration', '')}
Production notes: {pkg.get('production_notes', '')}

## Beat Structure (map each beat to one scene)
{beats_fmt}

## Alternate Hooks (use as scene text_overlay options)
{chr(10).join(f'  - {h}' for h in alt_hooks) or '  (none)'}

## Alternate CTAs
{chr(10).join(f'  - {c}' for c in alt_ctas) or '  (none)'}

## Product Context
{product_ctx}

## Instructions
- Create one scene per beat in the beat_structure above (minimum 4 scenes)
- The opening_line in ugc_script MUST be the Hook verbatim
- The closing_cta in ugc_script MUST be the CTA verbatim
- talking_points come from the Body content
- Derive demo_actions from the "direction" notes in each beat
- visual_description for each scene must be a specific, filmable shot
  consistent with the production_notes above
- Total duration must match the Script Package duration
- Shot list must be practical for a solo creator with a phone

## Output JSON schema
{_OUTPUT_SCHEMA}

Generate the storyboard now."""


def _prompt_from_ugc_brief(brief: dict, product_ctx: str) -> str:
    """Build a storyboard prompt from a UGC Creator Brief.

    The brief has opening_line, talking_points, demo_beats, creator_persona.
    Map these to scenes and UGC script — no beat_structure so scenes are
    inferred from the talking/demo flow.
    """
    talking  = brief.get("talking_points") or []
    demos    = brief.get("demo_beats")     or []
    no_go    = brief.get("no_go_notes")    or []

    return f"""Build a scene-by-scene video storyboard from this UGC Creator Brief.

## UGC Creator Brief (already approved)
Creator persona: {brief.get('creator_persona', '')}
Opening line: {brief.get('opening_line', '')}
Emotional tone: {brief.get('emotional_tone', '')}
Closing CTA: {brief.get('cta', '')}

## Talking Points (one scene per point)
{chr(10).join(f'  {i+1}. {p}' for i, p in enumerate(talking)) or '  (none)'}

## Demo Beats (physical actions to show on camera)
{chr(10).join(f'  - {d}' for d in demos) or '  (none)'}

## Scene Suggestions
{chr(10).join(f'  - {s}' for s in (brief.get("scene_suggestions") or [])) or '  (none)'}

## Do NOT include
{chr(10).join(f'  - {n}' for n in no_go) or '  (none)'}

## Product Context
{product_ctx}

## Instructions
- Scene 1 = hook: creator speaking opening_line directly to camera
- Middle scenes = one scene per talking point, interspersed with demo beats
- Last scene = CTA: creator delivering closing line with product visible
- The opening_line in ugc_script MUST match the brief's opening_line exactly
- The closing_cta in ugc_script MUST match the brief's cta exactly
- talking_points in ugc_script come from the Talking Points above
- demo_actions come from the Demo Beats above
- Follow the "Do NOT include" rules strictly
- Shot list should feel natural and handheld — phone creator aesthetic

## Output JSON schema
{_OUTPUT_SCHEMA}

Generate the storyboard now."""


def _prompt_from_concept(concept: str, product_ctx: str) -> str:
    """Fallback: build storyboard from raw concept text."""
    return f"""Create a complete video storyboard for this concept.

## Concept (winning hook)
{concept}

## Product Context
{product_ctx}

## Requirements
- 4 to 6 scenes
- Total duration 20-45 seconds
- Scene purposes must include "hook" (scene 1), at least one "demo" or "reveal", and "cta" (last)
- Each visual_description must be specific and filmable
- text_overlay = on-screen text (max 8 words) or empty string
- UGC script must be natural, first-person, conversational
- Shot list practical for a solo creator with a phone

## Output JSON schema
{_OUTPUT_SCHEMA}

Generate the storyboard now for: {concept}"""


# ─────────────────────────────────────────────────────────────────────
# Production package loader
# ─────────────────────────────────────────────────────────────────────

def _load_production_output(
    output_id: int,
    conn: sqlite3.Connection,
) -> dict[str, Any] | None:
    """Fetch a production_outputs row and parse output_json."""
    row = conn.execute(
        "SELECT * FROM production_outputs WHERE id = ?", (output_id,)
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    raw = d.get("output_json") or "{}"
    try:
        d["output_data"] = json.loads(raw)
    except json.JSONDecodeError:
        d["output_data"] = {}
    return d


def _find_best_package(
    concept: str,
    session_id: str | None,
    conn: sqlite3.Connection,
) -> dict[str, Any] | None:
    """Find the most recent Script Package or UGC Brief for this concept/session.

    Returns the full production_outputs row with output_data parsed, or None.
    Priority: script_package > ugc_brief (Script has more structure for scenes).
    """
    params: list[Any] = [concept, concept]
    session_clause = ""
    if session_id:
        params += [session_id, session_id]
        session_clause = "OR session_id IN (?, ?)"

    row = conn.execute(
        f"""SELECT * FROM production_outputs
            WHERE output_type IN ('script_package', 'ugc_brief')
              AND (concept_text = ? OR concept_text LIKE ? {session_clause})
            ORDER BY
              CASE output_type WHEN 'script_package' THEN 0 ELSE 1 END,
              id DESC
            LIMIT 1""",
        [concept, f"%{concept[:40]}%"] + ([session_id, session_id] if session_id else []),
    ).fetchone()

    if not row:
        return None
    d = dict(row)
    try:
        d["output_data"] = json.loads(d.get("output_json") or "{}")
    except json.JSONDecodeError:
        d["output_data"] = {}
    return d


# ─────────────────────────────────────────────────────────────────────
# DB persistence
# ─────────────────────────────────────────────────────────────────────

def _save_storyboard(
    concept: str,
    product_id: str | None,
    scenes: list[dict],
    ugc_script: dict,
    total_duration: int,
    source_production_output_id: int | None,
    source_output_type: str,
    conn: sqlite3.Connection,
) -> int:
    cursor = conn.execute(
        """INSERT INTO video_storyboards
           (product_id, concept_text, scenes_json, ugc_script_json,
            total_duration_seconds, source_production_output_id, source_output_type)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            product_id,
            concept,
            json.dumps(scenes),
            json.dumps(ugc_script),
            total_duration,
            source_production_output_id,
            source_output_type,
        ),
    )
    return cursor.lastrowid


def _save_scenes(storyboard_id: int, scenes: list[dict], conn: sqlite3.Connection) -> None:
    for scene in scenes:
        shot = scene.get("shot") or {}
        conn.execute(
            """INSERT INTO video_scenes
               (storyboard_id, scene_index, purpose, visual_description,
                text_overlay, duration_seconds,
                camera_type, framing, movement, product_focus, lighting_style,
                render_asset_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                storyboard_id,
                scene.get("scene_id", 0),
                scene.get("purpose", ""),
                scene.get("visual_description", ""),
                scene.get("text_overlay", ""),
                scene.get("duration_seconds", 5),
                shot.get("camera_type", "handheld"),
                shot.get("framing", "medium"),
                shot.get("movement", "none"),
                shot.get("product_focus", ""),
                shot.get("lighting_style", ""),
                scene.get("render_asset_id"),
            ),
        )


def _update_scene_asset(
    storyboard_id: int, scene_id: int, asset_id: int, conn: sqlite3.Connection
) -> None:
    conn.execute(
        "UPDATE video_scenes SET render_asset_id=? WHERE storyboard_id=? AND scene_index=?",
        (asset_id, storyboard_id, scene_id),
    )


# ─────────────────────────────────────────────────────────────────────
# Frame generation
# ─────────────────────────────────────────────────────────────────────

def _generate_scene_frame(
    scene: dict,
    storyboard_id: int,
    conn: sqlite3.Connection,
    aspect_ratio: str = "9:16",
    model: str | None = None,
) -> int | None:
    from creative_intelligence.rendering.providers import get_provider
    from creative_intelligence.rendering.asset_store import save_asset

    try:
        provider = get_provider(None)
        gen_kwargs: dict[str, Any] = {}
        if model:
            gen_kwargs["model"] = model
        paths = provider.generate(
            prompt=scene["visual_description"],
            negative_prompt=(
                "text overlays, watermarks, blurry, low quality, distorted, "
                "generic stock photo, fake-looking, oversaturated, pixelated"
            ),
            aspect_ratio=aspect_ratio,
            **gen_kwargs,
        )
        if not paths:
            return None
        return save_asset(
            render_output_id=0,
            variant_label=f"video_scene_{scene['scene_id']}",
            source=paths[0],
            conn=conn,
            metadata={
                "storyboard_id": storyboard_id,
                "scene_id":      scene["scene_id"],
                "purpose":       scene.get("purpose", ""),
                "aspect_ratio":  aspect_ratio,
            },
        )
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────

def build_storyboard(
    concept: str,
    product_id: str | None = None,
    conn: sqlite3.Connection | None = None,
    *,
    # Upstream package — explicit
    source_production_output_id: int | None = None,
    source_output_type: str | None = None,
    # Auto-resolve from session when no explicit package given
    session_id: str | None = None,
    # Image generation
    aspect_ratio: str = "9:16",
    generate_frames: bool = False,
    model: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Convert a concept (or production package) into a video storyboard.

    Source resolution (highest priority first):
    1. Explicit source_production_output_id  — use that package directly
    2. session_id / concept match            — auto-find best package
    3. Raw concept text fallback             — generate everything from scratch

    Returns
    -------
    {
        storyboard_id,           int | None
        concept,                 str
        scenes,                  list[dict]
        ugc_script,              dict
        total_duration_seconds,  int
        source_output_type,      str   ("script_package"|"ugc_brief"|"concept")
        source_production_output_id,  int | None
        dry_run,                 bool
    }
    """
    from creative_intelligence.db import get_connection
    _conn = conn or get_connection()

    # ── 1. Product context ────────────────────────────────────────────
    product_ctx = ""
    if product_id:
        product_ctx = build_prompt_context_block(product_id, _conn)
    if not product_ctx:
        product_ctx = "No product context provided."

    # ── 2. Resolve upstream source ───────────────────────────────────
    pkg: dict[str, Any] | None = None
    resolved_output_id: int | None = source_production_output_id
    resolved_output_type: str = source_output_type or "concept"

    if source_production_output_id:
        pkg = _load_production_output(source_production_output_id, _conn)
        if pkg:
            resolved_output_type = pkg["output_type"]

    if pkg is None:
        # Auto-resolve: look for Script Package or UGC Brief for this concept
        pkg = _find_best_package(concept, session_id, _conn)
        if pkg:
            resolved_output_id   = pkg["id"]
            resolved_output_type = pkg["output_type"]

    # ── 3. Build prompt based on source ──────────────────────────────
    if pkg and resolved_output_type == "script_package":
        user_prompt = _prompt_from_script_package(pkg["output_data"], product_ctx)
    elif pkg and resolved_output_type == "ugc_brief":
        user_prompt = _prompt_from_ugc_brief(pkg["output_data"], product_ctx)
    else:
        resolved_output_type = "concept"
        user_prompt = _prompt_from_concept(concept, product_ctx)

    # ── 4. LLM call ───────────────────────────────────────────────────
    llm = get_llm_client(dry_run=dry_run)
    # Storyboards are long JSON (5 scenes + UGC script). Pass higher max_tokens
    # to prevent truncation (default CI_LLM_MAX_TOKENS=2000 is too low).
    raw = llm.complete_json(_SYSTEM_PROMPT, user_prompt, temperature=0.7, max_tokens=4096)
    if isinstance(raw, dict) and "storyboard" in raw:
        raw = raw["storyboard"]

    scenes: list[dict] = raw.get("scenes", [])
    ugc_script: dict   = raw.get("ugc_script", {})
    total_duration: int = int(raw.get("total_duration_seconds", 30))

    # Normalise scenes
    for i, scene in enumerate(scenes):
        scene.setdefault("scene_id", i + 1)
        scene.setdefault("purpose", "demo")
        scene.setdefault("text_overlay", "")
        scene.setdefault("duration_seconds", 5)
        scene.setdefault("shot", {})
        scene["shot"].setdefault("camera_type", "handheld")
        scene["shot"].setdefault("framing", "medium")
        scene["shot"].setdefault("movement", "none")
        scene["shot"].setdefault("product_focus", "")
        scene["shot"].setdefault("lighting_style", "natural light")

    # ── 5. Persist ────────────────────────────────────────────────────
    storyboard_id: int | None = None
    if not dry_run:
        with _conn:
            storyboard_id = _save_storyboard(
                concept, product_id, scenes, ugc_script, total_duration,
                resolved_output_id, resolved_output_type, _conn,
            )
            _save_scenes(storyboard_id, scenes, _conn)

        if generate_frames and storyboard_id:
            for scene in scenes:
                asset_id = _generate_scene_frame(
                    scene, storyboard_id, _conn, aspect_ratio, model
                )
                if asset_id:
                    scene["render_asset_id"] = asset_id
                    with _conn:
                        _update_scene_asset(storyboard_id, scene["scene_id"], asset_id, _conn)

    if conn is None:
        _conn.close()

    return {
        "storyboard_id":                storyboard_id,
        "concept":                      concept,
        "scenes":                       scenes,
        "ugc_script":                   ugc_script,
        "total_duration_seconds":       total_duration,
        "source_output_type":           resolved_output_type,
        "source_production_output_id":  resolved_output_id,
        "dry_run":                      dry_run,
    }
