"""
AI Ads — finished static ads with the copy drawn in by Nano Banana Pro.

Flow (per product):
  1. draft_brief()      Claude writes a brief from the winning Meta patterns and
                        the operator's idea: one angle, a headline per variant,
                        Meta primary texts and a CTA. The operator edits it.
  2. build_variant_prompt()
                        The 12 fixed variants from the archived Render layer
                        (Minimal, Bold Type, Benefit Stack …), filled from the brief.
  3. invent_scenes() + build_scene_prompt()
                        After that, Claude invents new scenes from the product's
                        archetype menu (avoiding prior concepts) and writes fresh
                        copy matched to each scene, in the brief's angle.

Every word on the ad comes from the brief or the operator: the Render layout
templates that add their own text (urgency lines, invented reviews, stats,
comparison labels) are replaced here with versions that only print our copy.
"""
from __future__ import annotations

import json
import re
from typing import Any

from creative_intelligence.adstudio.chat import (
    _client, _image_blocks, _product_line, _system, MODEL,
)
from creative_intelligence.adstudio.concepts import build_image_prompt
from creative_intelligence.adstudio.profiles import normalize_type, profile
from creative_intelligence.rendering import prompt_builder as R

DEFAULT_ASPECT = "9:16"

# key → name, what it is, which copy fields it prints, fruit-only?
# fields: headline / sub (secondary line) / cta. [] = no text on the image.
VARIANTS: dict[str, dict[str, Any]] = {
    "minimal":              {"name": "Minimal", "blurb": "Pure product, zero text", "fields": []},
    "bold_type":            {"name": "Bold Type", "blurb": "Text is the hero, product secondary",
                             "fields": ["headline"]},
    "benefit_stack":        {"name": "Benefit Stack", "blurb": "Colour block + two stacked benefit lines + CTA",
                             "fields": ["headline", "sub", "cta"]},
    "social_proof":         {"name": "Social Proof", "blurb": "Your real customer review on the product",
                             "fields": ["headline"], "needs_review": True},
    "origin_story":         {"name": "Origin Story", "blurb": "Farm / orchard provenance",
                             "fields": ["headline"], "fruit_only": True},
    "lifestyle_tagline":    {"name": "Lifestyle Tagline", "blurb": "Emotional brand statement in a real moment",
                             "fields": ["headline"]},
    "direct_response":      {"name": "Direct Response", "blurb": "Headline + benefit line + CTA button",
                             "fields": ["headline", "sub", "cta"]},
    "premium":              {"name": "Premium", "blurb": "Dark luxury surface, small elegant label",
                             "fields": ["headline"]},
    "scroll_stopping":      {"name": "Scroll Stopping", "blurb": "Pattern interrupt — bold claim, loud design",
                             "fields": ["headline", "sub"]},
    "craving_macro":        {"name": "Craving Macro", "blurb": "Extreme close-up texture, no copy",
                             "fields": [], "fruit_only": True},
    "supermarket_contrast": {"name": "Supermarket Contrast", "blurb": "Split frame: dull store fruit vs ours",
                             "fields": ["headline"], "fruit_only": True},
    "seasonal_drop":        {"name": "Seasonal Drop", "blurb": "Dark, dramatic, in-season urgency",
                             "fields": ["headline", "sub"]},
}

# What each variant's copy is for — tells Claude how to write it.
_COPY_GUIDE = {
    "bold_type": "headline: 2-5 punchy words that ARE the visual",
    "benefit_stack": "headline: benefit line 1; sub: benefit line 2 (each <=38 chars)",
    "social_proof": "headline: the product name or a 2-4 word label shown above the customer review",
    "origin_story": "headline: a provenance line about where/how it's grown",
    "lifestyle_tagline": "headline: one emotional brand statement",
    "direct_response": "headline: the offer/benefit; sub: one supporting benefit line",
    "premium": "headline: a refined 1-4 word label",
    "scroll_stopping": "headline: a bold, surprising, pattern-interrupt line (true, not a fake stat); "
                       "sub: optional tiny witty fine print, or empty",
    "supermarket_contrast": "headline: the comparison line",
    "seasonal_drop": "headline: a season announcement; sub: an honest urgency line",
}

_COPY_RULES = (
    "COPY RULES — every word you write is printed on a real paid ad:\n"
    "- headline <= 38 characters; sub <= 45 characters; cta 2-3 words.\n"
    "- Only claim what the product details above support. Never invent shipping times, "
    "delivery promises, stock levels, prices, discounts, awards, statistics, customer "
    "counts or ratings.\n"
    "- Never write a customer review or quote, and never attribute words to a customer.\n"
    "- No emoji, hashtags, URLs or brand/platform names."
)


def variants_for(product: dict[str, Any], has_review: bool = False) -> list[str]:
    """The variant keys to generate for this product, in order."""
    fruit = normalize_type(product.get("product_type")) == "fruit"
    out = []
    for k, v in VARIANTS.items():
        if v.get("fruit_only") and not fruit:
            continue
        if v.get("needs_review") and not has_review:
            continue
        out.append(k)
    return out


def variant_list() -> list[dict[str, Any]]:
    return [{"key": k, "name": v["name"], "blurb": v["blurb"], "fields": v["fields"],
             "fruit_only": bool(v.get("fruit_only")), "needs_review": bool(v.get("needs_review"))}
            for k, v in VARIANTS.items()]


# ─────────────────────────────────────────────────────────────────────────────
# Claude: the brief, and scenes with their own copy
# ─────────────────────────────────────────────────────────────────────────────
def draft_brief(product: dict[str, Any], patterns: list[dict[str, Any]] | None = None,
                idea: str = "", has_review: bool = False,
                image_data_uris: list[str] | None = None) -> dict[str, Any]:
    """Claude's draft brief: one angle carried across every variant."""
    from creative_intelligence.adstudio.winning import prompt_block
    keys = [k for k in variants_for(product, has_review) if VARIANTS[k]["fields"]]
    parts = [_product_line(product)]
    if patterns:
        parts.append(prompt_block(patterns).replace(
            "Each hook must follow exactly ONE of these — set its pattern_id.",
            "The brief must follow exactly ONE of these — set its pattern_id."))
    if idea.strip():
        parts.append("IDEA TO TEST (from the operator — the brief must execute this idea"
                     + (", expressed through one of the winning patterns above" if patterns else "")
                     + f"):\n{idea.strip()}")
    guide = "\n".join(f'- {k} ({VARIANTS[k]["name"]}): {_COPY_GUIDE[k]}' for k in keys)
    variants_schema = ",".join(
        f'"{k}":{{' + ",".join(f'"{f}":"…"' for f in VARIANTS[k]["fields"] if f != "cta") + "}"
        for k in keys)
    parts.append(
        "Write ONE ad brief: a single sharp angle, then the on-image copy for each ad "
        "format below. Every format carries the same angle, but each headline must be "
        "written for that format — no two formats share a headline.\n\n"
        f"FORMATS:\n{guide}\n\n{_COPY_RULES}\n\n"
        'Return ONLY JSON: {"angle":"one sentence: the idea every ad carries",'
        '"hook":"the core hook line (D+C+PS)",'
        + ('"pattern_id":<the pattern_id this brief follows>,' if patterns else "")
        + '"primary_texts":["2-3 Meta primary-text options, 1-3 sentences each"],'
        '"cta":"2-3 word CTA button text",'
        '"visual_direction":"one sentence: mood, palette and light for the set",'
        f'"variants":{{{variants_schema}}}}}')
    content = _image_blocks(image_data_uris or []) + [{"type": "text", "text": "\n\n".join(parts)}]
    r = _client().messages.create(
        model=MODEL, max_tokens=3000, system=_system(product),
        messages=[{"role": "user", "content": content}],
    )
    data = _json(_text(r)) or {}
    if not isinstance(data, dict):
        data = {}
    valid = {p["id"] for p in patterns or []}
    try:
        pid = int(data.get("pattern_id"))
    except (TypeError, ValueError):
        pid = None
    return normalize_brief({**data, "pattern_id": pid if pid in valid else None})


def normalize_brief(b: dict[str, Any]) -> dict[str, Any]:
    """Coerce a brief (Claude's or the operator's edit) to the stored shape."""
    pts = b.get("primary_texts") or []
    if isinstance(pts, str):
        pts = [pts]
    variants = {}
    for k, v in (b.get("variants") or {}).items():
        if k in VARIANTS and isinstance(v, dict):
            variants[k] = {f: str(v.get(f) or "").strip() for f in ("headline", "sub")}
    return {
        "angle": str(b.get("angle") or "").strip(),
        "hook": str(b.get("hook") or "").strip(),
        "pattern_id": b.get("pattern_id"),
        "primary_texts": [str(x).strip() for x in pts if str(x).strip()],
        "cta": str(b.get("cta") or "").strip(),
        "visual_direction": str(b.get("visual_direction") or "").strip(),
        "variants": variants,
    }


_SCENE_SYSTEM = """\
You are an art director AND copywriter inventing DIVERSE finished static ad concepts for a product. A reference photo is attached — the product must appear exactly as shown.

Meta fingerprints visually similar images into one "Entity ID" and caps their reach, so every concept must be a different composition family AND a different concrete scene. You are given an archetype menu and a log of concepts ALREADY made for this product — never repeat or lightly vary anything in the log. Vary camera angle and distance, indoor vs outdoor, day vs night, surface and setting, props, palette, and whether a hand or person is in frame.

Each concept carries the brief's angle, but its copy is written FOR that scene: the headline and the image should land the same idea together. Leave clean negative space in the scene where the text will sit.
"""


def invent_scenes(product: dict[str, Any], brief: dict[str, Any], n: int,
                  prior_concepts: list[dict[str, Any]],
                  patterns: list[dict[str, Any]] | None = None, idea: str = "",
                  image_data_uris: list[str] | None = None) -> list[dict[str, str]]:
    """n new {archetype, title, scene, headline, sub, cta, body, text_layout}."""
    from creative_intelligence.adstudio.winning import prompt_block
    prof = profile(product)
    seeds = "\n".join(f"- {k}: {v['name']} — {v['blurb']}" for k, v in prof["formats"].items())
    log = "\n".join(
        f"- [{x.get('archetype', '')}] {x.get('title', '')}: {(x.get('concept') or '')[:160]}"
        for x in prior_concepts) or "(none yet)"
    parts = [_product_line(product)]
    if patterns:
        parts.append(prompt_block(patterns).replace(
            "Each hook must follow exactly ONE of these — set its pattern_id.",
            "The brief below follows one of these; write in its approach."))
    if idea.strip():
        parts.append(f"IDEA BEING TESTED:\n{idea.strip()}")
    parts.append(
        f"THE BRIEF\nAngle: {brief.get('angle', '')}\nHook: {brief.get('hook', '')}\n"
        f"CTA: {brief.get('cta', '')}\nVisual direction: {brief.get('visual_direction', '')}")
    parts.append(f"Archetype directions:\n{seeds}")
    parts.append(f"Concepts ALREADY made for this product — DO NOT repeat or lightly vary:\n{log}")
    parts.append(
        f"Invent {n} NEW, mutually distinct finished-ad concepts.\n\n{_COPY_RULES}\n\n"
        'Return ONLY JSON: {"concepts":[' + str(n) + ' objects, each {'
        '"archetype":"one archetype key from the menu, or \\"freeform\\"",'
        '"title":"3-6 word scene name",'
        '"scene":"2-4 vivid, photographable sentences: composition, camera angle and '
        'distance, setting and surface, props, light, palette, hand/person in frame, '
        'and where the clean negative space for the text is",'
        '"headline":"on-image headline written for this scene",'
        '"sub":"optional short secondary line, or empty",'
        '"cta":"2-3 word CTA, or empty for no button",'
        '"body":"Meta primary text for this ad, 1-3 sentences",'
        '"text_layout":"one sentence: where the text sits and its type style"'
        '}]}')
    content = _image_blocks(image_data_uris or []) + [{"type": "text", "text": "\n\n".join(parts)}]
    system = _SCENE_SYSTEM + (("\n" + prof["concept_rules"]) if prof["concept_rules"] else "")
    r = _client().messages.create(
        model=MODEL, max_tokens=3600, system=system,
        messages=[{"role": "user", "content": content}],
    )
    data = _json(_text(r))
    items = data.get("concepts", []) if isinstance(data, dict) else (data or [])
    out = []
    for x in items:
        if isinstance(x, dict) and x.get("scene") and x.get("headline"):
            out.append({k: str(x.get(k) or "").strip() for k in
                        ("archetype", "title", "scene", "headline", "sub", "cta", "body", "text_layout")})
            out[-1]["archetype"] = out[-1]["archetype"] or "freeform"
            out[-1]["title"] = out[-1]["title"] or "Untitled scene"
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Nano Banana prompts
# ─────────────────────────────────────────────────────────────────────────────
def text_block(lines: list[str], aspect_ratio: str = DEFAULT_ASPECT) -> str:
    """The ON-IMAGE TEXT block: exact strings, nothing else."""
    lines = [x for x in (s.strip() for s in lines) if x]
    if not lines:
        return ("NO TEXT of any kind anywhere in the image — no words, letters, numbers, "
                "logos, captions, or watermarks (the product's own printed label is fine).")
    safe = (" Keep all text out of the top 14% and bottom 20% of the frame — Stories and "
            "Reels cover those areas." if aspect_ratio == "9:16" else "")
    return ("ON-IMAGE TEXT — HIGHEST ACCURACY PRIORITY\n"
            "Render exactly these words, spelled exactly as written (capitalisation may follow "
            "the design), each once:\n" + "\n".join(f'- "{x}"' for x in lines) + "\n"
            "No other words, letters, numbers, prices, ratings, URLs or logos anywhere (the "
            "product's own printed label is fine). Crisp, professional typography, legible at "
            "phone size, high contrast against what is behind it." + safe)


def variant_text(variant: str, headline: str = "", sub: str = "", cta: str = "",
                 review: str = "", author: str = "") -> list[str]:
    """The exact strings a variant prints, in reading order."""
    fields = VARIANTS[variant]["fields"]
    out = []
    if "headline" in fields:
        out.append(headline)
    if variant == "social_proof":
        out += [f"“{review.strip()}”" if review.strip() else "", f"— {author.strip()}" if author.strip() else ""]
    if "sub" in fields:
        out.append(sub)
    if "cta" in fields:
        out.append(cta)
    return [x for x in out if x and x.strip()]


def build_variant_prompt(product: dict[str, Any], brief: dict[str, Any], variant: str,
                         headline: str = "", sub: str = "", cta: str = "",
                         review: str = "", author: str = "", style_seed: int = 0,
                         aspect_ratio: str = DEFAULT_ASPECT, corrections: str = "") -> str:
    """One of the 12 fixed formats, filled with our copy, wrapped in Ad Studio's locks."""
    prof = profile(product)
    fruit = normalize_type(product.get("product_type")) == "fruit"
    name = product.get("name", "the product")
    visual = brief.get("visual_direction", "")
    rb = {"product_visibility": name, "visual_direction": visual}
    s = style_seed
    fields = VARIANTS.get(variant, {}).get("fields", [])
    if "headline" in fields and not headline.strip():
        headline = name
    if variant == "benefit_stack" and not cta.strip():
        cta = "Order Now"   # the template always draws a CTA bar

    if variant == "minimal":
        layout = R._prompt_minimal(rb, name, visual, s) if fruit else _minimal_any(name, visual, s)
    elif variant == "bold_type":
        # skip the "single massive word" layout: it drops the rest of the headline
        bs = s + 1 if s % len(R._BOLD_TYPE_LAYOUTS) == 3 else s
        layout = R._prompt_bold_type(rb, name, headline, cta, bs)
    elif variant == "benefit_stack":
        layout = R._prompt_benefit_stack(rb, name, headline, sub, cta, s)
    elif variant == "social_proof":
        layout = _social_proof(name, headline, review, author, s)
    elif variant == "origin_story":
        layout = R._prompt_origin_story(rb, name, visual, headline, s)
    elif variant == "lifestyle_tagline":
        layout = (R._prompt_lifestyle_tagline(rb, name, headline, s) if fruit
                  else _lifestyle_any(name, headline, s))
    elif variant == "direct_response":
        layout = _direct_response(name, headline, sub, cta, s)
    elif variant == "premium":
        layout = R._prompt_premium(rb, name, headline, s) if fruit else _premium_any(name, headline, s)
    elif variant == "scroll_stopping":
        layout = _scroll_stopping(name, headline, sub, s)
    elif variant == "craving_macro":
        layout = R._prompt_craving_macro(rb, name, s)
    elif variant == "supermarket_contrast":
        layout = _supermarket_contrast(name, headline, s)
    elif variant == "seasonal_drop":
        layout = _seasonal_drop(name, headline, sub, s)
    else:
        raise ValueError(f"unknown variant {variant!r}")

    lines = variant_text(variant, headline, sub, cta, review, author)
    kind = (f"finished {aspect_ratio} static social ad: photorealistic product photography"
            + (" with clean, designed ad typography" if lines else ""))
    parts = [f"Create a {kind}. {prof['reference_lock']}"]
    if corrections.strip():
        parts.append("CORRECTIONS — HIGHEST PRIORITY\nA previous attempt got the following wrong. "
                     "Fix each of these exactly, and prioritize them above every other "
                     "instruction:\n" + corrections.strip())
    parts.append(f"AD FORMAT — {VARIANTS[variant]['name'].upper()}\n{layout}")
    look = (product.get("physical_desc") or "").strip()
    if fruit and look:
        parts.append(f"WHAT IT LOOKS LIKE\n{look}")
    if fruit and (product.get("varieties") or "").strip():
        parts.append(f"VARIETIES — show these, clearly distinguishable\n{product['varieties'].strip()}")
    if not fruit and (product.get("scent_notes") or "").strip():
        parts.append(f"{prof['notes_label'].upper()}\n{product['scent_notes'].strip()}")
    parts.append(text_block(lines, aspect_ratio))
    parts.append(f"TECHNICAL\n{prof['technical']}")
    # Render's negatives ask for urgency/season copy we don't have — drop those.
    extra = ", ".join(x for x in R._VARIANT_NEGATIVES.get(variant, "").split(", ")
                      if x and "urgency" not in x and "season reference" not in x)
    parts.append(prof["negative"] + (f" Also avoid: {extra}." if extra else ""))
    return "\n\n".join(parts)


def build_scene_prompt(product: dict[str, Any], scene: str, headline: str = "", sub: str = "",
                       cta: str = "", text_layout: str = "",
                       aspect_ratio: str = DEFAULT_ASPECT, corrections: str = "") -> str:
    """A Claude-invented scene as a finished ad, its own copy drawn in."""
    block = text_block([headline, sub, cta], aspect_ratio)
    if text_layout.strip():
        block = f"TEXT LAYOUT\n{text_layout.strip()}" + (" The CTA is a clean button." if cta else "") \
                + "\n\n" + block
    return build_image_prompt(product, scene, aspect_ratio, corrections, ad_text=block)


# ── Render templates rewritten to print only our copy ──────────────────────
_DARK = {"forest", "navy", "charcoal", "rust"}


def _social_proof(product: str, headline: str, review: str, author: str, s: int) -> str:
    fmts = [f for f in R._SOCIAL_PROOF_FORMATS if f[0] != "multi_review"]  # one real review only
    label, desc = fmts[s % len(fmts)]
    who = "the reviewer's name in smaller text below it" if author.strip() else "no reviewer name"
    return (f"Social proof ad. Photorealistic {product}, vivid and desirable. Layout: {desc}. "
            "Five gold stars above the review. "
            f"Short bold product line above the stars, then the customer review as the quote, with {who}. "
            "Feels like a genuine customer review, clean and trustworthy. No badges, no shield icons.")


def _direct_response(product: str, headline: str, sub: str, cta: str, s: int) -> str:
    bg = R._style(s + 2, R._BG_PALETTES)
    typo = R._style(s + 2, R._TYPO_MOODS)
    btn = ("white filled rectangle" if R._bg_label(s + 2) in _DARK
           else "bright orange (#FF6B2B) filled rectangle")
    return (f"Conversion-focused ad. {bg}. Photorealistic {product} product photography in the "
            f"centre of the frame. {typo}. Large bold headline at the top"
            + (", the benefit line directly below it" if sub else "")
            + (f", and a prominent {btn} CTA button at the very bottom" if cta else "")
            + ". Strong visual hierarchy — headline → product → CTA. Bold typography, legible at "
              "small sizes. No badges or decorative icons.")


def _scroll_stopping(product: str, headline: str, sub: str, s: int) -> str:
    fine = " The secondary line as very small fine print at the bottom." if sub else ""
    fmt = s % 3
    if fmt == 0:
        bg = ["bright acid yellow (#FFE600)", "electric lime green (#CCFF00)",
              "hot coral (#FF4D4D)", "vivid cyan (#00E5FF)"][s % 4]
        return (f"Scroll-stopping ad. {bg} background, high energy. Photorealistic {product}, bold "
                "and prominent. The headline in massive all-caps bold condensed type filling about "
                f"60% of the frame.{fine} Unexpected colour creates visual shock; the product looks "
                "genuinely incredible. No traditional CTA button.")
    if fmt == 1:
        bg = ["deep black, single dramatic spotlight on the product",
              "very dark navy (#0A0E1A), moody and cinematic",
              "rich dark forest green (#0D1F14), bold and unexpected"][s % 3]
        return (f"Scroll-stopping ad. {bg}. Photorealistic {product}, dramatically lit. The headline "
                f"oversized and punchy, white on dark, dominating the upper frame.{fine} The product "
                "and text glow against the dark. No CTA, no badges. Pure pattern interrupt.")
    return (f"Scroll-stopping ad. Clean white or cream background. Photorealistic {product} in the "
            "centre. The headline on a bold sticker-style label applied at a 10-15° tilt — hand-"
            f"placed, organic, like user-generated content, in a marker-style or condensed font.{fine}")


def _supermarket_contrast(product: str, headline: str, s: int) -> str:
    divide = ["sharp vertical line dividing the frame exactly in half",
              "hard diagonal divide from top-left to bottom-right",
              "horizontal divide — top half vs bottom half"][s % 3]
    return (f"Split-frame comparison ad. {divide}. ONE HALF: a generic supermarket {product} — "
            "desaturated, pale, slightly waxy, flat over-bright lighting, underwhelming. OTHER HALF: "
            f"our {product} — vivid, rich colour, glistening, warm directional light, perfect ripeness. "
            "The headline below both halves or across the divide. No labels on the halves. The visual "
            "contrast must read instantly at thumbnail size.")


def _seasonal_drop(product: str, headline: str, sub: str, s: int) -> str:
    dark = R._SEASON_DARK_BGS[s % len(R._SEASON_DARK_BGS)]
    typo = ["ultra-bold condensed display font, all caps", "massive heavy sans-serif, aggressive weight",
            "bold slab-serif, punchy and authoritative"][s % 3]
    return (f"Seasonal drop product ad. {dark}. Photorealistic {product}, dramatically lit — it "
            "glows against the dark background. Premium, urgent, exclusive, like a limited release. "
            f"{typo}. The headline as a bold announcement at the top"
            + (", the urgency line at the bottom" if sub else "")
            + ". Dark, moody, atmospheric. No white backgrounds, no cheerful colours.")


# Non-fruit versions of the templates whose Render wording is fruit-specific.
def _minimal_any(product: str, visual: str, s: int) -> str:
    surface = R._MINIMAL_SURFACES[s % len(R._MINIMAL_SURFACES)]
    return (f"Clean minimal product photography. {product}. {surface}. The product is the sole "
            "subject — no text, no props. Studio-quality still life, sharp focus, true colour."
            + (f" {visual}." if visual else ""))


def _premium_any(product: str, headline: str, s: int) -> str:
    surface = R._PREMIUM_SURFACES[s % len(R._PREMIUM_SURFACES)]
    return (f"Premium product catalogue ad. {surface}. A single {product}, centred with generous "
            "space, dramatic shadows, pristine. The headline as a small, refined label with elegant "
            "letter-spacing below the product. Luxury gifting aesthetic. No CTA button, no body copy, "
            "no badges. No white backgrounds.")


_MOMENTS_ANY = [
    "on a bedside table in soft evening lamplight, a book and linen nearby",
    "on a sunlit bathroom shelf beside folded towels, morning light",
    "on a coffee table in a calm, styled living room, golden hour",
    "held in two hands against a softly blurred warm interior",
    "on a dining table during a relaxed dinner, candlelit and warm",
]


def _lifestyle_any(product: str, headline: str, s: int) -> str:
    return (f"Lifestyle brand ad. {product} {_MOMENTS_ANY[s % len(_MOMENTS_ANY)]} — the product is "
            "the recognisable, beautiful centrepiece. The headline as an oversized, emotionally "
            "resonant brand statement with generous breathing room. No CTA button, no body copy, "
            "no badges, no price.")


# ── helpers ──────────────────────────────────────────────────────────────────
def _text(r: Any) -> str:
    return "".join(b.text for b in r.content if getattr(b, "type", "") == "text")


def _json(text: str) -> Any:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
