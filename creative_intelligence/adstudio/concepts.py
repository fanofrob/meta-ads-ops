"""
Diversity-seed concept generation for Ad Studio v2.

Claude invents DISTINCT, text-free image concepts for a product, using the 20
archetype families in formats.py as a menu of directions and the running log of
already-generated concepts as an explicit "do not repeat" list. Each concept is
then assembled into a text-free Nano Banana prompt (locked craft blocks, no copy).

This is the "evolve into diversity seeds" model: the archetypes guide variety
(distinct Meta Entity IDs), but every image is a fresh specific scene, never a
reused template, and carries no baked-in text.
"""
from __future__ import annotations

import json
import re
from typing import Any

from creative_intelligence.adstudio import formats as F
from creative_intelligence.adstudio.profiles import normalize_type, profile
from creative_intelligence.adstudio.chat import (
    _client, _image_blocks, _product_line, MODEL,
)

CONCEPT_SYSTEM = """\
You are an art director generating DIVERSE static ad IMAGE concepts (pure photography, NO text or copy) for a product. A reference photo is attached — the product must appear exactly as shown in every concept.

Your single objective is maximum visual diversity. Meta fingerprints visually similar images into one "Entity ID" and caps their audience reach; only substantially DIFFERENT compositions (flat-lay vs eye-level vs extreme macro vs human-in-frame vs wide environmental) each reach fresh audiences. So every concept must be a different composition family AND a different concrete scene.

You are given a menu of archetype directions and a log of concepts ALREADY generated for this product. Do NOT repeat, re-skin, or lightly vary anything in the log. Pick under-used directions and invent genuinely new scenes. Deliberately vary: camera angle and distance, indoor vs outdoor, day vs night, the surface and setting, the props, the palette, and whether a human hand or body is present.
"""


def _schema(n: int) -> str:
    return (
        'Return ONLY JSON: {"concepts":[ ' + str(n) + ' objects, each {'
        '"archetype":"one archetype key from the menu, or \\"freeform\\"",'
        '"title":"3-6 word scene name",'
        '"scene":"2-4 vivid, photographable sentences: exact composition, camera '
        'angle and distance, setting and surface, props, light, palette, and '
        'whether a hand/person is in frame. Concrete and specific. The image '
        'contains NO text/words."'
        '} ]}'
    )


def generate_concepts(product: dict[str, Any], n: int,
                      prior_concepts: list[dict[str, Any]],
                      image_data_uris: list[str] | None = None) -> list[dict]:
    """Return n distinct {archetype, title, scene} concepts avoiding prior ones."""
    c = _client()
    prof = profile(product)
    seeds = "\n".join(
        f"- {k}: {v['name']} — {v['blurb']}" for k, v in prof["formats"].items()
    )
    system = CONCEPT_SYSTEM + ("\n" + prof["concept_rules"] if prof["concept_rules"] else "")
    if prior_concepts:
        log = "\n".join(
            f"- [{x.get('archetype', '')}] {x.get('title', '')}: "
            f"{(x.get('concept') or '')[:160]}"
            for x in prior_concepts
        )
    else:
        log = "(none yet — this is the first batch, so establish a wide spread)"

    ask = (
        f"{_product_line(product)}\n\n"
        f"Archetype directions (your palette of seeds):\n{seeds}\n\n"
        f"Concepts ALREADY generated for this product — DO NOT repeat or lightly "
        f"vary any of these:\n{log}\n\n"
        f"Invent {n} NEW, mutually distinct image concepts. {_schema(n)}"
    )
    content = _image_blocks(image_data_uris or []) + [{"type": "text", "text": ask}]
    r = c.messages.create(
        model=MODEL, max_tokens=2600, system=system,
        messages=[{"role": "user", "content": content}],
    )
    text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
    return _parse(text)


def build_image_prompt(product: dict[str, Any], scene: str,
                       aspect_ratio: str = "4:5", corrections: str = "") -> str:
    """
    Assemble a text-free Nano Banana prompt for one concept scene.

    `corrections` — operator feedback from a retry (e.g. "the label is wrong;
    the cherries should be Rainier — blush-yellow, not red"). Placed at the very
    top as highest-priority instructions so the model actually fixes them.
    """
    prof = profile(product)
    is_candle = normalize_type(product.get("product_type")) == "candle"
    name = product.get("name", "the product")
    scent = (product.get("scent_notes") or "").strip()
    subject = name + (f" ({scent})" if scent else "")

    parts = [f"Create a premium {aspect_ratio} product photograph. {prof['reference_lock']}"]
    if corrections.strip():
        parts.append(
            "CORRECTIONS — HIGHEST PRIORITY\n"
            "A previous attempt got the following wrong. Fix each of these exactly, "
            "and prioritize them above every other instruction:\n"
            + corrections.strip()
        )
    parts.append(f"SUBJECT\n{subject}, unmistakably the hero of the image.")
    parts.append(f"SCENE / COMPOSITION\n{scene}")
    look = (product.get("physical_desc") or "").strip()
    varieties = (product.get("varieties") or "").strip()
    if not is_candle and look:
        parts.append(f"WHAT IT LOOKS LIKE\n{look}")
    if not is_candle and varieties:
        parts.append(f"VARIETIES — show these, clearly distinguishable\n{varieties}")
    if is_candle and scent:
        parts.append(
            "INGREDIENT ACCURACY\n"
            f"Any fruit or scent ingredients shown as props must be the product's "
            f"actual named components ({scent}) in their true real-world variety, "
            f"colour, and form — never a generic substitute (e.g. Rainier cherries "
            f"are blush-yellow with a red cheek, not solid red; pink guava has "
            f"green-yellow skin and rose-pink flesh). The product NAME may contain "
            f"wordplay that is NOT an ingredient: 'Rosé' refers to rosé wine (a "
            f"blush, fruity, celebratory mood and palette), NOT the rose flower — "
            f"do NOT add roses or rose petals unless the named notes above include "
            f"rose."
        )
    parts.append(f"LIGHT & MOOD\n{F.LIGHT_MOOD}")
    parts.append(f"TECHNICAL\n{prof['technical']}")
    parts.append(
        "NO TEXT of any kind anywhere in the image — no words, letters, numbers, "
        "logos, captions, or watermarks (the product's own printed label is fine). "
        "This is a clean photograph; ad copy is composited on later."
    )
    parts.append(prof["negative"])
    return "\n\n".join(parts)


def _parse(text: str) -> list[dict]:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    items = data.get("concepts", data) if isinstance(data, dict) else data
    out = []
    for x in items or []:
        if isinstance(x, dict) and x.get("scene"):
            out.append({
                "archetype": (x.get("archetype") or "freeform").strip(),
                "title": (x.get("title") or "Untitled scene").strip(),
                "scene": x.get("scene").strip(),
            })
    return out
