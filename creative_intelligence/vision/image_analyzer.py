"""
Vision analyzer — provider-agnostic interface + OpenAI implementation.

Architecture:
  VisionAnalyzer (abstract interface)
    └── OpenAIVisionAnalyzer  (production: uses gpt-4o vision)
    └── MockVisionAnalyzer    (testing: returns deterministic fixture data)

Usage:
    analyzer = get_analyzer()                     # auto-selects based on config
    result   = analyzer.analyze_url(url)          # returns VisualAttributes dict
    analyzer = get_analyzer(provider="mock")      # for tests

Never modifies data/raw/ or any src/ file.
All analysis is optional — failures return an error dict, not exceptions.
"""
from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from creative_intelligence import config
from creative_intelligence.db import get_connection
from creative_intelligence.vision import vision_prompts

ANALYSIS_VERSION = "1"


# ─────────────────────────────────────────────
# Interface
# ─────────────────────────────────────────────

class VisionAnalyzer(ABC):
    """Abstract vision analyzer. Swap providers by implementing this interface."""

    provider: str = "unknown"
    model: str = "unknown"

    @abstractmethod
    def analyze_url(self, image_url: str, asset_type: str = "image") -> dict[str, Any]:
        """Analyze an image URL and return structured visual attributes.

        On success: returns dict with all visual attribute fields.
        On failure: returns {"error": "...", all other fields: None}.
        """

    def _validate_result(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Ensure result contains only valid enum values. Coerce invalid to 'unknown'."""
        result = dict(raw)
        for field, allowed in vision_prompts.ALLOWED_VALUES.items():
            val = result.get(field)
            if val is not None and val not in allowed:
                result[field] = "unknown"
        # Booleans
        for bool_field in ("text_overlay_presence", "face_presence"):
            val = result.get(bool_field)
            if val is not None:
                result[bool_field] = bool(val)
        # face_count
        fc = result.get("face_count")
        if fc is not None:
            try:
                result["face_count"] = int(fc)
            except (TypeError, ValueError):
                result["face_count"] = 0
        # scroll_stopping_elements
        sse = result.get("scroll_stopping_elements")
        if isinstance(sse, list):
            result["scroll_stopping_elements"] = json.dumps(sse[:5])
        elif not isinstance(sse, str):
            result["scroll_stopping_elements"] = "[]"
        return result


# ─────────────────────────────────────────────
# Mock provider (for tests / dry runs)
# ─────────────────────────────────────────────

class MockVisionAnalyzer(VisionAnalyzer):
    provider = "mock"
    model    = "mock-v1"

    def analyze_url(self, image_url: str, asset_type: str = "image") -> dict[str, Any]:
        return self._validate_result({
            "visual_format":           "ugc",
            "subject_type":            "person_and_product",
            "shot_type":               "close_up",
            "composition_style":       "handheld",
            "background_type":         "home",
            "text_overlay_presence":   True,
            "text_overlay_density":    "medium",
            "branding_visibility":     "low",
            "product_visibility":      "high",
            "face_presence":           True,
            "face_count":              1,
            "emotion_or_expression":   "happy",
            "visual_energy":           "medium",
            "color_feel":              "bright",
            "scroll_stopping_elements": ["person holding product", "text overlay"],
            "visual_hook_description": "Person holding product with visible enthusiasm.",
            "visual_summary":          "Mock analysis — UGC style, person with product in hand.",
        })


# ─────────────────────────────────────────────
# OpenAI Vision provider
# ─────────────────────────────────────────────

class OpenAIVisionAnalyzer(VisionAnalyzer):
    provider = "openai"

    def __init__(self, model: str | None = None):
        self.model = model or config.CI_LLM_MODEL or "gpt-4o"

    def analyze_url(self, image_url: str, asset_type: str = "image") -> dict[str, Any]:
        try:
            import openai
        except ImportError:
            return {"error": "openai package not installed"}

        if not config.OPENAI_API_KEY:
            return {"error": "OPENAI_API_KEY not set"}

        prompt = (
            vision_prompts.ANALYSIS_PROMPT
            if asset_type == "image"
            else vision_prompts.THUMBNAIL_PROMPT
        )

        try:
            client = openai.OpenAI(api_key=config.OPENAI_API_KEY)
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": vision_prompts.SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": image_url, "detail": "low"}},
                        ],
                    },
                ],
                temperature=0,
                max_tokens=600,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or "{}"
            raw = json.loads(content)
            return self._validate_result(raw)
        except Exception as e:
            return {"error": str(e)}


# ─────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────

def get_analyzer(provider: str | None = None) -> VisionAnalyzer:
    """Return the appropriate VisionAnalyzer.

    provider: 'openai' | 'mock' | None (auto-select based on config)
    """
    p = provider or ("openai" if config.OPENAI_API_KEY else "mock")
    if p == "openai":
        return OpenAIVisionAnalyzer()
    return MockVisionAnalyzer()


# ─────────────────────────────────────────────
# DB persistence
# ─────────────────────────────────────────────

def save_visual_attributes(
    creative_id: str,
    asset_url: str | None,
    asset_type: str,
    result: dict[str, Any],
    run_id: int | None,
    analyzer: VisionAnalyzer,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Upsert visual attributes for a creative into the DB."""
    db = conn or get_connection()
    now = datetime.utcnow().isoformat()

    # Delete existing entry for this creative+version (idempotent).
    db.execute(
        "DELETE FROM creative_visual_attributes WHERE creative_id = ? AND analysis_version = ?",
        (creative_id, ANALYSIS_VERSION),
    )

    db.execute(
        """INSERT INTO creative_visual_attributes (
            creative_id, run_id, asset_url, asset_type, analyzed_at,
            analysis_provider, analysis_model, analysis_version,
            visual_format, subject_type, shot_type, composition_style, background_type,
            text_overlay_presence, text_overlay_density, branding_visibility, product_visibility,
            face_presence, face_count, emotion_or_expression, visual_energy, color_feel,
            scroll_stopping_elements, visual_hook_description, visual_summary,
            raw_response_json, error
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            creative_id, run_id, asset_url, asset_type, now,
            analyzer.provider, analyzer.model, ANALYSIS_VERSION,
            result.get("visual_format"),
            result.get("subject_type"),
            result.get("shot_type"),
            result.get("composition_style"),
            result.get("background_type"),
            1 if result.get("text_overlay_presence") else 0,
            result.get("text_overlay_density"),
            result.get("branding_visibility"),
            result.get("product_visibility"),
            1 if result.get("face_presence") else 0,
            result.get("face_count"),
            result.get("emotion_or_expression"),
            result.get("visual_energy"),
            result.get("color_feel"),
            result.get("scroll_stopping_elements"),
            result.get("visual_hook_description"),
            result.get("visual_summary"),
            json.dumps(result),
            result.get("error"),
        ),
    )
    db.commit()


def analyze_creatives_batch(
    creative_records: list[dict[str, Any]],
    asset_url_map: dict[str, tuple[str | None, str]],
    dry_run: bool = True,
    provider: str | None = None,
    run_id: int | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Analyze a batch of creatives and persist results.

    Args:
        creative_records: list of {creative_id, ad_name, ...}
        asset_url_map:    {creative_id: (url, asset_type)}
        dry_run:          if True, use MockVisionAnalyzer and don't persist
        provider:         force a specific provider
        run_id:           optional FK to creative_visual_analysis_runs
        conn:             DB connection

    Returns summary dict.
    """
    analyzer = get_analyzer("mock" if dry_run else provider)
    db = conn or get_connection()

    processed = 0
    failed    = 0
    skipped   = 0

    for rec in creative_records:
        cid = rec["creative_id"]
        url, atype = asset_url_map.get(cid, (None, "none"))

        if not url:
            skipped += 1
            continue

        result = analyzer.analyze_url(url, atype)

        if not dry_run:
            save_visual_attributes(cid, url, atype, result, run_id, analyzer, db)

        if result.get("error"):
            failed += 1
        else:
            processed += 1

    return {
        "processed": processed,
        "failed":    failed,
        "skipped":   skipped,
        "dry_run":   dry_run,
        "provider":  analyzer.provider,
        "model":     analyzer.model,
    }
