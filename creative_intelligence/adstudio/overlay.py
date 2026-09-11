"""
Programmatic text overlay for Ad Studio v2.

Draws headline / subhead / CTA onto a finished text-free image with PIL — crisp,
correctly spelled, editable, free. One image can carry any number of different
copy treatments without regenerating.

layout options (all optional):
  position : "top" | "bottom" | "center"   (default "top")
  color    : "auto" | "light" | "dark"     (default "auto" — picks per region luminance)
  scrim    : bool                          (default True — soft gradient for legibility)
  cta      : str                           (draws a pill button if present)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageFilter

# Font candidates (macOS first, then Linux/nix for the Railway image).
_BOLD_FONTS = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Helvetica.ttc",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/Library/Fonts/Arial Bold.ttf",
    "/nix/store/../share/fonts/truetype/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
_REG_FONTS = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Helvetica.ttc",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _font(paths: list[str], size: int) -> ImageFont.FreeTypeFont:
    for p in paths:
        try:
            if Path(p).exists():
                return ImageFont.truetype(p, size)
        except Exception:
            continue
    # search common dirs as a last resort
    for d in ("/System/Library/Fonts/Supplemental", "/System/Library/Fonts",
              "/Library/Fonts", "/usr/share/fonts"):
        try:
            for f in Path(d).rglob("*.tt?"):
                try:
                    return ImageFont.truetype(str(f), size)
                except Exception:
                    continue
        except Exception:
            continue
    return ImageFont.load_default()


def _wrap(draw, text: str, font, max_w: float) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _region_is_dark(img: Image.Image, box) -> bool:
    crop = img.crop(box).convert("L").resize((32, 32))
    px = list(crop.getdata())
    return (sum(px) / len(px)) < 128


def compose(image_path: str, out_path: str, *, headline: str = "",
            subhead: str = "", cta: str = "", layout: dict[str, Any] | None = None) -> str:
    layout = layout or {}
    position = layout.get("position", "top")
    color_mode = layout.get("color", "auto")
    scrim = layout.get("scrim", True)

    img = Image.open(image_path).convert("RGB")
    W, H = img.size
    margin = int(W * 0.07)
    max_w = W - 2 * margin

    hl_size = max(28, int(W / 12))
    sub_size = max(16, int(W / 26))
    cta_size = max(15, int(W / 30))
    f_hl = _font(_BOLD_FONTS, hl_size)
    f_sub = _font(_REG_FONTS, sub_size)
    f_cta = _font(_BOLD_FONTS, cta_size)

    draw = ImageDraw.Draw(img)
    hl_lines = _wrap(draw, headline, f_hl, max_w) if headline else []
    sub_lines = _wrap(draw, subhead, f_sub, max_w) if subhead else []

    line_gap = int(hl_size * 0.16)
    hl_h = sum(hl_size + line_gap for _ in hl_lines)
    sub_h = sum(sub_size + int(sub_size * 0.2) for _ in sub_lines)
    cta_h = int(cta_size * 2.4) if cta else 0
    block_h = hl_h + (int(hl_size * 0.4) if sub_lines else 0) + sub_h + \
        (int(cta_size * 1.2) + cta_h if cta else 0)

    if position == "bottom":
        y = H - margin - block_h
        box = (0, y - margin, W, H)
    elif position == "center":
        y = (H - block_h) // 2
        box = (0, y - margin, W, y + block_h + margin)
    else:  # top
        y = margin
        box = (0, 0, W, y + block_h + margin)

    dark = _region_is_dark(img, box) if color_mode == "auto" else (color_mode == "light")
    text_col = (255, 255, 255) if dark else (20, 20, 22)

    # soft scrim behind the text zone for guaranteed legibility
    if scrim:
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        scrim_col = (0, 0, 0, 90) if dark else (255, 255, 255, 90)
        pad = int(margin * 0.6)
        od.rectangle([margin - pad, box[1], W - margin + pad, box[3]], fill=scrim_col)
        overlay = overlay.filter(ImageFilter.GaussianBlur(pad))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(img)

    cy = y
    for ln in hl_lines:
        w = draw.textlength(ln, font=f_hl)
        draw.text(((W - w) / 2, cy), ln, font=f_hl, fill=text_col)
        cy += hl_size + line_gap
    if sub_lines:
        cy += int(hl_size * 0.4)
        for ln in sub_lines:
            w = draw.textlength(ln, font=f_sub)
            draw.text(((W - w) / 2, cy), ln, font=f_sub, fill=text_col)
            cy += sub_size + int(sub_size * 0.2)
    if cta:
        cy += int(cta_size * 1.2)
        tw = draw.textlength(cta, font=f_cta)
        pad_x, pad_y = int(cta_size * 0.9), int(cta_size * 0.55)
        bw, bh = tw + 2 * pad_x, cta_size + 2 * pad_y
        bx = (W - bw) / 2
        btn_col = (255, 255, 255) if dark else (20, 20, 22)
        btn_txt = (20, 20, 22) if dark else (255, 255, 255)
        draw.rounded_rectangle([bx, cy, bx + bw, cy + bh], radius=int(bh / 2), fill=btn_col)
        draw.text((bx + pad_x, cy + pad_y), cta, font=f_cta, fill=btn_txt)

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    return out_path
