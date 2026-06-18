"""Retrieval policy helpers for multimodal knowledge search."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List


_IMAGE_HINTS = ("图片", "图", "示意图", "结构图", "位置图", "照片", "插图")
_TABLE_HINTS = ("参数", "规格", "型号", "扭矩", "数量", "表", "清单")
_TEXT_HINTS = ("怎么", "步骤", "原因", "故障", "拆", "装", "维修", "检查")


def cosine_distance_to_relevance(distance: float) -> float:
    """Convert Redis cosine distance into a bounded relevance score."""
    return round(max(0.0, min(1.0, 1.0 - float(distance))), 6)


def detect_query_intent(query: str) -> str:
    """Return the dominant retrieval intent for a user query."""
    text = query or ""
    if any(hint in text for hint in _IMAGE_HINTS):
        return "image"
    if any(hint in text for hint in _TABLE_HINTS):
        return "table"
    if any(hint in text for hint in _TEXT_HINTS):
        return "text"
    return "mixed"


def _chunk_type(candidate: Dict[str, Any]) -> str:
    chunk_type = (candidate.get("metadata") or {}).get("chunk_type", "text")
    return "image" if chunk_type == "image_summary" else chunk_type


def diversify_candidates(
    candidates: Iterable[Dict[str, Any]],
    top_k: int,
    intent: str = "mixed",
) -> List[Dict[str, Any]]:
    """Keep strong candidates while giving different content types a chance."""
    ordered = sorted(
        candidates,
        key=lambda item: item.get("rerank_score", item.get("relevance_score", 0.0)),
        reverse=True,
    )
    if top_k <= 0 or intent != "mixed":
        return ordered[: max(top_k, 0)]

    selected: List[Dict[str, Any]] = []
    seen_types = set()
    best_score = ordered[0].get("rerank_score", ordered[0].get("relevance_score", 0.0)) if ordered else 0.0
    diversity_floor = max(0.45, best_score - 0.25)
    for candidate in ordered:
        candidate_score = candidate.get("rerank_score", candidate.get("relevance_score", 0.0))
        if candidate_score < diversity_floor:
            continue
        item_type = _chunk_type(candidate)
        if item_type not in seen_types:
            selected.append(candidate)
            seen_types.add(item_type)
        if len(selected) >= top_k:
            return selected

    selected_ids = {item.get("doc_id") for item in selected}
    for candidate in ordered:
        if candidate.get("doc_id") in selected_ids:
            continue
        selected.append(candidate)
        if len(selected) >= top_k:
            break
    return selected


def summarize_confidence(candidates: Iterable[Dict[str, Any]], intent: str = "mixed") -> Dict[str, Any]:
    """Summarize retrieval confidence from score strength, types, and routes."""
    items = list(candidates)
    matched_types = sorted({_chunk_type(item) for item in items})
    best = max((item.get("relevance_score", 0.0) for item in items), default=0.0)
    dual_image_hit = any(
        _chunk_type(item) == "image"
        and {"image_vector", "image_summary"}.issubset(set(item.get("routes") or []))
        for item in items
    )
    intent_type_hit = intent == "mixed" or intent in matched_types

    if best >= 0.9 and intent_type_hit and (intent != "image" or dual_image_hit):
        confidence = "high"
    elif best >= 0.65 and intent_type_hit:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "confidence": confidence,
        "matched_types": matched_types,
        "candidate_count": len(items),
        "best_relevance_score": round(best, 6),
        "dual_image_hit": dual_image_hit,
    }
