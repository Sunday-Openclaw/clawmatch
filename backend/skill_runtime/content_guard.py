"""
Content compliance engine for outgoing messages.

Validates message text against policy rules before sending,
hard-blocking violations so the agent must revise and retry.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ComplianceViolation:
    rule: str  # "avoid_phrase" | "conversation_avoid" | "contact_share" | "commitment"
    detail: str  # the specific phrase or pattern that triggered
    severity: str  # "block" | "handoff_required"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class ComplianceResult:
    passed: bool
    violations: list[ComplianceViolation]

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "violations": [v.to_dict() for v in self.violations]}


# ---------------------------------------------------------------------------
# Pre-defined keyword / pattern sets
# ---------------------------------------------------------------------------

CONTACT_KEYWORDS = [
    "email",
    "e-mail",
    "phone",
    "telephone",
    "微信",
    "wechat",
    "telegram",
    "whatsapp",
    "signal",
    "discord",
    "skype",
    "line",
]

EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
PHONE_RE = re.compile(r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}")

COMMITMENT_KEYWORDS = [
    "agree",
    "commit",
    "promise",
    "accept terms",
    "sign",
    "deal",
    "签约",
    "承诺",
    "同意",
    "确认合作",
    "accept the offer",
    "i confirm",
    "we confirm",
    "binding",
    "contract",
]


# ---------------------------------------------------------------------------
# Individual check functions
# ---------------------------------------------------------------------------


def _check_avoid_phrases(
    message_lower: str,
    phrases: list[str],
) -> list[ComplianceViolation]:
    violations: list[ComplianceViolation] = []
    for phrase in phrases:
        if not phrase:
            continue
        if phrase.lower() in message_lower:
            violations.append(
                ComplianceViolation(
                    rule="avoid_phrase",
                    detail=phrase,
                    severity="block",
                )
            )
    return violations


def _check_conversation_avoid(
    message_lower: str,
    avoid_rules: list[str],
) -> list[ComplianceViolation]:
    """Check conversation-level avoid rules using keyword extraction.

    Each rule is a natural-language phrase like "making commitments on behalf of owner".
    We extract significant words and check if a critical mass appears in the message.
    """
    violations: list[ComplianceViolation] = []
    stop_words = {
        "a",
        "an",
        "the",
        "of",
        "on",
        "in",
        "for",
        "to",
        "and",
        "or",
        "is",
        "be",
        "been",
        "being",
        "with",
        "without",
        "not",
        "no",
        "at",
        "by",
        "from",
        "it",
        "its",
        "that",
        "this",
        "these",
        "those",
        "when",
        "how",
    }
    for rule in avoid_rules:
        if not rule:
            continue
        keywords = [w for w in rule.lower().split() if w not in stop_words and len(w) > 2]
        if not keywords:
            continue
        hits = sum(1 for kw in keywords if kw in message_lower)
        threshold = max(1, len(keywords) // 2)
        if hits >= threshold:
            violations.append(
                ComplianceViolation(
                    rule="conversation_avoid",
                    detail=rule,
                    severity="block",
                )
            )
    return violations


def _check_contact_share(message: str, message_lower: str) -> list[ComplianceViolation]:
    violations: list[ComplianceViolation] = []
    for keyword in CONTACT_KEYWORDS:
        if keyword.lower() in message_lower:
            violations.append(
                ComplianceViolation(
                    rule="contact_share",
                    detail=f"keyword: {keyword}",
                    severity="handoff_required",
                )
            )
            break
    if not violations and EMAIL_RE.search(message):
        violations.append(
            ComplianceViolation(
                rule="contact_share",
                detail="email address detected",
                severity="handoff_required",
            )
        )
    if not violations and PHONE_RE.search(message):
        violations.append(
            ComplianceViolation(
                rule="contact_share",
                detail="phone number detected",
                severity="handoff_required",
            )
        )
    return violations


def _check_commitment_language(message_lower: str) -> list[ComplianceViolation]:
    violations: list[ComplianceViolation] = []
    for keyword in COMMITMENT_KEYWORDS:
        if keyword.lower() in message_lower:
            violations.append(
                ComplianceViolation(
                    rule="commitment",
                    detail=f"keyword: {keyword}",
                    severity="handoff_required",
                )
            )
            break
    return violations


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def check_message_compliance(
    message: str,
    policy: dict[str, Any],
    handoff_triggers: set[str],
) -> ComplianceResult:
    """Validate *message* against *policy* rules.

    Returns a ``ComplianceResult`` with ``passed=False`` if any violation is
    found.  All violations are collected (not short-circuited) so the caller
    can present them to the agent in a single response.
    """
    if not message or not message.strip():
        return ComplianceResult(passed=True, violations=[])

    message_lower = message.lower()
    all_violations: list[ComplianceViolation] = []

    # 1. avoidPhrases
    avoid_phrases = (policy.get("messaging") or {}).get("avoidPhrases") or []
    all_violations.extend(_check_avoid_phrases(message_lower, avoid_phrases))

    # 2. conversationPolicy.avoid
    conversation_avoid = (policy.get("conversationPolicy") or {}).get("avoid") or []
    all_violations.extend(_check_conversation_avoid(message_lower, conversation_avoid))

    # 3. contact_share (only when trigger is active)
    if "before_contact_share" in handoff_triggers:
        all_violations.extend(_check_contact_share(message, message_lower))

    # 4. commitment language (only when trigger is active)
    if "before_commitment" in handoff_triggers:
        all_violations.extend(_check_commitment_language(message_lower))

    return ComplianceResult(
        passed=len(all_violations) == 0,
        violations=all_violations,
    )


__all__ = [
    "ComplianceResult",
    "ComplianceViolation",
    "check_message_compliance",
]
