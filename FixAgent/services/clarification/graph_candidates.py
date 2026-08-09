"""Normalize Java knowledge-graph paths into clarification candidates.

The graph is treated as a source of observed candidates, not as a source of
free-form questions.  Every public field in this module is copied from the
graph response (or derived from stable node identifiers); no device or
component vocabulary is maintained here.
"""

from __future__ import annotations

import hashlib
import re
from collections import OrderedDict
from typing import Any, Iterable, Mapping

from services.clarification.models import KnowledgeCandidate


_PAGE_RE = re.compile(r"(?:^|[:#/_ -])(?:page|p)[:#/_ -]?(\d{1,4})(?:$|[^\d])", re.IGNORECASE)

_OBSERVABLE_HINTS = (
    "无法启动", "不能启动", "启动困难", "不工作", "熄火", "异响", "啸叫",
    "撞击声", "噪声", "抖动", "振动", "冒烟", "漏油", "渗漏", "过热",
    "温度高", "温度异常", "动力不足", "加速无力", "转速不稳", "怠速不稳",
    "压力低", "压力高", "压力不足", "压力波动", "故障灯", "报警", "报码",
    "打滑", "卡滞", "失灵", "冷机", "热机", "启动瞬间", "加速时", "怠速时",
)
_NON_OBSERVABLE_ACTIONS = ("安装", "拆卸", "检查", "检修", "更换", "调整", "维修")


def _value(record: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in record and record[name] not in (None, ""):
            return record[name]
    return None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _texts(value: Any) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    values = value if isinstance(value, (list, tuple, set)) else (value,)
    return tuple(dict.fromkeys(_text(item) for item in values if _text(item)))


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _pages(record: Mapping[str, Any], evidence_refs: Iterable[str]) -> tuple[int, ...]:
    raw_pages = _value(record, "pages", "pageNumbers", "page_numbers")
    values = raw_pages if isinstance(raw_pages, (list, tuple, set)) else ((raw_pages,) if raw_pages else ())
    pages: list[int] = []
    for value in values:
        try:
            page = int(value)
        except (TypeError, ValueError):
            continue
        if page > 0 and page not in pages:
            pages.append(page)
    for ref in evidence_refs:
        match = _PAGE_RE.search(ref)
        if match:
            page = int(match.group(1))
            if page > 0 and page not in pages:
                pages.append(page)
    return tuple(pages)


def _stable_path_id(record: Mapping[str, Any]) -> str:
    explicit = _text(_value(record, "pathId", "path_id"))
    if explicit:
        return explicit
    parts = tuple(
        _text(_value(record, key, key[0].lower() + key[1:]))
        for key in ("deviceId", "componentId", "faultId", "solutionId")
    )
    canonical = "|".join(parts)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]
    return f"derived-{digest}"


def _solution_values(record: Mapping[str, Any]) -> tuple[str, ...]:
    solutions = _value(record, "solutions")
    values: list[str] = []
    if isinstance(solutions, list):
        for solution in solutions:
            if not isinstance(solution, Mapping):
                continue
            value = _text(_value(solution, "id", "solutionId", "title"))
            if value and value not in values:
                values.append(value)
    fallback = _text(_value(record, "solutionId", "solution_id"))
    if fallback and fallback not in values:
        values.append(fallback)
    return tuple(values)


def _observable_label(features: tuple[str, ...], fault_name: str) -> str:
    for value in (*features, fault_name):
        text = _text(value)
        if (
            text
            and not any(action in text for action in _NON_OBSERVABLE_ACTIONS)
            and any(hint in text for hint in _OBSERVABLE_HINTS)
        ):
            return text
    return ""


def build_graph_candidates(
    records: Iterable[Mapping[str, Any]],
    *,
    query: str = "",
) -> tuple[KnowledgeCandidate, ...]:
    """Create one candidate per stable graph path and preserve provenance.

    Older Java deployments may omit ``pathId``.  In that case a deterministic
    identifier is derived from node IDs so the candidate can still be bound on
    the next turn without relying on labels or keywords.
    """
    unique: OrderedDict[str, Mapping[str, Any]] = OrderedDict()
    for raw in records or ():
        if not isinstance(raw, Mapping):
            continue
        path_id = _stable_path_id(raw)
        unique.setdefault(path_id, raw)

    candidates: list[KnowledgeCandidate] = []
    for path_id, record in unique.items():
        device_id = _text(_value(record, "deviceId", "device_id"))
        component_id = _text(_value(record, "componentId", "component_id"))
        fault_id = _text(_value(record, "faultId", "fault_id"))
        document_id = _text(_value(record, "documentId", "document_id"))
        component_name = _text(_value(record, "componentName", "component_name"))
        fault_name = _text(_value(record, "faultName", "fault_name"))
        device_name = _text(_value(record, "deviceName", "device_name")) or device_id
        solutions = _solution_values(record)
        evidence_refs = _texts(_value(record, "evidenceRefs", "evidence_refs"))
        source_chunk_uids = _texts(
            _value(record, "sourceChunkUids", "source_chunk_uids")
        )
        source_chunk_uid = _text(_value(record, "sourceChunkUid", "source_chunk_uid"))
        if source_chunk_uid and source_chunk_uid not in source_chunk_uids:
            source_chunk_uids = (*source_chunk_uids, source_chunk_uid)
        for chunk_uid in source_chunk_uids:
            if chunk_uid not in evidence_refs:
                evidence_refs = (*evidence_refs, chunk_uid)
        section_id = _text(_value(record, "sectionId", "section_id")) or path_id
        document_version = _text(
            _value(record, "documentVersion", "document_version", "importBatchId")
        )
        provenance_status = _text(
            _value(record, "provenanceStatus", "provenance_status")
        )
        if provenance_status not in {"complete", "partial", "missing"}:
            provenance_status = (
                "complete"
                if document_id and section_id and source_chunk_uids
                else "partial"
                if document_id
                else "missing"
            )
        manual_ids = _texts(_value(record, "manualIds", "manual_ids"))
        if not document_id and len(manual_ids) == 1:
            # A manual id is not assumed to be a document id; retain it only as
            # provenance.  This branch intentionally leaves document_id empty.
            pass
        pages = _pages(record, evidence_refs)
        features = _texts(_value(record, "distinguishingFeatures", "distinguishing_features"))
        actions = _texts(_value(record, "verificationActions", "verification_actions"))
        observable_label = _observable_label(features, fault_name)

        graph_score = _float(_value(record, "graphScore", "graph_score"), 0.0)
        match_score = _float(_value(record, "retrievalScore", "retrieval_score"), 0.0)
        if not match_score:
            # matchScore is an integer dimension count in the current Java API;
            # normalize it without treating it as a semantic confidence.
            try:
                raw_match_score = _value(record, "matchScore", "match_score")
                match_score = max(0.0, min(1.0, float(raw_match_score or 0.0) / 4.0))
            except (TypeError, ValueError):
                match_score = 0.0
        if not match_score:
            match_score = graph_score
        component_score = _float(_value(record, "componentScore", "component_score"), 0.0)
        fault_score = _float(_value(record, "faultScore", "fault_score"), 0.0)
        target_score = max(component_score, fault_score, match_score, graph_score)
        field_score = 1.0 if (solutions or evidence_refs) else 0.5
        dimensions = {
            "path_id": path_id,
            "device_id": device_id,
            "document_id": document_id,
            "section_id": section_id,
            "component_id": component_id,
            "fault_id": fault_id,
            "component": component_name,
            "fault": fault_name,
            "solution_id": solutions[0] if solutions else "",
            "observable_symptom": observable_label,
        }
        if device_name:
            dimensions["device_name"] = device_name
            dimensions["device_identity"] = device_name
        dimensions = {key: value for key, value in dimensions.items() if value}
        labels = {
            "path_id": path_id,
            "device_id": device_name,
            "document_id": device_name or document_id,
            "component_id": component_name or component_id,
            "fault_id": fault_name or fault_id,
            "component": component_name,
            "fault": fault_name,
            "solution_id": solutions[0] if solutions else "",
            "observable_symptom": observable_label,
        }
        labels = {key: value for key, value in labels.items() if value}
        node_ids = tuple(dict.fromkeys(
            value for value in (device_id, component_id, fault_id, *solutions) if value
        ))
        candidates.append(
            KnowledgeCandidate(
                candidate_id=f"graph:{path_id}",
                document_id=document_id,
                section_id=section_id,
                section_title=(
                    f"{component_name} / {fault_name}".strip(" /")
                    or path_id
                ),
                dimensions=dimensions,
                dimension_labels=labels,
                identity_score=1.0 if device_id else 0.4,
                target_score=target_score,
                context_score=1.0 if (component_id or fault_id) else 0.4,
                field_score=field_score,
                retrieval_score=match_score,
                evidence_refs=evidence_refs,
                source_chunk_uids=source_chunk_uids,
                pages=pages,
                source_kind="graph",
                source_kinds=("graph",),
                document_version=document_version,
                path_id=path_id,
                node_ids=node_ids,
                graph_path_ids=(path_id,),
                graph_node_ids=node_ids,
                graph_score=graph_score or match_score,
                provenance_status=provenance_status,
                distinguishing_features=features,
                verification_actions=actions,
            )
        )
    return tuple(candidates)


def unresolved_graph_dimensions(candidates: Iterable[KnowledgeCandidate]) -> tuple[str, ...]:
    """Return only worker-observable dimensions that separate graph paths.

    Device, component and path IDs remain server-side scope constraints. They
    are deliberately not clarification dimensions because asking a worker to
    choose them would require the diagnosis that the Agent is meant to make.
    """
    values = tuple(candidates)
    if not values:
        return ()

    def distinct(dimension: str) -> set[str]:
        return {
            _text(candidate.dimensions.get(dimension))
            for candidate in values
            if _text(candidate.dimensions.get(dimension))
        }

    return ("observable_symptom",) if len(distinct("observable_symptom")) > 1 else ()


__all__ = ["build_graph_candidates", "unresolved_graph_dimensions"]
