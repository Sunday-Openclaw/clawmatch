"""
Pure autopilot core used by the backend skill runtime and packaged skill runtime.
"""

from __future__ import annotations

DEFAULT_POLICY = {
    "scanStrategy": {
        "enabled": True,
        "scanIntervalMinutes": 180,
        "maxProjectsPerRun": 30,
        "maxNewInterestsPerRun": 2,
        "skipOwnProjects": True,
        "skipIfExistingOpenInterest": True,
    },
    "automation": {
        "autoSubmitInterest": False,
        "autoAcceptIncomingInterest": False,
        "autoStartConversation": False,
        "requireHumanApprovalForInterest": True,
        "requireHumanApprovalForAcceptingInterest": True,
        "requireHumanApprovalForConversation": True,
        "requireHumanApprovalForCommitmentSignals": True,
    },
    "preferences": {
        "prioritizeTags": [],
        "avoidTags": [],
        "preferredProjectTypes": [],
        "avoidProjectTypes": [],
        "preferredCollaborationStyle": [],
        "avoidCollaborationStyle": [],
    },
    "hardConstraints": {
        "disallowedPatterns": [],
        "mustHaveAtLeastOne": [],
    },
    "decisionThresholds": {
        "watch": 0.45,
        "interest": 0.70,
        "conversation": 0.82,
        "handoff": 0.90,
    },
    "decisionPolicy": {
        "preferWatchWhenUncertain": True,
        "preferDraftOverAutoSend": True,
        "requireSpecificFitReasonBeforeInterest": True,
        "requireAtLeastOneConcreteAlignment": True,
        "allowExploratoryInterestForHighUpside": True,
        "minConfidenceForInterest": 0.65,
        "minConfidenceForConversation": 0.75,
    },
    "messaging": {
        "tone": "warm-analytical",
        "length": "short",
        "mentionAgentContact": True,
        "includeDirectCommitmentLanguage": False,
        "avoidPhrases": [],
    },
    "conversationPolicy": {
        "goals": [
            "clarify project scope",
            "clarify collaboration style",
            "identify missing constraints",
            "test whether mutual fit is real",
        ],
        "avoid": [
            "making commitments on behalf of owner",
            "negotiating final terms without human review",
            "revealing unnecessary private information",
        ],
        "startConversationOnlyIf": [
            "interest accepted",
            "platform flow permits it",
            "confidence above conversation threshold",
        ],
    },
    "humanHandoff": {
        "notifyOnStrongMatch": True,
        "notifyOnMutualInterest": True,
        "notifyOnNeedClarification": True,
        "notifyOnRequestForCommitment": True,
        "notifyWhenMultiplePromisingOptionsExist": True,
    },
}


def deep_merge(base, override):
    if not isinstance(base, dict) or not isinstance(override, dict):
        return override
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def tokenize(text: str):
    return {
        word.strip().lower()
        for word in "".join(ch if ch.isalnum() else " " for ch in (text or "")).split()
        if len(word.strip()) > 2
    }


def project_text(project: dict):
    return " ".join(
        [
            project.get("project_name", "") or "",
            project.get("public_summary", "") or "",
            project.get("tags", "") or "",
        ]
    )


def overlap_count(project: dict, items: list[str]):
    if not items:
        return 0
    words = tokenize(project_text(project))
    wanted = set()
    for item in items:
        wanted |= tokenize(item)
    return len(words & wanted)


def contains_any_phrase(project: dict, phrases: list[str]):
    text = project_text(project).lower()
    return any((phrase or "").strip().lower() in text for phrase in phrases if phrase)


def must_have_any(project: dict, phrases: list[str]):
    if not phrases:
        return True
    text = project_text(project).lower()
    return any((phrase or "").strip().lower() in text for phrase in phrases if phrase)


def fit_band(score: float):
    if score >= 0.90:
        return "very-high"
    if score >= 0.82:
        return "high"
    if score >= 0.70:
        return "medium-high"
    if score >= 0.55:
        return "medium"
    if score >= 0.40:
        return "low-medium"
    return "low"


def build_interest_message(project: dict, decision: dict):
    name = project.get("project_name") or "this project"
    reasons = decision.get("reasons_for_fit", [])[:2]
    if reasons:
        fit_phrase = "; ".join(reasons)
        return (
            f"My owner may be a good fit for {name}, especially because {fit_phrase}. "
            f"If helpful, I'm happy to explore whether the scope and collaboration style line up."
        )
    tags = project.get("tags") or ""
    if tags:
        return (
            f"My owner may be a promising fit for {name}. "
            f"Interested in exploring whether there is a good collaboration fit around {tags}."
        )
    return f"My owner may be a promising fit for {name}. Interested in exploring whether there is a good collaboration fit."


def build_handoff_summary(project: dict, decision: dict):
    reasons = "; ".join(decision.get("reasons_for_fit", [])[:2]) or "promising owner-specific alignment"
    risks = "; ".join(decision.get("risks_or_mismatches", [])[:2]) or "no major blockers yet"
    missing = "; ".join(decision.get("missing_information", [])[:2]) or "no major missing info identified"
    return {
        "project_id": decision.get("project_id"),
        "project_name": decision.get("project_name"),
        "why_now": reasons,
        "current_risks": risks,
        "missing_information": missing,
        "recommended_next_step": "Human should review and decide whether to personally engage or approve stronger agent action.",
        "draft_interest": decision.get("opening_message"),
    }


def build_conversation_brief(project: dict, decision: dict, policy: dict):
    goals = policy.get("conversationPolicy", {}).get("goals", [])[:3]
    return {
        "project_id": decision.get("project_id"),
        "project_name": decision.get("project_name"),
        "status": "ready_if_mutual_or_allowed",
        "goals": goals,
        "suggested_opening": (
            "Happy to explore whether the scope, collaboration style, and expectations line up. "
            "Could you share a bit more about what kind of contribution would be most useful?"
        ),
    }


def build_conversation_auto_start_signal(decision: dict, policy: dict):
    automation = policy.get("automation", {})
    blocked_by = []
    trigger = None

    if decision.get("existing_interest_status") != "accepted":
        blocked_by.append("interest_not_accepted")
    if decision.get("existing_conversation_id"):
        blocked_by.append("existing_conversation")
    if decision.get("decision") != "conversation":
        blocked_by.append("conversation_threshold_not_met")
    if not automation.get("autoStartConversation", False):
        blocked_by.append("auto_start_disabled_by_policy")
    if automation.get("requireHumanApprovalForConversation", True):
        blocked_by.append("human_approval_required")

    candidate = len(blocked_by) == 0
    if candidate:
        trigger = "accepted_interest_and_policy_allows"

    return {
        "auto_start_conversation_candidate": candidate,
        "auto_start_blocked": not candidate,
        "blocked_by": blocked_by,
        "conversation_trigger": trigger,
    }


def build_conversation_state_plan(decision: dict):
    conversation_id = decision.get("existing_conversation_id")
    if decision.get("decision") == "handoff":
        summary = decision.get("handoff_summary") or {}
        return {
            "conversation_id": conversation_id,
            "project_id": decision.get("project_id"),
            "target_status": "handoff_ready" if decision.get("confidence", 0) >= 0.9 else "needs_human",
            "last_agent_decision": decision.get("handoff_reason") or "owner review recommended",
            "summary_for_owner": (
                f"Why promising: {summary.get('why_now', 'Potential fit identified')}. "
                f"Risks: {summary.get('current_risks', 'none noted')}"
            ),
            "recommended_next_step": summary.get(
                "recommended_next_step", "Review the thread and decide whether to continue personally."
            ),
            "ready_to_apply": bool(conversation_id),
        }

    if decision.get("decision") == "conversation":
        brief = decision.get("conversation_brief") or {}
        goals = brief.get("goals", [])
        goal_text = "; ".join(goals[:2]) if goals else "clarify fit and collaboration details"
        return {
            "conversation_id": conversation_id,
            "project_id": decision.get("project_id"),
            "target_status": "conversation_started",
            "last_agent_decision": "conversation candidate identified",
            "summary_for_owner": f"Agents think this thread is worth continuing. Immediate goal: {goal_text}.",
            "recommended_next_step": "Let the agents continue clarifying scope unless a human decision is needed now.",
            "ready_to_apply": bool(conversation_id),
        }

    return None


def evaluate_project(
    project: dict, policy: dict, my_user_id: str, existing_interest_map: dict, existing_conversation_map: dict
):
    reasons = []
    risks = []
    missing = []
    text = project_text(project)
    text_tokens = tokenize(text)

    scan = policy.get("scanStrategy", {})
    prefs = policy.get("preferences", {})
    constraints = policy.get("hardConstraints", {})
    decision_policy = policy.get("decisionPolicy", {})
    thresholds = policy.get("decisionThresholds", {})
    handoff_policy = policy.get("humanHandoff", {})

    existing_interest = existing_interest_map.get(project.get("id"))
    existing_conversation = existing_conversation_map.get(project.get("id"))

    if scan.get("skipOwnProjects", True) and project.get("user_id") == my_user_id:
        return {
            "project_id": project.get("id"),
            "project_name": project.get("project_name"),
            "decision": "skip",
            "fit_band": "low",
            "confidence": 1.0,
            "reasons_for_fit": [],
            "risks_or_mismatches": ["owner's own project"],
            "missing_information": [],
            "recommended_action": "none",
            "existing_interest_status": None,
            "existing_conversation_id": existing_conversation.get("id") if existing_conversation else None,
            "existing_conversation_status": existing_conversation.get("status") if existing_conversation else None,
        }

    if existing_interest and scan.get("skipIfExistingOpenInterest", True) and existing_interest.get("status") == "open":
        return {
            "project_id": project.get("id"),
            "project_name": project.get("project_name"),
            "decision": "watch",
            "fit_band": "medium-high",
            "confidence": 0.9,
            "reasons_for_fit": ["existing open interest already present"],
            "risks_or_mismatches": [],
            "missing_information": [],
            "recommended_action": "wait-on-existing-interest",
            "existing_interest_status": existing_interest.get("status"),
            "existing_conversation_id": existing_conversation.get("id") if existing_conversation else None,
            "existing_conversation_status": existing_conversation.get("status") if existing_conversation else None,
        }

    if contains_any_phrase(project, constraints.get("disallowedPatterns", [])):
        return {
            "project_id": project.get("id"),
            "project_name": project.get("project_name"),
            "decision": "skip",
            "fit_band": "low",
            "confidence": 0.95,
            "reasons_for_fit": [],
            "risks_or_mismatches": ["matches a disallowed pattern in owner policy"],
            "missing_information": [],
            "recommended_action": "none",
            "existing_interest_status": existing_interest.get("status") if existing_interest else None,
            "existing_conversation_id": existing_conversation.get("id") if existing_conversation else None,
            "existing_conversation_status": existing_conversation.get("status") if existing_conversation else None,
        }

    if not must_have_any(project, constraints.get("mustHaveAtLeastOne", [])):
        risks.append("listing does not clearly show minimum seriousness signals from owner policy")

    prioritize_overlap = overlap_count(project, prefs.get("prioritizeTags", []))
    avoid_overlap = overlap_count(project, prefs.get("avoidTags", []))
    preferred_style_overlap = overlap_count(project, prefs.get("preferredCollaborationStyle", []))
    avoid_style_overlap = overlap_count(project, prefs.get("avoidCollaborationStyle", []))
    preferred_type_overlap = overlap_count(project, prefs.get("preferredProjectTypes", []))
    avoid_type_overlap = overlap_count(project, prefs.get("avoidProjectTypes", []))

    score = 0.20
    if prioritize_overlap:
        score += min(0.30, 0.08 * prioritize_overlap)
        reasons.append("overlap with prioritized owner interests/topics")
    if preferred_style_overlap:
        score += min(0.18, 0.09 * preferred_style_overlap)
        reasons.append("collaboration style appears aligned")
    if preferred_type_overlap:
        score += min(0.15, 0.08 * preferred_type_overlap)
        reasons.append("project type aligns with owner preferences")
    if avoid_overlap:
        score -= min(0.25, 0.10 * avoid_overlap)
        risks.append("contains avoided tags or themes")
    if avoid_style_overlap:
        score -= min(0.20, 0.10 * avoid_style_overlap)
        risks.append("collaboration style may conflict with owner preferences")
    if avoid_type_overlap:
        score -= min(0.18, 0.09 * avoid_type_overlap)
        risks.append("project type may conflict with owner preferences")

    if len(text_tokens) < 12:
        score -= 0.10
        risks.append("listing is sparse or vague")
        missing.append("clearer project scope")
    else:
        score += 0.08
        reasons.append("listing has enough detail for preliminary judgment")

    summary_lower = (project.get("public_summary") or "").lower()
    if "async" in summary_lower:
        score += 0.06
        reasons.append("explicit async-friendly signal")
    if any(word in summary_lower for word in ["urgent", "immediately", "asap"]):
        score -= 0.08
        risks.append("timeline may be more urgent than owner prefers")
    if any(word in summary_lower for word in ["full-time", "full time"]):
        score -= 0.15
        risks.append("may imply stronger commitment than owner prefers")
    if "collabor" not in summary_lower and "research" not in summary_lower and "build" not in summary_lower:
        missing.append("clearer collaboration shape")

    score = max(0.0, min(1.0, score))

    if not reasons and decision_policy.get("requireAtLeastOneConcreteAlignment", True):
        risks.append("no concrete alignment signal found")

    watch_t = thresholds.get("watch", 0.45)
    interest_t = thresholds.get("interest", 0.70)
    conversation_t = thresholds.get("conversation", 0.82)
    handoff_t = thresholds.get("handoff", 0.90)
    min_confidence_for_interest = decision_policy.get("minConfidenceForInterest", 0.65)
    min_confidence_for_conversation = decision_policy.get("minConfidenceForConversation", 0.75)

    confidence = score
    decision = "skip"
    action = "none"
    handoff_reason = None

    if (
        existing_interest
        and existing_interest.get("status") == "accepted"
        and score >= conversation_t
        and confidence >= min_confidence_for_conversation
    ):
        decision = "conversation"
        action = "draft-conversation"
    elif score >= interest_t and confidence >= min_confidence_for_interest and reasons:
        decision = "interest"
        action = "draft-interest"
    elif score >= watch_t:
        decision = "watch"
        action = "watch"
    else:
        decision = "skip"
        action = "none"

    if (
        decision == "interest"
        and decision_policy.get("preferWatchWhenUncertain", True)
        and len(risks) >= 2
        and score < max(0.8, interest_t + 0.05)
    ):
        decision = "watch"
        action = "watch"

    if decision == "interest" and decision_policy.get("requireSpecificFitReasonBeforeInterest", True) and not reasons:
        decision = "watch"
        action = "watch"

    if (
        decision in {"interest", "conversation"}
        and handoff_policy.get("notifyOnNeedClarification", True)
        and len(missing) >= 2
        and score >= max(interest_t, 0.68)
    ):
        decision = "handoff"
        action = "human-review"
        handoff_reason = "promising match but too much key information is still missing"

    if decision == "conversation" and handoff_policy.get("notifyOnMutualInterest", True):
        decision = "handoff"
        action = "human-review"
        handoff_reason = "accepted interest suggests mutual fit and owner may want to review next steps"

    if decision == "interest" and handoff_policy.get("notifyOnStrongMatch", True) and score >= handoff_t:
        decision = "handoff"
        action = "human-review"
        handoff_reason = "strong owner-specific fit exceeds handoff threshold"

    result = {
        "project_id": project.get("id"),
        "project_name": project.get("project_name"),
        "decision": decision,
        "fit_band": fit_band(score),
        "confidence": round(confidence, 2),
        "reasons_for_fit": reasons[:4],
        "risks_or_mismatches": risks[:4],
        "missing_information": missing[:4],
        "recommended_action": action,
        "tags": project.get("tags"),
        "summary": project.get("public_summary"),
        "existing_interest_status": existing_interest.get("status") if existing_interest else None,
        "existing_interest_id": existing_interest.get("id") if existing_interest else None,
        "existing_interest_from_user_id": existing_interest.get("from_user_id") if existing_interest else None,
        "existing_conversation_id": existing_conversation.get("id") if existing_conversation else None,
        "existing_conversation_status": existing_conversation.get("status") if existing_conversation else None,
        "target_project_owner_user_id": project.get("user_id"),
    }

    if decision in {"interest", "handoff"}:
        result["opening_message"] = build_interest_message(project, result)
    if decision == "conversation":
        result["conversation_brief"] = build_conversation_brief(project, result, policy)
    if decision == "handoff":
        result["handoff_reason"] = handoff_reason or "owner review recommended"
        result["handoff_summary"] = build_handoff_summary(project, result)

    auto_start_signal = build_conversation_auto_start_signal(result, policy)
    result.update(auto_start_signal)

    state_plan = build_conversation_state_plan(result)
    if state_plan:
        result["conversation_state_plan"] = state_plan

    return result


def summarize_report(report: dict):
    return {
        "market_count": report.get("market_count", 0),
        "open_interest_count": report.get("open_interest_count", 0),
        "conversation_count": report.get("conversation_count", 0),
        "decision_counts": {
            "interest": len(report.get("selected_interests", [])),
            "watch": len(report.get("watchlist", [])),
            "conversation": len(report.get("conversation_candidates", [])),
            "handoff": len(report.get("handoffs", [])),
            "skip": len(report.get("skips", [])),
        },
        "top_interest_projects": [
            {
                "project_id": d.get("project_id"),
                "project_name": d.get("project_name"),
                "confidence": d.get("confidence"),
            }
            for d in report.get("selected_interests", [])[:5]
        ],
        "top_handoffs": [
            {
                "project_id": d.get("project_id"),
                "project_name": d.get("project_name"),
                "reason": d.get("handoff_reason"),
            }
            for d in report.get("handoffs", [])[:5]
        ],
        "auto_start_conversation_candidates": len(
            report.get("execution_plan", {}).get("conversation_auto_start_candidates", [])
        ),
    }


def build_execution_plan(report: dict):
    plan: dict[str, list] = {
        "interest_submissions": [],
        "conversation_state_updates": [],
        "conversation_auto_start_candidates": [],
    }

    for decision in report.get("selected_interests", []):
        plan["interest_submissions"].append(
            {
                "project_id": decision.get("project_id"),
                "project_name": decision.get("project_name"),
                "message": decision.get("opening_message"),
                "reason": "; ".join(decision.get("reasons_for_fit", [])[:2]) or "promising fit",
            }
        )

    for decision in report.get("handoffs", []) + report.get("conversation_candidates", []):
        state_plan = decision.get("conversation_state_plan")
        if state_plan:
            plan["conversation_state_updates"].append(state_plan)

    for decision in report.get("all_decisions", []):
        if decision.get("auto_start_conversation_candidate"):
            plan["conversation_auto_start_candidates"].append(
                {
                    "project_id": decision.get("project_id"),
                    "project_name": decision.get("project_name"),
                    "receiver_user_id": decision.get("target_project_owner_user_id"),
                    "existing_interest_id": decision.get("existing_interest_id"),
                    "existing_interest_status": decision.get("existing_interest_status"),
                    "conversation_trigger": decision.get("conversation_trigger"),
                    "blocked_by": decision.get("blocked_by", []),
                    "suggested_action": "start-conversation",
                }
            )

    return plan


def choose_candidates_from_data(
    me: dict, market: list[dict], open_interests: list[dict], conversations: list[dict], policy: dict
):
    existing_interest_map = {
        row["target_project_id"]: row for row in (open_interests or []) if row.get("target_project_id")
    }
    existing_conversation_map = {row["project_id"]: row for row in (conversations or []) if row.get("project_id")}

    decisions = [
        evaluate_project(project, policy, str(me.get("id", "")), existing_interest_map, existing_conversation_map)
        for project in (market or [])
    ]

    interests = [d for d in decisions if d["decision"] == "interest"]
    watches = [d for d in decisions if d["decision"] == "watch"]
    skips = [d for d in decisions if d["decision"] == "skip"]
    conversation_candidates = [d for d in decisions if d["decision"] == "conversation"]
    handoffs = [d for d in decisions if d["decision"] == "handoff"]

    interests.sort(key=lambda d: d["confidence"], reverse=True)
    watches.sort(key=lambda d: d["confidence"], reverse=True)
    conversation_candidates.sort(key=lambda d: d["confidence"], reverse=True)
    handoffs.sort(key=lambda d: d["confidence"], reverse=True)

    scan = policy.get("scanStrategy", {})
    max_new = int(scan.get("maxNewInterestsPerRun", 2))
    selected_interests = interests[:max_new]

    report = {
        "me": {"id": me.get("id"), "email": me.get("email")},
        "market_count": len(market or []),
        "open_interest_count": len(open_interests or []),
        "conversation_count": len(conversations),
        "selected_interests": selected_interests,
        "watchlist": watches,
        "conversation_candidates": conversation_candidates,
        "handoffs": handoffs,
        "skips": skips,
        "all_decisions": decisions,
    }
    report["summary"] = summarize_report(report)
    report["execution_plan"] = build_execution_plan(report)
    return report
