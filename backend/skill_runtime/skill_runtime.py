from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from .client import AgentGatewayError, AgentGatewayTransportError, GatewayClient
from .config import ClawborateConfig
from .content_guard import check_message_compliance
from .message_patrol import run_message_patrol
from .policy_runtime import db_policy_to_runtime_bundle
from .runner import run_patrol_once
from .storage import StorageLayout, load_health, load_json, save_json, write_health

SKILL_NAME = "clawborate-skill"
SECRET_NAME = "clawborate_agent_key"
DEFAULT_HOME_ENV = "CLAWBORATE_SKILL_HOME"
ACTION_NAMES = [
    "clawborate.run_patrol_now",
    "clawborate.get_status",
    "clawborate.list_projects",
    "clawborate.get_latest_report",
    "clawborate.revalidate_key",
    "clawborate.get_project",
    "clawborate.create_project",
    "clawborate.update_project",
    "clawborate.delete_project",
    "clawborate.list_market",
    "clawborate.get_policy",
    "clawborate.submit_interest",
    "clawborate.accept_interest",
    "clawborate.decline_interest",
    "clawborate.list_incoming_interests",
    "clawborate.list_outgoing_interests",
    "clawborate.start_conversation",
    "clawborate.send_message",
    "clawborate.list_conversations",
    "clawborate.list_messages",
    "clawborate.update_conversation",
    "clawborate.check_inbox",
    "clawborate.check_message_compliance",
    "clawborate.handle_incoming_interests",
]


class InstallError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"error": self.code, "message": self.message}


class FileSecretStore:
    def __init__(self, path: Path):
        self.path = path

    def get_secret(self, name: str) -> str | None:
        data = load_json(self.path, {})
        value = data.get(name)
        return str(value) if value else None

    def set_secret(self, name: str, value: str) -> None:
        data = load_json(self.path, {})
        data[name] = value
        save_json(self.path, data)


class ManifestRegistrar:
    def __init__(self, path: Path):
        self.path = path
        self.payload: dict[str, Any] = {"skill_name": SKILL_NAME, "worker": {}, "actions": []}

    def register_worker(self, *, entrypoint: str, tick_seconds: int) -> None:
        self.payload["worker"] = {
            "entrypoint": entrypoint,
            "tick_seconds": tick_seconds,
        }

    def register_actions(self, actions: list[dict[str, Any]]) -> None:
        self.payload["actions"] = actions

    def save(self) -> dict[str, Any]:
        save_json(self.path, self.payload)
        return self.payload


@dataclass(frozen=True)
class InstalledContext:
    layout: StorageLayout
    config: ClawborateConfig
    secret_store: FileSecretStore
    agent_key: str


def default_skill_home() -> Path:
    raw = os.environ.get(DEFAULT_HOME_ENV)
    if raw:
        return Path(raw).expanduser()
    return Path.home() / f".{SKILL_NAME}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_installed_context(home: Path | None = None) -> InstalledContext:
    layout = StorageLayout.from_root(home or default_skill_home())
    if not layout.config_path.exists():
        raise InstallError("not_installed", "Clawborate Skill is not installed yet.")
    config = ClawborateConfig.from_dict(load_json(layout.config_path, {}))
    secret_store = FileSecretStore(layout.secrets_path)
    agent_key = secret_store.get_secret(SECRET_NAME)
    if not agent_key:
        raise InstallError("missing_agent_key", "Clawborate agent key is missing. Reinstall or re-authorize the skill.")
    _sync_registration(layout, config)
    return InstalledContext(layout=layout, config=config, secret_store=secret_store, agent_key=agent_key)


def _build_client(
    context: InstalledContext,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> GatewayClient:
    if client_factory is not None:
        return client_factory(context.agent_key, context.config.base_url, context.config.anon_key)
    return GatewayClient(
        agent_key=context.agent_key,
        base_url=context.config.base_url,
        anon_key=context.config.anon_key,
    )


def _registration_actions() -> list[dict[str, Any]]:
    return [
        {"name": "clawborate.run_patrol_now", "entrypoint": "scripts/actions.py", "argv": ["run-patrol-now"]},
        {"name": "clawborate.get_status", "entrypoint": "scripts/actions.py", "argv": ["get-status"]},
        {"name": "clawborate.list_projects", "entrypoint": "scripts/actions.py", "argv": ["list-projects"]},
        {"name": "clawborate.get_latest_report", "entrypoint": "scripts/actions.py", "argv": ["get-latest-report"]},
        {"name": "clawborate.revalidate_key", "entrypoint": "scripts/actions.py", "argv": ["revalidate-key"]},
        {"name": "clawborate.get_project", "entrypoint": "scripts/actions.py", "argv": ["get-project"]},
        {"name": "clawborate.create_project", "entrypoint": "scripts/actions.py", "argv": ["create-project"]},
        {"name": "clawborate.update_project", "entrypoint": "scripts/actions.py", "argv": ["update-project"]},
        {"name": "clawborate.delete_project", "entrypoint": "scripts/actions.py", "argv": ["delete-project"]},
        {"name": "clawborate.list_market", "entrypoint": "scripts/actions.py", "argv": ["list-market"]},
        {"name": "clawborate.get_policy", "entrypoint": "scripts/actions.py", "argv": ["get-policy"]},
        {"name": "clawborate.submit_interest", "entrypoint": "scripts/actions.py", "argv": ["submit-interest"]},
        {"name": "clawborate.accept_interest", "entrypoint": "scripts/actions.py", "argv": ["accept-interest"]},
        {"name": "clawborate.decline_interest", "entrypoint": "scripts/actions.py", "argv": ["decline-interest"]},
        {
            "name": "clawborate.list_incoming_interests",
            "entrypoint": "scripts/actions.py",
            "argv": ["list-incoming-interests"],
        },
        {
            "name": "clawborate.list_outgoing_interests",
            "entrypoint": "scripts/actions.py",
            "argv": ["list-outgoing-interests"],
        },
        {"name": "clawborate.start_conversation", "entrypoint": "scripts/actions.py", "argv": ["start-conversation"]},
        {"name": "clawborate.send_message", "entrypoint": "scripts/actions.py", "argv": ["send-message"]},
        {"name": "clawborate.list_conversations", "entrypoint": "scripts/actions.py", "argv": ["list-conversations"]},
        {"name": "clawborate.list_messages", "entrypoint": "scripts/actions.py", "argv": ["list-messages"]},
        {"name": "clawborate.update_conversation", "entrypoint": "scripts/actions.py", "argv": ["update-conversation"]},
        {"name": "clawborate.check_inbox", "entrypoint": "scripts/actions.py", "argv": ["check-inbox"]},
        {
            "name": "clawborate.check_message_compliance",
            "entrypoint": "scripts/actions.py",
            "argv": ["check-message-compliance"],
        },
        {
            "name": "clawborate.handle_incoming_interests",
            "entrypoint": "scripts/actions.py",
            "argv": ["handle-incoming-interests"],
        },
    ]


def _sync_registration(layout: StorageLayout, config: ClawborateConfig) -> dict[str, Any]:
    registrar = ManifestRegistrar(layout.registration_path)
    registrar.register_worker(entrypoint="scripts/worker.py", tick_seconds=config.worker_tick_seconds)
    registrar.register_actions(_registration_actions())
    return registrar.save()


def _load_context_and_client(
    *,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> tuple[InstalledContext, GatewayClient]:
    context = load_installed_context(home=home)
    return context, _build_client(context, client_factory=client_factory)


def install_skill(
    *,
    agent_key: str,
    home: Path | None = None,
    config: ClawborateConfig | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> dict[str, Any]:
    cfg = config or ClawborateConfig()
    layout = StorageLayout.from_root(home or default_skill_home())
    layout.ensure()
    secret_store = FileSecretStore(layout.secrets_path)

    context = InstalledContext(layout=layout, config=cfg, secret_store=secret_store, agent_key=agent_key)
    client = _build_client(context, client_factory=client_factory)
    try:
        visible_projects = client.validate_agent_key()
    except AgentGatewayError as exc:
        code = "permission_denied" if exc.code == "missing_scope" else exc.code
        raise InstallError(code, exc.message) from exc
    except AgentGatewayTransportError as exc:
        raise InstallError("network_error", str(exc)) from exc

    secret_store.set_secret(SECRET_NAME, agent_key)
    save_json(layout.config_path, cfg.to_dict())
    save_json(layout.state_path, {"projects": {}})
    write_health(
        layout.health_path,
        {
            "status": "ready",
            "paused": False,
            "paused_reason": None,
            "last_attempt_at": None,
            "last_success_at": None,
            "last_error": None,
            "consecutive_failures": 0,
        },
    )
    registration = _sync_registration(layout, cfg)
    if not layout.latest_report_path.exists():
        save_json(
            layout.latest_report_path,
            {"mode": "not_run_yet", "project_count": len(visible_projects or []), "projects": []},
        )

    return {
        "ok": True,
        "skill_name": SKILL_NAME,
        "storage_dir": str(layout.root),
        "visible_project_count": len(visible_projects or []),
        "registration": registration,
        "config": cfg.to_dict(),
    }


def run_worker_tick(
    *,
    home: Path | None = None,
    now: datetime | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
    runner: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    context = load_installed_context(home=home)
    runner_callable = runner or run_patrol_once
    health = load_health(context.layout.health_path)
    attempted_at = (now or datetime.now(timezone.utc)).isoformat()
    try:
        summary = runner_callable(
            agent_key=context.agent_key,
            storage_dir=context.layout.root,
            agent_contact=context.config.agent_contact,
            now=now,
            client=_build_client(context, client_factory=client_factory),
            base_url=context.config.base_url,
            anon_key=context.config.anon_key,
        )
    except AgentGatewayError as exc:
        write_health(
            context.layout.health_path,
            {
                **health,
                "status": "paused" if exc.code == "invalid_agent_key" else "error",
                "paused": exc.code == "invalid_agent_key",
                "paused_reason": "revalidate_key_required" if exc.code == "invalid_agent_key" else None,
                "last_attempt_at": attempted_at,
                "last_error": {"code": exc.code, "message": exc.message},
                "consecutive_failures": int(health.get("consecutive_failures") or 0) + 1,
            },
        )
        raise
    except AgentGatewayTransportError as exc:
        write_health(
            context.layout.health_path,
            {
                **health,
                "status": "error",
                "paused": False,
                "paused_reason": None,
                "last_attempt_at": attempted_at,
                "last_error": {"code": "network_error", "message": str(exc)},
                "consecutive_failures": int(health.get("consecutive_failures") or 0) + 1,
            },
        )
        raise

    write_health(
        context.layout.health_path,
        {
            **health,
            "status": "ready",
            "paused": False,
            "paused_reason": None,
            "last_attempt_at": attempted_at,
            "last_success_at": attempted_at,
            "last_error": None,
            "consecutive_failures": 0,
        },
    )
    return summary


def run_patrol_now(
    *,
    home: Path | None = None,
    now: datetime | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
    runner: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return run_worker_tick(home=home, now=now, client_factory=client_factory, runner=runner)


def get_status(*, home: Path | None = None) -> dict[str, Any]:
    context = load_installed_context(home=home)
    health = load_health(context.layout.health_path)
    latest_report = load_json(context.layout.latest_report_path, None)
    return {
        "skill_name": SKILL_NAME,
        "installed": True,
        "storage_dir": str(context.layout.root),
        "config": context.config.to_dict(),
        "health": health,
        "has_latest_report": context.layout.latest_report_path.exists(),
        "latest_report": latest_report,
    }


def get_latest_report(*, home: Path | None = None) -> dict[str, Any]:
    context = load_installed_context(home=home)
    return cast(dict[str, Any], load_json(context.layout.latest_report_path, {"mode": "not_run_yet", "projects": []}))


def list_projects(
    *,
    limit: int = 200,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> list[dict[str, Any]]:
    _, client = _load_context_and_client(home=home, client_factory=client_factory)
    return client.list_my_projects(limit=limit)


def get_project(
    *,
    project_id: str,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> dict[str, Any]:
    _, client = _load_context_and_client(home=home, client_factory=client_factory)
    return client.get_project(project_id)


def create_project(
    *,
    name: str,
    summary: str | None = None,
    constraints: str | None = None,
    tags: str | None = None,
    contact: str | None = None,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> dict[str, Any]:
    _, client = _load_context_and_client(home=home, client_factory=client_factory)
    result = client.create_project(
        name=name,
        summary=summary,
        constraints=constraints,
        tags=tags,
        contact=contact,
    )
    return {"ok": True, "result": result}


def update_project(
    *,
    project_id: str,
    name: str | None = None,
    summary: str | None = None,
    constraints: str | None = None,
    tags: str | None = None,
    contact: str | None = None,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> dict[str, Any]:
    _, client = _load_context_and_client(home=home, client_factory=client_factory)
    result = client.update_project(
        project_id=project_id,
        name=name,
        summary=summary,
        constraints=constraints,
        tags=tags,
        contact=contact,
    )
    return {"ok": True, "result": result}


def delete_project(
    *,
    project_id: str,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> dict[str, Any]:
    _, client = _load_context_and_client(home=home, client_factory=client_factory)
    result = client.delete_project(project_id)
    return {"ok": True, "result": result}


def list_market(
    *,
    limit: int = 20,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> list[dict[str, Any]]:
    _, client = _load_context_and_client(home=home, client_factory=client_factory)
    return client.list_market(limit=limit)


def get_policy(
    *,
    project_id: str | None = None,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> dict[str, Any] | None:
    _, client = _load_context_and_client(home=home, client_factory=client_factory)
    return client.get_policy(project_id=project_id)


def submit_interest(
    *,
    project_id: str,
    message: str,
    contact: str | None = None,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> dict[str, Any]:
    _, client = _load_context_and_client(home=home, client_factory=client_factory)
    result = client.submit_interest(project_id=project_id, message=message, contact=contact)
    return {"ok": True, "result": result}


def accept_interest(
    *,
    interest_id: str,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> dict[str, Any]:
    _, client = _load_context_and_client(home=home, client_factory=client_factory)
    result = client.accept_interest(interest_id)
    return {"ok": True, "result": result}


def decline_interest(
    *,
    interest_id: str,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> dict[str, Any]:
    _, client = _load_context_and_client(home=home, client_factory=client_factory)
    result = client.decline_interest(interest_id)
    return {"ok": True, "result": result}


def list_incoming_interests(
    *,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> list[dict[str, Any]]:
    _, client = _load_context_and_client(home=home, client_factory=client_factory)
    return client.list_incoming_interests()


def list_outgoing_interests(
    *,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> list[dict[str, Any]]:
    _, client = _load_context_and_client(home=home, client_factory=client_factory)
    return client.list_outgoing_interests()


def start_conversation(
    *,
    project_id: str,
    interest_id: str,
    receiver_user_id: str,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> dict[str, Any]:
    _, client = _load_context_and_client(home=home, client_factory=client_factory)
    result = client.start_conversation(
        project_id=project_id,
        interest_id=interest_id,
        receiver_user_id=receiver_user_id,
    )
    return {"ok": True, "result": result}


def send_message(
    *,
    conversation_id: str,
    message: str,
    agent_name: str | None = None,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> dict[str, Any]:
    _, client = _load_context_and_client(home=home, client_factory=client_factory)

    # Content Guard: validate message against policy before sending
    policy_row = client.get_policy()
    if policy_row:
        bundle = db_policy_to_runtime_bundle(policy_row)
        triggers = set(bundle["row"].get("handoff_triggers") or [])
        compliance = check_message_compliance(message, bundle["effective_policy"], triggers)
        if not compliance.passed:
            return {
                "ok": False,
                "blocked": True,
                "violations": [v.to_dict() for v in compliance.violations],
                "message": "Message blocked by policy. Modify content and retry.",
            }

    result = client.send_message(conversation_id=conversation_id, message=message, agent_name=agent_name)
    return {"ok": True, "result": result, "compliance_check": "passed"}


def list_conversations(
    *,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> list[dict[str, Any]]:
    _, client = _load_context_and_client(home=home, client_factory=client_factory)
    return client.list_conversations()


def list_messages(
    *,
    conversation_id: str,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> list[dict[str, Any]]:
    _, client = _load_context_and_client(home=home, client_factory=client_factory)
    return client.list_messages(conversation_id=conversation_id)


def update_conversation(
    *,
    conversation_id: str,
    status: str | None = None,
    summary_for_owner: str | None = None,
    recommended_next_step: str | None = None,
    last_agent_decision: str | None = None,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> dict[str, Any]:
    _, client = _load_context_and_client(home=home, client_factory=client_factory)
    result = client.update_conversation(
        conversation_id=conversation_id,
        status=status,
        summary_for_owner=summary_for_owner,
        recommended_next_step=recommended_next_step,
        last_agent_decision=last_agent_decision,
    )
    return {"ok": True, "result": result}


def check_inbox(
    *,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> dict[str, Any]:
    context, client = _load_context_and_client(home=home, client_factory=client_factory)
    projects = client.list_my_projects(limit=200)
    all_items: list[dict[str, Any]] = []
    state = load_json(context.layout.state_path, {})
    conversations = client.list_conversations()

    for project in projects or []:
        project_id = project.get("id")
        policy_row = client.get_policy(project_id=project_id)
        bundle = db_policy_to_runtime_bundle(
            policy_row,
            project_id=project_id,
            owner_user_id=project.get("user_id"),
        )
        report = run_message_patrol(
            agent_user_id=project.get("user_id", ""),
            conversations=conversations or [],
            policy_bundle=bundle,
            conversation_state=state.get("conversations", {}),
            client=client,
        )
        all_items.extend(item.to_dict() for item in report.items_needing_attention)
        for conv_id, updates in report.state_updates.items():
            state.setdefault("conversations", {})[conv_id] = updates

    save_json(context.layout.state_path, state)
    return {"ok": True, "inbox_items": all_items, "total": len(all_items)}


def check_message_compliance_action(
    *,
    message: str,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> dict[str, Any]:
    _, client = _load_context_and_client(home=home, client_factory=client_factory)
    policy_row = client.get_policy()
    if not policy_row:
        return {"ok": True, "passed": True, "violations": [], "message": "No policy found, all content allowed."}
    bundle = db_policy_to_runtime_bundle(policy_row)
    triggers = set(bundle["row"].get("handoff_triggers") or [])
    result = check_message_compliance(message, bundle["effective_policy"], triggers)
    return {"ok": True, "passed": result.passed, "violations": [v.to_dict() for v in result.violations]}


def handle_incoming_interests(
    *,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> dict[str, Any]:
    _, client = _load_context_and_client(home=home, client_factory=client_factory)
    incoming = client.list_incoming_interests()
    open_incoming = [i for i in (incoming or []) if i.get("status") == "open"]
    if not open_incoming:
        return {"ok": True, "processed": 0, "results": []}

    policy_row = client.get_policy()
    bundle = db_policy_to_runtime_bundle(policy_row)
    auto_accept = bundle["effective_policy"].get("automation", {}).get("autoAcceptIncomingInterest", False)
    require_approval = (
        bundle["effective_policy"]
        .get("automation", {})
        .get(
            "requireHumanApprovalForAcceptingInterest",
            True,
        )
    )

    results: list[dict[str, Any]] = []
    for interest in open_incoming:
        if auto_accept and not require_approval:
            result = client.accept_interest(interest["id"])
            results.append({"interest_id": interest["id"], "action": "auto_accepted", "result": result})
        else:
            results.append({"interest_id": interest["id"], "action": "needs_human"})
    return {"ok": True, "processed": len(results), "results": results}


def revalidate_key(
    *,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> dict[str, Any]:
    context = load_installed_context(home=home)
    client = _build_client(context, client_factory=client_factory)
    attempted_at = utc_now_iso()
    try:
        visible_projects = client.validate_agent_key()
    except AgentGatewayError as exc:
        write_health(
            context.layout.health_path,
            {
                "status": "paused" if exc.code == "invalid_agent_key" else "error",
                "paused": exc.code == "invalid_agent_key",
                "paused_reason": "revalidate_key_required" if exc.code == "invalid_agent_key" else None,
                "last_attempt_at": attempted_at,
                "last_error": {"code": exc.code, "message": exc.message},
            },
        )
        raise
    except AgentGatewayTransportError as exc:
        write_health(
            context.layout.health_path,
            {
                "status": "error",
                "paused": False,
                "paused_reason": None,
                "last_attempt_at": attempted_at,
                "last_error": {"code": "network_error", "message": str(exc)},
            },
        )
        raise

    write_health(
        context.layout.health_path,
        {
            "status": "ready",
            "paused": False,
            "paused_reason": None,
            "last_attempt_at": attempted_at,
            "last_success_at": attempted_at,
            "last_error": None,
            "consecutive_failures": 0,
        },
    )
    return {"ok": True, "visible_project_count": len(visible_projects or [])}


__all__ = [
    "ACTION_NAMES",
    "DEFAULT_HOME_ENV",
    "accept_interest",
    "check_inbox",
    "check_message_compliance_action",
    "create_project",
    "decline_interest",
    "delete_project",
    "FileSecretStore",
    "get_policy",
    "get_project",
    "handle_incoming_interests",
    "InstallError",
    "InstalledContext",
    "list_conversations",
    "list_incoming_interests",
    "list_market",
    "list_messages",
    "list_outgoing_interests",
    "ManifestRegistrar",
    "SECRET_NAME",
    "SKILL_NAME",
    "send_message",
    "start_conversation",
    "submit_interest",
    "update_conversation",
    "update_project",
    "default_skill_home",
    "get_latest_report",
    "get_status",
    "install_skill",
    "list_projects",
    "load_installed_context",
    "revalidate_key",
    "run_patrol_now",
    "run_worker_tick",
]
