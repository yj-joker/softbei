"""Deterministic evidence coverage and auditable knowledge-source ledger."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from services.retrieval.aspects import QuestionAspect


@dataclass(frozen=True)
class EvidenceCoverage:
    status: str
    supported_aspect_ids: tuple[str, ...]
    missing_aspect_ids: tuple[str, ...]
    conflict_aspect_ids: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "supported_aspect_ids": list(self.supported_aspect_ids),
            "missing_aspect_ids": list(self.missing_aspect_ids),
            "conflict_aspect_ids": list(self.conflict_aspect_ids),
            "reason": self.reason,
        }


def determine_coverage(
    bundle: Mapping[str, Any],
    *,
    aspects: Sequence[QuestionAspect] | None = None,
    scope_status: str | None = None,
) -> EvidenceCoverage:
    aspect_ids = tuple(aspect.aspect_id for aspect in (aspects or ()))
    support_rows = bundle.get("aspect_support") or []
    supported = tuple(
        str(row.get("aspect_id"))
        for row in support_rows
        if isinstance(row, Mapping) and row.get("supported") and row.get("aspect_id")
    )
    missing = tuple(aspect_id for aspect_id in aspect_ids if aspect_id not in supported)
    conflicts = bundle.get("conflict_eligible") or bundle.get("conflicts") or []
    conflict_aspects = tuple(
        dict.fromkeys(
            str(aspect_id)
            for conflict in conflicts
            if isinstance(conflict, Mapping)
            for aspect_id in (conflict.get("aspect_ids") or aspect_ids)
            if aspect_id
        )
    )

    if scope_status == "out_of_scope" or not aspect_ids:
        status, reason = "unsupported", "out_of_scope" if scope_status == "out_of_scope" else "no_valid_aspects"
    elif conflicts:
        status, reason = "conflict", "unresolved_conflict"
    elif not bundle.get("qualified_evidence"):
        status, reason = "unsupported", "zero_qualified_evidence"
    elif not missing:
        status, reason = "complete", "all_aspects_supported"
    else:
        status, reason = "partial", "missing_aspects"
    return EvidenceCoverage(status, supported, missing, conflict_aspects, reason)


class EvidenceLedger:
    """Append-only, stable and serializable evidence identities."""

    def __init__(self, entries: Iterable[Mapping[str, Any]] | None = None) -> None:
        self.entries: list[dict[str, Any]] = []
        self._seen: set[str] = set()
        for entry in entries or ():
            self.append(entry)

    def append(self, entry: Mapping[str, Any]) -> bool:
        normalized = _json_copy(entry)
        evidence_id = str(normalized.get("evidence_id") or "").strip()
        source_type = str(normalized.get("source_type") or "").strip()
        if not evidence_id or source_type not in {"manual", "domain_rule", "graph"}:
            return False
        if evidence_id in self._seen:
            return False
        self._seen.add(evidence_id)
        self.entries.append(normalized)
        return True

    def canonical_json(self) -> str:
        return json.dumps(self.entries, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @classmethod
    def from_react_trace(cls, metadata: Mapping[str, Any] | None) -> "EvidenceLedger":
        ledger = cls()
        trace = metadata.get("react_trace") if isinstance(metadata, Mapping) else None
        if not isinstance(trace, list):
            return ledger
        for step in trace:
            calls = step.get("tool_calls") if isinstance(step, Mapping) else None
            if not isinstance(calls, list):
                continue
            for call in calls:
                if not isinstance(call, Mapping):
                    continue
                payload = _tool_payload(call)
                name = str(call.get("name") or "")
                if name == "knowledge_retrieval":
                    _append_manual_entries(ledger, payload)
                elif name == "domain_rule_engine":
                    _append_rule_entry(ledger, payload)
                elif name == "java_graph_diagnosis_path":
                    _append_graph_entries(ledger, payload)
        return ledger


def _json_copy(value: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(dict(value), ensure_ascii=False, sort_keys=True, default=str))


def _tool_payload(call: Mapping[str, Any]) -> Any:
    for key in ("result_data", "data", "result"):
        if key in call and call.get(key) is not None:
            return call.get(key)
    return None


def _manual_items(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, Mapping):
        items: list[Mapping[str, Any]] = []
        for key, qualification in (("qualified_evidence", "qualified"), ("reference_evidence", "reference_only"), ("results", "")):
            values = payload.get(key)
            if isinstance(values, list):
                for value in values:
                    if isinstance(value, Mapping):
                        items.append({**value, "_ledger_qualification": qualification})
        return items
    return [item for item in payload or [] if isinstance(item, Mapping)] if isinstance(payload, list) else []


def _append_manual_entries(ledger: EvidenceLedger, payload: Any) -> None:
    for item in _manual_items(payload):
        metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
        document_id = str(metadata.get("document_id") or item.get("document_id") or "").strip()
        chunk_id = str(metadata.get("chunk_id") or item.get("chunk_id") or item.get("id") or item.get("doc_id") or "").strip()
        if not document_id or not chunk_id:
            continue
        qualification = str(metadata.get("qualification") or item.get("qualification") or item.get("_ledger_qualification") or "")
        ledger.append({
            "evidence_id": f"manual:{document_id}:{chunk_id}",
            "source_type": "manual",
            "text": _entry_text(item),
            "qualification": qualification,
            "source": {
                "document_id": document_id,
                "document_version": str(metadata.get("document_version") or ""),
                "chunk_id": chunk_id,
                "page": metadata.get("page") if metadata.get("page") is not None else metadata.get("page_number"),
            },
        })


def _append_rule_entry(ledger: EvidenceLedger, payload: Any) -> None:
    if not isinstance(payload, Mapping):
        return
    rule = payload.get("rule") if isinstance(payload.get("rule"), Mapping) else {}
    rule_id = str(rule.get("rule_id") or payload.get("rule_id") or "").strip()
    status = str(rule.get("status") or payload.get("status") or "").strip()
    if not rule_id or status != "active":
        return
    ledger.append({
        "evidence_id": f"domain_rule:{rule_id}",
        "source_type": "domain_rule",
        "text": _entry_text(payload) or _entry_text(rule),
        "qualification": "qualified",
        "source": {"rule_id": rule_id, "status": status, "evidence_sources": list(payload.get("evidence_sources") or [])},
    })


def _append_graph_entries(ledger: EvidenceLedger, payload: Any) -> None:
    if not isinstance(payload, Mapping):
        return
    records = payload.get("raw_records") or payload.get("records") or []
    for record in records if isinstance(records, list) else []:
        if not isinstance(record, Mapping):
            continue
        path_ids = _as_list(record.get("pathIds") or record.get("path_ids") or record.get("pathId") or record.get("path_id"))
        node_ids = _as_list(record.get("nodeIds") or record.get("node_ids") or record.get("nodeId") or record.get("node_id"))
        if not path_ids and not node_ids:
            continue
        stable_id = path_ids[0] if path_ids else ":".join(node_ids)
        ledger.append({
            "evidence_id": f"graph:{stable_id}",
            "source_type": "graph",
            "text": _entry_text(record),
            "qualification": "qualified",
            "source": {
                "path_ids": path_ids,
                "node_ids": node_ids,
                "relationship_types": _as_list(record.get("relationshipTypes") or record.get("relationship_types")),
            },
        })


def _entry_text(item: Mapping[str, Any]) -> str:
    return "\n".join(str(item.get(key)).strip() for key in ("content", "text", "summary", "caption", "image_summary") if item.get(key))


def _as_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)] if value not in (None, "") else []
