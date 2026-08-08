from __future__ import annotations

from typing import Any, Dict, List

from services.retrieval.graph_evidence import normalize_graph_response


def build_evidence_items(source_tool: str, result_payload: Any) -> List[Dict[str, Any]]:
    if source_tool == "knowledge_retrieval":
        return _knowledge_retrieval_evidence(result_payload)
    if source_tool == "java_graph_diagnosis_path":
        return _graph_path_evidence(result_payload)
    return []


def _knowledge_retrieval_evidence(result_payload: Any) -> List[Dict[str, Any]]:
    if isinstance(result_payload, dict):
        # Only qualified evidence is admissible for grounding. Tool envelopes may
        # contain reference summaries and excluded diagnostics, neither of which
        # can support a manual claim.
        result_payload = result_payload.get("results") or result_payload.get("qualified_evidence") or []
    if not isinstance(result_payload, list):
        return []

    evidence: List[Dict[str, Any]] = []
    for item in result_payload:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        content = str(item.get("content") or item.get("text") or "").strip()
        doc_id = str(item.get("id") or metadata.get("doc_id") or "")
        score = _to_float(item.get("relevance_score"), item.get("score"), item.get("raw_score"))
        chunk_type = str(metadata.get("chunk_type") or "").strip()
        evidence_type = {
            "table": "manual_table",
            "image": "manual_image",
            "image_summary": "manual_image",
        }.get(chunk_type, "manual_text")
        evidence.append({
            "evidence_id": f"knowledge_retrieval:{doc_id}" if doc_id else f"knowledge_retrieval:{len(evidence)}",
            "source_tool": "knowledge_retrieval",
            "source_type": "manual_chunk",
            "evidence_type": evidence_type,
            "document_id": metadata.get("document_id"),
            "manual_name": metadata.get("manual_name") or metadata.get("document_name"),
            "page": metadata.get("page") or metadata.get("page_number"),
            "chunk_id": metadata.get("chunk_id") or doc_id,
            "confidence": score,
            "content": content,
        })
    return evidence


def _graph_path_evidence(result_payload: Any) -> List[Dict[str, Any]]:
    if not isinstance(result_payload, dict):
        return []
    scope = result_payload.get("graph_scope") or result_payload.get("scope")
    batch = normalize_graph_response(
        result_payload,
        scope=scope if isinstance(scope, dict) else None,
    )
    return [
        evidence.to_ledger_entry()
        for evidence in batch.evidence
        if evidence.qualification == "qualified"
    ]


def _to_float(*values: Any) -> float:
    for value in values:
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0
