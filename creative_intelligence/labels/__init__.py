"""
Good Hill Farms candle-label image prompt system.

Deterministic prompt assembly for the approved "1d · Full-bleed Photo" art
direction. Completely isolated from the Meta-ads render pipeline in
creative_intelligence/rendering/ — nothing here reads or writes that code path.

Unlike the ads pipeline (LLM writes a brief, templates rotate on a row-ID seed),
this system is pure deterministic assembly: the operator picks 5 codes off a
grid and gets exactly one controlled change per letter.
"""
