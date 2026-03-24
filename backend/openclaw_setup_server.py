#!/usr/bin/env python3
"""
Clawborate OpenClaw bootstrap API server.

This service issues one-time setup tokens for authenticated Dashboard users,
exchanges those tokens for an install bundle, and records the completion
receipt from the local Windows bootstrap flow.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import secrets
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

from supabase_client import SUPABASE_URL, get_current_user, require_config, service_headers

HOST = os.environ.get("CLAWBORATE_SETUP_API_HOST", "127.0.0.1")
PORT = int(os.environ.get("CLAWBORATE_SETUP_API_PORT", "8791"))
ALLOWED_ORIGIN = os.environ.get("CLAWBORATE_SETUP_ALLOWED_ORIGIN", "*")
SETUP_TTL_MINUTES = int(os.environ.get("CLAWBORATE_SETUP_TTL_MINUTES", "10"))
PUBLIC_BASE_URL = os.environ.get("CLAWBORATE_SETUP_API_PUBLIC_BASE_URL", f"http://{HOST}:{PORT}")
PYTHON_RUNTIME_URL = os.environ.get("CLAWBORATE_BOOTSTRAP_PYTHON_RUNTIME_URL")
PYTHON_RUNTIME_SHA256 = os.environ.get("CLAWBORATE_BOOTSTRAP_PYTHON_RUNTIME_SHA256")
PYTHON_RUNTIME_FILE = os.environ.get("CLAWBORATE_BOOTSTRAP_PYTHON_RUNTIME_FILE")
SKILL_VERSION = os.environ.get("CLAWBORATE_SKILL_VERSION", "0.2.3")

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "skills" / "clawborate-skill"
BOOTSTRAP_SCRIPT_PATH = REPO_ROOT / "backend" / "bootstrap" / "clawborate-bootstrap.ps1"


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SkillBundleArtifact:
    data: bytes
    sha256: str
    filename: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.end_headers()
    handler.wfile.write(body)


def raw_response(handler: BaseHTTPRequestHandler, status: int, content_type: str, body: bytes, filename: str | None = None) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
    if filename:
        handler.send_header("Content-Disposition", f'attachment; filename="{filename}"')
    handler.end_headers()
    handler.wfile.write(body)


def sha256_hex(value: str | bytes) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def random_secret(prefix: str, *, bytes_len: int = 24) -> str:
    token = secrets.token_urlsafe(bytes_len).replace("-", "_")
    return f"{prefix}{token}"


def extract_bearer(handler: BaseHTTPRequestHandler) -> str:
    auth = handler.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise ApiError(401, "missing_bearer", "Missing Authorization: Bearer <token>")
    return auth[len("Bearer ") :].strip()


def skill_bundle_artifact() -> SkillBundleArtifact:
    buffer = io.BytesIO()
    archive_name = f"clawborate-skill-{SKILL_VERSION}.zip"
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(SKILL_DIR.rglob("*")):
            if not path.is_file():
                continue
            archive.write(path, arcname=str(path.relative_to(SKILL_DIR.parent)))
    data = buffer.getvalue()
    return SkillBundleArtifact(data=data, sha256=sha256_hex(data), filename=archive_name)


def python_runtime_descriptor(base_url: str) -> dict[str, Any]:
    if PYTHON_RUNTIME_FILE:
        runtime_path = Path(PYTHON_RUNTIME_FILE).expanduser()
        if runtime_path.exists():
            payload = runtime_path.read_bytes()
            return {
                "url": f"{base_url}/api/openclaw/setup/download/python-runtime.zip",
                "sha256": sha256_hex(payload),
            }
    return {
        "url": PYTHON_RUNTIME_URL,
        "sha256": PYTHON_RUNTIME_SHA256,
    }


def default_config_batch() -> list[dict[str, Any]]:
    return [
        {
            "path": "plugins.entries.clawborate.enabled",
            "value": False,
        }
    ]


def default_cron_spec() -> dict[str, Any]:
    return {
        "name": "clawborate-patrol",
        "agent": "main",
        "session": "isolated",
        "session_key": "agent:main:clawborate-patrol",
        "every": "5m",
        "message": "Read CLAWBORATE_PATROL.md and execute one Clawborate patrol tick. If nothing requires user-visible output, reply CLAWBORATE_IDLE.",
        "light_context": True,
        "best_effort_deliver": True,
    }


def initial_state_template() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "tick_id": None,
        "projects": {},
        "conversations": {},
        "pending_actions": {},
        "counters": {"I": 0, "M": 0, "R": 0, "T": 0},
        "incoming_interest_notifications": {},
        "bootstrap": {},
    }


def build_install_manifest(base_url: str, *, setup_session_id: str, agent_key: str | None = None) -> dict[str, Any]:
    bundle = skill_bundle_artifact()
    return {
        "setup_session_id": setup_session_id,
        "agent_key": agent_key,
        "skill_bundle": {
            "url": f"{base_url}/api/openclaw/setup/download/skill-bundle.zip",
            "sha256": bundle.sha256,
            "version": SKILL_VERSION,
        },
        "python_runtime": python_runtime_descriptor(base_url),
        "workspace_path": "~/.openclaw/workspace",
        "skill_home": "~/.openclaw/clawborate",
        "config_batch": default_config_batch(),
        "cron_spec": default_cron_spec(),
        "plugin_migration": {
            "disable_legacy_plugin": True,
            "preserve_files": True,
            "plugin_key": "clawborate",
        },
        "initial_state": initial_state_template(),
        "dry_run_probe": {
            "config_validate": True,
            "cron_run": True,
            "expected_files": [
                "~/.openclaw/clawborate/state.json",
                "~/.openclaw/clawborate/bootstrap-plan.json",
            ],
        },
    }


def build_bootstrap_command(*, setup_token: str, api_base: str) -> str:
    bootstrap_url = f"{api_base}/api/openclaw/setup/bootstrap.ps1"
    return (
        "powershell -ExecutionPolicy Bypass -NoProfile -Command "
        f"\"$tmp=Join-Path $env:TEMP 'clawborate-bootstrap.ps1'; "
        f"Invoke-WebRequest '{bootstrap_url}' -OutFile $tmp; "
        f"& $tmp -SetupToken '{setup_token}' -ApiBase '{api_base}'\""
    )


def _rest_url(table: str) -> str:
    require_config()
    return f"{SUPABASE_URL}/rest/v1/{table}"


def _service_get(table: str, *, params: dict[str, str]) -> list[dict[str, Any]]:
    response = requests.get(_rest_url(table), headers=service_headers(), params=params, timeout=30)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list):
        raise ApiError(502, "unexpected_upstream_response", f"Expected list from Supabase, got {type(data).__name__}")
    return data


def _service_insert(table: str, *, payload: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(_rest_url(table), headers=service_headers(), json=payload, timeout=30)
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list) or not rows:
        raise ApiError(502, "unexpected_upstream_response", f"Expected inserted row list from {table}")
    return dict(rows[0])


def _service_patch(table: str, *, params: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    response = requests.patch(_rest_url(table), headers=service_headers(), params=params, json=payload, timeout=30)
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list) or not rows:
        raise ApiError(502, "unexpected_upstream_response", f"Expected updated row list from {table}")
    return dict(rows[0])


def _load_setup_session_by_hash(token_hash: str) -> dict[str, Any] | None:
    rows = _service_get(
        "openclaw_setup_sessions",
        params={
            "select": "*",
            "token_hash": f"eq.{token_hash}",
            "limit": "1",
        },
    )
    return rows[0] if rows else None


def _load_setup_session_by_id(session_id: str) -> dict[str, Any] | None:
    rows = _service_get(
        "openclaw_setup_sessions",
        params={
            "select": "*",
            "id": f"eq.{session_id}",
            "limit": "1",
        },
    )
    return rows[0] if rows else None


def _load_setup_session_for_owner(session_id: str, owner_user_id: str) -> dict[str, Any] | None:
    rows = _service_get(
        "openclaw_setup_sessions",
        params={
            "select": "*",
            "id": f"eq.{session_id}",
            "owner_user_id": f"eq.{owner_user_id}",
            "limit": "1",
        },
    )
    return rows[0] if rows else None


def _mark_session_expired_if_needed(session: dict[str, Any]) -> dict[str, Any]:
    status = str(session.get("status") or "")
    expires_at = datetime.fromisoformat(str(session["expires_at"]).replace("Z", "+00:00"))
    if status in {"issued", "exchanged"} and expires_at < utc_now():
        return _service_patch(
            "openclaw_setup_sessions",
            params={"id": f"eq.{session['id']}"},
            payload={
                "status": "expired",
                "updated_at": iso_now(),
            },
        )
    return session


def _create_agent_api_key(owner_user_id: str) -> tuple[str, dict[str, Any]]:
    agent_key = random_secret("cm_sk_live_")
    row = _service_insert(
        "agent_api_keys",
        payload={
            "owner_user_id": owner_user_id,
            "project_id": None,
            "key_name": "openclaw-bootstrap",
            "key_prefix": agent_key[:14],
            "key_hash": sha256_hex(agent_key),
            "scopes": ["projects", "market", "interests", "conversations", "messages", "policy"],
            "is_active": True,
        },
    )
    return agent_key, row


def create_setup_session_for_user(user_token: str) -> dict[str, Any]:
    user = get_current_user(user_token)
    owner_user_id = str(user.get("id") or "")
    if not owner_user_id:
        raise ApiError(401, "invalid_user", "Could not resolve authenticated user")

    setup_token = random_secret("claw_setup_")
    expires_at = utc_now() + timedelta(minutes=SETUP_TTL_MINUTES)
    issued_manifest = build_install_manifest(PUBLIC_BASE_URL, setup_session_id="pending", agent_key=None)
    row = _service_insert(
        "openclaw_setup_sessions",
        payload={
            "owner_user_id": owner_user_id,
            "token_hash": sha256_hex(setup_token),
            "status": "issued",
            "expires_at": expires_at.isoformat(),
            "install_manifest": issued_manifest,
            "client_receipt": None,
        },
    )
    manifest = build_install_manifest(PUBLIC_BASE_URL, setup_session_id=str(row["id"]), agent_key=None)
    _service_patch(
        "openclaw_setup_sessions",
        params={"id": f"eq.{row['id']}"},
        payload={
            "install_manifest": manifest,
            "updated_at": iso_now(),
        },
    )
    return {
        "setup_session_id": row["id"],
        "setup_token": setup_token,
        "expires_at": expires_at.isoformat(),
        "bootstrap_command": build_bootstrap_command(setup_token=setup_token, api_base=PUBLIC_BASE_URL),
        "status_url": f"{PUBLIC_BASE_URL}/api/openclaw/setup/status?id={row['id']}",
    }


def exchange_setup_token(setup_token: str) -> dict[str, Any]:
    if not setup_token:
        raise ApiError(400, "missing_setup_token", "setup_token is required")
    session = _load_setup_session_by_hash(sha256_hex(setup_token))
    if not session:
        raise ApiError(404, "setup_token_not_found", "Unknown setup token")
    session = _mark_session_expired_if_needed(session)
    if session.get("status") == "expired":
        raise ApiError(410, "setup_token_expired", "Setup token has expired")
    if session.get("status") != "issued":
        raise ApiError(409, "setup_token_already_used", "Setup token has already been exchanged")

    agent_key, api_key_row = _create_agent_api_key(str(session["owner_user_id"]))
    manifest = build_install_manifest(PUBLIC_BASE_URL, setup_session_id=str(session["id"]), agent_key=agent_key)
    _service_patch(
        "openclaw_setup_sessions",
        params={"id": f"eq.{session['id']}"},
        payload={
            "status": "exchanged",
            "consumed_at": iso_now(),
            "agent_api_key_id": api_key_row["id"],
            "install_manifest": {**manifest, "agent_key": None},
            "updated_at": iso_now(),
        },
    )
    return manifest


def complete_setup_session(session_id: str, agent_key: str, receipt: dict[str, Any]) -> dict[str, Any]:
    session = _load_setup_session_by_id(session_id)
    if not session:
        raise ApiError(404, "setup_session_not_found", "Unknown setup session")
    expected_key_id = session.get("agent_api_key_id")
    if not expected_key_id:
        raise ApiError(409, "setup_session_not_exchanged", "Setup session has not been exchanged yet")

    key_rows = _service_get(
        "agent_api_keys",
        params={
            "select": "id,key_hash",
            "id": f"eq.{expected_key_id}",
            "limit": "1",
        },
    )
    if not key_rows:
        raise ApiError(404, "agent_key_not_found", "Linked agent key could not be found")
    if sha256_hex(agent_key) != key_rows[0].get("key_hash"):
        raise ApiError(403, "invalid_agent_key", "Completion agent_key does not match the exchanged setup session")

    dry_run_status = str((receipt or {}).get("dry_run_status") or "")
    status = "applied" if dry_run_status in {"ok", "passed", "success"} and not receipt.get("error") else "failed"
    updated = _service_patch(
        "openclaw_setup_sessions",
        params={"id": f"eq.{session_id}"},
        payload={
            "status": status,
            "client_receipt": receipt,
            "updated_at": iso_now(),
        },
    )
    return {
        "setup_session_id": updated["id"],
        "status": updated["status"],
    }


class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:
        json_response(self, 200, {"ok": True})

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/api/openclaw/setup/bootstrap.ps1":
                raw_response(
                    self,
                    200,
                    "text/plain; charset=utf-8",
                    BOOTSTRAP_SCRIPT_PATH.read_bytes(),
                    filename="clawborate-bootstrap.ps1",
                )
                return

            if parsed.path == "/api/openclaw/setup/download/skill-bundle.zip":
                bundle = skill_bundle_artifact()
                raw_response(self, 200, "application/zip", bundle.data, filename=bundle.filename)
                return

            if parsed.path == "/api/openclaw/setup/download/python-runtime.zip":
                if not PYTHON_RUNTIME_FILE:
                    raise ApiError(404, "python_runtime_not_configured", "No local Python runtime bundle is configured")
                runtime_path = Path(PYTHON_RUNTIME_FILE).expanduser()
                if not runtime_path.exists():
                    raise ApiError(404, "python_runtime_missing", "Configured Python runtime bundle does not exist")
                raw_response(self, 200, "application/zip", runtime_path.read_bytes(), filename=runtime_path.name)
                return

            if parsed.path == "/api/openclaw/setup/status":
                session_id = parse_qs(parsed.query).get("id", [""])[0]
                user_token = extract_bearer(self)
                user = get_current_user(user_token)
                row = _load_setup_session_for_owner(session_id, str(user.get("id") or ""))
                if not row:
                    raise ApiError(404, "setup_session_not_found", "Setup session was not found")
                row = _mark_session_expired_if_needed(row)
                json_response(
                    self,
                    200,
                    {
                        "ok": True,
                        "setup_session_id": row["id"],
                        "status": row["status"],
                        "expires_at": row["expires_at"],
                        "consumed_at": row.get("consumed_at"),
                        "client_receipt": row.get("client_receipt"),
                    },
                )
                return

            raise ApiError(404, "not_found", "Unknown endpoint")
        except ApiError as exc:
            json_response(self, exc.status, {"error": exc.code, "message": exc.message})
        except requests.RequestException as exc:
            json_response(self, 502, {"error": "upstream_error", "message": str(exc)})
        except Exception as exc:  # pragma: no cover - defensive guard
            json_response(self, 500, {"error": "server_error", "message": str(exc)})

    def do_POST(self) -> None:
        try:
            parsed = urlparse(self.path)
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}") if length else {}

            if parsed.path == "/api/openclaw/setup-token":
                user_token = extract_bearer(self)
                data = create_setup_session_for_user(user_token)
                json_response(self, 200, {"ok": True, **data})
                return

            if parsed.path == "/api/openclaw/setup/exchange":
                manifest = exchange_setup_token(str(payload.get("setup_token") or ""))
                json_response(self, 200, {"ok": True, "install_manifest": manifest})
                return

            if parsed.path == "/api/openclaw/setup/complete":
                data = complete_setup_session(
                    str(payload.get("setup_session_id") or ""),
                    str(payload.get("agent_key") or ""),
                    dict(payload.get("client_receipt") or {}),
                )
                json_response(self, 200, {"ok": True, **data})
                return

            raise ApiError(404, "not_found", "Unknown endpoint")
        except ApiError as exc:
            json_response(self, exc.status, {"error": exc.code, "message": exc.message})
        except requests.RequestException as exc:
            json_response(self, 502, {"error": "upstream_error", "message": str(exc)})
        except json.JSONDecodeError as exc:
            json_response(self, 400, {"error": "invalid_json", "message": str(exc)})
        except Exception as exc:  # pragma: no cover - defensive guard
            json_response(self, 500, {"error": "server_error", "message": str(exc)})


def main() -> None:
    server = HTTPServer((HOST, PORT), Handler)
    print(f"Clawborate OpenClaw setup API listening on {PUBLIC_BASE_URL}")
    server.serve_forever()


__all__ = [
    "ApiError",
    "Handler",
    "PUBLIC_BASE_URL",
    "build_bootstrap_command",
    "build_install_manifest",
    "complete_setup_session",
    "create_setup_session_for_user",
    "default_config_batch",
    "default_cron_spec",
    "exchange_setup_token",
    "initial_state_template",
    "python_runtime_descriptor",
    "sha256_hex",
    "skill_bundle_artifact",
]


if __name__ == "__main__":
    main()
