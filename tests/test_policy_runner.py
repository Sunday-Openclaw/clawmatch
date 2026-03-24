import json
from datetime import datetime, timezone

import skill_runtime.runner as policy_runner


def test_run_once_handles_zero_projects(tmp_path, monkeypatch):
    monkeypatch.setattr(policy_runner, "list_my_projects", lambda *args, **kwargs: [])

    summary = policy_runner.run_once(
        agent_key="agent-key",
        state_file=tmp_path / "state.json",
        report_dir=tmp_path / "reports",
        now=datetime(2026, 3, 21, 12, 0, tzinfo=timezone.utc),
    )

    assert summary["project_count"] == 0
    assert summary["projects"] == []
    assert (tmp_path / "reports" / "latest-summary.json").exists()


def test_run_once_skips_manual_and_messages_scope_projects(tmp_path, monkeypatch):
    monkeypatch.setattr(
        policy_runner,
        "list_my_projects",
        lambda *args, **kwargs: [
            {"id": "project-manual", "project_name": "Manual", "user_id": "owner-1"},
            {"id": "project-messages", "project_name": "Messages", "user_id": "owner-1"},
        ],
    )
    monkeypatch.setattr(
        policy_runner,
        "get_policy",
        lambda *args, **kwargs: {
            "project-manual": {"market_patrol_interval": "manual", "patrol_scope": "market", "notification_mode": "verbose"},
            "project-messages": {"market_patrol_interval": "10m", "patrol_scope": "messages", "message_patrol_interval": "manual", "notification_mode": "verbose"},
        }[kwargs.get("project_id")],
    )
    monkeypatch.setattr(policy_runner, "list_market", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected")))
    monkeypatch.setattr(
        policy_runner,
        "list_outgoing_interests",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected")),
    )
    monkeypatch.setattr(
        policy_runner,
        "list_conversations",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected")),
    )

    summary = policy_runner.run_once(
        agent_key="agent-key",
        state_file=tmp_path / "state.json",
        report_dir=tmp_path / "reports",
        now=datetime(2026, 3, 21, 12, 0, tzinfo=timezone.utc),
    )

    assert summary["project_count"] == 2
    assert summary["projects"] == [
        {
            "project_id": "project-manual",
            "project_name": "Manual",
            "policy_source": "database",
            "status": "skipped",
            "reason": "manual_interval",
            "execution": {
                "interest_mode": "draft_then_confirm",
                "message_patrol_status": "market_only_scope",
            },
        },
        {
            "project_id": "project-messages",
            "project_name": "Messages",
            "policy_source": "database",
            "status": "skipped",
            "reason": "messages_only_scope",
            "execution": {
                "interest_mode": "draft_then_confirm",
                "message_patrol_status": "manual_interval",
            },
        },
    ]

    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state == {"projects": {}}
    assert (tmp_path / "reports" / "project-manual.json").exists()
    assert (tmp_path / "reports" / "project-messages.json").exists()
    assert (tmp_path / "reports" / "latest-summary.json").exists()


def test_run_once_executes_policy_and_writes_reports(tmp_path, monkeypatch):
    submitted_interests = []
    started_conversations = []
    updated_conversations = []
    choose_calls = []

    monkeypatch.setattr(
        policy_runner,
        "list_my_projects",
        lambda *args, **kwargs: [{"id": "project-1", "project_name": "Runner Source", "user_id": "owner-1"}],
    )
    monkeypatch.setattr(
        policy_runner,
        "get_policy",
        lambda *args, **kwargs: {
            "project_id": kwargs.get("project_id"),
            "owner_user_id": "owner-1",
            "market_patrol_interval": "10m",
            "message_patrol_interval": "10m",
            "patrol_scope": "market",
            "interest_policy": "auto_send_high_confidence",
            "reply_policy": "draft_then_confirm",
            "handoff_triggers": [],
            "collaborator_preferences": {
                "priorityTags": ["ai"],
                "constraints": "",
                "preferredWorkingStyle": "async",
            },
            "notification_mode": "important_only",
            "is_active": True,
        },
    )
    monkeypatch.setattr(
        policy_runner,
        "list_market",
        lambda *args, **kwargs: [{"id": "market-1"}, {"id": "market-2"}],
    )
    monkeypatch.setattr(
        policy_runner,
        "list_outgoing_interests",
        lambda *args, **kwargs: [{"id": "existing-interest", "target_project_id": "target-existing", "status": "open"}],
    )
    monkeypatch.setattr(
        policy_runner,
        "list_conversations",
        lambda *args, **kwargs: [{"id": "conv-existing", "project_id": "target-existing", "status": "active"}],
    )

    def fake_choose(me, market, open_interests, conversations, policy):
        choose_calls.append(
            {
                "me": me,
                "market": market,
                "open_interests": open_interests,
                "conversations": conversations,
                "policy": policy,
            }
        )
        return {
            "selected_interests": [
                {
                    "project_id": "target-high",
                    "project_name": "High Confidence Target",
                    "decision": "interest",
                    "confidence": 0.93,
                    "opening_message": "high-confidence interest",
                },
                {
                    "project_id": "target-low",
                    "project_name": "Low Confidence Target",
                    "decision": "interest",
                    "confidence": 0.81,
                    "opening_message": "low-confidence interest",
                },
            ],
            "watchlist": [],
            "skips": [],
            "conversation_candidates": [
                {
                    "project_id": "target-conversation",
                    "project_name": "Conversation Target",
                    "decision": "conversation",
                    "confidence": 0.91,
                    "conversation_state_plan": {
                        "conversation_id": None,
                        "target_status": "conversation_started",
                        "summary_for_owner": "Auto-started after accepted interest.",
                        "recommended_next_step": "Let the agents continue.",
                        "last_agent_decision": "conversation candidate identified",
                        "ready_to_apply": False,
                    },
                }
            ],
            "handoffs": [
                {
                    "project_id": "target-handoff",
                    "project_name": "Handoff Target",
                    "decision": "handoff",
                    "confidence": 0.95,
                    "conversation_state_plan": {
                        "conversation_id": "conversation-existing",
                        "target_status": "handoff_ready",
                        "summary_for_owner": "Needs human review.",
                        "recommended_next_step": "Review this thread.",
                        "last_agent_decision": "owner review recommended",
                        "ready_to_apply": True,
                    },
                }
            ],
            "execution_plan": {
                "conversation_state_updates": [
                    {
                        "conversation_id": "conversation-existing",
                        "target_status": "handoff_ready",
                        "summary_for_owner": "Needs human review.",
                        "recommended_next_step": "Review this thread.",
                        "last_agent_decision": "owner review recommended",
                        "ready_to_apply": True,
                    }
                ],
                "conversation_auto_start_candidates": [
                    {
                        "project_id": "target-conversation",
                        "project_name": "Conversation Target",
                        "existing_interest_id": "interest-accepted",
                        "receiver_user_id": "receiver-1",
                    }
                ],
            },
        }

    monkeypatch.setattr(policy_runner, "choose_candidates_from_data", fake_choose)
    monkeypatch.setattr(
        policy_runner,
        "submit_interest",
        lambda agent_key=None, project_id=None, message=None, contact=None, **kwargs: submitted_interests.append(
            {
                "project_id": project_id,
                "message": message,
                "contact": contact,
                "agent_key": agent_key,
            }
        )
        or {"id": f"interest-for-{project_id}"},
    )
    monkeypatch.setattr(
        policy_runner,
        "start_conversation",
        lambda agent_key=None, project_id=None, interest_id=None, receiver_user_id=None, **kwargs: started_conversations.append(
            {
                "project_id": project_id,
                "interest_id": interest_id,
                "receiver_user_id": receiver_user_id,
                "agent_key": agent_key,
            }
        )
        or [{"id": "conversation-started"}],
    )
    monkeypatch.setattr(
        policy_runner,
        "update_conversation",
        lambda **kwargs: updated_conversations.append(kwargs) or {"id": kwargs["conversation_id"], "status": kwargs["status"]},
    )
    monkeypatch.setattr(
        policy_runner,
        "list_incoming_interests",
        lambda *args, **kwargs: [],
    )

    now = datetime(2026, 3, 21, 12, 0, tzinfo=timezone.utc)
    summary = policy_runner.run_once(
        agent_key="agent-key",
        state_file=tmp_path / "state.json",
        report_dir=tmp_path / "reports",
        agent_contact="@bot",
        now=now,
    )

    assert len(choose_calls) == 1
    assert choose_calls[0]["policy"]["automation"]["autoSubmitInterest"] is True
    assert choose_calls[0]["policy"]["automation"]["requireHumanApprovalForConversation"] is False

    assert submitted_interests == [
        {
            "project_id": "target-high",
            "message": "high-confidence interest",
            "contact": "@bot",
            "agent_key": "agent-key",
        }
    ]
    assert started_conversations == [
        {
            "project_id": "target-conversation",
            "interest_id": "interest-accepted",
            "receiver_user_id": "receiver-1",
            "agent_key": "agent-key",
        }
    ]
    assert updated_conversations == [
        {
            "agent_key": "agent-key",
            "conversation_id": "conversation-existing",
            "status": "handoff_ready",
            "summary_for_owner": "Needs human review.",
            "recommended_next_step": "Review this thread.",
            "last_agent_decision": "owner review recommended",
        },
        {
            "agent_key": "agent-key",
            "conversation_id": "conversation-started",
            "status": "conversation_started",
            "summary_for_owner": "Auto-started after accepted interest.",
            "recommended_next_step": "Let the agents continue.",
            "last_agent_decision": "conversation candidate identified",
        },
    ]

    project_summary = summary["projects"][0]
    assert project_summary["status"] == "executed"
    assert project_summary["execution"]["interest_mode"] == "auto_send_high_confidence"
    assert len(project_summary["execution"]["interest_submissions"]) == 1
    assert len(project_summary["execution"]["started_conversations"]) == 1
    assert len(project_summary["execution"]["conversation_state_updates"]) == 2
    assert project_summary["execution"]["message_patrol_status"] == "market_only_scope"

    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["projects"]["project-1"]["last_market_run_at"] == now.isoformat()

    project_report = json.loads((tmp_path / "reports" / "project-1.json").read_text(encoding="utf-8"))
    assert project_report["source_project_id"] == "project-1"
    assert project_report["execution"]["interest_submissions"][0]["project_id"] == "target-high"
    latest_summary = json.loads((tmp_path / "reports" / "latest-summary.json").read_text(encoding="utf-8"))
    assert latest_summary["projects"][0]["project_id"] == "project-1"
