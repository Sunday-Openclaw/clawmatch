from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

from .client import AgentGatewayError, AgentGatewayTransportError, GatewayClient
from .config import ClawborateConfig
from .content_guard import check_message_compliance
from .message_patrol import run_message_patrol
from .policy_runtime import db_policy_to_runtime_bundle, should_run_market_patrol, should_run_message_patrol
from .runner import run_patrol_once
from .storage import StorageLayout, load_health, load_json, load_state, save_json, write_health, write_state

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
    "clawborate.get_patrol_brief",
    "clawborate.list_market_page",
    "clawborate.list_project_conversations",
    "clawborate.list_conversation_messages",
    "clawborate.apply_market_decision",
    "clawborate.apply_conversation_decision",
    "clawborate.resolve_pending_action",
    "clawborate.get_bootstrap_plan",
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
    return Path.home() / ".openclaw" / "clawborate"


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


def _load_runtime_state(context: InstalledContext) -> dict[str, Any]:
    return load_state(context.layout.state_path)


def _save_runtime_state(context: InstalledContext, state: dict[str, Any]) -> dict[str, Any]:
    return write_state(context.layout.state_path, state)


def _project_state_bucket(state: dict[str, Any], project_id: str) -> dict[str, Any]:
    projects = state.setdefault("projects", {})
    bucket = projects.setdefault(
        project_id,
        {
            "last_market_run_at": None,
            "last_message_run_at": None,
            "market_cursor": 0,
            "last_tick_id": None,
        },
    )
    return cast(dict[str, Any], bucket)


def _conversation_state_bucket(state: dict[str, Any], conversation_id: str) -> dict[str, Any]:
    conversations = state.setdefault("conversations", {})
    bucket = conversations.setdefault(
        conversation_id,
        {
            "last_seen_message_id": None,
            "last_processed_inbound_message_id": None,
            "last_auto_reply_at": None,
        },
    )
    return cast(dict[str, Any], bucket)


def _next_action_token(state: dict[str, Any], prefix: str) -> str:
    counters = state.setdefault("counters", {})
    current = int(counters.get(prefix, 0)) + 1
    counters[prefix] = current
    return f"{prefix}{current:02d}"


def _pending_action_expiry(now: datetime | None = None) -> str:
    anchor = now or datetime.now(timezone.utc)
    return (anchor + timedelta(days=7)).isoformat()


def _write_patrol_prompt(layout: StorageLayout) -> Path:
    prompt_path = layout.root / "CLAWBORATE_PATROL.md"
    content = """# Clawborate Patrol

你正在执行 Clawborate 的后台巡逻。

规则：
- 必须先调用 `clawborate.get_patrol_brief`
- 市场匹配、是否发 interest、是否回复 conversation，全部由你判断
- 不要依赖简单 tags overlap 或静态规则做最终结论
- `extra_requirements` 具有高优先级
- 不确定时返回 `ask_user`
- 如果 patrol brief 包含 `has_pending_user_actions: true` 或 `notice` 字段，必须向用户报告待办事项，禁止输出 `CLAWBORATE_IDLE`
- 仅当本轮没有任何用户可见事项且无待办事项时，才输出 `CLAWBORATE_IDLE`

建议流程：
1. 读取 patrol brief
2. 对每个 due 项目调用 `clawborate.list_market_page` / `clawborate.list_project_conversations`
3. 对每个候选作出结构化 decision
4. 调用 `clawborate.apply_market_decision` / `clawborate.apply_conversation_decision`
5. 对 incoming interest 只做整理，不自动接受/拒绝
"""
    prompt_path.write_text(content, encoding="utf-8")
    return prompt_path


def _read_openclaw_config(openclaw_root: str | Path | None) -> dict[str, Any]:
    root = Path(openclaw_root).expanduser() if openclaw_root else Path.home() / ".openclaw"
    config_path = root / "openclaw.json"
    return cast(dict[str, Any], load_json(config_path, {}))


def _resolve_workspace_path(openclaw_root: str | Path | None, openclaw_config: dict[str, Any]) -> Path:
    root = Path(openclaw_root).expanduser() if openclaw_root else Path.home() / ".openclaw"
    defaults = (openclaw_config.get("agents") or {}).get("defaults") or {}
    workspace = defaults.get("workspace")
    if workspace:
        return Path(str(workspace)).expanduser()
    return root / "workspace"


def _detect_primary_delivery(openclaw_config: dict[str, Any]) -> dict[str, Any]:
    bindings = openclaw_config.get("bindings") or []
    first_binding = bindings[0] if bindings else {}
    match = first_binding.get("match") or {}
    return {
        "channel": match.get("channel"),
        "accountId": match.get("accountId"),
        "to": None,
    }


def _build_bootstrap_plan(context: InstalledContext) -> dict[str, Any]:
    openclaw_config = _read_openclaw_config(context.config.openclaw_root)
    workspace_path = _resolve_workspace_path(context.config.openclaw_root, openclaw_config)
    delivery = _detect_primary_delivery(openclaw_config)
    session_key = f"agent:{context.config.patrol_agent}:{context.config.patrol_session}"
    cli = context.config.openclaw_cli or "openclaw"
    prompt_path = workspace_path / "CLAWBORATE_PATROL.md"
    cron_cmd = (
        f'{cli} cron add --name "{context.config.patrol_session}" '
        f'--agent "{context.config.patrol_agent}" '
        f'--session "isolated" '
        f'--session-key "{session_key}" '
        f'--every "{context.config.patrol_every_minutes}m" '
        f'--max-duration "180s" '
        f'--message "Read {prompt_path.name} and execute one Clawborate patrol tick. '
        f'If nothing requires user-visible output, reply CLAWBORATE_IDLE."'
    )
    if delivery.get("channel"):
        cron_cmd += f' --channel "{delivery["channel"]}"'
    if delivery.get("accountId"):
        cron_cmd += f' --account "{delivery["accountId"]}"'
    if delivery.get("to"):
        cron_cmd += f' --to "{delivery["to"]}"'

    return {
        "openclaw_root": context.config.openclaw_root,
        "openclaw_cli": context.config.openclaw_cli or "openclaw",
        "workspace_path": str(workspace_path),
        "prompt_path": str(prompt_path),
        "cron": {
            "name": context.config.patrol_session,
            "every": f"{context.config.patrol_every_minutes}m",
            "agent": context.config.patrol_agent,
            "session": "isolated",
            "session_key": session_key,
            "message": (
                f"Read {prompt_path.name} and execute one Clawborate patrol tick. "
                "If nothing requires user-visible output, reply CLAWBORATE_IDLE."
            ),
            "max_duration": "180s",
            "light_context": True,
            "best_effort_deliver": True,
            "delivery": delivery,
            "command_preview": cron_cmd,
        },
        "gateway": openclaw_config.get("gateway") or {},
        "bindings": openclaw_config.get("bindings") or [],
    }


def _policy_bundle_for_project(client: GatewayClient, project: dict[str, Any]) -> dict[str, Any]:
    project_id = str(project.get("id") or "")
    policy_row = client.get_policy(project_id=project_id)
    return db_policy_to_runtime_bundle(
        policy_row,
        project_id=project_id,
        owner_user_id=project.get("user_id"),
    )


def _find_conversation_by_id(client: GatewayClient, conversation_id: str) -> dict[str, Any] | None:
    for conversation in client.list_conversations():
        if str(conversation.get("id")) == conversation_id:
            return cast(dict[str, Any], conversation)
    return None


def _resolve_conversation_source_project(
    *,
    conversation: dict[str, Any],
    interests_by_id: dict[str, dict[str, Any]],
    project_ids: set[str],
) -> tuple[str | None, str]:
    source_project_id = conversation.get("source_project_id")
    if source_project_id:
        return str(source_project_id), "conversation.source_project_id"

    interest_id = conversation.get("interest_id")
    linked_interest = interests_by_id.get(str(interest_id)) if interest_id else None
    if linked_interest and linked_interest.get("source_project_id"):
        return str(linked_interest["source_project_id"]), "interest.source_project_id"

    project_id = conversation.get("project_id")
    if project_id and str(project_id) in project_ids:
        return str(project_id), "conversation.project_id"

    return None, "unresolved"


def _always_on_handoff_triggers() -> set[str]:
    return {"before_contact_share", "before_commitment"}


def _message_guard_result(message: str, policy_bundle: dict[str, Any]) -> dict[str, Any]:
    compliance = check_message_compliance(
        message,
        policy_bundle.get("effective_policy", {}),
        _always_on_handoff_triggers(),
    )
    return {
        "passed": compliance.passed,
        "violations": [violation.to_dict() for violation in compliance.violations],
    }


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
        {"name": "clawborate.get_patrol_brief", "entrypoint": "scripts/actions.py", "argv": ["get-patrol-brief"]},
        {"name": "clawborate.list_market_page", "entrypoint": "scripts/actions.py", "argv": ["list-market-page"]},
        {
            "name": "clawborate.list_project_conversations",
            "entrypoint": "scripts/actions.py",
            "argv": ["list-project-conversations"],
        },
        {
            "name": "clawborate.list_conversation_messages",
            "entrypoint": "scripts/actions.py",
            "argv": ["list-conversation-messages"],
        },
        {
            "name": "clawborate.apply_market_decision",
            "entrypoint": "scripts/actions.py",
            "argv": ["apply-market-decision"],
        },
        {
            "name": "clawborate.apply_conversation_decision",
            "entrypoint": "scripts/actions.py",
            "argv": ["apply-conversation-decision"],
        },
        {
            "name": "clawborate.resolve_pending_action",
            "entrypoint": "scripts/actions.py",
            "argv": ["resolve-pending-action"],
        },
        {
            "name": "clawborate.get_bootstrap_plan",
            "entrypoint": "scripts/actions.py",
            "argv": ["get-bootstrap-plan"],
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
    write_state(
        layout.state_path,
        {
            "schema_version": 2,
            "projects": {},
            "conversations": {},
            "pending_actions": {},
            "bootstrap": {
                "installed_at": utc_now_iso(),
                "openclaw_root": cfg.openclaw_root,
                "patrol_agent": cfg.patrol_agent,
                "patrol_session": cfg.patrol_session,
            },
        },
    )
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
    patrol_prompt_path = _write_patrol_prompt(layout)
    openclaw_config = _read_openclaw_config(cfg.openclaw_root)
    workspace_path = _resolve_workspace_path(cfg.openclaw_root, openclaw_config)
    patrol_prompt_content = patrol_prompt_path.read_text(encoding="utf-8")
    workspace_patrol_prompt_path = workspace_path / "CLAWBORATE_PATROL.md"
    workspace_patrol_prompt_path.parent.mkdir(parents=True, exist_ok=True)
    workspace_patrol_prompt_path.write_text(patrol_prompt_content, encoding="utf-8")
    workspace_skill_dir = workspace_path / "skills" / SKILL_NAME
    workspace_skill_dir.mkdir(parents=True, exist_ok=True)
    workspace_skill_patrol_prompt_path = workspace_skill_dir / "CLAWBORATE_PATROL.md"
    workspace_skill_patrol_prompt_path.write_text(patrol_prompt_content, encoding="utf-8")
    # Copy SKILL.md into the workspace so the OpenClaw WebUI can discover it.
    source_skill_md = Path(__file__).resolve().parent.parent / "SKILL.md"
    if source_skill_md.exists():
        (workspace_skill_dir / "SKILL.md").write_text(source_skill_md.read_text(encoding="utf-8"), encoding="utf-8")
    bootstrap_plan = _build_bootstrap_plan(context)
    save_json(layout.root / "bootstrap-plan.json", bootstrap_plan)
    if not layout.latest_report_path.exists():
        save_json(
            layout.latest_report_path,
            {
                "mode": "not_run_yet",
                "project_count": len(visible_projects or []),
                "projects": [],
                "pending_actions": [],
                "incoming_interests": [],
            },
        )

    return {
        "ok": True,
        "skill_name": SKILL_NAME,
        "storage_dir": str(layout.root),
        "visible_project_count": len(visible_projects or []),
        "registration": registration,
        "config": cfg.to_dict(),
        "patrol_prompt_path": str(patrol_prompt_path),
        "workspace_patrol_prompt_path": str(workspace_patrol_prompt_path),
        "workspace_skill_patrol_prompt_path": str(workspace_skill_patrol_prompt_path),
        "bootstrap_plan": bootstrap_plan,
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
    cursor: int | None = None,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> list[dict[str, Any]]:
    _, client = _load_context_and_client(home=home, client_factory=client_factory)
    return client.list_market(limit=limit, cursor=cursor)


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
    source_project_id: str | None = None,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> dict[str, Any]:
    _, client = _load_context_and_client(home=home, client_factory=client_factory)
    result = client.submit_interest(
        project_id=project_id,
        message=message,
        contact=contact,
        source_project_id=source_project_id,
    )
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
    project_id: str | None = None,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> list[dict[str, Any]]:
    _, client = _load_context_and_client(home=home, client_factory=client_factory)
    return client.list_incoming_interests(project_id=project_id)


def list_outgoing_interests(
    *,
    source_project_id: str | None = None,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> list[dict[str, Any]]:
    _, client = _load_context_and_client(home=home, client_factory=client_factory)
    return client.list_outgoing_interests(source_project_id=source_project_id)


def start_conversation(
    *,
    project_id: str,
    interest_id: str,
    receiver_user_id: str,
    source_project_id: str | None = None,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> dict[str, Any]:
    _, client = _load_context_and_client(home=home, client_factory=client_factory)
    result = client.start_conversation(
        project_id=project_id,
        interest_id=interest_id,
        receiver_user_id=receiver_user_id,
        source_project_id=source_project_id,
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
    context, client = _load_context_and_client(home=home, client_factory=client_factory)
    conversation = _find_conversation_by_id(client, conversation_id)
    projects = client.list_my_projects(limit=200)
    project_ids = {str(project.get("id")) for project in projects or [] if project.get("id")}
    incoming = client.list_incoming_interests()
    outgoing = client.list_outgoing_interests()
    interests_by_id = {str(item.get("id")): item for item in (incoming or []) + (outgoing or []) if item.get("id")}

    source_project_id = None
    source_resolution = "unknown"
    policy_bundle = db_policy_to_runtime_bundle(None)
    if conversation:
        source_project_id, source_resolution = _resolve_conversation_source_project(
            conversation=conversation,
            interests_by_id=interests_by_id,
            project_ids=project_ids,
        )
        if source_project_id:
            policy_bundle = db_policy_to_runtime_bundle(
                client.get_policy(project_id=source_project_id),
                project_id=source_project_id,
            )

    reply_behavior = policy_bundle.get("execution", {}).get("reply_behavior", "notify_then_send")
    if reply_behavior != "direct_send":
        return {
            "ok": False,
            "blocked": True,
            "reason": "policy_requires_review",
            "reply_behavior": reply_behavior,
            "message": (
                "Current policy does not allow direct sending. "
                "Use apply_conversation_decision instead so the message "
                "can be queued for user review."
            ),
            "source_project_id": source_project_id,
            "source_project_resolution": source_resolution,
        }

    compliance = _message_guard_result(message, policy_bundle)
    if not compliance["passed"]:
        return {
            "ok": False,
            "blocked": True,
            "violations": compliance["violations"],
            "message": "Message blocked by policy. Modify content and retry.",
            "source_project_id": source_project_id,
            "source_project_resolution": source_resolution,
        }

    result = client.send_message(conversation_id=conversation_id, message=message, agent_name=agent_name)
    state = _load_runtime_state(context)
    conv_state = _conversation_state_bucket(state, conversation_id)
    conv_state["last_auto_reply_at"] = utc_now_iso()
    if source_project_id:
        conv_state["source_project_id"] = source_project_id
    _save_runtime_state(context, state)
    return {
        "ok": True,
        "result": result,
        "compliance_check": "passed",
        "source_project_id": source_project_id,
        "source_project_resolution": source_resolution,
    }


def list_conversations(
    *,
    project_id: str | None = None,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> list[dict[str, Any]]:
    _, client = _load_context_and_client(home=home, client_factory=client_factory)
    return client.list_conversations(project_id=project_id)


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


def get_bootstrap_plan(
    *,
    home: Path | None = None,
) -> dict[str, Any]:
    context = load_installed_context(home=home)
    return {"ok": True, "plan": _build_bootstrap_plan(context)}


def get_patrol_brief(
    *,
    home: Path | None = None,
    now: datetime | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> dict[str, Any]:
    context, client = _load_context_and_client(home=home, client_factory=client_factory)
    state = _load_runtime_state(context)
    anchor = now or datetime.now(timezone.utc)
    tick_id = _next_action_token(state, "T")
    state["tick_id"] = tick_id

    projects = client.list_my_projects(limit=200) or []
    incoming = client.list_incoming_interests()

    # --- Reconciliation: sync local pending_actions against server state ---
    all_incoming_by_id = {str(item.get("id")): item for item in (incoming or []) if item.get("id")}
    pending_actions = state.setdefault("pending_actions", {})
    for _token, action in list(pending_actions.items()):
        if action.get("type") != "incoming_interest" or action.get("status") != "pending_user":
            continue
        interest_id = str(action.get("interest_id") or "")
        server_interest = all_incoming_by_id.get(interest_id)
        if server_interest is None:
            # Interest deleted or no longer visible — mark resolved
            action["status"] = "reconciled_gone"
            action["resolved_at"] = anchor.isoformat()
        elif server_interest.get("status") != "open":
            # User already accepted/declined on Dashboard — sync status
            action["status"] = f"reconciled_{server_interest.get('status', 'unknown')}"
            action["resolved_at"] = anchor.isoformat()

    open_incoming = [item for item in incoming if item.get("status") == "open"]
    existing_tokens = {
        str(action.get("interest_id")): token
        for token, action in pending_actions.items()
        if action.get("type") == "incoming_interest" and action.get("status") == "pending_user"
    }

    incoming_summaries: list[dict[str, Any]] = []
    for interest in open_incoming:
        interest_id = str(interest.get("id") or "")
        if not interest_id:
            continue
        token = existing_tokens.get(interest_id)
        if not token:
            token = _next_action_token(state, "I")
            state.setdefault("pending_actions", {})[token] = {
                "token": token,
                "type": "incoming_interest",
                "status": "pending_user",
                "interest_id": interest_id,
                "project_id": interest.get("target_project_id"),
                "draft_text": None,
                "created_at": anchor.isoformat(),
                "expires_at": _pending_action_expiry(anchor),
                "payload": interest,
            }
            state.setdefault("incoming_interest_notifications", {})[interest_id] = token
        incoming_summaries.append(
            {
                "token": token,
                "interest_id": interest_id,
                "target_project_id": interest.get("target_project_id"),
                "target_project_name": (interest.get("target") or {}).get("project_name"),
                "from_user_id": interest.get("from_user_id"),
                "message": interest.get("message"),
                "created_at": interest.get("created_at"),
            }
        )

    project_summaries: list[dict[str, Any]] = []
    for project in projects:
        project_id = str(project.get("id") or "")
        if not project_id:
            continue
        bundle = _policy_bundle_for_project(client, project)
        project_state = _project_state_bucket(state, project_id)
        market_due, market_reason = should_run_market_patrol(
            bundle["row"],
            cast(str | None, project_state.get("last_market_run_at")),
            now=anchor,
        )
        message_due, message_reason = should_run_message_patrol(
            bundle["row"],
            cast(str | None, project_state.get("last_message_run_at")),
            now=anchor,
        )
        project_state["last_tick_id"] = tick_id
        project_summaries.append(
            {
                "project_id": project_id,
                "project_name": project.get("project_name"),
                "policy": {
                    "market_patrol_interval": bundle["execution"]["market_patrol_interval"],
                    "message_patrol_interval": bundle["execution"]["message_patrol_interval"],
                    "interest_behavior": bundle["execution"]["interest_behavior"],
                    "reply_behavior": bundle["execution"]["reply_behavior"],
                    "extra_requirements": bundle["execution"]["extra_requirements"],
                },
                "due": {
                    "market": market_due,
                    "market_reason": market_reason,
                    "messages": message_due,
                    "message_reason": message_reason,
                },
                "state": {
                    "last_market_run_at": project_state.get("last_market_run_at"),
                    "last_message_run_at": project_state.get("last_message_run_at"),
                    "market_cursor": project_state.get("market_cursor", 0),
                },
            }
        )

    _save_runtime_state(context, state)
    pending_user_actions = [
        action for action in (state.get("pending_actions") or {}).values() if action.get("status") == "pending_user"
    ]
    has_pending = len(pending_user_actions) > 0
    latest_report = {
        "mode": "patrol_brief",
        "generated_at": anchor.isoformat(),
        "tick_id": tick_id,
        "project_count": len(project_summaries),
        "has_pending_user_actions": has_pending,
        "pending_actions": pending_user_actions,
        "incoming_interests": incoming_summaries,
        "projects": project_summaries,
    }
    save_json(context.layout.latest_report_path, latest_report)
    result = {"ok": True, **latest_report}
    if has_pending:
        result["notice"] = (
            "There are pending actions awaiting user review. "
            "Do NOT reply CLAWBORATE_IDLE. Report these pending items to the user."
        )
    return result


def list_market_page(
    *,
    project_id: str,
    cursor: int = 0,
    limit: int = 20,
    max_scan: int = 60,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> dict[str, Any]:
    context, client = _load_context_and_client(home=home, client_factory=client_factory)
    state = _load_runtime_state(context)
    projects = client.list_my_projects(limit=200) or []
    owned_ids = {str(project.get("id")) for project in projects if project.get("id")}
    source_project = next((project for project in projects if str(project.get("id")) == project_id), None)
    if not source_project:
        raise InstallError("project_not_found", f"Project {project_id} is not owned by this agent.")

    bundle = _policy_bundle_for_project(client, source_project)
    raw_market = client.list_market(limit=max_scan, cursor=cursor) or []
    outgoing = client.list_outgoing_interests(source_project_id=project_id) or []
    outgoing_target_ids = {
        str(item.get("target_project_id"))
        for item in outgoing
        if item.get("target_project_id") and item.get("status") in {"open", "accepted"}
    }

    incoming = client.list_incoming_interests()
    outgoing_all = client.list_outgoing_interests()
    interests_by_id = {str(item.get("id")): item for item in (incoming or []) + (outgoing_all or []) if item.get("id")}
    conversations = client.list_conversations() or []
    active_conversation_target_ids: set[str] = set()
    for conversation in conversations:
        resolved_project_id, _ = _resolve_conversation_source_project(
            conversation=conversation,
            interests_by_id=interests_by_id,
            project_ids=owned_ids,
        )
        if resolved_project_id != project_id:
            continue
        if str(conversation.get("status") or "") in {"closed", "closed_not_fit"}:
            continue
        target_id = conversation.get("project_id")
        if target_id:
            active_conversation_target_ids.add(str(target_id))

    filtered_items: list[dict[str, Any]] = []
    for item in raw_market:
        target_id = str(item.get("id") or "")
        if not target_id:
            continue
        if target_id in owned_ids:
            continue
        if target_id in outgoing_target_ids:
            continue
        if target_id in active_conversation_target_ids:
            continue
        if not item.get("project_name") or not item.get("public_summary"):
            continue
        filtered_items.append(item)
        if len(filtered_items) >= limit:
            break

    project_state = _project_state_bucket(state, project_id)
    project_state["market_cursor"] = cursor + len(raw_market)
    _save_runtime_state(context, state)
    return {
        "ok": True,
        "project_id": project_id,
        "project_name": source_project.get("project_name"),
        "policy": {
            "interest_behavior": bundle["execution"]["interest_behavior"],
            "extra_requirements": bundle["execution"]["extra_requirements"],
        },
        "source_project": {
            "id": project_id,
            "project_name": source_project.get("project_name"),
            "public_summary": source_project.get("public_summary"),
            "tags": source_project.get("tags"),
        },
        "items": filtered_items,
        "cursor": cursor,
        "next_cursor": cursor + len(raw_market),
        "exhausted": len(raw_market) < max_scan,
    }


def list_project_conversations(
    *,
    project_id: str,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> dict[str, Any]:
    _, client = _load_context_and_client(home=home, client_factory=client_factory)
    conversations = client.list_conversations() or []
    incoming = client.list_incoming_interests()
    outgoing = client.list_outgoing_interests()
    projects = client.list_my_projects(limit=200) or []
    project_ids = {str(project.get("id")) for project in projects if project.get("id")}
    interests_by_id = {str(item.get("id")): item for item in (incoming or []) + (outgoing or []) if item.get("id")}

    relevant: list[dict[str, Any]] = []
    for conversation in conversations:
        resolved_project_id, resolution = _resolve_conversation_source_project(
            conversation=conversation,
            interests_by_id=interests_by_id,
            project_ids=project_ids,
        )
        if resolved_project_id != project_id:
            continue
        if str(conversation.get("status") or "") in {"closed", "closed_not_fit"}:
            continue
        relevant.append({**conversation, "source_project_resolution": resolution})

    return {"ok": True, "project_id": project_id, "conversations": relevant}


def list_conversation_messages(
    *,
    conversation_id: str,
    since_id: str | None = None,
    history_limit: int = 12,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> dict[str, Any]:
    context, client = _load_context_and_client(home=home, client_factory=client_factory)
    state = _load_runtime_state(context)
    conversation = _find_conversation_by_id(client, conversation_id)
    if not conversation:
        raise InstallError("conversation_not_found", f"Conversation {conversation_id} was not found.")

    messages = client.list_messages(conversation_id=conversation_id) or []
    tracking = _conversation_state_bucket(state, conversation_id)
    pivot_id = (
        since_id
        or cast(str | None, tracking.get("last_processed_inbound_message_id"))
        or cast(str | None, tracking.get("last_seen_message_id"))
    )
    start_index = 0
    if pivot_id:
        for index, msg in enumerate(messages):
            if str(msg.get("id")) == pivot_id:
                start_index = index + 1
                break
    new_messages = messages[start_index:]
    recent_history = messages[-history_limit:]

    projects = client.list_my_projects(limit=200) or []
    project_ids = {str(project.get("id")) for project in projects if project.get("id")}
    incoming = client.list_incoming_interests()
    outgoing = client.list_outgoing_interests()
    interests_by_id = {str(item.get("id")): item for item in (incoming or []) + (outgoing or []) if item.get("id")}
    source_project_id, resolution = _resolve_conversation_source_project(
        conversation=conversation,
        interests_by_id=interests_by_id,
        project_ids=project_ids,
    )
    policy_bundle = (
        db_policy_to_runtime_bundle(client.get_policy(project_id=source_project_id), project_id=source_project_id)
        if source_project_id
        else db_policy_to_runtime_bundle(None)
    )

    return {
        "ok": True,
        "conversation": conversation,
        "source_project_id": source_project_id,
        "source_project_resolution": resolution,
        "policy": {
            "reply_behavior": policy_bundle["execution"]["reply_behavior"],
            "extra_requirements": policy_bundle["execution"]["extra_requirements"],
        },
        "recent_history": recent_history,
        "new_messages": new_messages,
        "tracking": tracking,
    }


def apply_market_decision(
    *,
    source_project_id: str,
    target_project_id: str,
    decision: str,
    confidence: float | None = None,
    reason: str | None = None,
    opening_message: str | None = None,
    target_project_name: str | None = None,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> dict[str, Any]:
    context, client = _load_context_and_client(home=home, client_factory=client_factory)
    state = _load_runtime_state(context)
    anchor = datetime.now(timezone.utc)
    project_state = _project_state_bucket(state, source_project_id)
    bundle = db_policy_to_runtime_bundle(client.get_policy(project_id=source_project_id), project_id=source_project_id)
    project_state["last_market_run_at"] = anchor.isoformat()

    normalized_decision = (decision or "skip").strip().lower()
    if normalized_decision == "skip":
        _save_runtime_state(context, state)
        return {"ok": True, "execution": "skipped"}

    should_queue = normalized_decision == "ask_user" or bundle["execution"]["interest_behavior"] == "notify_then_send"
    if should_queue:
        token = _next_action_token(state, "M")
        pending = {
            "token": token,
            "type": "market_interest",
            "status": "pending_user",
            "project_id": source_project_id,
            "target_project_id": target_project_id,
            "target_project_name": target_project_name,
            "draft_text": opening_message,
            "created_at": anchor.isoformat(),
            "expires_at": _pending_action_expiry(anchor),
            "payload": {
                "decision": normalized_decision,
                "confidence": confidence,
                "reason": reason,
            },
        }
        state.setdefault("pending_actions", {})[token] = pending
        _save_runtime_state(context, state)
        return {"ok": True, "execution": "pending_user", "pending_action": pending}

    result = client.submit_interest(
        project_id=target_project_id,
        message=opening_message or "",
        contact=context.config.agent_contact,
        source_project_id=source_project_id,
    )
    _save_runtime_state(context, state)
    return {
        "ok": True,
        "execution": "sent",
        "result": result,
        "summary": {
            "source_project_id": source_project_id,
            "target_project_id": target_project_id,
            "confidence": confidence,
            "reason": reason,
        },
    }


def apply_conversation_decision(
    *,
    source_project_id: str,
    conversation_id: str,
    decision: str,
    reply_text: str | None = None,
    confidence: float | None = None,
    reason: str | None = None,
    summary_for_owner: str | None = None,
    recommended_next_step: str | None = None,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> dict[str, Any]:
    context, client = _load_context_and_client(home=home, client_factory=client_factory)
    state = _load_runtime_state(context)
    anchor = datetime.now(timezone.utc)
    bundle = db_policy_to_runtime_bundle(client.get_policy(project_id=source_project_id), project_id=source_project_id)
    normalized_decision = (decision or "skip").strip().lower()

    project_state = _project_state_bucket(state, source_project_id)
    project_state["last_message_run_at"] = anchor.isoformat()
    conversation_state = _conversation_state_bucket(state, conversation_id)

    if normalized_decision == "skip":
        _save_runtime_state(context, state)
        return {"ok": True, "execution": "skipped"}

    guard = _message_guard_result(reply_text or "", bundle) if reply_text else {"passed": True, "violations": []}
    should_queue = (
        normalized_decision == "ask_user"
        or bundle["execution"]["reply_behavior"] == "notify_then_send"
        or not guard["passed"]
    )
    if should_queue:
        token = _next_action_token(state, "R")
        pending = {
            "token": token,
            "type": "conversation_reply",
            "status": "pending_user",
            "project_id": source_project_id,
            "conversation_id": conversation_id,
            "draft_text": reply_text,
            "created_at": anchor.isoformat(),
            "expires_at": _pending_action_expiry(anchor),
            "payload": {
                "decision": normalized_decision,
                "confidence": confidence,
                "reason": reason,
                "summary_for_owner": summary_for_owner,
                "recommended_next_step": recommended_next_step,
                "guard": guard,
            },
        }
        state.setdefault("pending_actions", {})[token] = pending
        client.update_conversation(
            conversation_id=conversation_id,
            status="needs_human",
            summary_for_owner=summary_for_owner,
            recommended_next_step=recommended_next_step,
            last_agent_decision=reason or normalized_decision,
        )
        _save_runtime_state(context, state)
        return {"ok": True, "execution": "pending_user", "pending_action": pending}

    result = client.send_message(conversation_id=conversation_id, message=reply_text or "", agent_name=None)
    client.update_conversation(
        conversation_id=conversation_id,
        status="active",
        summary_for_owner=summary_for_owner,
        recommended_next_step=recommended_next_step,
        last_agent_decision=reason or normalized_decision,
    )
    conversation_state["last_auto_reply_at"] = anchor.isoformat()
    _save_runtime_state(context, state)
    return {
        "ok": True,
        "execution": "sent",
        "result": result,
        "summary": {
            "source_project_id": source_project_id,
            "conversation_id": conversation_id,
            "confidence": confidence,
            "reason": reason,
        },
    }


def resolve_pending_action(
    *,
    action_token: str,
    decision: str,
    override_text: str | None = None,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> dict[str, Any]:
    context, client = _load_context_and_client(home=home, client_factory=client_factory)
    state = _load_runtime_state(context)
    pending_actions = state.setdefault("pending_actions", {})
    action = cast(dict[str, Any] | None, pending_actions.get(action_token))
    if not action:
        raise InstallError("pending_action_not_found", f"Pending action {action_token} was not found.")
    if action.get("status") != "pending_user":
        raise InstallError("pending_action_not_open", f"Pending action {action_token} is already resolved.")
    expires_at = action.get("expires_at")
    if expires_at and datetime.now(timezone.utc) > datetime.fromisoformat(str(expires_at).replace("Z", "+00:00")):
        raise InstallError("pending_action_expired", f"Pending action {action_token} has expired.")

    normalized = decision.strip().lower()
    payload = cast(dict[str, Any], action.get("payload") or {})
    result: Any = None

    if action.get("type") == "incoming_interest":
        interest_id = action.get("interest_id")
        if normalized in {"accept", "accepted", "接受"}:
            result = client.accept_interest(str(interest_id))
            action["status"] = "accepted"
        elif normalized in {"decline", "declined", "拒绝"}:
            result = client.decline_interest(str(interest_id))
            action["status"] = "declined"
        else:
            action["status"] = "skipped"
    elif action.get("type") == "market_interest":
        if normalized in {"send", "发送"}:
            result = client.submit_interest(
                project_id=str(action.get("target_project_id")),
                message=override_text if override_text is not None else str(action.get("draft_text") or ""),
                contact=context.config.agent_contact,
                source_project_id=cast(str | None, action.get("project_id")),
            )
            action["status"] = "sent"
        else:
            action["status"] = "skipped"
    elif action.get("type") == "conversation_reply":
        if normalized in {"send", "发送", "修改后发送"}:
            message = override_text if override_text is not None else str(action.get("draft_text") or "")
            bundle = db_policy_to_runtime_bundle(
                client.get_policy(project_id=cast(str | None, action.get("project_id"))),
                project_id=cast(str | None, action.get("project_id")),
            )
            guard = _message_guard_result(message, bundle)
            if not guard["passed"]:
                action["last_error"] = {"code": "message_blocked", "violations": guard["violations"]}
                _save_runtime_state(context, state)
                return {"ok": False, "blocked": True, "violations": guard["violations"]}
            result = client.send_message(
                conversation_id=str(action.get("conversation_id")),
                message=message,
                agent_name=None,
            )
            client.update_conversation(
                conversation_id=str(action.get("conversation_id")),
                status="active",
                summary_for_owner=payload.get("summary_for_owner"),
                recommended_next_step=payload.get("recommended_next_step"),
                last_agent_decision=payload.get("reason") or "user_confirmed_send",
            )
            action["status"] = "sent"
        else:
            action["status"] = "skipped"
    else:
        raise InstallError("unsupported_pending_action", f"Unsupported pending action type: {action.get('type')}")

    action["resolved_at"] = utc_now_iso()
    if override_text is not None:
        action["final_text"] = override_text
    _save_runtime_state(context, state)
    return {"ok": True, "result": result, "action": action}


def handle_incoming_interests(
    *,
    home: Path | None = None,
    client_factory: Callable[[str, str, str], GatewayClient] | None = None,
) -> dict[str, Any]:
    brief = get_patrol_brief(home=home, client_factory=client_factory)
    return {
        "ok": True,
        "processed": len(brief.get("incoming_interests") or []),
        "results": brief.get("incoming_interests") or [],
        "tick_id": brief.get("tick_id"),
    }


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
    "apply_conversation_decision",
    "apply_market_decision",
    "check_inbox",
    "check_message_compliance_action",
    "create_project",
    "decline_interest",
    "delete_project",
    "FileSecretStore",
    "get_bootstrap_plan",
    "get_patrol_brief",
    "get_policy",
    "get_project",
    "handle_incoming_interests",
    "InstallError",
    "InstalledContext",
    "list_conversation_messages",
    "list_conversations",
    "list_incoming_interests",
    "list_market",
    "list_market_page",
    "list_messages",
    "list_outgoing_interests",
    "list_project_conversations",
    "ManifestRegistrar",
    "resolve_pending_action",
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
