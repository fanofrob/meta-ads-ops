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

    for scene in scenes:
        asset_url = scene.get("asset_path_or_url") or ""
        img_path = _resolve_image_path(asset_url)

        if not img_path:
            # No frame for this scene — skip
            continue

        duration = float(scene.get("duration_seconds") or cfg.avg_scene_seconds)
        purpose  = (scene.get("purpose") or "demo").lower()
        movement = scene.get("movement") or cfg.ken_burns_map.get(purpose, cfg.default_movement)
        text     = scene.get("text_overlay") or ""

        try:
            frame_arrays = _make_scene_frames(img_path, duration, movement, target_w, target_h, fps)
        except Exception as exc:
            # Skip unreadable frames rather than abort
            continue

        if add_text_overlays and text:
            frame_arrays = _draw_text_on_frames(frame_arrays, text, overlay_style, target_w, target_h)

        clip = ImageSequenceClip(frame_arrays, fps=fps)
        clips.append(clip)
        total_duration += duration
        scenes_used += 1

    if not clips:
        return {"output_id": None, "output_path": None, "duration": 0,
                "scene_count": scene_count, "scenes_used": 0,
                "status": "no_frames",
                "error": "No scene frames available — generate frames first"}

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
