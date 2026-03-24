"""
Message patrol engine — scans active conversations for new incoming
messages and produces structured action items for the agent.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from .client import GatewayClient

ACTIVE_CONVERSATION_STATUSES = {"active", "mutual", "conversation_started"}

REPLY_ACTION_MAP = {
    "notify_only": "notify",
    "draft_then_confirm": "draft_reply",
    "auto_reply_simple": "reply_now",
}


@dataclass
class InboxItem:
    conversation_id: str
    project_id: str
    project_name: str
    new_messages: list[dict[str, Any]]
    reply_action: str
    policy_hints: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MessagePatrolReport:
    ran_at: str
    conversations_scanned: int
    items_needing_attention: list[InboxItem] = field(default_factory=list)
    state_updates: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ran_at": self.ran_at,
            "conversations_scanned": self.conversations_scanned,
            "items_needing_attention": [item.to_dict() for item in self.items_needing_attention],
            "state_updates": self.state_updates,
        }


def build_policy_hints(effective_policy: dict[str, Any]) -> dict[str, Any]:
    """Extract messaging-relevant constraints from the effective policy."""
    messaging = effective_policy.get("messaging") or {}
    conversation_policy = effective_policy.get("conversationPolicy") or {}
    return {
        "tone": messaging.get("tone", "warm-analytical"),
        "length": messaging.get("length", "short"),
        "style": messaging.get("style", []),
        "avoid_phrases": messaging.get("avoidPhrases", []),
        "include_direct_commitment_language": messaging.get("includeDirectCommitmentLanguage", False),
        "mention_agent_contact": messaging.get("mentionAgentContact", True),
        "conversation_goals": conversation_policy.get("goals", []),
        "conversation_avoid": conversation_policy.get("avoid", []),
    }


def run_message_patrol(
    *,
    agent_user_id: str,
    conversations: list[dict[str, Any]],
    policy_bundle: dict[str, Any],
    conversation_state: dict[str, Any],
    client: GatewayClient,
) -> MessagePatrolReport:
    """Scan conversations for new incoming messages and produce action items.

    Parameters
    ----------
    agent_user_id:
        The user_id of the agent (used to filter out own messages).
    conversations:
        Pre-fetched list of conversation dicts.
    policy_bundle:
        Output of ``db_policy_to_runtime_bundle()``.
    conversation_state:
        ``state.json["conversations"]`` — tracks ``last_seen_message_id`` per
        conversation.
    client:
        ``GatewayClient`` for fetching messages.
    """
    now = datetime.now(timezone.utc)
    effective_policy = policy_bundle.get("effective_policy", {})
    reply_policy = policy_bundle.get("execution", {}).get("reply_policy", "draft_then_confirm")
    reply_action = REPLY_ACTION_MAP.get(reply_policy, "draft_reply")
    hints = build_policy_hints(effective_policy)

    active_conversations = [c for c in conversations if c.get("status") in ACTIVE_CONVERSATION_STATUSES]

    items: list[InboxItem] = []
    state_updates: dict[str, Any] = {}

    for conv in active_conversations:
        conv_id = conv.get("id")
        if not conv_id:
            continue

        messages = client.list_messages(conversation_id=conv_id)
        if not messages:
            continue

        conv_tracking = conversation_state.get(conv_id) or {}
        last_seen_id = conv_tracking.get("last_seen_message_id")

        # Find new messages after the last seen one
        if last_seen_id:
            seen_index = None
            for i, msg in enumerate(messages):
                if msg.get("id") == last_seen_id:
                    seen_index = i
                    break
            new_messages = messages[seen_index + 1 :] if seen_index is not None else messages
        else:
            new_messages = messages

        # Filter to messages from the other party (not from the agent)
        incoming = [msg for msg in new_messages if msg.get("sender_user_id") != agent_user_id]

        if incoming:
            items.append(
                InboxItem(
                    conversation_id=conv_id,
                    project_id=conv.get("project_id", ""),
                    project_name=conv.get("project_name", ""),
                    new_messages=incoming,
                    reply_action=reply_action,
                    policy_hints=hints,
                )
            )

        # Always update last_seen to the latest message
        latest_msg_id = messages[-1].get("id") if messages else last_seen_id
        if latest_msg_id:
            state_updates[conv_id] = {
                "last_seen_message_id": latest_msg_id,
                "last_message_run_at": now.isoformat(),
            }

    return MessagePatrolReport(
        ran_at=now.isoformat(),
        conversations_scanned=len(active_conversations),
        items_needing_attention=items,
        state_updates=state_updates,
    )


__all__ = [
    "InboxItem",
    "MessagePatrolReport",
    "build_policy_hints",
    "run_message_patrol",
]
