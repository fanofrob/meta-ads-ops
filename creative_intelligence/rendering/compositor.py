"""
PIL-based text compositing for the static rendering layer.

Composites headline, body copy, and CTA text onto a background image using
one of four layout templates to produce the final ad image.

Public API
----------
composite_ad(background_source, spec, output_path=None, layout=None) → str
auto_select_layout(spec) → str
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

LAYOUT_TEMPLATES = ("bottom_panel", "top_left_copy", "top_center_hero", "poster_text_heavy")

_VARIANT_LAYOUT: dict[str, str] = {
    "minimal":         "top_center_hero",
    "premium":         "bottom_panel",
    "direct_response": "poster_text_heavy",
    "reveal":          "top_left_copy",
    "product_hero":    "bottom_panel",
}

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
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/run/current-system/sw/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/NotoSans-Bold.ttf",
]
_FONT_PATHS_REGULAR: list[str] = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/run/current-system/sw/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/NotoSans-Regular.ttf",
]

_font_path_cache: dict[str, str | None] = {}


def _find_font_path(paths: list[str]) -> str | None:
    key = paths[0]
    if key not in _font_path_cache:
        found = None
        for p in paths:
            if Path(p).exists():
                found = p
                break
        _font_path_cache[key] = found
        if found:
            log.info("Compositor using font: %s", found)
        else:
            log.warning("No TTF font found, falling back to PIL default")
    return _font_path_cache[key]


def _load_font(size: int, bold: bool = False) -> Any:
    from PIL import ImageFont
    paths = _FONT_PATHS_BOLD if bold else _FONT_PATHS_REGULAR
    path = _find_font_path(paths)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception as e:
            log.warning("Failed to load %s at size %d: %s", path, size, e)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        pass
    return ImageFont.load_default()


# ─── Layout selector ───────────────────────────────────────────────────────────

def auto_select_layout(spec: dict) -> str:
    shot = (spec.get("shot_type") or "").lower().strip()
    override = _SHOT_LAYOUT_OVERRIDE.get(shot)
    if override:
        return override
    variant = (spec.get("variant_label") or "premium").lower()
    return _VARIANT_LAYOUT.get(variant, "bottom_panel")


# ─── Drawing primitives ────────────────────────────────────────────────────────

def _make_vertical_gradient(
    w: int, h: int,
    direction: str = "bottom",
    start_alpha: int = 0,
    end_alpha: int = 240,
) -> Any:
    from PIL import Image
    strip = Image.new("RGBA", (1, h), (0, 0, 0, 0))
    for y in range(h):
        if direction == "bottom":
            t = y / max(h - 1, 1)
        elif direction == "top":
            t = 1.0 - y / max(h - 1, 1)
        else:
            t = 1.0 - abs(2.0 * y / max(h - 1, 1) - 1.0)
        alpha = int(start_alpha + (end_alpha - start_alpha) * t)
        strip.putpixel((0, y), (0, 0, 0, max(0, min(255, alpha))))
    return strip.resize((w, h), Image.NEAREST)


def _wrap_text(text: str, draw: Any, font: Any, max_width: int) -> list[str]:
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


def _line_height(font: Any, draw: Any, spacing: float = 1.25) -> int:
    bbox = draw.textbbox((0, 0), "Ag", font=font)
    return int((bbox[3] - bbox[1]) * spacing)


def _draw_text_with_shadow(
    draw: Any,
    text: str,
    font: Any,
    x: int,
    y: int,
    fill: tuple = (255, 255, 255),
    shadow_offset: int = 3,
    shadow_alpha: int = 200,
) -> None:
    """Draw text with a multi-layer shadow for strong readability."""
    # Outer shadow (larger offset, more transparent)
    draw.text((x + shadow_offset + 1, y + shadow_offset + 1), text,
              font=font, fill=(0, 0, 0, shadow_alpha - 40))
    # Inner shadow
    draw.text((x + shadow_offset, y + shadow_offset), text,
              font=font, fill=(0, 0, 0, shadow_alpha))
    # Main text
    draw.text((x, y), text, font=font, fill=fill)


def _draw_text_block(
    draw: Any,
    lines: list[str],
    font: Any,
    x: int,
    y: int,
    fill: tuple = (255, 255, 255),
    shadow_offset: int = 3,
    align: str = "left",
    max_width: int = 0,
    line_spacing: float = 1.25,
) -> int:
    lh = _line_height(font, draw, line_spacing)
    cur_y = y
    for line in lines:
        lbbox = draw.textbbox((0, 0), line, font=font)
        lw = lbbox[2] - lbbox[0]
        lx = x + (max_width - lw) // 2 if (align == "center" and max_width) else x
        _draw_text_with_shadow(draw, line, font, lx, cur_y, fill=fill,
                               shadow_offset=shadow_offset)
        cur_y += lh
    return cur_y


def _draw_cta_pill(
    canvas: Any,
    text: str,
    font: Any,
    center_x: int,
    top_y: int,
    pad_x: int = 54,
    pad_y: int = 24,
    bg_fill: tuple = (79, 70, 229),
    text_fill: tuple = (255, 255, 255),
) -> int:
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
# Font sizing guide for 1080-wide canvas:
#   Headline  : ~96px  (readable at any display size)
#   Body      : ~52px
#   CTA       : ~46px
# Use proportional sizing so other aspect ratios scale correctly.

def _layout_bottom_panel(canvas: Any, spec: dict) -> Any:
    """
    Strong dark gradient over the bottom 50 % of the image.
    Headline large + bold, body copy below, prominent CTA pill.
    Best for: premium, product hero.
    """
    from PIL import Image, ImageDraw

    w, h = canvas.size
    margin = int(w * 0.07)
    text_w = w - 2 * margin

    # Large, readable font sizes
    font_h1   = _load_font(max(72, int(w * 0.088)), bold=True)
    font_body = _load_font(max(44, int(w * 0.048)), bold=False)
    font_cta  = _load_font(max(40, int(w * 0.044)), bold=True)

    if canvas.mode != "RGBA":
        canvas = canvas.convert("RGBA")

    # Strong gradient starting at 48 % — gives plenty of dark space for text
    panel_start = int(h * 0.48)
    panel_h = h - panel_start
    grad = _make_vertical_gradient(w, panel_h, direction="bottom",
                                   start_alpha=0, end_alpha=255)
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    overlay.paste(grad, (0, panel_start))
    # Extra solid footer strip for guaranteed CTA contrast
    footer_h = int(h * 0.12)
    footer = Image.new("RGBA", (w, footer_h), (0, 0, 0, 230))
    overlay.paste(footer, (0, h - footer_h))
    canvas = Image.alpha_composite(canvas, overlay)

    draw = ImageDraw.Draw(canvas)
    headline  = (spec.get("headline_overlay") or spec.get("concept_title") or "").strip()
    body_text = (spec.get("body_overlay") or "").strip()
    cta_text  = (spec.get("cta_text") or "").strip()

    h_lines = _wrap_text(headline, draw, font_h1, text_w)
    b_lines = _wrap_text(body_text, draw, font_body, text_w) if body_text else []

    # Position: headline starts just inside the gradient zone
    text_top = int(h * 0.52)
    after_h = _draw_text_block(draw, h_lines, font_h1, margin, text_top,
                               fill=(255, 255, 255), shadow_offset=4)
    if b_lines:
        after_b = _draw_text_block(draw, b_lines, font_body, margin,
                                   after_h + int(h * 0.018),
                                   fill=(230, 230, 245), shadow_offset=3)
    else:
        after_b = after_h

    if cta_text:
        cta_y = max(after_b + int(h * 0.022), int(h * 0.82))
        # Keep CTA inside footer strip
        cta_y = min(cta_y, h - footer_h + int(h * 0.01))
        _draw_cta_pill(canvas, cta_text, font_cta, w // 2, cta_y)

    return canvas


def _layout_top_center_hero(canvas: Any, spec: dict) -> Any:
    """
    Strong top gradient with large centred headline.
    CTA pill at the bottom with its own dark band.
    Best for: minimal, overhead shots.
    """
    from PIL import Image, ImageDraw

    w, h = canvas.size
    margin = int(w * 0.07)
    text_w = w - 2 * margin

    font_h1   = _load_font(max(72, int(w * 0.088)), bold=True)
    font_body = _load_font(max(44, int(w * 0.048)), bold=False)
    font_cta  = _load_font(max(40, int(w * 0.044)), bold=True)

    if canvas.mode != "RGBA":
        canvas = canvas.convert("RGBA")

    # Top dark band (38 % of height)
    top_h = int(h * 0.38)
    top_grad = _make_vertical_gradient(w, top_h, direction="top",
                                       start_alpha=0, end_alpha=240)
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    overlay.paste(top_grad, (0, 0))

    # Bottom CTA band (18 % of height)
    bot_h = int(h * 0.18)
    bot_grad = _make_vertical_gradient(w, bot_h, direction="bottom",
                                       start_alpha=20, end_alpha=230)
    overlay.paste(bot_grad, (0, h - bot_h))
    canvas = Image.alpha_composite(canvas, overlay)

    draw = ImageDraw.Draw(canvas)
    headline  = (spec.get("headline_overlay") or spec.get("concept_title") or "").strip()
    body_text = (spec.get("body_overlay") or "").strip()
    cta_text  = (spec.get("cta_text") or "").strip()

    h_lines = _wrap_text(headline, draw, font_h1, text_w)
    text_top = int(h * 0.045)
    after_h = _draw_text_block(draw, h_lines, font_h1, margin, text_top,
                               fill=(255, 255, 255), align="center",
                               max_width=text_w, shadow_offset=4)
    if body_text:
        b_lines = _wrap_text(body_text, draw, font_body, text_w)
        _draw_text_block(draw, b_lines, font_body, margin,
                         after_h + int(h * 0.018),
                         fill=(230, 230, 245), align="center",
                         max_width=text_w, shadow_offset=3)

    if cta_text:
        _draw_cta_pill(canvas, cta_text, font_cta, w // 2, int(h * 0.836))

    return canvas


def _layout_top_left_copy(canvas: Any, spec: dict) -> Any:
    """
    Semi-transparent dark panel occupying the top-left third.
    Product visible in the right / lower portion.
    Best for: reveal, side-angle shots.
    """
    from PIL import Image, ImageDraw

    w, h = canvas.size
    panel_w = int(w * 0.62)
    panel_h = int(h * 0.46)
    margin  = int(w * 0.06)
    text_w  = panel_w - 2 * margin

    font_h1   = _load_font(max(64, int(w * 0.078)), bold=True)
    font_body = _load_font(max(40, int(w * 0.044)), bold=False)
    font_cta  = _load_font(max(38, int(w * 0.042)), bold=True)

    if canvas.mode != "RGBA":
        canvas = canvas.convert("RGBA")

    # Solid panel with slight gradient at the right edge
    panel = Image.new("RGBA", (panel_w, panel_h), (0, 0, 0, 200))
    # Soft right edge
    edge_w = int(panel_w * 0.15)
    for x in range(edge_w):
        alpha = int(200 * (1 - x / edge_w))
        for y in range(panel_h):
            panel.putpixel((panel_w - edge_w + x, y), (0, 0, 0, alpha))
    canvas.paste(panel, (0, 0), panel)

    draw = ImageDraw.Draw(canvas)
    headline  = (spec.get("headline_overlay") or spec.get("concept_title") or "").strip()
    body_text = (spec.get("body_overlay") or "").strip()
    cta_text  = (spec.get("cta_text") or "").strip()

    h_lines = _wrap_text(headline, draw, font_h1, text_w)
    text_top = int(h * 0.045)
    after_h = _draw_text_block(draw, h_lines, font_h1, margin, text_top,
                               fill=(255, 255, 255), shadow_offset=3)

    if body_text:
        b_lines = _wrap_text(body_text, draw, font_body, text_w)
        after_b = _draw_text_block(draw, b_lines, font_body, margin,
                                   after_h + int(h * 0.018),
                                   fill=(230, 230, 245), shadow_offset=2)
    else:
        after_b = after_h

    if cta_text:
        pill_h_approx = int(font_cta.size * 1.0 + 48)
        cta_y = min(after_b + int(h * 0.022),
                    panel_h - pill_h_approx - int(h * 0.01))
        cta_y = max(cta_y, after_b + int(h * 0.015))
        _draw_cta_pill(canvas, cta_text, font_cta, panel_w // 2, cta_y,
                       pad_x=44, pad_y=20)

    return canvas


def _layout_poster_text_heavy(canvas: Any, spec: dict) -> Any:
    """
    Full-frame dark scrim. Very large centred headline, body, prominent CTA.
    Maximum text impact — direct response.
    """
    from PIL import Image, ImageDraw

    w, h = canvas.size
    margin = int(w * 0.07)
    text_w = w - 2 * margin

    font_h1   = _load_font(max(84, int(w * 0.100)), bold=True)
    font_body = _load_font(max(48, int(w * 0.052)), bold=False)
    font_cta  = _load_font(max(44, int(w * 0.048)), bold=True)

    if canvas.mode != "RGBA":
        canvas = canvas.convert("RGBA")

    # Base scrim — dark but still shows the image
    scrim = Image.new("RGBA", (w, h), (0, 0, 0, 160))
    canvas = Image.alpha_composite(canvas, scrim)

    # Vignette: darker at top and bottom edges
    edge_h = int(h * 0.20)
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    overlay.paste(_make_vertical_gradient(w, edge_h, "top", 0, 100), (0, 0))
    overlay.paste(_make_vertical_gradient(w, edge_h, "bottom", 0, 100), (0, h - edge_h))
    canvas = Image.alpha_composite(canvas, overlay)

    draw = ImageDraw.Draw(canvas)
    headline  = (spec.get("headline_overlay") or spec.get("concept_title") or "").strip()
    body_text = (spec.get("body_overlay") or "").strip()
    cta_text  = (spec.get("cta_text") or "").strip()

    h_lines  = _wrap_text(headline, draw, font_h1, text_w)
    b_lines  = _wrap_text(body_text, draw, font_body, text_w) if body_text else []
    lh_h1    = _line_height(font_h1,   draw, 1.2)
    lh_body  = _line_height(font_body, draw, 1.35)
    cta_pill = int(font_cta.size + 48 * 2) if cta_text else 0
    gap      = int(h * 0.025)

    total = (lh_h1 * len(h_lines)
             + (gap + lh_body * len(b_lines) if b_lines else 0)
             + (gap + cta_pill if cta_text else 0))
    text_top = max(int(h * 0.22), (h - total) // 2)

    after_h = _draw_text_block(draw, h_lines, font_h1, margin, text_top,
                               fill=(255, 255, 255), align="center",
                               max_width=text_w, shadow_offset=4,
                               line_spacing=1.2)
    if b_lines:
        after_b = _draw_text_block(draw, b_lines, font_body, margin,
                                   after_h + gap,
                                   fill=(225, 225, 242), align="center",
                                   max_width=text_w, shadow_offset=3,
                                   line_spacing=1.35)
    else:
        after_b = after_h

    if cta_text:
        _draw_cta_pill(canvas, cta_text, font_cta, w // 2,
                       after_b + gap, pad_x=58, pad_y=26)

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
    spec              : RenderSpec dict.
    output_path       : Where to write the PNG. Auto-derived if None.
    layout            : Override layout template. None → auto_select_layout(spec).

    Returns
    -------
    str  Absolute path to the composited PNG.
    """
    from PIL import Image

    if output_path is None:
        if background_source.startswith(("https://", "http://", "file://", "mock://")):
            slug = hashlib.md5(background_source.encode()).hexdigest()[:12]
            tmp_dir = Path(tempfile.gettempdir()) / "ci_renders"
            tmp_dir.mkdir(exist_ok=True)
            output_path = str(tmp_dir / f"final_{slug}.png")
        else:
            src = Path(background_source)
            output_path = str(src.parent / f"{src.stem}_final.png")

    if background_source.startswith("mock:"):
        log.info("Skipping composite for mock background %s", background_source)
        return str(Path(output_path).with_suffix("")) + "_mock.png"

    try:
        bg = _load_background(background_source)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to load background from {background_source!r}: {exc}"
        ) from exc

    aspect_ratio = spec.get("aspect_ratio") or "9:16"
    target_w, target_h = _CANVAS_SIZES.get(aspect_ratio, _DEFAULT_CANVAS)
    bg = _cover_resize(bg, target_w, target_h).convert("RGBA")

    resolved_layout = layout or auto_select_layout(spec)
    layout_fn = _LAYOUT_FN.get(resolved_layout, _layout_bottom_panel)
    log.info("Compositing variant=%s layout=%s %dx%d → %s",
             spec.get("variant_label"), resolved_layout, target_w, target_h, output_path)

    try:
        result = layout_fn(bg, spec)
    except Exception as exc:
        log.error("Layout %r failed: %s", resolved_layout, exc, exc_info=True)
        raise RuntimeError(f"Compositing layout {resolved_layout!r} failed: {exc}") from exc

    final = result.convert("RGB") if result.mode == "RGBA" else result
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    final.save(output_path, format="PNG", optimize=False)
    log.info("Saved final ad → %s", output_path)
    return output_path
