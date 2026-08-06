"""Build one immutable route plan for streaming and non-streaming APIs."""

from __future__ import annotations

from typing import Iterable

from services.intent_router import IntentDecision
from services.retrieval.device_identity import DeviceCatalog, QueryContract
from services.retrieval.section_index import SectionRef
from services.routing.document_candidate_resolver import DocumentCandidateResolver
from services.routing.entity_resolver import EntityResolver
from services.routing.models import (
    DocumentCandidateResolution,
    RouteAction,
    RoutePlan,
)
from services.clarification.candidates import build_section_candidates, unresolved_section_dimensions
from services.clarification.graph_candidates import unresolved_graph_dimensions
from services.clarification.fusion import CandidateFusionEngine
from services.clarification.policy import ClarificationDecisionEngine, calculate_risk_level


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
        graph_candidates: Iterable = (),
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
        graph_values = tuple(graph_candidates or ())
        combined_clarification = None
        selected_graph_candidate = None
        graph_scope: dict[str, object] = {}
        risk_level = calculate_risk_level(
            task_action=decision.task_action,
            model_hint=contract.risk_level,
            operation_intent=bool(decision.operation_intent),
        )
        selected_section_id = ""
        scoped_document_ids = set(candidates.candidate_document_ids)
        if candidates.selected_document_id:
            scoped_document_ids.add(candidates.selected_document_id)
        scoped_refs = tuple(
            ref for ref in refs
            if not scoped_document_ids or ref.document_id in scoped_document_ids
        )
        section_candidates = build_section_candidates(scoped_refs, catalog, query=query)
        fused_candidates = CandidateFusionEngine().fuse(
            section_candidates,
            graph_values,
            entity.contract,
        )
        unresolved_dimensions = tuple(dict.fromkeys(
            (*unresolved_section_dimensions(section_candidates),
             *unresolved_graph_dimensions(graph_values))
        ))
        document_scope_clarification = candidates.action == RouteAction.CLARIFY_DOCUMENT
        should_decide = bool(graph_values) or candidates.action == RouteAction.CLARIFY_DOCUMENT or (
            decision.task_action != "find_cause"
            and len(section_candidates) > 1
            and bool(unresolved_dimensions)
        )
        if fused_candidates and should_decide:
            combined_clarification = ClarificationDecisionEngine().decide(
                fused_candidates,
                risk_level=risk_level,
                unresolved_dimensions=unresolved_dimensions,
            )
            if combined_clarification.should_clarify:
                selected_section_id = ""
                candidates = DocumentCandidateResolution(
                    action=(
                        RouteAction.CLARIFY_DOCUMENT
                        if document_scope_clarification and not graph_values
                        else RouteAction.CLARIFY
                    ),
                    candidate_document_ids=tuple(dict.fromkeys(
                        candidate.document_id
                        for candidate in fused_candidates
                        if candidate.document_id
                    )),
                    selected_document_id="",
                    reason="fused_candidate_ambiguity",
                )
            elif combined_clarification.selected_candidate_id:
                selected = next(
                    (
                        candidate for candidate in fused_candidates
                        if candidate.candidate_id == combined_clarification.selected_candidate_id
                    ),
                    None,
                )
                if selected is not None:
                    selected_section_id = selected.section_id
                    if selected.source_kind in {"graph", "fused"}:
                        selected_graph_candidate = selected
                        graph_scope = _candidate_scope(selected)
                    if (
                        selected.document_id
                        and catalog.document(selected.document_id) is not None
                        and candidates.action in {RouteAction.AI_FALLBACK, RouteAction.CLARIFY_DOCUMENT}
                    ):
                        candidates = DocumentCandidateResolution(
                            action=RouteAction.GROUNDED_RETRIEVAL,
                            candidate_document_ids=(selected.document_id,),
                            selected_document_id=selected.document_id,
                            reason="unique_fused_candidate",
                        )
        elif candidates.action == RouteAction.GROUNDED_RETRIEVAL and candidates.selected_document_id:
            same_document_sections = tuple(
                candidate for candidate in section_candidates
                if candidate.document_id == candidates.selected_document_id
            )
            if len(same_document_sections) == 1:
                selected_section_id = same_document_sections[0].section_id
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
        elif candidates.action == RouteAction.CLARIFY:
            allowed_tools = ()
            answer_source = "deterministic_clarification"
        else:
            allowed_tools = ()
            answer_source = "maintenance_ai"

        if combined_clarification and combined_clarification.should_clarify and combined_clarification.question:
            options = tuple({
                "id": option.option_id,
                "label": option.label,
                "value": option.value,
                "candidate_ids": list(option.candidate_ids),
                "constraints": dict(option.constraints),
            } for option in combined_clarification.question.options)
        else:
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
            clarification_kind=(
                "graph_scope"
                if graph_values and combined_clarification and combined_clarification.should_clarify
                else "document_selection"
                if document_scope_clarification and not graph_values
                else "slot_disambiguation"
                if combined_clarification and combined_clarification.should_clarify
                else "document_selection"
                if candidates.action == RouteAction.CLARIFY
                else ""
            ),
            clarification_question=(
                combined_clarification.question.prompt
                if combined_clarification
                and combined_clarification.should_clarify
                and combined_clarification.question
                and graph_values
                else ""
            ),
            selected_section_id=selected_section_id,
            graph_scope=graph_scope,
            selected_graph_candidate_id=(
                selected_graph_candidate.candidate_id
                if selected_graph_candidate is not None
                else ""
            ),
        )


def _candidate_scope(candidate) -> dict[str, object]:
    """Convert one graph candidate to an opaque, server-owned allow-list."""
    dimensions = candidate.dimensions
    scope: dict[str, object] = {}
    mapping = {
        "device_id": "allowed_device_ids",
        "component_id": "allowed_component_ids",
        "fault_id": "allowed_fault_ids",
        "path_id": "allowed_path_ids",
    }
    for dimension, key in mapping.items():
        value = str(dimensions.get(dimension) or "").strip()
        if value:
            scope[key] = [value]
    if candidate.document_id:
        scope["document_id"] = candidate.document_id
    if candidate.section_id:
        scope["allowed_section_ids"] = [candidate.section_id]
    if candidate.document_version:
        scope["document_version"] = candidate.document_version
    if candidate.evidence_refs:
        scope["allowed_evidence_refs"] = list(candidate.evidence_refs)
    if candidate.source_chunk_uids:
        scope["allowed_source_chunk_uids"] = list(candidate.source_chunk_uids)
    if candidate.pages:
        scope["pages"] = list(candidate.pages)
    if candidate.node_ids:
        scope["allowed_graph_node_ids"] = list(candidate.node_ids)
    return scope
