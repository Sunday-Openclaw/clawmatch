"""Tests for autopilot evaluation logic (no network calls)."""
from clawmatch_autopilot import (
    tokenize, overlap_count, fit_band, deep_merge,
    contains_any_phrase, must_have_any,
)


class TestTokenize:
    def test_basic(self):
        tokens = tokenize("I love Python coding")
        assert "python" in tokens
        assert "love" in tokens
        assert "coding" in tokens

    def test_short_words_excluded(self):
        tokens = tokenize("I am a Python dev")
        assert "am" not in tokens
        assert "python" in tokens
        assert "dev" in tokens

    def test_empty_input(self):
        assert tokenize("") == set()
        assert tokenize(None) == set()

    def test_special_characters(self):
        tokens = tokenize("machine-learning & AI!")
        assert "machine" in tokens
        assert "learning" in tokens


class TestOverlapCount:
    def test_basic_overlap(self):
        project = {"project_name": "AI Research", "public_summary": "python coding project", "tags": "ai,ml"}
        count = overlap_count(project, ["python", "ai"])
        assert count >= 1

    def test_no_overlap(self):
        project = {"project_name": "Finance", "public_summary": "accounting software", "tags": "finance"}
        count = overlap_count(project, ["biology", "chemistry"])
        assert count == 0

    def test_empty_items(self):
        project = {"project_name": "Test", "public_summary": "test", "tags": ""}
        assert overlap_count(project, []) == 0


class TestFitBand:
    def test_very_high(self):
        assert fit_band(0.95) == "very-high"
        assert fit_band(0.90) == "very-high"

    def test_high(self):
        assert fit_band(0.85) == "high"

    def test_medium_high(self):
        assert fit_band(0.75) == "medium-high"

    def test_medium(self):
        assert fit_band(0.60) == "medium"

    def test_low_medium(self):
        assert fit_band(0.45) == "low-medium"

    def test_low(self):
        assert fit_band(0.2) == "low"
        assert fit_band(0.0) == "low"


class TestDeepMerge:
    def test_nested_merge(self):
        base = {"a": {"b": 1, "c": 2}}
        override = {"a": {"b": 99}}
        result = deep_merge(base, override)
        assert result["a"]["b"] == 99
        assert result["a"]["c"] == 2

    def test_non_dict_override(self):
        assert deep_merge({"a": 1}, "string") == "string"

    def test_add_new_keys(self):
        base = {"a": 1}
        override = {"b": 2}
        result = deep_merge(base, override)
        assert result == {"a": 1, "b": 2}


class TestContainsAnyPhrase:
    def test_contains(self):
        project = {"project_name": "Crypto Trading Bot", "public_summary": "automated crypto trading", "tags": "crypto"}
        assert contains_any_phrase(project, ["crypto"]) is True

    def test_not_contains(self):
        project = {"project_name": "AI Research", "public_summary": "machine learning", "tags": "ai"}
        assert contains_any_phrase(project, ["crypto"]) is False


class TestMustHaveAny:
    def test_empty_list_returns_true(self):
        project = {"project_name": "Test", "public_summary": "test", "tags": ""}
        assert must_have_any(project, []) is True

    def test_has_match(self):
        project = {"project_name": "Research Lab", "public_summary": "academic research", "tags": "research"}
        assert must_have_any(project, ["research"]) is True

    def test_no_match(self):
        project = {"project_name": "Trading", "public_summary": "day trading", "tags": "finance"}
        assert must_have_any(project, ["research", "academic"]) is False
