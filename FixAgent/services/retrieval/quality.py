"""Quality gates for adaptive knowledge retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List

from services.retrieval.planner import RetrievalPlan


@dataclass(frozen=True)
class RetrievalQualityReport:
    grade: str
    score: float
    reasons: List[str]
    matched_types: List[str]
    required_types: List[str]
    supplemental_routes: List[str]
    should_supplement: bool
    candidate_count: int
    best_score: float


def _metadata(item: Dict[str, Any]) -> Dict[str, Any]:
    return item.get("metadata") or {}


def _chunk_type(item: Dict[str, Any]) -> str:
    value = _metadata(item).get("chunk_type", "text")
    return "image" if value == "image_summary" else str(value or "text")


def _score(item: Dict[str, Any]) -> float:
    value = item.get("rerank_score")
    if value is None:
        value = item.get("relevance_score")
    if value is None:
        value = item.get("score", 0.0)
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _stable_values(items: Iterable[Dict[str, Any]], field_name: str) -> set[str]:
    values = set()
    for item in items:
        value = _metadata(item).get(field_name)
        if value not in ("", None):
            values.add(str(value))
    return values


def required_types_for_plan(plan: RetrievalPlan) -> List[str]:
    if plan.intent == "parameter":
        return ["table"]
    if plan.intent in {"procedure", "diagnosis"}:
        return ["text"]
    if plan.intent == "image_identification":
        return ["image"]
    return []


def supplemental_routes_for_plan(plan: RetrievalPlan, missing_types: Iterable[str], weak_recall: bool = False) -> List[str]:
    routes: List[str] = []
    missing = set(missing_types)

    if "table" in missing:
        routes.append("table")
    if "text" in missing:
        routes.extend(["text", "keyword"])
    if "image" in missing:
        routes.extend(["image_summary", "image_vector"])

    if weak_recall and not routes:
        if plan.intent == "parameter":
            routes.extend(["table", "keyword"])
        elif plan.intent in {"procedure", "diagnosis"}:
            routes.extend(["text", "keyword"])
        elif plan.intent == "image_identification":
            routes.extend(["image_summary", "image_vector"])
        else:
            routes.extend(["semantic", "keyword"])

    seen = set()
    deduped = []
    for route in routes:
        if route in seen:
            continue
        seen.add(route)
        deduped.append(route)
    return deduped


def evaluate_retrieval_quality(
    plan: RetrievalPlan,
    ranked_candidates: Iterable[Dict[str, Any]],
    selected_candidates: Iterable[Dict[str, Any]],
    top_k: int,
) -> RetrievalQualityReport:
    """Estimate whether retrieved evidence is strong enough to answer directly."""
    ranked_items = sorted(list(ranked_candidates or []), key=_score, reverse=True)
    selected_items = sorted(list(selected_candidates or []), key=_score, reverse=True)
    evidence_items = selected_items or ranked_items[: max(top_k, 1)]

    if not evidence_items:
        routes = supplemental_routes_for_plan(plan, required_types_for_plan(plan), weak_recall=True)
        return RetrievalQualityReport(
            grade="low",
            score=0.0,
            reasons=["no_candidates"],
            matched_types=[],
            required_types=required_types_for_plan(plan),
            supplemental_routes=routes,
            should_supplement=bool(routes),
            candidate_count=0,
            best_score=0.0,
        )

    required_types = required_types_for_plan(plan)
    matched_types = sorted({_chunk_type(item) for item in evidence_items})
    missing_required = [item_type for item_type in required_types if item_type not in matched_types]
    best_score = _score(evidence_items[0])
    reasons: List[str] = []

    if best_score < 0.55:
        reasons.append("weak_top_score")
    elif best_score < 0.72:
        reasons.append("medium_top_score")

    for item_type in missing_required:
        reasons.append(f"missing_required_type:{item_type}")

    if top_k > 1 and len(evidence_items) < min(top_k, 2):
        reasons.append("too_few_candidates")

    if len(evidence_items) >= 2:
        second_score = _score(evidence_items[1])
        close_scores = best_score - second_score <= 0.03
        different_docs = len(_stable_values(evidence_items[:3], "document_id")) > 1
        different_sections = len(_stable_values(evidence_items[:3], "parent_section_id")) > 1
        if close_scores and (different_docs or different_sections):
            reasons.append("ambiguous_close_scores")

    top_scores = [_score(item) for item in evidence_items[: min(4, len(evidence_items))]]
    if len(top_scores) >= 3 and max(top_scores) - min(top_scores) <= 0.08:
        doc_count = len(_stable_values(evidence_items[:4], "document_id"))
        section_count = len(_stable_values(evidence_items[:4], "parent_section_id"))
        if doc_count > 2 or section_count > 2:
            reasons.append("dispersed_sources")

    if best_score < 0.55 or "no_candidates" in reasons:
        grade = "low"
    elif missing_required or "medium_top_score" in reasons or "too_few_candidates" in reasons:
        grade = "medium"
    elif "ambiguous_close_scores" in reasons or "dispersed_sources" in reasons:
        grade = "medium"
    elif best_score >= 0.8:
        grade = "high"
    else:
        grade = "medium"

    weak_recall = grade in {"low", "medium"} and not missing_required
    supplemental_routes = supplemental_routes_for_plan(plan, missing_required, weak_recall=weak_recall)
    return RetrievalQualityReport(
        grade=grade,
        score=round(best_score, 6),
        reasons=reasons,
        matched_types=matched_types,
        required_types=required_types,
        supplemental_routes=supplemental_routes,
        should_supplement=grade != "high" and bool(supplemental_routes),
        candidate_count=len(evidence_items),
        best_score=round(best_score, 6),
    )
