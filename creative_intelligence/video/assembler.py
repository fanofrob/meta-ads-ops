"""
Frame-based video assembler for Creative Intelligence.

Converts storyboard scene frames (images) into a real MP4 video.

Pipeline
--------
storyboard scenes → scene frames (images) → Ken Burns clips → text overlays
→ concatenate → export MP4

Dependencies
------------
moviepy >= 1.0.3     (pip install moviepy)
Pillow  >= 10.0.0    (pip install Pillow)
numpy                (transitive via moviepy)

Ken Burns is implemented frame-by-frame via Pillow/numpy so behaviour is
identical across all moviepy versions.  Text overlays are drawn with Pillow
so ImageMagick is not required.

Public API
----------
assemble_video(storyboard_id, conn, output_dir, ...) → dict
    {
        output_id:    int | None,
        output_path:  str,
        duration:     float,
        scene_count:  int,
        scenes_used:  int,
        status:       "ok" | "partial" | "no_frames" | "error",
        error:        str | None,
    }

check_dependencies() → list[str]   — returns list of missing package names
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

# ─────────────────────────────────────────────
# Aspect ratio → pixel dimensions
# ─────────────────────────────────────────────

_RATIO_DIMS: dict[str, tuple[int, int]] = {
    "9:16":  (768,  1344),
    "4:5":   (896,  1120),
    "1:1":   (1024, 1024),
    "16:9":  (1344, 768),
}

_DEFAULT_DIMS = (768, 1344)

# ─────────────────────────────────────────────
# Font search paths (system-dependent)
# ─────────────────────────────────────────────

_FONT_CANDIDATES: list[str] = [
    # Linux (Ubuntu / Debian / Railway)
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    # macOS
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/SFNSDisplay.ttf",
    # Windows
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
]

# ─────────────────────────────────────────────
# Dependency check
# ─────────────────────────────────────────────

def check_dependencies() -> list[str]:
    """Return names of missing required packages."""
    missing: list[str] = []
    try:
        import numpy  # noqa: F401
    except ImportError:
        missing.append("numpy")
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        missing.append("Pillow")
    try:
        from moviepy.editor import ImageSequenceClip  # noqa: F401
    except ImportError:
        missing.append("moviepy")
    return missing


# ─────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────

def _get_font(size: int):
    """Return a PIL ImageFont, trying system paths then falling back to default."""
    from PIL import ImageFont
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            pass
    # Built-in fallback (no kerning/sizing, but always available)
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def _cover_resize(img, target_w: int, target_h: int, pad_pct: float = 0.10):
    """Resize PIL image to cover (target + padding) while preserving aspect ratio."""
    from PIL import Image
    img_ar = img.width / img.height
    target_ar = target_w / target_h
    pad = 1.0 + pad_pct
    if img_ar > target_ar:
        new_h = int(target_h * pad)
        new_w = int(new_h * img_ar)
    else:
        new_w = int(target_w * pad)
        new_h = int(new_w / img_ar)
    return img.resize((new_w, new_h), Image.LANCZOS)


def _wrap_text(draw, text: str, font, max_width: int) -> list[str]:
    """Word-wrap text to fit within max_width pixels."""
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join(current + [word])
        try:
            bbox = draw.textbbox((0, 0), candidate, font=font)
            w = bbox[2] - bbox[0]
        except AttributeError:
            # Older Pillow: textlength
            w = draw.textlength(candidate, font=font)
        if w > max_width and current:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines or [""]


def _draw_text_on_frames(
    frames: list,
    text: str,
    style: str,
    target_w: int,
    target_h: int,
) -> list:
    """Draw text overlay on every frame array using Pillow."""
    import numpy as np
    from PIL import Image, ImageDraw

    if not text or not frames:
        return frames

    font_size = max(28, target_h // 26)
    font = _get_font(font_size)
    result: list = []

    for frame_arr in frames:
        img = Image.fromarray(frame_arr.astype("uint8"))
        draw = ImageDraw.Draw(img, "RGBA")

        max_text_w = target_w - 60
        lines = _wrap_text(draw, text, font, max_text_w)

        # Measure
        line_h = font_size + 6
        total_h = len(lines) * line_h

        if style in ("caption", "bottom_third"):
            y = target_h - total_h - 50
            # Semi-transparent background bar
            pad = 10
            draw.rectangle(
                [0, y - pad, target_w, y + total_h + pad],
                fill=(0, 0, 0, 140),
            )
        elif style in ("title_card", "center"):
            y = (target_h - total_h) // 2
            pad = 14
            # Measure widest line for bg
            max_lw = max(
                (draw.textbbox((0, 0), l, font=font)[2] if font else len(l) * font_size // 2)
                for l in lines
            )
            cx = target_w // 2
            draw.rectangle(
                [cx - max_lw // 2 - pad, y - pad,
                 cx + max_lw // 2 + pad, y + total_h + pad],
                fill=(0, 0, 0, 160),
            )
        elif style in ("bold_cta", "center_bold"):
            y = target_h // 3
            pad = 12
            draw.rectangle(
                [0, y - pad, target_w, y + total_h + pad],
                fill=(0, 0, 0, 180),
            )
        else:  # minimal / bottom
            y = target_h - total_h - 40

        for line in lines:
            try:
                bbox = draw.textbbox((0, 0), line, font=font)
                lw = bbox[2] - bbox[0]
            except AttributeError:
                lw = int(len(line) * font_size * 0.6)
            x = (target_w - lw) // 2

            # Shadow
            shadow_off = max(2, font_size // 16)
            draw.text((x + shadow_off, y + shadow_off), line, font=font,
                      fill=(0, 0, 0, 220))
            # Main text
            draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
            y += line_h

        result.append(np.array(img.convert("RGB")))

    return result


def _make_scene_frames(
    img_path: str,
    duration: float,
    movement: str,
    target_w: int,
    target_h: int,
    fps: int,
) -> list:
    """
    Build a list of numpy uint8 RGB frames for one scene.
    Applies Ken Burns motion (zoom in / zoom out / slow pan / tilt).
    """
    import numpy as np
    from PIL import Image

    try:
        pil_img = Image.open(img_path).convert("RGB")
    except Exception as exc:
        raise RuntimeError(f"Cannot open image {img_path}: {exc}") from exc

    base = _cover_resize(pil_img, target_w, target_h, pad_pct=0.10)
    base_w, base_h = base.size

    n_frames = max(1, int(round(duration * fps)))
    frames: list = []

    mv = (movement or "none").lower().replace("-", " ").replace("_", " ")

    for i in range(n_frames):
        # t goes 0 → 1 over the clip
        t = i / max(n_frames - 1, 1)

        if mv == "zoom in":
            scale = 1.0 + 0.06 * t
        elif mv == "zoom out":
            scale = 1.06 - 0.06 * t
        elif mv in ("slow pan", "slow pan"):
            scale = 1.04
        elif mv == "tilt down":
            scale = 1.04
        elif mv == "tilt up":
            scale = 1.04
        else:
            scale = 1.0

        sw = max(target_w, int(base_w * scale))
        sh = max(target_h, int(base_h * scale))

        scaled = base.resize((sw, sh), Image.BILINEAR)

        # Crop position
        if mv == "slow pan":
            max_pan = sw - target_w
            left = int(max_pan * t)
            top = (sh - target_h) // 2
        elif mv == "tilt down":
            left = (sw - target_w) // 2
            max_tilt = sh - target_h
            top = int(max_tilt * t)
        elif mv == "tilt up":
            left = (sw - target_w) // 2
            max_tilt = sh - target_h
            top = int(max_tilt * (1.0 - t))
        else:
            left = (sw - target_w) // 2
            top = (sh - target_h) // 2

        left = max(0, min(left, sw - target_w))
        top  = max(0, min(top,  sh - target_h))

        cropped = scaled.crop((left, top, left + target_w, top + target_h))
        frames.append(np.array(cropped, dtype="uint8"))

    return frames


# ─────────────────────────────────────────────
# AI image-to-video helpers
# ─────────────────────────────────────────────

def _image_to_data_uri(path_or_url: str) -> str | None:
    """Read an image (local path, file://, or http URL) and return a base64 data URI.

    Returns None for mock:// paths or on any read error.
    """
    import base64, urllib.request
    try:
        if path_or_url.startswith("mock://"):
            return None
        if path_or_url.startswith("file://"):
            path_or_url = path_or_url[7:]
        if path_or_url.startswith(("http://", "https://")):
            with urllib.request.urlopen(path_or_url, timeout=30) as r:
                data = r.read()
                ct = r.headers.get("Content-Type", "image/jpeg")
                mime = ct.split(";")[0].strip()
        else:
            with open(path_or_url, "rb") as f:
                data = f.read()
            mime = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
        return f"data:{mime};base64,{base64.b64encode(data).decode()}"
    except Exception:
        return None


def _download_video_clip(url: str, dest: Path) -> bool:
    """Download a video URL to dest path. Returns True on success."""
    import logging
    import urllib.request
    try:
        urllib.request.urlretrieve(url, str(dest))
        ok = dest.exists() and dest.stat().st_size > 0
        if not ok:
            logging.getLogger(__name__).warning("Downloaded clip is empty: %s", url)
        return ok
    except Exception as exc:
        logging.getLogger(__name__).warning("Failed to download clip %s: %s", url, exc)
        return False


def _animate_scene(
    img_path: str,
    prompt: str,
    motion_model: str,
    api_key: str,
    tmp_dir: Path,
    scene_id: int,
) -> Path | None:
    """Call a Replicate image-to-video model for one scene.

    Supports:
      minimax/video-01-live           — first_frame_image + prompt → ~6s HD clip
      stability-ai/stable-video-*    — image → ~4s clip
      wan-video/*                    — image + prompt → ~5s clip
      any other model                — tries common input keys, skips unknowns

    Returns local Path to downloaded MP4, or None on any failure.
    """
    import replicate

    img_input = (
        img_path
        if img_path.startswith(("http://", "https://"))
        else _image_to_data_uri(img_path)
    )
    if not img_input:
        return None

    client = replicate.Client(api_token=api_key)
    model_lower = motion_model.lower()

    # Prepend motion instruction so minimax/SVD generate actual movement
    motion_prompt = f"Smooth cinematic motion, natural movement. {prompt}"

    try:
        if "minimax" in model_lower or "video-01" in model_lower:
            output = client.run(
                motion_model,
                input={"prompt": motion_prompt, "first_frame_image": img_input},
            )
        elif "stable-video" in model_lower or "svd" in model_lower:
            output = client.run(
                motion_model,
                input={
                    "image": img_input,
                    "video_length": "25_frames_with_svd_xt",
                    "fps": 8,
                    "motion_bucket_id": 100,
                    "cond_aug": 0.02,
                },
            )
        elif "wan" in model_lower:
            output = client.run(
                motion_model,
                input={"image": img_input, "prompt": motion_prompt},
            )
        else:
            # Generic: try all common keys; models ignore unknown ones
            output = client.run(
                motion_model,
                input={"image": img_input, "prompt": motion_prompt,
                       "first_frame_image": img_input},
            )
    except Exception:
        return None

    # Extract URL from varied output shapes
    video_url: str | None = None
    if isinstance(output, str) and output.startswith("http"):
        video_url = output
    elif hasattr(output, "url"):
        video_url = str(output.url)
    elif isinstance(output, (list, tuple)) and output:
        item = output[0]
        video_url = str(item) if isinstance(item, str) else (
            item.url if hasattr(item, "url") else str(item)
        )

    if not video_url:
        return None

    dest = tmp_dir / f"scene_{scene_id}_ai.mp4"
    return dest if _download_video_clip(video_url, dest) else None


def _animate_scenes_parallel(
    scenes_with_paths: list[tuple[dict, str]],   # (scene_row, img_path)
    motion_model: str,
    api_key: str,
    tmp_dir: Path,
    max_workers: int = 4,
) -> dict[int, Path]:
    """Animate multiple scenes in parallel. Returns {scene_index: clip_path}."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: dict[int, Path] = {}

    def _worker(scene: dict, img_path: str) -> tuple[int, Path | None]:
        idx = scene.get("scene_index", scene.get("id", 0))
        prompt = scene.get("visual_description", "")
        clip = _animate_scene(img_path, prompt, motion_model, api_key, tmp_dir, idx)
        return idx, clip

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {
            ex.submit(_worker, scene, path): scene
            for scene, path in scenes_with_paths
        }
        for fut in as_completed(futures):
            try:
                idx, clip = fut.result()
                if clip:
                    results[idx] = clip
            except Exception:
                pass

    return results


def _resolve_image_path(asset_path_or_url: str) -> str | None:
    """
    Resolve an asset_path_or_url to a local file path.
    - Local paths: returned as-is if the file exists.
    - http/https URLs: downloaded to a temp file.
    - mock:// paths: no real image, return None.
    """
    if not asset_path_or_url:
        return None
    if asset_path_or_url.startswith("mock://"):
        return None
    if asset_path_or_url.startswith("file://"):
        p = asset_path_or_url[7:]
        return p if Path(p).exists() else None
    if asset_path_or_url.startswith(("http://", "https://")):
        import tempfile
        import urllib.request
        try:
            suffix = Path(asset_path_or_url.split("?")[0]).suffix or ".png"
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            urllib.request.urlretrieve(asset_path_or_url, tmp.name)
            return tmp.name
        except Exception:
            return None
    # Local path
    p = Path(asset_path_or_url)
    return str(p) if p.exists() else None


# ─────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────

def _save_video_output(
    storyboard_id: int,
    video_type: str,
    source_output_type: str,
    source_production_output_id: int | None,
    output_path: str,
    metadata: dict,
    conn: sqlite3.Connection,
) -> int:
    cur = conn.execute(
        """INSERT INTO video_outputs
           (storyboard_id, video_type, source_output_type,
            source_production_output_id, output_path, metadata_json, status)
           VALUES (?, ?, ?, ?, ?, ?, 'ok')""",
        (
            storyboard_id,
            video_type,
            source_output_type,
            source_production_output_id,
            output_path,
            json.dumps(metadata),
        ),
    )
    return cur.lastrowid


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def assemble_video(
    storyboard_id: int,
    conn: sqlite3.Connection,
    output_dir: Path | str,
    *,
    video_type: str = "ugc",
    aspect_ratio: str = "9:16",
    fps: int = 24,
    add_text_overlays: bool = True,
    text_overlay_style: str | None = None,   # None → infer from video_type
    motion_mode: str = "ken_burns",          # "ken_burns" | "ai_video" | "clip"
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Assemble an MP4 from the scene frames of a video storyboard.

    Parameters
    ----------
    storyboard_id   : ID of the video_storyboards row
    conn            : SQLite connection
    output_dir      : Directory to write the MP4 (created if absent)
    video_type      : One of VIDEO_TYPES (for style defaults)
    aspect_ratio    : "9:16" | "4:5" | "1:1" | "16:9"
    fps             : Output frames per second (default 24)
    add_text_overlays: Draw scene text_overlay on each clip
    text_overlay_style: Override style from video_type config
    motion_mode     : "ken_burns" (zoom/pan on stills) | "ai_video" (Replicate i2v) |
                      "clip" (use pre-generated video_scene_clips rows, fallback to ken_burns)
    dry_run         : If True, do not write MP4 or DB row

    Returns
    -------
    {
        output_id:    int | None,
        output_path:  str | None,
        duration:     float,
        scene_count:  int,
        scenes_used:  int,          # scenes that had a frame
        status:       str,          # "ok" | "partial" | "no_frames" | "error"
        error:        str | None,
    }
    """
    missing = check_dependencies()
    if missing:
        return {
            "output_id": None, "output_path": None, "duration": 0,
            "scene_count": 0, "scenes_used": 0,
            "status": "error",
            "error": f"Missing dependencies: {', '.join(missing)}. "
                     f"Run: pip install {' '.join(missing)}",
        }

    from moviepy.editor import ImageSequenceClip, concatenate_videoclips
    from creative_intelligence.video.types import get_config

    cfg = get_config(video_type)
    target_w, target_h = _RATIO_DIMS.get(aspect_ratio, _DEFAULT_DIMS)
    overlay_style = text_overlay_style or cfg.text_overlay_style

    # ── Fetch storyboard ──────────────────────────────────────────────
    sb_row = conn.execute(
        "SELECT * FROM video_storyboards WHERE id = ?", (storyboard_id,)
    ).fetchone()
    if not sb_row:
        return {"output_id": None, "output_path": None, "duration": 0,
                "scene_count": 0, "scenes_used": 0,
                "status": "error", "error": f"Storyboard {storyboard_id} not found"}

    sb = dict(sb_row)

    # ── Fetch scenes with asset info ──────────────────────────────────
    scene_rows = conn.execute(
        """SELECT vs.*, ra.asset_path_or_url
           FROM video_scenes vs
           LEFT JOIN render_assets ra ON ra.id = vs.render_asset_id
           WHERE vs.storyboard_id = ?
           ORDER BY vs.scene_index""",
        (storyboard_id,),
    ).fetchall()

    scenes = [dict(r) for r in scene_rows]
    scene_count = len(scenes)

    if scene_count == 0:
        return {"output_id": None, "output_path": None, "duration": 0,
                "scene_count": 0, "scenes_used": 0,
                "status": "error", "error": "No scenes found for storyboard"}

    # ── Build per-scene clips ─────────────────────────────────────────
    clips: list = []
    total_duration: float = 0.0
    scenes_used: int = 0

    # Resolve all image paths first
    scene_paths: list[tuple[dict, str]] = []
    for scene in scenes:
        asset_url = scene.get("asset_path_or_url") or ""
        img_path = _resolve_image_path(asset_url)
        if img_path:
            scene_paths.append((scene, img_path))

    # ── AI video mode: animate each frame into a real video clip ─────
    import tempfile
    tmp_dir = Path(tempfile.mkdtemp(prefix="ci_aivideo_"))

    # "clip" mode doesn't need scene images — guard only applies to image-based modes
    if motion_mode != "clip" and not scene_paths:
        return {"output_id": None, "output_path": None, "duration": 0,
                "scene_count": scene_count, "scenes_used": 0,
                "status": "no_frames",
                "error": "No scene frames available — generate frames first"}

    if motion_mode == "ai_video":
        from creative_intelligence import config as _cfg
        api_key = _cfg.CI_REPLICATE_API_KEY
        motion_model = _cfg.CI_VIDEO_MOTION_MODEL

        if not api_key:
            return {"output_id": None, "output_path": None, "duration": 0,
                    "scene_count": scene_count, "scenes_used": 0,
                    "status": "error",
                    "error": "CI_REPLICATE_API_KEY not set — cannot generate AI video"}

        ai_clips: dict[int, Path] = _animate_scenes_parallel(
            scene_paths, motion_model, api_key, tmp_dir
        )

        for scene, img_path in scene_paths:
            idx = scene.get("scene_index", scene.get("id", 0))
            clip_path = ai_clips.get(idx)
            duration = float(scene.get("duration_seconds") or cfg.avg_scene_seconds)
            text = scene.get("text_overlay") or ""
            ai_success = False

            if clip_path:
                try:
                    from moviepy.editor import VideoFileClip
                    vc = VideoFileClip(str(clip_path))
                    vc_resized = vc.resize((target_w, target_h))
                    if add_text_overlays and text:
                        raw_frames = [
                            vc_resized.get_frame(t)
                            for t in [i / fps for i in range(int(vc_resized.duration * fps))]
                        ]
                        raw_frames = _draw_text_on_frames(
                            raw_frames, text, overlay_style, target_w, target_h
                        )
                        clip = ImageSequenceClip(raw_frames, fps=fps)
                        vc_resized.close()
                    else:
                        clip = vc_resized
                    clips.append(clip)
                    total_duration += vc.duration
                    scenes_used += 1
                    vc.close()
                    ai_success = True
                except Exception:
                    pass

            if not ai_success:
                # Fallback: Ken Burns for any scene where AI generation failed/skipped
                try:
                    movement = scene.get("movement") or cfg.ken_burns_map.get(
                        (scene.get("purpose") or "demo").lower(), cfg.default_movement
                    )
                    frame_arrays = _make_scene_frames(
                        img_path, duration, movement, target_w, target_h, fps
                    )
                    if add_text_overlays and text:
                        frame_arrays = _draw_text_on_frames(
                            frame_arrays, text, overlay_style, target_w, target_h
                        )
                    clips.append(ImageSequenceClip(frame_arrays, fps=fps))
                    total_duration += duration
                    scenes_used += 1
                except Exception:
                    pass

    elif motion_mode == "clip":
        # ── Pre-generated clip mode ────────────────────────────────────
        # Look up video_scene_clips rows (status=ok) for each scene.
        # Falls back to Ken Burns for any scene without a ready clip.
        clip_rows = conn.execute(
            """SELECT scene_id, scene_index, clip_local_path, clip_url
               FROM video_scene_clips
               WHERE storyboard_id = ? AND status = 'ok'
               ORDER BY scene_index""",
            (storyboard_id,),
        ).fetchall()
        clip_by_scene_id: dict[int, dict] = {r["scene_id"]: dict(r) for r in clip_rows}
        # Build img_path lookup for Ken Burns fallback (scenes that have images)
        img_by_scene_id: dict[int, str] = {s["id"]: p for s, p in scene_paths}

        for scene in scenes:
            scene_id = scene.get("id", 0)
            duration = float(scene.get("duration_seconds") or cfg.avg_scene_seconds)
            text = scene.get("text_overlay") or ""
            clip_record = clip_by_scene_id.get(scene_id)
            used_clip = False

            if clip_record:
                import logging as _log
                local_stored = clip_record.get("clip_local_path") or ""
                remote_url   = clip_record.get("clip_url") or ""
                clip_path_str = local_stored or remote_url

                local_path: str | None = None
                if clip_path_str and not clip_path_str.startswith("mock://"):
                    # Prefer local file (always use if it exists)
                    if local_stored and Path(local_stored).exists():
                        local_path = local_stored
                    elif remote_url and remote_url.startswith(("http://", "https://")):
                        # Try passing URL directly to ffmpeg/moviepy first (no disk needed)
                        local_path = remote_url  # moviepy/ffmpeg can open http:// URLs
                    elif clip_path_str and not clip_path_str.startswith("http"):
                        local_path = clip_path_str if Path(clip_path_str).exists() else None

                    if local_path:
                        _log.getLogger(__name__).info(
                            "Assembling scene %s from: %s", scene_id,
                            "local" if local_path == local_stored else "url"
                        )

                    if local_path:
                        try:
                            from moviepy.editor import VideoFileClip
                            vc = VideoFileClip(str(local_path))
                            vc_resized = vc.resize((target_w, target_h))
                            if add_text_overlays and text:
                                raw_frames = [
                                    vc_resized.get_frame(t)
                                    for t in [i / fps for i in range(int(vc_resized.duration * fps))]
                                ]
                                raw_frames = _draw_text_on_frames(
                                    raw_frames, text, overlay_style, target_w, target_h
                                )
                                clip = ImageSequenceClip(raw_frames, fps=fps)
                                vc_resized.close()
                            else:
                                clip = vc_resized
                            clips.append(clip)
                            total_duration += vc.duration
                            scenes_used += 1
                            vc.close()
                            used_clip = True
                        except Exception:
                            pass

            if not used_clip:
                # Fallback to Ken Burns only if a frame image is available
                img_path = img_by_scene_id.get(scene_id)
                if img_path:
                    try:
                        movement = scene.get("movement") or cfg.ken_burns_map.get(
                            (scene.get("purpose") or "demo").lower(), cfg.default_movement
                        )
                        frame_arrays = _make_scene_frames(
                            img_path, duration, movement, target_w, target_h, fps
                        )
                        if add_text_overlays and text:
                            frame_arrays = _draw_text_on_frames(
                                frame_arrays, text, overlay_style, target_w, target_h
                            )
                        clips.append(ImageSequenceClip(frame_arrays, fps=fps))
                        total_duration += duration
                        scenes_used += 1
                    except Exception:
                        pass

    else:
        # ── Ken Burns mode (default) ──────────────────────────────────
        for scene, img_path in scene_paths:
            duration = float(scene.get("duration_seconds") or cfg.avg_scene_seconds)
            purpose  = (scene.get("purpose") or "demo").lower()
            movement = scene.get("movement") or cfg.ken_burns_map.get(purpose, cfg.default_movement)
            text     = scene.get("text_overlay") or ""

            try:
                frame_arrays = _make_scene_frames(img_path, duration, movement, target_w, target_h, fps)
            except Exception:
                continue

            if add_text_overlays and text:
                frame_arrays = _draw_text_on_frames(frame_arrays, text, overlay_style, target_w, target_h)

            clips.append(ImageSequenceClip(frame_arrays, fps=fps))
            total_duration += duration
            scenes_used += 1

    if not clips:
        return {"output_id": None, "output_path": None, "duration": 0,
                "scene_count": scene_count, "scenes_used": 0,
                "status": "no_frames",
                "error": "No scene frames could be assembled"}

    # ── Concatenate ───────────────────────────────────────────────────
    final = concatenate_videoclips(clips, method="compose")

    # ── Export ────────────────────────────────────────────────────────
    if dry_run:
        return {
            "output_id": None, "output_path": None,
            "duration": total_duration,
            "scene_count": scene_count, "scenes_used": scenes_used,
            "status": "dry_run", "error": None,
        }

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    filename = f"video_{storyboard_id}_{video_type}_{ts}.mp4"
    output_path = str(output_dir / filename)

    final.write_videofile(
        output_path,
        fps=fps,
        codec="libx264",
        audio=False,
        logger=None,         # suppress moviepy progress bars in server logs
        preset="fast",
        ffmpeg_params=["-crf", "23"],
    )

    # Cleanup clips (free memory)
    for c in clips:
        try:
            c.close()
        except Exception:
            pass
    try:
        final.close()
    except Exception:
        pass

    # ── Persist ───────────────────────────────────────────────────────
    metadata = {
        "aspect_ratio":  aspect_ratio,
        "fps":           fps,
        "scene_count":   scene_count,
        "scenes_used":   scenes_used,
        "duration":      total_duration,
        "video_type":    video_type,
        "motion_mode":   motion_mode,
    }
    with conn:
        output_id = _save_video_output(
            storyboard_id=storyboard_id,
            video_type=video_type,
            source_output_type=sb.get("source_output_type", "concept"),
            source_production_output_id=sb.get("source_production_output_id"),
            output_path=output_path,
            metadata=metadata,
            conn=conn,
        )

    status = "ok" if scenes_used == scene_count else "partial"
    return {
        "output_id":   output_id,
        "output_path": output_path,
        "duration":    total_duration,
        "scene_count": scene_count,
        "scenes_used": scenes_used,
        "status":      status,
        "error":       None,
    }
