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

logger = logging.getLogger(__name__)


RequestJson = Callable[..., Any]


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
        timeout_seconds: float = 3.0,
        request_json: RequestJson | None = None,
    ) -> None:
        settings = get_settings()
        self.base_url = str(base_url or settings.java_service_url).rstrip("/")
        self.internal_token = str(internal_token or settings.internal_token or "")
        self.timeout_seconds = max(float(timeout_seconds), 0.2)
        self._request_json = request_json
        self.last_error: str = ""

    async def fetch_candidates(
        self,
        contract: QueryContract,
        *,
        image_urls: list[str] | None = None,
        allowed_document_ids: tuple[str, ...] = (),
        allowed_section_ids: tuple[str, ...] = (),
        allowed_source_chunk_uids: tuple[str, ...] = (),
        allowed_evidence_refs: tuple[str, ...] = (),
        limit: int = 10,
        min_score: float = 0.70,
    ) -> tuple:
        """Fetch graph paths relevant to a structured query contract."""
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
            "limit": max(1, int(limit)),
            "minScore": float(min_score),
        }
        if image_urls:
            payload["imageUrls"] = list(image_urls)
        response = await self._request("POST", "/weixiu/path/candidates", json=payload)
        data = response.get("data") if isinstance(response, Mapping) else None
        if isinstance(data, Mapping):
            data = data.get("records") or data.get("candidates") or data.get("paths") or ()
        records = [dict(item) for item in (data or ()) if isinstance(item, Mapping)]
        return build_graph_candidates(records, query=contract.raw_query)

    @staticmethod
    def _text_list(values: Any) -> list[str]:
        return list(dict.fromkeys(
            str(value).strip() for value in values or () if str(value).strip()
        ))

    @staticmethod
    def _contract_payload(contract: QueryContract) -> dict[str, Any]:
        return {
            "rawQuery": contract.raw_query,
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
            logger.info("[graph-routing] candidate query unavailable: %s", exc)
            return {}

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
