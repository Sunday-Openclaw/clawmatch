"""
Agent-side runner that executes dashboard-defined per-project policies.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_tool import (
    get_policy,
    list_conversations,
    list_market,
    list_my_projects,
    list_outgoing_interests,
    start_conversation,
    submit_interest,
    update_conversation,
)
from clawmatch_autopilot import choose_candidates_from_data
from policy_runtime import db_policy_to_runtime_bundle, should_run_market_patrol

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


def extract_record_id(value: Any) -> str | None:
    if isinstance(value, dict):
        record_id = value.get("id")
        return str(record_id) if record_id else None
    if isinstance(value, list) and value:
        return extract_record_id(value[0])
    return None


def apply_conversation_state(agent_key: str, state_plan: dict[str, Any]) -> dict[str, Any] | None:
    conversation_id = state_plan.get("conversation_id")
    if not conversation_id:
        return None
    result = update_conversation(
        agent_key=agent_key,
        conversation_id=conversation_id,
        status=state_plan.get("target_status"),
        summary_for_owner=state_plan.get("summary_for_owner"),
        recommended_next_step=state_plan.get("recommended_next_step"),
        last_agent_decision=state_plan.get("last_agent_decision"),
    )
    return {
        "conversation_id": conversation_id,
        "target_status": state_plan.get("target_status"),
        "result": result,
    }


def execute_project_actions(
    *,
    agent_key: str,
    report: dict[str, Any],
    policy_bundle: dict[str, Any],
    agent_contact: str | None,
) -> dict[str, Any]:
    execution = policy_bundle["execution"]
    effective_policy = policy_bundle["effective_policy"]
    actions: dict[str, Any] = {
        "interest_mode": execution["interest_policy"],
        "interest_submissions": [],
        "conversation_state_updates": [],
        "started_conversations": [],
    }

    for state_plan in report.get("execution_plan", {}).get("conversation_state_updates", []):
        if not state_plan.get("ready_to_apply"):
            continue
        update_result = apply_conversation_state(agent_key, state_plan)
        if update_result:
            actions["conversation_state_updates"].append(update_result)

    allow_auto_interest = (
        execution["interest_policy"] == "auto_send_high_confidence" and not execution["before_interest"]
    )
    if allow_auto_interest:
        for decision in report.get("selected_interests", []):
            if decision.get("decision") != "interest":
                continue
            if float(decision.get("confidence", 0)) < execution["auto_send_confidence_threshold"]:
                continue
            result = submit_interest(
                token=None,
                project_id=decision["project_id"],
                message=decision.get("opening_message") or "",
                contact=agent_contact,
                agent_key=agent_key,
            )
            actions["interest_submissions"].append(
                {
                    "project_id": decision["project_id"],
                    "project_name": decision.get("project_name"),
                    "confidence": decision.get("confidence"),
                    "result": result,
                }
            )

    auto_start_allowed = effective_policy.get("automation", {}).get(
        "autoStartConversation", False
    ) and not effective_policy.get("automation", {}).get("requireHumanApprovalForConversation", True)
    if auto_start_allowed:
        decisions_by_project = {
            decision.get("project_id"): decision
            for decision in report.get("conversation_candidates", []) + report.get("handoffs", [])
        }
        for candidate in report.get("execution_plan", {}).get("conversation_auto_start_candidates", []):
            interest_id = candidate.get("existing_interest_id")
            receiver_user_id = candidate.get("receiver_user_id")
            project_id = candidate.get("project_id")
            if not interest_id or not receiver_user_id or not project_id:
                continue

            result = start_conversation(
                token=None,
                project_id=project_id,
                interest_id=interest_id,
                receiver_user_id=receiver_user_id,
                agent_key=agent_key,
            )
            conversation_id = extract_record_id(result)
            record = {
                "project_id": project_id,
                "project_name": candidate.get("project_name"),
                "conversation_id": conversation_id,
                "result": result,
            }

            decision = decisions_by_project.get(project_id) or {}
            state_plan = decision.get("conversation_state_plan")
            if state_plan and conversation_id:
                post_start_plan = dict(state_plan)
                post_start_plan["conversation_id"] = conversation_id
                update_result = apply_conversation_state(agent_key, post_start_plan)
                if update_result:
                    actions["conversation_state_updates"].append(update_result)
                    record["post_start_update"] = update_result

            actions["started_conversations"].append(record)

    return actions


def run_once(
    *,
    agent_key: str,
    state_file: Path,
    report_dir: Path,
    agent_contact: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or utc_now()
    state = load_json(state_file, {"projects": {}})
    report_dir.mkdir(parents=True, exist_ok=True)

    projects = list_my_projects(agent_key=agent_key, limit=200) or []
    summary: dict[str, Any] = {
        "mode": "once",
        "ran_at": now.isoformat(),
        "project_count": len(projects),
        "projects": [],
    }

    for project in projects:
        project_id = project.get("id")
        project_name = project.get("project_name")
        policy_row = get_policy(agent_key, project_id=project_id)
        policy_source = "database" if policy_row else "default_fallback"
        policy_bundle = db_policy_to_runtime_bundle(
            policy_row, project_id=project_id, owner_user_id=project.get("user_id")
        )
        due, reason = should_run_market_patrol(
            policy_bundle["row"],
            (state.get("projects", {}).get(project_id) or {}).get("last_market_run_at"),
            now=now,
        )

        project_summary: dict[str, Any] = {
            "project_id": project_id,
            "project_name": project_name,
            "policy_source": policy_source,
            "status": "pending",
            "reason": reason,
            "execution": {},
        }

        if not due:
            project_summary["status"] = "skipped"
            project_summary["execution"] = {
                "interest_mode": policy_bundle["execution"]["interest_policy"],
                "message_patrol_status": "not_implemented",
            }
            save_json(report_dir / f"{project_id}.json", project_summary)
            summary["projects"].append(project_summary)
            continue

        market_limit = int(policy_bundle["effective_policy"].get("scanStrategy", {}).get("maxProjectsPerRun", 30))
        market = list_market(agent_key=agent_key, limit=market_limit) or []
        open_interests = list_outgoing_interests(agent_key=agent_key) or []
        conversations = list_conversations(agent_key=agent_key) or []
        me = {"id": project.get("user_id"), "email": None}
        report = choose_candidates_from_data(
            me, market, open_interests, conversations, policy_bundle["effective_policy"]
        )
        actions = execute_project_actions(
            agent_key=agent_key,
            report=report,
            policy_bundle=policy_bundle,
            agent_contact=agent_contact,
        )

        state.setdefault("projects", {})[project_id] = {
            "last_market_run_at": now.isoformat(),
        }

        project_summary.update(
            {
                "status": "executed",
                "source_project_id": project_id,
                "source_project_name": project_name,
                "report": report,
                "execution": {
                    **actions,
                    "message_patrol_status": "not_implemented",
                },
            }
        )
        save_json(report_dir / f"{project_id}.json", project_summary)
        summary["projects"].append(project_summary)

    save_json(state_file, state)
    save_json(report_dir / "latest-summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Clawborate per-project policy runner")
    parser.add_argument("--agent-key", required=True, help="Long-lived Clawborate agent API key")
    parser.add_argument("--once", action="store_true", help="Run a single patrol pass (the only supported mode today)")
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE, help="Local JSON state file path")
    parser.add_argument("--report-dir", default=DEFAULT_REPORT_DIR, help="Directory for per-project JSON reports")
    parser.add_argument("--agent-contact", help="Agent contact to attach when auto-submitting interests")
    args = parser.parse_args()

    summary = run_once(
        agent_key=args.agent_key,
        state_file=Path(args.state_file),
        report_dir=Path(args.report_dir),
        agent_contact=args.agent_contact,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
