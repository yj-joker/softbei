"""Normalize Java graph-path responses into auditable graph evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from typing import Any, Mapping

from services.retrieval.graph_quality import (
    DEFAULT_HIGH_THRESHOLD,
    DEFAULT_MEDIUM_THRESHOLD,
    GraphQualityTier,
    evaluate_graph_path_quality,
)


GRAPH_RETRIEVAL_STATUSES = {
    "found",
    "empty",
    "not_applicable",
    "degraded",
    "unavailable",
    "filtered_out",
}


@dataclass(frozen=True)
class GraphAuthorizationContext:
    """Independent facts allowed to authorize graph claims.

    Candidate path/node IDs intentionally do not belong here because a graph
    candidate cannot prove its own scope membership.
    """

    canonical_device_identity: str = ""
    document_ids: tuple[str, ...] = ()
    document_versions: tuple[str, ...] = ()
    section_ids: tuple[str, ...] = ()
    source_chunk_uids: tuple[str, ...] = ()


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
    quality_tier: str
    quality_reasons: tuple[str, ...]
    graph_revision: str
    provenance_status: str
    source: GraphEvidenceSource
    rejection_reasons: tuple[str, ...]
    claim_types: tuple[str, ...]
    supports_aspect_ids: tuple[str, ...]
    text: str
    qualification_basis: str = ""
    authorized_claim_types: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["source_type"] = "graph"
        result["node_ids"] = list(self.node_ids)
        result["relationship_types"] = list(self.relationship_types)
        result["source"] = self.source.to_dict()
        result["rejection_reasons"] = list(self.rejection_reasons)
        result["quality_reasons"] = list(self.quality_reasons)
        result["claim_types"] = list(self.claim_types)
        result["authorized_claim_types"] = list(self.authorized_claim_types)
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
            "quality_tier": self.quality_tier,
            "quality_reasons": list(self.quality_reasons),
            "graph_revision": self.graph_revision,
            "provenance_status": self.provenance_status,
            "source": self.source.to_dict(),
            "rejection_reasons": list(self.rejection_reasons),
            "claim_types": list(self.claim_types),
            "authorized_claim_types": list(self.authorized_claim_types),
            "qualification_basis": self.qualification_basis,
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
    high_threshold: float = DEFAULT_HIGH_THRESHOLD,
    medium_threshold: float = DEFAULT_MEDIUM_THRESHOLD,
    authorization_context: GraphAuthorizationContext | None = None,
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
    evaluated: list[GraphEvidence] = []
    for record in records:
        if isinstance(record, Mapping):
            evaluated.extend(_normalize_record(
                record,
                scope or {},
                high_threshold,
                medium_threshold,
                authorization_context,
            ))

    normalized = [
        item for item in evaluated
        if item.quality_tier != GraphQualityTier.LOW.value
    ]

    if status in {"found", "degraded"} and evaluated and not normalized:
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
        "rejected_count": sum(item.quality_tier == "low" for item in evaluated),
        "high_quality_count": sum(item.quality_tier == "high" for item in evaluated),
        "medium_quality_count": sum(item.quality_tier == "medium" for item in evaluated),
        "low_quality_count": sum(item.quality_tier == "low" for item in evaluated),
        "discarded_count": len(evaluated) - len(normalized),
        "discard_reasons": sorted({
            reason
            for item in evaluated
            if item.quality_tier == "low"
            for reason in item.quality_reasons
        }),
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
        "semanticScore": item.get("confidence"),
        "qualityTier": item.get("quality_tier"),
    }


def _normalize_record(
    record: Mapping[str, Any],
    scope: Mapping[str, Any],
    high_threshold: float,
    medium_threshold: float,
    authorization_context: GraphAuthorizationContext | None,
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
    quality = evaluate_graph_path_quality(
        record,
        high_threshold=high_threshold,
        medium_threshold=medium_threshold,
    )
    confidence = quality.semantic_score

    rejected = _scope_rejections(path_id, device, component, fault, scope)
    authorization_reasons = _authorization_reasons(
        device=device,
        source=source,
        authorization_context=authorization_context,
    )
    core_ids = (device["id"], component["id"], fault["id"])
    routing_reasons: list[str] = list(quality.reasons)
    if not path_id:
        rejected.append("missing_path_id")
    elif all(core_ids) and not _path_identity_matches(path_id, node_ids, core_ids):
        rejected.append("path_identity_mismatch")
    if not all(core_ids) or not all((device["name"], component["name"], fault["name"])):
        routing_reasons.append("incomplete_core_identity")
    stable_node_identity = _has_stable_node_identity(node_ids)
    if all(core_ids) and not stable_node_identity and not set(core_ids).issubset(set(node_ids)):
        rejected.append("incomplete_node_identity")
    if not {"OWNS", "CAUSES"}.issubset(set(relationship_types)):
        rejected.append("missing_required_relationship")
    if authorization_context is not None and authorization_reasons:
        rejected.extend(authorization_reasons)
    structural_exact = bool(
        authorization_context is not None
        and not authorization_reasons
        and not rejected
        and provenance_status == "complete"
        and source.source_chunk_uids
        and {"OWNS", "CAUSES"}.issubset(set(relationship_types))
    )
    if rejected or quality.tier is GraphQualityTier.LOW:
        qualification = "rejected"
        quality_tier = GraphQualityTier.LOW.value
        reasons = tuple(dict.fromkeys(rejected + routing_reasons))
        qualification_basis = "rejected"
        authorized_claim_types: tuple[str, ...] = ()
    elif quality.tier is GraphQualityTier.MEDIUM:
        qualification = "qualified" if structural_exact else "routing_only"
        quality_tier = GraphQualityTier.MEDIUM.value
        reasons = tuple(dict.fromkeys(
            routing_reasons
            + ([] if authorization_context is not None else ["independent_authorization_missing"])
        ))
        qualification_basis = "structural_exact" if structural_exact else "routing_only"
        authorized_claim_types = (
            ("component_ownership", "fault_relation") if structural_exact else ()
        )
    else:
        qualification = "qualified"
        quality_tier = GraphQualityTier.HIGH.value
        reasons = ()
        qualification_basis = "semantic_high"
        authorized_claim_types = (
            "device_identity",
            "component_ownership",
            "fault_relation",
        )

    claim_types = (
        ("component_ownership", "fault_relation")
        if qualification_basis == "structural_exact"
        else ("device_identity", "component_ownership", "fault_relation")
    )

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
        quality_tier=quality_tier,
        quality_reasons=reasons,
        graph_revision=graph_revision,
        provenance_status=provenance_status,
        source=source,
        rejection_reasons=reasons,
        claim_types=claim_types,
        supports_aspect_ids=("device", "component", "fault-cause"),
        text=_path_text(device, component, fault),
        qualification_basis=qualification_basis,
        authorized_claim_types=authorized_claim_types,
    )
    output = [base]
    if qualification != "qualified" or qualification_basis != "semantic_high":
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
            quality_tier=GraphQualityTier.HIGH.value,
            quality_reasons=(),
            graph_revision=graph_revision,
            provenance_status=provenance_status,
            source=source,
            rejection_reasons=(),
            claim_types=("verified_solution",),
            supports_aspect_ids=("treatment",),
            text=f"{_path_text(device, component, fault)} -> HAS_SOLUTION -> {_text(solution.get('title'))}",
            qualification_basis="semantic_high",
            authorized_claim_types=("verified_solution",),
        ))
    return output


def _authorization_reasons(
    *,
    device: Mapping[str, str],
    source: GraphEvidenceSource,
    authorization_context: GraphAuthorizationContext | None,
) -> list[str]:
    if authorization_context is None:
        return []
    reasons: list[str] = []
    canonical_device = _text(authorization_context.canonical_device_identity).casefold()
    if not canonical_device:
        reasons.append("authorization_device_missing")
    elif _text(device.get("name")).casefold() != canonical_device:
        reasons.append("authorization_device_mismatch")

    allowed_documents = set(_text_tuple(authorization_context.document_ids))
    if not allowed_documents:
        reasons.append("authorization_document_missing")
    elif source.document_id not in allowed_documents:
        reasons.append("authorization_document_mismatch")

    allowed_versions = set(_text_tuple(authorization_context.document_versions))
    if allowed_versions and source.document_version not in allowed_versions:
        reasons.append("authorization_document_version_mismatch")

    allowed_sections = set(_text_tuple(authorization_context.section_ids))
    if allowed_sections and source.section_id not in allowed_sections:
        reasons.append("authorization_section_mismatch")

    allowed_chunks = set(_text_tuple(authorization_context.source_chunk_uids))
    if allowed_chunks and not allowed_chunks.intersection(source.source_chunk_uids):
        reasons.append("authorization_source_anchor_mismatch")
    return reasons


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


def _path_identity_matches(
    path_id: str,
    node_ids: tuple[str, ...],
    core_ids: tuple[str, ...],
) -> bool:
    legacy = f"kgpath:{core_ids[0]}:{core_ids[1]}:{core_ids[2]}"
    if path_id == legacy:
        return True
    if len(node_ids) != 3 or not all(node_ids):
        return False
    if not _has_stable_node_identity(node_ids):
        return False
    digest = hashlib.sha256("\x1f".join(node_ids).encode("utf-8")).hexdigest()
    return path_id == f"kgpath:{digest}"


def _has_stable_node_identity(node_ids: tuple[str, ...]) -> bool:
    stable_types = ("kg:device:", "kg:component:", "kg:fault:")
    return len(node_ids) == 3 and all(
        value.startswith(prefix) for value, prefix in zip(node_ids, stable_types)
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
