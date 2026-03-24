"""Tests for message_patrol — inbox scanning engine."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from skill_runtime.message_patrol import MessagePatrolReport, build_policy_hints, run_message_patrol
from skill_runtime.policy_runtime import db_policy_to_runtime_bundle


AGENT_USER_ID = "agent-user-001"
OTHER_USER_ID = "other-user-002"


def _make_bundle(reply_policy: str = "draft_then_confirm") -> dict:
    row = {
        "reply_policy": reply_policy,
        "patrol_scope": "both",
        "message_patrol_interval": "10m",
        "is_active": True,
    }
    return db_policy_to_runtime_bundle(row, project_id="proj-1", owner_user_id=AGENT_USER_ID)


def _conversation(conv_id: str = "conv-1", status: str = "active") -> dict:
    return {
        "id": conv_id,
        "project_id": "proj-1",
        "project_name": "Test Project",
        "status": status,
    }


def _message(msg_id: str, sender: str, text: str = "hello") -> dict:
    return {
        "id": msg_id,
        "sender_user_id": sender,
        "message": text,
        "created_at": "2026-03-22T12:00:00Z",
    }


def _mock_client(messages: list[dict] | None = None) -> MagicMock:
    client = MagicMock()
    client.list_messages.return_value = messages or []
    return client


# ---------------------------------------------------------------------------
# No new messages
# ---------------------------------------------------------------------------


def test_no_new_messages():
    client = _mock_client([_message("m1", AGENT_USER_ID)])
    report = run_message_patrol(
        agent_user_id=AGENT_USER_ID,
        conversations=[_conversation()],
        policy_bundle=_make_bundle(),
        conversation_state={"conv-1": {"last_seen_message_id": "m1"}},
        client=client,
    )
    assert report.items_needing_attention == []
    assert report.conversations_scanned == 1


def test_no_conversations():
    report = run_message_patrol(
        agent_user_id=AGENT_USER_ID,
        conversations=[],
        policy_bundle=_make_bundle(),
        conversation_state={},
        client=_mock_client(),
    )
    assert report.conversations_scanned == 0
    assert report.items_needing_attention == []


# ---------------------------------------------------------------------------
# Detects new messages
# ---------------------------------------------------------------------------


def test_detects_new_messages():
    messages = [
        _message("m1", AGENT_USER_ID, "hi"),
        _message("m2", OTHER_USER_ID, "hey there"),
    ]
    client = _mock_client(messages)
    report = run_message_patrol(
        agent_user_id=AGENT_USER_ID,
        conversations=[_conversation()],
        policy_bundle=_make_bundle(),
        conversation_state={"conv-1": {"last_seen_message_id": "m1"}},
        client=client,
    )
    assert len(report.items_needing_attention) == 1
    item = report.items_needing_attention[0]
    assert item.conversation_id == "conv-1"
    assert len(item.new_messages) == 1
    assert item.new_messages[0]["id"] == "m2"


def test_first_run_all_messages_new():
    messages = [
        _message("m1", OTHER_USER_ID, "hello"),
        _message("m2", OTHER_USER_ID, "are you there?"),
    ]
    client = _mock_client(messages)
    report = run_message_patrol(
        agent_user_id=AGENT_USER_ID,
        conversations=[_conversation()],
        policy_bundle=_make_bundle(),
        conversation_state={},
        client=client,
    )
    assert len(report.items_needing_attention) == 1
    assert len(report.items_needing_attention[0].new_messages) == 2


# ---------------------------------------------------------------------------
# Filters own messages
# ---------------------------------------------------------------------------


def test_skips_own_messages():
    messages = [
        _message("m1", AGENT_USER_ID, "hi"),
        _message("m2", AGENT_USER_ID, "following up"),
    ]
    client = _mock_client(messages)
    report = run_message_patrol(
        agent_user_id=AGENT_USER_ID,
        conversations=[_conversation()],
        policy_bundle=_make_bundle(),
        conversation_state={},
        client=client,
    )
    assert report.items_needing_attention == []


# ---------------------------------------------------------------------------
# Reply policy mapping
# ---------------------------------------------------------------------------


def test_reply_policy_notify_only():
    messages = [_message("m1", OTHER_USER_ID)]
    client = _mock_client(messages)
    report = run_message_patrol(
        agent_user_id=AGENT_USER_ID,
        conversations=[_conversation()],
        policy_bundle=_make_bundle("notify_only"),
        conversation_state={},
        client=client,
    )
    assert report.items_needing_attention[0].reply_action == "notify"


def test_reply_policy_draft_then_confirm():
    messages = [_message("m1", OTHER_USER_ID)]
    client = _mock_client(messages)
    report = run_message_patrol(
        agent_user_id=AGENT_USER_ID,
        conversations=[_conversation()],
        policy_bundle=_make_bundle("draft_then_confirm"),
        conversation_state={},
        client=client,
    )
    assert report.items_needing_attention[0].reply_action == "draft_reply"


def test_reply_policy_auto_reply_simple():
    messages = [_message("m1", OTHER_USER_ID)]
    client = _mock_client(messages)
    report = run_message_patrol(
        agent_user_id=AGENT_USER_ID,
        conversations=[_conversation()],
        policy_bundle=_make_bundle("auto_reply_simple"),
        conversation_state={},
        client=client,
    )
    assert report.items_needing_attention[0].reply_action == "reply_now"


# ---------------------------------------------------------------------------
# State updates
# ---------------------------------------------------------------------------


def test_updates_last_seen_message_id():
    messages = [_message("m1", OTHER_USER_ID), _message("m2", OTHER_USER_ID)]
    client = _mock_client(messages)
    report = run_message_patrol(
        agent_user_id=AGENT_USER_ID,
        conversations=[_conversation()],
        policy_bundle=_make_bundle(),
        conversation_state={},
        client=client,
    )
    assert "conv-1" in report.state_updates
    assert report.state_updates["conv-1"]["last_seen_message_id"] == "m2"


# ---------------------------------------------------------------------------
# Policy hints structure
# ---------------------------------------------------------------------------


def test_policy_hints_structure():
    messages = [_message("m1", OTHER_USER_ID)]
    client = _mock_client(messages)
    report = run_message_patrol(
        agent_user_id=AGENT_USER_ID,
        conversations=[_conversation()],
        policy_bundle=_make_bundle(),
        conversation_state={},
        client=client,
    )
    hints = report.items_needing_attention[0].policy_hints
    assert "tone" in hints
    assert "length" in hints
    assert "avoid_phrases" in hints
    assert "conversation_goals" in hints
    assert "conversation_avoid" in hints
    assert isinstance(hints["conversation_goals"], list)


# ---------------------------------------------------------------------------
# Skips inactive conversation statuses
# ---------------------------------------------------------------------------


def test_skips_closed_conversations():
    messages = [_message("m1", OTHER_USER_ID)]
    client = _mock_client(messages)
    report = run_message_patrol(
        agent_user_id=AGENT_USER_ID,
        conversations=[_conversation(status="closed")],
        policy_bundle=_make_bundle(),
        conversation_state={},
        client=client,
    )
    assert report.conversations_scanned == 0
    assert report.items_needing_attention == []


# ---------------------------------------------------------------------------
# build_policy_hints
# ---------------------------------------------------------------------------


def test_build_policy_hints():
    policy = {
        "messaging": {
            "tone": "warm-analytical",
            "length": "short",
            "avoidPhrases": ["game-changing"],
        },
        "conversationPolicy": {
            "goals": ["clarify scope"],
            "avoid": ["making commitments"],
        },
    }
    hints = build_policy_hints(policy)
    assert hints["tone"] == "warm-analytical"
    assert hints["avoid_phrases"] == ["game-changing"]
    assert hints["conversation_goals"] == ["clarify scope"]
    assert hints["conversation_avoid"] == ["making commitments"]


# ---------------------------------------------------------------------------
# to_dict serialization
# ---------------------------------------------------------------------------


def test_report_to_dict():
    messages = [_message("m1", OTHER_USER_ID)]
    client = _mock_client(messages)
    report = run_message_patrol(
        agent_user_id=AGENT_USER_ID,
        conversations=[_conversation()],
        policy_bundle=_make_bundle(),
        conversation_state={},
        client=client,
    )
    d = report.to_dict()
    assert "ran_at" in d
    assert "conversations_scanned" in d
    assert isinstance(d["items_needing_attention"], list)
    assert len(d["items_needing_attention"]) == 1
    assert d["items_needing_attention"][0]["conversation_id"] == "conv-1"
