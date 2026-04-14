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

# Visual style anchor per video type — prepended to EVERY scene prompt for consistency
_TYPE_STYLE_ANCHOR: dict[str, str] = {
    "ugc": (
        "Authentic UGC-style video ad. Real human hands and face, photorealistic skin, "
        "natural proportions. Warm natural window light. Handheld, slightly unsteady. "
        "Genuine unpolished feel. Shot on iPhone. NOT CGI, NOT animated, NOT illustrated."
    ),
    "farm_origin": (
        "Cinematic farm-origin commercial. Lush tropical orchard setting. "
        "Rich saturated greens, golden hour sunlight through canopy. "
        "Shallow depth of field. 4K cinematography. Warm earthy tones. "
        "Photorealistic fruit and environment. NOT CGI, NOT illustrated."
    ),
    "product_hero": (
        "Premium product hero commercial. Clean white or neutral studio background. "
        "Dramatic soft-box rim lighting. Ultra-sharp photorealistic product detail. "
        "Smooth slow camera movement. Professional commercial cinematography. "
        "NOT CGI, NOT illustrated, NOT cartoon."
    ),
    "comparison_reveal": (
        "Dramatic split-screen commercial ad. High contrast between two states. "
        "Clean graphic aesthetic. Professional broadcast quality. Photorealistic."
    ),
}

# Camera motion per scene camera_type + movement combination
_CAMERA_MOTION: dict[str, str] = {
    "tripod_none":         "locked-off static shot, perfectly steady",
    "tripod_pan":          "slow smooth tripod pan left to right",
    "tripod_tilt":         "slow smooth tripod tilt down",
    "tripod_zoom":         "slow smooth optical zoom in",
    "handheld_none":       "subtle handheld breathing movement",
    "handheld_tracking":   "gentle handheld follow/tracking shot",
    "drone_none":          "steady aerial drone hover",
    "drone_pan":           "slow aerial drone pan",
    "drone_push":          "slow aerial drone push forward",
    "gimbal_dolly":        "smooth gimbal dolly push forward",
    "gimbal_orbit":        "smooth gimbal orbit around subject",
}

# Per-purpose subject and motion guidance
_PURPOSE_GUIDANCE: dict[str, str] = {
    "establishing": "Wide establishing shot. Camera slowly reveals the full environment. Sense of place and scale.",
    "hook":         "Dynamic opening shot. Bold striking visual. Immediate visual impact. Camera moves with energy.",
    "harvest":      "Intimate close-up of hands harvesting. Slow deliberate movement. Tactile textures prominent.",
    "quality":      "Extreme macro close-up revealing surface texture and freshness detail. Very slow drift.",
    "process":      "Mid-shot showing careful skilled hands at work. Methodical deliberate movement.",
    "hero":         "Beauty shot of product. Camera slowly orbits or glides. Maximum visual appeal.",
    "lifestyle":    "Aspirational lifestyle context. Product integrated naturally. Warm inviting atmosphere.",
    "cta":          "Bold confident final shot. Camera pushes toward product. Energetic decisive movement.",
    "demo":         "Clear detailed product demonstration. Smooth tracking movement follows the action.",
    "problem":      "Relatable real-world scene. Slightly imperfect, authentic feel.",
    "solution":     "Satisfying reveal. Smooth controlled movement. Problem resolved.",
    "proof":        "Confident steady shot. Clean and credible. Subtle slow zoom.",
    "origin":       "Wide sweeping landscape. Slow cinematic pan. Sense of provenance and journey.",
    "reveal":       "Dramatic reveal motion. Before/after contrast. Smooth transition.",
}

_DEFAULT_PURPOSE = "Smooth cinematic movement. Subject clearly visible. Professional commercial quality."


def build_clip_prompt(
    scene: dict,
    video_type: str = "ugc",
    product_name: str | None = None,
    product_description: str | None = None,
) -> str:
    """
    Build a detailed, consistent text-to-video prompt for a storyboard scene.

    Uses a style anchor (consistent across all scenes) + scene-specific subject/motion.
    product_name and product_description are inserted so the model renders the right product.
    product_description should include specific visual attributes (colour, texture, shape)
    so the model generates the actual product rather than a generic approximation.
    """
    # 1. Style anchor — locks in consistent look across all scenes
    style_anchor = _TYPE_STYLE_ANCHOR.get(video_type, "Cinematic commercial footage.")

    # 2. Purpose-based subject/motion guidance
    purpose = (scene.get("purpose") or "demo").lower().strip()
    purpose_guide = _PURPOSE_GUIDANCE.get(purpose, _DEFAULT_PURPOSE)

    # 3. Scene visual description (from storyboard LLM)
    visual = (scene.get("visual_description") or "").strip()

    # 4. Camera motion from shot metadata
    cam_type = (scene.get("camera_type") or "").lower().strip()
    movement = (scene.get("movement") or "none").lower().strip()
    cam_key = f"{cam_type}_{movement}" if cam_type else None
    cam_motion = _CAMERA_MOTION.get(cam_key or "", "")

    # 5. Product — name + visual description for consistent appearance across clips.
    # The description anchors the model to the real product's visual characteristics
    # (colour, texture, shape, cut appearance) rather than a generic approximation.
    product_parts: list[str] = []
    if product_name:
        product_parts.append(f"Product: {product_name}.")
    if product_description:
        # Trim to first 200 chars — enough for visual anchoring, not overwhelming
        desc = product_description.strip()[:200]
        product_parts.append(f"Visual reference: {desc}.")
    product_str = " ".join(product_parts)

    # 6. Lighting
    lighting = (scene.get("lighting_style") or "").strip()

    # 7. Product focus detail
    product_focus = (scene.get("product_focus") or "").strip()

    # Assemble prompt — order matters: style → subject → motion → product → details → quality
    parts: list[str] = [style_anchor]
    parts.append(purpose_guide)
    if visual:
        parts.append(visual)
    if cam_motion:
        parts.append(f"Camera: {cam_motion}.")
    if product_str:
        parts.append(product_str)
    if product_focus:
        parts.append(product_focus)
    if lighting:
        parts.append(f"Lighting: {lighting}.")
    parts.append(
        "Photorealistic. No text overlays. No watermarks. No logos. "
        "Smooth natural motion. Professional commercial advertisement quality."
    )

    return " ".join(p.rstrip(".").strip() + "." for p in parts if p.strip())


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
    product_name: str | None = None,
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
    prompt = build_clip_prompt(scene, video_type, product_name=product_name)

    if dry_run:
        return {
            "scene_id": scene_id, "scene_index": scene_index,
            "status": "dry_run", "clip_path": None, "clip_url": None,
            "prompt": prompt, "error": None,
        }

    clip_dir = output_dir or Path(config.CI_CLIP_OUTPUT_DIR) / str(storyboard_id)
    provider = get_clip_provider(provider_name)

    clip_path: str | None = None
    error_msg: str | None = None
    try:
        clip_path = provider.generate(
            prompt=prompt,
            duration=duration,
            aspect_ratio=aspect_ratio,
            scene_id=scene_id,
            output_dir=clip_dir,
        )
    except Exception as exc:
        import logging
        logging.getLogger(__name__).error(
            "Provider %s raised for scene %s: %s", provider.name, scene_id, exc, exc_info=True
        )
        error_msg = f"{type(exc).__name__}: {exc}"

    if clip_path is None and error_msg is None:
        error_msg = "Provider returned no clip"

    is_mock = clip_path and clip_path.startswith("mock://")
    is_url  = clip_path and clip_path.startswith(("http://", "https://"))

    # Download URL clips to persistent volume immediately — CDN URLs expire in ~24h
    clip_local_path: str | None = None
    clip_url: str | None = clip_path if is_url else None
    if is_url and clip_path:
        clip_dir.mkdir(parents=True, exist_ok=True)
        local_dest = clip_dir / f"scene_{scene_id}.mp4"
        try:
            import urllib.request
            urllib.request.urlretrieve(clip_path, str(local_dest))
            if local_dest.exists() and local_dest.stat().st_size > 0:
                clip_local_path = str(local_dest)
            else:
                local_dest.unlink(missing_ok=True)
        except Exception as dl_exc:
            import logging
            logging.getLogger(__name__).warning(
                "Could not download clip URL for scene %s: %s", scene_id, dl_exc
            )
    elif not is_mock and clip_path:
        clip_local_path = clip_path

    status = "ok" if clip_path else "error"

    if not dry_run:
        _upsert_scene_clip(
            conn=conn,
            storyboard_id=storyboard_id,
            scene_id=scene_id,
            scene_index=scene_index,
            provider=provider.name,
            model=getattr(provider, "_model", provider.name),
            prompt=prompt,
            clip_local_path=clip_local_path,
            clip_url=clip_url,
            duration_seconds=duration,
            aspect_ratio=aspect_ratio,
            status=status,
            error_message=error_msg,
        )

    return {
        "scene_id": scene_id,
        "scene_index": scene_index,
        "status": status,
        "clip_path": clip_local_path or clip_url,
        "clip_url": clip_url,
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
    clip_model: str | None = None,
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

    # Fetch storyboard metadata for video_type + product name
    sb_row = conn.execute(
        "SELECT video_type, product_id FROM video_storyboards WHERE id = ?", (storyboard_id,)
    ).fetchone()
    video_type = (sb_row["video_type"] if sb_row else None) or "ugc"

    # Resolve product name + description + image for consistent prompt anchoring.
    # description gives the model specific visual attributes (colour, texture, shape)
    # image_url enables image-to-video conditioning (animates FROM the real product photo)
    product_name: str | None = None
    product_description: str | None = None
    product_image_url: str | None = None
    if sb_row and sb_row["product_id"]:
        prod_row = conn.execute(
            "SELECT name, short_description, description, image_url FROM products WHERE id = ? LIMIT 1",
            (sb_row["product_id"],),
        ).fetchone()
        if prod_row:
            product_name = prod_row["name"]
            # Prefer short_description for prompt brevity; fall back to first 200 chars of description
            product_description = (
                prod_row["short_description"]
                or (prod_row["description"] or "")[:200]
                or None
            )
            product_image_url = prod_row["image_url"] or None
        else:
            # product_id may be a plain name string
            product_name = str(sb_row["product_id"])

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

    from creative_intelligence.video.clip_providers import get_clip_provider, ReplicateSceneClipProvider
    from creative_intelligence import config

    clip_dir = output_dir or Path(config.CI_CLIP_OUTPUT_DIR) / str(storyboard_id)
    provider = get_clip_provider(provider_name, clip_model=clip_model)

    # ── Async prediction path (Replicate) ────────────────────────────
    # Create all predictions instantly (~1s each), store prediction IDs in DB,
    # then poll for results. Predictions survive container restarts.
    if isinstance(provider, ReplicateSceneClipProvider) and not dry_run:
        import logging as _log
        _logger = _log.getLogger(__name__)
        for scene in scenes_to_generate:
            scene_id    = scene.get("id", 0)
            scene_index = scene.get("scene_index", 0)
            duration    = float(scene.get("duration_seconds") or 5.0)
            prompt      = build_clip_prompt(
                scene, video_type,
                product_name=product_name,
                product_description=product_description,
            )
            pred_id = provider.create_prediction(
                prompt, duration, image_url=product_image_url
            )
            if pred_id:
                _logger.info("Created prediction %s for scene %s", pred_id, scene_id)
                # Write pending row with prediction_id
                conn.execute(
                    """DELETE FROM video_scene_clips WHERE storyboard_id=? AND scene_id=?""",
                    (storyboard_id, scene_id),
                )
                conn.execute(
                    """INSERT INTO video_scene_clips
                       (storyboard_id, scene_id, scene_index, provider, model, prompt,
                        duration_seconds, aspect_ratio, status, prediction_id)
                       VALUES (?,?,?,?,?,?,?,?,'pending',?)""",
                    (storyboard_id, scene_id, scene_index,
                     provider.name, getattr(provider, "_model", provider.name),
                     prompt, duration, aspect_ratio, pred_id),
                )
                conn.commit()
                results.append({
                    "scene_id": scene_id, "scene_index": scene_index,
                    "status": "pending", "prediction_id": pred_id,
                    "prompt": prompt, "error": None,
                })
            else:
                _logger.error("Failed to create prediction for scene %s", scene_id)
                conn.execute(
                    """DELETE FROM video_scene_clips WHERE storyboard_id=? AND scene_id=?""",
                    (storyboard_id, scene_id),
                )
                conn.execute(
                    """INSERT INTO video_scene_clips
                       (storyboard_id, scene_id, scene_index, provider, model, prompt,
                        duration_seconds, aspect_ratio, status, error_message)
                       VALUES (?,?,?,?,?,?,?,?,'error','Failed to create prediction')""",
                    (storyboard_id, scene_id, scene_index,
                     provider.name, getattr(provider, "_model", provider.name),
                     prompt, duration, aspect_ratio),
                )
                conn.commit()
                results.append({
                    "scene_id": scene_id, "scene_index": scene_index,
                    "status": "error", "error": "Failed to create prediction",
                })

    # Sort results by scene_index
    results.sort(key=lambda r: r.get("scene_index", 0))

    pending  = sum(1 for r in results if r["status"] == "pending")
    generated = sum(1 for r in results if r["status"] in ("ok", "dry_run"))
    failed = sum(1 for r in results if r["status"] == "error")

    if pending > 0:
        status = "pending"
    elif generated == 0 and failed > 0:
        status = "all_failed"
    elif failed > 0:
        status = "partial"
    else:
        status = "ok"

    return {
        "storyboard_id": storyboard_id,
        "total_scenes": len(scenes),
        "pending": pending,
        "generated": generated,
        "skipped": skipped,
        "failed": failed,
        "results": results,
        "status": status,
    }


def poll_storyboard_clips(storyboard_id: int, conn: sqlite3.Connection) -> dict[str, Any]:
    """
    Poll Replicate for all pending predictions for a storyboard.
    Updates DB rows from 'pending' → 'ok'/'error'.
    Downloads clip to volume if succeeded.
    Returns summary dict.
    """
    import logging as _log
    from creative_intelligence.video.clip_providers import ReplicateSceneClipProvider
    from creative_intelligence import config

    _logger = _log.getLogger(__name__)
    clip_dir = Path(config.CI_CLIP_OUTPUT_DIR)

    pending_rows = conn.execute(
        """SELECT id, scene_id, scene_index, storyboard_id, prediction_id, duration_seconds
           FROM video_scene_clips
           WHERE storyboard_id = ? AND status = 'pending' AND prediction_id IS NOT NULL""",
        (storyboard_id,),
    ).fetchall()

    if not pending_rows:
        return {"storyboard_id": storyboard_id, "polled": 0, "completed": 0, "still_pending": 0}

    provider = ReplicateSceneClipProvider()
    completed = 0
    still_pending = 0

    for row in pending_rows:
        clip_id    = row["id"]
        scene_id   = row["scene_id"]
        pred_id    = row["prediction_id"]
        duration   = float(row["duration_seconds"] or 5.0)

        pred_status, video_url = provider.poll_prediction(pred_id)

        if pred_status == "succeeded" and video_url:
            # Download to persistent volume
            scene_clip_dir = clip_dir / str(storyboard_id)
            scene_clip_dir.mkdir(parents=True, exist_ok=True)
            local_dest = scene_clip_dir / f"scene_{scene_id}.mp4"
            clip_local_path: str | None = None
            try:
                import urllib.request as _ur
                _ur.urlretrieve(video_url, str(local_dest))
                if local_dest.exists() and local_dest.stat().st_size > 0:
                    clip_local_path = str(local_dest)
                else:
                    local_dest.unlink(missing_ok=True)
            except Exception as dl_exc:
                _logger.warning("Download failed for scene %s: %s", scene_id, dl_exc)

            conn.execute(
                """UPDATE video_scene_clips
                   SET status='ok', clip_url=?, clip_local_path=?
                   WHERE id=?""",
                (video_url, clip_local_path, clip_id),
            )
            conn.commit()
            _logger.info("Clip ready for scene %s (local=%s)", scene_id, clip_local_path)
            completed += 1

        elif pred_status == "failed":
            conn.execute(
                """UPDATE video_scene_clips
                   SET status='error', error_message='Replicate prediction failed'
                   WHERE id=?""",
                (clip_id,),
            )
            conn.commit()
            _logger.error("Prediction %s failed for scene %s", pred_id, scene_id)
            completed += 1  # done (with failure)

        else:
            still_pending += 1  # still processing

    return {
        "storyboard_id": storyboard_id,
        "polled": len(pending_rows),
        "completed": completed,
        "still_pending": still_pending,
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
