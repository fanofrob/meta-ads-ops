"""
Hook generator.

Generates hook variants modeled on winning creative patterns.
Supports:
  - hook banks from top-performing patterns
  - product-enriched hooks
  - hook type expansions (curiosity / pain_point / social_proof / etc.)
  - visual-informed hooks (when vision data is available)

All generation defaults to dry_run=True.
Results are persisted in generated_hooks and generation_runs tables.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from typing import Any

from creative_intelligence.db import get_connection
from creative_intelligence.generation.llm_client import get_llm_client
from creative_intelligence.generation.hook_qa import filter_hooks, QAConfig, summarise_qa
from creative_intelligence.analysis.patterns import get_patterns
from creative_intelligence.product_knowledge.enricher import build_prompt_context_block

_SYSTEM_PROMPT = """You are an expert direct-response ad copywriter specialising in Meta/Facebook ads.
You write hooks that stop the scroll and drive clicks.
Follow the brief exactly. Return ONLY the JSON object requested.

## Hook University — Core Principles (apply to every hook you write)
1. Hook = Paradox = Desire + Conflict + Partial Solution. Never give full resolution — leave tension.
2. Order is flexible. Start with desire, conflict, or solution — all orderings work.
3. 4-Year-Old Test: zero ambiguity. Anyone must know exactly what you mean within 1 second.
4. One archetype. Every hook speaks to one specific person — never a vague "everyone".
5. Hook writing is mechanical. Think opposite. Think solution. Reframe the obvious.
6. Specific beats generic. A vivid, concrete detail outperforms a broad claim every time.
7. Long hooks work. Specificity = authority. Don't fear 25–40 word hooks when the idea demands it.

## Authority Hook Formulas (use where they fit the brief angle)
AF1  "I spent X doing/buying Y to figure out Z — so you don't have to"
     → You did the hard work. Instant authority.
AF2  "I thought X until Y"
     → Paradox reveal. Challenges a belief then resolves it. Extremely strong.
AF3  "I [struggled with X] for Y until I [discovered/changed Z]"
     → Journey authority. Share the path, not just the destination.
AF4  "I'm discovering this too — here's what I've learned so far"
     → Works without established authority. Invites viewers on the journey with you.
AF5  "Everyone says X, but [study/expert/data] shows Y"
     → Third-party hook. Removes salesy tone — you're reporting, not selling.
AF6  "X doesn't have to mean Y — here's how to have both"
     → Resolves a perceived trade-off. Creates immediate intrigue."""

_HOOK_PROMPT_TEMPLATE = """Write {request_count} scroll-stopping ad hooks based on the following brief.

## Creative Pattern (what's working)
Hook type: {hook_type}
Angle: {angle}
Archetype: {archetype}
Emotional trigger: {emotional_trigger}
Average CTR of this pattern: {avg_ctr}
Tone: {tone}
Campaign goal: {goal}
Target audience: {audience}

## Product Context
{product_context}

## Visual Context (if available)
{visual_context}

## Winning hooks from YOUR account history (same pattern)
These are real hooks that performed on this product/audience. Study the angle,
specificity, and tone — then write better versions, not copies.
{example_hooks}

## HARD RULES (violations will be rejected)
1. PRODUCT NAME REQUIRED — every hook must name the actual product: {product_name}.
   Do NOT use placeholders like [fruit], [product], [brand], or [X].
2. NO COPYING — do not reuse the opening words of any example hook.
   Forbidden openings (do not start with these):
{forbidden_openings}
3. CHARACTER LIMIT — each hook must be 20–125 characters.
4. NO BRACKETS — no [placeholder] text of any kind.

## STRUCTURAL VARIETY — use EACH of these 7 structures at least once
S1  Bold quality claim:   "These are the [superlative] [product] you will ever taste."
S2  Sensory-first:        Lead with a taste/texture description before naming product.
S3  Contrast:             Contrast what grocery stores offer vs. what you deliver.
S4  Discovery/reveal:     "You've never tasted [product] like this." or similar reveal frame.
S5  Question:             A single sharp question that ends with "?".
S6  Origin/provenance:    Lead with WHERE or HOW the product is grown/sourced.
S7  Invitation to try:    A direct, warm invitation to experience the product.

Additionally, weave in Authority Hook formulas from the system prompt (AF1–AF6)
where they strengthen the angle. These are proven paradox/conflict structures
that layer naturally on top of the 7 formats above.

Distribute your {request_count} hooks across the structures. If {request_count} > 7,
revisit structures with fresh wording — never reuse the same opening phrase.
Always ground each hook in what worked in your account history above:
same emotional trigger, same specificity level, same audience voice.

## Output format
Return a JSON object with a single key "hooks" containing an array of strings.
Example: {{"hooks": ["hook text one", "hook text two", ...]}}

Generate exactly {request_count} hooks now."""


def _get_example_hooks(
    creative_ids: list[str],
    db: sqlite3.Connection,
) -> list[str]:
    """Fetch hook_text from DB for a list of creative_ids."""
    if not creative_ids:
        return []
    placeholders = ",".join("?" * len(creative_ids))
    rows = db.execute(
        f"SELECT hook_text FROM creatives WHERE id IN ({placeholders}) AND hook_text != ''",
        creative_ids,
    ).fetchall()
    return [r["hook_text"] for r in rows if r["hook_text"]]


def _forbidden_openings(example_hooks: list[str]) -> str:
    """Extract the first 4 normalised tokens from each example as forbidden openings."""
    lines = []
    for h in example_hooks:
        tokens = re.sub(r"[^\w\s]", "", h.lower()).split()[:4]
        if len(tokens) >= 2:
            lines.append(f'  - "{" ".join(tokens)}..."')
    return "\n".join(lines) if lines else "  (none — no example hooks available)"


def _product_name_from_context(product_ctx: str, pattern: dict) -> str:
    """Best-effort extract a product name for the hard-rule reminder in the prompt."""
    # Try the product context block first (looks for 'Name:' or 'Product:' line)
    for line in product_ctx.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith(("name:", "product:")):
            val = stripped.split(":", 1)[-1].strip()
            if val:
                return val
    return "the product"


def _get_visual_context(creative_ids: list[str], db: sqlite3.Connection) -> str:
    """Build visual context string from visual analysis data, if available."""
    if not creative_ids:
        return "No visual data available."
    placeholders = ",".join("?" * len(creative_ids))
    rows = db.execute(
        f"""SELECT visual_format, shot_type, composition_style, face_presence,
                   text_overlay_presence, visual_hook_description
            FROM creative_visual_attributes
            WHERE creative_id IN ({placeholders})
            LIMIT 3""",
        creative_ids,
    ).fetchall()
    if not rows:
        return "No visual data available."
    lines = []
    for r in rows:
        parts = []
        if r["visual_format"]:
            parts.append(f"Format: {r['visual_format']}")
        if r["shot_type"]:
            parts.append(f"Shot: {r['shot_type']}")
        if r["face_presence"]:
            parts.append("Has faces")
        if r["text_overlay_presence"]:
            parts.append("Has text overlay")
        if r["visual_hook_description"]:
            parts.append(f'Visual hook: "{r["visual_hook_description"]}"')
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def _create_run(
    run_type: str,
    input_creative_ids: list[str],
    input_pattern_ids: list[int],
    product_id: str | None,
    dry_run: bool,
    db: sqlite3.Connection,
    model: str,
) -> int:
    cursor = db.execute(
        """INSERT INTO generation_runs
           (run_type, model, input_creative_ids, input_pattern_ids, product_id, dry_run)
           VALUES (?,?,?,?,?,?)""",
        (
            run_type,
            model,
            json.dumps(input_creative_ids),
            json.dumps(input_pattern_ids),
            product_id,
            1 if dry_run else 0,
        ),
    )
    db.commit()
    return cursor.lastrowid


def _save_hooks(
    hooks: list[str],
    run_id: int,
    hook_type: str,
    angle: str,
    creative_ids: list[str],
    pattern_id: int | None,
    product_id: str | None,
    db: sqlite3.Connection,
) -> list[int]:
    ids = []
    for hook in hooks:
        cursor = db.execute(
            """INSERT INTO generated_hooks
               (run_id, hook_text, hook_type, angle,
                based_on_creative_ids, based_on_pattern_id, product_id)
               VALUES (?,?,?,?,?,?,?)""",
            (run_id, hook, hook_type, angle,
             json.dumps(creative_ids), pattern_id, product_id),
        )
        ids.append(cursor.lastrowid)
    db.commit()
    return ids


def generate_hooks_from_pattern(
    pattern_id: int,
    count: int = 10,
    product_id: str | None = None,
    dry_run: bool = True,
    conn: sqlite3.Connection | None = None,
    # QA / generation controls
    similarity_threshold: float = 0.65,
    diversity: str = "medium",
    tone: str = "authentic",
    goal: str = "conversions",
    audience: str = "",
    required_terms: list[str] | None = None,
) -> dict[str, Any]:
    """Generate hooks based on a single creative pattern.

    Returns {run_id, hooks: [str], hook_ids: [int], dry_run: bool, qa_summary: dict}.
    """
    db = conn or get_connection()
    llm = get_llm_client(dry_run=dry_run)

    pattern_row = db.execute(
        "SELECT * FROM creative_patterns WHERE id = ?", (pattern_id,)
    ).fetchone()
    if not pattern_row:
        raise ValueError(f"Pattern {pattern_id} not found")
    pattern = dict(pattern_row)

    example_ids = json.loads(pattern.get("example_creative_ids") or "[]")
    example_hooks = _get_example_hooks(example_ids, db)
    visual_ctx = _get_visual_context(example_ids, db)
    product_ctx = build_prompt_context_block(product_id, db) if product_id else ""

    product_name = _product_name_from_context(product_ctx, pattern)
    forbidden = _forbidden_openings(example_hooks)

    # Over-request by 1.5× so QA filtering leaves enough clean hooks.
    request_count = max(count, int(count * 1.5))

    prompt = _HOOK_PROMPT_TEMPLATE.format(
        request_count     = request_count,
        hook_type         = pattern.get("hook_type") or "unknown",
        angle             = pattern.get("angle") or "unknown",
        archetype         = pattern.get("archetype") or "unknown",
        emotional_trigger = pattern.get("emotional_trigger") or "unknown",
        avg_ctr           = f"{pattern.get('avg_ctr') or 0:.2%}" if pattern.get("avg_ctr") else "unknown",
        tone              = tone,
        goal              = goal or "conversions",
        audience          = audience or "general consumers",
        product_context   = product_ctx or "No product context provided.",
        visual_context    = visual_ctx,
        example_hooks     = "\n".join(f"- {h}" for h in example_hooks[:5]) or "None available.",
        product_name      = product_name,
        forbidden_openings = forbidden,
    )

    run_id = _create_run("hooks", example_ids, [pattern_id], product_id, dry_run, db, llm.model)

    raw = llm.complete_json(_SYSTEM_PROMPT, prompt, temperature=0.85)
    raw_hooks: list[str] = raw.get("hooks", raw.get("items", [])) if isinstance(raw, dict) else raw
    raw_hooks = [str(h).strip() for h in raw_hooks if h]

    # QA filter — source_hooks = example_hooks so we don't re-surface winning copy verbatim.
    qa_cfg = QAConfig(
        similarity_threshold=similarity_threshold,
        diversity=diversity,
    )
    qa_results = filter_hooks(
        raw_hooks,
        source_hooks=example_hooks,
        config=qa_cfg,
        required_terms=required_terms,
    )
    qa_summary = summarise_qa(qa_results)
    hooks = [r.hook for r in qa_results if r.passed][:count]

    hook_ids = []
    if not dry_run:
        hook_ids = _save_hooks(
            hooks, run_id,
            pattern.get("hook_type", "unknown"),
            pattern.get("angle", "unknown"),
            example_ids, pattern_id, product_id, db,
        )
        db.execute(
            "UPDATE generation_runs SET output_count=? WHERE id=?",
            (len(hooks), run_id),
        )
        db.commit()

    return {
        "run_id":      run_id,
        "hooks":       hooks,
        "hook_ids":    hook_ids,
        "dry_run":     dry_run,
        "qa_summary":  qa_summary,
        "qa_results":  [r.to_dict() for r in qa_results],
    }


def generate_hook_bank(
    date_range: str = "7d",
    hooks_per_pattern: int = 5,
    max_patterns: int = 5,
    product_id: str | None = None,
    dry_run: bool = True,
    conn: sqlite3.Connection | None = None,
    pattern_id: int | None = None,
    # QA / generation controls
    similarity_threshold: float = 0.65,
    diversity: str = "medium",
    tone: str = "authentic",
    required_terms: list[str] | None = None,
) -> dict[str, Any]:
    """Generate a full hook bank from top patterns.

    Returns {patterns_used: int, total_hooks: int, results: [...]}.
    """
    db = conn or get_connection()

    if pattern_id is not None:
        pattern_row = db.execute(
            "SELECT * FROM creative_patterns WHERE id = ?", (pattern_id,)
        ).fetchone()
        if not pattern_row:
            return {"patterns_used": 0, "total_hooks": 0, "results": []}
        patterns = [dict(pattern_row)]
    else:
        patterns = get_patterns(min_winners=1, conn=db)[:max_patterns]

    if not patterns:
        return {"patterns_used": 0, "total_hooks": 0, "results": []}

    results = []
    for p in patterns:
        result = generate_hooks_from_pattern(
            p["id"], hooks_per_pattern, product_id, dry_run, db,
            similarity_threshold=similarity_threshold,
            diversity=diversity,
            tone=tone,
            required_terms=required_terms,
        )
        results.append({
            "pattern_name": p["pattern_name"],
            **result,
        })

    total = sum(len(r["hooks"]) for r in results)
    return {"patterns_used": len(patterns), "total_hooks": total, "results": results}
