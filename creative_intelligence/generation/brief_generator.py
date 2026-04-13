"""
Brief and script generator.

Generates:
  - UGC briefs (for video creators)
  - Static creative briefs (for designers)
  - Script concepts (structured narrative outline)
  - Primary text variants
  - Headline variants
  - CTA variants
  - Structured test matrices

All generation defaults to dry_run=True.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from creative_intelligence.db import get_connection
from creative_intelligence.generation.llm_client import get_llm_client
from creative_intelligence.analysis.patterns import get_patterns
from creative_intelligence.product_knowledge.enricher import build_prompt_context_block

_SYSTEM_PROMPT = """You are a senior performance marketing creative strategist.
You write clear, actionable creative briefs for ad creators and copywriters.
Follow the brief format exactly. Return ONLY valid JSON."""


# ─────────────────────────────────────────────
# UGC Brief
# ─────────────────────────────────────────────

_UGC_BRIEF_PROMPT = """Create a detailed UGC (user-generated content) video brief for a Meta ad.

## Pattern context (what's working)
Hook type: {hook_type}
Angle: {angle}
Emotional trigger: {emotional_trigger}
Visual pattern: {visual_context}

## Product context
{product_context}

## Hook to open with
{hook_text}

## Requirements
Return a JSON object with these fields:
{{
  "title": "Brief title",
  "target_length_seconds": 30,
  "hook_instruction": "Exact opening line + delivery direction",
  "scene_breakdown": [
    {{"scene": 1, "duration_s": 5, "action": "...", "voiceover": "..."}},
    ...
  ],
  "key_messages": ["message 1", "message 2", "message 3"],
  "tone_and_style": "...",
  "visual_notes": "...",
  "cta_instruction": "...",
  "do_not_include": ["thing to avoid 1", "thing to avoid 2"]
}}"""


# ─────────────────────────────────────────────
# Static Creative Brief
# ─────────────────────────────────────────────

_STATIC_BRIEF_PROMPT = """Create a static image ad creative brief for a Meta ad.

## Pattern context
Hook type: {hook_type}
Angle: {angle}
Visual pattern: {visual_context}

## Product context
{product_context}

## Headline
{headline}

Return a JSON object:
{{
  "title": "Brief title",
  "format": "1:1 square | 4:5 portrait | 9:16 story",
  "primary_visual": "Description of the main visual element",
  "headline_text": "...",
  "body_text": "...",
  "cta_button": "...",
  "text_overlay": "Optional text overlay content",
  "visual_style_notes": "Colour, mood, composition direction",
  "design_dont_list": ["avoid 1", "avoid 2"]
}}"""


# ─────────────────────────────────────────────
# Primary Text & Headlines
# ─────────────────────────────────────────────

_PRIMARY_TEXT_PROMPT = """Write {count} primary text variants for a Meta ad.

## Hook
{hook_text}

## Pattern context
Angle: {angle}
Offer style: {offer_style}

## Product context
{product_context}

Requirements:
- Each variant 50-150 words
- Lead with a pain point or desire
- Include the product benefit clearly
- End with a soft or hard CTA
- Vary the structure across variants

Return JSON: {{"variants": ["text1", "text2", ...]}}"""

_HEADLINE_PROMPT = """Write {count} headline variants for a Meta ad.

Hook: {hook_text}
Angle: {angle}
Product: {product_context}

Requirements:
- Max 40 characters each
- Focus on one clear benefit or hook
- Vary style: benefit, curiosity, social proof, direct

Return JSON: {{"headlines": ["h1", "h2", ...]}}"""

_CTA_PROMPT = """Write {count} CTA text variants for a Meta ad.

Product: {product_context}
Offer: {offer_context}
Desired action: purchase

Requirements:
- Short (2-5 words)
- Action-oriented
- Vary urgency level (soft to hard)

Return JSON: {{"ctas": ["cta1", "cta2", ...]}}"""


# ─────────────────────────────────────────────
# Test Matrix
# ─────────────────────────────────────────────

_TEST_MATRIX_PROMPT = """Create a structured A/B test matrix for Meta ad creative testing.

## Context
Top-performing pattern: {pattern_summary}
Product: {product_context}

## Goal
Generate a test matrix that isolates ONE variable at a time.
Design 2-4 test groups with clear hypotheses.

Return JSON:
{{
  "test_name": "...",
  "hypothesis": "...",
  "primary_variable": "hook_type | angle | visual_format | offer_style | cta",
  "control_description": "Current best performer description",
  "variants": [
    {{
      "variant_id": "A",
      "variable_value": "...",
      "hook": "...",
      "angle": "...",
      "cta": "...",
      "rationale": "Why this variant"
    }}
  ],
  "success_metric": "CTR | CPA | ROAS | purchases",
  "minimum_spend_per_variant": 50,
  "notes": "..."
}}"""


# ─────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────

def _get_visual_summary(creative_ids: list[str], db: sqlite3.Connection) -> str:
    if not creative_ids:
        return "No visual data."
    placeholders = ",".join("?" * len(creative_ids))
    rows = db.execute(
        f"SELECT visual_summary FROM creative_visual_attributes WHERE creative_id IN ({placeholders}) LIMIT 2",
        creative_ids,
    ).fetchall()
    summaries = [r["visual_summary"] for r in rows if r["visual_summary"]]
    return " ".join(summaries) or "No visual data."


def _save_script(
    script_type: str,
    title: str,
    content: str,
    run_id: int,
    hook_id: int | None,
    creative_ids: list[str],
    product_id: str | None,
    db: sqlite3.Connection,
) -> int:
    cursor = db.execute(
        """INSERT INTO generated_scripts
           (run_id, script_type, title, content, hook_id, based_on_creative_ids, product_id)
           VALUES (?,?,?,?,?,?,?)""",
        (run_id, script_type, title, content, hook_id,
         json.dumps(creative_ids), product_id),
    )
    db.commit()
    return cursor.lastrowid


def _create_run(
    run_type: str,
    creative_ids: list[str],
    pattern_ids: list[int],
    product_id: str | None,
    dry_run: bool,
    model: str,
    db: sqlite3.Connection,
) -> int:
    cursor = db.execute(
        """INSERT INTO generation_runs
           (run_type, model, input_creative_ids, input_pattern_ids, product_id, dry_run)
           VALUES (?,?,?,?,?,?)""",
        (run_type, model, json.dumps(creative_ids), json.dumps(pattern_ids),
         product_id, 1 if dry_run else 0),
    )
    db.commit()
    return cursor.lastrowid


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def generate_ugc_brief(
    pattern_id: int | None = None,
    hook_text: str = "",
    product_id: str | None = None,
    dry_run: bool = True,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    db = conn or get_connection()
    llm = get_llm_client(dry_run=dry_run)
    pattern: dict = {}
    example_ids: list[str] = []

    if pattern_id:
        row = db.execute("SELECT * FROM creative_patterns WHERE id = ?", (pattern_id,)).fetchone()
        if row:
            pattern = dict(row)
            example_ids = json.loads(pattern.get("example_creative_ids") or "[]")

    visual_ctx  = _get_visual_summary(example_ids, db)
    product_ctx = build_prompt_context_block(product_id, db) if product_id else "Not provided."

    prompt = _UGC_BRIEF_PROMPT.format(
        hook_type       = pattern.get("hook_type", "unknown"),
        angle           = pattern.get("angle", "unknown"),
        emotional_trigger = pattern.get("emotional_trigger", "unknown"),
        visual_context  = visual_ctx,
        product_context = product_ctx,
        hook_text       = hook_text or "Open with the strongest hook you can write.",
    )

    run_id = _create_run(
        "ugc_brief", example_ids,
        [pattern_id] if pattern_id else [], product_id, dry_run, llm.model, db,
    )
    raw    = llm.complete_json(_SYSTEM_PROMPT, prompt, temperature=0.7)
    brief  = raw if isinstance(raw, dict) else {"raw": raw}
    title  = brief.get("title", "UGC Brief")
    content = json.dumps(brief, indent=2)

    script_id = None
    if not dry_run:
        script_id = _save_script("ugc_brief", title, content, run_id, None, example_ids, product_id, db)
        db.execute("UPDATE generation_runs SET output_count=1 WHERE id=?", (run_id,))
        db.commit()

    return {"run_id": run_id, "script_id": script_id, "brief": brief, "dry_run": dry_run}


def generate_static_brief(
    pattern_id: int | None = None,
    headline: str = "",
    product_id: str | None = None,
    dry_run: bool = True,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    db = conn or get_connection()
    llm = get_llm_client(dry_run=dry_run)
    pattern: dict = {}
    example_ids: list[str] = []

    if pattern_id:
        row = db.execute("SELECT * FROM creative_patterns WHERE id = ?", (pattern_id,)).fetchone()
        if row:
            pattern = dict(row)
            example_ids = json.loads(pattern.get("example_creative_ids") or "[]")

    visual_ctx  = _get_visual_summary(example_ids, db)
    product_ctx = build_prompt_context_block(product_id, db) if product_id else "Not provided."

    prompt = _STATIC_BRIEF_PROMPT.format(
        hook_type       = pattern.get("hook_type", "unknown"),
        angle           = pattern.get("angle", "unknown"),
        visual_context  = visual_ctx,
        product_context = product_ctx,
        headline        = headline or "Write a compelling headline.",
    )

    run_id = _create_run(
        "static_brief", example_ids,
        [pattern_id] if pattern_id else [], product_id, dry_run, llm.model, db,
    )
    raw    = llm.complete_json(_SYSTEM_PROMPT, prompt, temperature=0.7)
    brief  = raw if isinstance(raw, dict) else {"raw": raw}
    title  = brief.get("title", "Static Brief")
    content = json.dumps(brief, indent=2)

    script_id = None
    if not dry_run:
        script_id = _save_script("static_brief", title, content, run_id, None, example_ids, product_id, db)
        db.execute("UPDATE generation_runs SET output_count=1 WHERE id=?", (run_id,))
        db.commit()

    return {"run_id": run_id, "script_id": script_id, "brief": brief, "dry_run": dry_run}


def generate_primary_text_variants(
    hook_text: str,
    angle: str = "problem_solution",
    offer_style: str = "hard_offer",
    count: int = 5,
    product_id: str | None = None,
    dry_run: bool = True,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    db = conn or get_connection()
    llm = get_llm_client(dry_run=dry_run)
    product_ctx = build_prompt_context_block(product_id, db) if product_id else "Not provided."

    prompt = _PRIMARY_TEXT_PROMPT.format(
        count=count, hook_text=hook_text, angle=angle,
        offer_style=offer_style, product_context=product_ctx,
    )
    run_id = _create_run("primary_text", [], [], product_id, dry_run, llm.model, db)
    raw = llm.complete_json(_SYSTEM_PROMPT, prompt, temperature=0.85)
    texts = raw.get("variants", []) if isinstance(raw, dict) else []

    script_ids = []
    if not dry_run:
        for t in texts:
            sid = _save_script("primary_text", f"Primary text variant", t, run_id, None, [], product_id, db)
            script_ids.append(sid)
        db.execute("UPDATE generation_runs SET output_count=? WHERE id=?", (len(texts), run_id))
        db.commit()

    return {"run_id": run_id, "texts": texts, "script_ids": script_ids, "dry_run": dry_run}


def generate_test_matrix(
    pattern_id: int | None = None,
    product_id: str | None = None,
    dry_run: bool = True,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    db = conn or get_connection()
    llm = get_llm_client(dry_run=dry_run)
    pattern: dict = {}
    pattern_ids: list[int] = []

    if pattern_id:
        row = db.execute("SELECT * FROM creative_patterns WHERE id = ?", (pattern_id,)).fetchone()
        if row:
            pattern = dict(row)
            pattern_ids = [pattern_id]

    product_ctx = build_prompt_context_block(product_id, db) if product_id else "Not provided."
    pattern_summary = (
        f"Hook: {pattern.get('hook_type')} | Angle: {pattern.get('angle')} | "
        f"Format: {pattern.get('format')} | Winner count: {pattern.get('winner_count', 0)}"
        if pattern else "No pattern selected."
    )

    prompt = _TEST_MATRIX_PROMPT.format(
        pattern_summary=pattern_summary,
        product_context=product_ctx,
    )
    run_id = _create_run("test_matrix", [], pattern_ids, product_id, dry_run, llm.model, db)
    raw    = llm.complete_json(_SYSTEM_PROMPT, prompt, temperature=0.5)
    matrix = raw if isinstance(raw, dict) else {"raw": raw}

    script_id = None
    if not dry_run:
        script_id = _save_script(
            "test_matrix",
            matrix.get("test_name", "Test Matrix"),
            json.dumps(matrix, indent=2),
            run_id, None, [], product_id, db,
        )
        db.execute("UPDATE generation_runs SET output_count=1 WHERE id=?", (run_id,))
        db.commit()

    return {"run_id": run_id, "script_id": script_id, "matrix": matrix, "dry_run": dry_run}
