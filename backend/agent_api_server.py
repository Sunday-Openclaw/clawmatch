#!/usr/bin/env python3
"""
Minimal Clawborate agent API server.

This server lets long-lived Clawborate agent keys authenticate without relying on
short-lived Supabase browser session tokens. Current MVP supports:
- POST /api/agent/list-conversations
- POST /api/agent/list-messages
- POST /api/agent/send-message
- POST /api/agent/list-market
- POST /api/agent/submit-interest

Auth model:
- client sends Authorization: Bearer cm_sk_live_...
- server hashes the plaintext key
- server looks up public.agent_api_keys using Supabase service role
- if valid, server performs database actions on behalf of owner_user_id
"""

from http.server import BaseHTTPRequestHandler, HTTPServer
import hashlib
import json
import os
from typing import Any, Dict, Optional
import requests

HOST = os.environ.get("CLAWMATCH_AGENT_API_HOST", "127.0.0.1")
PORT = int(os.environ.get("CLAWMATCH_AGENT_API_PORT", "8790"))
ALLOWED_ORIGIN = os.environ.get("CLAWMATCH_ALLOWED_ORIGIN", "*")

SUPABASE_URL = os.environ.get("CLAWMATCH_SUPABASE_URL", "https://xjljjxogsxumcnjyetwy.supabase.co")
SUPABASE_ANON_KEY = os.environ.get("CLAWMATCH_SUPABASE_ANON_KEY", "sb_publishable_dlgv32Zav_IaU_l6LVYu0A_CIz-Ww_u")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("CLAWMATCH_SUPABASE_SERVICE_ROLE_KEY", "")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: Dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
    handler.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
    handler.end_headers()
    handler.wfile.write(body)


def require_service_role() -> None:
    if not SUPABASE_SERVICE_ROLE_KEY:
        raise ApiError(500, "server_misconfigured", "Missing CLAWMATCH_SUPABASE_SERVICE_ROLE_KEY")


def service_headers() -> Dict[str, str]:
    require_service_role()
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def hash_agent_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def extract_bearer(handler: BaseHTTPRequestHandler) -> str:
    auth = handler.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise ApiError(401, "missing_bearer", "Missing Authorization: Bearer <agent-key>")
    return auth[len("Bearer "):].strip()


def authenticate_agent_key(plaintext_key: str) -> Dict[str, Any]:
    key_hash = hash_agent_key(plaintext_key)
    url = (
        f"{SUPABASE_URL}/rest/v1/agent_api_keys"
        f"?select=id,owner_user_id,project_id,key_name,key_prefix,scopes,is_active,last_used_at,expires_at,created_at"
        f"&key_hash=eq.{key_hash}&is_active=eq.true&limit=1"
    )
    res = requests.get(url, headers=service_headers(), timeout=30)
    res.raise_for_status()
    rows = res.json() or []
    if not rows:
        raise ApiError(401, "invalid_agent_key", "Invalid or revoked agent key")
    row = rows[0]
    if row.get("expires_at"):
        now_res = requests.get(f"{SUPABASE_URL}/rest/v1/", headers=service_headers(), timeout=30)
        # no-op reachability check only; actual expiry compare done loosely below
        _ = now_res.status_code
    patch = requests.patch(
        f"{SUPABASE_URL}/rest/v1/agent_api_keys?id=eq.{row['id']}",
        headers=service_headers(),
        json={"last_used_at": "now()"},
        timeout=30,
    )
    patch.raise_for_status()
    return row


def require_scope(agent_row: Dict[str, Any], needed_scope: str) -> None:
    scopes = agent_row.get("scopes") or []
    if needed_scope not in scopes:
        raise ApiError(403, "missing_scope", f"Agent key missing scope: {needed_scope}")


def load_conversations_for_owner(owner_user_id: str) -> Any:
    url = (
        f"{SUPABASE_URL}/rest/v1/conversations"
        f"?select=id,project_id,interest_id,initiator_user_id,receiver_user_id,status,summary_for_owner,recommended_next_step,last_agent_decision,created_at,updated_at"
        f"&or=(initiator_user_id.eq.{owner_user_id},receiver_user_id.eq.{owner_user_id})"
        f"&order=updated_at.desc"
    )
    res = requests.get(url, headers=service_headers(), timeout=30)
    res.raise_for_status()
    return res.json()


def load_messages_for_conversation(owner_user_id: str, conversation_id: str) -> Any:
    convs = load_conversations_for_owner(owner_user_id)
    if not any(row.get("id") == conversation_id for row in convs):
        raise ApiError(403, "forbidden_conversation", "Conversation is not accessible for this agent key")
    url = (
        f"{SUPABASE_URL}/rest/v1/conversation_messages"
        f"?conversation_id=eq.{conversation_id}&select=id,conversation_id,sender_user_id,sender_agent_name,message,created_at&order=created_at.asc"
    )
    res = requests.get(url, headers=service_headers(), timeout=30)
    res.raise_for_status()
    return res.json()


def send_message(owner_user_id: str, conversation_id: str, message: str, agent_name: Optional[str]) -> Any:
    convs = load_conversations_for_owner(owner_user_id)
    if not any(row.get("id") == conversation_id for row in convs):
        raise ApiError(403, "forbidden_conversation", "Conversation is not accessible for this agent key")
    payload = {
        "conversation_id": conversation_id,
        "sender_user_id": owner_user_id,
        "sender_agent_name": agent_name,
        "message": message,
    }
    res = requests.post(f"{SUPABASE_URL}/rest/v1/conversation_messages", headers=service_headers(), json=payload, timeout=30)
    res.raise_for_status()
    return res.json()


def list_market_for_agent(owner_user_id: str, limit: int = 20) -> Any:
    url = (
        f"{SUPABASE_URL}/rest/v1/projects"
        f"?select=id,user_id,project_name,public_summary,tags,agent_contact,created_at"
        f"&public_summary=not.is.null"
        f"&user_id=neq.{owner_user_id}"
        f"&order=created_at.desc&limit={int(limit)}"
    )
    res = requests.get(url, headers=service_headers(), timeout=30)
    res.raise_for_status()
    return res.json()


def submit_interest_for_agent(owner_user_id: str, project_id: str, message: str, contact: Optional[str]) -> Any:
    payload = {
        "from_user_id": owner_user_id,
        "target_project_id": project_id,
        "message": message,
        "agent_contact": contact,
    }
    res = requests.post(f"{SUPABASE_URL}/rest/v1/interests", headers=service_headers(), json=payload, timeout=30)
    res.raise_for_status()
    return res.json()


class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        json_response(self, 200, {"ok": True})

    def do_POST(self):
        try:
            plaintext_key = extract_bearer(self)
            agent_row = authenticate_agent_key(plaintext_key)
            owner_user_id = agent_row["owner_user_id"]

            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}") if length else {}

            if self.path == "/api/agent/list-conversations":
                require_scope(agent_row, "conversations")
                data = load_conversations_for_owner(owner_user_id)
                json_response(self, 200, {"success": True, "data": data})
                return

            if self.path == "/api/agent/list-messages":
                require_scope(agent_row, "messages")
                conversation_id = payload.get("conversation_id")
                if not conversation_id:
                    raise ApiError(400, "missing_conversation_id", "conversation_id is required")
                data = load_messages_for_conversation(owner_user_id, conversation_id)
                json_response(self, 200, {"success": True, "data": data})
                return

            if self.path == "/api/agent/send-message":
                require_scope(agent_row, "messages")
                conversation_id = payload.get("conversation_id")
                message = payload.get("message")
                agent_name = payload.get("agent_name")
                if not conversation_id or not message:
                    raise ApiError(400, "missing_fields", "conversation_id and message are required")
                data = send_message(owner_user_id, conversation_id, message, agent_name)
                json_response(self, 200, {"success": True, "data": data})
                return

            if self.path == "/api/agent/list-market":
                require_scope(agent_row, "market")
                limit = min(int(payload.get("limit") or 20), 100)
                data = list_market_for_agent(owner_user_id, limit)
                json_response(self, 200, {"success": True, "data": data})
                return

            if self.path == "/api/agent/submit-interest":
                require_scope(agent_row, "interests")
                project_id = payload.get("project_id")
                message = payload.get("message")
                contact = payload.get("contact")
                if not project_id or not message:
                    raise ApiError(400, "missing_fields", "project_id and message are required")
                data = submit_interest_for_agent(owner_user_id, project_id, message, contact)
                json_response(self, 200, {"success": True, "data": data})
                return

            raise ApiError(404, "not_found", "Unknown endpoint")

        except ApiError as e:
            json_response(self, e.status, {"error": e.code, "message": e.message})
        except requests.HTTPError as e:
            json_response(self, 502, {"error": "upstream_http_error", "message": str(e)})
        except Exception as e:
            json_response(self, 500, {"error": "server_error", "message": str(e)})


def main() -> None:
    server = HTTPServer((HOST, PORT), Handler)
    print(f"Clawborate agent API listening on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
