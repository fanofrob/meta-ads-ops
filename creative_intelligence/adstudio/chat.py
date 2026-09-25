"""
Claude, inside Ad Studio.

Two entry points, both able to see the reference photo:
  chat_reply()    — freeform conversation to develop hooks/angles
  suggest_hooks() — returns N structured, saveable hooks for a product

Uses the Anthropic key already in .env. The system prompt is the hook engine
reverse-engineered from the app (Desire + Conflict + Partial Solution).
"""
from __future__ import annotations

import json
import re
from typing import Any

from creative_intelligence.adstudio.profiles import profile

# Sonnet for copy quality; verified working on this account.
MODEL = "claude-sonnet-4-5"

SYSTEM = """\
You are a senior DTC performance-creative director for Good Hill Farms, helping build Meta/Instagram ad copy for a specific product (a reference photo may be attached — use it).

Every hook = Desire + Conflict + Partial Solution (D+C+PS): name what the buyer wants (D), reveal what's in the way (C), tease that a solution exists without completing the loop (PS). Never complete the loop.

Rules: these are PAID ads — make someone click to BUY, not to learn. Use ONE specific, ownable claim (never a line that could fit a competitor). Use the product name at most once. No placeholder brackets. Reject rage-bait, "easy steps," vague curiosity ("this changed everything"), and generic hooks.
"""


def _system(product: dict[str, Any]) -> str:
    """SYSTEM plus the product type's take on what the buyer desires."""
    return SYSTEM + "\n" + profile(product)["hook_desire"] + "\n"

_HOOK_SCHEMA = (
    'Return ONLY JSON: {"hooks":[{'
    '"hook_text":"the full hook line",'
    '"archetype":"e.g. A7 Most think X but actually Y",'
    '"d":"what the buyer wants","c":"the obstacle","ps":"the purchase/experience teased",'
    '"headline":"<=6 word on-image headline","subhead":"<=12 word subhead",'
    '"body":"1-2 sentence caption","cta":"2-4 word CTA"'
    '}]}'
)


def _client():
    import os
    import anthropic
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    return anthropic.Anthropic(api_key=key)


def _image_blocks(data_uris: list[str]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for uri in data_uris or []:
        m = re.match(r"data:(image/[a-zA-Z0-9.+-]+);base64,(.*)$", uri, re.DOTALL)
        if not m:
            continue
        blocks.append({
            "type": "image",
            "source": {"type": "base64", "media_type": m.group(1), "data": m.group(2)},
        })
    return blocks


def _product_line(product: dict[str, Any]) -> str:
    prof = profile(product)
    bits = [f"Product: {product.get('name','(unnamed)')} (type: {prof['label']})"]
    if product.get("scent_notes"):
        bits.append(f"{prof['notes_label']}: {product['scent_notes']}")
    if product.get("physical_desc"):
        bits.append(f"Looks like: {product['physical_desc']}")
    if product.get("varieties"):
        bits.append(f"Varieties: {product['varieties']}")
    if product.get("description"):
        bits.append(f"Description: {product['description']}")
    if product.get("notes"):
        bits.append(
            "OPERATOR CONTEXT & NOTES (authoritative — honor these exactly, and "
            "elaborate them into concrete photographic detail in your scenes):\n"
            f"{product['notes']}")
    return "\n".join(bits)


def chat_reply(messages: list[dict[str, str]], product: dict[str, Any],
               image_data_uris: list[str] | None = None) -> str:
    """messages = [{role, text}]. The reference photo is attached to the FIRST turn."""
    c = _client()
    api_msgs: list[dict[str, Any]] = []
    imgs = _image_blocks(image_data_uris or [])
    for idx, m in enumerate(messages):
        content: list[dict[str, Any]] = []
        if idx == 0 and imgs:
            content.extend(imgs)
        content.append({"type": "text", "text": m["text"]})
        api_msgs.append({"role": m["role"], "content": content})

    r = c.messages.create(
        model=MODEL, max_tokens=1200,
        system=_system(product) + "\n" + _product_line(product),
        messages=api_msgs,
    )
    return "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip()


def suggest_hooks(product: dict[str, Any], n: int = 4,
                  brief: str = "", image_data_uris: list[str] | None = None) -> list[dict]:
    """Return n structured, saveable hooks. `brief` = optional steer from the user."""
    c = _client()
    imgs = _image_blocks(image_data_uris or [])
    ask = (
        f"{_product_line(product)}\n\n"
        f"Write {n} distinct, strong ad hooks for this product."
        + (f" Direction from me: {brief}" if brief else "")
        + f"\n\n{_HOOK_SCHEMA}"
    )
    content: list[dict[str, Any]] = list(imgs) + [{"type": "text", "text": ask}]
    r = c.messages.create(
        model=MODEL, max_tokens=2000, system=_system(product),
        messages=[{"role": "user", "content": content}],
    )
    text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
    return _parse_hooks(text)


def extract_hooks(product: dict[str, Any], messages: list[dict[str, str]],
                  image_data_uris: list[str] | None = None) -> list[dict]:
    """Pull every distinct hook already written in a chat into structured JSON.

    Uses the exact wording from the conversation — does not invent new hooks.
    """
    c = _client()
    convo = "\n\n".join(f"{m['role'].upper()}: {m['text']}" for m in messages)
    ask = (
        f"{_product_line(product)}\n\n"
        "Below is a brainstorming conversation. Extract EVERY distinct ad hook that "
        "was written in it into structured JSON. Preserve the EXACT wording of the "
        "hook line, body, and CTA in hook_text / body / cta — do not invent new hooks "
        "or reword them. The one exception: if a hook has no short on-image headline, "
        "distill a punchy ≤6-word `headline` from that hook (concise phrasing of your "
        "own is fine here) so it's ready to place on an image; still keep the full "
        "original line in `hook_text`. Leave body/cta empty only if truly absent.\n\n"
        f"CONVERSATION:\n{convo}\n\n{_HOOK_SCHEMA}"
    )
    content = _image_blocks(image_data_uris or []) + [{"type": "text", "text": ask}]
    r = c.messages.create(
        model=MODEL, max_tokens=3000, system=_system(product),
        messages=[{"role": "user", "content": content}],
    )
    text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
    return _parse_hooks(text)


def _parse_hooks(text: str) -> list[dict]:
    # tolerate ```json fences / stray prose around the JSON
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    hooks = data.get("hooks", data) if isinstance(data, dict) else data
    out = []
    for h in hooks or []:
        if isinstance(h, dict) and (h.get("hook_text") or h.get("headline")):
            out.append(h)
    return out
