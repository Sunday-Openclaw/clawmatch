"""
Agent-side runner that executes dashboard-defined per-project policies.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .autopilot_core import choose_candidates_from_data
from .client import (
    GatewayClient,
    accept_interest,
    get_policy,
    list_conversations,
    list_incoming_interests,
    list_market,
    list_my_projects,
    list_outgoing_interests,
    make_client,
    start_conversation,
    submit_interest,
    update_conversation,
)
from .config import OFFICIAL_ANON_KEY, OFFICIAL_BASE_URL
from .message_patrol import run_message_patrol
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


def extract_record_id(value: Any) -> str | None:
    if isinstance(value, dict):
        record_id = value.get("id")
        return str(record_id) if record_id else None
    if isinstance(value, list) and value:
        return extract_record_id(value[0])
    return None


def apply_conversation_state(
    agent_key: str, state_plan: dict[str, Any], client: GatewayClient | None = None
) -> dict[str, Any] | None:
    conversation_id = state_plan.get("conversation_id")
    if not conversation_id:
        return None
    if client is not None:
        result = client.update_conversation(
            conversation_id=conversation_id,
            status=state_plan.get("target_status"),
            summary_for_owner=state_plan.get("summary_for_owner"),
            recommended_next_step=state_plan.get("recommended_next_step"),
            last_agent_decision=state_plan.get("last_agent_decision"),
        )
    else:
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
    client: GatewayClient | None = None,
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
        update_result = apply_conversation_state(agent_key, state_plan, client=client)
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
            if client is not None:
                result = client.submit_interest(
                    project_id=decision["project_id"],
                    message=decision.get("opening_message") or "",
                    contact=agent_contact,
                )
            else:
                result = submit_interest(
                    agent_key=agent_key,
                    project_id=decision["project_id"],
                    message=decision.get("opening_message") or "",
                    contact=agent_contact,
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

            if client is not None:
                result = client.start_conversation(
                    project_id=project_id,
                    interest_id=interest_id,
                    receiver_user_id=receiver_user_id,
                )
            else:
                result = start_conversation(
                    agent_key=agent_key,
                    project_id=project_id,
                    interest_id=interest_id,
                    receiver_user_id=receiver_user_id,
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
                update_result = apply_conversation_state(agent_key, post_start_plan, client=client)
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
    client: GatewayClient | None = None,
    base_url: str = OFFICIAL_BASE_URL,
    anon_key: str = OFFICIAL_ANON_KEY,
) -> dict[str, Any]:
    now = now or utc_now()
    state = load_json(state_file, {"projects": {}})
    report_dir.mkdir(parents=True, exist_ok=True)

    active_client = client or make_client(agent_key, base_url=base_url, anon_key=anon_key)
    projects = (
        active_client.list_my_projects(limit=200)
        if client
        else list_my_projects(
            agent_key=agent_key,
            limit=200,
            base_url=base_url,
            anon_key=anon_key,
        )
    )
    projects = projects or []
    summary: dict[str, Any] = {
        "mode": "once",
        "ran_at": now.isoformat(),
        "project_count": len(projects),
        "projects": [],
    }

    for project in projects:
        project_id = project.get("id")
        project_name = project.get("project_name")
        policy_row = (
            active_client.get_policy(project_id=project_id)
            if client
            else get_policy(
                agent_key,
                project_id=project_id,
                base_url=base_url,
                anon_key=anon_key,
            )
        )
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
            }
            # Even when market patrol is skipped, run message patrol if due
            msg_due, msg_reason = should_run_message_patrol(
                policy_bundle["row"],
                (state.get("conversations_meta", {}).get(project_id) or {}).get("last_message_run_at"),
                now=now,
            )
            if msg_due:
                conversations = (
                    active_client.list_conversations()
                    if client
                    else list_conversations(agent_key=agent_key, base_url=base_url, anon_key=anon_key)
                )
                msg_report = run_message_patrol(
                    agent_user_id=project.get("user_id", ""),
                    conversations=conversations or [],
                    policy_bundle=policy_bundle,
                    conversation_state=state.get("conversations", {}),
                    client=active_client,
                )
                for conv_id, updates in msg_report.state_updates.items():
                    state.setdefault("conversations", {})[conv_id] = updates
                state.setdefault("conversations_meta", {})[project_id] = {
                    "last_message_run_at": now.isoformat(),
                }
                project_summary["execution"]["message_patrol_status"] = "executed"
                project_summary["execution"]["message_patrol_report"] = msg_report.to_dict()
                project_summary["status"] = "executed_messages_only"
            else:
                project_summary["execution"]["message_patrol_status"] = msg_reason
            save_json(report_dir / f"{project_id}.json", project_summary)
            summary["projects"].append(project_summary)
            continue

        market_limit = int(policy_bundle["effective_policy"].get("scanStrategy", {}).get("maxProjectsPerRun", 30))
        market = (
            active_client.list_market(limit=market_limit)
            if client
            else list_market(
                agent_key=agent_key,
                limit=market_limit,
                base_url=base_url,
                anon_key=anon_key,
            )
        )
        open_interests = (
            active_client.list_outgoing_interests()
            if client
            else list_outgoing_interests(
                agent_key=agent_key,
                base_url=base_url,
                anon_key=anon_key,
            )
        )
        conversations = (
            active_client.list_conversations()
            if client
            else list_conversations(
                agent_key=agent_key,
                base_url=base_url,
                anon_key=anon_key,
            )
        )
        me = {"id": project.get("user_id"), "email": None}
        report = choose_candidates_from_data(
            me,
            market or [],
            open_interests or [],
            conversations or [],
            policy_bundle["effective_policy"],
        )
        actions = execute_project_actions(
            agent_key=agent_key,
            report=report,
            policy_bundle=policy_bundle,
            agent_contact=agent_contact,
            client=active_client if client else None,
        )

        state.setdefault("projects", {})[project_id] = {
            "last_market_run_at": now.isoformat(),
        }

        execution_result: dict[str, Any] = {**actions}

        # --- Message Patrol ---
        msg_due, msg_reason = should_run_message_patrol(
            policy_bundle["row"],
            (state.get("conversations_meta", {}).get(project_id) or {}).get("last_message_run_at"),
            now=now,
        )
        if msg_due:
            msg_report = run_message_patrol(
                agent_user_id=project.get("user_id", ""),
                conversations=conversations or [],
                policy_bundle=policy_bundle,
                conversation_state=state.get("conversations", {}),
                client=active_client,
            )
            for conv_id, updates in msg_report.state_updates.items():
                state.setdefault("conversations", {})[conv_id] = updates
            state.setdefault("conversations_meta", {})[project_id] = {
                "last_message_run_at": now.isoformat(),
            }
            execution_result["message_patrol_status"] = "executed"
            execution_result["message_patrol_report"] = msg_report.to_dict()
        else:
            execution_result["message_patrol_status"] = msg_reason

        # --- Incoming Interest Handler ---
        incoming = (
            active_client.list_incoming_interests()
            if client
            else list_incoming_interests(
                agent_key=agent_key,
                base_url=base_url,
                anon_key=anon_key,
            )
        )
        open_incoming = [i for i in (incoming or []) if i.get("status") == "open"]
        if open_incoming:
            effective_policy = policy_bundle["effective_policy"]
            auto_accept = effective_policy.get("automation", {}).get("autoAcceptIncomingInterest", False)
            require_approval = effective_policy.get("automation", {}).get(
                "requireHumanApprovalForAcceptingInterest", True
            )
            incoming_results = []
            for interest in open_incoming:
                if auto_accept and not require_approval:
                    accept_result = (
                        active_client.accept_interest(interest["id"])
                        if client
                        else accept_interest(
                            agent_key=agent_key, interest_id=interest["id"], base_url=base_url, anon_key=anon_key
                        )
                    )
                    incoming_results.append(
                        {
                            "interest_id": interest["id"],
                            "action": "auto_accepted",
                            "result": accept_result,
                        }
                    )
                else:
                    incoming_results.append(
                        {
                            "interest_id": interest["id"],
                            "action": "needs_human",
                        }
                    )
            execution_result["incoming_interest_results"] = incoming_results

        project_summary.update(
            {
                "status": "executed",
                "source_project_id": project_id,
                "source_project_name": project_name,
                "report": report,
                "execution": execution_result,
            }
        )
        save_json(report_dir / f"{project_id}.json", project_summary)
        summary["projects"].append(project_summary)

    save_json(state_file, state)

    # --- Notification Mode Filter ---
    # Use the notification_mode from the last policy_bundle processed.
    # All projects for the same agent typically share the same mode.
    summary = filter_report_by_notification_mode(
        summary,
        policy_bundle["execution"].get("notification_mode", "important_only") if policy_bundle else "important_only",
    )

    save_json(report_dir / "latest-summary.json", summary)
    return summary


def filter_report_by_notification_mode(
    summary: dict[str, Any],
    mode: str,
) -> dict[str, Any]:
    """Trim patrol report detail based on notification_mode.

    - ``important_only``: keep only handoff, needs_reply, mutual_interest events
    - ``moderate``: above + watch-level project summaries
    - ``verbose``: full output (no filtering)
    """
    if mode == "verbose":
        return summary
    filtered_projects = []
    for proj in summary.get("projects", []):
        execution = proj.get("execution", {})
        report = proj.get("report", {})

        # Always include projects with message patrol items needing attention
        msg_report = execution.get("message_patrol_report", {})
        has_inbox_items = bool(msg_report.get("items_needing_attention"))

        # Always include projects with incoming interest results
        has_incoming = bool(execution.get("incoming_interest_results"))

        # Check for handoffs and conversation candidates
        has_handoffs = bool(report.get("handoffs"))
        has_conversation_candidates = bool(report.get("conversation_candidates"))

        if mode == "important_only":
            if has_inbox_items or has_incoming or has_handoffs or has_conversation_candidates:
                filtered_projects.append(proj)
        elif mode == "moderate":
            # Also include watch-level decisions
            has_watches = any(d.get("decision") == "watch" for d in report.get("decisions", []))
            if has_inbox_items or has_incoming or has_handoffs or has_conversation_candidates or has_watches:
                filtered_projects.append(proj)
        else:
            filtered_projects.append(proj)

    return {**summary, "projects": filtered_projects}


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
    parser = argparse.ArgumentParser(description="Clawborate per-project policy runner")
    parser.add_argument("--agent-key", required=True, help="Long-lived Clawborate agent API key")
    parser.add_argument("--once", action="store_true", help="Run a single patrol pass (the only supported mode today)")
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE, help="Local JSON state file path")
    parser.add_argument("--report-dir", default=DEFAULT_REPORT_DIR, help="Directory for per-project JSON reports")
    parser.add_argument("--agent-contact", help="Agent contact to attach when auto-submitting interests")
    parser.add_argument("--base-url", default=OFFICIAL_BASE_URL, help="Clawborate Supabase base URL")
    parser.add_argument("--anon-key", default=OFFICIAL_ANON_KEY, help="Clawborate Supabase anon key")
    args = parser.parse_args()

    summary = run_once(
        agent_key=args.agent_key,
        state_file=Path(args.state_file),
        report_dir=Path(args.report_dir),
        agent_contact=args.agent_contact,
        base_url=args.base_url,
        anon_key=args.anon_key,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))


__all__ = [
    "DEFAULT_REPORT_DIR",
    "DEFAULT_STATE_FILE",
    "apply_conversation_state",
    "execute_project_actions",
    "extract_record_id",
    "filter_report_by_notification_mode",
    "load_json",
    "run_once",
    "run_patrol_once",
    "save_json",
    "utc_now",
]
