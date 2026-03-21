from datetime import datetime, timedelta, timezone

from policy_runtime import db_policy_to_runtime_bundle, should_run_market_patrol


def test_db_policy_to_runtime_bundle_maps_policy_fields_and_overrides_auto_send():
    bundle = db_policy_to_runtime_bundle(
        {
            "project_id": "project-1",
            "owner_user_id": "owner-1",
            "market_patrol_interval": "30m",
            "message_patrol_interval": "10m",
            "patrol_scope": "both",
            "interest_policy": "auto_send_high_confidence",
            "reply_policy": "notify_only",
            "handoff_triggers": ["before_interest", "high_value_conversation"],
            "project_mode": "startup",
            "notification_mode": "verbose",
            "collaborator_preferences": {
                "priorityTags": ["ai", "biology"],
                "constraints": "avoid crypto; async friendly\nserious team",
                "preferredWorkingStyle": "async-first, fast iteration",
                "automation": {
                    "autoAcceptIncomingInterest": True,
                    "requireHumanApprovalForAcceptingInterest": False,
                },
            },
        }
    )

    effective_policy = bundle["effective_policy"]
    execution = bundle["execution"]

    assert effective_policy["preferences"]["prioritizeTags"] == ["ai", "biology"]
    assert effective_policy["preferences"]["preferredCollaborationStyle"] == ["async-first", "fast iteration"]
    assert effective_policy["hardConstraints"]["disallowedPatterns"] == ["avoid crypto"]
    assert effective_policy["hardConstraints"]["mustHaveAtLeastOne"] == ["async friendly", "serious team"]

    assert effective_policy["automation"]["autoSubmitInterest"] is False
    assert effective_policy["automation"]["requireHumanApprovalForInterest"] is True
    assert effective_policy["automation"]["autoStartConversation"] is False
    assert effective_policy["automation"]["requireHumanApprovalForConversation"] is True
    assert effective_policy["automation"]["autoAcceptIncomingInterest"] is False
    assert effective_policy["automation"]["requireHumanApprovalForAcceptingInterest"] is True

    assert execution["interest_policy"] == "auto_send_high_confidence"
    assert execution["reply_policy"] == "notify_only"
    assert execution["before_interest"] is True
    assert execution["high_value_conversation"] is True
    assert execution["auto_send_confidence_threshold"] == 0.82
    assert execution["metadata_only_fields"] == {
        "project_mode": "startup",
        "notification_mode": "verbose",
        "before_contact_share": False,
        "before_commitment": False,
        "human_handoff_only": False,
        "autoAcceptIncomingInterest": False,
    }


def test_should_run_market_patrol_handles_manual_messages_inactive_and_due_windows():
    now = datetime(2026, 3, 21, 12, 0, tzinfo=timezone.utc)

    assert should_run_market_patrol({"is_active": False}, None, now=now) == (False, "inactive")
    assert should_run_market_patrol({"patrol_scope": "messages"}, None, now=now) == (
        False,
        "messages_scope_not_implemented",
    )
    assert should_run_market_patrol({"market_patrol_interval": "manual"}, None, now=now) == (
        False,
        "manual_interval",
    )
    assert should_run_market_patrol({"market_patrol_interval": "10m"}, None, now=now) == (True, "first_run")
    assert should_run_market_patrol(
        {"market_patrol_interval": "10m"},
        (now - timedelta(minutes=11)).isoformat(),
        now=now,
    ) == (True, "interval_elapsed")
    assert should_run_market_patrol(
        {"market_patrol_interval": "30m"},
        (now - timedelta(minutes=20)).isoformat(),
        now=now,
    ) == (False, "not_due")
