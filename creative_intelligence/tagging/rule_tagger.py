"""
Rule-based creative tagger.

Classifies each creative across the following tag dimensions:
  - hook_type        : curiosity | pain_point | social_proof | authority |
                       direct_offer | story | shock | transformation | question
  - angle            : problem_solution | aspiration | fear | authority_demo |
                       ugc_testimonial | comparison | lifestyle | urgency | discount
  - format           : image | video | carousel | collection | dco | unknown
  - archetype        : direct_response | brand | ugc | demo | testimonial |
                       educational | social_proof | lifestyle
  - emotional_trigger: fear | curiosity | desire | trust | urgency | social_proof | pride
  - offer_style      : hard_offer | soft_offer | brand | free_trial | discount |
                       bundle | guarantee | no_offer
  - cta_type         : shop_now | learn_more | get_offer | sign_up | book_now |
                       download | watch_more | contact | order_now | other

Rules are keyword/regex based and operate on hook_text, primary_text, headline, and ad_name.
Confidence is 1.0 for rule matches; AI tagger may override with lower confidence.
"""
from __future__ import annotations

import re
from typing import Any

# ─────────────────────────────────────────────
# Rule definitions
# Each entry: (tag_value, [keyword/pattern list])
# Patterns are matched case-insensitively against the text fields.
# ─────────────────────────────────────────────

_HOOK_TYPE_RULES: list[tuple[str, list[str]]] = [
    ("question",       [r"\?"]),
    # quality_authenticity: "This is how REAL X is supposed to taste" and variants.
    # This is the dominant hook type for premium/exotic produce brands — challenges
    # the inferior grocery store experience and positions the product as genuine.
    # Added 2026-04-07 after inspecting the top unknowns; covers the #1, #5, #9
    # ROAS performers in the account.
    ("quality_authenticity", [
        r"this is how real",
        r"supposed to taste",
        r"real .{1,30} is supposed",
        r"you.ve never tasted",
        r"never tasted .{1,30} like this",
        r"the way .{1,20} should taste",
        r"true taste of",
        r"discover the best .{1,30} on earth",
        r"discover the best .{1,30} in the world",
        r"the best .{1,30} you.ve ever",
        r"ripened to perfection",
        r"picked at peak",
        r"tree.ripened",
        r"sun.ripened",
    ]),
    ("curiosity",      ["secret", "hidden", "most people don't", "nobody tells", "here's why",
                        "this is why", "did you know", "the truth", "what if", "here's what"]),
    ("pain_point",     ["struggle", "tired of", "stop wasting", "sick of", "problem",
                        "frustrated", "can't seem to", "failing", "suffering", "pain"]),
    ("social_proof",   ["customers", "people love", "sold out", "best seller", "rated",
                        "#1", "over .* sold", "trusted by", "join .* people", "reviews"]),
    ("authority",      ["dr.", "doctor", "expert", "scientist", "studied", "research",
                        "proven", "clinical", "certified", "award"]),
    ("transformation", ["before.*after", "changed my life", "transformed", "went from",
                        "lost .* lbs", "gained", "finally", "now i can", "used to"]),
    ("story",          ["i was", "my story", "one day", "last year", "i used to",
                        "it started when", "growing up", "let me tell you"]),
    ("direct_offer",   ["% off", "free shipping", "buy now", "order now", "limited time",
                        "save \\$", "get yours", "only \\$", "just \\$"]),
    ("shock",          ["warning", "stop", "don't", "never", "dangerous", "shocking",
                        "you won't believe", "wtf", "insane", "crazy"]),
]

_ANGLE_RULES: list[tuple[str, list[str]]] = [
    # authenticity_quality: the "grocery store inferior / we are the real thing" angle.
    # Matches the dominant narrative in this account: rare/exotic produce that grocery
    # stores can't supply, so buying direct unlocks the authentic experience.
    # Added 2026-04-07 alongside quality_authenticity hook_type.
    ("authenticity_quality", [
        r"grocery store",
        r"supermarket",
        r"this is how real",
        r"supposed to taste",
        r"real .{1,30} should",
        r"you.ve never tasted",
        r"never tasted .{1,30} like",
        r"farm.?fresh",
        r"hand.?picked",
        r"locally? .{0,10}grown",
        r"direct from .{1,20}farm",
        r"farm to",
        r"straight from",
        r"source to",
        r"rare .{1,20}fruit",
        r"exotic fruit",
    ]),
    ("problem_solution", ["problem", "solution", "fix", "solve", "tired of", "struggle",
                          "works for", "finally works", "stop.*struggle"]),
    ("fear",             ["don't miss", "warning", "risk", "danger", "before it's too late",
                          "running out", "last chance", "avoid", "protect"]),
    ("aspiration",       ["dream", "imagine", "what if", "achieve", "goal", "discover",
                          "reach your", "become", "vision", "best version", "best .* on earth",
                          "best .* in the world"]),
    ("authority_demo",   ["how to", "step by step", "tutorial", "guide", "demo", "watch how",
                          "see how", "we show", "proven method"]),
    ("ugc_testimonial",  ["i tried", "i used", "it worked", "honestly", "real results",
                          "my experience", "i'm obsessed", "changed my"]),
    ("comparison",       ["vs", "versus", "better than", "unlike", "compared to",
                          "alternative to", "switch from", "why we're different",
                          r"why .{1,30}store .{1,30}will ruin",
                          r"ruin .{1,30}for you"]),
    ("lifestyle",        ["feel like", "live like", "every day", "routine", "lifestyle",
                          "part of my life", "daily", "morning", "night"]),
    ("urgency",          ["today only", "limited", "hurry", "ends soon", "while supplies",
                          "last chance", "only .* left", "expires", "season is",
                          "end of season", "wait is over", "back in stock"]),
    ("discount",         ["% off", "sale", "deal", "save", "discount", "promo", "coupon",
                          "code", "special price"]),
]

_FORMAT_RULES: list[tuple[str, list[str]]] = [
    ("video",      ["video", ".mp4", "watch", "reel", "ugc"]),
    ("carousel",   ["swipe", "carousel", "slide", "multiple"]),
    ("collection", ["collection", "catalog", "shop all"]),
    ("dco",        ["dco", "dynamic", "dynamic creative"]),
    ("image",      ["image", "photo", "static", ".jpg", ".png"]),
]

_ARCHETYPE_RULES: list[tuple[str, list[str]]] = [
    # product_hero: the product itself is the star — sensory description, quality
    # positioning, taste experience. Common in premium food/produce DTC brands.
    # Added 2026-04-07 to classify the dominant "This is how REAL X tastes" format.
    ("product_hero",  [
        r"this is how real",
        r"supposed to taste",
        r"pure .{1,20}bliss",
        r"pure .{1,20}perfection",
        r"pure .{1,20}nectar",
        r"pure .{1,20}sweetness",
        r"pure .{1,20}flavor",
        r"sweet.tart",
        r"tree.ripened",
        r"sun.ripened",
        r"juiciest",
        r"ripened to perfection",
        r"picked at peak",
        r"nature.s candy",
    ]),
    ("ugc",           ["ugc", "user generated", "real person", "authentic", "honest review",
                       "i tried", "my experience"]),
    ("testimonial",   ["testimonial", "review", "customer says", "real customer",
                       "success story", "transformation story"]),
    ("demo",          ["demo", "how to", "tutorial", "see it work", "watch how", "step by step"]),
    ("educational",   ["learn", "did you know", "science", "research shows", "fact:",
                       "study shows", "explained"]),
    ("social_proof",  ["best seller", "#1", "top rated", "over .* sold", "trusted", "loved by"]),
    ("lifestyle",     ["lifestyle", "everyday", "routine", "feel like", "live like"]),
    ("direct_response", ["order now", "buy now", "shop now", "limited time", "% off",
                          "free shipping", "get yours today"]),
    ("brand",         ["our mission", "we believe", "our story", "founded", "brand",
                       "values", "vision"]),
]

_EMOTIONAL_TRIGGER_RULES: list[tuple[str, list[str]]] = [
    ("fear",         ["don't miss", "before it's too late", "risk", "danger", "warning",
                      "lose", "miss out", "fomo", "protect"]),
    ("curiosity",    ["secret", "hidden", "you don't know", "most people", "here's why",
                      "what if", "truth about"]),
    ("desire",       ["dream", "imagine", "want", "wish", "achieve", "best",
                      "amazing", "incredible", "love"]),
    ("trust",        ["proven", "research", "doctor", "clinical", "trusted",
                      "guaranteed", "certified", "tested"]),
    ("urgency",      ["now", "today", "limited", "hurry", "ends", "last chance",
                      "expires", "only left"]),
    ("social_proof", ["thousands", "people love", "sold out", "best seller",
                      "rated", "reviews", "joined by"]),
    ("pride",        ["proud", "achievement", "earned", "deserve", "you've",
                      "success", "winner", "best version of you"]),
]

_OFFER_STYLE_RULES: list[tuple[str, list[str]]] = [
    ("discount",     ["% off", "save \\$", "coupon", "promo code", "sale price"]),
    ("free_trial",   ["free trial", "try free", "first .* free", "risk free", "no commitment"]),
    ("bundle",       ["bundle", "kit", "set", "pack", "combo", "collection", "with free"]),
    ("guarantee",    ["guarantee", "money back", "risk-free", "return policy", "satisfaction"]),
    ("hard_offer",   ["order now", "buy now", "limited time", "only \\$", "just \\$",
                      "today only", "while supplies"]),
    ("soft_offer",   ["learn more", "discover", "find out", "see why", "explore",
                      "get started"]),
    ("no_offer",     ["our story", "brand", "mission", "values", "lifestyle",
                      "inspiration"]),
]

_CTA_RULES: list[tuple[str, list[str]]] = [
    ("shop_now",   ["shop now", "shop the"]),
    ("order_now",  ["order now"]),
    ("get_offer",  ["get offer", "get deal", "get discount", "claim", "redeem"]),
    ("learn_more", ["learn more"]),
    ("sign_up",    ["sign up", "join now", "subscribe", "create account"]),
    ("book_now",   ["book now", "schedule", "book a"]),
    ("download",   ["download", "get the app", "install"]),
    ("watch_more", ["watch", "see more", "view"]),
    ("contact",    ["contact", "call us", "message us", "chat"]),
]


def _match(text: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if re.search(pat, text, re.IGNORECASE):
            return True
    return False


def _combined_text(record: dict[str, Any]) -> str:
    parts = [
        record.get("hook_text", "") or "",
        record.get("primary_text", "") or "",
        record.get("headline", "") or "",
        record.get("ad_name", "") or "",
        record.get("description", "") or "",
    ]
    return " ".join(p for p in parts if p)


def _first_match(
    text: str,
    rules: list[tuple[str, list[str]]],
    fallback: str = "unknown",
) -> str:
    for tag_value, patterns in rules:
        if _match(text, patterns):
            return tag_value
    return fallback


def tag_creative(record: dict[str, Any]) -> dict[str, str]:
    """Apply all rule-based classifiers to a creative record.

    Returns a dict of {tag_type: tag_value}.
    """
    text = _combined_text(record)
    fmt  = (record.get("format") or "").lower()

    # Format: prefer explicit format field, fall back to text rules.
    format_tag = fmt if fmt in {"image", "video", "carousel", "collection", "dco"} \
                 else _first_match(text, _FORMAT_RULES, "unknown")

    return {
        "hook_type":         _first_match(text, _HOOK_TYPE_RULES, "unknown"),
        "angle":             _first_match(text, _ANGLE_RULES, "unknown"),
        "format":            format_tag,
        "archetype":         _first_match(text, _ARCHETYPE_RULES, "unknown"),
        "emotional_trigger": _first_match(text, _EMOTIONAL_TRIGGER_RULES, "unknown"),
        "offer_style":       _first_match(text, _OFFER_STYLE_RULES, "unknown"),
        "cta_type":          _first_match(text, _CTA_RULES, "other"),
    }


def tag_all(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tag a list of creative records.

    Returns list of dicts: {creative_id, tag_type, tag_value, confidence, source}
    """
    rows = []
    for rec in records:
        tags = tag_creative(rec)
        cid  = rec.get("id") or rec.get("creative_id") or rec.get("ad_id", "")
        for tag_type, tag_value in tags.items():
            rows.append({
                "creative_id": cid,
                "tag_type":    tag_type,
                "tag_value":   tag_value,
                "confidence":  1.0,
                "source":      "rule",
            })
    return rows
