#!/usr/bin/env python3
"""
Minimal live-agent evaluation API for ClawMatch.

This is the backend piece the static website needs in order to do real-time,
agent-mediated matching. The intended flow is:

1. Browser sends POST /evaluate with:
   - target project id
   - user's Supabase JWT (the ClawMatch session token)
2. This server fetches the target market project.
3. The server calls a LIVE agent adapter (OpenClaw, webhook, or custom bridge)
   to ask: "Given what you know about your owner, would this be a promising collaborator/opportunity?"
4. The server returns structured JSON back to the website.

IMPORTANT:
- This file is a scaffold, not a finished production service.
- The default adapter is a safe placeholder that explains what still needs to be wired.
- This backend avoids storing large profile snapshots in Supabase.
"""

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import urllib.request
import urllib.error
from typing import Any, Dict

SUPABASE_URL = os.environ.get("CLAWMATCH_SUPABASE_URL", "https://xjljjxogsxumcnjyetwy.supabase.co")
SUPABASE_ANON_KEY = os.environ.get("CLAWMATCH_SUPABASE_ANON_KEY", "sb_publishable_dlgv32Zav_IaU_l6LVYu0A_CIz-Ww_u")
HOST = os.environ.get("CLAWMATCH_EVAL_HOST", "127.0.0.1")
PORT = int(os.environ.get("CLAWMATCH_EVAL_PORT", "8787"))
ALLOWED_ORIGIN = os.environ.get("CLAWMATCH_ALLOWED_ORIGIN", "*")
OPENCLAW_AGENT_EVAL_URL = os.environ.get("OPENCLAW_AGENT_EVAL_URL", "").strip()


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


def supabase_get(path: str, user_jwt: str) -> Any:
    req = urllib.request.Request(
        f"{SUPABASE_URL}{path}",
        headers={
            "apikey": SUPABASE_ANON_KEY,
            "Authorization": f"Bearer {user_jwt}",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_target_project(project_id: str, user_jwt: str) -> Dict[str, Any]:
    rows = supabase_get(
        f"/rest/v1/projects?id=eq.{project_id}&select=id,user_id,project_name,public_summary,private_constraints,tags,agent_contact,created_at",
        user_jwt,
    )
    if not rows:
        raise ValueError("Target project not found")
    return rows[0]


def fetch_current_user(user_jwt: str) -> Dict[str, Any]:
    req = urllib.request.Request(
        f"{SUPABASE_URL}/auth/v1/user",
        headers={
            "apikey": SUPABASE_ANON_KEY,
            "Authorization": f"Bearer {user_jwt}",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def call_live_agent_adapter(target_project: Dict[str, Any], user_jwt: str, current_user: Dict[str, Any]) -> Dict[str, Any]:
    """
    Adapter contract for real live-agent evaluation.

    If OPENCLAW_AGENT_EVAL_URL is configured, this function POSTs a payload to that
    service and expects JSON like:
      {
        "score": 87,
        "confidence": 0.81,
        "reason": "...",
        "best_project_name": null,
        "should_connect": true
      }

    That upstream service is where you connect to OpenClaw / your live agent.
    """
    payload = {
        "kind": "clawmatch.live_evaluate",
        "targetProject": target_project,
        "userJwt": user_jwt,
        "currentUser": {
            "id": current_user.get("id"),
            "email": current_user.get("email"),
            "user_metadata": current_user.get("user_metadata", {}),
        },
        "instruction": (
            "Evaluate the target project using the live agent's memory about its owner. "
            "Do not require an existing user project folder. Judge whether this looks like a promising potential collaborator or opportunity. "
            "Return a percentage score, confidence, concise reason, optional best_project_name, and should_connect."
        ),
    }

    if OPENCLAW_AGENT_EVAL_URL:
        req = urllib.request.Request(
            OPENCLAW_AGENT_EVAL_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))

    return {
        "error": "live_agent_not_configured",
        "message": (
            "Live-agent adapter is not configured yet. Set OPENCLAW_AGENT_EVAL_URL "
            "to a service that can reach the user's agent/OpenClaw runtime."
        ),
    }


class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        json_response(self, 200, {"ok": True})

    def do_POST(self):
        if self.path != "/evaluate":
            json_response(self, 404, {"error": "not_found"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            json_response(self, 400, {"error": "invalid_json"})
            return

        project_id = (payload.get("projectId") or "").strip()
        user_jwt = (payload.get("userJwt") or "").strip()

        if not project_id or not user_jwt:
            json_response(self, 400, {"error": "missing_project_or_token"})
            return

        try:
            current_user = fetch_current_user(user_jwt)
            target_project = fetch_target_project(project_id, user_jwt)
            result = call_live_agent_adapter(target_project, user_jwt, current_user)
            if result.get("error"):
                json_response(self, 501, result)
                return
            json_response(self, 200, {
                "ok": True,
                "project": target_project,
                "evaluation": result,
            })
        except urllib.error.HTTPError as e:
            details = e.read().decode("utf-8", errors="ignore")
            json_response(self, 502, {"error": "supabase_http_error", "status": e.code, "details": details})
        except ValueError as e:
            json_response(self, 404, {"error": "not_found", "message": str(e)})
        except Exception as e:
            json_response(self, 500, {"error": "server_error", "message": str(e)})


def main() -> None:
    server = HTTPServer((HOST, PORT), Handler)
    print(f"ClawMatch live-agent evaluation API listening on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
