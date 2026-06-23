from __future__ import annotations

from typing import Any, Dict, List


def build_evidence_items(source_tool: str, result_payload: Any) -> List[Dict[str, Any]]:
    if source_tool == "knowledge_retrieval":
        return _knowledge_retrieval_evidence(result_payload)
    if source_tool == "java_graph_diagnosis_path":
        return _graph_path_evidence(result_payload)
    return []


def _knowledge_retrieval_evidence(result_payload: Any) -> List[Dict[str, Any]]:
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

    records = result_payload.get("raw_records") or []
    if not isinstance(records, list):
        return []

    evidence: List[Dict[str, Any]] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        solutions = record.get("solutions") or []
        first_solution = solutions[0] if isinstance(solutions, list) and solutions else {}
        solution = ""
        if isinstance(first_solution, dict):
            solution = str(first_solution.get("title") or first_solution.get("name") or "").strip()
        solution = solution or str(record.get("solutionTitle") or "").strip()
        device = record.get("deviceName")
        component = record.get("componentName")
        fault = record.get("faultName")
        content_parts = [part for part in [device, component, fault, solution] if part]
        evidence.append({
            "evidence_id": f"java_graph_diagnosis_path:{index}",
            "source_tool": "java_graph_diagnosis_path",
            "source_type": "knowledge_graph",
            "evidence_type": "graph_path",
            "device": device,
            "component": component,
            "fault": fault,
            "solution": solution,
            "confidence": _to_float(record.get("matchScore"), record.get("score")),
            "content": " -> ".join(str(part) for part in content_parts),
        })
    return evidence


def _to_float(*values: Any) -> float:
    for value in values:
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0
