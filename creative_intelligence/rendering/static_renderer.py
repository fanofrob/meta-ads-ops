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
    model: str | None = None,
    dry_run: bool = False,
    background_only: bool = True,
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
    background_only      : If True, skip PIL compositing and return raw generated images

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
    # Load active learnings for this product to guide image prompts
    _learnings_rows = []
    _product_id = row_dict.get("product_id")
    if _product_id:
        try:
            _learnings_rows = conn.execute(
                "SELECT learning_type, summary FROM creative_learnings WHERE product_id=? AND active=1",
                (_product_id,),
            ).fetchall()
        except Exception:
            pass
    _learnings = [{"type": r["learning_type"], "summary": r["summary"]} for r in _learnings_rows]

    specs = build_render_specs(
        brief=brief,
        production_output_id=production_output_id,
        variants=variants,
        aspect_ratio=aspect_ratio,
        product_learnings=_learnings if _learnings else None,
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

    # ── 5. Optional image generation + compositing ──────────────────────────
    if generate_images and not dry_run:
        from creative_intelligence.rendering.compositor import composite_ad
        from creative_intelligence.rendering.asset_store import get_render_output_dir
        import logging as _logging
        _log = _logging.getLogger(__name__)

        try:
            for spec in specs:
                generate_kwargs: dict[str, Any] = {}
                if model:
                    generate_kwargs["model"] = model
                paths = provider.generate(
                    prompt=spec["visual_prompt"],
                    negative_prompt=spec["negative_prompt"],
                    aspect_ratio=spec["aspect_ratio"],
                    **generate_kwargs,
                )
                for bg_path in paths:
                    # ── 5a. Save generated image ───────────────────────────
                    # background_only=True (default): AI image IS the final ad
                    # (prompt already includes headline/CTA so text is baked in)
                    # background_only=False: save as background, then composite
                    primary_type = "final_ad" if background_only else "background"
                    bg_asset_id = save_asset(
                        render_output_id=render_output_id,
                        variant_label=spec["variant_label"],
                        source=bg_path,
                        conn=conn,
                        metadata={
                            "concept_title": spec.get("concept_title", ""),
                            "aspect_ratio":  spec["aspect_ratio"],
                            "asset_role":    primary_type,
                        },
                        asset_type=primary_type,
                    )
                    assets.append({
                        "variant_label": spec["variant_label"],
                        "path":          bg_path,
                        "asset_id":      bg_asset_id,
                        "asset_type":    primary_type,
                    })

                    # ── 5b. Composite final ad (only when explicitly requested) ──
                    if background_only:
                        continue

                    # Use local file if save_asset already downloaded it
                    bg_for_composite = bg_path
                    try:
                        saved_row = conn.execute(
                            "SELECT asset_path_or_url FROM render_assets WHERE id = ?",
                            (bg_asset_id,),
                        ).fetchone()
                        if saved_row:
                            candidate = saved_row["asset_path_or_url"]
                            if candidate and not candidate.startswith(
                                ("https://", "http://", "mock://")
                            ):
                                bg_for_composite = candidate
                    except Exception:
                        pass

                    out_dir = get_render_output_dir() / str(render_output_id)
                    out_dir.mkdir(parents=True, exist_ok=True)
                    final_output_path = str(out_dir / f"{spec['variant_label']}_final.png")

                    try:
                        final_path = composite_ad(
                            background_source=bg_for_composite,
                            spec=spec,
                            output_path=final_output_path,
                        )
                        # Guard: skip stub paths emitted for mock backgrounds
                        if final_path.endswith("_mock.png"):
                            continue

                        final_asset_id = save_asset(
                            render_output_id=render_output_id,
                            variant_label=spec["variant_label"],
                            source=final_path,
                            conn=conn,
                            metadata={
                                "concept_title":       spec.get("concept_title", ""),
                                "aspect_ratio":        spec["aspect_ratio"],
                                "background_asset_id": bg_asset_id,
                                "asset_role":          "final_ad",
                            },
                            asset_type="final_ad",
                        )
                        assets.append({
                            "variant_label": spec["variant_label"],
                            "path":          final_path,
                            "asset_id":      final_asset_id,
                            "asset_type":    "final_ad",
                        })
                    except Exception as comp_exc:
                        # Compositing failure is non-fatal — background already saved
                        _log.warning(
                            "Compositing failed for variant=%s render_output_id=%s: %s",
                            spec["variant_label"], render_output_id, comp_exc,
                        )

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
