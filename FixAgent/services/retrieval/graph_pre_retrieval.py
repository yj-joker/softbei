"""Server-controlled graph pre-retrieval for diagnostic requests."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from services.retrieval.graph_evidence import GraphEvidenceBatch
from services.routing.graph_candidate_provider import get_graph_candidate_provider
from services.routing.graph_policy import decide_graph_use


class GraphPreRetrievalService:
    def __init__(self, *, provider=None) -> None:
        self.provider = provider or get_graph_candidate_provider()

    async def retrieve(
        self,
        *,
        rag_variant: str,
        route_plan: Mapping[str, Any],
        graph_scope: Mapping[str, Any],
        image_urls: list[str] | tuple[str, ...] = (),
    ) -> GraphEvidenceBatch:
        variant = str(rag_variant or "production").strip().lower()
        contract = route_plan.get("query_contract")
        contract = contract if isinstance(contract, Mapping) else {}
        policy = decide_graph_use(
            variant,
            {**dict(contract), "intent": route_plan.get("intent"), "task_action": route_plan.get("task_action")},
        )
        if not policy.pre_retrieval_enabled:
            return _status_batch("not_applicable", policy.reason)

        task_action = str(route_plan.get("task_action") or "").strip()
        intent = str(route_plan.get("intent") or "").strip()
        action = str(route_plan.get("action") or "").strip()
        if task_action in {"list_items", "procedure_lookup"}:
            return _status_batch("not_applicable", "non_diagnostic_request")
        if task_action == "parameter_lookup" and intent != "fault_diagnosis":
            return _status_batch("not_applicable", "non_diagnostic_request")
        if intent not in {
            "fault_diagnosis",
            "maintenance_guidance",
            "procedure_planning",
        } or action in {"clarify_document", "clarify"}:
            return _status_batch("not_applicable", "non_diagnostic_request")

        scope = dict(graph_scope or {})
        allowed_keys = (
            "allowed_path_ids",
            "allowed_device_ids",
            "allowed_component_ids",
            "allowed_fault_ids",
        )
        symptoms = _text_join(contract.get("symptoms"))
        fault_description = symptoms or str(contract.get("raw_query") or "").strip()
        component_description = _text_join((
            contract.get("component"),
            contract.get("part_spec"),
            contract.get("assembly_context"),
            contract.get("orientation"),
        ))
        if not any(scope.get(key) for key in allowed_keys):
            return _status_batch("filtered_out", "empty_graph_scope")
        keyword = ""
        if not (scope.get("allowed_device_ids") or scope.get("allowed_path_ids")):
            keyword = str(
                contract.get("raw_device_span")
                or contract.get("device_name")
                or contract.get("device_identity")
                or ""
            ).strip()
        return await self.provider.retrieve_path_evidence(
            keyword=keyword,
            fault_description=fault_description,
            component_description=component_description,
            image_urls=list(image_urls or ()),
            allowed_path_ids=list(scope.get("allowed_path_ids") or ()),
            allowed_device_ids=list(scope.get("allowed_device_ids") or ()),
            allowed_component_ids=list(scope.get("allowed_component_ids") or ()),
            allowed_fault_ids=list(scope.get("allowed_fault_ids") or ()),
        )


def _status_batch(status: str, reason: str) -> GraphEvidenceBatch:
    return GraphEvidenceBatch(
        status=status,
        reason=reason,
        evidence=(),
        diagnostics={
            "record_count": 0,
            "qualified_count": 0,
            "routing_only_count": 0,
            "rejected_count": 0,
        },
    )


def _text_join(values: Any) -> str:
    if not isinstance(values, (list, tuple, set)):
        values = (values,)
    return " ".join(dict.fromkeys(
        str(value).strip() for value in values if str(value or "").strip()
    ))


__all__ = ["GraphPreRetrievalService"]
