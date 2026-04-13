"""
Hook QA layer.

Filters and flags generated hooks before they reach the operator.
All checks are dependency-free (stdlib only).

Usage:
  from creative_intelligence.generation.hook_qa import filter_hooks, QAConfig

  config  = QAConfig(similarity_threshold=0.65, diversity="high")
  results = filter_hooks(hooks, source_hooks=example_hooks, config=config)
  clean   = [r.hook for r in results if r.passed]
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────

@dataclass
class QAConfig:
    # Hooks with source-similarity above this are rejected.
    similarity_threshold: float = 0.65
    # Hooks with near-duplicate similarity above this are collapsed.
    duplicate_threshold: float = 0.75
    # Structural diversity level controls how hard we enforce variety.
    # "low"    — only reject exact structural duplicates
    # "medium" — allow up to 3 hooks per opening pattern
    # "high"   — allow at most 2 hooks per opening pattern
    diversity: str = "medium"   # "low" | "medium" | "high"


_DIVERSITY_MAX: dict[str, int] = {
    "low": 999,
    "medium": 3,
    "high": 2,
}

# ─────────────────────────────────────────────
# Known weak / generic openings to flag
# ─────────────────────────────────────────────

_GENERIC_OPENINGS: list[str] = [
    r"^are you still struggling",
    r"^here's what nobody tells you",
    r"^this changed everything",
    r"^you won't believe",
    r"^this one weird",
    r"^doctors hate",
    r"^i never thought",
    r"^stop doing this",
    r"^the secret (to|of)",
    r"^have you (ever|heard)",
    r"^everybody (knows|does)",
    r"^just wanted to share",
    r"^i can't believe",
    r"^trending now",
    r"^going viral",
    r"^\[",      # unclosed placeholder at start
    r"\[.*?\]",  # any [placeholder] anywhere
]

# Compiled once at import.
_GENERIC_RE = [re.compile(p, re.IGNORECASE) for p in _GENERIC_OPENINGS]
_PLACEHOLDER_RE = re.compile(r"\[.+?\]")


# ─────────────────────────────────────────────
# QA result
# ─────────────────────────────────────────────

@dataclass
class HookQAResult:
    hook: str
    passed: bool
    flags: list[str] = field(default_factory=list)
    similarity_to_source: float = 0.0
    structural_pattern: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "hook":                 self.hook,
            "passed":               self.passed,
            "flags":                self.flags,
            "similarity_to_source": round(self.similarity_to_source, 3),
            "structural_pattern":   self.structural_pattern,
        }


# ─────────────────────────────────────────────
# Core checks
# ─────────────────────────────────────────────

def _ngrams(text: str, n: int = 3) -> set[str]:
    """Character n-grams of a normalised string."""
    t = re.sub(r"[^\w\s]", "", text.lower())
    t = re.sub(r"\s+", " ", t).strip()
    return {t[i:i+n] for i in range(len(t) - n + 1)} if len(t) >= n else set()


def _jaccard(a: str, b: str, n: int = 3) -> float:
    """Jaccard similarity of character trigrams."""
    ga, gb = _ngrams(a, n), _ngrams(b, n)
    if not ga and not gb:
        return 1.0
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def _opening_pattern(hook: str) -> str:
    """Return the first 4 tokens (lowercased) as a structural fingerprint."""
    tokens = re.sub(r"[^\w\s]", "", hook.lower()).split()
    return " ".join(tokens[:4])


def check_placeholder(hook: str) -> list[str]:
    if _PLACEHOLDER_RE.search(hook):
        return ["placeholder: contains [bracket] text"]
    return []


def check_generic(hook: str) -> list[str]:
    flags = []
    for pattern in _GENERIC_RE:
        if pattern.search(hook):
            flags.append(f"generic: matches weak pattern '{pattern.pattern}'")
    return flags


def check_length(hook: str, max_chars: int = 140) -> list[str]:
    if len(hook) > max_chars:
        return [f"too_long: {len(hook)} chars (max {max_chars})"]
    if len(hook) < 20:
        return ["too_short: fewer than 20 characters"]
    return []


def check_similarity_to_source(hook: str, source_hooks: list[str], threshold: float) -> tuple[float, list[str]]:
    """Return max similarity to source hooks and a flag list."""
    if not source_hooks:
        return 0.0, []
    max_sim = max(_jaccard(hook, src) for src in source_hooks)
    if max_sim >= threshold:
        return max_sim, [f"too_similar_to_source: {max_sim:.0%} match (threshold {threshold:.0%})"]
    return max_sim, []


def check_near_duplicate(hook: str, accepted: list[str], threshold: float = 0.75) -> list[str]:
    """Flag if too similar to an already-accepted hook."""
    for prev in accepted:
        if _jaccard(hook, prev) >= threshold:
            return [f"near_duplicate: {_jaccard(hook, prev):.0%} similar to '{prev[:60]}'"]
    return []


def check_specificity(hook: str, required_terms: list[str]) -> list[str]:
    """Flag if none of the required product terms appear in the hook."""
    if not required_terms:
        return []
    hook_lower = hook.lower()
    if not any(t.lower() in hook_lower for t in required_terms):
        return [f"low_specificity: none of {required_terms[:3]} found in hook"]
    return []


# ─────────────────────────────────────────────
# Main filter function
# ─────────────────────────────────────────────

def filter_hooks(
    hooks: list[str],
    source_hooks: list[str] | None = None,
    config: QAConfig | None = None,
    required_terms: list[str] | None = None,
) -> list[HookQAResult]:
    """Run all QA checks on a list of generated hooks.

    Args:
        hooks:          Raw hooks from the LLM.
        source_hooks:   Example hooks from winning creatives (similarity check).
        config:         QAConfig — defaults used if None.
        required_terms: Product/fruit terms that must appear in each hook.

    Returns:
        List of HookQAResult in input order.
        Use `[r for r in results if r.passed]` to get clean hooks.
    """
    cfg = config or QAConfig()
    src = source_hooks or []

    max_per_pattern = _DIVERSITY_MAX.get(cfg.diversity, 3)
    pattern_counts: dict[str, int] = {}
    accepted: list[str] = []
    results: list[HookQAResult] = []

    for hook in hooks:
        hook = hook.strip()
        if not hook:
            continue

        flags: list[str] = []

        # Hard rejections (applied before diversity tracking).
        flags += check_placeholder(hook)
        flags += check_length(hook)

        # Source similarity.
        sim, sim_flags = check_similarity_to_source(hook, src, cfg.similarity_threshold)
        flags += sim_flags

        # Near-duplicate vs already-accepted.
        flags += check_near_duplicate(hook, accepted, cfg.duplicate_threshold)

        # Generic phrasing (advisory — does not block unless combined with other flags).
        generic_flags = check_generic(hook)
        flags += generic_flags

        # Product specificity (advisory).
        flags += check_specificity(hook, required_terms or [])

        # Structural diversity.
        pat = _opening_pattern(hook)
        count = pattern_counts.get(pat, 0)
        if count >= max_per_pattern:
            flags.append(f"structural_repetition: '{pat}' used {count} times (max {max_per_pattern})")

        # A hook passes if it has no hard flags.
        # Hard flags: placeholder, too_long, too_short, too_similar_to_source,
        #             near_duplicate, structural_repetition.
        # Advisory flags: generic, low_specificity (reported but don't block).
        hard_flags = [f for f in flags if not f.startswith(("generic:", "low_specificity:"))]
        passed = len(hard_flags) == 0

        if passed:
            pattern_counts[pat] = count + 1
            accepted.append(hook)

        results.append(HookQAResult(
            hook=hook,
            passed=passed,
            flags=flags,
            similarity_to_source=sim,
            structural_pattern=pat,
        ))

    return results


# ─────────────────────────────────────────────
# Summary helper
# ─────────────────────────────────────────────

def summarise_qa(results: list[HookQAResult]) -> dict[str, Any]:
    total    = len(results)
    passed   = sum(1 for r in results if r.passed)
    rejected = total - passed
    flag_counts: dict[str, int] = {}
    for r in results:
        for f in r.flags:
            key = f.split(":")[0]
            flag_counts[key] = flag_counts.get(key, 0) + 1
    return {
        "total":       total,
        "passed":      passed,
        "rejected":    rejected,
        "pass_rate":   round(passed / total, 2) if total else 0,
        "flag_counts": flag_counts,
    }
