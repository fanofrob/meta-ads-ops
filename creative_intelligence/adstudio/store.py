"""
Local file storage for Ad Studio — uploaded reference photos and generated ads.

Kept separate from every other store so Ad Studio never writes into the ads or
label pipelines.
"""
from __future__ import annotations

import base64
import mimetypes
import re
import shutil
import time
from pathlib import Path


def _base_dir() -> Path:
    """Resolve the Ad Studio data root, robust to an empty CI_RENDER_OUTPUT_DIR."""
    from creative_intelligence import config
    configured = (config.CI_RENDER_OUTPUT_DIR or "").strip()
    if configured:
        base = Path(configured).parent  # sibling of render_outputs
    else:
        base = Path(__file__).resolve().parent.parent.parent / "creative_intelligence_data"
    d = base / "adstudio"
    d.mkdir(parents=True, exist_ok=True)
    return d


def photos_dir() -> Path:
    d = _base_dir() / "photos"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ads_dir() -> Path:
    d = _base_dir() / "ads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def images_dir() -> Path:
    d = _base_dir() / "images"
    d.mkdir(parents=True, exist_ok=True)
    return d


def composites_dir() -> Path:
    d = _base_dir() / "composites"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_generated_image(source: str, product_id: int, image_id: int) -> str:
    """Persist a generated text-free image; return absolute path."""
    dest = images_dir() / f"img{image_id}_p{product_id}_{int(time.time())}.png"
    if source.startswith("file://"):
        from urllib.request import url2pathname
        shutil.copy2(Path(url2pathname(source[7:])), dest)
    elif source.startswith(("http://", "https://")):
        from urllib.request import urlretrieve
        urlretrieve(source, dest)  # noqa: S310
    else:
        shutil.copy2(Path(source), dest)
    if not dest.exists() or dest.stat().st_size < 5_000:
        raise RuntimeError(f"Saved image missing/too small: {dest}")
    return str(dest.resolve())


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40] or "x"


def save_reference_photo(raw: bytes, filename: str, product_id: int) -> str:
    """Persist an uploaded reference photo; return its absolute path."""
    ext = (Path(filename).suffix or ".png").lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        ext = ".png"
    dest = photos_dir() / f"p{product_id}_{int(time.time()*1000)}{ext}"
    dest.write_bytes(raw)
    return str(dest.resolve())


def save_ad_image(source: str, product_id: int, ad_id: int) -> str:
    """Persist a generated ad (http url / file uri / local path); absolute path."""
    dest = ads_dir() / f"ad{ad_id}_p{product_id}_{int(time.time())}.png"
    if source.startswith("file://"):
        from urllib.request import url2pathname
        shutil.copy2(Path(url2pathname(source[7:])), dest)
    elif source.startswith(("http://", "https://")):
        from urllib.request import urlretrieve
        urlretrieve(source, dest)  # noqa: S310
    else:
        shutil.copy2(Path(source), dest)
    if not dest.exists() or dest.stat().st_size < 5_000:
        raise RuntimeError(f"Saved ad image missing/too small: {dest}")
    return str(dest.resolve())


def to_data_uri(path: str, max_dim: int = 1024, quality: int = 85) -> str:
    """
    Downscaled base64 data URI of a reference image for Replicate image_input.

    Full-res phone photos (multi-MB) base64-encoded make the request body huge and
    time out on UPLOAD ("write operation timed out"). ~1024px JPEG is plenty for a
    product reference and keeps the whole payload well under ~200KB per image.
    """
    import io
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB")
        im.thumbnail((max_dim, max_dim))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{b64}"
    except Exception:
        p = Path(path)
        mime = mimetypes.guess_type(p.name)[0] or "image/png"
        return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode("ascii")
