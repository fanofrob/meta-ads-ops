"""
Production brief builders.

Turns an approved copilot concept into a structured production-ready document.

Key distinction from copilot_actions.py:
  - copilot actions are GENERATIVE (create new concepts / refinements)
  - brief builders are ELABORATIVE (expand an approved concept into a full brief)

Public API
----------
build_static_brief(concept, product_id, conn, ...)
build_ugc_brief(concept, product_id, conn, ...)
build_script_package(concept, product_id, conn, ...)
build_test_package(concept, product_id, session_id, conn, ...)
    → all return {"output_id|package_id": int|None, "output_type": str,
                  "data": dict, "output_md": str}

dry_run=True  → skips DB writes (default for direct builder calls)
dry_run=False → persists to production_outputs or production_packages
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from creative_intelligence.generation.llm_client import get_llm_client
from creative_intelligence.product_knowledge.enricher import build_prompt_context_block
from creative_intelligence.analysis.patterns import get_patterns
from creative_intelligence.production.schemas import (
    StaticAdBrief,
    UGCCreatorBrief,
    ScriptPackage,
    VideoBrief,
    empty_static_brief,
    empty_ugc_brief,
    empty_script_package,
)
from creative_intelligence.production.exporters import to_markdown


# ─────────────────────────────────────────────
# Prompt builders (return system, user tuple)
# ─────────────────────────────────────────────

def _static_brief_prompt(
    concept: str,
    product_ctx: str,
) -> tuple[str, str]:
    system = (
        "You are an expert direct-response ad designer specialising in DTC food and produce brands. "
        "You create precise, production-ready static ad briefs for Meta (Facebook/Instagram) placements. "
        "Your briefs are explicit enough for a designer who has never seen the product to execute without follow-up questions. "
        "Rules: use the product name at most once per section. "
        "Never use placeholder text, brackets, or ellipses. "
        "All copy must be concrete and specific to this product. "
        f'Return ONLY a JSON object matching this schema:\n{StaticAdBrief.SCHEMA_HINT}'
    )
    user = (
        f"Product context:\n{product_ctx}\n\n"
        f"Approved concept / hook:\n{concept}\n\n"
        "Write a complete static ad brief for this concept. "
        "The hook field must be the concept verbatim. "
        "Headline options should be 3 distinct general angles, each ≤8 words. "
        "variant_headlines must include all 8 keys (minimal, bold_type, benefit_stack, social_proof, origin_story, lifestyle_tagline, direct_response, premium) — each a unique headline written specifically for that ad format:\n"
        "  minimal: clean, understated, product-forward, ≤6 words\n"
        "  bold_type: ultra-short, 1-3 words, all-caps energy, text IS the visual\n"
        "  benefit_stack: specific, benefit-led, what the customer GETS, ≤8 words\n"
        "  social_proof: quote-style or third-person proof framing, ≤8 words\n"
        "  origin_story: provenance or origin angle (where it's grown/made), ≤8 words\n"
        "  lifestyle_tagline: emotional brand statement, how it fits their life, ≤7 words\n"
        "  direct_response: action + urgency, conversion-focused, ≤8 words\n"
        "  premium: elegant, aspirational, quality-positioning, ≤6 words\n"
        "Body options should each be 1-2 punchy sentences that could stand alone as primary text. "
        "Visual direction and composition notes should be specific enough for a designer to brief a photographer or illustrator. "
        "Why it works should reference the emotional hook and why this angle resonates for this product category."
    )
    return system, user


def _ugc_brief_prompt(
    concept: str,
    product_ctx: str,
) -> tuple[str, str]:
    system = (
        "You are a UGC creator coach who briefs creators for DTC food and produce brands. "
        "Your briefs are conversational, specific, and immediately actionable for a creator. "
        "Rules: use the product name at most once in the brief. "
        "Never use placeholder text, brackets, or ellipses. "
        "The opening_line should be close to word-for-word what the creator should say. "
        "no_go_notes must reflect real brand guardrails, not generic advice. "
        f'Return ONLY a JSON object matching this schema:\n{UGCCreatorBrief.SCHEMA_HINT}'
    )
    user = (
        f"Product context:\n{product_ctx}\n\n"
        f"Approved concept / hook:\n{concept}\n\n"
        "Write a complete UGC creator brief for this concept. "
        "Creator persona should reflect who would authentically talk about this product. "
        "Talking points should be ordered naturally (hook → value → proof → CTA). "
        "Demo beats should describe physical actions on camera, not abstract ideas. "
        "Scene suggestions should be practical, low-production environments. "
        "no_go_notes should include at least one guardrail specific to premium produce (e.g., avoid grocery store comparisons unless intentional)."
    )
    return system, user


def _script_package_prompt(
    concept: str,
    product_ctx: str,
) -> tuple[str, str]:
    system = (
        "You are a short-form video scriptwriter specialising in DTC food brands. "
        "You write punchy, conversion-focused scripts for 15-30 second Meta video ads. "
        "Your beat structures are precise and follow hook → value → proof → CTA rhythm. "
        "Rules: use the product name at most once in the main script. "
        "Never use placeholder text or brackets. "
        "All spoken lines must be natural, not ad-speak. "
        f'Return ONLY a JSON object matching this schema:\n{ScriptPackage.SCHEMA_HINT}'
    )
    user = (
        f"Product context:\n{product_ctx}\n\n"
        f"Approved concept / hook:\n{concept}\n\n"
        "Write a complete script package for this concept. "
        "The hook field should be the opening 1-2 sentences spoken on camera (may adapt the approved concept slightly for natural delivery). "
        "Beat structure should have 3-4 beats with accurate second ranges that sum to the total duration. "
        "Alternate hooks should each try a different angle (curiosity / comparison / statement). "
        "Alternate CTAs should vary the urgency and tone. "
        "Production notes should mention: ideal shot type, pacing, whether B-roll is needed, and any specific visual moment to capture."
    )
    return system, user


def _video_brief_prompt(
    concept: str,
    product_ctx: str,
    video_type: str = "ugc",
) -> tuple[str, str]:
    """Build a unified video brief prompt that merges UGC creator + script direction."""
    from creative_intelligence.video.types import get_config, VIDEO_TYPE_LABELS
    cfg = get_config(video_type)
    type_label = VIDEO_TYPE_LABELS.get(video_type, "UGC")

    system = (
        f"You are a video creative director and UGC creator coach specialising in DTC food brands. "
        f"You write unified video briefs that work for both human UGC creators and automated storyboard generation. "
        f"This brief is for a {type_label} ad format. "
        f"Rules: use the product name at most once in the main content. "
        f"Never use placeholder text, brackets, or ellipses. "
        f"The hook must be the exact opening line — word-for-word what should be said or shown first. "
        f"Beat structure scenes must sum to the estimated duration. "
        f"All spoken lines must sound natural, not like ad-copy. "
        f'Return ONLY a JSON object matching this schema:\n{VideoBrief.SCHEMA_HINT}'
    )

    # Type-specific guidance
    type_notes = {
        "ugc": (
            "Creator persona should reflect who would authentically discuss this product. "
            "Opening hook is spoken directly to camera. "
            "Demo beats describe physical actions the creator performs on camera. "
            "Pacing is fast — 3-5s per beat. Shot style is handheld, authentic."
        ),
        "farm_origin": (
            "This is a provenance / origin story format. "
            "Opening is an establishing visual (farm or orchard scene), not direct-to-camera. "
            "Beats follow: establishing → harvest → quality → CTA. "
            "Tone is warm, premium, cinematic. Pacing is slower, 5-8s per beat."
        ),
        "product_hero": (
            "Product is the star — minimal creator presence. "
            "Opening is a macro/beauty shot of the product. "
            "Beats follow: macro reveal → detail → benefit → CTA. "
            "Tone is clean and premium. Production notes must specify studio-quality lighting."
        ),
        "comparison_reveal": (
            "This is a contrast/reveal format. "
            "Opening hook shows the problem or the inferior alternative — sharp, slightly uncomfortable. "
            "Beats follow: hook (problem) → contrast → reveal (product) → proof → CTA. "
            "Text overlays should be prominent and punchy. CTA leverages the contrast."
        ),
    }.get(video_type, "")

    user = (
        f"Product context:\n{product_ctx}\n\n"
        f"Approved concept / hook:\n{concept}\n\n"
        f"Video format: {type_label}\n"
        f"{type_notes}\n\n"
        "Write a complete video brief for this concept. "
        "The hook field must be the opening line verbatim (or very close). "
        "Talking points should be ordered naturally (hook → value → proof → CTA). "
        "Demo beats describe physical actions on camera — not abstract ideas. "
        "Beat structure must have 3-4 beats with realistic second ranges that sum to the duration. "
        "Production notes must specify: ideal shot type, pacing, whether B-roll is needed, "
        "any key visual moment, and lighting style. "
        "No-go notes must include at least one brand-specific guardrail for premium produce. "
        "Alternate hooks should try meaningfully different angles (curiosity / contrast / statement)."
    )
    return system, user


# ─────────────────────────────────────────────
# LLM call + parse helpers
# ─────────────────────────────────────────────

def _call_llm(
    system: str,
    user: str,
    dry_run: bool,
) -> dict[str, Any]:
    llm = get_llm_client(dry_run=dry_run)
    raw = llm.complete_json(system=system, user=user, temperature=0.5)
    # LLM may return {"brief": {...}} or {"script_package": {...}} or the dict directly
    if isinstance(raw, dict):
        for key in ("brief", "script_package", "output"):
            if key in raw and isinstance(raw[key], dict):
                return raw[key]
    return raw if isinstance(raw, dict) else {}


def _ensure_lists(data: dict, list_fields: tuple[str, ...]) -> dict:
    """Ensure specified fields are lists (coerce strings to single-item lists)."""
    for field in list_fields:
        if field in data and isinstance(data[field], str):
            data[field] = [data[field]] if data[field] else []
    return data


# ─────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────

def _persist_output(
    output_type: str,
    source_iteration_id: int | None,
    session_id: str | None,
    product_id: str | None,
    concept_text: str,
    data: dict,
    output_md: str,
    conn: sqlite3.Connection,
) -> int:
    cursor = conn.execute(
        """INSERT INTO production_outputs
           (output_type, source_iteration_id, session_id, product_id,
            concept_text, output_json, output_md)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            output_type,
            source_iteration_id,
            session_id,
            product_id,
            concept_text,
            json.dumps(data),
            output_md,
        ),
    )
    conn.commit()
    return cursor.lastrowid


def _persist_package(
    session_id: str | None,
    product_id: str | None,
    core_concept: str,
    core_iteration_id: int | None,
    variants: list[dict],
    audience: str,
    goal: str,
    pattern_alignment: dict,
    score_summary: dict,
    output_md: str,
    conn: sqlite3.Connection,
) -> int:
    cursor = conn.execute(
        """INSERT INTO production_packages
           (session_id, product_id, core_concept, core_iteration_id,
            variants_json, audience, goal,
            pattern_alignment_json, score_summary_json, output_md)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session_id,
            product_id,
            core_concept,
            core_iteration_id,
            json.dumps(variants),
            audience,
            goal,
            json.dumps(pattern_alignment),
            json.dumps(score_summary),
            output_md,
        ),
    )
    conn.commit()
    return cursor.lastrowid


# ─────────────────────────────────────────────
# Public builders
# ─────────────────────────────────────────────

def build_static_brief(
    concept: str,
    product_id: str | None,
    conn: sqlite3.Connection,
    source_iteration_id: int | None = None,
    session_id: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Generate a static ad brief from an approved concept.

    Returns
    -------
    {
      "output_id"   : int | None,   # None when dry_run=True
      "output_type" : "static_brief",
      "data"        : StaticAdBrief dict,
      "output_md"   : str,          # Markdown for human handoff
    }
    """
    product_ctx = build_prompt_context_block(product_id, conn) if product_id else ""
    system, user = _static_brief_prompt(concept, product_ctx)
    data = _call_llm(system, user, dry_run)

    # Normalise
    data.setdefault("hook", concept)
    data = _ensure_lists(data, ("headline_options", "body_options"))
    # variant_headlines should be a dict; if LLM returned a list, ignore it
    if not isinstance(data.get("variant_headlines"), dict):
        data["variant_headlines"] = {}
    for field, default in empty_static_brief().items():
        data.setdefault(field, default)

    output_md = to_markdown("static_brief", data)

    output_id = None
    if not dry_run:
        output_id = _persist_output(
            "static_brief", source_iteration_id, session_id,
            product_id, concept, data, output_md, conn,
        )

    return {"output_id": output_id, "output_type": "static_brief",
            "data": data, "output_md": output_md}


def build_ugc_brief(
    concept: str,
    product_id: str | None,
    conn: sqlite3.Connection,
    source_iteration_id: int | None = None,
    session_id: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Generate a UGC creator brief from an approved concept.

    Returns same shape as build_static_brief, output_type="ugc_brief".
    """
    product_ctx = build_prompt_context_block(product_id, conn) if product_id else ""
    system, user = _ugc_brief_prompt(concept, product_ctx)
    data = _call_llm(system, user, dry_run)

    data.setdefault("opening_line", concept)
    data = _ensure_lists(data, ("talking_points", "demo_beats", "scene_suggestions", "no_go_notes"))
    for field, default in empty_ugc_brief().items():
        data.setdefault(field, default)

    output_md = to_markdown("ugc_brief", data)

    output_id = None
    if not dry_run:
        output_id = _persist_output(
            "ugc_brief", source_iteration_id, session_id,
            product_id, concept, data, output_md, conn,
        )

    return {"output_id": output_id, "output_type": "ugc_brief",
            "data": data, "output_md": output_md}


def build_script_package(
    concept: str,
    product_id: str | None,
    conn: sqlite3.Connection,
    source_iteration_id: int | None = None,
    session_id: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Generate a script package from an approved concept.

    Returns same shape as build_static_brief, output_type="script_package".
    """
    product_ctx = build_prompt_context_block(product_id, conn) if product_id else ""
    system, user = _script_package_prompt(concept, product_ctx)
    data = _call_llm(system, user, dry_run)

    data.setdefault("hook", concept)
    data = _ensure_lists(data, ("alternate_hooks", "alternate_ctas"))
    if not isinstance(data.get("beat_structure"), list):
        data["beat_structure"] = []
    for field, default in empty_script_package().items():
        data.setdefault(field, default)

    output_md = to_markdown("script_package", data)

    output_id = None
    if not dry_run:
        output_id = _persist_output(
            "script_package", source_iteration_id, session_id,
            product_id, concept, data, output_md, conn,
        )

    return {"output_id": output_id, "output_type": "script_package",
            "data": data, "output_md": output_md}


def build_video_brief(
    concept: str,
    product_id: str | None,
    conn: sqlite3.Connection,
    source_iteration_id: int | None = None,
    session_id: str | None = None,
    video_type: str = "ugc",
    dry_run: bool = True,
) -> dict[str, Any]:
    """Generate a unified video brief from an approved concept.

    Merges the UGC creator brief and script package into a single format that
    feeds directly into storyboard generation for any video_type.

    Returns same shape as other builders: {output_id, output_type, data, output_md}.
    """
    from creative_intelligence.production.exporters import to_markdown as _to_md
    product_ctx = build_prompt_context_block(product_id, conn) if product_id else ""
    system, user = _video_brief_prompt(concept, product_ctx, video_type)
    data = _call_llm(system, user, dry_run)

    data.setdefault("hook", concept)
    data = _ensure_lists(data, ("talking_points", "demo_beats", "no_go_notes", "alternate_hooks"))
    if not isinstance(data.get("beat_structure"), list):
        data["beat_structure"] = []
    # Sensible defaults for all fields
    defaults = {
        "hook": concept,
        "talking_points": [],
        "demo_beats": [],
        "beat_structure": [],
        "cta": "",
        "duration": "25-30s",
        "creator_persona": "",
        "production_notes": "",
        "no_go_notes": [],
        "alternate_hooks": [],
        "video_type": video_type,
    }
    for field, default in defaults.items():
        data.setdefault(field, default)
    # Always store the video_type used
    data["video_type"] = video_type

    output_md = _to_md("video_brief", data)

    output_id = None
    if not dry_run:
        output_id = _persist_output(
            "video_brief", source_iteration_id, session_id,
            product_id, concept, data, output_md, conn,
        )

    return {"output_id": output_id, "output_type": "video_brief",
            "data": data, "output_md": output_md}


def _build_test_package_removed(*args, **kwargs) -> dict:  # type: ignore[return]
    raise ValueError("test_package has been removed. Use static_brief, ugc_brief, or script_package.")


def build_test_package(  # noqa: E501  — kept for backwards compatibility, raises on call
    concept: str,
    product_id: str | None,
    session_id: str | None,
    conn: sqlite3.Connection,
    source_iteration_id: int | None = None,
    audience: str = "",
    goal: str = "conversions",
    dry_run: bool = True,
) -> dict[str, Any]:
    """Assemble a test package from existing session iterations.

    No LLM call — pulls and scores sibling iterations from the session,
    fetches top winning pattern for alignment context, and bundles everything
    into a structured package with lineage/iteration_id references for
    future live-performance tracking.

    Returns
    -------
    {
      "package_id"  : int | None,   # None when dry_run=True
      "output_type" : "test_package",
      "data"        : TestPackage dict,
      "output_md"   : str,
    }
    """
    from creative_intelligence.generation.copilot_actions import score_concept

    # ── Score the core concept ──────────────────────────────────────
    core_score = score_concept(concept, conn)

    # ── Pull variants from session ──────────────────────────────────
    # Priority: favorited iterations → most recent hook-type iterations
    variants_raw: list[dict] = []
    if session_id:
        # Favorites first (exclude the core concept itself)
        fav_rows = conn.execute(
            """SELECT id, concept_text, action_type, predicted_score
               FROM copilot_iterations
               WHERE session_id = ? AND is_favorite = 1
                 AND action_type NOT IN ('ugc_concepts','static_concepts','script','creator_brief')
               ORDER BY created_at DESC LIMIT 8""",
            (session_id,),
        ).fetchall()

        # Fallback: recent hook iterations
        recent_rows = conn.execute(
            """SELECT id, concept_text, action_type, predicted_score
               FROM copilot_iterations
               WHERE session_id = ?
                 AND action_type NOT IN ('ugc_concepts','static_concepts','script','creator_brief')
               ORDER BY created_at DESC LIMIT 10""",
            (session_id,),
        ).fetchall()

        seen_texts: set[str] = {concept}
        for row in list(fav_rows) + list(recent_rows):
            row_d = dict(row)
            text = row_d.get("concept_text", "")
            if text and text not in seen_texts:
                seen_texts.add(text)
                variants_raw.append(row_d)
                if len(variants_raw) >= 5:
                    break

    # Score and annotate variants (preserve iteration_id for lineage)
    variants: list[dict] = []
    all_scores = [core_score["overall"]]
    for v in variants_raw:
        try:
            v_score = score_concept(v["concept_text"], conn)
        except Exception:
            v_score = {"structural": 0.0, "pattern_match": 0.0, "overall": 0.0}
        all_scores.append(v_score["overall"])
        variants.append({
            "concept_text":    v["concept_text"],
            "action_type":     v.get("action_type", "unknown"),
            "predicted_score": v.get("predicted_score") or v_score["overall"],
            "iteration_id":    v.get("id"),            # lineage reference
            "scores":          v_score,
        })

    # ── Score summary ───────────────────────────────────────────────
    score_summary = {
        "avg":   round(sum(all_scores) / len(all_scores), 1) if all_scores else 0.0,
        "min":   round(min(all_scores), 1) if all_scores else 0.0,
        "max":   round(max(all_scores), 1) if all_scores else 0.0,
        "count": len(all_scores),
    }

    # ── Pattern alignment ───────────────────────────────────────────
    pattern_alignment: dict[str, Any] = {}
    try:
        patterns = get_patterns(min_winners=1, conn=conn)
        if patterns:
            top = patterns[0]
            pattern_alignment = {
                "pattern_name":  top.get("pattern_name", ""),
                "hook_type":     top.get("hook_type", ""),
                "angle":         top.get("angle", ""),
                "winner_count":  top.get("winner_count", 0),
                "avg_roas":      top.get("avg_roas"),
                "avg_ctr":       top.get("avg_ctr"),
            }
    except Exception:
        pass

    # ── Product context (for rendering) ────────────────────────────
    product_context = build_prompt_context_block(product_id, conn) if product_id else ""

    data = {
        "core_concept":      concept,
        "core_iteration_id": source_iteration_id,
        "core_score":        core_score,
        "variants":          variants,
        "audience":          audience,
        "goal":              goal,
        "pattern_alignment": pattern_alignment,
        "score_summary":     score_summary,
        "product_context":   product_context,
    }

    output_md = to_markdown("test_package", data)

    package_id = None
    if not dry_run:
        package_id = _persist_package(
            session_id=session_id,
            product_id=product_id,
            core_concept=concept,
            core_iteration_id=source_iteration_id,
            variants=variants,
            audience=audience,
            goal=goal,
            pattern_alignment=pattern_alignment,
            score_summary=score_summary,
            output_md=output_md,
            conn=conn,
        )

    return {"package_id": package_id, "output_type": "test_package",
            "data": data, "output_md": output_md}


# ─────────────────────────────────────────────
# Dispatcher
# ─────────────────────────────────────────────

def build_output(
    output_type: str,
    concept: str,
    product_id: str | None,
    conn: sqlite3.Connection,
    session_id: str | None = None,
    source_iteration_id: int | None = None,
    audience: str = "",
    goal: str = "conversions",
    dry_run: bool = True,
) -> dict[str, Any]:
    """Dispatch to the appropriate builder by output_type.

    Raises ValueError for unknown output_type.
    """
    if output_type == "static_brief":
        return build_static_brief(concept, product_id, conn,
                                  source_iteration_id, session_id, dry_run)
    elif output_type == "video_brief":
        return build_video_brief(concept, product_id, conn,
                                 source_iteration_id, session_id,
                                 video_type="ugc", dry_run=dry_run)
    elif output_type == "ugc_brief":
        return build_ugc_brief(concept, product_id, conn,
                               source_iteration_id, session_id, dry_run)
    elif output_type == "script_package":
        return build_script_package(concept, product_id, conn,
                                    source_iteration_id, session_id, dry_run)
    else:
        raise ValueError(f"Unknown output_type: {output_type!r}")
