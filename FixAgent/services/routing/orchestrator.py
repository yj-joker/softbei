"""Build one immutable route plan for streaming and non-streaming APIs."""

from __future__ import annotations

from typing import Iterable

from services.intent_router import IntentDecision
from services.retrieval.device_identity import DeviceCatalog, QueryContract
from services.retrieval.section_index import SectionRef
from services.routing.document_candidate_resolver import DocumentCandidateResolver
from services.routing.entity_resolver import EntityResolver
from services.routing.models import RouteAction, RoutePlan


class SemanticRoutingOrchestrator:
    def __init__(self) -> None:
        self.entity_resolver = EntityResolver()
        self.candidate_resolver = DocumentCandidateResolver()

    async def build_plan(
        self,
        *,
        query: str,
        decision: IntentDecision,
        catalog: DeviceCatalog,
        section_refs: Iterable[SectionRef],
        request_document_id: str = "",
        session_document_id: str = "",
        query_contract: QueryContract | None = None,
    ) -> RoutePlan:
        contract = query_contract or QueryContract.from_mapping(decision.model_dump(), raw_query=query)
        if decision.intent == "knowledge_inventory":
            return RoutePlan(
                action=RouteAction.KNOWLEDGE_INVENTORY,
                intent=decision.intent,
                task_action=decision.task_action,
                query_contract=contract,
                entity_role="knowledge_metadata",
                candidate_document_ids=(),
                selected_document_id="",
                allowed_tools=("knowledge_inventory",),
                answer_source="inventory_tool",
                allow_ai_fallback=False,
                reason="inventory_intent",
            )
        if decision.intent == "chat_social":
            return RoutePlan(
                action=RouteAction.GENERAL_AI,
                intent=decision.intent,
                task_action=decision.task_action,
                query_contract=contract,
                entity_role="chat_subject",
                candidate_document_ids=(),
                selected_document_id="",
                allowed_tools=(),
                answer_source="general_ai",
                allow_ai_fallback=True,
                reason="chat_intent",
            )

        refs = tuple(section_refs)
        entity = self.entity_resolver.resolve(contract, catalog, refs)
        candidates = self.candidate_resolver.resolve(
            entity.contract,
            catalog,
            refs,
            request_document_id=request_document_id,
            session_document_id=session_document_id,
        )
        allowed_tools: tuple[str, ...]
        answer_source: str
        allow_ai_fallback = candidates.action == RouteAction.AI_FALLBACK
        if candidates.action == RouteAction.GROUNDED_RETRIEVAL:
            configured = tuple(
                tool for tool in decision.allowed_tools
                if tool != "knowledge_inventory"
            )
            allowed_tools = configured or ("knowledge_retrieval",)
            answer_source = "selected_document"
        elif candidates.action == RouteAction.CLARIFY_DOCUMENT:
            allowed_tools = ()
            answer_source = "deterministic_clarification"
        else:
            allowed_tools = ()
            answer_source = "maintenance_ai"

        options = tuple(
            {
                "document_id": document_id,
                "display_name": (
                    catalog.document(document_id).device_name
                    if catalog.document(document_id) is not None
                    else document_id
                ),
            }
            for document_id in candidates.candidate_document_ids
        )
        return RoutePlan(
            action=candidates.action,
            intent=decision.intent,
            task_action=decision.task_action,
            query_contract=entity.contract,
            entity_role=entity.entity_role,
            candidate_document_ids=candidates.candidate_document_ids,
            selected_document_id=candidates.selected_document_id,
            allowed_tools=allowed_tools,
            answer_source=answer_source,
            allow_ai_fallback=allow_ai_fallback,
            reason=candidates.reason,
            clarification_options=options,
        )
