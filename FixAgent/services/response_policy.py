"""Deterministic answer policy derived from intent, scope, evidence and risk."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from services.intent_router import IntentDecision

GENERAL_AI = "GENERAL_AI"
GROUNDED_KNOWLEDGE = "GROUNDED_KNOWLEDGE"
PARTIAL_GROUNDED = "PARTIAL_GROUNDED"
MAINTENANCE_AI_FALLBACK = "MAINTENANCE_AI_FALLBACK"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
BLOCKED_SCOPE = "BLOCKED_SCOPE"
EVIDENCE_CONFLICT = "EVIDENCE_CONFLICT"
PENDING_RETRIEVAL = "PENDING_RETRIEVAL"


@dataclass(frozen=True)
class ResponsePolicy:
    mode: str
    intent: str
    knowledge_status: str
    scope_status: str
    source_type: str
    risk_level: str
    disclaimer_required: bool
    manual_citation_allowed: bool
    images_allowed: bool
    allow_knowledge_retrieval: bool
    allow_ai_fallback: bool
    required_facts: tuple[str, ...] = ()
    style_profile: str = "plain_conversational"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _scope_reason(scope: Mapping[str, Any]) -> str:
    return str(scope.get("reason") or "").strip().lower()


def _scope_status(scope: Mapping[str, Any]) -> str:
    return str(scope.get("status") or "unknown").strip().lower()


def _is_explicit_conflict(scope: Mapping[str, Any]) -> bool:
    return _scope_reason(scope) in {
        "explicit_device_conflict",
        "explicit_document_conflict",
        "unknown_document",
        "document_not_found",
    }


def _is_missing_device_document(scope: Mapping[str, Any]) -> bool:
    return _scope_reason(scope) in {
        "device_document_conflict",
        "unsupported_device",
        "no_matching_device_document",
        "explicit_device_switch",
        "identity_attribute_conflict",
        "identity_not_distinguishing",
        "query_device_not_explicit",
        "no_confirmed_scope",
    } and bool(scope.get("detected_device_type") or scope.get("requested_device_type"))


def _risk_level(decision: IntentDecision, query: str) -> str:
    if decision.intent in {"parameter_query", "maintenance_guidance", "procedure_planning"}:
        return "high"
    if decision.intent == "fault_diagnosis" and any(
        term in query for term in ("异响", "过热", "漏油", "报警", "启动不了")
    ):
        return "medium"
    return "low"


def derive_response_policy(
    decision: IntentDecision,
    scope: Mapping[str, Any] | None,
    evidence: Mapping[str, Any] | None,
    *,
    query: str = "",
) -> ResponsePolicy:
    scope = scope or {}
    evidence = evidence or {}
    scope_status = _scope_status(scope)
    coverage = str(evidence.get("coverage_status") or evidence.get("overall_status") or "").lower()
    risk = _risk_level(decision, query)

    if decision.target_layer == "chat" or decision.intent == "chat_social":
        return ResponsePolicy(
            mode=GENERAL_AI, intent=decision.intent, knowledge_status="not_required",
            scope_status=scope_status, source_type="ai", risk_level="low",
            disclaimer_required=False, manual_citation_allowed=False, images_allowed=False,
            allow_knowledge_retrieval=False, allow_ai_fallback=True, style_profile="general_ai",
        )

    if _is_explicit_conflict(scope):
        return ResponsePolicy(
            mode=BLOCKED_SCOPE, intent=decision.intent, knowledge_status="blocked",
            scope_status=scope_status, source_type="scope", risk_level=risk,
            disclaimer_required=False, manual_citation_allowed=False, images_allowed=False,
            allow_knowledge_retrieval=False, allow_ai_fallback=False, style_profile="scope_guard",
        )

    if _is_missing_device_document(scope):
        mode = INSUFFICIENT_EVIDENCE if risk == "high" else MAINTENANCE_AI_FALLBACK
        return ResponsePolicy(
            mode=mode, intent=decision.intent,
            knowledge_status="no_matching_device_document", scope_status=scope_status,
            source_type="ai", risk_level=risk, disclaimer_required=True,
            manual_citation_allowed=False, images_allowed=False, allow_knowledge_retrieval=False,
            allow_ai_fallback=risk != "high",
            style_profile="insufficient_evidence" if risk == "high" else "maintenance_ai",
        )

    if not evidence and decision.requires_knowledge_retrieval:
        return ResponsePolicy(
            mode=PENDING_RETRIEVAL, intent=decision.intent, knowledge_status="pending",
            scope_status=scope_status, source_type="pending", risk_level=risk,
            disclaimer_required=False, manual_citation_allowed=False, images_allowed=False,
            allow_knowledge_retrieval=True, allow_ai_fallback=False, style_profile="pending",
        )

    if coverage == "conflict":
        mode = EVIDENCE_CONFLICT
    elif coverage == "partial":
        mode = PARTIAL_GROUNDED
    elif coverage == "complete" or evidence.get("qualified_evidence"):
        mode = GROUNDED_KNOWLEDGE
    else:
        requires_strict_manual_evidence = (
            decision.requires_manual_evidence
            and decision.intent != "fault_diagnosis"
        )
        mode = (
            INSUFFICIENT_EVIDENCE
            if risk == "high" or requires_strict_manual_evidence
            else MAINTENANCE_AI_FALLBACK
        )

    grounded = mode in {GROUNDED_KNOWLEDGE, PARTIAL_GROUNDED, EVIDENCE_CONFLICT}
    if grounded:
        return ResponsePolicy(
            mode=mode, intent=decision.intent, knowledge_status=coverage or "complete",
            scope_status=scope_status, source_type="manual", risk_level=risk,
            disclaimer_required=False, manual_citation_allowed=True, images_allowed=True,
            allow_knowledge_retrieval=True, allow_ai_fallback=False, style_profile="grounded",
        )
    high_risk = mode == INSUFFICIENT_EVIDENCE
    return ResponsePolicy(
        mode=mode, intent=decision.intent, knowledge_status=coverage or "no_evidence",
        scope_status=scope_status, source_type="ai", risk_level=risk,
        disclaimer_required=True, manual_citation_allowed=False, images_allowed=False,
        allow_knowledge_retrieval=True, allow_ai_fallback=not high_risk,
        style_profile="insufficient_evidence" if high_risk else "maintenance_ai",
    )
