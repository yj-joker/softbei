"""Fail-closed audit ensuring grounded answers use one selected document."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class EvidenceDocumentAudit:
    accepted: bool
    selected_document_id: str
    evidence_document_ids: tuple[str, ...]
    foreign_document_ids: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "selected_document_id": self.selected_document_id,
            "evidence_document_ids": list(self.evidence_document_ids),
            "foreign_document_ids": list(self.foreign_document_ids),
            "reason": self.reason,
        }


def _plain(value: Any) -> Any:
    return value.model_dump() if hasattr(value, "model_dump") else value


def _result_items(value: Any) -> Iterable[Mapping[str, Any]]:
    value = _plain(value)
    if isinstance(value, Mapping):
        for key in ("data", "results", "items", "qualified_evidence", "reference_evidence"):
            nested = value.get(key)
            if isinstance(nested, list):
                for item in nested:
                    item = _plain(item)
                    if isinstance(item, Mapping):
                        yield item
                return
        if value.get("metadata") or value.get("document_id"):
            yield value
    elif isinstance(value, list):
        for item in value:
            item = _plain(item)
            if isinstance(item, Mapping):
                yield item


class EvidenceDocumentGate:
    def audit(
        self,
        metadata: Mapping[str, Any] | None,
        *,
        selected_document_id: str,
    ) -> EvidenceDocumentAudit:
        selected = str(selected_document_id or "").strip()
        document_ids: list[str] = []

        def add(value: Any) -> None:
            document_id = str(value or "").strip()
            if document_id and document_id not in document_ids:
                document_ids.append(document_id)

        metadata = metadata or {}
        for document_id in metadata.get("_deterministic_answer_document_ids") or []:
            add(document_id)
        for raw_step in metadata.get("react_trace") or []:
            step = _plain(raw_step)
            if not isinstance(step, Mapping):
                continue
            for raw_call in step.get("tool_calls") or []:
                call = _plain(raw_call)
                if not isinstance(call, Mapping) or call.get("name") != "knowledge_retrieval":
                    continue
                result = call.get("result_data")
                if result is None:
                    result = call.get("data")
                if result is None:
                    result = call.get("result")
                for item in _result_items(result):
                    item_metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
                    add(item_metadata.get("document_id") or item.get("document_id"))

        foreign = tuple(document_id for document_id in document_ids if selected and document_id != selected)
        if not selected:
            reason = "selected_document_missing"
            accepted = False
        elif foreign:
            reason = "foreign_document_evidence"
            accepted = False
        elif document_ids:
            reason = "single_document_verified"
            accepted = True
        else:
            reason = "no_document_identity_in_trace"
            accepted = True
        return EvidenceDocumentAudit(
            accepted=accepted,
            selected_document_id=selected,
            evidence_document_ids=tuple(document_ids),
            foreign_document_ids=foreign,
            reason=reason,
        )
