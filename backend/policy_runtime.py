"""
Helpers for turning dashboard-stored agent policies into executable runtime config.
"""

from __future__ import annotations

import copy
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from clawmatch_autopilot import DEFAULT_POLICY, deep_merge


DEFAULT_HANDOFF_TRIGGERS = [
    "before_interest",
    "before_contact_share",
    "before_commitment",
    "high_value_conversation",
]

DEFAULT_DB_POLICY = {
    "project_mode": "both",
    "market_patrol_interval": "30m",
    "message_patrol_interval": "10m",
    "patrol_scope": "both",
    "interest_policy": "draft_then_confirm",
    "reply_policy": "draft_then_confirm",
    "handoff_triggers": DEFAULT_HANDOFF_TRIGGERS,
    "collaborator_preferences": {
        "priorityTags": [],
        "constraints": "",
        "preferredWorkingStyle": "",
        "automation": {
            "autoAcceptIncomingInterest": False,
            "requireHumanApprovalForAcceptingInterest": True,
        },
    },
    "notification_mode": "important_only",
    "is_active": True,
}

MARKET_INTERVAL_MINUTES = {
    "10m": 10,
    "30m": 30,
    "1h": 60,
}

DISALLOWED_PREFIXES = ("no ", "not ", "avoid ", "without ", "must not ")


def _copy_default_db_policy() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_DB_POLICY)


def _normalize_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items = value
    else:
        items = re.split(r"[,;\n]+", str(value))

    normalized = []
    seen = set()
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


def coerce_db_policy_row(
    policy_row: dict[str, Any] | None,
    *,
    project_id: str | None = None,
    owner_user_id: str | None = None,
) -> dict[str, Any]:
    row = _copy_default_db_policy()
    if policy_row:
        row.update({k: v for k, v in policy_row.items() if k != "collaborator_preferences"})

    prefs = copy.deepcopy(DEFAULT_DB_POLICY["collaborator_preferences"])
    if policy_row and isinstance(policy_row.get("collaborator_preferences"), dict):
        prefs.update(policy_row["collaborator_preferences"])
        if isinstance(policy_row["collaborator_preferences"].get("automation"), dict):
            prefs["automation"].update(policy_row["collaborator_preferences"]["automation"])

    row["collaborator_preferences"] = prefs
    raw_handoff_triggers = row.get("handoff_triggers")
    if raw_handoff_triggers is None:
        raw_handoff_triggers = DEFAULT_HANDOFF_TRIGGERS
    row["handoff_triggers"] = _normalize_text_list(raw_handoff_triggers)
    row["project_id"] = row.get("project_id") or project_id
    row["owner_user_id"] = row.get("owner_user_id") or owner_user_id
    row["is_active"] = bool(row.get("is_active", True))
    return row


def market_interval_minutes(value: str | None) -> int | None:
    if not value:
        return None
    return MARKET_INTERVAL_MINUTES.get(value)


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

    patrol_scope = row.get("patrol_scope")
    if patrol_scope == "messages":
        return False, "messages_scope_not_implemented"
    if patrol_scope not in {"market", "both"}:
        return False, "unsupported_scope"

    interval_minutes = market_interval_minutes(row.get("market_patrol_interval"))
    if interval_minutes is None:
        return False, "manual_interval"

    last_run_dt = parse_timestamp(last_market_run_at)
    if last_run_dt is None:
        return True, "first_run"

    if now >= last_run_dt + timedelta(minutes=interval_minutes):
        return True, "interval_elapsed"
    return False, "not_due"


def db_policy_to_runtime_bundle(
    policy_row: dict[str, Any] | None,
    *,
    project_id: str | None = None,
    owner_user_id: str | None = None,
) -> dict[str, Any]:
    row = coerce_db_policy_row(policy_row, project_id=project_id, owner_user_id=owner_user_id)
    prefs = row["collaborator_preferences"]
    priority_tags = _normalize_text_list(prefs.get("priorityTags"))
    preferred_styles = _normalize_text_list(prefs.get("preferredWorkingStyle"))
    constraint_phrases = _normalize_text_list(prefs.get("constraints"))
    disallowed_patterns = [phrase for phrase in constraint_phrases if phrase.lower().startswith(DISALLOWED_PREFIXES)]
    must_have_phrases = [phrase for phrase in constraint_phrases if phrase not in disallowed_patterns]

    triggers = set(row.get("handoff_triggers") or [])
    before_interest = "before_interest" in triggers
    high_value_conversation = "high_value_conversation" in triggers
    interval_minutes = market_interval_minutes(row.get("market_patrol_interval"))

    effective_policy = deep_merge(
        copy.deepcopy(DEFAULT_POLICY),
        {
            "scanStrategy": {
                "enabled": bool(row.get("is_active", True)) and interval_minutes is not None,
                "scanIntervalMinutes": interval_minutes or DEFAULT_POLICY["scanStrategy"]["scanIntervalMinutes"],
            },
            "preferences": {
                "prioritizeTags": priority_tags,
                "preferredCollaborationStyle": preferred_styles,
            },
            "hardConstraints": {
                "disallowedPatterns": disallowed_patterns,
                "mustHaveAtLeastOne": must_have_phrases,
            },
            "automation": {
                "autoSubmitInterest": row.get("interest_policy") == "auto_send_high_confidence" and not before_interest,
                "requireHumanApprovalForInterest": (
                    row.get("interest_policy") != "auto_send_high_confidence" or before_interest
                ),
                "autoStartConversation": not high_value_conversation,
                "requireHumanApprovalForConversation": high_value_conversation,
                "autoAcceptIncomingInterest": False,
                "requireHumanApprovalForAcceptingInterest": True,
            },
        },
    )

    return {
        "row": row,
        "effective_policy": effective_policy,
        "execution": {
            "interest_policy": row.get("interest_policy"),
            "reply_policy": row.get("reply_policy"),
            "market_patrol_interval": row.get("market_patrol_interval"),
            "message_patrol_interval": row.get("message_patrol_interval"),
            "patrol_scope": row.get("patrol_scope"),
            "before_interest": before_interest,
            "high_value_conversation": high_value_conversation,
            "message_patrol_implemented": False,
            "auto_send_confidence_threshold": 0.82,
            "metadata_only_fields": {
                "project_mode": row.get("project_mode"),
                "notification_mode": row.get("notification_mode"),
                "before_contact_share": "before_contact_share" in triggers,
                "before_commitment": "before_commitment" in triggers,
                "human_handoff_only": "human_handoff_only" in triggers,
                "autoAcceptIncomingInterest": False,
            },
        },
    }
