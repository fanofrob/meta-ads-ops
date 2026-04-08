"""Tests for creative_intelligence.generation.hook_qa."""
import pytest

from creative_intelligence.generation.hook_qa import (
    QAConfig,
    HookQAResult,
    filter_hooks,
    summarise_qa,
    check_placeholder,
    check_generic,
    check_length,
    check_similarity_to_source,
    check_near_duplicate,
    check_specificity,
)


# ─────────────────────────────────────────────
# Individual check tests
# ─────────────────────────────────────────────

class TestCheckPlaceholder:
    def test_clean_hook_passes(self):
        assert check_placeholder("Taste mangoes the way they should taste.") == []

    def test_bracket_placeholder_flagged(self):
        flags = check_placeholder("Try the best [fruit] of your life.")
        assert len(flags) == 1
        assert "placeholder" in flags[0]

    def test_empty_brackets_not_flagged(self):
        # _PLACEHOLDER_RE uses .+ (1+ chars), so empty [] is NOT matched — correct behaviour.
        flags = check_placeholder("Click [] here.")
        assert len(flags) == 0

    def test_single_char_brackets_flagged(self):
        flags = check_placeholder("Click [x] here.")
        assert len(flags) == 1


class TestCheckGeneric:
    def test_clean_hook_no_flags(self):
        assert check_generic("Farm-fresh mangoes shipped overnight.") == []

    def test_are_you_still_struggling_flagged(self):
        flags = check_generic("Are you still struggling to find good fruit?")
        assert any("generic" in f for f in flags)

    def test_heres_what_nobody_tells_you_flagged(self):
        flags = check_generic("Here's what nobody tells you about supermarket mangoes.")
        assert any("generic" in f for f in flags)

    def test_case_insensitive(self):
        flags = check_generic("GOING VIRAL: the mango everyone's talking about.")
        assert any("generic" in f for f in flags)

    def test_multiple_patterns_accumulate(self):
        # All _GENERIC_OPENINGS patterns use ^ start anchor except \[.*?\] (bracket anywhere).
        # A hook that starts with "you won't believe" and also contains [bracket] triggers both.
        flags = check_generic("You won't believe the [fruit] difference.")
        assert len(flags) == 2


class TestCheckLength:
    def test_ok_length(self):
        assert check_length("Taste mangoes the way they should taste.") == []

    def test_too_short(self):
        flags = check_length("Buy now.")
        assert any("too_short" in f for f in flags)

    def test_exactly_20_chars_passes(self):
        assert check_length("A" * 20) == []

    def test_exactly_140_chars_passes(self):
        assert check_length("A" * 140) == []

    def test_141_chars_fails(self):
        flags = check_length("A" * 141)
        assert any("too_long" in f for f in flags)

    def test_custom_max_chars(self):
        flags = check_length("A" * 50, max_chars=40)
        assert any("too_long" in f for f in flags)


class TestCheckSimilarityToSource:
    def test_no_sources_always_passes(self):
        sim, flags = check_similarity_to_source("Any hook", [], threshold=0.5)
        assert sim == 0.0
        assert flags == []

    def test_identical_hook_rejected(self):
        source = "This is how real mangoes are supposed to taste!"
        sim, flags = check_similarity_to_source(source, [source], threshold=0.65)
        assert sim >= 0.65
        assert any("too_similar_to_source" in f for f in flags)

    def test_completely_different_hook_passes(self):
        source = "This is how real mangoes are supposed to taste!"
        hook   = "Farm-fresh durians shipped overnight from Southeast Asia."
        sim, flags = check_similarity_to_source(hook, [source], threshold=0.65)
        assert flags == []

    def test_threshold_respected(self):
        source = "Taste the difference of real farm-fresh mango."
        hook   = "Taste the difference of real farm-fresh mango today."
        # Very similar — should trip at default threshold
        sim, flags = check_similarity_to_source(hook, [source], threshold=0.65)
        assert any("too_similar_to_source" in f for f in flags)


class TestCheckNearDuplicate:
    def test_no_accepted_always_passes(self):
        assert check_near_duplicate("Any hook", [], threshold=0.75) == []

    def test_near_identical_flagged(self):
        accepted = ["Farm-fresh mangoes shipped to your door."]
        flags = check_near_duplicate(
            "Farm-fresh mangoes shipped to your door today.", accepted, threshold=0.75
        )
        assert any("near_duplicate" in f for f in flags)

    def test_distinct_hook_passes(self):
        accepted = ["Farm-fresh mangoes shipped to your door."]
        flags = check_near_duplicate(
            "You've never tasted mangoes like these — tree-ripened perfection.", accepted, threshold=0.75
        )
        assert flags == []


class TestCheckSpecificity:
    def test_no_required_terms_always_passes(self):
        assert check_specificity("Any hook", []) == []

    def test_required_term_present_passes(self):
        assert check_specificity("Taste our mangoes today.", ["mango", "mangoes"]) == []

    def test_required_term_missing_flagged(self):
        flags = check_specificity("Taste the best fruit ever.", ["mango", "mangoes"])
        assert any("low_specificity" in f for f in flags)

    def test_case_insensitive_match(self):
        assert check_specificity("Taste our MANGO today.", ["mango"]) == []


# ─────────────────────────────────────────────
# filter_hooks integration tests
# ─────────────────────────────────────────────

class TestFilterHooks:
    def test_clean_hooks_all_pass(self):
        hooks = [
            "Farm-fresh mangoes shipped overnight from our grove.",
            "You've never tasted mangoes like these — tree-ripened perfection.",
            "Grocery store mangoes can't compete with what we grow.",
        ]
        results = filter_hooks(hooks)
        assert all(r.passed for r in results)

    def test_placeholder_hook_rejected(self):
        hooks = ["Taste our [fruit] today — you won't forget it."]
        results = filter_hooks(hooks)
        assert not results[0].passed
        assert any("placeholder" in f for f in results[0].flags)

    def test_too_short_hook_rejected(self):
        hooks = ["Buy now."]
        results = filter_hooks(hooks)
        assert not results[0].passed

    def test_too_long_hook_rejected(self):
        hooks = ["A" * 141]
        results = filter_hooks(hooks)
        assert not results[0].passed

    def test_similar_to_source_rejected(self):
        source = "This is how real mangoes are supposed to taste!"
        hooks  = [source]
        results = filter_hooks(hooks, source_hooks=[source], config=QAConfig(similarity_threshold=0.5))
        assert not results[0].passed

    def test_near_duplicate_within_batch_rejected(self):
        hooks = [
            "Farm-fresh mangoes shipped overnight from our grove.",
            "Farm-fresh mangoes shipped overnight from our grove today.",  # near-dup
        ]
        results = filter_hooks(hooks, config=QAConfig(duplicate_threshold=0.75))
        assert results[0].passed
        assert not results[1].passed

    def _repetition_hooks(self) -> list[str]:
        """4 hooks that share the same 4-token opening pattern but are not near-duplicates."""
        # All start with "taste real tree ripened" (4 tokens) but content diverges.
        return [
            "Taste real tree ripened mangoes shipped same-day from our grove.",
            "Taste real tree ripened citrus that grocery stores will never carry.",
            "Taste real tree ripened papayas bursting with authentic tropical sweetness.",
            "Taste real tree ripened lychees picked hours before your order ships.",
        ]

    def test_structural_repetition_medium_diversity(self):
        # Same 4-token opening used 4 times — medium allows max 3, so 4th is rejected.
        hooks = self._repetition_hooks()
        results = filter_hooks(hooks, config=QAConfig(diversity="medium"))
        passed = [r for r in results if r.passed]
        assert len(passed) == 3

    def test_structural_repetition_high_diversity(self):
        # Same 4-token opening used 4 times — high allows max 2, so 3rd and 4th are rejected.
        hooks = self._repetition_hooks()
        results = filter_hooks(hooks, config=QAConfig(diversity="high"))
        passed = [r for r in results if r.passed]
        assert len(passed) == 2

    def test_structural_repetition_low_diversity(self):
        # low: no structural limit — all 4 pass (subject to other checks).
        hooks = self._repetition_hooks()
        results = filter_hooks(hooks, config=QAConfig(diversity="low"))
        assert all(r.passed for r in results)

    def test_generic_flag_is_advisory_not_blocking(self):
        # Generic phrasing alone should NOT block
        hooks = ["Are you still struggling to find good mangoes?"]
        results = filter_hooks(hooks)
        assert results[0].passed
        assert any("generic" in f for f in results[0].flags)

    def test_specificity_flag_is_advisory_not_blocking(self):
        # Missing required terms alone should NOT block
        hooks = ["Farm-fresh fruit shipped overnight from our grove."]
        results = filter_hooks(hooks, required_terms=["mango", "mangoes"])
        assert results[0].passed
        assert any("low_specificity" in f for f in results[0].flags)

    def test_empty_hooks_skipped(self):
        hooks = ["", "   ", "Valid hook about mangoes with enough characters."]
        results = filter_hooks(hooks)
        assert len(results) == 1

    def test_order_preserved(self):
        hooks = [
            "Farm-fresh mangoes shipped overnight.",
            "A" * 141,  # too long
            "You've never tasted mangoes like these.",
        ]
        results = filter_hooks(hooks)
        assert results[0].passed
        assert not results[1].passed
        assert results[2].passed

    def test_qa_result_to_dict(self):
        hooks = ["Farm-fresh mangoes shipped overnight from our grove."]
        results = filter_hooks(hooks)
        d = results[0].to_dict()
        assert set(d.keys()) == {"hook", "passed", "flags", "similarity_to_source", "structural_pattern"}
        assert isinstance(d["similarity_to_source"], float)


# ─────────────────────────────────────────────
# summarise_qa tests
# ─────────────────────────────────────────────

class TestSummariseQA:
    def test_all_pass(self):
        hooks = [
            "Farm-fresh mangoes shipped overnight.",
            "You've never tasted mangoes like these.",
        ]
        results = filter_hooks(hooks)
        summary = summarise_qa(results)
        assert summary["total"] == 2
        assert summary["passed"] == 2
        assert summary["rejected"] == 0
        assert summary["pass_rate"] == 1.0

    def test_mixed(self):
        hooks = [
            "Farm-fresh mangoes shipped overnight from our grove.",
            "A" * 141,
        ]
        results = filter_hooks(hooks)
        summary = summarise_qa(results)
        assert summary["total"] == 2
        assert summary["passed"] == 1
        assert summary["rejected"] == 1
        assert summary["pass_rate"] == 0.5
        assert "too_long" in summary["flag_counts"]

    def test_empty_results(self):
        summary = summarise_qa([])
        assert summary["total"] == 0
        assert summary["pass_rate"] == 0
