"""
Static rendering layer for the creative intelligence subsystem.

Turns StoredAdBrief production outputs into structured render specs
and optional image generation variants for internal review.

Public API
----------
render_static_brief(production_output_id, conn, ...) → dict
    Core entry point. Returns render specs + asset references.

See also:
  schemas.py       — RenderSpec TypedDict + VARIANT_STRATEGIES
  prompt_builder.py — builds prompts from StaticAdBrief fields
  providers.py     — ImageGenerator ABC + Mock + NanaBanana stubs
  asset_store.py   — file path management + DB helpers
  exporters.py     — render spec → markdown / JSON / text
"""
from creative_intelligence.rendering.static_renderer import render_static_brief
from creative_intelligence.rendering.schemas import VARIANT_STRATEGIES, RenderSpec

__all__ = ["render_static_brief", "VARIANT_STRATEGIES", "RenderSpec"]
