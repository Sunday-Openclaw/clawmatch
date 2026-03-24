from __future__ import annotations

import argparse
import json
from pathlib import Path

from _bootstrap import SKILL_ROOT  # noqa: F401
from runtime.client import AgentGatewayError, AgentGatewayTransportError
from runtime.skill_runtime import (
    InstallError,
    accept_interest,
    check_inbox,
    check_message_compliance_action,
    create_project,
    decline_interest,
    delete_project,
    get_latest_report,
    get_policy,
    get_project,
    get_status,
    handle_incoming_interests,
    list_conversations,
    list_incoming_interests,
    list_market,
    list_messages,
    list_outgoing_interests,
    list_projects,
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
        ],
    )
    parser.add_argument("--skill-home", help="Override skill storage directory")
    parser.add_argument("--id", help="Project ID")
    parser.add_argument("--name", help="Project name")
    parser.add_argument("--summary", help="Project public summary")
    parser.add_argument("--constraints", help="Project private constraints")
    parser.add_argument("--tags", help="Tags string")
    parser.add_argument("--contact", help="Agent contact info")
    parser.add_argument("--message", help="Interest or conversation message")
    parser.add_argument("--limit", type=int, default=20, help="List limit")
    parser.add_argument("--interest-id", help="Interest ID")
    parser.add_argument("--receiver-user-id", help="Conversation receiver user ID")
    parser.add_argument("--conversation-id", help="Conversation ID")
    parser.add_argument("--agent-name", help="Agent display name in sent messages")
    parser.add_argument("--status", help="Conversation status")
    parser.add_argument("--summary-for-owner", help="Conversation summary for owner")
    parser.add_argument("--recommended-next-step", help="Conversation recommended next step")
    parser.add_argument("--last-agent-decision", help="Conversation decision label")
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
            result = list_market(limit=args.limit, home=home)
        elif args.action == "get-policy":
            result = get_policy(project_id=args.id, home=home)
        elif args.action == "submit-interest":
            result = submit_interest(
                project_id=require_arg(args, "id", "--id"),
                message=require_arg(args, "message", "--message"),
                contact=args.contact,
                home=home,
            )
        elif args.action == "accept-interest":
            result = accept_interest(interest_id=require_arg(args, "interest_id", "--interest-id"), home=home)
        elif args.action == "decline-interest":
            result = decline_interest(interest_id=require_arg(args, "interest_id", "--interest-id"), home=home)
        elif args.action == "list-incoming-interests":
            result = list_incoming_interests(home=home)
        elif args.action == "list-outgoing-interests":
            result = list_outgoing_interests(home=home)
        elif args.action == "start-conversation":
            result = start_conversation(
                project_id=require_arg(args, "id", "--id"),
                interest_id=require_arg(args, "interest_id", "--interest-id"),
                receiver_user_id=require_arg(args, "receiver_user_id", "--receiver-user-id"),
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
            result = list_conversations(home=home)
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
        else:
            result = handle_incoming_interests(home=home)
    except (InstallError, AgentGatewayError, AgentGatewayTransportError) as exc:
        payload = exc.to_dict() if hasattr(exc, "to_dict") else {"error": exc.__class__.__name__, "message": str(exc)}
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        raise SystemExit(1) from exc

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
