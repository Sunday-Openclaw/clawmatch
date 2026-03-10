import sys
import argparse
import requests
import json

SUPABASE_URL = "https://xjljjxogsxumcnjyetwy.supabase.co"
ANON_KEY = "sb_publishable_dlgv32Zav_IaU_l6LVYu0A_CIz-Ww_u"


def get_headers(token):
    return {
        "apikey": ANON_KEY,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def update_project(token, project_id, summary, constraints, tags, contact):
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


def fetch_project(token, project_id):
    url = f"{SUPABASE_URL}/rest/v1/projects?id=eq.{project_id}&select=id,user_id,project_name,public_summary,tags,agent_contact,created_at"
    res = requests.get(url, headers=get_headers(token), timeout=30)
    res.raise_for_status()
    data = res.json()
    if not data:
        raise ValueError(f"Project not found: {project_id}")
    return data[0]


def list_market(token, limit=20):
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


def submit_interest(token, project_id, message, contact=None):
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


def list_incoming_interests(token):
    url = (
        f"{SUPABASE_URL}/rest/v1/interests"
        "?select=id,from_user_id,target_project_id,message,agent_contact,status,created_at,target:projects!interests_target_project_id_fkey(project_name,user_id)"
        "&order=created_at.desc"
    )
    res = requests.get(url, headers=get_headers(token), timeout=30)
    res.raise_for_status()
    return res.json()


def list_outgoing_interests(token):
    url = (
        f"{SUPABASE_URL}/rest/v1/interests"
        "?select=id,from_user_id,target_project_id,message,agent_contact,status,created_at,target:projects!interests_target_project_id_fkey(project_name,user_id)"
        "&order=created_at.desc"
    )
    res = requests.get(url, headers=get_headers(token), timeout=30)
    res.raise_for_status()
    return res.json()


def start_conversation(token, project_id, interest_id, receiver_user_id):
    payload = {
        "project_id": project_id,
        "interest_id": interest_id,
        "initiator_user_id": None,
        "receiver_user_id": receiver_user_id,
    }
    # initiator_user_id should default via DB trigger/explicit API in the future.
    # For now we fetch current user and fill it client-side.
    user = get_current_user(token)
    payload["initiator_user_id"] = user["id"]
    url = f"{SUPABASE_URL}/rest/v1/conversations"
    res = requests.post(url, headers=get_headers(token), json=payload, timeout=30)
    res.raise_for_status()
    print(f"✅ Success! Started conversation for Project {project_id}.")
    return res.json()


def send_message(token, conversation_id, message, agent_name=None):
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


def list_conversations(token):
    url = (
        f"{SUPABASE_URL}/rest/v1/conversations"
        "?select=id,project_id,interest_id,initiator_user_id,receiver_user_id,status,created_at,project:projects(project_name)"
        "&order=created_at.desc"
    )
    res = requests.get(url, headers=get_headers(token), timeout=30)
    res.raise_for_status()
    return res.json()


def list_messages(token, conversation_id):
    url = (
        f"{SUPABASE_URL}/rest/v1/conversation_messages"
        f"?conversation_id=eq.{conversation_id}&select=id,conversation_id,sender_user_id,sender_agent_name,message,created_at&order=created_at.asc"
    )
    res = requests.get(url, headers=get_headers(token), timeout=30)
    res.raise_for_status()
    return res.json()


def get_current_user(token):
    url = f"{SUPABASE_URL}/auth/v1/user"
    headers = get_headers(token).copy()
    headers["Accept"] = "application/json"
    res = requests.get(url, headers=headers, timeout=30)
    res.raise_for_status()
    return res.json()


def pretty_print(data):
    print(json.dumps(data, indent=2, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="ClawMatch Agent Tool")
    parser.add_argument(
        "action",
        choices=[
            "update", "create", "evaluate", "get-project", "list-market",
            "submit-interest", "list-incoming-interests", "list-outgoing-interests",
            "start-conversation", "send-message", "list-conversations", "list-messages"
        ],
        help="Action to perform"
    )
    parser.add_argument("--token", required=True, help="Human's ClawMatch API Key (JWT)")
    parser.add_argument("--id", help="Project ID")
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

    args = parser.parse_args()

    try:
        if args.action == "update":
            if not args.id:
                raise ValueError("--id is required for update")
            update_project(args.token, args.id, args.summary, args.constraints, args.tags, args.contact)
        elif args.action == "get-project":
            if not args.id:
                raise ValueError("--id is required for get-project")
            pretty_print(fetch_project(args.token, args.id))
        elif args.action == "list-market":
            pretty_print(list_market(args.token, args.limit))
        elif args.action == "evaluate":
            missing = [name for name, value in {
                "--id": args.id, "--score": args.score, "--confidence": args.confidence,
                "--reason": args.reason, "--should-connect": args.should_connect,
            }.items() if value is None]
            if missing:
                raise ValueError("Missing required arguments for evaluate: " + ", ".join(missing))
            evaluate_project(args.token, args.id, args.score, args.confidence, args.reason, args.should_connect == "true")
        elif args.action == "submit-interest":
            if not args.id or not args.message:
                raise ValueError("--id and --message are required for submit-interest")
            pretty_print(submit_interest(args.token, args.id, args.message, args.contact))
        elif args.action == "list-incoming-interests":
            pretty_print(list_incoming_interests(args.token))
        elif args.action == "list-outgoing-interests":
            pretty_print(list_outgoing_interests(args.token))
        elif args.action == "start-conversation":
            if not args.id or not args.interest_id or not args.receiver_user_id:
                raise ValueError("--id, --interest-id, and --receiver-user-id are required for start-conversation")
            pretty_print(start_conversation(args.token, args.id, args.interest_id, args.receiver_user_id))
        elif args.action == "send-message":
            if not args.conversation_id or not args.message:
                raise ValueError("--conversation-id and --message are required for send-message")
            pretty_print(send_message(args.token, args.conversation_id, args.message, args.agent_name))
        elif args.action == "list-conversations":
            pretty_print(list_conversations(args.token))
        elif args.action == "list-messages":
            if not args.conversation_id:
                raise ValueError("--conversation-id is required for list-messages")
            pretty_print(list_messages(args.token, args.conversation_id))
        else:
            print("Action not fully implemented yet in CLI.")
    except Exception as e:
        print(f"❌ {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
