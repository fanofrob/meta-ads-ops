"""
Where generated label images live on disk.

Kept separate from rendering/asset_store.py so the label studio never writes
into the ads pipeline's render_assets table.
"""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path


def get_label_output_dir() -> Path:
    """
    Return (creating if needed) the directory for generated label images.

    Falls back to the package-relative default when CI_RENDER_OUTPUT_DIR is
    unset *or set to an empty string* — an empty env var overrides the config
    default and silently resolves to Path("") == ".", which is what previously
    scattered images into the repo root.
    """
    from creative_intelligence import config

    configured = (config.CI_RENDER_OUTPUT_DIR or "").strip()
    if configured:
        base = Path(configured)
    else:
        base = Path(__file__).resolve().parent.parent.parent / \
            "creative_intelligence_data" / "render_outputs"

    path = base / "labels"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def save_label_image(source: str, sku: str, recipe: str) -> str:
    """
    Persist a generated image locally and return its ABSOLUTE path.

    `source` may be an https:// URL (Replicate CDN), a file:// URI (provider
    wrote raw bytes to temp), or an existing local path. Absolute paths are
    stored so the serving route never depends on the process working dir.
    """
    dest_dir = get_label_output_dir()
    dest = dest_dir / f"{_slug(sku)}_{_slug(recipe)}_{int(time.time())}.png"

    if source.startswith("file://"):
        from urllib.request import url2pathname
        shutil.copy2(Path(url2pathname(source[7:])), dest)
    elif source.startswith(("http://", "https://")):
        from urllib.request import urlretrieve
        urlretrieve(source, dest)  # noqa: S310 — provider-supplied URL
    else:
        shutil.copy2(Path(source), dest)

    if not dest.exists() or dest.stat().st_size < 10_000:
        raise RuntimeError(
            f"Saved label image is missing or too small: {dest} "
            f"({dest.stat().st_size if dest.exists() else 0} bytes)"
        )

    return str(dest.resolve())
