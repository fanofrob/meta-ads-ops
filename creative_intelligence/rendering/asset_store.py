"""
Asset storage helpers for the static rendering layer.

Handles:
- Local render output directory management
- Saving asset paths/URLs to the render_assets table
- Downloading remote URLs to local files (skipped for mock:// paths)

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

    For real URLs  → downloads the file to
        {CI_RENDER_OUTPUT_DIR}/{render_output_id}/{variant_label}.png
        and stores the local path.
    For mock://    → stores the path string as-is (no download).

    Parameters
    ----------
    render_output_id : FK → render_outputs.id
    variant_label    : e.g. "minimal", "premium"
    source           : URL (https://…) or mock path (mock://…)
    conn             : open sqlite3 connection
    metadata         : optional dict serialised as JSON

    Returns
    -------
    render_assets.id of the inserted row
    """
    if source.startswith("mock://") or source.startswith("mock:"):
        asset_path = source
    else:
        # Download real image to local file
        out_dir = get_render_output_dir() / str(render_output_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(source.split("?")[0]).suffix or ".png"
        dest = out_dir / f"{variant_label}{ext}"
        _download_url(source, dest)
        asset_path = str(dest)

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
    """Return all render_assets rows for a given render_output_id."""
    rows = conn.execute(
        "SELECT * FROM render_assets WHERE render_output_id = ? ORDER BY id ASC",
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
