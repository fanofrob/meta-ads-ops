"""
Video storyboard builder.

Converts a winning concept into a complete video structure:
  - 4-6 scenes (purpose / visual / text overlay / duration)
  - Shot list per scene (camera, framing, movement, lighting)
  - UGC-style script (opening line, talking points, demo actions, CTA)
  - Optional frame images via existing rendering providers

One LLM call generates the full storyboard JSON; frame generation is a
separate optional step that reuses creative_intelligence.rendering.providers.

Public API
----------
build_storyboard(
    concept, product_id, conn,
    *,
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
from creative_intelligence.product_knowledge.enricher import (
    build_prompt_context_block,
    _clean_product_name,
)

# ─────────────────────────────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a direct-response video creative director specialising
in short-form social video (TikTok / Reels / Shorts / Meta Feed).

Your storyboards are:
- Hook-first: scene 1 MUST stop the scroll within 2 seconds
- Benefit-dense: every scene earns its screen time
- Creator-ready: scripts can be read by a UGC creator with no editing
- Specific over generic: name the product, the benefit, the sensation

Return ONLY valid JSON — no prose, no markdown fences."""

_USER_PROMPT_TEMPLATE = """Create a complete video storyboard for this concept.

## Concept (winning hook)
{concept}

## Product Context
{product_context}

## Requirements
- 4 to 6 scenes
- Total duration 20–45 seconds
- Scene purposes must include "hook" (scene 1), at least one "demo" or "reveal", and "cta" (last scene)
- Each visual_description must be a specific, filmable shot — no vague references
- text_overlay is what appears ON SCREEN (not spoken) — keep under 8 words or empty string
- UGC script must be natural, first-person, conversational — avoid corporate tone
- Shot list must be practical for a solo creator with a phone

## Output JSON schema (return exactly this structure)
{{
  "scenes": [
    {{
      "scene_id": 1,
      "purpose": "hook",
      "visual_description": "string — filmable description of what the camera sees",
      "text_overlay": "string — on-screen text (max 8 words) or empty string",
      "duration_seconds": 3,
      "shot": {{
        "camera_type": "handheld" | "static" | "tripod",
        "framing": "macro" | "close-up" | "medium" | "wide",
        "movement": "none" | "slow pan" | "zoom in" | "zoom out" | "tilt down" | "tilt up",
        "product_focus": "string — how/where product appears in this shot",
        "lighting_style": "string — e.g. natural window light / warm overhead / backlit"
      }}
    }}
  ],
  "ugc_script": {{
    "opening_line": "string — exact hook line spoken to camera (word-for-word)",
    "talking_points": [
      "string — key message 1",
      "string — key message 2",
      "string — key message 3"
    ],
    "demo_actions": [
      "string — physical action to show on camera",
      "string — second demo beat"
    ],
    "closing_cta": "string — exact closing line spoken to camera"
  }},
  "total_duration_seconds": 30
}}

Generate the storyboard now for: {concept}"""


# ─────────────────────────────────────────────────────────────────────
# Frame generation (reuses existing rendering providers)
# ─────────────────────────────────────────────────────────────────────

def _generate_scene_frame(
    scene: dict,
    storyboard_id: int,
    conn: sqlite3.Connection,
    aspect_ratio: str = "9:16",
    model: str | None = None,
) -> int | None:
    """Generate one image for a scene using the existing rendering provider.

    Returns the render_assets.id of the new asset, or None on failure.
    """
    from creative_intelligence.rendering.providers import get_provider
    from creative_intelligence.rendering.asset_store import save_asset

    try:
        provider = get_provider(None)  # respects CI_IMAGE_PROVIDER env var
        negative = (
            "text overlays, watermarks, blurry, low quality, distorted, "
            "generic stock photo, fake-looking, oversaturated, pixelated"
        )
        gen_kwargs: dict[str, Any] = {}
        if model:
            gen_kwargs["model"] = model

        paths = provider.generate(
            prompt=scene["visual_description"],
            negative_prompt=negative,
            aspect_ratio=aspect_ratio,
            **gen_kwargs,
        )
        if not paths:
            return None

        # Re-use render_assets table; render_output_id = 0 marks video scene frames
        asset_id = save_asset(
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
        return asset_id
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────
# DB persistence
# ─────────────────────────────────────────────────────────────────────

def _save_storyboard(
    concept: str,
    product_id: str | None,
    scenes: list[dict],
    ugc_script: dict,
    total_duration: int,
    conn: sqlite3.Connection,
) -> int:
    """Insert video_storyboards row and return new id."""
    cursor = conn.execute(
        """INSERT INTO video_storyboards
           (product_id, concept_text, scenes_json, ugc_script_json, total_duration_seconds)
           VALUES (?, ?, ?, ?, ?)""",
        (
            product_id,
            concept,
            json.dumps(scenes),
            json.dumps(ugc_script),
            total_duration,
        ),
    )
    return cursor.lastrowid


def _save_scenes(
    storyboard_id: int,
    scenes: list[dict],
    conn: sqlite3.Connection,
) -> None:
    """Insert video_scenes rows."""
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
                shot.get("camera_type", ""),
                shot.get("framing", ""),
                shot.get("movement", "none"),
                shot.get("product_focus", ""),
                shot.get("lighting_style", ""),
                scene.get("render_asset_id"),  # populated after frame gen
            ),
        )


def _update_scene_asset(
    storyboard_id: int,
    scene_id: int,
    asset_id: int,
    conn: sqlite3.Connection,
) -> None:
    conn.execute(
        """UPDATE video_scenes
           SET render_asset_id = ?
           WHERE storyboard_id = ? AND scene_index = ?""",
        (asset_id, storyboard_id, scene_id),
    )


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────

def build_storyboard(
    concept: str,
    product_id: str | None = None,
    conn: sqlite3.Connection | None = None,
    *,
    aspect_ratio: str = "9:16",
    generate_frames: bool = False,
    model: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Convert a winning concept into a complete video storyboard.

    Parameters
    ----------
    concept:         The hook / concept text (from copilot or production).
    product_id:      Optional product for context injection.
    conn:            Open SQLite connection. If None, a new one is opened.
    aspect_ratio:    Frame aspect ratio for image generation (default 9:16).
    generate_frames: If True, generate one image per scene via the existing
                     rendering provider. Requires CI_IMAGE_GENERATION_ENABLED=1.
    model:           Optional Replicate model slug override.
    dry_run:         If True, skip all DB writes (LLM still runs).

    Returns
    -------
    {
        "storyboard_id": int | None,
        "concept":        str,
        "scenes":         list[SceneDict],
        "ugc_script":     UGCScriptDict,
        "total_duration_seconds": int,
        "dry_run":        bool,
    }
    """
    from creative_intelligence.db import get_connection

    _conn = conn or get_connection()

    # Build product context block
    product_ctx = ""
    if product_id:
        product_ctx = build_prompt_context_block(product_id, _conn)
    if not product_ctx:
        product_ctx = "No product context provided."

    # Call LLM
    llm = get_llm_client(dry_run=dry_run)
    user_prompt = _USER_PROMPT_TEMPLATE.format(
        concept=concept,
        product_context=product_ctx,
    )

    raw = llm.complete_json(_SYSTEM_PROMPT, user_prompt, temperature=0.7)

    # Unwrap if model wrapped in outer key
    if isinstance(raw, dict) and "storyboard" in raw:
        raw = raw["storyboard"]

    scenes: list[dict] = raw.get("scenes", [])
    ugc_script: dict   = raw.get("ugc_script", {})
    total_duration: int = int(raw.get("total_duration_seconds", 30))

    # Validate / normalise scenes
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

    storyboard_id: int | None = None

    if not dry_run:
        with _conn:
            storyboard_id = _save_storyboard(
                concept, product_id, scenes, ugc_script, total_duration, _conn
            )
            _save_scenes(storyboard_id, scenes, _conn)

        # Generate frames after DB rows exist
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
        "storyboard_id":          storyboard_id,
        "concept":                concept,
        "scenes":                 scenes,
        "ugc_script":             ugc_script,
        "total_duration_seconds": total_duration,
        "dry_run":                dry_run,
    }
