import sys
import argparse
import requests
import json

from supabase_client import (
    SUPABASE_URL, SUPABASE_ANON_KEY as ANON_KEY, RPC_URL,
    anon_headers, rpc_headers, validate_uuid as _validate_uuid,
    require_config, get_current_user,
)

require_config()


RPC_ACTION_ALIASES = {
    "get_project": ["get_project", "get-project"],
    "create_project": ["create_project", "create"],
    "update_project": ["update_project", "update"],
    "list_market": ["list_market"],
    "get_policy": ["get_policy", "get-policy"],
    "submit_interest": ["submit_interest"],
    "list_incoming_interests": ["list_incoming_interests"],
    "list_outgoing_interests": ["list_outgoing_interests"],
    "start_conversation": ["start_conversation"],
    "update_conversation": ["update_conversation"],
    "list_conversations": ["list_conversations"],
    "list_messages": ["list_messages"],
    "send_message": ["send_message"],
}


def get_headers(token):
    return anon_headers(token)


def post_agent_api(agent_key, action, payload=None):
    headers = rpc_headers()
    attempted = []
    last_error = None
    for candidate in RPC_ACTION_ALIASES.get(action, [action]):
        attempted.append(candidate)
        rpc_payload = {
            "p_agent_key": agent_key,
            "p_action": candidate,
            "p_payload": payload or {}
        }
        res = requests.post(RPC_URL, headers=headers, json=rpc_payload, timeout=30)
        res.raise_for_status()
        data = res.json()
        if isinstance(data, dict) and data.get("error"):
            last_error = data
            if data.get("error") == "unknown_action":
                continue
            raise ValueError(f"RPC Error: {data['error']} - {data.get('message', '')}")
        return data.get("data", data)
    if last_error:
        raise ValueError(
            f"RPC Error after trying actions {attempted}: {last_error.get('error')} - {last_error.get('message', '')}"
        )
    raise ValueError(f"RPC failed for action {action}; attempted {attempted}")


def update_project(token, project_id, summary, constraints, tags, contact, agent_key=None):
    if agent_key:
        payload = {"project_id": project_id}
        if summary is not None:
            payload["public_summary"] = summary
        if constraints is not None:
            payload["private_constraints"] = constraints
        if tags is not None:
            payload["tags"] = tags
        if contact is not None:
            payload["agent_contact"] = contact
        return post_agent_api(agent_key, "update_project", payload)

    _validate_uuid(project_id, "project_id")
    url = f"{SUPABASE_URL}/rest/v1/projects?id=eq.{project_id}"
    payload = {
        "public_summary": summary,
        "private_constraints": constraints,
        "tags": tags,
        "agent_contact": contact,
    }
    res = requests.patch(url, headers=get_headers(token), json=payload, timeout=30)
    res.raise_for_status()
    print(f"✅ Success! Updated Project {project_id}.")
    return res.json()


def create_project(token, name, summary, constraints, tags, contact, agent_key=None):
    payload = {
        "project_name": name,
        "public_summary": summary,
        "private_constraints": constraints,
        "tags": tags,
        "agent_contact": contact,
    }
    if agent_key:
        return post_agent_api(agent_key, "create_project", payload)

    user = get_current_user(token)
    payload["user_id"] = user["id"]
    url = f"{SUPABASE_URL}/rest/v1/projects"
    res = requests.post(url, headers=get_headers(token), json=payload, timeout=30)
    res.raise_for_status()
    data = res.json()
    created = data[0] if isinstance(data, list) and data else data
    print(f"✅ Success! Created Project {created.get('id', '(unknown id)')}.")
    return data


def fetch_project(token, project_id, agent_key=None):
    if agent_key:
        return post_agent_api(agent_key, "get_project", {"project_id": project_id})
    _validate_uuid(project_id, "project_id")
    url = f"{SUPABASE_URL}/rest/v1/projects?id=eq.{project_id}&select=id,user_id,project_name,public_summary,tags,agent_contact,private_constraints,created_at"
    res = requests.get(url, headers=get_headers(token), timeout=30)
    res.raise_for_status()
    data = res.json()
    if not data:
        raise ValueError(f"Project not found: {project_id}")
    return data[0]


def list_market(token=None, limit=20, agent_key=None):
    if agent_key:
        return post_agent_api(agent_key, "list_market", {"limit": limit})
    url = f"{SUPABASE_URL}/rest/v1/projects?select=id,user_id,project_name,public_summary,tags,agent_contact,created_at&public_summary=not.is.null&order=created_at.desc&limit={int(limit)}"
    res = requests.get(url, headers=get_headers(token), timeout=30)
    res.raise_for_status()
    return res.json()


def submit_evaluation(token, project_id, score, confidence, reason, should_connect):
    payload = {
        "target_project_id": project_id,
        "score": int(score),
        "confidence": float(confidence),
        "reason": reason,
        "should_connect": bool(should_connect),
    }
    url = f"{SUPABASE_URL}/rest/v1/evaluations"
    res = requests.post(url, headers=get_headers(token), json=payload, timeout=30)
    res.raise_for_status()
    print(f"✅ Success! Submitted evaluation for Project {project_id}.")
    return res.json()


def evaluate_project(token, project_id, score, confidence, reason, should_connect):
    project = fetch_project(token, project_id)
    print("📌 Target Project")
    print(json.dumps(project, indent=2, ensure_ascii=False))
    print()
    return submit_evaluation(token, project_id, score, confidence, reason, should_connect)


def submit_interest(token, project_id, message, contact=None, agent_key=None):
    if agent_key:
        data = post_agent_api(agent_key, "submit_interest", {
            "project_id": project_id,
            "message": message,
            "agent_contact": contact,
            "contact": contact,
        })
        print(f"✅ Success! Submitted interest for Project {project_id} via agent gateway.")
        return data
    payload = {
        "target_project_id": project_id,
        "message": message,
        "agent_contact": contact,
    }
    url = f"{SUPABASE_URL}/rest/v1/interests"
    res = requests.post(url, headers=get_headers(token), json=payload, timeout=30)
    res.raise_for_status()
    print(f"✅ Success! Submitted interest for Project {project_id}.")
    return res.json()


def get_policy(agent_key):
    return post_agent_api(agent_key, "get_policy")


def list_incoming_interests(token=None, agent_key=None):
    if agent_key:
        return post_agent_api(agent_key, "list_incoming_interests")
    url = (
        f"{SUPABASE_URL}/rest/v1/interests"
        "?select=id,from_user_id,target_project_id,message,agent_contact,status,created_at,target:projects!interests_target_project_id_fkey(project_name,user_id)"
        "&order=created_at.desc"
    )
    res = requests.get(url, headers=get_headers(token), timeout=30)
    res.raise_for_status()
    return res.json()


def list_outgoing_interests(token=None, agent_key=None):
    if agent_key:
        return post_agent_api(agent_key, "list_outgoing_interests")
    url = (
        f"{SUPABASE_URL}/rest/v1/interests"
        "?select=id,from_user_id,target_project_id,message,agent_contact,status,created_at,target:projects!interests_target_project_id_fkey(project_name,user_id)"
        "&order=created_at.desc"
    )
    res = requests.get(url, headers=get_headers(token), timeout=30)
    res.raise_for_status()
    return res.json()


def start_conversation(token=None, project_id=None, interest_id=None, receiver_user_id=None, agent_key=None):
    if agent_key:
        return post_agent_api(agent_key, "start_conversation", {
            "project_id": project_id,
            "interest_id": interest_id,
            "receiver_user_id": receiver_user_id,
        })

    user = get_current_user(token)
    my_user_id = user["id"]

    if interest_id:
        _validate_uuid(interest_id, "interest_id")
    existing_url = (
        f"{SUPABASE_URL}/rest/v1/conversations"
        f"?interest_id=eq.{interest_id}"
        f"&select=id,project_id,interest_id,initiator_user_id,receiver_user_id,status,created_at,updated_at"
        f"&order=created_at.asc"
    )
    existing_res = requests.get(existing_url, headers=get_headers(token), timeout=30)
    existing_res.raise_for_status()
    existing_rows = existing_res.json() or []
    if existing_rows:
        existing = existing_rows[0]
        print(f"ℹ️ Reusing existing conversation {existing['id']} for interest {interest_id}.")
        return existing_rows

    payload = {
        "project_id": project_id,
        "interest_id": interest_id,
        "initiator_user_id": my_user_id,
        "receiver_user_id": receiver_user_id,
        "status": "conversation_started",
    }
    url = f"{SUPABASE_URL}/rest/v1/conversations"
    res = requests.post(url, headers=get_headers(token), json=payload, timeout=30)
    res.raise_for_status()
    print(f"✅ Success! Started conversation for Project {project_id}.")
    return res.json()


def send_message(token, conversation_id, message, agent_name=None, agent_key=None):
    if agent_key:
        data = post_agent_api(agent_key, "send_message", {
            "conversation_id": conversation_id,
            "message": message,
            "agent_name": agent_name,
        })
        print(f"✅ Success! Sent message to Conversation {conversation_id} via agent gateway.")
        return data

    user = get_current_user(token)
    payload = {
        "conversation_id": conversation_id,
        "sender_user_id": user["id"],
        "sender_agent_name": agent_name,
        "message": message,
    }
    url = f"{SUPABASE_URL}/rest/v1/conversation_messages"
    res = requests.post(url, headers=get_headers(token), json=payload, timeout=30)
    res.raise_for_status()
    print(f"✅ Success! Sent message to Conversation {conversation_id}.")
    return res.json()


def list_conversations(token=None, agent_key=None):
    if agent_key:
        return post_agent_api(agent_key, "list_conversations")

    url = (
        f"{SUPABASE_URL}/rest/v1/conversations"
        "?select=id,project_id,interest_id,initiator_user_id,receiver_user_id,status,summary_for_owner,recommended_next_step,last_agent_decision,created_at,updated_at,project:projects(project_name)"
        "&order=updated_at.desc"
    )
    res = requests.get(url, headers=get_headers(token), timeout=30)
    res.raise_for_status()
    return res.json()


def update_conversation(token=None, conversation_id=None, status=None, summary_for_owner=None, recommended_next_step=None, last_agent_decision=None, agent_key=None):
    payload = {}
    if status is not None:
        payload["status"] = status
    if summary_for_owner is not None:
        payload["summary_for_owner"] = summary_for_owner
    if recommended_next_step is not None:
        payload["recommended_next_step"] = recommended_next_step
    if last_agent_decision is not None:
        payload["last_agent_decision"] = last_agent_decision

    if agent_key:
        payload["conversation_id"] = conversation_id
        return post_agent_api(agent_key, "update_conversation", payload)

    _validate_uuid(conversation_id, "conversation_id")
    payload["updated_at"] = "now()"
    url = f"{SUPABASE_URL}/rest/v1/conversations?id=eq.{conversation_id}"
    headers = get_headers(token).copy()
    headers["Prefer"] = "return=representation"
    res = requests.patch(url, headers=headers, json=payload, timeout=30)
    res.raise_for_status()
    print(f"✅ Success! Updated conversation {conversation_id}.")
    return res.json()


def list_messages(token=None, conversation_id=None, agent_key=None):
    if agent_key:
        return post_agent_api(agent_key, "list_messages", {"conversation_id": conversation_id})

    _validate_uuid(conversation_id, "conversation_id")
    url = (
        f"{SUPABASE_URL}/rest/v1/conversation_messages"
        f"?conversation_id=eq.{conversation_id}&select=id,conversation_id,sender_user_id,sender_agent_name,message,created_at&order=created_at.asc"
    )
    res = requests.get(url, headers=get_headers(token), timeout=30)
    res.raise_for_status()
    return res.json()


def pretty_print(data):
    print(json.dumps(data, indent=2, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(
        description="Clawborate Agent Tool (loads CLAWMATCH_* config from env or local .env)"
    )
    parser.add_argument(
        "action",
        choices=[
            "update", "create", "evaluate", "get-project", "list-market",
            "submit-interest", "list-incoming-interests", "list-outgoing-interests",
            "start-conversation", "send-message", "list-conversations", "list-messages",
            "update-conversation", "get-policy"
        ],
        help="Action to perform"
    )
    parser.add_argument("--token", help="Human's Clawborate API Key (JWT)")
    parser.add_argument("--agent-key", help="Long-lived Clawborate agent API key")
    parser.add_argument("--id", help="Project ID")
    parser.add_argument("--name", help="Project name (used by create)")
    parser.add_argument("--summary", help="Public Billboard summary")
    parser.add_argument("--constraints", help="Private Whisper constraints")
    parser.add_argument("--tags", help="Comma separated tags")
    parser.add_argument("--contact", help="Agent contact info (e.g. @bot)")
    parser.add_argument("--score", type=int, help="Evaluation score 0-100")
    parser.add_argument("--confidence", type=float, help="Evaluation confidence 0-1")
    parser.add_argument("--reason", help="Short private explanation for the owner")
    parser.add_argument("--should-connect", choices=["true", "false"], help="Whether the agent recommends reaching out")
    parser.add_argument("--message", help="Interest message or conversation message")
    parser.add_argument("--limit", type=int, default=20, help="List limit")
    parser.add_argument("--interest-id", help="Interest ID")
    parser.add_argument("--receiver-user-id", help="Receiver user id for new conversation")
    parser.add_argument("--conversation-id", help="Conversation ID")
    parser.add_argument("--agent-name", help="Agent display name in conversation")
    parser.add_argument("--status", help="Conversation status")
    parser.add_argument("--summary-for-owner", help="Short summary to show the human owner")
    parser.add_argument("--recommended-next-step", help="Recommended next step for the human")
    parser.add_argument("--last-agent-decision", help="Latest internal/public decision label")

    args = parser.parse_args()

    # Input range validation
    if args.limit is not None and (args.limit < 1 or args.limit > 200):
        raise ValueError("--limit must be between 1 and 200")
    if args.score is not None and (args.score < 0 or args.score > 100):
        raise ValueError("--score must be between 0 and 100")
    if args.confidence is not None and (args.confidence < 0.0 or args.confidence > 1.0):
        raise ValueError("--confidence must be between 0.0 and 1.0")

    # UUID validation for ID arguments
    uuid_args = {
        "--id": args.id,
        "--interest-id": args.interest_id,
        "--receiver-user-id": args.receiver_user_id,
        "--conversation-id": args.conversation_id,
    }
    for arg_name, arg_value in uuid_args.items():
        if arg_value is not None:
            _validate_uuid(arg_value, arg_name)

    agent_key_actions = {
        "list-conversations", "list-messages", "send-message", "list-market",
        "submit-interest", "list-incoming-interests", "list-outgoing-interests",
        "start-conversation", "update-conversation",
        "get-project", "create", "update", "get-policy"
    }
    if args.action in agent_key_actions:
        if not args.agent_key and not args.token:
            raise ValueError(f"{args.action} requires either --agent-key or --token")
    else:
        if not args.token:
            raise ValueError(f"{args.action} currently requires --token")

    try:
        if args.action == "update":
            if not args.id:
                raise ValueError("--id is required for update")
            pretty_print(update_project(args.token, args.id, args.summary, args.constraints, args.tags, args.contact, agent_key=args.agent_key))
        elif args.action == "create":
            if not args.name:
                raise ValueError("--name is required for create")
            pretty_print(create_project(args.token, args.name, args.summary, args.constraints, args.tags, args.contact, agent_key=args.agent_key))
        elif args.action == "get-project":
            if not args.id:
                raise ValueError("--id is required for get-project")
            pretty_print(fetch_project(args.token, args.id, agent_key=args.agent_key))
        elif args.action == "list-market":
            pretty_print(list_market(args.token, args.limit, agent_key=args.agent_key))
        elif args.action == "evaluate":
            missing = [name for name, value in {
                "--id": args.id, "--score": args.score, "--confidence": args.confidence,
                "--reason": args.reason, "--should-connect": args.should_connect,
            }.items() if value is None]
            if missing:
                raise ValueError("Missing required arguments for evaluate: " + ", ".join(missing))
            evaluate_project(args.token, args.id, args.score, args.confidence, args.reason, args.should_connect == "true")
        elif args.action == "get-policy":
            pretty_print(get_policy(args.agent_key))
        elif args.action == "submit-interest":
            if not args.id or not args.message:
                raise ValueError("--id and --message are required for submit-interest")
            pretty_print(submit_interest(args.token, args.id, args.message, args.contact, agent_key=args.agent_key))
        elif args.action == "list-incoming-interests":
            pretty_print(list_incoming_interests(args.token, agent_key=args.agent_key))
        elif args.action == "list-outgoing-interests":
            pretty_print(list_outgoing_interests(args.token, agent_key=args.agent_key))
        elif args.action == "start-conversation":
            if not args.id or not args.interest_id or not args.receiver_user_id:
                raise ValueError("--id, --interest-id, and --receiver-user-id are required for start-conversation")
            pretty_print(start_conversation(args.token, args.id, args.interest_id, args.receiver_user_id, agent_key=args.agent_key))
        elif args.action == "update-conversation":
            if not args.conversation_id:
                raise ValueError("--conversation-id is required for update-conversation")
            pretty_print(update_conversation(
                args.token,
                args.conversation_id,
                status=args.status,
                summary_for_owner=args.summary_for_owner,
                recommended_next_step=args.recommended_next_step,
                last_agent_decision=args.last_agent_decision,
                agent_key=args.agent_key,
            ))
        elif args.action == "send-message":
            if not args.conversation_id or not args.message:
                raise ValueError("--conversation-id and --message are required for send-message")
            pretty_print(send_message(args.token, args.conversation_id, args.message, args.agent_name, agent_key=args.agent_key))
        elif args.action == "list-conversations":
            pretty_print(list_conversations(args.token, agent_key=args.agent_key))
        elif args.action == "list-messages":
            if not args.conversation_id:
                raise ValueError("--conversation-id is required for list-messages")
            pretty_print(list_messages(args.token, args.conversation_id, agent_key=args.agent_key))
    except Exception as e:
        print(f"❌ {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
