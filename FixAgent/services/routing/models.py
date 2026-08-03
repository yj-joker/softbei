"""Immutable contracts shared by semantic routing and API execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from services.retrieval.device_identity import QueryContract


class RouteAction(str, Enum):
    GENERAL_AI = "general_ai"
    KNOWLEDGE_INVENTORY = "knowledge_inventory"
    GROUNDED_RETRIEVAL = "grounded_retrieval"
    CLARIFY_DOCUMENT = "clarify_document"
    AI_FALLBACK = "ai_fallback"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(frozen=True)
class EntityResolution:
    contract: QueryContract
    entity_role: str
    reason: str
    matched_section_document_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class DocumentCandidateResolution:
    action: RouteAction
    candidate_document_ids: tuple[str, ...]
    selected_document_id: str
    reason: str


@dataclass(frozen=True)
class RoutePlan:
    action: RouteAction
    intent: str
    task_action: str
    query_contract: QueryContract
    entity_role: str
    candidate_document_ids: tuple[str, ...]
    selected_document_id: str
    allowed_tools: tuple[str, ...]
    answer_source: str
    allow_ai_fallback: bool
    reason: str
    clarification_options: tuple[dict[str, Any], ...] = ()

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RoutePlan":
        query_data = payload.get("query_contract") if isinstance(payload.get("query_contract"), dict) else {}
        return cls(
            action=RouteAction(str(payload.get("action") or RouteAction.AI_FALLBACK.value)),
            intent=str(payload.get("intent") or ""),
            task_action=str(payload.get("task_action") or ""),
            query_contract=QueryContract.from_mapping(
                query_data,
                raw_query=str(query_data.get("raw_query") or ""),
            ),
            entity_role=str(payload.get("entity_role") or "unspecified"),
            candidate_document_ids=tuple(str(item) for item in payload.get("candidate_document_ids") or ()),
            selected_document_id=str(payload.get("selected_document_id") or ""),
            allowed_tools=tuple(str(item) for item in payload.get("allowed_tools") or ()),
            answer_source=str(payload.get("answer_source") or ""),
            allow_ai_fallback=bool(payload.get("allow_ai_fallback")),
            reason=str(payload.get("reason") or ""),
            clarification_options=tuple(
                dict(item) for item in payload.get("clarification_options") or ()
                if isinstance(item, dict)
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["action"] = self.action.value
        data["query_contract"] = self.query_contract.to_dict()
        data["candidate_document_ids"] = list(self.candidate_document_ids)
        data["allowed_tools"] = list(self.allowed_tools)
        data["clarification_options"] = [dict(item) for item in self.clarification_options]
        return data
