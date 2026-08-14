"""Rank fusion helpers for multi-route retrieval."""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List


DEFAULT_RRF_CONSTANT = 60
ORIGINAL_SCORE_RETAIN_FACTOR = 0.85


def _metadata(item: Dict[str, Any]) -> Dict[str, Any]:
    return dict(item.get("metadata") or {})


def _score(item: Dict[str, Any]) -> float:
    value = item.get("relevance_score")
    if value is None:
        value = item.get("score", 0.0)
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _routes(item: Dict[str, Any]) -> set[str]:
    routes = set(item.get("routes") or [])
    route = item.get("retrieval_route")
    if route:
        routes.add(str(route))
    return routes


def _query_variants(item: Dict[str, Any]) -> List[Dict[str, str]]:
    metadata = item.get("metadata") or {}
    variants: List[Dict[str, str]] = []
    existing = metadata.get("query_variants") or []
    if isinstance(existing, list):
        for value in existing:
            if not isinstance(value, dict) or not str(value.get("text") or "").strip():
                continue
            variants.append({
                "text": str(value.get("text") or "").strip(),
                "source": str(value.get("source") or "").strip(),
                "target_id": str(value.get("target_id") or "").strip(),
            })
    text = str(metadata.get("query_variant_text") or "").strip()
    if text:
        current = {
            "text": text,
            "source": str(metadata.get("query_variant_source") or "").strip(),
            "target_id": str(metadata.get("query_variant_target_id") or "").strip(),
        }
        if current not in variants:
            variants.append(current)
    return variants


def reciprocal_rank_fusion(
    ranked_lists: Iterable[Iterable[Dict[str, Any]]],
    *,
    key_fn: Callable[[Dict[str, Any]], str],
    top_k: int | None = None,
    rrf_constant: int = DEFAULT_RRF_CONSTANT,
) -> List[Dict[str, Any]]:
    """Fuse ranked retrieval lists with normalized Reciprocal Rank Fusion."""
    lists = [list(items or []) for items in ranked_lists]
    if not lists or not any(lists):
        return []

    constant = max(int(rrf_constant), 1)
    max_possible_score = len(lists) / (constant + 1)
    fused: Dict[str, Dict[str, Any]] = {}

    for list_index, items in enumerate(lists):
        seen_in_list: set[str] = set()
        for rank, candidate in enumerate(items, start=1):
            key = str(key_fn(candidate) or "")
            if not key or key in seen_in_list:
                continue
            seen_in_list.add(key)

            entry = fused.setdefault(
                key,
                {
                    "raw_rrf_score": 0.0,
                    "best_candidate": dict(candidate),
                    "best_relevance_score": _score(candidate),
                    "first_seen": (list_index, rank),
                    "route_ranks": [],
                    "routes": set(),
                    "query_variants": [],
                },
            )
            entry["raw_rrf_score"] += 1.0 / (constant + rank)
            entry["route_ranks"].append(rank)
            entry["routes"].update(_routes(candidate))
            for variant in _query_variants(candidate):
                if variant not in entry["query_variants"]:
                    entry["query_variants"].append(variant)

            relevance = _score(candidate)
            if relevance > entry["best_relevance_score"]:
                entry["best_candidate"] = dict(candidate)
                entry["best_relevance_score"] = relevance

    fused_items: List[Dict[str, Any]] = []
    for key, entry in fused.items():
        normalized_score = entry["raw_rrf_score"] / max_possible_score if max_possible_score else 0.0
        normalized_score = round(max(0.0, min(1.0, normalized_score)), 6)
        raw_rrf_score = round(entry["raw_rrf_score"], 6)
        pre_rrf_score = round(entry["best_relevance_score"], 6)
        fused_relevance_score = round(
            max(normalized_score, min(1.0, pre_rrf_score * ORIGINAL_SCORE_RETAIN_FACTOR)),
            6,
        )

        item = dict(entry["best_candidate"])
        item["doc_id"] = key
        item["routes"] = sorted(entry["routes"])
        item["rrf_score"] = normalized_score
        item["relevance_score"] = fused_relevance_score
        item["score"] = fused_relevance_score

        metadata = _metadata(item)
        metadata.update(
            {
                "rrf_enabled": True,
                "rrf_constant": constant,
                "rrf_list_count": len(lists),
                "rrf_route_count": len(entry["route_ranks"]),
                "rrf_raw_score": raw_rrf_score,
                "rrf_score": normalized_score,
                "rrf_route_ranks": list(entry["route_ranks"]),
                "pre_rrf_relevance_score": pre_rrf_score,
                "fusion_relevance_score": fused_relevance_score,
                "query_variants": list(entry["query_variants"]),
            }
        )
        item["metadata"] = metadata
        fused_items.append(item)

    fused_items.sort(
        key=lambda item: (
            item.get("rrf_score", 0.0),
            item["metadata"].get("pre_rrf_relevance_score", 0.0),
            -min(item["metadata"].get("rrf_route_ranks") or [999999]),
        ),
        reverse=True,
    )

    for rank, item in enumerate(fused_items, start=1):
        item["metadata"]["rrf_rank"] = rank

    if top_k is None:
        return fused_items
    return fused_items[: max(int(top_k), 0)]
