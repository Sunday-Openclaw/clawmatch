"""Tests for content_guard — message compliance engine."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from skill_runtime.content_guard import ComplianceResult, check_message_compliance


def _policy(
    avoid_phrases: list[str] | None = None,
    conversation_avoid: list[str] | None = None,
) -> dict:
    return {
        "messaging": {
            "avoidPhrases": avoid_phrases or [],
        },
        "conversationPolicy": {
            "avoid": conversation_avoid or [],
        },
    }


ALL_TRIGGERS = {"before_contact_share", "before_commitment"}
NO_TRIGGERS: set[str] = set()


# ---------------------------------------------------------------------------
# Basic pass / block
# ---------------------------------------------------------------------------


def test_passes_clean_message():
    result = check_message_compliance("Hello, I'd like to learn more.", _policy(), ALL_TRIGGERS)
    assert result.passed
    assert result.violations == []


def test_passes_empty_message():
    result = check_message_compliance("", _policy(), ALL_TRIGGERS)
    assert result.passed


# ---------------------------------------------------------------------------
# avoidPhrases
# ---------------------------------------------------------------------------


def test_blocks_avoid_phrase():
    policy = _policy(avoid_phrases=["perfect match"])
    result = check_message_compliance("This is a perfect match!", policy, NO_TRIGGERS)
    assert not result.passed
    assert any(v.rule == "avoid_phrase" and "perfect match" in v.detail for v in result.violations)


def test_blocks_avoid_phrase_case_insensitive():
    policy = _policy(avoid_phrases=["Game-Changing"])
    result = check_message_compliance("This is a GAME-CHANGING opportunity", policy, NO_TRIGGERS)
    assert not result.passed


def test_passes_when_phrase_absent():
    policy = _policy(avoid_phrases=["perfect match"])
    result = check_message_compliance("This looks interesting.", policy, NO_TRIGGERS)
    assert result.passed


# ---------------------------------------------------------------------------
# conversationPolicy.avoid
# ---------------------------------------------------------------------------


def test_blocks_conversation_avoid():
    policy = _policy(conversation_avoid=["making commitments on behalf of owner"])
    result = check_message_compliance(
        "I'm making commitments behalf owner right now.",
        policy,
        NO_TRIGGERS,
    )
    assert not result.passed
    assert any(v.rule == "conversation_avoid" for v in result.violations)


def test_passes_conversation_avoid_when_no_match():
    policy = _policy(conversation_avoid=["negotiating final terms without human review"])
    result = check_message_compliance("Let me check and get back to you.", policy, NO_TRIGGERS)
    assert result.passed


# ---------------------------------------------------------------------------
# contact_share detection
# ---------------------------------------------------------------------------


def test_blocks_contact_share_email():
    result = check_message_compliance(
        "You can reach me at user@example.com",
        _policy(),
        {"before_contact_share"},
    )
    assert not result.passed
    assert any(v.rule == "contact_share" for v in result.violations)


def test_blocks_contact_share_phone():
    result = check_message_compliance(
        "Call me at +1-555-123-4567",
        _policy(),
        {"before_contact_share"},
    )
    assert not result.passed
    assert any(v.rule == "contact_share" for v in result.violations)


def test_blocks_contact_share_keyword():
    result = check_message_compliance(
        "Let's move to WeChat for faster communication",
        _policy(),
        {"before_contact_share"},
    )
    assert not result.passed
    assert any(v.rule == "contact_share" for v in result.violations)


def test_contact_share_allowed_when_trigger_disabled():
    result = check_message_compliance(
        "You can reach me at user@example.com",
        _policy(),
        NO_TRIGGERS,
    )
    assert result.passed


# ---------------------------------------------------------------------------
# commitment language
# ---------------------------------------------------------------------------


def test_blocks_commitment_language():
    result = check_message_compliance(
        "I agree to the terms you proposed.",
        _policy(),
        {"before_commitment"},
    )
    assert not result.passed
    assert any(v.rule == "commitment" for v in result.violations)


def test_commitment_allowed_when_trigger_disabled():
    result = check_message_compliance(
        "I agree to the terms.",
        _policy(),
        NO_TRIGGERS,
    )
    assert result.passed


# ---------------------------------------------------------------------------
# Multiple violations
# ---------------------------------------------------------------------------


def test_multiple_violations():
    policy = _policy(avoid_phrases=["definitely interested"])
    result = check_message_compliance(
        "I'm definitely interested! Email me at test@example.com",
        policy,
        {"before_contact_share"},
    )
    assert not result.passed
    rules = {v.rule for v in result.violations}
    assert "avoid_phrase" in rules
    assert "contact_share" in rules


# ---------------------------------------------------------------------------
# to_dict serialization
# ---------------------------------------------------------------------------


def test_compliance_result_to_dict():
    result = check_message_compliance("perfect match here", _policy(avoid_phrases=["perfect match"]), NO_TRIGGERS)
    d = result.to_dict()
    assert d["passed"] is False
    assert isinstance(d["violations"], list)
    assert d["violations"][0]["rule"] == "avoid_phrase"
