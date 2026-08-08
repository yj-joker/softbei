"""Canonical identity and structural ordering for manual evidence."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any


_MISSING_POSITION = math.inf
_POSITION_FIELDS = (
    "section_index",
    "page",
    "source_index",
    "child_index",
    "row_index",
)


def _nested_mapping(item: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = item.get(key)
    return value if isinstance(value, Mapping) else {}


def _field(item: Mapping[str, Any], name: str) -> Any:
    source = _nested_mapping(item, "source")
    metadata = _nested_mapping(item, "metadata")
    for container in (source, metadata, item):
        value = container.get(name)
        if value not in (None, ""):
            return value
    return None


def canonical_manual_chunk_id(item: Mapping[str, Any]) -> str:
    """Return the source chunk shared by derived and direct representations."""
    for name in (
        "source_chunk_id",
        "chunk_id",
        "chunk_uid",
        "id",
        "doc_id",
        "evidence_id",
    ):
        value = _field(item, name)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _numeric_position(value: Any) -> int | float:
    if value in (None, "") or isinstance(value, bool):
        return _MISSING_POSITION
    if isinstance(value, (int, float)):
        return value
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return _MISSING_POSITION
    return int(number) if number.is_integer() else number


def manual_position_key(item: Mapping[str, Any]) -> tuple[Any, ...]:
    """Return a stable source-order key independent of retrieval relevance."""
    document_id = str(_field(item, "document_id") or "")
    positions = tuple(_numeric_position(_field(item, name)) for name in _POSITION_FIELDS)
    return (document_id, *positions, canonical_manual_chunk_id(item))


def _record_preference(item: Mapping[str, Any]) -> tuple[int, int]:
    chunk_type = str(_field(item, "chunk_type") or "")
    raw_step = int(chunk_type == "step_raw")
    structural_fields = sum(
        _field(item, name) not in (None, "")
        for name in ("document_id", "section_index", "page", *_POSITION_FIELDS[2:])
    )
    return raw_step, structural_fields


def dedupe_and_sort_manual_records(
    items: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Deduplicate representations of one source chunk and restore source order."""
    selected: dict[str, tuple[dict[str, Any], tuple[int, int]]] = {}
    anonymous: list[dict[str, Any]] = []
    for raw in items:
        item = dict(raw)
        identity = canonical_manual_chunk_id(item)
        if not identity:
            anonymous.append(item)
            continue
        preference = _record_preference(item)
        previous = selected.get(identity)
        if previous is None or preference > previous[1]:
            selected[identity] = (item, preference)
    records = [item for item, _ in selected.values()]
    records.extend(anonymous)
    return sorted(records, key=manual_position_key)
