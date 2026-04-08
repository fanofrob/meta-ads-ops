"""
Core rendering engine for the static rendering layer.

Converts a stored StaticAdBrief (production_outputs row) into:
  1. A list of structured RenderSpec dicts (always)
  2. Optionally: image assets via an ImageGenerator provider

Public API
----------
render_static_brief(
    production_output_id, conn,
    variants, aspect_ratio,
    generate_images, provider_name,
    dry_run
) → dict

Return shape
------------
{
    "render_output_id": int | None,    # None if dry_run=True
    "render_specs":     list[dict],    # one RenderSpec per variant
    "assets":           list[dict],    # [{variant_label, path, asset_id}]
    "status":           str,           # "spec_only" | "images_generated" | "images_failed"
    "provider":         str,           # provider name used
}
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from creative_intelligence.rendering.schemas import VARIANT_STRATEGIES
from creative_intelligence.rendering.prompt_builder import build_render_specs
from creative_intelligence.rendering.asset_store import save_asset, list_render_assets


def render_static_brief(
    production_output_id: int,
    conn: sqlite3.Connection,
    variants: tuple[str, ...] = VARIANT_STRATEGIES,
    aspect_ratio: str = "9:16",
    generate_images: bool | None = None,
    provider_name: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Build render specs for a stored StaticAdBrief and optionally generate images.

    Parameters
    ----------
    production_output_id : ID of the production_outputs row (output_type='static_brief')
    conn                 : Open sqlite3 connection
    variants             : Which variant strategies to generate (default: all 5)
    aspect_ratio         : Target aspect ratio (default: "9:16")
    generate_images      : True/False override; None reads CI_IMAGE_GENERATION_ENABLED
    provider_name        : Provider override; None reads CI_IMAGE_PROVIDER
    dry_run              : If True, build specs but skip all DB writes

    Returns
    -------
    {
        "render_output_id": int | None,
        "render_specs":     list[dict],
        "assets":           list[dict],
        "status":           "spec_only" | "images_generated" | "images_failed",
        "provider":         str,
    }

    Raises
    ------
    ValueError  : if the production_outputs row doesn't exist or isn't a static_brief
    """
    from creative_intelligence import config
    from creative_intelligence.rendering.providers import get_provider

    # ── 1. Fetch and validate source brief ──────────────────────────────
    row = conn.execute(
        "SELECT * FROM production_outputs WHERE id = ?",
        (production_output_id,),
    ).fetchone()

    if row is None:
        raise ValueError(
            f"production_outputs id={production_output_id} not found"
        )

    row_dict = dict(row)
    if row_dict.get("output_type") != "static_brief":
        raise ValueError(
            f"production_outputs id={production_output_id} has "
            f"output_type={row_dict.get('output_type')!r}, expected 'static_brief'"
        )

    try:
        brief = json.loads(row_dict.get("output_json") or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"production_outputs id={production_output_id}: "
            f"output_json is not valid JSON: {exc}"
        ) from exc

    # ── 2. Build render specs ────────────────────────────────────────────
    specs = build_render_specs(
        brief=brief,
        production_output_id=production_output_id,
        variants=variants,
        aspect_ratio=aspect_ratio,
    )

    # ── 3. Persist render_outputs row ────────────────────────────────────
    render_output_id: int | None = None
    if not dry_run:
        with conn:
            cur = conn.execute(
                """INSERT INTO render_outputs
                     (source_production_output_id, render_type, render_spec_json,
                      status, provider_name)
                   VALUES (?, 'static_spec', ?, 'spec_only', ?)""",
                (
                    production_output_id,
                    json.dumps(specs),
                    provider_name or config.CI_IMAGE_PROVIDER,
                ),
            )
        render_output_id = cur.lastrowid

    # ── 4. Resolve image-generation flag ────────────────────────────────
    if generate_images is None:
        generate_images = config.CI_IMAGE_GENERATION_ENABLED

    assets: list[dict[str, Any]] = []
    status = "spec_only"
    provider = get_provider(provider_name)

    # ── 5. Optional image generation ────────────────────────────────────
    if generate_images and not dry_run:
        try:
            for spec in specs:
                paths = provider.generate(
                    prompt=spec["visual_prompt"],
                    negative_prompt=spec["negative_prompt"],
                    aspect_ratio=spec["aspect_ratio"],
                )
                for path in paths:
                    asset_id = save_asset(
                        render_output_id=render_output_id,
                        variant_label=spec["variant_label"],
                        source=path,
                        conn=conn,
                        metadata={
                            "concept_title": spec.get("concept_title", ""),
                            "aspect_ratio":  spec["aspect_ratio"],
                        },
                    )
                    assets.append({
                        "variant_label": spec["variant_label"],
                        "path":          path,
                        "asset_id":      asset_id,
                    })

            # Update status to reflect images were generated
            with conn:
                conn.execute(
                    "UPDATE render_outputs SET status = 'images_generated' WHERE id = ?",
                    (render_output_id,),
                )
            status = "images_generated"

        except Exception as exc:
            # Image generation failed — spec is still stored, log and continue
            if render_output_id is not None:
                with conn:
                    conn.execute(
                        "UPDATE render_outputs SET status = 'images_failed' WHERE id = ?",
                        (render_output_id,),
                    )
            status = "images_failed"
            # Re-raise so callers can distinguish spec-only from generation failure
            raise RuntimeError(
                f"Image generation failed for render_output_id={render_output_id}: {exc}"
            ) from exc

    return {
        "render_output_id": render_output_id,
        "render_specs":     specs,
        "assets":           assets,
        "status":           status,
        "provider":         provider.name,
    }
