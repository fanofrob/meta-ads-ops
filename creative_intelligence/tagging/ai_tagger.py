"""
AI creative tagger (Claude) with a fruit-specific taxonomy.

The keyword rule_tagger puts most of our ads into one bucket ("quality /
authenticity" hook + angle), so tag-pair patterns can barely differ from the
average ad. This tagger has Claude read each ad's name, headline and primary
text and classify it on labels that actually separate our concepts — taste vs
rarity vs vs-supermarket vs farm origin vs gifting, avatar vs listicle vs
unboxing, and so on.

- Re-tags every ad it is given; its tags replace the rule tags for the same
  tag types (creative_tags is UNIQUE(creative_id, tag_type)). `format` and
  `cta_type` stay rule-based — they come from the ad's own data, not copy.
- Each distinct copy is classified once: results are cached in ai_tag_cache,
  keyed by taxonomy version + copy, so re-syncs only pay for new ads.
- Ads are sent in batches with a strict JSON schema (every value is an enum),
  so the output always parses and never invents labels.
- Catalog / {{product.name}} ads are skipped — there is no copy to read.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Callable

MODEL = "claude-opus-5"
TAXONOMY_VERSION = "ghf-fruit-v1"
BATCH_SIZE = 25

# tag_type → {value: what it means}. "other" is the escape hatch for each type.
TAXONOMY: dict[str, dict[str, str]] = {
    "hook_type": {
        "taste_sensory": "opens on flavour, texture, juiciness, sweetness — what it's like to eat",
        "discovery_new_fruit": "a fruit most people have never tried / 'have you ever seen this?'",
        "rarity_scarcity": "hard to find, tiny harvest, short season, 'once it's gone it's gone'",
        "vs_supermarket": "contrasts with grocery-store fruit ('this is how X is SUPPOSED to taste')",
        "social_proof": "reviews, customer counts, testimonials, star ratings lead the hook",
        "offer_price": "leads with a discount, deal, bundle or free shipping",
        "curiosity_question": "a question or teaser that withholds the answer",
        "story_pov": "a personal story, founder/farmer/avatar POV, 'I…' narrative",
        "education": "explains how to eat / pick / cut / what it is",
        "gift_occasion": "leads with gifting, a holiday or an occasion",
        "other": "none of the above clearly dominates",
    },
    "angle": {
        "flavor_experience": "the core promise is how it tastes / feels to eat",
        "tree_ripened_quality": "picked ripe, peak freshness, premium quality vs normal fruit",
        "seasonal_limited": "limited season / window / availability",
        "farm_origin": "where and how it's grown, the grower, the farm",
        "exotic_discovery": "novelty — trying something new and unusual",
        "variety_box": "a curated mix / assortment / variety box",
        "gifting": "a gift for someone else",
        "health": "nutrition, wellness, clean eating",
        "value_deal": "price, savings, discount",
        "convenience_delivery": "delivered to your door fast / fresh shipping",
        "other": "none of the above clearly dominates",
    },
    "archetype": {
        "product_hero": "the fruit itself is the star; polished product showcase",
        "avatar_talking_head": "a person (avatar / creator / founder) talking to camera",
        "ugc_testimonial": "customer-style review or reaction",
        "listicle": "numbered reasons / list format",
        "unboxing": "opening the box / delivery reveal",
        "cutting_demo": "cutting, peeling, tasting demonstration",
        "comparison": "side-by-side vs another fruit / store version",
        "lifestyle": "fruit in a lifestyle moment (picnic, kitchen, brunch)",
        "other": "none of the above clearly dominates",
    },
    "emotional_trigger": {
        "craving": "appetite, mouth-watering desire",
        "fomo_urgency": "fear of missing out, act now",
        "curiosity": "wanting to know / try",
        "trust": "reassurance, guarantee, credibility",
        "delight_surprise": "wow, surprise, 'you won't believe'",
        "nostalgia": "memories, childhood, travel",
        "belonging": "joining others, social belonging",
        "other": "none of the above clearly dominates",
    },
    "offer_style": {
        "discount": "percent/dollar off or sale price",
        "bundle_variety": "bundle, variety or mixed box offer",
        "guarantee": "satisfaction / happiness / freshness guarantee",
        "free_shipping": "free or fast shipping is the offer",
        "no_offer": "no explicit offer",
        "other": "some other offer",
    },
}
TAG_TYPES = list(TAXONOMY)

SYSTEM = f"""\
You classify Meta ads for Good Hill Farms, a DTC shop that ships rare, tree-ripened and exotic fruit (cherimoya, Rainier cherries, pink pineapple, passion fruit, mangos, variety boxes…).

For each ad, read its ad name (it often names the concept format, e.g. "Avatar", "Listicle", "Packaging"), headline and primary text, and choose the single DOMINANT value for each label — what the ad leads with, not everything it mentions. Use "other" only when nothing fits.

Labels and their values:
{json.dumps(TAXONOMY, indent=1)}
"""


def _schema() -> dict[str, Any]:
    props: dict[str, Any] = {"id": {"type": "string"}}
    for t, vals in TAXONOMY.items():
        props[t] = {"type": "string", "enum": list(vals)}
    return {
        "type": "object",
        "properties": {"ads": {"type": "array", "items": {
            "type": "object", "properties": props,
            "required": ["id", *TAG_TYPES], "additionalProperties": False}}},
        "required": ["ads"],
        "additionalProperties": False,
    }


def is_catalog(rec: dict[str, Any]) -> bool:
    return "{{" in (rec.get("headline") or "") or "{{" in (rec.get("primary_text") or "")


def has_copy(rec: dict[str, Any]) -> bool:
    return len(((rec.get("headline") or "") + (rec.get("primary_text") or "")).strip()) >= 15


def copy_key(rec: dict[str, Any]) -> str:
    raw = "\x1f".join([TAXONOMY_VERSION, rec.get("ad_name") or "", rec.get("headline") or "",
                       rec.get("primary_text") or ""])
    return hashlib.sha256(raw.encode()).hexdigest()


def _client():
    import anthropic
    return anthropic.Anthropic()


def classify_batch(records: list[dict[str, Any]], client: Any = None) -> dict[str, dict[str, str]]:
    """{copy_key: {tag_type: value}} for one batch. Empty dict on refusal."""
    client = client or _client()
    keys = {f"a{i + 1}": copy_key(r) for i, r in enumerate(records)}   # short ids to echo
    ads = [{"id": f"a{i + 1}", "ad_name": r.get("ad_name") or "",
            "headline": r.get("headline") or "", "primary_text": r.get("primary_text") or ""}
           for i, r in enumerate(records)]
    resp = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=SYSTEM,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": _schema()}},
        messages=[{"role": "user", "content":
                   "Classify each of these ads. Return one entry per ad, echoing its id.\n\n"
                   + json.dumps(ads, ensure_ascii=False)}],
    )
    if resp.stop_reason == "refusal":
        return {}
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    out: dict[str, dict[str, str]] = {}
    for a in data.get("ads", []):
        if a.get("id") in keys:
            out[keys[a["id"]]] = {t: a[t] for t in TAG_TYPES if a.get(t) in TAXONOMY[t]}
    return out


def _cache_get(db: sqlite3.Connection, keys: list[str]) -> dict[str, dict[str, str]]:
    if not keys:
        return {}
    ph = ",".join("?" * len(keys))
    return {r["key"]: json.loads(r["tags_json"]) for r in db.execute(
        f"SELECT key, tags_json FROM ai_tag_cache WHERE key IN ({ph})", keys).fetchall()}


def tag_with_ai(db: sqlite3.Connection, records: list[dict[str, Any]],
                classify: Callable[[list[dict[str, Any]]], dict[str, dict[str, str]]] | None = None,
                log: Callable[[str], None] = print) -> dict[str, int]:
    """
    AI-tag creatives (dicts with id, ad_name, headline, primary_text) and write
    their tags to creative_tags, replacing rule tags for the same types.
    Returns counts: tagged, from_cache, classified, skipped, failed.
    """
    classify = classify or (lambda batch: classify_batch(batch))
    todo = [r for r in records if r.get("id") and has_copy(r) and not is_catalog(r)]
    skipped = len(records) - len(todo)
    by_key: dict[str, list[dict[str, Any]]] = {}
    for r in todo:
        by_key.setdefault(copy_key(r), []).append(r)

    cached = _cache_get(db, list(by_key))
    was_cached = set(cached)
    missing = [rs[0] for k, rs in by_key.items() if k not in cached]
    log(f"  AI tagging {len(todo)} ads: {len(by_key)} distinct copies, "
        f"{len(cached)} cached, {len(missing)} to classify ({skipped} skipped: no copy / catalog)")
    failed = 0
    for i in range(0, len(missing), BATCH_SIZE):
        batch = missing[i:i + BATCH_SIZE]
        log(f"  batch {i // BATCH_SIZE + 1}/{(len(missing) + BATCH_SIZE - 1) // BATCH_SIZE}")
        try:
            got = classify(batch)
        except Exception as exc:  # keep going; unclassified ads keep their rule tags
            log(f"  [WARN] batch failed: {str(exc)[:200]}")
            got = {}
        failed += len([r for r in batch if copy_key(r) not in got])
        with db:
            for k, tags in got.items():
                if len(tags) == len(TAG_TYPES):
                    db.execute("INSERT OR REPLACE INTO ai_tag_cache (key, tags_json, model, taxonomy)"
                               " VALUES (?,?,?,?)", (k, json.dumps(tags), MODEL, TAXONOMY_VERSION))
                    cached[k] = tags

    tagged = 0
    with db:
        for k, rs in by_key.items():
            tags = cached.get(k)
            if not tags:
                continue
            for r in rs:
                for t, v in tags.items():
                    db.execute("INSERT OR REPLACE INTO creative_tags (creative_id, tag_type, tag_value,"
                               " confidence, source) VALUES (?,?,?,0.9,'ai')", (r["id"], t, v))
                tagged += 1
    return {"tagged": tagged,
            "from_cache": sum(len(rs) for k, rs in by_key.items() if k in was_cached),
            "classified": len(missing) - failed, "skipped": skipped, "failed": failed}
