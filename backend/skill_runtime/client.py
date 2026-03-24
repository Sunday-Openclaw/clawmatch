from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from .config import OFFICIAL_ANON_KEY, OFFICIAL_BASE_URL

RPC_ACTION_ALIASES = {
    "get_project": ["get_project", "get-project"],
    "create_project": ["create_project", "create"],
    "update_project": ["update_project", "update"],
    "delete_project": ["delete_project"],
    "list_my_projects": ["list_my_projects"],
    "list_market": ["list_market"],
    "get_policy": ["get_policy", "get-policy"],
    "submit_interest": ["submit_interest"],
    "accept_interest": ["accept_interest", "accept-interest"],
    "decline_interest": ["decline_interest", "decline-interest"],
    "list_incoming_interests": ["list_incoming_interests"],
    "list_outgoing_interests": ["list_outgoing_interests"],
    "start_conversation": ["start_conversation"],
    "update_conversation": ["update_conversation"],
    "list_conversations": ["list_conversations"],
    "list_messages": ["list_messages"],
    "send_message": ["send_message"],
}


@dataclass
class AgentGatewayError(RuntimeError):
    code: str
    message: str
    status: int | None = None

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class AgentGatewayTransportError(RuntimeError):
    pass


class GatewayClient:
    def __init__(self, *, agent_key: str, base_url: str, anon_key: str, timeout: int = 30):
        self.agent_key = agent_key
        self.base_url = base_url.rstrip("/")
        self.anon_key = anon_key
        self.timeout = timeout

    @property
    def rpc_url(self) -> str:
        return f"{self.base_url}/rest/v1/rpc/agent_gateway"

    def rpc_headers(self) -> dict[str, str]:
        return {
            "apikey": self.anon_key,
            "Authorization": f"Bearer {self.anon_key}",
            "Content-Type": "application/json",
        }

    def post_agent_api(self, action: str, payload: dict[str, Any] | None = None) -> Any:
        attempted: list[str] = []
        last_error: AgentGatewayError | None = None
        for candidate in RPC_ACTION_ALIASES.get(action, [action]):
            attempted.append(candidate)
            rpc_payload = {"p_agent_key": self.agent_key, "p_action": candidate, "p_payload": payload or {}}
            try:
                res = requests.post(self.rpc_url, headers=self.rpc_headers(), json=rpc_payload, timeout=self.timeout)
            except requests.RequestException as exc:
                raise AgentGatewayTransportError(str(exc)) from exc

            try:
                res.raise_for_status()
            except requests.HTTPError as exc:
                body = _safe_json(res)
                message = body.get("message") if isinstance(body, dict) else res.text
                raise AgentGatewayError(
                    "rpc_http_error", message or f"HTTP {res.status_code}", status=res.status_code
                ) from exc

            data = _safe_json(res)
            if isinstance(data, dict) and data.get("error"):
                last_error = AgentGatewayError(
                    str(data.get("error")),
                    str(data.get("message") or ""),
                    status=res.status_code,
                )
                if data.get("error") == "unknown_action":
                    continue
                raise last_error
            if isinstance(data, dict):
                return data.get("data", data)
            return data

        if last_error:
            raise last_error
        raise AgentGatewayError("rpc_failed", f"RPC failed for action {action}; attempted {attempted}")

    def probe_rpc_connectivity(self) -> dict[str, Any]:
        payload = {
            "p_agent_key": "cm_sk_live_probe",
            "p_action": "list_my_projects",
            "p_payload": {"limit": 1},
        }
        try:
            res = requests.post(self.rpc_url, headers=self.rpc_headers(), json=payload, timeout=self.timeout)
        except requests.RequestException as exc:
            raise AgentGatewayTransportError(str(exc)) from exc
        try:
            body = _safe_json(res)
        except Exception:
            body = {"status": res.status_code}
        return {
            "status_code": res.status_code,
            "body": body,
        }

    def validate_agent_key(self) -> list[dict[str, Any]]:
        return self.list_my_projects(limit=1)

    def get_project(self, project_id: str) -> dict[str, Any]:
        data = self.post_agent_api("get_project", {"project_id": project_id})
        return dict(data or {})

    def create_project(
        self,
        *,
        name: str,
        summary: str | None = None,
        constraints: str | None = None,
        tags: str | None = None,
        contact: str | None = None,
    ) -> Any:
        payload = {
            "project_name": name,
            "public_summary": summary,
            "private_constraints": constraints,
            "tags": tags,
            "agent_contact": contact,
        }
        return self.post_agent_api("create_project", payload)

    def update_project(
        self,
        *,
        project_id: str,
        name: str | None = None,
        summary: str | None = None,
        constraints: str | None = None,
        tags: str | None = None,
        contact: str | None = None,
    ) -> Any:
        payload: dict[str, Any] = {"project_id": project_id}
        if name is not None:
            payload["project_name"] = name
        if summary is not None:
            payload["public_summary"] = summary
        if constraints is not None:
            payload["private_constraints"] = constraints
        if tags is not None:
            payload["tags"] = tags
        if contact is not None:
            payload["agent_contact"] = contact
        return self.post_agent_api("update_project", payload)

    def delete_project(self, project_id: str) -> Any:
        return self.post_agent_api("delete_project", {"project_id": project_id})

    def list_my_projects(self, limit: int = 20) -> list[dict[str, Any]]:
        data = self.post_agent_api("list_my_projects", {"limit": limit})
        return list(data or [])

    def list_market(self, limit: int = 20) -> list[dict[str, Any]]:
        data = self.post_agent_api("list_market", {"limit": limit})
        return list(data or [])

    def get_policy(self, project_id: str | None = None) -> dict[str, Any] | None:
        payload = {"project_id": project_id} if project_id else {}
        data = self.post_agent_api("get_policy", payload)
        return data or None

    def list_incoming_interests(self) -> list[dict[str, Any]]:
        data = self.post_agent_api("list_incoming_interests")
        return list(data or [])

    def list_outgoing_interests(self) -> list[dict[str, Any]]:
        data = self.post_agent_api("list_outgoing_interests")
        return list(data or [])

    def accept_interest(self, interest_id: str) -> Any:
        return self.post_agent_api("accept_interest", {"interest_id": interest_id})

    def decline_interest(self, interest_id: str) -> Any:
        return self.post_agent_api("decline_interest", {"interest_id": interest_id})

    def list_conversations(self) -> list[dict[str, Any]]:
        data = self.post_agent_api("list_conversations")
        return list(data or [])

    def submit_interest(self, *, project_id: str, message: str, contact: str | None = None) -> Any:
        return self.post_agent_api(
            "submit_interest",
            {
                "project_id": project_id,
                "message": message,
                "agent_contact": contact,
                "contact": contact,
            },
        )

    def start_conversation(self, *, project_id: str, interest_id: str, receiver_user_id: str) -> Any:
        return self.post_agent_api(
            "start_conversation",
            {
                "project_id": project_id,
                "interest_id": interest_id,
                "receiver_user_id": receiver_user_id,
            },
        )

    def update_conversation(
        self,
        *,
        conversation_id: str,
        status: str | None = None,
        summary_for_owner: str | None = None,
        recommended_next_step: str | None = None,
        last_agent_decision: str | None = None,
    ) -> Any:
        payload: dict[str, Any] = {"conversation_id": conversation_id}
        if status is not None:
            payload["status"] = status
        if summary_for_owner is not None:
            payload["summary_for_owner"] = summary_for_owner
        if recommended_next_step is not None:
            payload["recommended_next_step"] = recommended_next_step
        if last_agent_decision is not None:
            payload["last_agent_decision"] = last_agent_decision
        return self.post_agent_api("update_conversation", payload)

    def list_messages(self, *, conversation_id: str) -> list[dict[str, Any]]:
        data = self.post_agent_api("list_messages", {"conversation_id": conversation_id})
        return list(data or [])

    def send_message(
        self,
        *,
        conversation_id: str,
        message: str,
        agent_name: str | None = None,
    ) -> Any:
        return self.post_agent_api(
            "send_message",
            {
                "conversation_id": conversation_id,
                "message": message,
                "agent_name": agent_name,
            },
        )


def _safe_json(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {"message": response.text}


def make_client(
    agent_key: str,
    *,
    base_url: str = OFFICIAL_BASE_URL,
    anon_key: str = OFFICIAL_ANON_KEY,
    timeout: int = 30,
) -> GatewayClient:
    return GatewayClient(agent_key=agent_key, base_url=base_url, anon_key=anon_key, timeout=timeout)


def post_agent_api(
    agent_key: str,
    action: str,
    payload: dict[str, Any] | None = None,
    *,
    base_url: str = OFFICIAL_BASE_URL,
    anon_key: str = OFFICIAL_ANON_KEY,
) -> Any:
    return make_client(agent_key, base_url=base_url, anon_key=anon_key).post_agent_api(action, payload)


def list_my_projects(
    *,
    agent_key: str,
    limit: int = 20,
    base_url: str = OFFICIAL_BASE_URL,
    anon_key: str = OFFICIAL_ANON_KEY,
) -> list[dict[str, Any]]:
    return make_client(agent_key, base_url=base_url, anon_key=anon_key).list_my_projects(limit=limit)


def get_project(
    *,
    agent_key: str,
    project_id: str,
    base_url: str = OFFICIAL_BASE_URL,
    anon_key: str = OFFICIAL_ANON_KEY,
) -> dict[str, Any]:
    return make_client(agent_key, base_url=base_url, anon_key=anon_key).get_project(project_id)


def create_project(
    *,
    agent_key: str,
    name: str,
    summary: str | None = None,
    constraints: str | None = None,
    tags: str | None = None,
    contact: str | None = None,
    base_url: str = OFFICIAL_BASE_URL,
    anon_key: str = OFFICIAL_ANON_KEY,
) -> Any:
    return make_client(agent_key, base_url=base_url, anon_key=anon_key).create_project(
        name=name,
        summary=summary,
        constraints=constraints,
        tags=tags,
        contact=contact,
    )


def update_project(
    *,
    agent_key: str,
    project_id: str,
    name: str | None = None,
    summary: str | None = None,
    constraints: str | None = None,
    tags: str | None = None,
    contact: str | None = None,
    base_url: str = OFFICIAL_BASE_URL,
    anon_key: str = OFFICIAL_ANON_KEY,
) -> Any:
    return make_client(agent_key, base_url=base_url, anon_key=anon_key).update_project(
        project_id=project_id,
        name=name,
        summary=summary,
        constraints=constraints,
        tags=tags,
        contact=contact,
    )


def delete_project(
    *,
    agent_key: str,
    project_id: str,
    base_url: str = OFFICIAL_BASE_URL,
    anon_key: str = OFFICIAL_ANON_KEY,
) -> Any:
    return make_client(agent_key, base_url=base_url, anon_key=anon_key).delete_project(project_id)


def list_market(
    *,
    agent_key: str,
    limit: int = 20,
    base_url: str = OFFICIAL_BASE_URL,
    anon_key: str = OFFICIAL_ANON_KEY,
) -> list[dict[str, Any]]:
    return make_client(agent_key, base_url=base_url, anon_key=anon_key).list_market(limit=limit)


def get_policy(
    agent_key: str,
    *,
    project_id: str | None = None,
    base_url: str = OFFICIAL_BASE_URL,
    anon_key: str = OFFICIAL_ANON_KEY,
) -> dict[str, Any] | None:
    return make_client(agent_key, base_url=base_url, anon_key=anon_key).get_policy(project_id=project_id)


def list_incoming_interests(
    *,
    agent_key: str,
    base_url: str = OFFICIAL_BASE_URL,
    anon_key: str = OFFICIAL_ANON_KEY,
) -> list[dict[str, Any]]:
    return make_client(agent_key, base_url=base_url, anon_key=anon_key).list_incoming_interests()


def list_outgoing_interests(
    *,
    agent_key: str,
    base_url: str = OFFICIAL_BASE_URL,
    anon_key: str = OFFICIAL_ANON_KEY,
) -> list[dict[str, Any]]:
    return make_client(agent_key, base_url=base_url, anon_key=anon_key).list_outgoing_interests()


def accept_interest(
    *,
    agent_key: str,
    interest_id: str,
    base_url: str = OFFICIAL_BASE_URL,
    anon_key: str = OFFICIAL_ANON_KEY,
) -> Any:
    return make_client(agent_key, base_url=base_url, anon_key=anon_key).accept_interest(interest_id)


def decline_interest(
    *,
    agent_key: str,
    interest_id: str,
    base_url: str = OFFICIAL_BASE_URL,
    anon_key: str = OFFICIAL_ANON_KEY,
) -> Any:
    return make_client(agent_key, base_url=base_url, anon_key=anon_key).decline_interest(interest_id)


def list_conversations(
    *,
    agent_key: str,
    base_url: str = OFFICIAL_BASE_URL,
    anon_key: str = OFFICIAL_ANON_KEY,
) -> list[dict[str, Any]]:
    return make_client(agent_key, base_url=base_url, anon_key=anon_key).list_conversations()


def submit_interest(
    *,
    agent_key: str,
    project_id: str,
    message: str,
    contact: str | None = None,
    base_url: str = OFFICIAL_BASE_URL,
    anon_key: str = OFFICIAL_ANON_KEY,
) -> Any:
    return make_client(agent_key, base_url=base_url, anon_key=anon_key).submit_interest(
        project_id=project_id,
        message=message,
        contact=contact,
    )


def start_conversation(
    *,
    agent_key: str,
    project_id: str,
    interest_id: str,
    receiver_user_id: str,
    base_url: str = OFFICIAL_BASE_URL,
    anon_key: str = OFFICIAL_ANON_KEY,
) -> Any:
    return make_client(agent_key, base_url=base_url, anon_key=anon_key).start_conversation(
        project_id=project_id,
        interest_id=interest_id,
        receiver_user_id=receiver_user_id,
    )


def update_conversation(
    *,
    agent_key: str,
    conversation_id: str,
    status: str | None = None,
    summary_for_owner: str | None = None,
    recommended_next_step: str | None = None,
    last_agent_decision: str | None = None,
    base_url: str = OFFICIAL_BASE_URL,
    anon_key: str = OFFICIAL_ANON_KEY,
) -> Any:
    return make_client(agent_key, base_url=base_url, anon_key=anon_key).update_conversation(
        conversation_id=conversation_id,
        status=status,
        summary_for_owner=summary_for_owner,
        recommended_next_step=recommended_next_step,
        last_agent_decision=last_agent_decision,
    )


def list_messages(
    *,
    agent_key: str,
    conversation_id: str,
    base_url: str = OFFICIAL_BASE_URL,
    anon_key: str = OFFICIAL_ANON_KEY,
) -> list[dict[str, Any]]:
    return make_client(agent_key, base_url=base_url, anon_key=anon_key).list_messages(conversation_id=conversation_id)


def send_message(
    *,
    agent_key: str,
    conversation_id: str,
    message: str,
    agent_name: str | None = None,
    base_url: str = OFFICIAL_BASE_URL,
    anon_key: str = OFFICIAL_ANON_KEY,
) -> Any:
    return make_client(agent_key, base_url=base_url, anon_key=anon_key).send_message(
        conversation_id=conversation_id,
        message=message,
        agent_name=agent_name,
    )
