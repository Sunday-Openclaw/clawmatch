from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone

import pytest

from skill_runtime import (
    ClawborateConfig,
    apply_market_decision,
    get_latest_report,
    get_patrol_brief,
    get_status,
    install_skill,
    list_projects,
    resolve_pending_action,
    revalidate_key,
    run_worker_tick,
)
from skill_runtime.client import AgentGatewayError
from skill_runtime.skill_runtime import ACTION_NAMES, InstallError


class FakeClient:
    def __init__(self, *, projects=None, policies=None, incoming=None, error: AgentGatewayError | None = None):
        self.projects = projects or []
        self.policies = policies or {}
        self.incoming = incoming or []
        self.error = error
        self.submitted = []
        self.accepted = []
        self.declined = []

    def validate_agent_key(self):
        if self.error:
            raise self.error
        return self.projects

    def list_my_projects(self, limit=200):
        if self.error:
            raise self.error
        return self.projects

    def get_policy(self, project_id=None):
        if project_id is None:
            return next(iter(self.policies.values()), None)
        return self.policies.get(project_id)

    def list_incoming_interests(self, project_id=None):
        items = self.incoming
        if project_id:
            items = [item for item in items if item.get("target_project_id") == project_id]
        return items

    def list_outgoing_interests(self, source_project_id=None):
        return []

    def list_conversations(self, project_id=None):
        return []

    def submit_interest(self, *, project_id, message, contact=None, source_project_id=None):
        payload = {
            "project_id": project_id,
            "message": message,
            "contact": contact,
            "source_project_id": source_project_id,
        }
        self.submitted.append(payload)
        return {"id": f"interest-{len(self.submitted)}", **payload}

    def accept_interest(self, interest_id):
        self.accepted.append(interest_id)
        return {"id": interest_id, "status": "accepted"}

    def decline_interest(self, interest_id):
        self.declined.append(interest_id)
        return {"id": interest_id, "status": "declined"}


def fake_client_factory_with(projects=None, policies=None, incoming=None, error=None):
    def factory(agent_key: str, base_url: str, anon_key: str):
        return FakeClient(projects=projects, policies=policies, incoming=incoming, error=error)

    return factory


def make_test_config(tmp_path, *, agent_contact=None):
    openclaw_root = tmp_path / "openclaw"
    workspace = openclaw_root / "workspace"
    openclaw_root.mkdir(parents=True, exist_ok=True)
    (openclaw_root / "openclaw.json").write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {
                        "workspace": str(workspace),
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return ClawborateConfig(openclaw_root=str(openclaw_root), agent_contact=agent_contact)


def test_install_skill_creates_private_storage_and_registration(tmp_path):
    result = install_skill(
        agent_key="cm_sk_live_test",
        home=tmp_path,
        config=make_test_config(tmp_path, agent_contact="@skill-bot"),
        client_factory=fake_client_factory_with(projects=[{"id": "project-1"}]),
    )

    assert result["ok"] is True
    assert result["skill_name"] == "clawborate-skill"
    assert result["visible_project_count"] == 1
    assert (tmp_path / "config.json").exists()
    assert (tmp_path / "secrets.json").exists()
    assert (tmp_path / "state.json").exists()
    assert (tmp_path / "health.json").exists()
    assert (tmp_path / "registration.json").exists()
    assert (tmp_path / "reports" / "latest-summary.json").exists()

    registration = json.loads((tmp_path / "registration.json").read_text(encoding="utf-8"))
    assert registration["worker"]["entrypoint"] == "scripts/worker.py"
    assert registration["worker"]["tick_seconds"] == 300
    assert [action["name"] for action in registration["actions"]] == ACTION_NAMES

    status = get_status(home=tmp_path)
    assert status["installed"] is True
    assert status["health"]["status"] == "ready"
    assert get_latest_report(home=tmp_path)["mode"] == "not_run_yet"
    assert list_projects(home=tmp_path, client_factory=fake_client_factory_with(projects=[{"id": "project-1"}])) == [{"id": "project-1"}]


def test_install_skill_surfaces_invalid_agent_key(tmp_path):
    with pytest.raises(InstallError, match="Invalid or revoked agent key"):
        install_skill(
            agent_key="cm_sk_live_bad",
            home=tmp_path,
            config=make_test_config(tmp_path),
            client_factory=fake_client_factory_with(
                error=AgentGatewayError("invalid_agent_key", "Invalid or revoked agent key")
            ),
        )


def test_run_worker_tick_writes_health_and_pauses_on_invalid_key(tmp_path):
    install_skill(
        agent_key="cm_sk_live_test",
        home=tmp_path,
        config=make_test_config(tmp_path),
        client_factory=fake_client_factory_with(projects=[{"id": "project-1"}]),
    )

    calls = []

    def fake_runner(**kwargs):
        calls.append(kwargs)
        return {
            "mode": "once",
            "ran_at": datetime(2026, 3, 21, 12, 0, tzinfo=timezone.utc).isoformat(),
            "project_count": 1,
            "projects": [],
        }

    summary = run_worker_tick(
        home=tmp_path,
        now=datetime(2026, 3, 21, 12, 0, tzinfo=timezone.utc),
        client_factory=fake_client_factory_with(projects=[{"id": "project-1"}]),
        runner=fake_runner,
    )
    assert summary["project_count"] == 1
    assert calls[0]["storage_dir"] == tmp_path

    health = json.loads((tmp_path / "health.json").read_text(encoding="utf-8"))
    assert health["status"] == "ready"
    assert health["paused"] is False
    assert health["last_success_at"] is not None

    def failing_runner(**kwargs):
        raise AgentGatewayError("invalid_agent_key", "Invalid or revoked agent key")

    with pytest.raises(AgentGatewayError):
        run_worker_tick(
            home=tmp_path,
            now=datetime(2026, 3, 21, 12, 5, tzinfo=timezone.utc),
            client_factory=fake_client_factory_with(projects=[{"id": "project-1"}]),
            runner=failing_runner,
        )

    paused_health = json.loads((tmp_path / "health.json").read_text(encoding="utf-8"))
    assert paused_health["status"] == "paused"
    assert paused_health["paused"] is True
    assert paused_health["paused_reason"] == "revalidate_key_required"


def test_revalidate_key_clears_paused_state(tmp_path):
    install_skill(
        agent_key="cm_sk_live_test",
        home=tmp_path,
        config=make_test_config(tmp_path),
        client_factory=fake_client_factory_with(projects=[{"id": "project-1"}]),
    )
    (tmp_path / "health.json").write_text(
        json.dumps(
            {
                "status": "paused",
                "paused": True,
                "paused_reason": "revalidate_key_required",
                "last_attempt_at": None,
                "last_success_at": None,
                "last_error": {"code": "invalid_agent_key", "message": "Invalid"},
                "consecutive_failures": 2,
            }
        ),
        encoding="utf-8",
    )

    result = revalidate_key(home=tmp_path, client_factory=fake_client_factory_with(projects=[{"id": "project-1"}]))
    assert result == {"ok": True, "visible_project_count": 1}

    health = json.loads((tmp_path / "health.json").read_text(encoding="utf-8"))
    assert health["status"] == "ready"
    assert health["paused"] is False
    assert health["consecutive_failures"] == 0


def test_install_skill_writes_patrol_prompt_and_bootstrap_plan(tmp_path):
    openclaw_root = tmp_path / "openclaw"
    workspace = openclaw_root / "workspace"
    (openclaw_root / "openclaw.json").parent.mkdir(parents=True, exist_ok=True)
    (openclaw_root / "openclaw.json").write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {
                        "workspace": str(workspace),
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    result = install_skill(
        agent_key="cm_sk_live_test",
        home=tmp_path,
        config=make_test_config(tmp_path),
        client_factory=fake_client_factory_with(projects=[{"id": "project-1"}]),
    )

    assert (tmp_path / "CLAWBORATE_PATROL.md").exists()
    assert (workspace / "CLAWBORATE_PATROL.md").exists()
    assert (workspace / "skills" / "clawborate-skill" / "CLAWBORATE_PATROL.md").exists()
    assert (tmp_path / "bootstrap-plan.json").exists()
    assert result["bootstrap_plan"]["cron"]["name"] == "clawborate-patrol"
    assert result["bootstrap_plan"]["cron"]["every"] == "5m"
    assert result["bootstrap_plan"]["cron"]["message"] == (
        "Read CLAWBORATE_PATROL.md and execute one Clawborate patrol tick. "
        "If nothing requires user-visible output, reply CLAWBORATE_IDLE."
    )
    assert result["bootstrap_plan"]["cron"]["light_context"] is True
    assert result["bootstrap_plan"]["cron"]["best_effort_deliver"] is True
    assert "openclaw cron add" in result["bootstrap_plan"]["cron"]["command_preview"]
    assert Path(result["bootstrap_plan"]["prompt_path"]) == workspace / "CLAWBORATE_PATROL.md"


def test_get_patrol_brief_creates_pending_incoming_interest_actions(tmp_path):
    install_skill(
        agent_key="cm_sk_live_test",
        home=tmp_path,
        config=make_test_config(tmp_path),
        client_factory=fake_client_factory_with(
            projects=[{"id": "project-1", "project_name": "Alpha", "user_id": "owner-1"}],
            policies={
                "project-1": {
                    "market_patrol_interval": "10m",
                    "message_patrol_interval": "5m",
                    "interest_behavior": "direct_send",
                    "reply_behavior": "notify_then_send",
                    "extra_requirements": "Prefer async projects.",
                }
            },
            incoming=[
                {
                    "id": "interest-1",
                    "status": "open",
                    "target_project_id": "project-1",
                    "target": {"project_name": "Alpha"},
                    "message": "Interested in collaborating",
                    "from_user_id": "peer-1",
                }
            ],
        ),
    )

    brief = get_patrol_brief(
        home=tmp_path,
        now=datetime(2026, 3, 21, 12, 0, tzinfo=timezone.utc),
        client_factory=fake_client_factory_with(
            projects=[{"id": "project-1", "project_name": "Alpha", "user_id": "owner-1"}],
            policies={
                "project-1": {
                    "market_patrol_interval": "10m",
                    "message_patrol_interval": "5m",
                    "interest_behavior": "direct_send",
                    "reply_behavior": "notify_then_send",
                    "extra_requirements": "Prefer async projects.",
                }
            },
            incoming=[
                {
                    "id": "interest-1",
                    "status": "open",
                    "target_project_id": "project-1",
                    "target": {"project_name": "Alpha"},
                    "message": "Interested in collaborating",
                    "from_user_id": "peer-1",
                }
            ],
        ),
    )

    assert brief["project_count"] == 1
    assert brief["projects"][0]["due"]["market"] is True
    assert brief["projects"][0]["policy"]["interest_behavior"] == "direct_send"
    assert brief["incoming_interests"][0]["token"] == "I01"

    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["pending_actions"]["I01"]["type"] == "incoming_interest"


def test_apply_market_decision_and_resolve_pending_action(tmp_path):
    install_skill(
        agent_key="cm_sk_live_test",
        home=tmp_path,
        config=make_test_config(tmp_path),
        client_factory=fake_client_factory_with(
            projects=[{"id": "project-1", "project_name": "Alpha", "user_id": "owner-1"}],
            policies={
                "project-1": {
                    "market_patrol_interval": "10m",
                    "message_patrol_interval": "5m",
                    "interest_behavior": "notify_then_send",
                    "reply_behavior": "notify_then_send",
                    "extra_requirements": "",
                }
            },
        ),
    )

    client_factory = fake_client_factory_with(
        projects=[{"id": "project-1", "project_name": "Alpha", "user_id": "owner-1"}],
        policies={
            "project-1": {
                "market_patrol_interval": "10m",
                "message_patrol_interval": "5m",
                "interest_behavior": "notify_then_send",
                "reply_behavior": "notify_then_send",
                "extra_requirements": "",
            }
        },
    )

    pending = apply_market_decision(
        source_project_id="project-1",
        target_project_id="target-9",
        decision="send",
        confidence=0.92,
        reason="Strong match",
        opening_message="We should talk.",
        home=tmp_path,
        client_factory=client_factory,
    )

    assert pending["execution"] == "pending_user"
    assert pending["pending_action"]["token"] == "M01"

    resolved = resolve_pending_action(
        action_token="M01",
        decision="send",
        home=tmp_path,
        client_factory=client_factory,
    )
    assert resolved["ok"] is True
    assert resolved["action"]["status"] == "sent"
