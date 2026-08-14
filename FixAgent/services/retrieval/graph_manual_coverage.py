"""Question-level coverage contract for graph relations plus manual actions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Iterable


_ACTION_MARKERS = (
    "检查", "检测", "测量", "更换", "拆卸", "拆下", "安装", "调整",
    "修复", "清洗", "紧固", "润滑", "排除", "维修",
)


@dataclass(frozen=True)
class GraphManualCoverage:
    component: bool
    fault: bool
    solution: bool
    provenance: bool

    @property
    def complete(self) -> bool:
        return self.component and self.fault and self.solution and self.provenance

    def to_dict(self) -> dict[str, bool]:
        return {
            "component": self.component,
            "fault": self.fault,
            "solution": self.solution,
            "provenance": self.provenance,
            "complete": self.complete,
        }


def evaluate_graph_manual_coverage(
    *,
    query: Any,
    graph_evidence: Iterable[Any],
    manual_evidence: Iterable[Any],
) -> GraphManualCoverage:
    """Require graph relation identity and a same-document manual repair action."""
    del query  # Selection relevance is decided before evidence enters this contract.
    graph_rows = [_mapping(item) for item in graph_evidence]
    qualified_graph = [row for row in graph_rows if _qualified_graph_relation(row)]
    component = any(_named(row.get("component")) for row in qualified_graph)
    fault = any(_named(row.get("fault")) for row in qualified_graph)

    matched_manual: list[Mapping[str, Any]] = []
    for item in manual_evidence:
        manual = _mapping(item)
        if not _qualified_manual_action(manual):
            continue
        if any(_same_document_version(manual, graph) for graph in qualified_graph):
            matched_manual.append(manual)

    graph_source_complete = any(_complete_graph_source(row) for row in qualified_graph)
    solution = bool(matched_manual)
    provenance = graph_source_complete and solution
    return GraphManualCoverage(component, fault, solution, provenance)


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        payload = to_dict()
        return payload if isinstance(payload, Mapping) else {}
    return {}


def _qualified_graph_relation(row: Mapping[str, Any]) -> bool:
    relationships = {str(value) for value in row.get("relationship_types") or ()}
    return (
        row.get("qualification") == "qualified"
        and row.get("provenance_status") == "complete"
        and {"OWNS", "CAUSES"}.issubset(relationships)
    )


def _qualified_manual_action(row: Mapping[str, Any]) -> bool:
    if row.get("qualification") != "qualified":
        return False
    text = " ".join(str(row.get(key) or "") for key in ("text", "content"))
    return any(marker in text for marker in _ACTION_MARKERS)


def _named(value: Any) -> bool:
    return isinstance(value, Mapping) and bool(str(value.get("name") or "").strip())


def _source(row: Mapping[str, Any]) -> Mapping[str, Any]:
    value = row.get("source")
    return value if isinstance(value, Mapping) else {}


def _same_document_version(
    manual: Mapping[str, Any],
    graph: Mapping[str, Any],
) -> bool:
    manual_source = _source(manual)
    graph_source = _source(graph)
    if not manual_source or not graph_source:
        return False
    if str(manual_source.get("document_id") or "").strip() != str(
        graph_source.get("document_id") or ""
    ).strip():
        return False
    graph_version = str(graph_source.get("document_version") or "").strip()
    manual_version = str(manual_source.get("document_version") or "").strip()
    return not graph_version or not manual_version or graph_version == manual_version


def _complete_graph_source(row: Mapping[str, Any]) -> bool:
    source = _source(row)
    return bool(
        str(source.get("document_id") or "").strip()
        and str(source.get("document_version") or "").strip()
        and str(source.get("section_id") or "").strip()
        and list(source.get("source_chunk_uids") or ())
        and list(source.get("pages") or ())
    )


__all__ = ["GraphManualCoverage", "evaluate_graph_manual_coverage"]
