"""
Creative Copilot — action dispatch + LLM prompts.

All copilot-specific generation lives here so app.py stays thin.
Reuses: llm_client, enricher.build_prompt_context_block,
        scorer._structural_score / _pattern_match_score, patterns.get_patterns.

Public API
----------
run_action(action_type, concept, product_id, params, conn, dry_run=False)
    → {"results": [...], "action_type": str, "metadata": dict}

score_concept(concept, conn)
    → {"structural": float, "pattern_match": float, "overall": float}

explain_concept(concept, product_id, conn)
    → str (markdown)
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from creative_intelligence.generation.llm_client import get_llm_client
from creative_intelligence.product_knowledge.enricher import build_prompt_context_block
from creative_intelligence.scoring.scorer import _structural_score, _pattern_match_score
from creative_intelligence.analysis.patterns import get_patterns


# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────

_HOOK_ACTIONS = {
    "rewrite",
    "variants",
    "premium",
    "direct_response",
    "curiosity",
    "mainstream",
    "adapt_audience",
}

_RICH_ACTIONS = {
    "ugc_concepts",
    "static_concepts",
    "script",
    "creator_brief",
}

_ALL_ACTIONS = _HOOK_ACTIONS | _RICH_ACTIONS


# ─────────────────────────────────────────────
# System prompt (shared across all hook transforms)
# ─────────────────────────────────────────────

_HOOK_SYSTEM = """\
You are an expert direct-response ad copywriter specialising in Meta/Facebook/Instagram ads.
You write concise, scroll-stopping hooks and copy for premium specialty food e-commerce.

Rules that ALWAYS apply:
- Use specific product claims from the context (origin, variety, flavour, texture, harvest method).
- Use the product name AT MOST ONCE per hook. After that, use "it", "them", "our", or a descriptor.
- NEVER write a hook that could apply to any fruit/food — every hook must be ownable to THIS product.
- NEVER use placeholder brackets like [product], [benefit], or [name].
- Keep hooks 20–120 characters unless told otherwise.
- Write in plain English — no emojis unless the brief specifically asks.
- Output ONLY valid JSON — no markdown, no explanation outside the JSON.
"""


# ─────────────────────────────────────────────
# Per-action prompt builders
# ─────────────────────────────────────────────

def _hook_transform_prompt(
    action_type: str,
    concept: str,
    product_ctx: str,
    params: dict[str, Any],
) -> tuple[str, str]:
    """Return (system, user) prompts for single-hook transform actions."""
    count: int = int(params.get("count", 1))
    audience: str = params.get("audience", "").strip()
    tone: str = params.get("tone", "authentic").strip()

    ctx_block = f"\n\nProduct context:\n{product_ctx}" if product_ctx else ""

    audience_note = f"\nTarget audience: {audience}" if audience else ""
    tone_note = f"\nTone: {tone}" if tone else ""

    instructions: dict[str, str] = {
        "rewrite": (
            "Rewrite the concept below. Keep the same core idea and product reference but "
            "make the language fresher — vary sentence structure, rhythm, and word choice."
        ),
        "variants": (
            f"Generate {count} distinct variants of the concept below. "
            f"Use a DIFFERENT structural pattern for each: e.g. question, bold claim, social proof, "
            f"pain point, curiosity gap, origin story, sensory detail. "
            f"No two variants may share the same opening structure or angle. "
            f"Each must use at least one specific product claim from the context."
        ),
        "premium": (
            "Rewrite the concept using more premium, aspirational, quality-focused language. "
            "Evoke exclusivity, craftsmanship, or elevated sensory experience."
        ),
        "direct_response": (
            "Rewrite for maximum direct-response conversion. Add urgency, specificity, "
            "and a clear reason to act now. Make it punchy and CTA-ready."
        ),
        "curiosity": (
            "Rewrite to maximise the curiosity gap. The hook should create an open loop "
            "that compels the reader to find out more — without being clickbait."
        ),
        "mainstream": (
            "Rewrite to be more broadly accessible. Remove niche jargon, exotic references, "
            "or specialist language. Make it appeal to a general mainstream audience."
        ),
        "adapt_audience": (
            f"Adapt the concept specifically for this audience: {audience or 'general consumers'}. "
            "Adjust language, references, and pain points to resonate with them."
        ),
    }

    instruction = instructions.get(action_type, "Rewrite this concept.")

    user = (
        f"{instruction}{audience_note}{tone_note}{ctx_block}\n\n"
        f"Original concept:\n{concept}\n\n"
        f"Output JSON: {{\"hooks\": [<{count} string(s)>]}}"
    )
    return _HOOK_SYSTEM, user


def _ugc_concepts_prompt(
    concept: str,
    product_ctx: str,
    params: dict[str, Any],
) -> tuple[str, str]:
    count = int(params.get("count", 3))
    audience = params.get("audience", "").strip()
    ctx_block = f"\n\nProduct context:\n{product_ctx}" if product_ctx else ""
    audience_note = f"\nTarget audience: {audience}" if audience else ""

    system = """\
You are an expert UGC (user-generated content) creative strategist for Meta ads.
You develop creator briefs and UGC video concepts that feel authentic, not scripted.
Output ONLY valid JSON — no markdown fence, no explanation outside the JSON.
"""
    user = (
        f"Turn the hook/concept below into {count} distinct UGC video ad concepts. "
        f"Each concept should work as a short (15–45 second) talking-head or POV video.{audience_note}"
        f"{ctx_block}\n\n"
        f"Hook/concept:\n{concept}\n\n"
        f"Output JSON with this exact shape:\n"
        f'{{"concepts": [{count} objects, each with: "title" (str), "hook" (str, the opening line), '
        f'"story_arc" (str, 2-3 sentence narrative), "cta" (str), "format" (str: talking-head|POV|b-roll|demo)}}]}}'
    )
    return system, user


def _static_concepts_prompt(
    concept: str,
    product_ctx: str,
    params: dict[str, Any],
) -> tuple[str, str]:
    count = int(params.get("count", 3))
    ctx_block = f"\n\nProduct context:\n{product_ctx}" if product_ctx else ""

    system = """\
You are an expert static ad creative director for Meta/Instagram feeds and Stories.
You design scroll-stopping static image ads with punchy copy.
Output ONLY valid JSON — no markdown fence, no explanation outside the JSON.
"""
    user = (
        f"Turn the hook/concept below into {count} distinct static ad creative concepts.{ctx_block}\n\n"
        f"Hook/concept:\n{concept}\n\n"
        f"Output JSON:\n"
        f'{{"concepts": [{count} objects, each with: "title" (str), '
        f'"visual_description" (str, 1-2 sentences describing the image/layout), '
        f'"headline" (str, ≤8 words), "body_copy" (str, 1-2 punchy sentences), '
        f'"cta" (str, button text)}}]}}'
    )
    return system, user


def _script_prompt(
    concept: str,
    product_ctx: str,
    params: dict[str, Any],
) -> tuple[str, str]:
    ctx_block = f"\n\nProduct context:\n{product_ctx}" if product_ctx else ""
    tone = params.get("tone", "authentic").strip()

    system = """\
You are a direct-response video ad scriptwriter specialising in 15–30 second Meta video ads.
You write tight, punchy scripts with strong hooks, clear value delivery, and hard CTAs.
Output ONLY valid JSON — no markdown fence, no explanation outside the JSON.
"""
    user = (
        f"Turn the concept below into a short video ad script (15–30 seconds). "
        f"Tone: {tone}.{ctx_block}\n\n"
        f"Concept:\n{concept}\n\n"
        f"Output JSON:\n"
        f'{{"script": {{"hook": (str, opening 1-2 sentences spoken on camera), '
        f'"body": (str, 2-4 sentences of value delivery), '
        f'"cta": (str, the closing call-to-action), '
        f'"duration": (str, estimated seconds e.g. "20s"), '
        f'"notes": (str, director/talent notes — optional production guidance)}}}}'
    )
    return system, user


def _creator_brief_prompt(
    concept: str,
    product_ctx: str,
    params: dict[str, Any],
) -> tuple[str, str]:
    ctx_block = f"\n\nProduct context:\n{product_ctx}" if product_ctx else ""
    audience = params.get("audience", "").strip()
    audience_note = f"\nTarget audience: {audience}" if audience else ""

    system = """\
You are a UGC campaign manager briefing creators for Meta ad campaigns.
You write clear, inspiring creator briefs that get great takes on the first try.
Output ONLY valid JSON — no markdown fence, no explanation outside the JSON.
"""
    user = (
        f"Write a creator brief for the concept below.{audience_note}{ctx_block}\n\n"
        f"Concept:\n{concept}\n\n"
        f"Output JSON:\n"
        f'{{"brief": {{"hook_direction": (str, what the hook should feel like and achieve), '
        f'"story_arc": (str, 3-act structure in 2-3 sentences), '
        f'"key_messages": (list of 3-5 str, core points the creator must hit), '
        f'"tone_dos": (list of 3 str, what the tone should feel like), '
        f'"tone_donts": (list of 3 str, what to avoid), '
        f'"cta": (str, exact or approximate closing CTA line), '
        f'"examples": (str, optional reference or inspiration note)}}}}'
    )
    return system, user


def _explain_prompt(
    concept: str,
    product_ctx: str,
    patterns: list[dict[str, Any]],
) -> tuple[str, str]:
    ctx_block = f"\n\nProduct context:\n{product_ctx}" if product_ctx else ""

    # Build a compact pattern summary
    pattern_lines = []
    for p in patterns[:5]:
        line = (
            f"- Pattern: {p.get('pattern_name', 'unknown')} | "
            f"Winners: {p.get('winner_count', 0)} | "
            f"Avg CTR: {p.get('avg_ctr') or 'n/a'}"
        )
        pattern_lines.append(line)
    pattern_block = "\n".join(pattern_lines) if pattern_lines else "No patterns available."

    system = """\
You are a creative strategist analysing Meta ad copy against historical winning patterns.
Be concise and specific — no fluff. Write in plain text (markdown OK).
"""
    user = (
        f"Analyse the concept below against these winning creative patterns.\n\n"
        f"Winning patterns:\n{pattern_block}{ctx_block}\n\n"
        f"Concept to analyse:\n{concept}\n\n"
        f"In 150–250 words:\n"
        f"1. Which pattern(s) does this most closely align with, and why?\n"
        f"2. What structural elements make it strong or weak?\n"
        f"3. One specific suggestion to better align it with the top pattern.\n"
    )
    return system, user


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def run_action(
    action_type: str,
    concept: str,
    product_id: str | None,
    params: dict[str, Any],
    conn: sqlite3.Connection,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Dispatch an action and return structured results.

    Returns
    -------
    {
        "results":     list[str | dict],  # strings for hook actions, dicts for rich actions
        "action_type": str,
        "metadata":    dict,              # angle, format, etc. where applicable
    }
    """
    if action_type not in _ALL_ACTIONS:
        raise ValueError(
            f"Unknown action_type {action_type!r}. "
            f"Valid actions: {sorted(_ALL_ACTIONS)}"
        )

    product_ctx = build_prompt_context_block(product_id, conn) if product_id else ""
    llm = get_llm_client(dry_run=dry_run)

    # ── Hook transform actions ──────────────────────────────
    if action_type in _HOOK_ACTIONS:
        count = int(params.get("count", 5 if action_type == "variants" else 1))
        params = {**params, "count": count}
        system, user = _hook_transform_prompt(action_type, concept, product_ctx, params)
        raw = llm.complete_json(system=system, user=user, temperature=0.85)
        hooks: list[str] = []
        if isinstance(raw, dict):
            hooks = raw.get("hooks", [])
        elif isinstance(raw, list):
            hooks = raw
        # Ensure they are strings
        hooks = [str(h).strip() for h in hooks if str(h).strip()]
        return {
            "results": hooks,
            "action_type": action_type,
            "metadata": {"count": len(hooks), "tone": params.get("tone", "")},
        }

    # ── Rich format actions ──────────────────────────────────
    if action_type == "ugc_concepts":
        system, user = _ugc_concepts_prompt(concept, product_ctx, params)
        raw = llm.complete_json(system=system, user=user, temperature=0.8)
        concepts: list[dict] = []
        if isinstance(raw, dict):
            concepts = raw.get("concepts", [])
        return {
            "results": concepts,
            "action_type": action_type,
            "metadata": {},
        }

    if action_type == "static_concepts":
        system, user = _static_concepts_prompt(concept, product_ctx, params)
        raw = llm.complete_json(system=system, user=user, temperature=0.8)
        concepts = []
        if isinstance(raw, dict):
            concepts = raw.get("concepts", [])
        return {
            "results": concepts,
            "action_type": action_type,
            "metadata": {},
        }

    if action_type == "script":
        system, user = _script_prompt(concept, product_ctx, params)
        raw = llm.complete_json(system=system, user=user, temperature=0.75)
        script: dict[str, Any] = {}
        if isinstance(raw, dict):
            script = raw.get("script", raw)
        return {
            "results": [script],
            "action_type": action_type,
            "metadata": {},
        }

    if action_type == "creator_brief":
        system, user = _creator_brief_prompt(concept, product_ctx, params)
        raw = llm.complete_json(system=system, user=user, temperature=0.75)
        brief: dict[str, Any] = {}
        if isinstance(raw, dict):
            brief = raw.get("brief", raw)
        return {
            "results": [brief],
            "action_type": action_type,
            "metadata": {},
        }

    # Should never reach here (validated above)
    raise ValueError(f"Unhandled action_type: {action_type!r}")


def score_concept(concept: str, conn: sqlite3.Connection) -> dict[str, Any]:
    """Score a concept string using structural + pattern match signals.

    Returns {"structural": float, "pattern_match": float, "overall": float}
    """
    import re as _re

    # ── Structural score ───────────────────────────────────────────────
    # copilot concepts are single-line hooks — treat whole text as hook_text.
    # Award partial credit for body/headline absence so hooks aren't all 30pts.
    text = concept.strip()
    score = 0.0

    # Hook presence and length (ideal 20–120 chars)
    if text:
        score += 30
        if 20 <= len(text) <= 120:
            score += 20
        elif len(text) <= 20:
            score += 5   # too short
        else:
            score += 10  # long but present

    # Specificity: contains a number
    if _re.search(r"\d", text):
        score += 10

    # Curiosity / open loop
    if "?" in text:
        score += 10

    # Urgency / action language
    urgency_kw = ("now", "today", "order", "get", "try", "grab", "shop", "limited", "only")
    if any(k in text.lower() for k in urgency_kw):
        score += 10

    # Social proof signals
    proof_kw = ("people", "customers", "everyone", "reviews", "rated", "best", "top")
    if any(k in text.lower() for k in proof_kw):
        score += 10

    # Sensory / concrete language
    sensory_kw = ("taste", "flavor", "flavour", "sweet", "creamy", "fresh", "crisp",
                  "juicy", "rich", "buttery", "tangy", "crunchy", "tender", "ripe")
    if any(k in text.lower() for k in sensory_kw):
        score += 10

    structural = round(min(score, 100), 1)

    # ── Pattern match ──────────────────────────────────────────────────
    try:
        winning = get_patterns(min_winners=1, conn=conn)
    except Exception:
        winning = []

    # Use the full rule tagger so copilot hooks get the same rich classification
    # as historical creatives (covers comparison, story, transformation, curiosity, etc.)
    # rather than collapsing to quality_authenticity by default.
    from creative_intelligence.tagging.rule_tagger import tag_creative
    tags = tag_creative({"hook_text": text})

    pattern_match = _pattern_match_score(tags, winning)
    overall = round(structural * 0.6 + pattern_match * 0.4, 1)
    return {
        "structural": structural,
        "pattern_match": round(pattern_match, 1),
        "overall": overall,
    }


def explain_concept(
    concept: str,
    product_id: str | None,
    conn: sqlite3.Connection,
    dry_run: bool = False,
) -> str:
    """Return markdown explanation of how concept aligns with winning patterns."""
    product_ctx = build_prompt_context_block(product_id, conn) if product_id else ""
    try:
        winning = get_patterns(min_winners=1, conn=conn)
    except Exception:
        winning = []

    system, user = _explain_prompt(concept, product_ctx, winning)
    llm = get_llm_client(dry_run=dry_run)
    return llm.complete(system=system, user=user, temperature=0.4)
