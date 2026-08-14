"""Central policy for GraphRAG applicability and experiment isolation."""

from dataclasses import dataclass
from typing import Any, Mapping


_FULL_ALIASES = frozenset({"graph", "graph_full", "production"})
_DIAGNOSTIC_INTENTS = frozenset(
    {"fault_diagnosis", "relation_disambiguation", "multi_hop", "cross_document"}
)
_DIAGNOSTIC_ACTIONS = frozenset(
    {"find_cause", "diagnose_fault", "relation_disambiguation", "multi_hop", "cross_document"}
)
_MANUAL_ONLY_ACTIONS = frozenset(
    {
        "parameter_lookup",
        "procedure_lookup",
        "list_items",
        "show_image",
        "image_lookup",
        "safety_lookup",
    }
)
_GRAPH_CLAIMS = ("device_identity", "component_ownership", "fault_relation", "verified_solution")


@dataclass(frozen=True)
class GraphUseDecision:
    candidate_enabled: bool
    pre_retrieval_enabled: bool
    may_influence_route: bool
    may_enter_evidence: bool
    graph_review_enabled: bool
    allowed_claim_types: tuple[str, ...]
    reason: str


def decide_graph_use(
    rag_variant: str,
    contract: Mapping[str, Any] | None,
) -> GraphUseDecision:
    variant = str(rag_variant or "production").strip().lower()
    if variant == "graph":
        variant = "graph_full"
    payload = contract if isinstance(contract, Mapping) else {}
    intent = str(payload.get("intent") or "").strip().lower()
    action = str(payload.get("task_action") or payload.get("action") or "").strip().lower()

    if variant == "no_graph":
        return GraphUseDecision(False, False, False, False, False, (), "rag_variant_no_graph")
    diagnostic_intent = intent in _DIAGNOSTIC_INTENTS
    diagnostic_parameter_lookup = diagnostic_intent and action == "parameter_lookup"
    if action in _MANUAL_ONLY_ACTIONS and not diagnostic_parameter_lookup:
        return GraphUseDecision(False, False, False, False, False, (), "manual_only_request")
    symptoms = payload.get("symptoms")
    symptom_driven_repair = bool(
        action == "repair_guidance"
        and isinstance(symptoms, (list, tuple, set, frozenset))
        and any(str(item or "").strip() for item in symptoms)
    )
    confirmed_fault_procedure = bool(
        intent == "procedure_planning"
        and action == "formal_procedure"
        and str(payload.get("component") or "").strip()
        and isinstance(symptoms, (list, tuple, set, frozenset))
        and any(str(item or "").strip() for item in symptoms)
    )
    structured_fault = bool(
        str(payload.get("component") or payload.get("raw_component_span") or "").strip()
        and str(payload.get("fault") or payload.get("raw_fault_span") or "").strip()
    )
    applicable = bool(
        diagnostic_intent
        or action in _DIAGNOSTIC_ACTIONS
        or symptom_driven_repair
        or confirmed_fault_procedure
        or structured_fault
    )
    if not applicable:
        return GraphUseDecision(False, False, False, False, False, (), "non_diagnostic_request")
    if variant == "graph_shadow":
        return GraphUseDecision(True, True, False, False, False, _GRAPH_CLAIMS, "shadow_audit_only")
    if variant in _FULL_ALIASES:
        reason = "structured_fault_graph_enabled" if structured_fault and not (
            diagnostic_intent
            or action in _DIAGNOSTIC_ACTIONS
            or symptom_driven_repair
            or confirmed_fault_procedure
        ) else "diagnostic_graph_enabled"
        return GraphUseDecision(True, True, True, True, True, _GRAPH_CLAIMS, reason)
    return GraphUseDecision(False, False, False, False, False, (), "unknown_variant")


__all__ = ["GraphUseDecision", "decide_graph_use"]
