"""
PIL-based text compositing for the static rendering layer.

Composites headline, body copy, and CTA text onto a background image using
one of four layout templates to produce the final ad image.

Public API
----------
composite_ad(background_source, spec, output_path=None, layout=None) → str
    background_source: local file path or https:// URL (mock:// is a no-op)
    Returns the local path to the composited PNG.

auto_select_layout(spec) → str
    Returns: 'bottom_panel' | 'top_left_copy' | 'top_center_hero' | 'poster_text_heavy'

Layout templates
----------------
bottom_panel       : Dark gradient scrim over bottom ~40 %; headline + body inside,
                     CTA pill at foot. Best for premium and product_hero.
top_center_hero    : Top gradient, headline+body centred near top, CTA at bottom.
                     Best for minimal and overhead shots.
top_left_copy      : Semi-transparent top-left panel with all copy. Best for reveal.
poster_text_heavy  : Full-frame dark scrim, large centred headline+body+CTA.
                     Best for direct_response.
"""
from __future__ import annotations

import hashlib
import logging
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ─── Canvas sizes ──────────────────────────────────────────────────────────────
_CANVAS_SIZES: dict[str, tuple[int, int]] = {
    "9:16":  (1080, 1920),
    "4:5":   (1080, 1350),
    "1:1":   (1080, 1080),
    "16:9":  (1920, 1080),
}
_DEFAULT_CANVAS = (1080, 1920)

# ─── Layout templates ──────────────────────────────────────────────────────────
LAYOUT_TEMPLATES = ("bottom_panel", "top_left_copy", "top_center_hero", "poster_text_heavy")

# ─── Variant → layout ──────────────────────────────────────────────────────────
_VARIANT_LAYOUT: dict[str, str] = {
    "minimal":         "top_center_hero",
    "premium":         "bottom_panel",
    "direct_response": "poster_text_heavy",
    "reveal":          "top_left_copy",
    "product_hero":    "bottom_panel",
}

# ─── Shot type → layout override ───────────────────────────────────────────────
_SHOT_LAYOUT_OVERRIDE: dict[str, str] = {
    "overhead":  "top_center_hero",
    "flat lay":  "top_center_hero",
    "flat_lay":  "top_center_hero",
    "close-up":  "bottom_panel",
    "close up":  "bottom_panel",
    "macro":     "bottom_panel",
    "side":      "top_left_copy",
}

# ─── Font search paths ─────────────────────────────────────────────────────────
_FONT_PATHS_BOLD: list[str] = [
    # Linux (Railway / Debian)
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    # macOS
    "/Library/Fonts/Arial Bold.ttf",
    "/Library/Fonts/NotoSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    # Additional common Linux paths
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
]
_FONT_PATHS_REGULAR: list[str] = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    "/Library/Fonts/Arial.ttf",
    "/Library/Fonts/NotoSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
]

# Resolved font path cache
_font_path_cache: dict[str, str | None] = {}


def _find_font_path(paths: list[str]) -> str | None:
    key = "|".join(paths[:2])  # cheap cache key
    if key not in _font_path_cache:
        _font_path_cache[key] = next((p for p in paths if Path(p).exists()), None)
    return _font_path_cache[key]


def _load_font(size: int, bold: bool = False) -> Any:
    """Load a TTF font at *size* pixels with graceful degradation."""
    from PIL import ImageFont

    paths = _FONT_PATHS_BOLD if bold else _FONT_PATHS_REGULAR
    path = _find_font_path(paths)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    # Pillow >= 10.1 supports size=N on load_default
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        pass
    return ImageFont.load_default()


# ─── Layout selector ───────────────────────────────────────────────────────────

def auto_select_layout(spec: dict) -> str:
    """Return the best layout template name for a RenderSpec dict."""
    shot = (spec.get("shot_type") or "").lower().strip()
    override = _SHOT_LAYOUT_OVERRIDE.get(shot)
    if override:
        return override
    variant = (spec.get("variant_label") or "premium").lower()
    return _VARIANT_LAYOUT.get(variant, "bottom_panel")


# ─── Drawing primitives ────────────────────────────────────────────────────────

def _make_vertical_gradient(
    w: int,
    h: int,
    direction: str = "bottom",  # "bottom" | "top" | "center"
    start_alpha: int = 0,
    end_alpha: int = 220,
) -> Any:
    """Return an RGBA Image (black, varying alpha) as a compositing overlay."""
    from PIL import Image

    # Build a 1-pixel-wide strip then scale — much faster than per-pixel loops
    strip = Image.new("RGBA", (1, h), (0, 0, 0, 0))
    for y in range(h):
        if direction == "bottom":
            t = y / max(h - 1, 1)
        elif direction == "top":
            t = 1.0 - y / max(h - 1, 1)
        else:  # center
            t = 1.0 - abs(2.0 * y / max(h - 1, 1) - 1.0)
        alpha = int(start_alpha + (end_alpha - start_alpha) * t)
        strip.putpixel((0, y), (0, 0, 0, max(0, min(255, alpha))))
    return strip.resize((w, h), Image.NEAREST)


def _wrap_text(text: str, draw: Any, font: Any, max_width: int) -> list[str]:
    """Word-wrap *text* to fit within *max_width* pixels; return list of lines."""
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join(current + [word])
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width or not current:
            current.append(word)
        else:
            lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines or [""]


def _line_step(lines: list[str], draw: Any, font: Any, spacing: float = 1.3) -> int:
    """Pixel distance between baselines (height × spacing)."""
    if not lines:
        return 0
    bbox = draw.textbbox((0, 0), lines[0], font=font)
    return int((bbox[3] - bbox[1]) * spacing)


def _draw_text_block(
    draw: Any,
    lines: list[str],
    font: Any,
    x: int,
    y: int,
    fill: tuple = (255, 255, 255),
    shadow: tuple | None = (0, 0, 0),
    shadow_offset: int = 2,
    align: str = "left",        # "left" | "center"
    max_width: int = 0,
    line_spacing: float = 1.3,
) -> int:
    """Draw *lines* and return the y-coordinate immediately after the last line."""
    step = _line_step(lines, draw, font, line_spacing)
    cur_y = y
    for line in lines:
        lbbox = draw.textbbox((0, 0), line, font=font)
        lw = lbbox[2] - lbbox[0]
        lx = x + (max_width - lw) // 2 if (align == "center" and max_width) else x
        if shadow:
            draw.text(
                (lx + shadow_offset, cur_y + shadow_offset),
                line, font=font,
                fill=(*shadow, 160),
            )
        draw.text((lx, cur_y), line, font=font, fill=fill)
        cur_y += step
    return cur_y


def _draw_cta_pill(
    canvas: Any,
    text: str,
    font: Any,
    center_x: int,
    top_y: int,
    pad_x: int = 40,
    pad_y: int = 18,
    bg_fill: tuple = (79, 70, 229),
    text_fill: tuple = (255, 255, 255),
) -> int:
    """Draw a rounded-rect CTA pill centred at *center_x*. Returns bottom y."""
    from PIL import ImageDraw

    draw = ImageDraw.Draw(canvas)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    pill_w = tw + pad_x * 2
    pill_h = th + pad_y * 2
    x0 = center_x - pill_w // 2
    y0 = top_y
    x1 = x0 + pill_w
    y1 = y0 + pill_h
    radius = pill_h // 2

    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=bg_fill)
    tx = center_x - tw // 2
    ty = y0 + pad_y - bbox[1]
    draw.text((tx, ty), text, font=font, fill=text_fill)
    return y1


# ─── Layout implementations ────────────────────────────────────────────────────

def _layout_bottom_panel(canvas: Any, spec: dict) -> Any:
    """Dark gradient scrim over bottom ~40 % with headline, body, CTA pill."""
    from PIL import Image, ImageDraw

    w, h = canvas.size
    margin  = int(w * 0.07)
    text_w  = w - 2 * margin

    font_h1   = _load_font(max(44, int(w * 0.058)), bold=True)
    font_body = _load_font(max(28, int(w * 0.031)), bold=False)
    font_cta  = _load_font(max(28, int(w * 0.030)), bold=True)

    if canvas.mode != "RGBA":
        canvas = canvas.convert("RGBA")

    # Gradient scrim from 58 % down
    panel_start = int(h * 0.58)
    grad = _make_vertical_gradient(w, h - panel_start, direction="bottom",
                                   start_alpha=0, end_alpha=235)
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    overlay.paste(grad, (0, panel_start))
    canvas = Image.alpha_composite(canvas, overlay)

    draw = ImageDraw.Draw(canvas)
    headline  = (spec.get("headline_overlay") or spec.get("concept_title") or "").strip()
    body_text = (spec.get("body_overlay") or "").strip()
    cta_text  = (spec.get("cta_text") or "").strip()

    h_lines = _wrap_text(headline, draw, font_h1, text_w)
    b_lines = _wrap_text(body_text, draw, font_body, text_w) if body_text else []

    text_top = int(h * 0.625)
    after_h = _draw_text_block(draw, h_lines, font_h1, margin, text_top,
                                fill=(255, 255, 255), shadow_offset=2)
    if b_lines:
        after_b = _draw_text_block(draw, b_lines, font_body, margin,
                                    after_h + int(h * 0.012),
                                    fill=(220, 220, 235), shadow_offset=1)
    else:
        after_b = after_h

    if cta_text:
        cta_y = max(after_b + int(h * 0.015), int(h * 0.84))
        _draw_cta_pill(canvas, cta_text, font_cta, w // 2, cta_y)

    return canvas


def _layout_top_center_hero(canvas: Any, spec: dict) -> Any:
    """Top gradient with centred headline+body; CTA pill near bottom."""
    from PIL import Image, ImageDraw

    w, h = canvas.size
    margin  = int(w * 0.08)
    text_w  = w - 2 * margin

    font_h1   = _load_font(max(42, int(w * 0.052)), bold=True)
    font_body = _load_font(max(26, int(w * 0.028)), bold=False)
    font_cta  = _load_font(max(26, int(w * 0.028)), bold=True)

    if canvas.mode != "RGBA":
        canvas = canvas.convert("RGBA")

    # Top scrim (32 % of height)
    top_h = int(h * 0.32)
    grad = _make_vertical_gradient(w, top_h, direction="top",
                                   start_alpha=0, end_alpha=200)
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    overlay.paste(grad, (0, 0))
    # Bottom scrim (20 % of height)
    bot_h = int(h * 0.20)
    bot_grad = _make_vertical_gradient(w, bot_h, direction="bottom",
                                        start_alpha=0, end_alpha=180)
    overlay.paste(bot_grad, (0, h - bot_h))
    canvas = Image.alpha_composite(canvas, overlay)

    draw = ImageDraw.Draw(canvas)
    headline  = (spec.get("headline_overlay") or spec.get("concept_title") or "").strip()
    body_text = (spec.get("body_overlay") or "").strip()
    cta_text  = (spec.get("cta_text") or "").strip()

    h_lines = _wrap_text(headline, draw, font_h1, text_w)
    text_top = int(h * 0.06)
    after_h = _draw_text_block(draw, h_lines, font_h1, margin, text_top,
                                fill=(255, 255, 255), align="center",
                                max_width=text_w, shadow_offset=2)
    if body_text:
        b_lines = _wrap_text(body_text, draw, font_body, text_w)
        _draw_text_block(draw, b_lines, font_body, margin,
                         after_h + int(h * 0.012),
                         fill=(220, 220, 235), align="center",
                         max_width=text_w, shadow_offset=1)

    if cta_text:
        _draw_cta_pill(canvas, cta_text, font_cta, w // 2, int(h * 0.85))

    return canvas


def _layout_top_left_copy(canvas: Any, spec: dict) -> Any:
    """Semi-transparent top-left panel; product occupies the rest of the frame."""
    from PIL import Image, ImageDraw

    w, h = canvas.size
    panel_w = int(w * 0.58)
    panel_h = int(h * 0.44)
    margin  = int(w * 0.06)
    text_w  = panel_w - 2 * margin

    font_h1   = _load_font(max(40, int(w * 0.050)), bold=True)
    font_body = _load_font(max(26, int(w * 0.028)), bold=False)
    font_cta  = _load_font(max(26, int(w * 0.028)), bold=True)

    if canvas.mode != "RGBA":
        canvas = canvas.convert("RGBA")

    panel = Image.new("RGBA", (panel_w, panel_h), (0, 0, 0, 185))
    canvas.paste(panel, (0, 0), panel)

    draw = ImageDraw.Draw(canvas)
    headline  = (spec.get("headline_overlay") or spec.get("concept_title") or "").strip()
    body_text = (spec.get("body_overlay") or "").strip()
    cta_text  = (spec.get("cta_text") or "").strip()

    h_lines = _wrap_text(headline, draw, font_h1, text_w)
    text_top = int(h * 0.05)
    after_h = _draw_text_block(draw, h_lines, font_h1, margin, text_top,
                                fill=(255, 255, 255), shadow_offset=2)

    if body_text:
        b_lines = _wrap_text(body_text, draw, font_body, text_w)
        after_b = _draw_text_block(draw, b_lines, font_body, margin,
                                    after_h + int(h * 0.012),
                                    fill=(220, 220, 235), shadow_offset=1)
    else:
        after_b = after_h

    if cta_text:
        cta_y = min(after_b + int(h * 0.015), panel_h - int(font_cta.size * 2.8 + 36 * 2 + 8))
        _draw_cta_pill(canvas, cta_text, font_cta, panel_w // 2, max(cta_y, after_b + 16))

    return canvas


def _layout_poster_text_heavy(canvas: Any, spec: dict) -> Any:
    """Full-frame dark scrim, large centred headline+body+CTA (direct response)."""
    from PIL import Image, ImageDraw

    w, h = canvas.size
    margin  = int(w * 0.07)
    text_w  = w - 2 * margin

    font_h1   = _load_font(max(56, int(w * 0.072)), bold=True)
    font_body = _load_font(max(30, int(w * 0.032)), bold=False)
    font_cta  = _load_font(max(30, int(w * 0.032)), bold=True)

    if canvas.mode != "RGBA":
        canvas = canvas.convert("RGBA")

    # Base scrim
    scrim = Image.new("RGBA", (w, h), (0, 0, 0, 145))
    canvas = Image.alpha_composite(canvas, scrim)

    # Additional top + bottom edge darkening
    edge_h = int(h * 0.25)
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    overlay.paste(
        _make_vertical_gradient(w, edge_h, "top",    start_alpha=0, end_alpha=120), (0, 0))
    overlay.paste(
        _make_vertical_gradient(w, edge_h, "bottom", start_alpha=0, end_alpha=120),
        (0, h - edge_h))
    canvas = Image.alpha_composite(canvas, overlay)

    draw = ImageDraw.Draw(canvas)
    headline  = (spec.get("headline_overlay") or spec.get("concept_title") or "").strip()
    body_text = (spec.get("body_overlay") or "").strip()
    cta_text  = (spec.get("cta_text") or "").strip()

    h_lines  = _wrap_text(headline,  draw, font_h1,   text_w)
    b_lines  = _wrap_text(body_text, draw, font_body,  text_w) if body_text else []
    h_step   = _line_step(h_lines, draw, font_h1)
    b_step   = _line_step(b_lines, draw, font_body)   if b_lines else 0
    cta_h    = int(font_cta.size + 36 * 2 + 4)       if cta_text else 0

    block_h = (h_step * len(h_lines)
               + (int(h * 0.022) + b_step * len(b_lines) if b_lines else 0)
               + (int(h * 0.028) + cta_h if cta_text else 0))
    text_top = max(int(h * 0.25), (h - block_h) // 2)

    after_h = _draw_text_block(draw, h_lines, font_h1, margin, text_top,
                                fill=(255, 255, 255), align="center",
                                max_width=text_w, shadow_offset=3)
    if b_lines:
        after_b = _draw_text_block(draw, b_lines, font_body, margin,
                                    after_h + int(h * 0.022),
                                    fill=(210, 210, 230), align="center",
                                    max_width=text_w, shadow_offset=2)
    else:
        after_b = after_h

    if cta_text:
        _draw_cta_pill(canvas, cta_text, font_cta, w // 2,
                       after_b + int(h * 0.028), pad_x=50, pad_y=22)

    return canvas


# ─── Layout dispatch ──────────────────────────────────────────────────────────

_LAYOUT_FN: dict[str, Any] = {
    "bottom_panel":      _layout_bottom_panel,
    "top_center_hero":   _layout_top_center_hero,
    "top_left_copy":     _layout_top_left_copy,
    "poster_text_heavy": _layout_poster_text_heavy,
}


# ─── Image helpers ────────────────────────────────────────────────────────────

def _cover_resize(img: Any, target_w: int, target_h: int) -> Any:
    """Scale then centre-crop to fill target dimensions (CSS cover fit)."""
    from PIL import Image

    iw, ih = img.size
    scale  = max(target_w / iw, target_h / ih)
    new_w  = int(iw * scale)
    new_h  = int(ih * scale)
    img    = img.resize((new_w, new_h), Image.LANCZOS)
    left   = (new_w - target_w) // 2
    top    = (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


def _load_background(source: str) -> Any:
    """Load background from a local path, https:// URL, or file:// URI."""
    from PIL import Image

    if source.startswith(("https://", "http://")):
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.close()
        try:
            urllib.request.urlretrieve(source, tmp.name)  # noqa: S310
            img = Image.open(tmp.name).copy()
        finally:
            Path(tmp.name).unlink(missing_ok=True)
        return img
    if source.startswith("file://"):
        from urllib.request import url2pathname
        return Image.open(url2pathname(source[7:])).copy()
    return Image.open(source).copy()


# ─── Public API ───────────────────────────────────────────────────────────────

def composite_ad(
    background_source: str,
    spec: dict,
    output_path: str | None = None,
    layout: str | None = None,
) -> str:
    """
    Composite text overlays onto *background_source* to produce the final ad.

    Parameters
    ----------
    background_source : Local file path, https:// URL, or file:// URI.
                        mock:// paths are skipped (returns a stub path).
    spec              : RenderSpec dict — headline_overlay, body_overlay,
                        cta_text, variant_label, shot_type, aspect_ratio.
    output_path       : Where to write the composited PNG.  Auto-derived if None.
    layout            : Override layout template name.  None → auto_select_layout(spec).

    Returns
    -------
    str  Absolute path to the composited PNG (or stub path for mock backgrounds).

    Raises
    ------
    RuntimeError  If background loading or PIL compositing fails.
    """
    from PIL import Image

    # ── Resolve output path ────────────────────────────────────────────────
    if output_path is None:
        if background_source.startswith(("https://", "http://", "file://", "mock://")):
            slug = hashlib.md5(background_source.encode()).hexdigest()[:12]
            tmp_dir = Path(tempfile.gettempdir()) / "ci_renders"
            tmp_dir.mkdir(exist_ok=True)
            output_path = str(tmp_dir / f"final_{slug}.png")
        else:
            src = Path(background_source)
            output_path = str(src.parent / f"{src.stem}_final.png")

    # ── Guard: mock backgrounds can't be composited ────────────────────────
    if background_source.startswith("mock:"):
        log.info("Skipping composite for mock background %s", background_source)
        # Return a predictable stub path that callers can detect
        stub = str(Path(output_path).with_suffix("")) + "_mock.png"
        return stub

    # ── Load background ────────────────────────────────────────────────────
    try:
        bg = _load_background(background_source)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to load background from {background_source!r}: {exc}"
        ) from exc

    # ── Resize to canonical canvas ─────────────────────────────────────────
    aspect_ratio = spec.get("aspect_ratio") or "9:16"
    target_w, target_h = _CANVAS_SIZES.get(aspect_ratio, _DEFAULT_CANVAS)
    bg = _cover_resize(bg, target_w, target_h).convert("RGBA")

    # ── Select and apply layout ────────────────────────────────────────────
    resolved_layout = layout or auto_select_layout(spec)
    layout_fn = _LAYOUT_FN.get(resolved_layout, _layout_bottom_panel)
    log.info(
        "Compositing variant=%s layout=%s size=%dx%d → %s",
        spec.get("variant_label"), resolved_layout, target_w, target_h, output_path,
    )

    try:
        result = layout_fn(bg, spec)
    except Exception as exc:
        log.error("Layout %r failed: %s", resolved_layout, exc, exc_info=True)
        raise RuntimeError(f"Compositing layout {resolved_layout!r} failed: {exc}") from exc

    # ── Save ───────────────────────────────────────────────────────────────
    final = result.convert("RGB") if result.mode == "RGBA" else result
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    final.save(output_path, format="PNG", optimize=False)
    log.info("Saved composited ad → %s", output_path)
    return output_path
