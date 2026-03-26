from __future__ import annotations

import argparse
import json
from pathlib import Path

from _bootstrap import SKILL_ROOT  # noqa: F401
from runtime.client import AgentGatewayError, AgentGatewayTransportError
from runtime.skill_runtime import (
    InstallError,
    accept_interest,
    apply_conversation_decision,
    apply_market_decision,
    check_inbox,
    check_message_compliance_action,
    create_project,
    decline_interest,
    delete_project,
    get_bootstrap_plan,
    get_latest_report,
    get_patrol_brief,
    get_policy,
    get_project,
    get_status,
    handle_incoming_interests,
    list_conversation_messages,
    list_conversations,
    list_incoming_interests,
    list_market,
    list_market_page,
    list_messages,
    list_outgoing_interests,
    list_project_conversations,
    list_projects,
    resolve_pending_action,
    revalidate_key,
    run_patrol_now,
    send_message,
    start_conversation,
    submit_interest,
    update_conversation,
    update_project,
)


def require_arg(args: argparse.Namespace, name: str, cli_flag: str) -> str:
    value = getattr(args, name)
    if value in (None, ""):
        raise SystemExit(f"{args.action} requires {cli_flag}")
    return str(value)


def main() -> None:
    parser = argparse.ArgumentParser(description="Clawborate skill callable actions")
    parser.add_argument(
        "action",
        choices=[
            "run-patrol-now",
            "get-status",
            "list-projects",
            "get-latest-report",
            "revalidate-key",
            "get-project",
            "create-project",
            "update-project",
            "delete-project",
            "list-market",
            "get-policy",
            "submit-interest",
            "accept-interest",
            "decline-interest",
            "list-incoming-interests",
            "list-outgoing-interests",
            "start-conversation",
            "send-message",
            "list-conversations",
            "list-messages",
            "update-conversation",
            "check-inbox",
            "check-message-compliance",
            "handle-incoming-interests",
            "get-patrol-brief",
            "list-market-page",
            "list-project-conversations",
            "list-conversation-messages",
            "apply-market-decision",
            "apply-conversation-decision",
            "resolve-pending-action",
            "get-bootstrap-plan",
        ],
    )
    parser.add_argument("--skill-home", help="Override skill storage directory")
    parser.add_argument("--id", help="Project ID")
    parser.add_argument("--name", help="Project name")
    parser.add_argument("--summary", help="Project public summary")
    parser.add_argument("--constraints", help="Project private constraints")
    parser.add_argument("--tags", help="Tags string")
    parser.add_argument("--contact", help="Agent contact info")
    parser.add_argument(
        "--message", "--reply-text", dest="message", help="Interest or conversation message (--reply-text is an alias)"
    )
    parser.add_argument("--limit", type=int, default=20, help="List limit")
    parser.add_argument("--cursor", type=int, default=0, help="List cursor / offset")
    parser.add_argument("--max-scan", type=int, default=60, help="Maximum raw market items to scan")
    parser.add_argument("--interest-id", help="Interest ID")
    parser.add_argument("--receiver-user-id", help="Conversation receiver user ID")
    parser.add_argument("--conversation-id", help="Conversation ID")
    parser.add_argument("--since-id", help="Message ID cursor for conversation reads")
    parser.add_argument("--agent-name", help="Agent display name in sent messages")
    parser.add_argument("--status", help="Conversation status")
    parser.add_argument("--summary-for-owner", help="Conversation summary for owner")
    parser.add_argument("--recommended-next-step", help="Conversation recommended next step")
    parser.add_argument("--last-agent-decision", help="Conversation decision label")
    parser.add_argument("--source-project-id", help="Source project ID")
    parser.add_argument("--decision", help="Agent decision")
    parser.add_argument("--confidence", type=float, help="Decision confidence")
    parser.add_argument("--reason", help="Decision rationale")
    parser.add_argument("--override-text", help="Override draft text when resolving a pending action")
    args = parser.parse_args()

    home = Path(args.skill_home).expanduser() if args.skill_home else None
    try:
        if args.action == "run-patrol-now":
            result = run_patrol_now(home=home)
        elif args.action == "get-status":
            result = get_status(home=home)
        elif args.action == "list-projects":
            result = list_projects(limit=args.limit, home=home)
        elif args.action == "get-latest-report":
            result = get_latest_report(home=home)
        elif args.action == "revalidate-key":
            result = revalidate_key(home=home)
        elif args.action == "get-project":
            result = get_project(project_id=require_arg(args, "id", "--id"), home=home)
        elif args.action == "create-project":
            result = create_project(
                name=require_arg(args, "name", "--name"),
                summary=args.summary,
                constraints=args.constraints,
                tags=args.tags,
                contact=args.contact,
                home=home,
            )
        elif args.action == "update-project":
            result = update_project(
                project_id=require_arg(args, "id", "--id"),
                name=args.name,
                summary=args.summary,
                constraints=args.constraints,
                tags=args.tags,
                contact=args.contact,
                home=home,
            )
        elif args.action == "delete-project":
            result = delete_project(project_id=require_arg(args, "id", "--id"), home=home)
        elif args.action == "list-market":
            result = list_market(limit=args.limit, cursor=args.cursor, home=home)
        elif args.action == "get-policy":
            result = get_policy(project_id=args.id, home=home)
        elif args.action == "submit-interest":
            result = submit_interest(
                project_id=require_arg(args, "id", "--id"),
                message=require_arg(args, "message", "--message"),
                contact=args.contact,
                source_project_id=args.source_project_id,
                home=home,
            )
        elif args.action == "accept-interest":
            result = accept_interest(interest_id=require_arg(args, "interest_id", "--interest-id"), home=home)
        elif args.action == "decline-interest":
            result = decline_interest(interest_id=require_arg(args, "interest_id", "--interest-id"), home=home)
        elif args.action == "list-incoming-interests":
            result = list_incoming_interests(project_id=args.id, home=home)
        elif args.action == "list-outgoing-interests":
            result = list_outgoing_interests(source_project_id=args.source_project_id, home=home)
        elif args.action == "start-conversation":
            result = start_conversation(
                project_id=require_arg(args, "id", "--id"),
                interest_id=require_arg(args, "interest_id", "--interest-id"),
                receiver_user_id=require_arg(args, "receiver_user_id", "--receiver-user-id"),
                source_project_id=args.source_project_id,
                home=home,
            )
        elif args.action == "send-message":
            result = send_message(
                conversation_id=require_arg(args, "conversation_id", "--conversation-id"),
                message=require_arg(args, "message", "--message"),
                agent_name=args.agent_name,
                home=home,
            )
        elif args.action == "list-conversations":
            result = list_conversations(project_id=args.id, home=home)
        elif args.action == "list-messages":
            result = list_messages(
                conversation_id=require_arg(args, "conversation_id", "--conversation-id"),
                home=home,
            )
        elif args.action == "update-conversation":
            result = update_conversation(
                conversation_id=require_arg(args, "conversation_id", "--conversation-id"),
                status=args.status,
                summary_for_owner=args.summary_for_owner,
                recommended_next_step=args.recommended_next_step,
                last_agent_decision=args.last_agent_decision,
                home=home,
            )
        elif args.action == "check-inbox":
            result = check_inbox(home=home)
        elif args.action == "check-message-compliance":
            result = check_message_compliance_action(
                message=require_arg(args, "message", "--message"),
                home=home,
            )
        elif args.action == "get-patrol-brief":
            result = get_patrol_brief(home=home)
        elif args.action == "list-market-page":
            result = list_market_page(
                project_id=require_arg(args, "id", "--id"),
                cursor=args.cursor,
                limit=args.limit,
                max_scan=args.max_scan,
                home=home,
            )
        elif args.action == "list-project-conversations":
            result = list_project_conversations(project_id=require_arg(args, "id", "--id"), home=home)
        elif args.action == "list-conversation-messages":
            result = list_conversation_messages(
                conversation_id=require_arg(args, "conversation_id", "--conversation-id"),
                since_id=args.since_id,
                home=home,
            )
        elif args.action == "apply-market-decision":
            result = apply_market_decision(
                source_project_id=require_arg(args, "source_project_id", "--source-project-id"),
                target_project_id=require_arg(args, "id", "--id"),
                decision=require_arg(args, "decision", "--decision"),
                confidence=args.confidence,
                reason=args.reason,
                opening_message=args.message,
                home=home,
            )
        elif args.action == "apply-conversation-decision":
            result = apply_conversation_decision(
                source_project_id=require_arg(args, "source_project_id", "--source-project-id"),
                conversation_id=require_arg(args, "conversation_id", "--conversation-id"),
                decision=require_arg(args, "decision", "--decision"),
                confidence=args.confidence,
                reason=args.reason,
                reply_text=args.message,
                summary_for_owner=args.summary_for_owner,
                recommended_next_step=args.recommended_next_step,
                home=home,
            )
        elif args.action == "resolve-pending-action":
            result = resolve_pending_action(
                action_token=require_arg(args, "id", "--id"),
                decision=require_arg(args, "decision", "--decision"),
                override_text=args.override_text,
                home=home,
            )
        elif args.action == "get-bootstrap-plan":
            result = get_bootstrap_plan(home=home)
        else:
            result = handle_incoming_interests(home=home)
    except (InstallError, AgentGatewayError, AgentGatewayTransportError) as exc:
        payload = exc.to_dict() if hasattr(exc, "to_dict") else {"error": exc.__class__.__name__, "message": str(exc)}
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        raise SystemExit(1) from exc

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
