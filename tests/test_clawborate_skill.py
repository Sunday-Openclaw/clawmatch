from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from skill_runtime import ClawborateConfig, get_latest_report, get_status, install_skill, list_projects, revalidate_key, run_worker_tick
from skill_runtime.client import AgentGatewayError
from skill_runtime.skill_runtime import ACTION_NAMES, InstallError


class FakeClient:
    def __init__(self, *, projects=None, error: AgentGatewayError | None = None):
        self.projects = projects or []
        self.error = error

    def validate_agent_key(self):
        if self.error:
            raise self.error
        return self.projects

    def list_my_projects(self, limit=200):
        if self.error:
            raise self.error
        return self.projects


def fake_client_factory_with(projects=None, error=None):
    def factory(agent_key: str, base_url: str, anon_key: str):
        return FakeClient(projects=projects, error=error)

    return factory


def test_install_skill_creates_private_storage_and_registration(tmp_path):
    result = install_skill(
        agent_key="cm_sk_live_test",
        home=tmp_path,
        config=ClawborateConfig(agent_contact="@skill-bot"),
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
            client_factory=fake_client_factory_with(
                error=AgentGatewayError("invalid_agent_key", "Invalid or revoked agent key")
            ),
        )


def test_run_worker_tick_writes_health_and_pauses_on_invalid_key(tmp_path):
    install_skill(
        agent_key="cm_sk_live_test",
        home=tmp_path,
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
