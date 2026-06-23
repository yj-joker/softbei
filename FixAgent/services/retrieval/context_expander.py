"""Context expansion for parent/child retrieval evidence."""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Tuple


logger = logging.getLogger(__name__)

SCALAR_EXPANSION_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("parent_table_chunk_id", "table_parent"),
    ("source_image_id", "paired_image"),
    ("prev_chunk_id", "previous_chunk"),
    ("next_chunk_id", "next_chunk"),
)

LIST_EXPANSION_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("related_step_chunk_ids", "related_step"),
    ("related_text_chunk_ids", "related_text"),
)

SECTION_LOOKUP_LIMIT = 6
SECTION_LABEL_PRIORITY = {
    "troubleshooting": 0,
    "step": 1,
    "safety": 2,
    "table_row": 3,
    "table_full": 4,
    "image_summary": 5,
    "image": 6,
    "general": 9,
}


def _as_dict(item: Any) -> Dict[str, Any]:
    if hasattr(item, "model_dump"):
        return item.model_dump()
    if isinstance(item, dict):
        return dict(item)
    return {}


def _doc_id(item: Dict[str, Any]) -> str:
    return str(item.get("doc_id") or item.get("id") or "")


def _score(item: Dict[str, Any]) -> float:
    value = item.get("relevance_score")
    if value is None:
        value = item.get("score", 0.0)
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _mark_primary(item: Dict[str, Any]) -> Dict[str, Any]:
    marked = dict(item)
    marked["doc_id"] = _doc_id(marked)
    metadata = dict(marked.get("metadata") or {})
    metadata.setdefault("context_role", "primary")
    marked["metadata"] = metadata
    return marked


def _iter_expansion_refs(item: Dict[str, Any]) -> Iterable[Tuple[str, str]]:
    metadata = item.get("metadata") or {}
    for field_name, reason in SCALAR_EXPANSION_FIELDS:
        ref_id = metadata.get(field_name)
        if ref_id:
            yield str(ref_id), reason
    for field_name, reason in LIST_EXPANSION_FIELDS:
        refs = metadata.get(field_name)
        if isinstance(refs, list):
            for ref_id in refs:
                if ref_id:
                    yield str(ref_id), reason


def _mark_expanded(record: Dict[str, Any], source_id: str, reason: str, source_score: float) -> Dict[str, Any]:
    marked = dict(record)
    marked["doc_id"] = _doc_id(marked)
    metadata = dict(marked.get("metadata") or {})
    metadata["context_role"] = "expanded"
    metadata["expansion_reason"] = reason
    metadata["expanded_from_doc_id"] = source_id
    metadata["retrieval_context_source_score"] = source_score
    marked["metadata"] = metadata

    expanded_score = max(0.0, source_score - 0.05)
    if not marked.get("relevance_score"):
        marked["relevance_score"] = expanded_score
    if not marked.get("score"):
        marked["score"] = marked.get("relevance_score", expanded_score)
    marked.setdefault("raw_score_type", "context_expansion")
    return marked


def _section_identity(item: Dict[str, Any]) -> Tuple[str, str]:
    metadata = item.get("metadata") or {}
    document_id = str(metadata.get("document_id") or "")
    parent_section_id = str(metadata.get("parent_section_id") or "")
    return document_id, parent_section_id


def _section_priority(item: Dict[str, Any]) -> Tuple[int, int, str]:
    metadata = item.get("metadata") or {}
    label = str(metadata.get("chunk_label") or "")
    chunk_type = str(metadata.get("chunk_type") or "")
    priority = SECTION_LABEL_PRIORITY.get(label, SECTION_LABEL_PRIORITY.get(chunk_type, 8))
    source_index = metadata.get("source_index")
    if source_index is None:
        source_index = metadata.get("row_index")
    try:
        order = int(source_index)
    except (TypeError, ValueError):
        order = 9999
    return priority, order, _doc_id(item)


def _collect_section_context(
    primaries: List[Dict[str, Any]],
    vector_service: Any,
    seen_ids: set[str],
    remaining: int,
) -> List[Dict[str, Any]]:
    if remaining <= 0 or not hasattr(vector_service, "get_section_records"):
        return []

    section_sources: Dict[Tuple[str, str], Tuple[str, float]] = {}
    for primary in primaries:
        section_key = _section_identity(primary)
        if not all(section_key):
            continue
        section_sources.setdefault(section_key, (_doc_id(primary), _score(primary)))

    expanded: List[Dict[str, Any]] = []
    for (document_id, parent_section_id), (source_id, source_score) in section_sources.items():
        try:
            records = vector_service.get_section_records(
                document_id,
                parent_section_id,
                limit=SECTION_LOOKUP_LIMIT,
            )
        except Exception as exc:
            logger.warning("section context expansion skipped: %s", exc)
            continue
        for record in sorted((_as_dict(item) for item in records), key=_section_priority):
            ref_id = _doc_id(record)
            if not ref_id or ref_id in seen_ids:
                continue
            metadata = record.get("metadata") or {}
            if metadata.get("document_id") != document_id:
                continue
            if metadata.get("parent_section_id") != parent_section_id:
                continue
            expanded.append(_mark_expanded(record, source_id, "section_context", source_score))
            seen_ids.add(ref_id)
            if len(expanded) >= remaining:
                return expanded
    return expanded


def expand_retrieval_context(
    primary_candidates: List[Dict[str, Any]],
    vector_service: Any,
    max_expanded: int = 6,
) -> List[Dict[str, Any]]:
    """Append relation-linked context chunks to primary retrieval hits."""
    primaries = [_mark_primary(_as_dict(item)) for item in primary_candidates or []]
    if not primaries or max_expanded <= 0:
        return primaries
    if not hasattr(vector_service, "get_vector_records") and not hasattr(vector_service, "get_section_records"):
        return primaries

    seen_ids = {_doc_id(item) for item in primaries if _doc_id(item)}
    expansion_order: List[str] = []
    expansion_meta: Dict[str, Tuple[str, str, float]] = {}
    if hasattr(vector_service, "get_vector_records"):
        for primary in primaries:
            source_id = _doc_id(primary)
            source_score = _score(primary)
            for ref_id, reason in _iter_expansion_refs(primary):
                if ref_id in seen_ids or ref_id in expansion_meta:
                    continue
                expansion_meta[ref_id] = (source_id, reason, source_score)
                expansion_order.append(ref_id)
                if len(expansion_order) >= max_expanded:
                    break
            if len(expansion_order) >= max_expanded:
                break

    expanded: List[Dict[str, Any]] = []
    if expansion_order:
        fetched = vector_service.get_vector_records(expansion_order)
        fetched_by_id = {_doc_id(_as_dict(item)): _as_dict(item) for item in fetched}
        for ref_id in expansion_order:
            record = fetched_by_id.get(ref_id)
            if not record:
                continue
            # 防御：按 id 直取的上下文扩展只拼接手册记录，杜绝会话事实混入手册回答
            if (record.get("metadata") or {}).get("record_type", "manual") != "manual":
                continue
            source_id, reason, source_score = expansion_meta[ref_id]
            expanded.append(_mark_expanded(record, source_id, reason, source_score))
            seen_ids.add(ref_id)

    remaining = max_expanded - len(expanded)
    if remaining > 0:
        expanded.extend(_collect_section_context(primaries, vector_service, seen_ids, remaining))

    return primaries + expanded
