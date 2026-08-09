"""Structured graph candidate provider used by the semantic router."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import httpx

from config.settings import get_settings
from services.clarification.graph_candidates import build_graph_candidates
from services.retrieval.device_identity import QueryContract
from services.retrieval.graph_evidence import GraphEvidenceBatch, normalize_graph_response
from services.retrieval.graph_quality import GraphQualityTier, evaluate_graph_path_quality

logger = logging.getLogger(__name__)


RequestJson = Callable[..., Any]


def filter_candidates_by_resolved_scope(
    candidates: Any,
    resolved_scope: Any | None,
) -> tuple[Any, ...]:
    """Reapply a selected clarification scope on the Python boundary."""
    values = tuple(candidates or ())
    if resolved_scope is None:
        return values

    scalar_limits = (
        ("device_id", resolved_scope.allowed_device_ids),
        ("component_id", resolved_scope.allowed_component_ids),
        ("fault_id", resolved_scope.allowed_fault_ids),
        ("path_id", resolved_scope.allowed_path_ids),
    )
    allowed_nodes = set(resolved_scope.allowed_graph_node_ids)
    allowed_sections = set(resolved_scope.allowed_section_ids)
    allowed_chunks = set(resolved_scope.allowed_source_chunk_uids)
    allowed_refs = set(resolved_scope.allowed_evidence_refs)
    matched: list[Any] = []
    for candidate in values:
        dimensions = getattr(candidate, "dimensions", {}) or {}
        if (
            resolved_scope.document_id
            and str(getattr(candidate, "document_id", "") or "").strip()
            != resolved_scope.document_id
        ):
            continue
        if allowed_sections and str(getattr(candidate, "section_id", "") or "").strip() not in allowed_sections:
            continue
        if any(
            limits and str(dimensions.get(name) or "").strip() not in set(limits)
            for name, limits in scalar_limits
        ):
            continue
        candidate_nodes = {
            str(node_id).strip()
            for node_id in getattr(candidate, "node_ids", ()) or ()
            if str(node_id).strip()
        }
        if allowed_nodes and not candidate_nodes.issubset(allowed_nodes):
            continue
        candidate_chunks = set(getattr(candidate, "source_chunk_uids", ()) or ())
        if allowed_chunks and not candidate_chunks.intersection(allowed_chunks):
            continue
        candidate_refs = set(getattr(candidate, "evidence_refs", ()) or ())
        if allowed_refs and not candidate_refs.intersection(allowed_refs):
            continue
        matched.append(candidate)
    return tuple(matched)


class JavaGraphCandidateProvider:
    """Call Java graph endpoints and return typed clarification candidates.

    ``request_json`` is injectable for tests and for deployments that already
    have an HTTP gateway.  Network errors are deliberately non-fatal: graph
    clarification is an additional evidence source, while the document route
    remains responsible for deciding whether an AI fallback is allowed.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        internal_token: str | None = None,
        timeout_seconds: float | None = None,
        request_json: RequestJson | None = None,
    ) -> None:
        settings = get_settings()
        self.base_url = str(base_url or settings.java_service_url).rstrip("/")
        self.internal_token = str(internal_token or settings.internal_token or "")
        configured_timeout = (
            settings.graph_client_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        self.timeout_seconds = max(float(configured_timeout), 0.2)
        self.high_quality_threshold = float(settings.graph_quality_high_threshold)
        self.medium_quality_threshold = float(settings.graph_quality_medium_threshold)
        self._request_json = request_json
        self.last_error: str = ""
        self.last_error_code: str = ""
        self.retrieval_status: dict[str, Any] = {
            "status": "empty",
            "reason": "not_queried",
            "diagnostics": {},
        }

    async def fetch_candidates(
        self,
        contract: QueryContract,
        *,
        image_urls: list[str] | None = None,
        allowed_document_ids: tuple[str, ...] = (),
        allowed_section_ids: tuple[str, ...] = (),
        allowed_source_chunk_uids: tuple[str, ...] = (),
        allowed_evidence_refs: tuple[str, ...] = (),
        allowed_device_ids: tuple[str, ...] = (),
        allowed_component_ids: tuple[str, ...] = (),
        allowed_fault_ids: tuple[str, ...] = (),
        allowed_path_ids: tuple[str, ...] = (),
        allowed_graph_node_ids: tuple[str, ...] = (),
        limit: int = 10,
        min_score: float = 0.70,
    ) -> tuple:
        """Fetch graph paths relevant to a structured query contract."""
        self.last_error = ""
        self.last_error_code = ""
        intent = contract.intent or (
            "fault_diagnosis" if contract.task_action == "find_cause" else ""
        )
        # A diagnostic question can be phrased as a parameter lookup.  The
        # intent is authoritative; do not discard its graph route.
        if contract.task_action == "parameter_lookup" and intent != "fault_diagnosis":
            self.retrieval_status = {
                "status": "not_applicable",
                "reason": "parameter_lookup",
                "diagnostics": {"record_count": 0, "candidate_count": 0},
            }
            return ()
        if intent not in {
            "fault_diagnosis",
            "maintenance_guidance",
            "procedure_planning",
        }:
            self.retrieval_status = {
                "status": "not_applicable",
                "reason": "non_diagnostic_request",
                "diagnostics": {"record_count": 0, "candidate_count": 0},
            }
            return ()
        source_chunks = tuple(dict.fromkeys(
            str(value).strip()
            for value in (*allowed_source_chunk_uids, *allowed_evidence_refs)
            if str(value).strip() and not str(value).strip().lower().startswith("page:")
        ))
        payload = {
            "queryContract": self._contract_payload(contract),
            "allowedDocumentIds": self._text_list(allowed_document_ids),
            "allowedSectionIds": self._text_list(allowed_section_ids),
            "allowedSourceChunkUids": list(source_chunks),
            "allowedDeviceIds": self._text_list(allowed_device_ids),
            "allowedComponentIds": self._text_list(allowed_component_ids),
            "allowedFaultIds": self._text_list(allowed_fault_ids),
            "allowedPathIds": self._text_list(allowed_path_ids),
            "allowedGraphNodeIds": self._text_list(allowed_graph_node_ids),
            "limit": max(1, int(limit)),
            "minScore": float(min_score),
        }
        if image_urls:
            payload["imageUrls"] = list(image_urls)
        response = await self._request("POST", "/weixiu/path/candidates", json=payload)
        if self.last_error_code:
            reason = (
                "candidate_timeout"
                if self.last_error_code == "request_timeout"
                else "candidate_request_failed"
            )
            self.retrieval_status = {
                "status": "unavailable",
                "reason": reason,
                "diagnostics": {"record_count": 0, "candidate_count": 0},
            }
            return ()
        data = response.get("data") if isinstance(response, Mapping) else None
        status_payload = data if isinstance(data, Mapping) else response
        response_status = self._retrieval_status(status_payload)
        java_reason = str(status_payload.get("reason") or "") if isinstance(status_payload, Mapping) else ""
        java_diagnostics = (
            dict(status_payload.get("diagnostics") or {})
            if isinstance(status_payload, Mapping)
            and isinstance(status_payload.get("diagnostics"), Mapping)
            else {}
        )
        if isinstance(data, Mapping):
            data = data.get("records") or data.get("candidates") or data.get("paths") or ()
        records = [dict(item) for item in (data or ()) if isinstance(item, Mapping)]
        quality_decisions = [
            evaluate_graph_path_quality(
                record,
                high_threshold=self.high_quality_threshold,
                medium_threshold=self.medium_quality_threshold,
                trusted_query_structure=True,
            )
            for record in records
        ]
        candidates = build_graph_candidates(
            records,
            query=contract.raw_query,
            high_threshold=self.high_quality_threshold,
            medium_threshold=self.medium_quality_threshold,
        )
        status = response_status or ("found" if candidates else "empty")
        if records and not candidates:
            status = "filtered_out"
        self.retrieval_status = {
            "status": status,
            "reason": java_reason or ("" if status in {"found", "degraded"} else "no_matching_candidates"),
            "diagnostics": {
                **java_diagnostics,
                "record_count": len(records),
                "candidate_count": len(candidates),
                "filtered_count": max(0, len(records) - len(candidates)),
                "high_quality_count": sum(
                    item.tier is GraphQualityTier.HIGH for item in quality_decisions
                ),
                "medium_quality_count": sum(
                    item.tier is GraphQualityTier.MEDIUM for item in quality_decisions
                ),
                "low_quality_count": sum(
                    item.tier is GraphQualityTier.LOW for item in quality_decisions
                ),
            },
        }
        return candidates

    @staticmethod
    def _text_list(values: Any) -> list[str]:
        return list(dict.fromkeys(
            str(value).strip() for value in values or () if str(value).strip()
        ))

    @staticmethod
    def _contract_payload(contract: QueryContract) -> dict[str, Any]:
        return {
            "rawQuery": contract.raw_query,
            "intent": contract.intent,
            "deviceIdentity": contract.raw_device_span or contract.device_name,
            "component": contract.component,
            "partSpec": contract.part_spec,
            "symptoms": list(contract.symptoms),
            "operatingConditions": list(contract.operating_conditions),
            "taskAction": contract.task_action,
            "procedureAction": contract.action,
            "assemblyContext": contract.assembly_context,
            "orientation": contract.orientation,
            "requestedFields": list(contract.requested_fields),
        }

    async def reverse_devices(
        self,
        component_description: str,
        *,
        limit: int = 10,
        min_score: float = 0.70,
    ) -> list[dict[str, Any]]:
        if not str(component_description or "").strip():
            return []
        payload = await self._request(
            "GET",
            "/weixiu/path/reverse-device",
            params={
                "componentDescription": str(component_description).strip(),
                "limit": max(1, int(limit)),
                "minScore": float(min_score),
            },
        )
        data = payload.get("data") if isinstance(payload, Mapping) else None
        if isinstance(data, Mapping):
            data = data.get("records") or data.get("devices") or data.get("data")
        return [dict(item) for item in (data or ()) if isinstance(item, Mapping)]

    async def search_paths(
        self,
        *,
        keyword: str = "",
        fault_description: str = "",
        component_description: str = "",
        image_urls: list[str] | tuple[str, ...] = (),
        limit: int = 10,
        min_score: float = 0.70,
        allowed_device_ids: list[str] | tuple[str, ...] = (),
        allowed_component_ids: list[str] | tuple[str, ...] = (),
        allowed_fault_ids: list[str] | tuple[str, ...] = (),
        allowed_path_ids: list[str] | tuple[str, ...] = (),
    ) -> list[dict[str, Any]]:
        body: dict[str, Any] = {
            "page": 0,
            "size": max(1, int(limit)),
            "minScore": float(min_score),
        }
        for key, value in (
            ("keyword", keyword),
            ("faultDescription", fault_description),
            ("componentDescription", component_description),
        ):
            if str(value or "").strip():
                body[key] = str(value).strip()
        if image_urls:
            body["imageUrls"] = list(image_urls)
        for key, values in (
            ("allowedDeviceIds", allowed_device_ids),
            ("allowedComponentIds", allowed_component_ids),
            ("allowedFaultIds", allowed_fault_ids),
            ("allowedPathIds", allowed_path_ids),
        ):
            normalized = [str(value).strip() for value in values if str(value).strip()]
            if normalized:
                body[key] = normalized

        payload = await self._request("POST", "/weixiu/path/search", json=body)
        data = payload.get("data") if isinstance(payload, Mapping) else None
        if isinstance(data, Mapping):
            data = data.get("records") or data.get("paths") or ()
        return [dict(item) for item in (data or ()) if isinstance(item, Mapping)]

    async def retrieve_path_evidence(
        self,
        *,
        keyword: str = "",
        fault_description: str = "",
        component_description: str = "",
        image_urls: list[str] | tuple[str, ...] = (),
        limit: int = 10,
        min_score: float = 0.70,
        allowed_device_ids: list[str] | tuple[str, ...] = (),
        allowed_component_ids: list[str] | tuple[str, ...] = (),
        allowed_fault_ids: list[str] | tuple[str, ...] = (),
        allowed_path_ids: list[str] | tuple[str, ...] = (),
    ) -> GraphEvidenceBatch:
        """Retrieve and normalize path evidence under a server-owned scope."""
        self.last_error = ""
        self.last_error_code = ""
        body: dict[str, Any] = {
            "page": 0,
            "size": max(1, int(limit)),
            "minScore": float(min_score),
        }
        # The caller has already enforced a non-empty server scope. Preserve
        # only the populated dimensions so unspecified dimensions do not turn
        # into explicit empty allow-lists during evidence normalization.
        for key, values in (
            ("allowedDeviceIds", allowed_device_ids),
            ("allowedComponentIds", allowed_component_ids),
            ("allowedFaultIds", allowed_fault_ids),
            ("allowedPathIds", allowed_path_ids),
        ):
            normalized = self._text_list(values)
            if normalized:
                body[key] = normalized
        for key, value in (
            ("keyword", keyword),
            ("faultDescription", fault_description),
            ("componentDescription", component_description),
        ):
            if str(value or "").strip():
                body[key] = str(value).strip()
        if image_urls:
            body["imageUrls"] = list(image_urls)

        payload = await self._request("POST", "/weixiu/path/search", json=body)
        if self.last_error_code:
            reason = (
                "graph_path_timeout"
                if self.last_error_code == "request_timeout"
                else "graph_path_request_failed"
            )
            return normalize_graph_response({
                "evidence_status": "unavailable",
                "reason": reason,
                "raw_records": [],
            })

        data = payload.get("data") if isinstance(payload, Mapping) else None
        data = data if isinstance(data, Mapping) else {}
        records = data.get("records") or data.get("paths") or []
        status = self._retrieval_status(data) or ("found" if records else "empty")
        scope = {
            snake: body[camel]
            for snake, camel in (
                ("allowed_device_ids", "allowedDeviceIds"),
                ("allowed_component_ids", "allowedComponentIds"),
                ("allowed_fault_ids", "allowedFaultIds"),
                ("allowed_path_ids", "allowedPathIds"),
            )
            if camel in body
        }
        return normalize_graph_response(
            {
                "evidence_status": status,
                "reason": data.get("reason") or "",
                "diagnostics": data.get("diagnostics") or {},
                "raw_records": records,
            },
            scope=scope,
            high_threshold=self.high_quality_threshold,
            medium_threshold=self.medium_quality_threshold,
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> Mapping[str, Any]:
        url = f"{self.base_url}{path}"
        headers = dict(kwargs.pop("headers", {}) or {})
        if self.internal_token:
            headers.setdefault("X-Internal-Token", self.internal_token)
        kwargs["headers"] = headers
        try:
            if self._request_json is not None:
                result = self._request_json(method, url, **kwargs)
                if inspect.isawaitable(result):
                    result = await result
                return result if isinstance(result, Mapping) else {}
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.request(method, url, **kwargs)
                response.raise_for_status()
                result = response.json()
                return result if isinstance(result, Mapping) else {}
        except Exception as exc:
            self.last_error = str(exc)
            self.last_error_code = (
                "request_timeout"
                if isinstance(exc, (TimeoutError, httpx.TimeoutException))
                else "request_failed"
            )
            logger.info("[graph-routing] candidate query unavailable: %s", exc)
            return {}

    @staticmethod
    def _retrieval_status(payload: Any) -> str:
        if not isinstance(payload, Mapping):
            return ""
        value = str(
            payload.get("retrievalStatus")
            or payload.get("retrieval_status")
            or payload.get("status")
            or ""
        ).strip()
        return value if value in {
            "found", "empty", "not_applicable", "degraded", "unavailable", "filtered_out"
        } else ""

    @staticmethod
    def _component_description(contract: QueryContract) -> str:
        values: list[str] = []
        for target in contract.targets:
            values.extend((target.raw_component_span, target.component, target.part_spec))
        values.extend((contract.raw_component_span, contract.component, contract.part_spec))
        values.extend((contract.assembly_context, contract.orientation))
        return " ".join(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


_provider: JavaGraphCandidateProvider | None = None


def get_graph_candidate_provider() -> JavaGraphCandidateProvider:
    global _provider
    if _provider is None:
        _provider = JavaGraphCandidateProvider()
    return _provider


__all__ = ["JavaGraphCandidateProvider", "get_graph_candidate_provider"]
