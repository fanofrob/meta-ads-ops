"""
AI-assisted creative tagger (LLM-based).

Uses OpenAI to classify creatives across the same tag dimensions as rule_tagger,
but with richer understanding of nuanced copy.

Only runs when:
  - config.AI_TAGGING_ENABLED is True (CI_AI_TAGGING=1)
  - OPENAI_API_KEY is set
  - The creative has meaningful copy text

Outputs the same tag dict format as rule_tagger so the two can be composed:
  {creative_id, tag_type, tag_value, confidence, source}

The AI tagger runs AFTER the rule tagger. It only re-classifies tags where
the rule tagger returned "unknown" or low-confidence results.
"""
from __future__ import annotations

import json
from typing import Any

from creative_intelligence import config

_TAG_TYPES = [
    "hook_type",
    "angle",
    "format",
    "archetype",
    "emotional_trigger",
    "offer_style",
    "cta_type",
]

_VALID_VALUES: dict[str, list[str]] = {
    "hook_type":         ["curiosity", "pain_point", "social_proof", "authority",
                          "direct_offer", "story", "shock", "transformation", "question", "unknown"],
    "angle":             ["problem_solution", "aspiration", "fear", "authority_demo",
                          "ugc_testimonial", "comparison", "lifestyle", "urgency", "discount", "unknown"],
    "format":            ["image", "video", "carousel", "collection", "dco", "unknown"],
    "archetype":         ["direct_response", "brand", "ugc", "demo", "testimonial",
                          "educational", "social_proof", "lifestyle", "unknown"],
    "emotional_trigger": ["fear", "curiosity", "desire", "trust", "urgency", "social_proof", "pride", "unknown"],
    "offer_style":       ["hard_offer", "soft_offer", "brand", "free_trial", "discount",
                          "bundle", "guarantee", "no_offer", "unknown"],
    "cta_type":          ["shop_now", "learn_more", "get_offer", "sign_up", "book_now",
                          "download", "watch_more", "contact", "order_now", "other"],
}

_SYSTEM_PROMPT = """You are an expert direct-response ad creative analyst.
Classify the given ad creative according to the specified tag dimensions.
Return ONLY a valid JSON object. No explanation. No markdown.
Each value must be one of the allowed values listed for that dimension."""

_USER_PROMPT_TEMPLATE = """Ad creative copy:
---
Ad name: {ad_name}
Hook / opening: {hook_text}
Primary text: {primary_text}
Headline: {headline}
CTA: {cta}
Format: {format}
---

Classify this creative. Return a JSON object with exactly these keys:
{tag_types}

Allowed values per key:
{allowed_values}
"""


def _call_openai(prompt: str, model: str) -> dict[str, Any]:
    """Call OpenAI chat completions and return parsed JSON response."""
    try:
        import openai
    except ImportError:
        raise RuntimeError("openai package required for AI tagging. pip install openai")

    client = openai.OpenAI(api_key=config.OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ],
        temperature=0,
        max_tokens=300,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or "{}"
    return json.loads(content)


def _build_prompt(record: dict[str, Any]) -> str:
    tag_types_str = json.dumps(_TAG_TYPES, indent=2)
    allowed_str   = json.dumps(_VALID_VALUES, indent=2)
    return _USER_PROMPT_TEMPLATE.format(
        ad_name      = record.get("ad_name", ""),
        hook_text    = record.get("hook_text", ""),
        primary_text = record.get("primary_text", ""),
        headline     = record.get("headline", ""),
        cta          = record.get("cta", ""),
        format       = record.get("format", ""),
        tag_types    = tag_types_str,
        allowed_values = allowed_str,
    )


def _has_meaningful_copy(record: dict[str, Any]) -> bool:
    fields = ["hook_text", "primary_text", "headline", "ad_name"]
    text = " ".join((record.get(f) or "") for f in fields).strip()
    return len(text) >= 15


def _validate_tags(raw: dict[str, Any]) -> dict[str, tuple[str, float]]:
    """Return {tag_type: (tag_value, confidence)} with fallback for invalid values."""
    result: dict[str, tuple[str, float]] = {}
    for tag_type in _TAG_TYPES:
        val = str(raw.get(tag_type, "unknown")).strip().lower().replace(" ", "_")
        allowed = _VALID_VALUES.get(tag_type, [])
        if val in allowed:
            result[tag_type] = (val, 0.85)
        else:
            result[tag_type] = ("unknown", 0.0)
    return result


def tag_creative_ai(
    record: dict[str, Any],
    existing_tags: dict[str, str] | None = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    """Tag a single creative using the LLM.

    Args:
        record: creative record dict
        existing_tags: dict of {tag_type: tag_value} from rule tagger
        model: OpenAI model to use (defaults to config.CI_LLM_MODEL)

    Returns list of tag dicts (same format as rule_tagger.tag_all).
    Only re-classifies tags where existing_tags has "unknown" or is missing.
    """
    if not config.AI_TAGGING_ENABLED:
        return []
    if not config.OPENAI_API_KEY:
        return []
    if not _has_meaningful_copy(record):
        return []

    # Only run for tags the rule tagger couldn't resolve.
    unknown_types = [
        t for t in _TAG_TYPES
        if not existing_tags or existing_tags.get(t, "unknown") == "unknown"
    ]
    if not unknown_types:
        return []

    prompt = _build_prompt(record)
    _model = model or config.CI_LLM_MODEL

    try:
        raw_result = _call_openai(prompt, _model)
    except Exception:
        return []

    validated = _validate_tags(raw_result)
    cid = record.get("creative_id") or record.get("ad_id", "")

    return [
        {
            "creative_id": cid,
            "tag_type":    tag_type,
            "tag_value":   val,
            "confidence":  conf,
            "source":      "ai",
        }
        for tag_type, (val, conf) in validated.items()
        if tag_type in unknown_types and val != "unknown"
    ]


def tag_all_ai(
    records: list[dict[str, Any]],
    existing_tags_by_creative: dict[str, dict[str, str]] | None = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    """AI-tag a batch of creative records.

    existing_tags_by_creative: {creative_id: {tag_type: tag_value}}
    Only processes records with meaningful copy.
    """
    results = []
    for rec in records:
        cid = rec.get("creative_id") or rec.get("ad_id", "")
        existing = (existing_tags_by_creative or {}).get(cid)
        tags = tag_creative_ai(rec, existing, model)
        results.extend(tags)
    return results
