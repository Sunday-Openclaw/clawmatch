#!/usr/bin/env python3
"""
OpenClaw bridge for ClawMatch live evaluation.

This bridge receives a ClawMatch evaluation request, maps the current logged-in
ClawMatch user to an OpenClaw agent session, asks OpenClaw over its local HTTP
Chat Completions endpoint, and returns structured JSON back to ClawMatch.

Default behavior is safe for a single-user/self-hosted setup:
- if no explicit mapping exists, it falls back to the main OpenClaw session
- mapping can be overridden by email in `agent_identity_map.json`
"""

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import pathlib
import urllib.request
from typing import Any, Dict

HOST = os.environ.get("CLAWMATCH_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("CLAWMATCH_BRIDGE_PORT", "8788"))
ALLOWED_ORIGIN = os.environ.get("CLAWMATCH_ALLOWED_ORIGIN", "*")

OPENCLAW_GATEWAY_URL = os.environ.get("OPENCLAW_GATEWAY_HTTP_URL", "http://127.0.0.1:18789")
OPENCLAW_GATEWAY_TOKEN = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "10a10dd6f00713cec64faac71629048343133853381319b6")
OPENCLAW_AGENT_ID = os.environ.get("CLAWMATCH_OPENCLAW_AGENT_ID", "main")
DEFAULT_SESSION_KEY = os.environ.get("CLAWMATCH_DEFAULT_OPENCLAW_SESSION_KEY", "agent:main:main")
MAP_PATH = pathlib.Path(os.environ.get("CLAWMATCH_AGENT_MAP_PATH", str(pathlib.Path(__file__).with_name("agent_identity_map.json"))))


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


def load_identity_map() -> Dict[str, Any]:
    if MAP_PATH.exists():
        try:
            return json.loads(MAP_PATH.read_text())
        except Exception:
            return {}
    return {}


def resolve_session(current_user: Dict[str, Any]) -> Dict[str, str]:
    data = load_identity_map()
    by_email = data.get("by_email", {})
    email = (current_user.get("email") or "").strip().lower()

    mapped = by_email.get(email)
    if mapped:
        return {
            "agentId": mapped.get("agentId", OPENCLAW_AGENT_ID),
            "sessionKey": mapped.get("sessionKey", DEFAULT_SESSION_KEY),
            "source": f"agent_identity_map:{email}",
        }

    return {
        "agentId": OPENCLAW_AGENT_ID,
        "sessionKey": DEFAULT_SESSION_KEY,
        "source": "default",
    }


def build_agent_prompt(target_project: Dict[str, Any], current_user: Dict[str, Any]) -> str:
    return f"""
You are evaluating a ClawMatch market listing for your human.

Use your real memory and knowledge of your owner. Do NOT require an existing project folder.
The question is: would this listing represent a promising potential collaborator or opportunity for your human?

Current logged-in ClawMatch user:
- id: {current_user.get('id')}
- email: {current_user.get('email')}

Target project:
- id: {target_project.get('id')}
- name: {target_project.get('project_name')}
- summary: {target_project.get('public_summary')}
- tags: {target_project.get('tags')}
- agent_contact: {target_project.get('agent_contact')}

Return ONLY strict JSON with this schema:
{{
  "score": <0-100 integer>,
  "confidence": <0-1 float>,
  "reason": "short explanation referencing the owner's preferences/goals/constraints",
  "best_project_name": null,
  "should_connect": <true|false>
}}
""".strip()


def call_openclaw(prompt: str, agent_id: str, session_key: str) -> Dict[str, Any]:
    req_body = {
        "model": f"openclaw:{agent_id}",
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "temperature": 0.2,
    }

    req = urllib.request.Request(
        f"{OPENCLAW_GATEWAY_URL}/v1/chat/completions",
        data=json.dumps(req_body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {OPENCLAW_GATEWAY_TOKEN}",
            "Content-Type": "application/json",
            "x-openclaw-agent-id": agent_id,
            "x-openclaw-session-key": session_key,
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def extract_text(chat_response: Dict[str, Any]) -> str:
    choices = chat_response.get("choices") or []
    if not choices:
        raise ValueError("OpenClaw response had no choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "\n".join(parts).strip()
    raise ValueError("OpenClaw response content was not text")


def parse_agent_json(text: str) -> Dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"Could not find JSON object in model output: {text[:400]}")
    return json.loads(cleaned[start:end+1])


class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        json_response(self, 200, {"ok": True})

    def do_POST(self):
        if self.path != "/agent-evaluate":
            json_response(self, 404, {"error": "not_found"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            json_response(self, 400, {"error": "invalid_json"})
            return

        target_project = payload.get("targetProject") or {}
        current_user = payload.get("currentUser") or {}
        if not target_project:
            json_response(self, 400, {"error": "missing_target_project"})
            return

        try:
            route = resolve_session(current_user)
            prompt = build_agent_prompt(target_project, current_user)
            raw = call_openclaw(prompt, route["agentId"], route["sessionKey"])
            text = extract_text(raw)
            result = parse_agent_json(text)
            result["route"] = route
            json_response(self, 200, result)
        except Exception as e:
            json_response(self, 502, {
                "error": "openclaw_bridge_failed",
                "message": str(e),
            })


def main() -> None:
    server = HTTPServer((HOST, PORT), Handler)
    print(f"OpenClaw eval bridge listening on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
