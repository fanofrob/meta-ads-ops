"""
Scene-level clip generation for Creative Intelligence.

Converts storyboard scenes into individually generated MP4 clips via a
SceneClipProvider (default: ReplicateSceneClipProvider → runwayml/gen-4.5).

Public API
----------
build_clip_prompt(scene, video_type) → str
    Builds a motion-optimised text prompt for a single scene.

generate_scene_clip(scene, storyboard_id, conn, ...) → dict
    Generates and persists a clip for one scene.

generate_storyboard_clips(storyboard_id, conn, ...) → dict
    Generates clips for all scenes in a storyboard (parallel).
    Skips scenes that already have a completed clip.
"""
from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

# ─────────────────────────────────────────────
# Motion prompt building
# ─────────────────────────────────────────────

# Per-video-type prompt prefix
_TYPE_MOTION_PREFIX: dict[str, str] = {
    "ugc":               "Authentic handheld selfie-style footage, natural light, genuine emotion.",
    "farm_origin":       "Cinematic nature footage, golden hour light, slow reveal, sweeping wide shots.",
    "product_hero":      "Studio product shot, clean background, smooth camera orbit, dramatic lighting.",
    "comparison_reveal": "Split-screen reveal, dramatic contrast, smooth transition between two states.",
}

# Per-purpose motion hints
_PURPOSE_MOTION: dict[str, str] = {
    "hook":      "Fast eye-catching open, bold movement to grab attention immediately.",
    "problem":   "Relatable struggle, slightly chaotic, real-world handheld feel.",
    "solution":  "Smooth reveal motion, satisfying product emergence.",
    "demo":      "Close-up detail tracking shot, product in use, smooth slow motion.",
    "proof":     "Confident on-screen text animation, testimonial feel, steady shot.",
    "cta":       "Bold forward motion, energetic finish, camera push-in toward product.",
    "origin":    "Wide establishing shot, slow pan across landscape or facility.",
    "harvest":   "Macro detail shot of produce, gentle movement, natural textures.",
    "lifestyle": "Aspirational lifestyle context, warm light, slow dolly or pan.",
    "reveal":    "Before/after split, dramatic wipe transition, clean comparison.",
}

_DEFAULT_MOTION = "smooth cinematic motion, natural camera movement"


def build_clip_prompt(scene: dict, video_type: str = "ugc") -> str:
    """
    Build a motion-optimised text prompt for a single storyboard scene.

    Combines:
      - video-type motion prefix (sets aesthetic tone)
      - scene purpose hint (directs movement style)
      - visual description (subject matter)
      - product focus (if any)
      - lighting style (if any)

    The prompt intentionally emphasises MOTION over static description because
    text-to-video models (e.g. runwayml/gen-4.5) respond well to action words
    rather than purely visual descriptions.
    """
    type_prefix = _TYPE_MOTION_PREFIX.get(video_type, "Cinematic footage,")
    purpose = (scene.get("purpose") or "demo").lower().strip()
    purpose_hint = _PURPOSE_MOTION.get(purpose, _DEFAULT_MOTION)
    visual = (scene.get("visual_description") or "").strip()
    product_focus = (scene.get("product_focus") or "").strip()
    lighting = (scene.get("lighting_style") or "").strip()

    parts = [type_prefix, purpose_hint]
    if visual:
        parts.append(visual)
    if product_focus:
        parts.append(f"Product focus: {product_focus}.")
    if lighting:
        parts.append(f"Lighting: {lighting}.")

    # Append universal quality modifiers
    parts.append("Photorealistic, high quality, no text overlays, no watermarks.")

    return " ".join(p.rstrip(".") + "." for p in parts if p)


# ─────────────────────────────────────────────
# Single-scene generation
# ─────────────────────────────────────────────

def generate_scene_clip(
    scene: dict,
    storyboard_id: int,
    conn: sqlite3.Connection,
    *,
    provider_name: str | None = None,
    aspect_ratio: str = "9:16",
    output_dir: Path | None = None,
    video_type: str = "ugc",
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Generate a clip for one scene and persist to video_scene_clips.

    Returns:
    {
        "scene_id":   int,
        "scene_index": int,
        "status":     "ok" | "error" | "dry_run" | "skipped",
        "clip_path":  str | None,
        "clip_url":   str | None,
        "prompt":     str,
        "error":      str | None,
    }
    """
    from creative_intelligence.video.clip_providers import get_clip_provider
    from creative_intelligence import config

    scene_id = scene.get("id", 0)
    scene_index = scene.get("scene_index", 0)
    duration = float(scene.get("duration_seconds") or 5.0)
    prompt = build_clip_prompt(scene, video_type)

    if dry_run:
        return {
            "scene_id": scene_id, "scene_index": scene_index,
            "status": "dry_run", "clip_path": None, "clip_url": None,
            "prompt": prompt, "error": None,
        }

    clip_dir = output_dir or Path(config.CI_CLIP_OUTPUT_DIR) / str(storyboard_id)
    provider = get_clip_provider(provider_name)

    clip_path = provider.generate(
        prompt=prompt,
        duration=duration,
        aspect_ratio=aspect_ratio,
        scene_id=scene_id,
        output_dir=clip_dir,
    )

    is_mock = clip_path and clip_path.startswith("mock://")
    status = "ok" if clip_path else "error"
    error_msg = None if clip_path else "Provider returned no clip"

    if not dry_run:
        _upsert_scene_clip(
            conn=conn,
            storyboard_id=storyboard_id,
            scene_id=scene_id,
            scene_index=scene_index,
            provider=provider.name,
            model=getattr(provider, "_model", provider.name),
            prompt=prompt,
            clip_local_path=None if is_mock else clip_path,
            clip_url=None,
            duration_seconds=duration,
            aspect_ratio=aspect_ratio,
            status=status,
            error_message=error_msg,
        )

    return {
        "scene_id": scene_id,
        "scene_index": scene_index,
        "status": status,
        "clip_path": clip_path,
        "clip_url": None,
        "prompt": prompt,
        "error": error_msg,
    }


# ─────────────────────────────────────────────
# Storyboard-level generation (parallel)
# ─────────────────────────────────────────────

def generate_storyboard_clips(
    storyboard_id: int,
    conn: sqlite3.Connection,
    *,
    provider_name: str | None = None,
    aspect_ratio: str = "9:16",
    output_dir: Path | None = None,
    max_workers: int = 3,
    skip_existing: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Generate clips for all scenes in a storyboard.

    If skip_existing=True (default), scenes that already have a completed
    clip in video_scene_clips are skipped (idempotent).

    Returns:
    {
        "storyboard_id": int,
        "total_scenes":  int,
        "generated":     int,
        "skipped":       int,
        "failed":        int,
        "results":       list[dict],   # one per scene
        "status":        "ok" | "partial" | "all_failed" | "no_scenes",
    }
    """
    # Fetch scenes
    scene_rows = conn.execute(
        "SELECT * FROM video_scenes WHERE storyboard_id = ? ORDER BY scene_index",
        (storyboard_id,),
    ).fetchall()
    scenes = [dict(r) for r in scene_rows]

    if not scenes:
        return {
            "storyboard_id": storyboard_id, "total_scenes": 0,
            "generated": 0, "skipped": 0, "failed": 0,
            "results": [], "status": "no_scenes",
        }

    # Determine which scenes already have a completed clip
    existing_scene_ids: set[int] = set()
    if skip_existing:
        # Only skip scenes with a real clip file — mock clips (no path) are regenerated
        rows = conn.execute(
            """SELECT scene_id FROM video_scene_clips
               WHERE storyboard_id = ? AND status = 'ok'
                 AND (clip_local_path IS NOT NULL OR clip_url IS NOT NULL)""",
            (storyboard_id,),
        ).fetchall()
        existing_scene_ids = {r["scene_id"] for r in rows}

    # Fetch storyboard metadata for video_type
    sb_row = conn.execute(
        "SELECT video_type FROM video_storyboards WHERE id = ?", (storyboard_id,)
    ).fetchone()
    video_type = (sb_row["video_type"] if sb_row else None) or "ugc"

    results: list[dict] = []
    skipped = 0

    scenes_to_generate = []
    for scene in scenes:
        if scene["id"] in existing_scene_ids:
            results.append({
                "scene_id": scene["id"], "scene_index": scene["scene_index"],
                "status": "skipped", "clip_path": None, "clip_url": None,
                "prompt": None, "error": None,
            })
            skipped += 1
        else:
            scenes_to_generate.append(scene)

    # Parallel generation — only provider calls in threads; DB writes happen
    # serially in the main thread afterward (SQLite is not thread-safe by default).
    from creative_intelligence.video.clip_providers import get_clip_provider
    from creative_intelligence import config

    clip_dir = output_dir or Path(config.CI_CLIP_OUTPUT_DIR) / str(storyboard_id)
    provider = get_clip_provider(provider_name)

    def _worker(scene: dict) -> dict:
        """Call provider only — no DB access."""
        scene_id    = scene.get("id", 0)
        scene_index = scene.get("scene_index", 0)
        duration    = float(scene.get("duration_seconds") or 5.0)
        prompt      = build_clip_prompt(scene, video_type)

        if dry_run:
            return {
                "scene_id": scene_id, "scene_index": scene_index,
                "status": "dry_run", "clip_path": None, "clip_url": None,
                "prompt": prompt, "error": None,
                "_scene": scene, "_duration": duration,
            }

        clip_path = provider.generate(
            prompt=prompt,
            duration=duration,
            aspect_ratio=aspect_ratio,
            scene_id=scene_id,
            output_dir=clip_dir,
        )
        status    = "ok" if clip_path else "error"
        error_msg = None if clip_path else "Provider returned no clip"
        return {
            "scene_id": scene_id, "scene_index": scene_index,
            "status": status, "clip_path": clip_path, "clip_url": None,
            "prompt": prompt, "error": error_msg,
            "_scene": scene, "_duration": duration,
        }

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_worker, s): s for s in scenes_to_generate}
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as exc:
                scene = futures[fut]
                results.append({
                    "scene_id": scene["id"], "scene_index": scene["scene_index"],
                    "status": "error", "clip_path": None, "clip_url": None,
                    "prompt": None, "error": str(exc),
                    "_scene": scene, "_duration": 5.0,
                })

    # Persist all results to DB serially (avoids SQLite cross-thread errors)
    if not dry_run:
        for r in results:
            if r.get("status") in ("ok", "error") and r.get("_scene"):
                scene   = r["_scene"]
                is_mock = r["clip_path"] and r["clip_path"].startswith("mock://")
                _upsert_scene_clip(
                    conn=conn,
                    storyboard_id=storyboard_id,
                    scene_id=r["scene_id"],
                    scene_index=r["scene_index"],
                    provider=provider.name,
                    model=getattr(provider, "_model", provider.name),
                    prompt=r["prompt"] or "",
                    clip_local_path=None if is_mock else r["clip_path"],
                    clip_url=None,
                    duration_seconds=r.get("_duration", 5.0),
                    aspect_ratio=aspect_ratio,
                    status=r["status"],
                    error_message=r["error"],
                )

    # Sort results by scene_index
    results.sort(key=lambda r: r.get("scene_index", 0))

    generated = sum(1 for r in results if r["status"] in ("ok", "dry_run"))
    failed = sum(1 for r in results if r["status"] == "error")

    if generated == 0 and failed > 0:
        status = "all_failed"
    elif failed > 0:
        status = "partial"
    else:
        status = "ok"

    return {
        "storyboard_id": storyboard_id,
        "total_scenes": len(scenes),
        "generated": generated,
        "skipped": skipped,
        "failed": failed,
        "results": results,
        "status": status,
    }


# ─────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────

def _upsert_scene_clip(
    conn: sqlite3.Connection,
    storyboard_id: int,
    scene_id: int,
    scene_index: int,
    provider: str,
    model: str,
    prompt: str,
    clip_local_path: str | None,
    clip_url: str | None,
    duration_seconds: float,
    aspect_ratio: str,
    status: str,
    error_message: str | None,
) -> int:
    """Insert or replace a video_scene_clips row. Returns inserted row id."""
    # Delete existing row for this scene (replace pattern — simpler than upsert)
    conn.execute(
        "DELETE FROM video_scene_clips WHERE storyboard_id = ? AND scene_id = ?",
        (storyboard_id, scene_id),
    )
    cur = conn.execute(
        """INSERT INTO video_scene_clips
           (storyboard_id, scene_id, scene_index, provider, model, prompt,
            clip_local_path, clip_url, duration_seconds, aspect_ratio,
            status, error_message)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            storyboard_id, scene_id, scene_index, provider, model, prompt,
            clip_local_path, clip_url, duration_seconds, aspect_ratio,
            status, error_message,
        ),
    )
    conn.commit()
    return cur.lastrowid


def get_scene_clips(storyboard_id: int, conn: sqlite3.Connection) -> list[dict]:
    """Return all video_scene_clips rows for a storyboard, ordered by scene_index."""
    rows = conn.execute(
        """SELECT * FROM video_scene_clips
           WHERE storyboard_id = ?
           ORDER BY scene_index""",
        (storyboard_id,),
    ).fetchall()
    return [dict(r) for r in rows]
