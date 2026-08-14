"""Build auditable many-to-many bindings between manual figures and evidence chunks."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable


IMAGE_BINDING_SCHEMA_VERSION = 2
_POSITIONED_CHUNK_TYPES = frozenset({"text", "table"})
_MAX_PROCEDURE_LAYOUT_GAP = 180.0


def _bbox(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        box = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        return None
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    return box


def _horizontal_overlap_ratio(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    overlap = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    return overlap / max(1.0, min(left[2] - left[0], right[2] - right[0]))


def _layout_score(
    chunk_box: tuple[float, float, float, float],
    image_box: tuple[float, float, float, float],
) -> float | None:
    overlap = _horizontal_overlap_ratio(chunk_box, image_box)
    if overlap < 0.15:
        return None
    if chunk_box[3] < image_box[1]:
        vertical_gap = image_box[1] - chunk_box[3]
        reading_order_penalty = 0.0
    elif chunk_box[1] > image_box[3]:
        vertical_gap = chunk_box[1] - image_box[3]
        reading_order_penalty = 4.0
    else:
        vertical_gap = 0.0
        reading_order_penalty = 0.0
    return vertical_gap + reading_order_penalty + (1.0 - overlap) * 24.0


def _intersects_layout_target(
    chunk_box: tuple[float, float, float, float],
    image_box: tuple[float, float, float, float],
) -> bool:
    return (
        _horizontal_overlap_ratio(chunk_box, image_box) >= 0.15
        and min(chunk_box[3], image_box[3]) > max(chunk_box[1], image_box[1])
    )


def _vertical_relation(
    chunk_box: tuple[float, float, float, float],
    image_box: tuple[float, float, float, float],
) -> str:
    if chunk_box[3] <= image_box[1]:
        return "before"
    if chunk_box[1] >= image_box[3]:
        return "after"
    return "overlap"


def _vertical_gap(
    chunk_box: tuple[float, float, float, float],
    image_box: tuple[float, float, float, float],
) -> float:
    relation = _vertical_relation(chunk_box, image_box)
    if relation == "before":
        return image_box[1] - chunk_box[3]
    if relation == "after":
        return chunk_box[1] - image_box[3]
    return 0.0


def _scope_id(chunk: dict[str, Any]) -> str:
    return str((chunk.get("metadata") or {}).get("procedure_scope_id") or "").strip()


def _target_type(chunk: dict[str, Any]) -> str:
    if str(chunk.get("chunk_label") or "") == "step":
        return "step"
    if str(chunk.get("chunk_type") or "") == "table":
        return "table"
    return "text"


def _empty_bundle(*, page_has_steps: bool) -> dict[str, Any]:
    return {
        "image_binding_schema_version": IMAGE_BINDING_SCHEMA_VERSION,
        "image_bindings": [],
        "related_step_chunk_ids": [],
        "related_text_chunk_ids": [],
        "procedure_scope_ids": [],
        "binding_role": "page_fallback" if page_has_steps else "section_fallback",
        "binding_confidence": 0.0,
    }


def _append_binding(
    relations: dict[int, dict[str, dict[str, Any]]],
    image_index: int,
    chunk: dict[str, Any],
    *,
    relation: str,
    confidence: float,
) -> None:
    target_id = str(chunk.get("id") or "").strip()
    if not target_id:
        return
    candidate = {
        "target_id": target_id,
        "target_type": _target_type(chunk),
        "relation": relation,
        "confidence": confidence,
    }
    current = relations[image_index].get(target_id)
    if current is None or float(current.get("confidence") or 0.0) < confidence:
        relations[image_index][target_id] = candidate


def build_layout_image_bindings(
    chunks: Iterable[dict[str, Any]],
    images: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return one Schema v2 binding bundle for every input image.

    Each positioned image gets a nearest layout anchor. Additional chunks in the
    same structured procedure are assigned to the nearest eligible image, which
    permits both one-step-to-many-images and one-image-to-many-chunks without
    promoting page membership into evidence.
    """

    chunk_list = list(chunks or [])
    image_list = list(images or [])
    positioned_chunks: list[tuple[int, dict[str, Any], tuple[float, float, float, float]]] = []
    for order, chunk in enumerate(chunk_list):
        if str(chunk.get("chunk_type") or "") not in _POSITIONED_CHUNK_TYPES:
            continue
        if str(chunk.get("chunk_label") or "") == "outline":
            continue
        chunk_box = _bbox((chunk.get("metadata") or {}).get("bbox"))
        if chunk_box and str(chunk.get("id") or "").strip():
            positioned_chunks.append((order, chunk, chunk_box))

    page_has_steps = {
        page: any(
            chunk.get("page") == page and chunk.get("chunk_label") == "step"
            for chunk in chunk_list
        )
        for page in {image.get("page") for image in image_list}
    }
    bundles = [
        _empty_bundle(page_has_steps=bool(page_has_steps.get(image.get("page"))))
        for image in image_list
    ]
    image_boxes = {index: _bbox(image.get("bbox")) for index, image in enumerate(image_list)}
    anchors: dict[int, tuple[dict[str, Any], float]] = {}
    embedded_image_indexes: set[int] = set()

    for image_index, image in enumerate(image_list):
        image_box = image_boxes[image_index]
        if image_box is None:
            continue
        candidates: list[tuple[float, int, dict[str, Any]]] = []
        for order, chunk, chunk_box in positioned_chunks:
            if chunk.get("page") != image.get("page"):
                continue
            if _intersects_layout_target(chunk_box, image_box):
                embedded_image_indexes.add(image_index)
            score = _layout_score(chunk_box, image_box)
            if score is not None:
                candidates.append((score, order, chunk))
        if candidates:
            score, _, anchor = min(candidates, key=lambda item: (item[0], item[1]))
            anchors[image_index] = (anchor, score)

    relations: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)
    for image_index, (anchor, _) in anchors.items():
        _append_binding(
            relations,
            image_index,
            anchor,
            relation="layout_anchor",
            confidence=0.95,
        )

    for _, chunk, chunk_box in positioned_chunks:
        chunk_scope = _scope_id(chunk)
        if not chunk_scope:
            continue
        eligible: list[tuple[float, int]] = []
        for image_index, (anchor, _) in anchors.items():
            image = image_list[image_index]
            if image.get("page") != chunk.get("page") or _scope_id(anchor) != chunk_scope:
                continue
            image_box = image_boxes[image_index]
            anchor_box = _bbox((anchor.get("metadata") or {}).get("bbox"))
            if (
                image_index in embedded_image_indexes
                and not _intersects_layout_target(chunk_box, image_box)
            ):
                continue
            if image_index not in embedded_image_indexes and anchor_box is not None:
                anchor_side = _vertical_relation(anchor_box, image_box)
                chunk_side = _vertical_relation(chunk_box, image_box)
                if anchor_side != "overlap" and chunk_side != anchor_side:
                    continue
                if _vertical_gap(chunk_box, image_box) > _MAX_PROCEDURE_LAYOUT_GAP:
                    continue
            score = _layout_score(chunk_box, image_box)
            if score is not None:
                eligible.append((score, image_index))
        if not eligible:
            continue
        best_score = min(score for score, _ in eligible)
        chunk_height = max(1.0, chunk_box[3] - chunk_box[1])
        tie_tolerance = max(8.0, min(36.0, chunk_height))
        for score, image_index in eligible:
            if score > best_score + tie_tolerance:
                continue
            _append_binding(
                relations,
                image_index,
                chunk,
                relation="procedure_layout_member",
                confidence=0.75,
            )

    chunk_order = {
        str(chunk.get("id") or ""): order
        for order, chunk in enumerate(chunk_list)
    }
    for image_index, relation_map in relations.items():
        ordered = sorted(
            relation_map.values(),
            key=lambda item: (chunk_order.get(item["target_id"], 10**9), item["target_id"]),
        )
        step_ids = [item["target_id"] for item in ordered if item["target_type"] == "step"]
        text_ids = [item["target_id"] for item in ordered]
        scope_ids = list(dict.fromkeys(
            _scope_id(chunk)
            for chunk in chunk_list
            if str(chunk.get("id") or "") in set(text_ids) and _scope_id(chunk)
        ))
        bundles[image_index] = {
            "image_binding_schema_version": IMAGE_BINDING_SCHEMA_VERSION,
            "image_bindings": ordered,
            "related_step_chunk_ids": step_ids,
            "related_text_chunk_ids": text_ids,
            "procedure_scope_ids": scope_ids,
            "binding_role": "positioned_step" if step_ids else "positioned_text",
            "binding_confidence": max(float(item["confidence"]) for item in ordered),
        }
    return bundles
