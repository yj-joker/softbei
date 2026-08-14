"""Server-controlled graph pre-retrieval for diagnostic requests."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from services.retrieval.graph_evidence import GraphAuthorizationContext, GraphEvidenceBatch
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
        route_policy = route_plan.get("graph_policy")
        if isinstance(route_policy, Mapping) and "pre_retrieval_enabled" in route_policy:
            pre_retrieval_enabled = bool(route_policy.get("pre_retrieval_enabled"))
            policy_reason = str(route_policy.get("reason") or "route_policy")
        else:
            policy = decide_graph_use(
                variant,
                {**dict(contract), "intent": route_plan.get("intent"), "task_action": route_plan.get("task_action")},
            )
            pre_retrieval_enabled = policy.pre_retrieval_enabled
            policy_reason = policy.reason
        if not pre_retrieval_enabled:
            return _status_batch("not_applicable", policy_reason)

        task_action = str(route_plan.get("task_action") or "").strip()
        intent = str(route_plan.get("intent") or "").strip()
        action = str(route_plan.get("action") or "").strip()
        if action in {"clarify_document", "clarify"}:
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
        authorization_context = _authorization_context(route_plan, contract, scope)
        return await self.provider.retrieve_path_evidence(
            keyword=keyword,
            fault_description=fault_description,
            component_description=component_description,
            image_urls=list(image_urls or ()),
            allowed_path_ids=list(scope.get("allowed_path_ids") or ()),
            allowed_device_ids=list(scope.get("allowed_device_ids") or ()),
            allowed_component_ids=list(scope.get("allowed_component_ids") or ()),
            allowed_fault_ids=list(scope.get("allowed_fault_ids") or ()),
            authorization_context=authorization_context,
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


def _authorization_context(
    route_plan: Mapping[str, Any],
    contract: Mapping[str, Any],
    scope: Mapping[str, Any],
) -> GraphAuthorizationContext | None:
    selected_document_id = str(route_plan.get("selected_document_id") or "").strip()
    scoped_document_id = str(scope.get("document_id") or "").strip()
    allowed_paths = _text_tuple(scope.get("allowed_path_ids"))
    allowed_sections = _text_tuple(scope.get("allowed_section_ids"))
    allowed_chunks = _text_tuple(scope.get("allowed_source_chunk_uids"))
    if (
        not selected_document_id
        or scoped_document_id != selected_document_id
        or not allowed_paths
        or not allowed_sections
        or not allowed_chunks
    ):
        return None

    canonical_device = str(route_plan.get("authorized_device_identity") or "").strip()
    if not canonical_device and str(contract.get("identity_resolution") or "") == "catalog_exact":
        canonical_device = str(contract.get("device_name") or "").strip()
    if not canonical_device:
        return None
    document_version = str(scope.get("document_version") or "").strip()
    return GraphAuthorizationContext(
        canonical_device_identity=canonical_device,
        document_ids=(selected_document_id,),
        document_versions=((document_version,) if document_version else ()),
        section_ids=allowed_sections,
        source_chunk_uids=allowed_chunks,
    )


def _text_join(values: Any) -> str:
    if not isinstance(values, (list, tuple, set)):
        values = (values,)
    return " ".join(dict.fromkeys(
        str(value).strip() for value in values if str(value or "").strip()
    ))


def _text_tuple(values: Any) -> tuple[str, ...]:
    source = values if isinstance(values, (list, tuple, set)) else (values,)
    return tuple(dict.fromkeys(
        str(value).strip() for value in source if str(value or "").strip()
    ))


__all__ = ["GraphPreRetrievalService"]
