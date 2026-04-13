"""
Asset storage helpers for the static rendering layer.

Handles:
- Local render output directory management
- Saving asset paths/URLs to the render_assets table
- HTTP URLs downloaded to the persistent volume for long-term availability

Public API
----------
get_render_output_dir() → Path
save_asset(render_output_id, variant_label, source, conn, metadata) → int
list_render_assets(render_output_id, conn) → list[dict]
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any


def get_render_output_dir() -> Path:
    """
    Return (and create if needed) the local render output directory.
    Controlled by CI_RENDER_OUTPUT_DIR config var.
    """
    from creative_intelligence import config
    path = Path(config.CI_RENDER_OUTPUT_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _download_url(url: str, dest: Path) -> None:
    """Download a remote URL (or copy a file:// URI) to a local file."""
    try:
        if url.startswith("file://"):
            # file:// URI — copy from temp file written by provider
            from urllib.request import url2pathname
            src = Path(url2pathname(url[7:]))  # strip "file://"
            shutil.copy2(src, dest)
        else:
            import urllib.request
            urllib.request.urlretrieve(url, dest)  # noqa: S310
    except Exception as exc:
        raise RuntimeError(f"Failed to download asset from {url}: {exc}") from exc


def save_asset(
    render_output_id: int,
    variant_label: str,
    source: str,
    conn: sqlite3.Connection,
    metadata: dict[str, Any] | None = None,
) -> int:
    """
    Persist an asset reference to render_assets.

    For real URLs  → downloads to the persistent volume (Railway Volume /
                     CI_RENDER_OUTPUT_DIR).  CDN URL kept in metadata["cdn_url"]
                     as a fallback.  If download fails or yields a tiny file,
                     the CDN URL is stored directly and the /image route will
                     redirect the browser to it.
    For file://    → copies to local render dir (provider wrote raw bytes).
    For mock://    → stores the path string as-is (no download).

    Parameters
    ----------
    render_output_id : FK → render_outputs.id
    variant_label    : e.g. "minimal", "premium"
    source           : URL (https://…), file:// URI, or mock path (mock://…)
    conn             : open sqlite3 connection
    metadata         : optional dict serialised as JSON

    Returns
    -------
    render_assets.id of the inserted row
    """
    if source.startswith("mock://") or source.startswith("mock:"):
        # Mock path — store as-is, no download
        asset_path = source
    elif source.startswith("https://") or source.startswith("http://"):
        # Download to the persistent volume so the image survives CDN URL
        # expiry (Replicate URLs expire after ~24 h).  Store the original CDN
        # URL in metadata as a fallback in case the local file is ever missing.
        if metadata is None:
            metadata = {}
        metadata["cdn_url"] = source  # always preserve original URL

        out_dir = get_render_output_dir() / str(render_output_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        # Use a counter suffix so concurrent regenerations don't overwrite each
        # other before the DB row is committed.
        import time as _time
        dest = out_dir / f"{variant_label}_{int(_time.time())}.png"
        try:
            _download_url(source, dest)
            # Sanity-check: a real AI image is at least ~10 KB.
            if dest.exists() and dest.stat().st_size >= 10_000:
                asset_path = str(dest)
            else:
                # Tiny / corrupt file — fall back to CDN redirect.
                if dest.exists():
                    dest.unlink(missing_ok=True)
                asset_path = source
        except Exception:
            # Download failed — use CDN URL directly; /image will redirect.
            if dest.exists():
                dest.unlink(missing_ok=True)
            asset_path = source
    elif source.startswith("file://"):
        # Temp file written by provider for models that return raw bytes.
        # Copy to the render output dir so it survives for the current process.
        out_dir = get_render_output_dir() / str(render_output_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / f"{variant_label}.png"  # always .png for binary outputs
        _download_url(source, dest)
        asset_path = str(dest)
    else:
        # Unknown scheme — store as-is
        asset_path = source

    meta_str = json.dumps(metadata or {})
    with conn:
        cur = conn.execute(
            """INSERT INTO render_assets
                 (render_output_id, asset_type, variant_label, asset_path_or_url, metadata_json)
               VALUES (?, 'image_variant', ?, ?, ?)""",
            (render_output_id, variant_label, asset_path, meta_str),
        )
    return cur.lastrowid


def list_render_assets(
    render_output_id: int,
    conn: sqlite3.Connection,
) -> list[dict[str, Any]]:
    """Return the latest render_assets row per variant_label for a render_output_id.

    When a variant has been regenerated multiple times only the most recent
    asset (highest id) is returned — keeps the response small regardless of
    how many regeneration runs have accumulated.
    """
    rows = conn.execute(
        """SELECT * FROM render_assets
           WHERE id IN (
               SELECT MAX(id) FROM render_assets
               WHERE render_output_id = ?
               GROUP BY variant_label
           )
           ORDER BY id ASC""",
        (render_output_id,),
    ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        try:
            d["metadata"] = json.loads(d.get("metadata_json") or "{}")
        except Exception:
            d["metadata"] = {}
        result.append(d)
    return result
