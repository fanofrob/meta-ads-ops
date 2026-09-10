"""
Deterministic prompt assembly for GHF candle labels.

Assembly order is fixed and must not change:
    1. BLOCK 0   — reference PDF preamble
    2. BASE STYLE — verbatim, never edited
    3. The five selected grid options, written out in full
    4. NEGATIVE  — verbatim, always last

Same recipe in -> same prompt out. No LLM, no randomness, no seeds.
"""
from __future__ import annotations

from typing import Any

from creative_intelligence.labels import recipes as R


class RecipeError(ValueError):
    """Raised when a recipe references a code that doesn't exist."""


def _resolve(sku: str, s: str, i: str, e: str, h: str, p: str) -> dict[str, Any]:
    """Validate every code and return the resolved option objects."""
    if sku not in R.SKUS:
        raise RecipeError(f"Unknown SKU {sku!r}. Valid: {', '.join(R.SKUS)}")
    sku_def = R.SKUS[sku]

    def pick(table: dict, code: str, label: str) -> dict:
        if code not in table:
            raise RecipeError(
                f"Unknown {label} code {code!r}. Valid: {', '.join(table)}"
            )
        return table[code]

    return {
        "sku_def":  sku_def,
        "count":    pick(R.COUNT_STATE, s, "count/state (B1)"),
        "interior": pick(sku_def["interior"], i, "interior (B2)"),
        "exterior": pick(sku_def["exterior"], e, "exterior (B3)"),
        "hierarchy": pick(R.HIERARCHY, h, "hierarchy (B4)"),
        "palette":  pick(R.PALETTE, p, "palette (B5)"),
    }


def recipe_code(sku: str, s: str, i: str, e: str, h: str, p: str) -> str:
    """Canonical log string, e.g. 'PP · S2 / I1 / E1 / H5 / P1'."""
    return f"{sku} · {s} / {i} / {e} / {h} / {p}"


def assemble_prompt(
    sku: str, s: str, i: str, e: str, h: str, p: str,
) -> dict[str, Any]:
    """
    Build the full prompt for one recipe.

    Returns
    -------
    {
        "recipe":          "PP · S2 / I1 / E1 / H5 / P1",
        "prompt":          str,   # positive prompt (blocks 0 + A + B)
        "negative_prompt": str,   # block C, passed separately to the image model
        "full_text":       str,   # everything incl. negative, for copy/paste
        "warnings":        [str],
        "sku_name":        str,
        "scent":           str,
        "selection":       {...}, # resolved names for UI display
    }
    """
    r = _resolve(sku, s, i, e, h, p)
    sku_def = r["sku_def"]

    # P5's echo colour is per-SKU — fold it in so the prompt is self-contained.
    palette_desc = r["palette"]["desc"]
    if p == "P5":
        palette_desc = (
            f"{palette_desc} For this SKU the echo colour is "
            f"{sku_def['echo_color']}."
        )

    variation = "\n\n".join([
        "**Subject**\n"
        f"{sku_def['name']} — scent concept \"{sku_def['scent']}\". "
        f"Hero: {sku_def['hero']}",

        f"**Count & State — {r['count']['name']}**\n{r['count']['desc']}",

        f"**Interior Detail — {r['interior']['name']}**\n{r['interior']['desc']}",

        f"**Exterior Detail — {r['exterior']['name']}**\n{r['exterior']['desc']}",

        f"**Hierarchy — {r['hierarchy']['name']}**\n{r['hierarchy']['desc']}",

        f"**Palette & Sweep — {r['palette']['name']}**\n{palette_desc}",
    ])

    # ── Sent to the image API ────────────────────────────────────────────
    # BLOCK_0 is deliberately EXCLUDED here. It opens with "A reference PDF is
    # attached" — but an API call carries no attachment, so that sentence is
    # simply false, and image models act on it. FLUX 1.1 Pro took the whole
    # block literally and rendered a picture of the reference *document*:
    # page layout, body text, wordmark and amber jar — precisely the things
    # BLOCK_0 tells it not to draw. Only the art direction goes to the model.
    prompt = "\n\n".join([R.BASE_STYLE, "---", variation])

    # ── For copy/paste into a chat-based, PDF-capable tool ───────────────
    # Here the operator attaches the real PDF, so BLOCK_0's instructions about
    # how to read it are true and useful. Block 0 belongs here and only here.
    full_text = "\n\n".join([
        R.BLOCK_0,
        "---",
        R.BASE_STYLE,
        "---",
        variation,
        "---",
        "**DO NOT INCLUDE**\n" + R.NEGATIVE,
    ])

    return {
        "recipe":          recipe_code(sku, s, i, e, h, p),
        "prompt":          prompt,
        "negative_prompt": R.NEGATIVE,
        "full_text":       full_text,
        "warnings":        R.conflicts(sku, s, i, e, h, p),
        "sku_name":        sku_def["name"],
        "scent":           sku_def["scent"],
        "pdf_page":        sku_def["pdf_page"],
        "selection": {
            "count":     f"{s} · {r['count']['name']}",
            "interior":  f"{i} · {r['interior']['name']}",
            "exterior":  f"{e} · {r['exterior']['name']}",
            "hierarchy": f"{h} · {r['hierarchy']['name']}",
            "palette":   f"{p} · {r['palette']['name']}",
        },
    }


def ui_config() -> dict[str, Any]:
    """Everything the front-end needs to render the pickers, in one payload."""
    return {
        "skus": {
            code: {
                "name":     d["name"],
                "scent":    d["scent"],
                "hero":     d["hero"],
                "default":  d["default"],
                "pdf_page": d["pdf_page"],
                "interior": d["interior"],
                "exterior": d["exterior"],
            }
            for code, d in R.SKUS.items()
        },
        "count_state": R.COUNT_STATE,
        "hierarchy":   R.HIERARCHY,
        "palette":     R.PALETTE,
        "pdf_filename": R.PDF_FILENAME,
        "models":       R.MODELS,
        "default_model": R.DEFAULT_MODEL,
    }
