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

_SYSTEM_PROMPT = """You are an expert direct-response ad copywriter specialising in Meta/Facebook/Instagram ads.
You write scroll-stopping hooks that make people stop, feel seen, and click to buy.
Follow the brief exactly. Return ONLY the JSON object requested.

## The Framework: Every Hook = Desire + Conflict + Partial Solution (D+C+PS)

A hook is a PARADOX. It must create tension by naming what someone wants (D),
revealing why they can't have it yet or what's in the way (C), then teasing
that a solution exists — without giving it away (PS).

Order is flexible. D→C→PS, C→D→PS, PS→C→D all work.
The loop must NEVER be completed inside the hook. Completion kills the click.

## The 12 Hook Archetypes (one per hook — never mix)

A1  Classic D+C+PS
    Desire first, then the specific conflict, then a teased solution.
    "You want X. Here's the problem. There's a way."

A2  Conflict → Desire → PS
    Open with the pain point directly. Name the desire it's blocking. Tease the fix.

A3  PS → Conflict → Desire
    Start with the partial solution (creates false confidence), then flip with the conflict.
    The most powerful pattern interrupt.

A4  "I thought X until Y" — Paradox Reveal
    Challenges a belief the reader holds, then reveals a contradiction without resolving it.
    NEVER complete the revelation in the hook.

A5  "I spent X on Y so you don't have to" — Authority
    Sacrifice + research = instant credibility. The viewer gets the benefit without the cost.

A6  "I [struggled] for X until I [discovered]" — Journey Authority
    Share the path and the turning point. Stop before the destination.

A7  "Most people think X, but actually Y" — Flipped Belief
    Directly confronts and corrects a widespread wrong assumption.

A8  "X doesn't have to mean Y — here's how to have both" — False Tradeoff
    Names the sacrifice the avatar has accepted, then removes it.

A9  Question → Paradox
    A single sharp question that names the exact symptom the avatar is living.
    The question IS the conflict. Answer is withheld.

A10 "I'm figuring this out too — here's what changed" — No Authority Needed
    Peer-level discovery frame. Removes the guru barrier. Invites the scroll.

A11 Carousel Slide 1 — Full Paradox (35–60 words)
    Exhaust every wrong diagnosis the avatar has tried, then name the real specific conflict.
    End with "here's exactly what I do / here's what changed" — never complete the answer.

A12 Carousel Re-Hook — Different Angle (slide 2–3)
    Deepens the same paradox from a different angle (sensory, timing, visual signal, cost).
    Ends with an explicit scroll command at maximum tension.

## AD-SPECIFIC RULES (these are paid Meta ads, not organic content)
- The Partial Solution must tease a PURCHASE or product experience — not a content series.
  ("It ships at peak ripeness" not "here's my tracking method")
- Every hook must make someone want to click to BUY, not just watch more.
- Urgency, scarcity, and specificity of the product experience are your tools.
- The desire must be the desire to TASTE, OWN, or EXPERIENCE — not just to KNOW.
- Sensory language (creamy, sweet-tart, custard-like) beats educational language every time.

## Guardrails — these will be rejected
✗ Rage-bait ("This will make you angry", "You won't believe")
✗ Easy-steps language ("5 simple steps", "here's the trick", "it's this easy")
✗ Vague curiosity bait ("This changed everything", "Nobody talks about this")
✗ Generic hooks (could apply to any product in the category)
✗ Completed loops (hook that answers its own question)
✗ Placeholder brackets [like this]

## Specificity rules
- Numbers anchor credibility: "3 days", "6-hour window", "$12 a fruit", "3 years"
- Name the specific variety, origin, or sensory detail — not just the category
- The avatar's language should appear verbatim where possible"""


_HOOK_PROMPT_TEMPLATE = """Write {request_count} scroll-stopping Meta ad hooks using the D+C+PS framework.

## Creative Pattern (what's working in this account)
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

## Avatar — Core Desires & Conflicts
Use this to identify the specific D, C, and PS for each hook.
{avatar_context}

## Visual Context (if available)
{visual_context}

## Winning hooks from YOUR account history (same pattern)
Study the angle, specificity, and emotional register. Write better versions — never copies.
{example_hooks}

## HARD RULES (violations will be rejected)
1. PRODUCT NAME REQUIRED — every hook must name the actual product: {product_name}.
   Do NOT use placeholders like [fruit], [product], [brand], or [X].
2. NO COPYING — do not reuse the opening words of any example hook.
   Forbidden openings (do not start with these):
{forbidden_openings}
3. NO BRACKETS — no [placeholder] text of any kind.
4. ONE ARCHETYPE PER HOOK — label each hook with its archetype (A1–A12).
5. LOOP NEVER COMPLETED — the hook must not answer its own question.
6. AD-FIRST — the PS must tease a product purchase/experience, not content consumption.

## ARCHETYPE DISTRIBUTION
Spread your {request_count} hooks across AT LEAST 6 different archetypes (A1–A12).
Use A11 or A12 (Carousel) for at least one hook if count ≥ 8.
Never use the same archetype twice with the same opening structure.

## Output format — return a JSON object with a "hooks" array
Each hook object must have ALL of these fields:
{{
  "text":      "The full hook text (no character limit — use as many words as the idea needs)",
  "archetype": "A3 — PS → Conflict → Desire",
  "d":         "Desire: one sentence describing what the avatar wants",
  "c":         "Conflict: one sentence naming the specific obstacle",
  "ps":        "Partial Solution: one sentence on what is teased without completing the loop",
  "clarity":   "One sentence explaining why a 4-year-old would instantly understand this hook"
}}

Example output shape:
{{"hooks": [
  {{
    "text": "...",
    "archetype": "A4 — I thought X until Y",
    "d": "To taste a cherimoya at perfect ripeness",
    "c": "The grower has been picking it 5–7 days early without knowing",
    "ps": "The timing mistake is identified but the fix is withheld",
    "clarity": "Anyone who has eaten chalky fruit recognises the problem immediately"
  }}
]}}

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


def _build_avatar_context(product_id: str | None, db: sqlite3.Connection) -> str:
    """Build a short avatar desires/conflicts block from product_benefits."""
    if not product_id:
        return ""
    rows = db.execute(
        """SELECT benefit_type, content FROM product_benefits
           WHERE product_id = ? ORDER BY priority DESC LIMIT 12""",
        (product_id,),
    ).fetchall()
    if not rows:
        return ""
    desires, pain_points, outcomes = [], [], []
    for r in rows:
        btype = r["benefit_type"] or ""
        content = r["content"] or ""
        if not content:
            continue
        if btype == "pain_point":
            pain_points.append(content)
        elif btype == "desired_outcome":
            outcomes.append(content)
        else:
            desires.append(content)

    lines = []
    if desires:
        lines.append("Desires / wants:\n" + "\n".join(f"  - {d}" for d in desires[:4]))
    if pain_points:
        lines.append("Conflicts / pain points:\n" + "\n".join(f"  - {p}" for p in pain_points[:3]))
    if outcomes:
        lines.append("Desired outcomes:\n" + "\n".join(f"  - {o}" for o in outcomes[:3]))
    return "\n\n".join(lines)


def _save_hooks(
    hooks: list[dict],
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
        if isinstance(hook, dict):
            hook_text    = hook.get("text", "").strip()
            archetype    = hook.get("archetype", "")
            dcp_d        = hook.get("d", "")
            dcp_c        = hook.get("c", "")
            dcp_ps       = hook.get("ps", "")
            dcp_clarity  = hook.get("clarity", "")
        else:
            hook_text   = str(hook).strip()
            archetype   = dcp_d = dcp_c = dcp_ps = dcp_clarity = ""
        if not hook_text:
            continue
        cursor = db.execute(
            """INSERT INTO generated_hooks
               (run_id, hook_text, hook_type, angle,
                based_on_creative_ids, based_on_pattern_id, product_id,
                archetype, dcp_d, dcp_c, dcp_ps, dcp_clarity)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, hook_text, hook_type, angle,
             json.dumps(creative_ids), pattern_id, product_id,
             archetype, dcp_d, dcp_c, dcp_ps, dcp_clarity),
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
    avatar_ctx = _build_avatar_context(product_id, db)

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
        avatar_context    = avatar_ctx or "No avatar data available — use product context to infer desires and conflicts.",
        visual_context    = visual_ctx,
        example_hooks     = "\n".join(f"- {h}" for h in example_hooks[:5]) or "None available.",
        product_name      = product_name,
        forbidden_openings = forbidden,
    )

    run_id = _create_run("hooks", example_ids, [pattern_id], product_id, dry_run, db, llm.model)

    raw = llm.complete_json(_SYSTEM_PROMPT, prompt, temperature=0.85)
    # The LLM returns a list of rich objects: {text, archetype, d, c, ps, clarity}
    raw_hook_objects: list = raw.get("hooks", raw.get("items", [])) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])

    # Extract plain text for QA filtering; keep objects aligned.
    raw_hook_texts: list[str] = []
    for h in raw_hook_objects:
        if isinstance(h, dict):
            raw_hook_texts.append(str(h.get("text", "")).strip())
        else:
            raw_hook_texts.append(str(h).strip())

    # QA filter — source_hooks = example_hooks so we don't re-surface winning copy verbatim.
    qa_cfg = QAConfig(
        similarity_threshold=similarity_threshold,
        diversity=diversity,
    )
    qa_results = filter_hooks(
        raw_hook_texts,
        source_hooks=example_hooks,
        config=qa_cfg,
        required_terms=required_terms,
    )
    qa_summary = summarise_qa(qa_results)

    # Rebuild passed hooks as rich dicts (re-attach original object data where available).
    text_to_obj: dict[str, dict] = {}
    for h in raw_hook_objects:
        if isinstance(h, dict):
            t = h.get("text", "").strip()
            if t:
                text_to_obj[t] = h

    hooks_rich: list[dict] = []
    for r in qa_results:
        if r.passed:
            obj = text_to_obj.get(r.hook, {"text": r.hook})
            if not isinstance(obj, dict):
                obj = {"text": r.hook}
            if "text" not in obj:
                obj["text"] = r.hook
            hooks_rich.append(obj)
    hooks_rich = hooks_rich[:count]

    # Plain text list for backwards-compat return value
    hooks = [h["text"] for h in hooks_rich]

    hook_ids = []
    if not dry_run:
        try:
            hook_ids = _save_hooks(
                hooks_rich, run_id,
                pattern.get("hook_type", "unknown"),
                pattern.get("angle", "unknown"),
                example_ids, pattern_id, product_id, db,
            )
            db.execute(
                "UPDATE generation_runs SET output_count=? WHERE id=?",
                (len(hooks), run_id),
            )
            db.commit()
        except Exception:
            # DB save failed (e.g. schema migration not yet applied).
            # Hooks were already generated — return them anyway.
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "generate_hooks_from_pattern: _save_hooks failed (schema migration pending?); "
                "returning %d hooks without persisting to generated_hooks",
                len(hooks),
                exc_info=True,
            )

    return {
        "run_id":      run_id,
        "hooks":       hooks,         # plain text list (backwards compat)
        "hooks_rich":  hooks_rich,    # list of {text, archetype, d, c, ps, clarity}
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
    total_rich = sum(len(r.get("hooks_rich", [])) for r in results)
    return {"patterns_used": len(patterns), "total_hooks": total, "total_hooks_rich": total_rich, "results": results}
