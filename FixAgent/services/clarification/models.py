"""Immutable data contracts for clarification candidates and decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class KnowledgeCandidate:
    candidate_id: str
    document_id: str
    section_id: str
    section_title: str
    dimensions: Mapping[str, str]
    dimension_labels: Mapping[str, str] = field(default_factory=dict)
    identity_score: float = 0.0
    target_score: float = 0.0
    context_score: float = 0.0
    field_score: float = 0.0
    retrieval_score: float = 0.0
    hard_conflicts: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    pages: tuple[int, ...] = ()
    source_kind: str = "section"
    source_kinds: tuple[str, ...] = ()
    document_version: str = ""
    source_chunk_uids: tuple[str, ...] = ()
    path_id: str = ""
    node_ids: tuple[str, ...] = ()
    graph_path_ids: tuple[str, ...] = ()
    graph_node_ids: tuple[str, ...] = ()
    graph_score: float = 0.0
    quality_tier: str = "medium"
    quality_reasons: tuple[str, ...] = ()
    # ``unknown`` preserves compatibility for callers that construct a
    # candidate without provenance metadata. Retrieval adapters set this to
    # ``complete``, ``partial`` or ``missing`` explicitly.
    provenance_status: str = "unknown"
    distinguishing_features: tuple[str, ...] = ()
    verification_actions: tuple[str, ...] = ()

    @property
    def score(self) -> float:
        value = (
            0.30 * self.identity_score
            + 0.25 * self.target_score
            + 0.20 * self.context_score
            + 0.15 * self.field_score
            + 0.10 * self.retrieval_score
        )
        return round(max(0.0, min(1.0, value)), 6)


@dataclass(frozen=True)
class ClarificationOption:
    option_id: str
    label: str
    value: str
    candidate_ids: tuple[str, ...]
    constraints: Mapping[str, Any]


@dataclass(frozen=True)
class ClarificationQuestion:
    dimension: str
    prompt: str
    options: tuple[ClarificationOption, ...]
    score: float
    score_breakdown: Mapping[str, float]


@dataclass(frozen=True)
class ClarificationDecision:
    should_clarify: bool
    risk_level: RiskLevel
    selected_candidate_id: str
    candidate_ids: tuple[str, ...]
    reason: str
    question: ClarificationQuestion | None = None
    diagnostics: Mapping[str, Any] = field(default_factory=dict)
