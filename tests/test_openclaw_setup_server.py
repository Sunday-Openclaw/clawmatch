from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import openclaw_setup_server as setup_server


def test_build_bootstrap_command_contains_token_and_api_base():
    command = setup_server.build_bootstrap_command(
        setup_token="claw_setup_abc123",
        api_base="http://127.0.0.1:8791",
    )
    assert "claw_setup_abc123" in command
    assert "http://127.0.0.1:8791/api/openclaw/setup/bootstrap.ps1" in command
    assert "powershell -ExecutionPolicy Bypass" in command
    assert '-Command \'' in command
    assert '$tmp = Join-Path $env:TEMP "clawborate-bootstrap.ps1"' in command


def test_skill_bundle_artifact_contains_skill_root():
    artifact = setup_server.skill_bundle_artifact()
    assert artifact.filename.endswith(".zip")
    assert len(artifact.data) > 0
    assert len(artifact.sha256) == 64


def test_build_install_manifest_includes_required_fields(monkeypatch):
    monkeypatch.setattr(
        setup_server,
        "skill_bundle_artifact",
        lambda: setup_server.SkillBundleArtifact(data=b"abc", sha256="f" * 64, filename="skill.zip"),
    )
    monkeypatch.setattr(
        setup_server,
        "python_runtime_descriptor",
        lambda base_url: {"url": f"{base_url}/python.zip", "sha256": "1" * 64},
    )

    manifest = setup_server.build_install_manifest(
        "http://127.0.0.1:8791",
        setup_session_id="session-1",
        agent_key="cm_sk_live_test",
    )

    assert manifest["setup_session_id"] == "session-1"
    assert manifest["agent_key"] == "cm_sk_live_test"
    assert manifest["skill_bundle"]["sha256"] == "f" * 64
    assert manifest["python_runtime"]["url"] == "http://127.0.0.1:8791/python.zip"
    assert manifest["config_batch"][0]["path"] == "plugins.entries.clawborate.enabled"
    assert manifest["config_batch"][1] == {"path": "agents.defaults.sandbox.mode", "value": "non-main"}
    assert manifest["config_batch"][2] == {"path": "tools.exec.host", "value": "sandbox"}
    assert manifest["cron_spec"]["name"] == "clawborate-patrol"
    assert manifest["cron_spec"]["session"] == "isolated"
    assert manifest["plugin_migration"]["disable_legacy_plugin"] is True


def test_exchange_setup_token_marks_session_exchanged(monkeypatch):
    patched = {}
    monkeypatch.setattr(
        setup_server,
        "_load_setup_session_by_hash",
        lambda token_hash: {
            "id": "session-1",
            "owner_user_id": "owner-1",
            "status": "issued",
            "expires_at": "2099-01-01T00:00:00+00:00",
        },
    )
    monkeypatch.setattr(setup_server, "_mark_session_expired_if_needed", lambda row: row)
    monkeypatch.setattr(
        setup_server,
        "_create_agent_api_key",
        lambda owner_user_id: ("cm_sk_live_generated", {"id": "key-1"}),
    )
    monkeypatch.setattr(
        setup_server,
        "build_install_manifest",
        lambda base_url, setup_session_id, agent_key=None: {
            "setup_session_id": setup_session_id,
            "agent_key": agent_key,
        },
    )
    monkeypatch.setattr(
        setup_server,
        "_service_patch",
        lambda table, params, payload: patched.setdefault("payload", payload) or payload,
    )

    manifest = setup_server.exchange_setup_token("claw_setup_123")
    assert manifest["agent_key"] == "cm_sk_live_generated"
    assert patched["payload"]["status"] == "exchanged"
    assert patched["payload"]["agent_api_key_id"] == "key-1"
    assert patched["payload"]["install_manifest"]["agent_key"] is None


def test_exchange_setup_token_rejects_reuse(monkeypatch):
    monkeypatch.setattr(
        setup_server,
        "_load_setup_session_by_hash",
        lambda token_hash: {
            "id": "session-1",
            "owner_user_id": "owner-1",
            "status": "exchanged",
            "expires_at": "2099-01-01T00:00:00+00:00",
        },
    )
    monkeypatch.setattr(setup_server, "_mark_session_expired_if_needed", lambda row: row)

    with pytest.raises(setup_server.ApiError, match="already been exchanged"):
        setup_server.exchange_setup_token("claw_setup_123")


def test_complete_setup_session_updates_status(monkeypatch):
    patched = {}
    monkeypatch.setattr(
        setup_server,
        "_load_setup_session_by_id",
        lambda session_id: {
            "id": session_id,
            "status": "exchanged",
            "agent_api_key_id": "key-1",
        },
    )
    monkeypatch.setattr(
        setup_server,
        "_service_get",
        lambda table, params: [{"id": "key-1", "key_hash": setup_server.sha256_hex("cm_sk_live_done")}],
    )
    monkeypatch.setattr(
        setup_server,
        "_service_patch",
        lambda table, params, payload: (patched.setdefault("payload", payload), {"id": "session-1", **payload})[1],
    )

    result = setup_server.complete_setup_session(
        "session-1",
        "cm_sk_live_done",
        {"dry_run_status": "ok"},
    )
    assert result["status"] == "applied"
    assert patched["payload"]["status"] == "applied"
