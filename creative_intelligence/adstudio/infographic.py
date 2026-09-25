"""
Box-size infographics for fruit product pages — drawn with PIL, never AI.

Two layouts, matching the existing shop.goodhillfarms.com artwork:

  render_pdp()  "BOX SIZE / <FRUIT>" — thumbnail + navy table of N variants
                (SIZE / WEIGHT / PIECES), footnote. Square, default 2048px.
  render_bab()  "FRUIT INFO / <FRUIT>" — one sample variant (BOX / WEIGHT /
                PIECES) + shipping & guarantee badges. Square, default 1080px.

Rows are plain dicts: {"size": "SAMPLE", "weight": "2 LBS", "pieces": "4–6"}.
`estimate` picks which column carries the asterisk: "pieces" (count varies,
the default) or "weight" (weight varies — e.g. cacao, sold by piece count).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

FONT_DIR = Path(__file__).resolve().parent / "fonts"
NAVY = (46, 77, 107)        # panel
NAVY_TEXT = (39, 72, 101)   # headline text on white
CREAM = (255, 249, 236)
WHITE = (255, 255, 255)


def _f(name: str, size: int) -> ImageFont.FreeTypeFont:
    size = max(6, int(size))
    if name == "montserrat":
        f = ImageFont.truetype(str(FONT_DIR / "Montserrat.ttf"), size)
        try:
            f.set_variation_by_name("Regular")
        except Exception:
            pass
        return f
    return ImageFont.truetype(str(FONT_DIR / f"Poppins-{name}.ttf"), size)


def _tracked_width(draw, text: str, font, tracking: float) -> float:
    if not text:
        return 0
    return sum(draw.textlength(c, font=font) for c in text) + tracking * (len(text) - 1)


def _draw_tracked(draw, xy, text: str, font, fill, tracking: float) -> None:
    x, y = xy
    for c in text:
        draw.text((x, y), c, font=font, fill=fill)
        x += draw.textlength(c, font=font) + tracking


def _center(draw, cx: float, cy: float, text: str, font, fill) -> None:
    draw.text((cx, cy), text, font=font, fill=fill, anchor="mm")


def _fit(draw, text: str, name: str, size: int, max_w: float,
         tracking_em: float = 0.0, min_size: int = 10) -> ImageFont.FreeTypeFont:
    """Largest font <= size whose (tracked) width fits max_w."""
    while size > min_size:
        f = _f(name, size)
        if _tracked_width(draw, text, f, size * tracking_em) <= max_w:
            return f
        size -= 2
    return _f(name, min_size)


def _split_name(text: str) -> list[str]:
    """Split a long fruit name onto two balanced lines."""
    words = text.split()
    if len(words) < 2:
        return [text]
    best, best_diff = [text], 1e9
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        diff = abs(len(a) - len(b))
        if diff < best_diff:
            best, best_diff = [a, b], diff
    return best


def _square_thumb(path: str | None, side: int) -> Image.Image | None:
    if not path:
        return None
    try:
        im = Image.open(path).convert("RGB")
    except Exception:
        return None
    return ImageOps.fit(im, (side, side), Image.LANCZOS, centering=(0.5, 0.45))


def _headers(estimate: str, first: str) -> list[str]:
    return [first, "WEIGHT" + ("*" if estimate == "weight" else ""),
            "PIECES" + ("*" if estimate != "weight" else "")]


def _footnote(estimate: str) -> str:
    return ("*Estimate, weight varies by box" if estimate == "weight"
            else "*Estimate, count varies by box")


def _name_block(draw, lines_text: str, x0: float, x1: float, y_top: float,
                size: int, weight: str, tracking_em: float, fill,
                align: str = "center", max_lines: int = 2,
                max_h: float | None = None) -> float:
    """Draw the tracked fruit name (1–2 lines) inside [x0,x1]; return bottom y."""
    text = lines_text.upper().strip()
    max_w = x1 - x0
    f = _f(weight, size)
    lines = [text]
    if _tracked_width(draw, text, f, size * tracking_em) > max_w and max_lines > 1:
        lines = _split_name(text)
    widest = max(lines, key=lambda s: _tracked_width(draw, s, f, size * tracking_em))
    if max_h:
        size = min(size, int(max_h / (1.12 * len(lines))))
    f = _fit(draw, widest, weight, size, max_w, tracking_em, min_size=int(size * 0.45))
    fs = f.size
    y = y_top
    for ln in lines:
        w = _tracked_width(draw, ln, f, fs * tracking_em)
        x = x0 + (max_w - w) / 2 if align == "center" else x0
        _draw_tracked(draw, (x, y), ln, f, fill, fs * tracking_em)
        y += fs * 1.12
    return y


# ─────────────────────────────────────────────────────────────────────────────
# PDP — "BOX SIZE"
# ─────────────────────────────────────────────────────────────────────────────
def render_pdp(fruit_name: str, rows: list[dict[str, Any]], out_path: str, *,
               thumb_path: str | None = None, estimate: str = "pieces",
               size: int = 2048) -> str:
    rows = [r for r in rows if any((r.get(k) or "").strip() for k in ("size", "weight", "pieces"))]
    if not rows:
        raise ValueError("at least one variant row is required")
    S = size / 2048.0
    img = Image.new("RGB", (size, size), WHITE)
    d = ImageDraw.Draw(img)

    # thumbnail
    tside = int(502 * S)
    tx, ty = int(146 * S), int(57 * S)
    th = _square_thumb(thumb_path, tside)
    if th is not None:
        img.paste(th, (tx, ty))
    title_x0 = (tx + tside + 90 * S) if th is not None else 120 * S
    title_x1 = size - 110 * S

    # headline
    f_box = _fit(d, "BOX SIZE", "Bold", int(215 * S), title_x1 - title_x0)
    d.text(((title_x0 + title_x1) / 2, 225 * S), "BOX SIZE", font=f_box,
           fill=NAVY_TEXT, anchor="mm")
    _name_block(d, fruit_name, title_x0, title_x1, 350 * S, int(150 * S),
                "Regular", 0.12, NAVY_TEXT, max_h=240 * S)

    # panel
    px0, py0, px1, py1 = 95 * S, 620 * S, size - 95 * S, 1832 * S
    d.rounded_rectangle([px0, py0, px1, py1], radius=int(115 * S), fill=NAVY)
    cols = [430 * S, 940 * S, 1510 * S]

    f_head = _f("Medium", int(96 * S))
    for cx, h in zip(cols, _headers(estimate, "SIZE")):
        _center(d, cx, 795 * S, h, f_head, CREAM)
    d.line([(218 * S, 870 * S), (size - 218 * S, 870 * S)], fill=CREAM, width=max(2, int(7 * S)))

    # rows: fixed pitch (matches the 4-row art), block centred in the free space
    n = len(rows)
    top, bottom = 870 * S, 1640 * S
    pitch = min(185 * S, (bottom - top) / max(n, 1))
    fsize = int(min(100 * S, pitch * 0.55))
    f_row = _f("Medium", fsize)
    y0 = top + ((bottom - top) - pitch * n) / 2 + pitch / 2
    col_w = [520 * S, 480 * S, 520 * S]
    for i, r in enumerate(rows):
        cy = y0 + i * pitch
        for cx, w, key in zip(cols, col_w, ("size", "weight", "pieces")):
            txt = (r.get(key) or "").strip().upper()
            f = f_row if d.textlength(txt, font=f_row) <= w else _fit(d, txt, "Medium", fsize, w)
            _center(d, cx, cy, txt, f, CREAM)

    _center(d, size / 2, 1728 * S, _footnote(estimate), _f("montserrat", int(76 * S)), CREAM)

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    return out_path


# ─────────────────────────────────────────────────────────────────────────────
# BaB — "FRUIT INFO" (one sample variant + badges)
# ─────────────────────────────────────────────────────────────────────────────
def _truck(d, x: float, y: float, s: float, col, w: int) -> None:
    """Simple line-art delivery truck with speed lines, box (x,y) size ~ s."""
    d.rounded_rectangle([x + .22 * s, y + .22 * s, x + .70 * s, y + .66 * s], radius=int(.04 * s), outline=col, width=w)
    d.line([(x + .70 * s, y + .34 * s), (x + .86 * s, y + .34 * s), (x + .98 * s, y + .50 * s),
            (x + .98 * s, y + .66 * s), (x + .70 * s, y + .66 * s)], fill=col, width=w, joint="curve")
    for cx in (.36, .84):
        r = .08 * s
        d.ellipse([x + cx * s - r, y + .70 * s - r, x + cx * s + r, y + .70 * s + r], fill=NAVY, outline=col, width=w)
    for yy, x0 in ((.34, .02), (.46, .0), (.58, .06)):
        d.line([(x + x0 * s, y + yy * s), (x + .16 * s, y + yy * s)], fill=col, width=w)


def _shield(d, x: float, y: float, s: float, col, w: int) -> None:
    pts = [(x + .5 * s, y + .08 * s), (x + .88 * s, y + .22 * s), (x + .84 * s, y + .58 * s),
           (x + .5 * s, y + .92 * s), (x + .16 * s, y + .58 * s), (x + .12 * s, y + .22 * s)]
    d.line(pts + [pts[0]], fill=col, width=w, joint="curve")
    d.line([(x + .33 * s, y + .50 * s), (x + .46 * s, y + .63 * s), (x + .68 * s, y + .38 * s)],
           fill=col, width=w, joint="curve")


def render_bab(fruit_name: str, row: dict[str, Any], out_path: str, *,
               thumb_path: str | None = None, estimate: str = "pieces",
               size: int = 1080) -> str:
    S = size / 360.0
    img = Image.new("RGB", (size, size), WHITE)
    d = ImageDraw.Draw(img)
    m = 22 * S

    tside = int(90 * S)
    th = _square_thumb(thumb_path, tside)
    if th is not None:
        mask = Image.new("L", (tside, tside), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, tside, tside], radius=int(4 * S), fill=255)
        img.paste(th, (int(m), int(m)), mask)
    tx0 = m + tside + 13 * S if th is not None else m
    tx1 = size - m
    f_t = _fit(d, "FRUIT INFO", "Bold", int(37 * S), tx1 - tx0)
    d.text((tx0, 30 * S), "FRUIT INFO", font=f_t, fill=NAVY_TEXT)
    _name_block(d, fruit_name, tx0, tx1, 76 * S, int(13 * S), "Medium", 0.06,
                NAVY_TEXT, align="left", max_h=40 * S)

    # table panel
    d.rounded_rectangle([m, 130 * S, size - m, 250 * S], radius=int(12 * S), fill=NAVY)
    cols = [95 * S, 180 * S, 268 * S]
    f_h = _f("Medium", int(15 * S))
    for cx, h in zip(cols, _headers(estimate, "BOX")):
        _center(d, cx, 158 * S, h, f_h, CREAM)
    d.line([(55 * S, 174 * S), (size - 55 * S, 174 * S)], fill=CREAM, width=max(1, int(1.2 * S)))
    f_r = _f("Medium", int(16 * S))
    for cx, key in zip(cols, ("size", "weight", "pieces")):
        txt = (row.get(key) or "").strip().upper()
        f = f_r if d.textlength(txt, font=f_r) <= 80 * S else _fit(d, txt, "Medium", int(16 * S), 80 * S)
        _center(d, cx, 196 * S, txt, f, CREAM)
    _center(d, size / 2, 228 * S, _footnote(estimate), _f("montserrat", int(11 * S)), CREAM)

    # badges
    bw = (size - 2 * m - 8 * S) / 2
    lw = max(2, int(1.6 * S))
    for i, (icon, l1, l2) in enumerate(((_truck, "FAST 1-2 DAY", "INSURED SHIPPING"),
                                         (_shield, "MONEY BACK", "100% GUARANTEE"))):
        bx = m + i * (bw + 8 * S)
        d.rounded_rectangle([bx, 258 * S, bx + bw, 338 * S], radius=int(10 * S), fill=NAVY)
        icon(d, bx + 14 * S, 280 * S, 36 * S, CREAM, lw)
        f_b = _fit(d, l2, "Medium", int(11 * S), bw - 62 * S)
        d.text((bx + 58 * S, 298 * S), l1, font=f_b, fill=CREAM, anchor="ls")
        d.text((bx + 58 * S, 314 * S), l2, font=f_b, fill=CREAM, anchor="ls")

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    return out_path
