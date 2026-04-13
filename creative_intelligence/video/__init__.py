"""
Video creative module.

Converts winning concepts into:
- Storyboards (4-6 scenes with purpose, visual, text, duration)
- Shot lists (camera type, framing, movement, lighting per scene)
- UGC scripts (hook, talking points, demo actions, CTA)
- Scene frame images (via existing rendering providers)

Public API
----------
build_storyboard(concept, product_id, conn, ...) -> dict
"""
from creative_intelligence.video.storyboard_builder import build_storyboard

__all__ = ["build_storyboard"]
