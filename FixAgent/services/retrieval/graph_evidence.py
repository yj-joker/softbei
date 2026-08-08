"""Normalize Java graph-path responses into auditable graph evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from typing import Any, Mapping


GRAPH_RETRIEVAL_STATUSES = {
    "found",
    "empty",
    "not_applicable",
    "degraded",
    "unavailable",
    "filtered_out",
}


@dataclass(frozen=True)
class GraphEvidenceSource:
    document_id: str = ""
    document_version: str = ""
    section_id: str = ""
    source_chunk_uids: tuple[str, ...] = ()
    pages: tuple[int, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "document_version": self.document_version,
            "section_id": self.section_id,
            "source_chunk_uids": list(self.source_chunk_uids),
            "pages": list(self.pages),
        }


@dataclass(frozen=True)
class GraphEvidence:
    evidence_id: str
    qualification: str
    path_id: str
    node_ids: tuple[str, ...]
    relationship_types: tuple[str, ...]
    device: dict[str, str]
    component: dict[str, str]
    fault: dict[str, str]
    solution: dict[str, Any]
    confidence: float
    graph_revision: str
    provenance_status: str
    source: GraphEvidenceSource
    rejection_reasons: tuple[str, ...]
    claim_types: tuple[str, ...]
    supports_aspect_ids: tuple[str, ...]
    text: str

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["source_type"] = "graph"
        result["node_ids"] = list(self.node_ids)
        result["relationship_types"] = list(self.relationship_types)
        result["source"] = self.source.to_dict()
        result["rejection_reasons"] = list(self.rejection_reasons)
        result["claim_types"] = list(self.claim_types)
        result["supports_aspect_ids"] = list(self.supports_aspect_ids)
        return result

    def to_ledger_entry(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source_type": "graph",
            "text": self.text,
            "qualification": self.qualification,
            "path_id": self.path_id,
            "node_ids": list(self.node_ids),
            "relationship_types": list(self.relationship_types),
            "device": dict(self.device),
            "component": dict(self.component),
            "fault": dict(self.fault),
            "solution": dict(self.solution),
            "confidence": self.confidence,
            "graph_revision": self.graph_revision,
            "provenance_status": self.provenance_status,
            "source": self.source.to_dict(),
            "rejection_reasons": list(self.rejection_reasons),
            "claim_types": list(self.claim_types),
            "supports_aspect_ids": list(self.supports_aspect_ids),
        }


@dataclass(frozen=True)
class GraphEvidenceBatch:
    status: str
    reason: str
    evidence: tuple[GraphEvidence, ...]
    diagnostics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "evidence": [item.to_dict() for item in self.evidence],
            "diagnostics": dict(self.diagnostics),
        }


def normalize_graph_response(
    payload: Mapping[str, Any] | None,
    *,
    scope: Mapping[str, Any] | None = None,
    min_match_score: float = 1.0,
) -> GraphEvidenceBatch:
    """Normalize a Java envelope or graph-tool result without broadening scope."""
    raw_payload = payload if isinstance(payload, Mapping) else {}
    data = raw_payload.get("data") if isinstance(raw_payload.get("data"), Mapping) else raw_payload
    records = data.get("raw_records") or data.get("records") or []
    if not records and isinstance(data.get("evidence"), list):
        records = [
            _record_from_normalized(item)
            for item in data.get("evidence") or []
            if isinstance(item, Mapping)
        ]
    records = records if isinstance(records, list) else []
    status = _status(data, records)
    reason = _text(data.get("reason") or raw_payload.get("reason"))
    normalized: list[GraphEvidence] = []
    for record in records:
        if isinstance(record, Mapping):
            normalized.extend(_normalize_record(record, scope or {}, min_match_score))

    if status in {"found", "degraded"} and normalized and all(
        item.qualification == "rejected" for item in normalized
    ):
        status = "filtered_out"
        reason = reason or "all_records_rejected"
    if status in {"found", "degraded"} and not normalized:
        status = "empty"
        reason = reason or "no_diagnostic_records"

    diagnostics = dict(data.get("diagnostics") or {}) if isinstance(data.get("diagnostics"), Mapping) else {}
    diagnostics.update({
        "record_count": len(records),
        "qualified_count": sum(item.qualification == "qualified" for item in normalized),
        "routing_only_count": sum(item.qualification == "routing_only" for item in normalized),
        "rejected_count": sum(item.qualification == "rejected" for item in normalized),
    })
    return GraphEvidenceBatch(status, reason, tuple(normalized), diagnostics)


def _record_from_normalized(item: Mapping[str, Any]) -> dict[str, Any]:
    source = item.get("source") if isinstance(item.get("source"), Mapping) else {}
    device = item.get("device") if isinstance(item.get("device"), Mapping) else {}
    component = item.get("component") if isinstance(item.get("component"), Mapping) else {}
    fault = item.get("fault") if isinstance(item.get("fault"), Mapping) else {}
    solution = item.get("solution") if isinstance(item.get("solution"), Mapping) else {}
    return {
        "pathId": item.get("path_id"),
        "nodeIds": item.get("node_ids"),
        "relationshipTypes": item.get("relationship_types"),
        "deviceId": device.get("id"),
        "deviceName": device.get("name"),
        "componentId": component.get("id"),
        "componentName": component.get("name"),
        "faultId": fault.get("id"),
        "faultName": fault.get("name"),
        "faultSeverity": fault.get("severity"),
        "solutions": [solution] if solution else [],
        "documentId": source.get("document_id"),
        "documentVersion": source.get("document_version"),
        "sectionId": source.get("section_id"),
        "sourceChunkUids": source.get("source_chunk_uids"),
        "pages": source.get("pages"),
        "graphRevision": item.get("graph_revision"),
        "provenanceStatus": item.get("provenance_status"),
        "matchScore": item.get("confidence"),
    }


def _normalize_record(
    record: Mapping[str, Any],
    scope: Mapping[str, Any],
    min_match_score: float,
) -> list[GraphEvidence]:
    path_id = _text(record.get("pathId") or record.get("path_id"))
    node_ids = _text_tuple(record.get("nodeIds") or record.get("node_ids"))
    relationship_types = _text_tuple(
        record.get("relationshipTypes") or record.get("relationship_types")
    )
    device = {"id": _text(record.get("deviceId")), "name": _text(record.get("deviceName"))}
    component = {"id": _text(record.get("componentId")), "name": _text(record.get("componentName"))}
    fault = {
        "id": _text(record.get("faultId")),
        "name": _text(record.get("faultName")),
        "severity": _text(record.get("faultSeverity")),
    }
    source = GraphEvidenceSource(
        document_id=_text(record.get("documentId")),
        document_version=_text(record.get("documentVersion")),
        section_id=_text(record.get("sectionId")),
        source_chunk_uids=_text_tuple(record.get("sourceChunkUids")),
        pages=_int_tuple(record.get("pages")),
    )
    graph_revision = _text(record.get("graphRevision"))
    provenance_status = _text(record.get("provenanceStatus")) or "missing"
    confidence = _number(record.get("matchScore"), record.get("score"))

    rejected = _scope_rejections(path_id, device, component, fault, scope)
    core_ids = (device["id"], component["id"], fault["id"])
    routing_reasons: list[str] = []
    if not path_id:
        rejected.append("missing_path_id")
    elif all(core_ids) and path_id != f"kgpath:{device['id']}:{component['id']}:{fault['id']}":
        rejected.append("path_identity_mismatch")
    if not all(core_ids) or not all((device["name"], component["name"], fault["name"])):
        routing_reasons.append("incomplete_core_identity")
    if all(core_ids) and not set(core_ids).issubset(set(node_ids)):
        rejected.append("incomplete_node_identity")
    if not {"OWNS", "CAUSES"}.issubset(set(relationship_types)):
        rejected.append("missing_required_relationship")
    if (
        provenance_status != "complete"
        or not source.document_id
        or not source.document_version
        or not source.section_id
        or not source.source_chunk_uids
        or not source.pages
        or not graph_revision
    ):
        routing_reasons.append("incomplete_provenance")
    if confidence < min_match_score:
        routing_reasons.append("below_answer_threshold")

    if rejected:
        qualification = "rejected"
        reasons = tuple(dict.fromkeys(rejected + routing_reasons))
    elif routing_reasons:
        qualification = "routing_only"
        reasons = tuple(dict.fromkeys(routing_reasons))
    else:
        qualification = "qualified"
        reasons = ()

    rejected_identity = hashlib.sha256(
        "|".join((*core_ids, path_id)).encode("utf-8")
    ).hexdigest()[:20]
    base = GraphEvidence(
        evidence_id=(
            f"graph:{path_id}:none"
            if path_id.startswith("kgpath:")
            else f"graph:rejected:{rejected_identity}:none"
        ),
        qualification=qualification,
        path_id=path_id,
        node_ids=node_ids,
        relationship_types=relationship_types,
        device=device,
        component=component,
        fault=fault,
        solution={},
        confidence=confidence,
        graph_revision=graph_revision,
        provenance_status=provenance_status,
        source=source,
        rejection_reasons=reasons,
        claim_types=("device_identity", "component_ownership", "fault_relation"),
        supports_aspect_ids=("device", "component", "fault-cause"),
        text=_path_text(device, component, fault),
    )
    output = [base]
    if qualification != "qualified":
        return output

    if "HAS_SOLUTION" not in relationship_types:
        return output
    solutions = record.get("solutions") if isinstance(record.get("solutions"), list) else []
    for raw_solution in solutions:
        if not isinstance(raw_solution, Mapping):
            continue
        solution = dict(raw_solution)
        solution_id = _text(solution.get("id"))
        active = (_text(solution.get("status")) or "active") == "active"
        verified = solution.get("verified") is True
        if not solution_id or not active or not verified:
            continue
        output.append(GraphEvidence(
            evidence_id=f"graph:{path_id}:{solution_id}",
            qualification="qualified",
            path_id=path_id,
            node_ids=node_ids + (solution_id,),
            relationship_types=relationship_types,
            device=device,
            component=component,
            fault=fault,
            solution=solution,
            confidence=confidence,
            graph_revision=graph_revision,
            provenance_status=provenance_status,
            source=source,
            rejection_reasons=(),
            claim_types=("verified_solution",),
            supports_aspect_ids=("treatment",),
            text=f"{_path_text(device, component, fault)} -> HAS_SOLUTION -> {_text(solution.get('title'))}",
        ))
    return output


def _scope_rejections(
    path_id: str,
    device: Mapping[str, str],
    component: Mapping[str, str],
    fault: Mapping[str, str],
    scope: Mapping[str, Any],
) -> list[str]:
    checks = (
        ("path", path_id, "allowed_path_ids", "allowedPathIds"),
        ("device", device.get("id", ""), "allowed_device_ids", "allowedDeviceIds"),
        ("component", component.get("id", ""), "allowed_component_ids", "allowedComponentIds"),
        ("fault", fault.get("id", ""), "allowed_fault_ids", "allowedFaultIds"),
    )
    reasons: list[str] = []
    for label, actual, snake_key, camel_key in checks:
        scope_key = snake_key if snake_key in scope else camel_key if camel_key in scope else ""
        if not scope_key:
            continue
        allowed = _text_tuple(scope.get(scope_key))
        if not allowed:
            if "empty_allowed_scope" not in reasons:
                reasons.append("empty_allowed_scope")
            continue
        if allowed and actual not in allowed:
            reasons.append(f"outside_allowed_{label}_ids")
    return reasons


def _status(payload: Mapping[str, Any], records: list[Any]) -> str:
    value = _text(
        payload.get("retrievalStatus")
        or payload.get("retrieval_status")
        or payload.get("evidence_status")
        or payload.get("status")
    )
    if value in GRAPH_RETRIEVAL_STATUSES:
        return value
    return "found" if records else "empty"


def _path_text(
    device: Mapping[str, str],
    component: Mapping[str, str],
    fault: Mapping[str, str],
) -> str:
    return (
        f"{device.get('name') or device.get('id')} -> OWNS -> "
        f"{component.get('name') or component.get('id')} -> CAUSES -> "
        f"{fault.get('name') or fault.get('id')}"
    )


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _text_tuple(value: Any) -> tuple[str, ...]:
    values = value if isinstance(value, (list, tuple, set)) else (() if value in (None, "") else (value,))
    return tuple(dict.fromkeys(_text(item) for item in values if _text(item)))


def _int_tuple(value: Any) -> tuple[int, ...]:
    values = value if isinstance(value, (list, tuple, set)) else (() if value in (None, "") else (value,))
    result: list[int] = []
    for item in values:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number not in result:
            result.append(number)
    return tuple(result)


def _number(*values: Any) -> float:
    for value in values:
        try:
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0
