"""
Agent-first patrol summary runner.

This module intentionally does not decide whether to send interest or reply to
conversations. It only computes per-project patrol cadence and summarizes open
incoming interests so a dedicated OpenClaw cron session can ask the agent to
act through higher-level skill actions.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .client import GatewayClient, get_policy, list_incoming_interests, list_my_projects, make_client
from .config import OFFICIAL_ANON_KEY, OFFICIAL_BASE_URL
from .policy_runtime import db_policy_to_runtime_bundle, should_run_market_patrol, should_run_message_patrol

DEFAULT_STATE_FILE = ".clawborate_policy_runner_state.json"
DEFAULT_REPORT_DIR = ".clawborate_policy_runner_reports"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run_once(
    *,
    agent_key: str,
    state_file: Path,
    report_dir: Path,
    agent_contact: str | None = None,
    now: datetime | None = None,
    client: GatewayClient | None = None,
    base_url: str = OFFICIAL_BASE_URL,
    anon_key: str = OFFICIAL_ANON_KEY,
) -> dict[str, Any]:
    del agent_contact  # The runner no longer performs direct send actions.
    anchor = now or utc_now()
    state = load_json(
        state_file,
        {
            "schema_version": 2,
            "projects": {},
            "pending_actions": {},
        },
    )
    report_dir.mkdir(parents=True, exist_ok=True)

    active_client = client or make_client(agent_key, base_url=base_url, anon_key=anon_key)
    projects = active_client.list_my_projects(limit=200) if client else list_my_projects(
        agent_key=agent_key,
        limit=200,
        base_url=base_url,
        anon_key=anon_key,
    )
    projects = projects or []
    incoming = active_client.list_incoming_interests() if client else list_incoming_interests(
        agent_key=agent_key,
        base_url=base_url,
        anon_key=anon_key,
    )
    open_incoming = [item for item in (incoming or []) if item.get("status") == "open"]

    summary: dict[str, Any] = {
        "mode": "agent_first_brief",
        "ran_at": anchor.isoformat(),
        "project_count": len(projects),
        "projects": [],
        "incoming_interests": open_incoming,
        "pending_actions": [
            action
            for action in (state.get("pending_actions") or {}).values()
            if action.get("status") == "pending_user"
        ],
    }

    for project in projects:
        project_id = project.get("id")
        if not project_id:
            continue
        project_state = (state.get("projects") or {}).get(project_id) or {}
        policy_row = active_client.get_policy(project_id=project_id) if client else get_policy(
            agent_key,
            project_id=project_id,
            base_url=base_url,
            anon_key=anon_key,
        )
        bundle = db_policy_to_runtime_bundle(policy_row, project_id=project_id, owner_user_id=project.get("user_id"))
        market_due, market_reason = should_run_market_patrol(
            bundle["row"],
            project_state.get("last_market_run_at"),
            now=anchor,
        )
        message_due, message_reason = should_run_message_patrol(
            bundle["row"],
            project_state.get("last_message_run_at"),
            now=anchor,
        )

        project_summary = {
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
        save_json(report_dir / f"{project_id}.json", project_summary)
        summary["projects"].append(project_summary)

    save_json(report_dir / "latest-summary.json", summary)
    save_json(state_file, state)
    return summary


def run_patrol_once(
    *,
    agent_key: str,
    storage_dir: Path,
    agent_contact: str | None = None,
    now: datetime | None = None,
    client: GatewayClient | None = None,
    base_url: str = OFFICIAL_BASE_URL,
    anon_key: str = OFFICIAL_ANON_KEY,
) -> dict[str, Any]:
    return run_once(
        agent_key=agent_key,
        state_file=storage_dir / "state.json",
        report_dir=storage_dir / "reports",
        agent_contact=agent_contact,
        now=now,
        client=client,
        base_url=base_url,
        anon_key=anon_key,
    )


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Clawborate agent-first patrol summary runner")
    parser.add_argument("--agent-key", required=True, help="Long-lived Clawborate agent API key")
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE, help="Local JSON state file path")
    parser.add_argument("--report-dir", default=DEFAULT_REPORT_DIR, help="Directory for patrol JSON reports")
    parser.add_argument("--base-url", default=OFFICIAL_BASE_URL, help="Clawborate Supabase base URL")
    parser.add_argument("--anon-key", default=OFFICIAL_ANON_KEY, help="Clawborate Supabase anon key")
    args = parser.parse_args()

    summary = run_once(
        agent_key=args.agent_key,
        state_file=Path(args.state_file),
        report_dir=Path(args.report_dir),
        base_url=args.base_url,
        anon_key=args.anon_key,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))


__all__ = [
    "DEFAULT_REPORT_DIR",
    "DEFAULT_STATE_FILE",
    "load_json",
    "run_once",
    "run_patrol_once",
    "save_json",
    "utc_now",
]
