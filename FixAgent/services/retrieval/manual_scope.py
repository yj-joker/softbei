"""Server-authoritative scope for manual evidence retrieval."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


MANUAL_SCOPE_SCALAR_KEYS = (
    "document_id",
    "document_version",
    "device_type",
    "parent_section_id",
)
MANUAL_SCOPE_LIST_KEYS = (
    "allowed_section_ids",
    "allowed_evidence_refs",
    "allowed_source_chunk_uids",
    "pages",
)


def _unique_text(values: Any) -> list[str]:
    source = values if isinstance(values, (list, tuple, set)) else [values]
    return list(dict.fromkeys(
        str(value).strip() for value in source if str(value or "").strip()
    ))


def _unique_pages(values: Any) -> list[int]:
    source = values if isinstance(values, (list, tuple, set)) else [values]
    pages: list[int] = []
    for value in source:
        try:
            page = int(value)
        except (TypeError, ValueError):
            continue
        if page > 0 and page not in pages:
            pages.append(page)
    return pages


def _valid_scope(
    scope: Mapping[str, Any] | None,
    document_id: str,
) -> dict[str, Any]:
    payload = dict(scope or {})
    scoped_document = str(payload.get("document_id") or "").strip()
    if not payload or not scoped_document or scoped_document != document_id:
        return {}
    return payload


def build_manual_retrieval_scope(
    *,
    selected_document_id: str,
    selected_section_id: str = "",
    resolved_scope: Mapping[str, Any] | None = None,
    graph_scope: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the one manual-retrieval boundary owned by the server."""
    document_id = str(selected_document_id or "").strip()
    if not document_id:
        return {}
    resolved = _valid_scope(resolved_scope, document_id)
    graph = _valid_scope(graph_scope, document_id)

    scope: dict[str, Any] = {
        "server_authoritative": True,
        "document_id": document_id,
        # Graph display names are not Redis identity metadata.
        "device_type": "",
    }
    graph_version = str(graph.get("document_version") or "").strip()
    if graph_version:
        scope["document_version"] = graph_version

    section_id = str(selected_section_id or "").strip()
    if section_id:
        scope["parent_section_id"] = section_id

    for key in ("allowed_section_ids", "allowed_source_chunk_uids"):
        values = _unique_text(resolved.get(key)) or _unique_text(graph.get(key))
        if key == "allowed_section_ids" and section_id:
            values = [section_id]
        if values:
            scope[key] = values
    evidence_refs = _unique_text(resolved.get("allowed_evidence_refs"))
    if evidence_refs:
        scope["allowed_evidence_refs"] = evidence_refs
    pages = _unique_pages(resolved.get("pages")) or _unique_pages(graph.get("pages"))
    if pages:
        scope["pages"] = pages

    canonical = json.dumps(
        manual_scope_tool_kwargs(scope),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    scope["scope_fingerprint"] = f"manual-scope:{digest}"
    return scope


def manual_scope_tool_kwargs(scope: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = dict(scope or {})
    result: dict[str, Any] = {}
    for key in MANUAL_SCOPE_SCALAR_KEYS:
        value = payload.get(key)
        if value not in (None, ""):
            result[key] = value
    for key in MANUAL_SCOPE_LIST_KEYS:
        values = list(payload.get(key) or ())
        if values:
            result[key] = values
    return result


def apply_authoritative_manual_scope(
    kwargs: Mapping[str, Any] | None,
    scope: Mapping[str, Any] | None,
) -> dict[str, Any]:
    effective = dict(kwargs or {})
    if not scope:
        return effective
    for key in (*MANUAL_SCOPE_SCALAR_KEYS, *MANUAL_SCOPE_LIST_KEYS):
        effective.pop(key, None)
    effective.update(manual_scope_tool_kwargs(scope))
    return effective


def build_manual_retrieval_kwargs(
    query: str,
    scope: Mapping[str, Any] | None,
    *,
    top_k: int = 5,
) -> dict[str, Any]:
    return {
        "query": str(query or "").strip(),
        "top_k": int(top_k),
        **manual_scope_tool_kwargs(scope),
    }


__all__ = [
    "MANUAL_SCOPE_LIST_KEYS",
    "MANUAL_SCOPE_SCALAR_KEYS",
    "apply_authoritative_manual_scope",
    "build_manual_retrieval_kwargs",
    "build_manual_retrieval_scope",
    "manual_scope_tool_kwargs",
]
