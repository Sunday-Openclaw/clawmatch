"""
Helpers for turning dashboard-stored agent policies into executable runtime config.

The new runtime is intentionally agent-first:

- market/conversation decisions are made by the OpenClaw agent
- the skill only handles scheduling, state, execution, and safety checks
- policy is reduced to patrol cadence + send behavior + free-form requirements

This module still accepts legacy dashboard rows so older data can be migrated
without breaking the current runtime.
"""

from __future__ import annotations

import copy
import re
from datetime import datetime, timedelta, timezone
from typing import Any

DEFAULT_DB_POLICY = {
    "market_patrol_interval": "30m",
    "message_patrol_interval": "10m",
    "interest_behavior": "notify_then_send",
    "reply_behavior": "notify_then_send",
    "extra_requirements": "",
    "is_active": True,
}

MARKET_INTERVAL_MINUTES = {
    "10m": 10,
    "30m": 30,
    "1h": 60,
}

MESSAGE_INTERVAL_MINUTES = {
    "5m": 5,
    "10m": 10,
    "30m": 30,
}

INTEREST_POLICY_TO_BEHAVIOR = {
    "notify_only": "notify_then_send",
    "draft_then_confirm": "notify_then_send",
    "auto_send_high_confidence": "direct_send",
}

REPLY_POLICY_TO_BEHAVIOR = {
    "notify_only": "notify_then_send",
    "draft_then_confirm": "notify_then_send",
    "auto_reply_simple": "direct_send",
}

BEHAVIOR_TO_LEGACY_INTEREST_POLICY = {
    "notify_then_send": "draft_then_confirm",
    "direct_send": "auto_send_high_confidence",
}

BEHAVIOR_TO_LEGACY_REPLY_POLICY = {
    "notify_then_send": "draft_then_confirm",
    "direct_send": "auto_reply_simple",
}

EXTRA_REQUIREMENT_PREFIXES = (
    "avoid:",
    "forbid:",
    "must avoid:",
    "do not:",
    "don't:",
    "禁止：",
    "禁止:",
)


def _normalize_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, list) else re.split(r"[,;\n]+", str(value))

    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = " ".join(str(item).strip().split())
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(text)
    return normalized


def _merge_extra_requirements(
    explicit_value: Any,
    collaborator_preferences: dict[str, Any] | None,
    legacy_row: dict[str, Any] | None,
) -> str:
    if explicit_value is not None and str(explicit_value).strip():
        return str(explicit_value).strip()

    prefs = collaborator_preferences or {}
    lines: list[str] = []

    priority_tags = _normalize_text_list(prefs.get("priorityTags"))
    if priority_tags:
        lines.append(f"Prioritize projects or conversations related to: {', '.join(priority_tags)}.")

    preferred_style = str(prefs.get("preferredWorkingStyle") or "").strip()
    if preferred_style:
        lines.append(f"Preferred working style: {preferred_style}.")

    constraints = str(prefs.get("constraints") or "").strip()
    if constraints:
        lines.append(f"Legacy constraints: {constraints}")

    avoid_phrases = _normalize_text_list(prefs.get("avoidPhrases"))
    if avoid_phrases:
        lines.append("Avoid these phrases in outgoing messages: " + "; ".join(avoid_phrases))

    conversation_goals = _normalize_text_list(prefs.get("conversationGoals"))
    if conversation_goals:
        lines.append("Conversation goals: " + "; ".join(conversation_goals))

    conversation_avoid = _normalize_text_list(prefs.get("conversationAvoid"))
    if conversation_avoid:
        lines.append("Conversation topics to avoid: " + "; ".join(conversation_avoid))

    if legacy_row:
        project_mode = legacy_row.get("project_mode")
        if project_mode:
            lines.append(f"Legacy project mode preference: {project_mode}.")

    return "\n".join(lines).strip()


def _extract_extra_requirement_blocklist(extra_requirements: str) -> list[str]:
    blocked: list[str] = []
    for raw_line in extra_requirements.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lowered = line.lower()
        matched_prefix = next((prefix for prefix in EXTRA_REQUIREMENT_PREFIXES if lowered.startswith(prefix)), None)
        if matched_prefix:
            phrase = line[len(matched_prefix) :].strip(" -:：")
            if phrase:
                blocked.append(phrase)
            continue
        if "avoid these phrases" in lowered:
            continue
        inline_match = re.search(r"\bavoid\s+([a-zA-Z0-9 _-]+)", lowered)
        if inline_match:
            phrase = inline_match.group(1).strip(" -:：")
            if phrase:
                blocked.append(phrase)
    return _normalize_text_list(blocked)


def coerce_db_policy_row(
    policy_row: dict[str, Any] | None,
    *,
    project_id: str | None = None,
    owner_user_id: str | None = None,
) -> dict[str, Any]:
    source = copy.deepcopy(policy_row or {})
    prefs = source.get("collaborator_preferences")
    prefs_dict = copy.deepcopy(prefs) if isinstance(prefs, dict) else {}

    interest_behavior = str(
        source.get("interest_behavior")
        or INTEREST_POLICY_TO_BEHAVIOR.get(str(source.get("interest_policy") or ""))
        or DEFAULT_DB_POLICY["interest_behavior"]
    )
    if interest_behavior not in {"notify_then_send", "direct_send"}:
        interest_behavior = str(DEFAULT_DB_POLICY["interest_behavior"])

    reply_behavior = str(
        source.get("reply_behavior")
        or REPLY_POLICY_TO_BEHAVIOR.get(str(source.get("reply_policy") or ""))
        or DEFAULT_DB_POLICY["reply_behavior"]
    )
    if reply_behavior not in {"notify_then_send", "direct_send"}:
        reply_behavior = str(DEFAULT_DB_POLICY["reply_behavior"])

    row: dict[str, Any] = {
        "project_id": source.get("project_id") or project_id,
        "owner_user_id": source.get("owner_user_id") or owner_user_id,
        "market_patrol_interval": str(
            source.get("market_patrol_interval") or DEFAULT_DB_POLICY["market_patrol_interval"]
        ),
        "message_patrol_interval": str(
            source.get("message_patrol_interval") or DEFAULT_DB_POLICY["message_patrol_interval"]
        ),
        "interest_behavior": interest_behavior,
        "reply_behavior": reply_behavior,
        "extra_requirements": _merge_extra_requirements(
            source.get("extra_requirements"),
            prefs_dict,
            source,
        ),
        "is_active": bool(source.get("is_active", DEFAULT_DB_POLICY["is_active"])),
    }

    if row["market_patrol_interval"] not in {*MARKET_INTERVAL_MINUTES.keys(), "manual"}:
        row["market_patrol_interval"] = DEFAULT_DB_POLICY["market_patrol_interval"]
    if row["message_patrol_interval"] not in {*MESSAGE_INTERVAL_MINUTES.keys(), "manual"}:
        row["message_patrol_interval"] = DEFAULT_DB_POLICY["message_patrol_interval"]

    # Preserve legacy fields for compatibility with existing code paths.
    row["patrol_scope"] = source.get("patrol_scope") or "both"
    row["project_mode"] = source.get("project_mode")
    row["notification_mode"] = source.get("notification_mode") or "verbose"
    row["handoff_triggers"] = _normalize_text_list(source.get("handoff_triggers") or [])
    row["collaborator_preferences"] = prefs_dict
    row["interest_policy"] = BEHAVIOR_TO_LEGACY_INTEREST_POLICY[row["interest_behavior"]]
    row["reply_policy"] = BEHAVIOR_TO_LEGACY_REPLY_POLICY[row["reply_behavior"]]
    return row


def market_interval_minutes(value: str | None) -> int | None:
    if not value:
        return None
    return MARKET_INTERVAL_MINUTES.get(value)


def message_interval_minutes(value: str | None) -> int | None:
    if not value:
        return None
    return MESSAGE_INTERVAL_MINUTES.get(value)


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def should_run_market_patrol(
    policy_row: dict[str, Any] | None,
    last_market_run_at: str | None,
    *,
    now: datetime | None = None,
) -> tuple[bool, str]:
    row = coerce_db_policy_row(policy_row)
    now = now or datetime.now(timezone.utc)

    if not row.get("is_active", True):
        return False, "inactive"

    if row.get("patrol_scope") == "messages":
        return False, "messages_only_scope"

    interval = market_interval_minutes(row.get("market_patrol_interval"))
    if interval is None:
        return False, "manual_interval"

    last_run_dt = parse_timestamp(last_market_run_at)
    if last_run_dt is None:
        return True, "first_run"

    if now >= last_run_dt + timedelta(minutes=interval):
        return True, "interval_elapsed"
    return False, "not_due"


def should_run_message_patrol(
    policy_row: dict[str, Any] | None,
    last_message_run_at: str | None,
    *,
    now: datetime | None = None,
) -> tuple[bool, str]:
    row = coerce_db_policy_row(policy_row)
    now = now or datetime.now(timezone.utc)

    if not row.get("is_active", True):
        return False, "inactive"

    if row.get("patrol_scope") == "market":
        return False, "market_only_scope"

    interval = message_interval_minutes(row.get("message_patrol_interval"))
    if interval is None:
        return False, "manual_interval"

    last_run_dt = parse_timestamp(last_message_run_at)
    if last_run_dt is None:
        return True, "first_run"

    if now >= last_run_dt + timedelta(minutes=interval):
        return True, "interval_elapsed"
    return False, "not_due"


def db_policy_to_runtime_bundle(
    policy_row: dict[str, Any] | None,
    *,
    project_id: str | None = None,
    owner_user_id: str | None = None,
) -> dict[str, Any]:
    row = coerce_db_policy_row(policy_row, project_id=project_id, owner_user_id=owner_user_id)
    prefs = row.get("collaborator_preferences") or {}
    legacy_avoid_phrases = _normalize_text_list(prefs.get("avoidPhrases"))
    extra_requirements = str(row.get("extra_requirements") or "")
    extra_requirement_blocklist = _extract_extra_requirement_blocklist(extra_requirements)
    conversation_goals = _normalize_text_list(prefs.get("conversationGoals"))
    conversation_avoid = _normalize_text_list(prefs.get("conversationAvoid"))

    effective_policy: dict[str, Any] = {
        "agentContext": {
            "extraRequirements": extra_requirements,
            "requireAgentJudgment": True,
        },
        "messaging": {
            "avoidPhrases": _normalize_text_list(legacy_avoid_phrases + extra_requirement_blocklist),
        },
        "conversationPolicy": {
            "goals": conversation_goals,
            "avoid": conversation_avoid,
        },
        "behavior": {
            "interest_behavior": row["interest_behavior"],
            "reply_behavior": row["reply_behavior"],
        },
        "patrol": {
            "market_patrol_interval": row["market_patrol_interval"],
            "message_patrol_interval": row["message_patrol_interval"],
        },
    }

    return {
        "row": row,
        "effective_policy": effective_policy,
        "execution": {
            "interest_behavior": row["interest_behavior"],
            "reply_behavior": row["reply_behavior"],
            "market_patrol_interval": row["market_patrol_interval"],
            "message_patrol_interval": row["message_patrol_interval"],
            "extra_requirements": extra_requirements,
            # compatibility fields for older callers/tests
            "interest_policy": row["interest_policy"],
            "reply_policy": row["reply_policy"],
            "patrol_scope": row.get("patrol_scope", "both"),
            "notification_mode": row.get("notification_mode", "verbose"),
            "before_interest": False,
            "high_value_conversation": False,
            "before_contact_share": True,
            "before_commitment": True,
            "message_patrol_implemented": True,
            "auto_send_confidence_threshold": None,
            "metadata_only_fields": {
                "project_mode": row.get("project_mode"),
                "human_handoff_only": False,
            },
        },
    }


__all__ = [
    "DEFAULT_DB_POLICY",
    "INTEREST_POLICY_TO_BEHAVIOR",
    "REPLY_POLICY_TO_BEHAVIOR",
    "coerce_db_policy_row",
    "db_policy_to_runtime_bundle",
    "market_interval_minutes",
    "message_interval_minutes",
    "parse_timestamp",
    "should_run_market_patrol",
    "should_run_message_patrol",
]
