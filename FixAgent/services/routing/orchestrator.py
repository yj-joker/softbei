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
from services.clarification.models import KnowledgeCandidate
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
        preserve_query_contract: bool = False,
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
        graph_input = tuple(graph_candidates or ())
        entity = self.entity_resolver.resolve(
            contract,
            catalog,
            refs,
            graph_candidates=graph_input,
        )
        resolved_contract = contract if preserve_query_contract else entity.contract
        graph_document_ids = tuple(dict.fromkeys(
            str(
                getattr(candidate, "document_id", "")
                or ((candidate.get("documentId") or candidate.get("document_id") or "") if isinstance(candidate, dict) else "")
            ).strip()
            for candidate in graph_input
            if str(
                getattr(candidate, "document_id", "")
                or ((candidate.get("documentId") or candidate.get("document_id") or "") if isinstance(candidate, dict) else "")
            ).strip()
        ))
        candidates = self.candidate_resolver.resolve(
            resolved_contract,
            catalog,
            refs,
            request_document_id=request_document_id,
            session_document_id=session_document_id,
            graph_document_ids=graph_document_ids,
        )
        graph_values, explicit_graph_path = _narrow_explicit_graph_candidates(
            resolved_contract,
            graph_input,
        )
        multi_target_graph_scope = _multi_target_graph_scope(resolved_contract, graph_values)
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
        if explicit_graph_path is not None:
            scoped_refs = tuple(
                ref
                for ref in scoped_refs
                if _section_matches_graph_path(ref, explicit_graph_path)
            )
        section_candidates = build_section_candidates(scoped_refs, catalog, query=query)
        fused_candidates = CandidateFusionEngine().fuse(
            section_candidates,
            graph_values,
            resolved_contract,
        )
        diagnostic_clarification = bool(
            decision.intent == "fault_diagnosis"
            or decision.task_action == "find_cause"
            or (
                decision.intent == "maintenance_guidance"
                and resolved_contract.symptoms
            )
        )
        section_dimensions = unresolved_section_dimensions(section_candidates)
        graph_dimensions = unresolved_graph_dimensions(graph_values)
        unresolved_dimensions = (
            graph_dimensions
            if diagnostic_clarification
            else tuple(dict.fromkeys((*section_dimensions, *graph_dimensions)))
        )
        document_scope_clarification = candidates.action == RouteAction.CLARIFY_DOCUMENT
        should_decide = not multi_target_graph_scope and (
            bool(graph_values) or candidates.action == RouteAction.CLARIFY_DOCUMENT or (
            decision.task_action != "find_cause"
            and len(section_candidates) > 1
            and bool(unresolved_dimensions)
            )
        )
        if multi_target_graph_scope:
            graph_scope = multi_target_graph_scope
            scoped_document_id = str(multi_target_graph_scope.get("document_id") or "").strip()
            if scoped_document_id and catalog.document(scoped_document_id) is not None:
                candidates = DocumentCandidateResolution(
                    action=RouteAction.GROUNDED_RETRIEVAL,
                    candidate_document_ids=(scoped_document_id,),
                    selected_document_id=scoped_document_id,
                    reason="multi_target_same_document",
                )
        elif fused_candidates and should_decide:
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
        observable_graph_question = bool(
            combined_clarification
            and combined_clarification.should_clarify
            and combined_clarification.question
            and combined_clarification.question.dimension == "observable_symptom"
        )
        if (
            diagnostic_clarification
            and candidates.action in {RouteAction.CLARIFY, RouteAction.CLARIFY_DOCUMENT}
            and not observable_graph_question
        ):
            candidates = DocumentCandidateResolution(
                action=RouteAction.AI_FALLBACK,
                candidate_document_ids=candidates.candidate_document_ids,
                selected_document_id="",
                reason="diagnostic_ambiguity_without_observable_discriminator",
            )
        elif (
            diagnostic_clarification
            and len(graph_values) > 1
            and candidates.action == RouteAction.GROUNDED_RETRIEVAL
            and not observable_graph_question
        ):
            candidates = DocumentCandidateResolution(
                action=RouteAction.GROUNDED_RETRIEVAL,
                candidate_document_ids=candidates.candidate_document_ids,
                selected_document_id=candidates.selected_document_id,
                reason="diagnostic_ambiguity_without_observable_discriminator",
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
        elif candidates.action == RouteAction.CLARIFY:
            allowed_tools = ()
            answer_source = "deterministic_clarification"
        else:
            allowed_tools = ()
            answer_source = "maintenance_ai"

        if (
            candidates.action in {RouteAction.CLARIFY, RouteAction.CLARIFY_DOCUMENT}
            and combined_clarification
            and combined_clarification.should_clarify
            and combined_clarification.question
        ):
            options = tuple({
                "id": option.option_id,
                "label": option.label,
                "value": option.value,
                "candidate_ids": list(option.candidate_ids),
                "constraints": dict(option.constraints),
            } for option in combined_clarification.question.options)
        elif candidates.action == RouteAction.CLARIFY:
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
        else:
            options = ()
        return RoutePlan(
            action=candidates.action,
            intent=decision.intent,
            task_action=decision.task_action,
            query_contract=resolved_contract,
            entity_role=entity.entity_role,
            candidate_document_ids=candidates.candidate_document_ids,
            selected_document_id=candidates.selected_document_id,
            allowed_tools=allowed_tools,
            answer_source=answer_source,
            allow_ai_fallback=allow_ai_fallback,
            reason=candidates.reason,
            clarification_options=options,
            clarification_kind=(
                "graph_observation"
                if graph_values and observable_graph_question
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
                if candidates.action == RouteAction.CLARIFY
                and combined_clarification
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


def _narrow_explicit_graph_candidates(
    contract: QueryContract,
    candidates: tuple[KnowledgeCandidate, ...],
) -> tuple[tuple[KnowledgeCandidate, ...], KnowledgeCandidate | None]:
    """Bind a unique graph path when the query names its component and fault."""
    if not candidates:
        return (), None

    narrowed = candidates
    normalized_query = _normalized_text(contract.raw_query)
    exact_fault_matches = tuple(
        candidate
        for candidate in candidates
        if len(_normalized_text(candidate.dimensions.get("fault"))) >= 2
        and _normalized_text(candidate.dimensions.get("fault")) in normalized_query
    )
    if exact_fault_matches:
        # The raw query retains phrases that an LLM-produced component slot can
        # lose (for example "空调压缩机" becoming only "压缩机").  An exact
        # materialized fault phrase is therefore the strongest deterministic
        # scope signal and must be applied before component-based narrowing.
        narrowed = exact_fault_matches

    component_terms = tuple(
        dict.fromkeys(
            value
            for value in (
                _normalized_text(contract.raw_component_span),
                _normalized_text(contract.component),
                _normalized_text(contract.assembly_context),
                *(
                    _normalized_text(value)
                    for target in contract.targets
                    for value in (
                        target.raw_component_span,
                        target.component,
                        target.part_spec,
                        target.assembly_context,
                    )
                ),
            )
            if len(value) >= 2
        )
    )
    # An exact fault phrase is decisive only when it identifies one path.
    # Duplicate fault names across components still require the explicit
    # component named by the user to finish narrowing.
    if component_terms and len(narrowed) > 1:
        component_matches = tuple(
            candidate
            for candidate in narrowed
            if any(
                _texts_overlap(term, _normalized_text(candidate.dimensions.get("component")))
                for term in component_terms
            )
        )
        if component_matches:
            narrowed = component_matches

    if not exact_fault_matches:
        symptom_terms = tuple(
            dict.fromkeys(
                value
                for value in (
                    *(_normalized_text(item) for item in contract.symptoms),
                    normalized_query,
                )
                if len(value) >= 2
            )
        )
        fault_matches = tuple(
            candidate
            for candidate in narrowed
            if any(
                _texts_overlap(term, _normalized_text(candidate.dimensions.get("fault")))
                for term in symptom_terms
            )
        )
        if fault_matches:
            narrowed = fault_matches

    explicit_path = narrowed[0] if len(narrowed) == 1 and narrowed != candidates else None
    return narrowed, explicit_path


def _multi_target_graph_scope(
    contract: QueryContract,
    candidates: tuple[KnowledgeCandidate, ...],
) -> dict[str, object]:
    """Authorize the union of explicit targets only inside one device/manual."""
    if not candidates:
        return {}
    selected: list[KnowledgeCandidate] = []
    if len(contract.targets) >= 2:
        target_terms = [
            tuple(dict.fromkeys(
                value
                for value in (
                    _normalized_text(target.raw_component_span),
                    _normalized_text(target.component),
                    _normalized_text(target.part_spec),
                )
                if len(value) >= 2
            ))
            for target in contract.targets
        ]
        if any(not terms for terms in target_terms):
            return {}
        for terms in target_terms:
            matches = tuple(
                candidate
                for candidate in candidates
                if any(
                    _texts_overlap(term, _normalized_text(candidate.dimensions.get("component")))
                    for term in terms
                )
            )
            if not matches:
                return {}
            for candidate in matches:
                if candidate not in selected:
                    selected.append(candidate)
    else:
        query = str(contract.raw_query or "")
        if not any(marker in query for marker in ("是不是", "是否", "分别", "关系", "导致", "与")):
            return {}
        normalized_query = _normalized_text(query)
        for candidate in candidates:
            component = _normalized_text(candidate.dimensions.get("component"))
            if component and len(component) >= 2 and _texts_overlap(component, normalized_query):
                selected.append(candidate)
        if len({
            _normalized_text(candidate.dimensions.get("component"))
            for candidate in selected
            if _normalized_text(candidate.dimensions.get("component"))
        }) < 2:
            return {}

    document_ids = {candidate.document_id for candidate in selected if candidate.document_id}
    device_ids = {
        str(candidate.dimensions.get("device_id") or "").strip()
        for candidate in selected
        if str(candidate.dimensions.get("device_id") or "").strip()
    }
    if len(document_ids) != 1 or len(device_ids) > 1:
        return {}

    scope: dict[str, object] = {"document_id": next(iter(document_ids))}
    for dimension, key in (
        ("path_id", "allowed_path_ids"),
        ("device_id", "allowed_device_ids"),
        ("component_id", "allowed_component_ids"),
        ("fault_id", "allowed_fault_ids"),
    ):
        values = list(dict.fromkeys(
            str(candidate.dimensions.get(dimension) or "").strip()
            for candidate in selected
            if str(candidate.dimensions.get(dimension) or "").strip()
        ))
        if values:
            scope[key] = values
    return scope


def _section_matches_graph_path(ref: SectionRef, candidate: KnowledgeCandidate) -> bool:
    if candidate.document_id and ref.document_id != candidate.document_id:
        return False
    if candidate.section_id and ref.section_id == candidate.section_id:
        return True
    return bool(set(ref.evidence_refs) & set(candidate.source_chunk_uids))


def _normalized_text(value: object) -> str:
    return "".join(char for char in str(value or "").casefold() if char.isalnum())


def _texts_overlap(left: str, right: str) -> bool:
    return bool(left and right and (left in right or right in left))
