"""Tests for evaluation interface."""
from evaluation_interface import EvaluationResult


class TestEvaluationResult:
    def test_to_dict(self):
        result = EvaluationResult(
            score=85,
            confidence=0.9,
            reason="Good fit",
            should_connect=True,
            source="matcher",
        )
        d = result.to_dict()
        assert d["score"] == 85
        assert d["confidence"] == 0.9
        assert d["reason"] == "Good fit"
        assert d["should_connect"] is True
        assert d["source"] == "matcher"
        assert d["best_project_name"] is None

    def test_from_dict(self):
        data = {
            "score": 72,
            "confidence": 0.8,
            "reason": "Partial alignment",
            "should_connect": False,
            "source": "autopilot",
        }
        result = EvaluationResult.from_dict(data)
        assert result.score == 72
        assert result.confidence == 0.8
        assert result.should_connect is False

    def test_from_dict_defaults(self):
        result = EvaluationResult.from_dict({})
        assert result.score == 0
        assert result.confidence == 0.0
        assert result.reason == ""
        assert result.should_connect is False
        assert result.source == "unknown"

    def test_roundtrip(self):
        original = EvaluationResult(
            score=95,
            confidence=0.95,
            reason="Excellent match",
            should_connect=True,
            best_project_name="Project Alpha",
            source="openclaw",
        )
        restored = EvaluationResult.from_dict(original.to_dict())
        assert restored.score == original.score
        assert restored.confidence == original.confidence
        assert restored.reason == original.reason
        assert restored.best_project_name == original.best_project_name
