"""Deterministic quality grading for knowledge-graph paths.

The grader only consumes structural, semantic-score and provenance signals.
It deliberately contains no device, component or fault vocabulary so test
fixtures and production entities follow exactly the same path.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


DEFAULT_HIGH_THRESHOLD = 0.85
DEFAULT_MEDIUM_THRESHOLD = 0.70


class GraphQualityTier(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class GraphQualityDecision:
    tier: GraphQualityTier
    semantic_score: float
    reasons: tuple[str, ...]


def evaluate_graph_path_quality(
    record: Mapping[str, Any],
    *,
    high_threshold: float = DEFAULT_HIGH_THRESHOLD,
    medium_threshold: float = DEFAULT_MEDIUM_THRESHOLD,
    trusted_query_structure: bool = False,
) -> GraphQualityDecision:
    """Grade a path using only generic retrieval and data-integrity signals."""
    high, medium = _thresholds(high_threshold, medium_threshold)
    semantic_score = graph_semantic_score(record)
    structural_reasons = _structural_reasons(
        record,
        trusted_query_structure=trusted_query_structure,
    )

    if structural_reasons:
        return GraphQualityDecision(
            GraphQualityTier.LOW,
            semantic_score,
            tuple(structural_reasons),
        )
    if semantic_score < medium:
        return GraphQualityDecision(
            GraphQualityTier.LOW,
            semantic_score,
            ("semantic_score_below_medium_threshold",),
        )

    provenance_reasons = _provenance_reasons(record)
    if semantic_score >= high and not provenance_reasons:
        decision = GraphQualityDecision(GraphQualityTier.HIGH, semantic_score, ())
        return _apply_declared_tier_ceiling(record, decision)

    reasons: list[str] = []
    if semantic_score < high:
        reasons.append("semantic_score_below_high_threshold")
    reasons.extend(provenance_reasons)
    decision = GraphQualityDecision(
        GraphQualityTier.MEDIUM,
        semantic_score,
        tuple(dict.fromkeys(reasons)),
    )
    return _apply_declared_tier_ceiling(record, decision)


def graph_semantic_score(record: Mapping[str, Any]) -> float:
    """Return the strongest real semantic score; never use dimension counts."""
    values = (
        record.get("semanticScore"),
        record.get("semantic_score"),
        record.get("graphScore"),
        record.get("graph_score"),
        record.get("retrievalScore"),
        record.get("retrieval_score"),
        record.get("componentScore"),
        record.get("component_score"),
        record.get("faultScore"),
        record.get("fault_score"),
    )
    scores = [_score(value) for value in values if value is not None]
    return max(scores, default=0.0)


def _apply_declared_tier_ceiling(
    record: Mapping[str, Any],
    decision: GraphQualityDecision,
) -> GraphQualityDecision:
    declared = _text(record.get("qualityTier") or record.get("quality_tier")).lower()
    if declared == GraphQualityTier.LOW.value:
        return GraphQualityDecision(
            GraphQualityTier.LOW,
            decision.semantic_score,
            tuple(dict.fromkeys((*decision.reasons, "declared_low_quality"))),
        )
    if declared == GraphQualityTier.MEDIUM.value and decision.tier is GraphQualityTier.HIGH:
        return GraphQualityDecision(
            GraphQualityTier.MEDIUM,
            decision.semantic_score,
            ("declared_medium_quality_ceiling",),
        )
    return decision


def _structural_reasons(
    record: Mapping[str, Any],
    *,
    trusted_query_structure: bool,
) -> list[str]:
    device_id = _text(record.get("deviceId") or record.get("device_id"))
    component_id = _text(record.get("componentId") or record.get("component_id"))
    fault_id = _text(record.get("faultId") or record.get("fault_id"))
    path_id = _text(record.get("pathId") or record.get("path_id"))
    node_ids = set(_texts(record.get("nodeIds") or record.get("node_ids")))
    relationships = set(_texts(
        record.get("relationshipTypes") or record.get("relationship_types")
    ))
    reasons: list[str] = []
    core_ids = (device_id, component_id, fault_id)
    if not all(core_ids):
        reasons.append("incomplete_core_identity")
    if not path_id:
        reasons.append("missing_path_id")
    elif (
        path_id.startswith("kgpath:")
        and all(core_ids)
        and path_id != f"kgpath:{device_id}:{component_id}:{fault_id}"
    ):
        reasons.append("path_identity_mismatch")
    if all(core_ids) and not set(core_ids).issubset(node_ids) and not (
        trusted_query_structure and not node_ids
    ):
        reasons.append("incomplete_node_identity")
    if not {"OWNS", "CAUSES"}.issubset(relationships) and not (
        trusted_query_structure and not relationships
    ):
        reasons.append("missing_required_relationship")
    return reasons


def _provenance_reasons(record: Mapping[str, Any]) -> list[str]:
    status = _text(record.get("provenanceStatus") or record.get("provenance_status"))
    source_chunks = _texts(
        record.get("sourceChunkUids")
        or record.get("source_chunk_uids")
        or record.get("sourceChunkUid")
        or record.get("source_chunk_uid")
    )
    fields = (
        record.get("documentId") or record.get("document_id"),
        record.get("documentVersion") or record.get("document_version"),
        record.get("sectionId") or record.get("section_id"),
        source_chunks,
        record.get("pages"),
        record.get("graphRevision") or record.get("graph_revision"),
    )
    reasons: list[str] = []
    if status != "complete":
        reasons.append("provenance_not_complete")
    if not all(bool(value) for value in fields):
        reasons.append("incomplete_provenance")
    return reasons


def _thresholds(high: float, medium: float) -> tuple[float, float]:
    normalized_high = _score(high)
    normalized_medium = _score(medium)
    if normalized_medium > normalized_high:
        raise ValueError("graph medium threshold cannot exceed high threshold")
    return normalized_high, normalized_medium


def _score(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _text(value: Any) -> str:
    return str(value or "").strip()


def _texts(value: Any) -> tuple[str, ...]:
    values = value if isinstance(value, (list, tuple, set)) else (() if value in (None, "") else (value,))
    return tuple(dict.fromkeys(_text(item) for item in values if _text(item)))


__all__ = [
    "DEFAULT_HIGH_THRESHOLD",
    "DEFAULT_MEDIUM_THRESHOLD",
    "GraphQualityDecision",
    "GraphQualityTier",
    "evaluate_graph_path_quality",
    "graph_semantic_score",
]
