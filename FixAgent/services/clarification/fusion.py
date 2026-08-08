"""Fuse section retrieval and knowledge-graph candidates by provenance.

Candidate identity is structural: document/section/source-chunk identifiers are
the only merge keys. Display labels are retained for presentation only and are
never used to join records.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Iterable

from services.clarification.models import KnowledgeCandidate
from services.retrieval.device_identity import QueryContract


_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)


def _norm(value: object) -> str:
    return " ".join(_TOKEN_RE.findall(str(value or "").casefold()))


def _tokens(value: object) -> set[str]:
    return set(_TOKEN_RE.findall(str(value or "").casefold()))


def _candidate_identity(candidate: KnowledgeCandidate) -> tuple[str, ...]:
    dimensions = candidate.dimensions
    values: list[str] = []
    for key in (
        "device_id",
        "device_identity",
        "device_name",
        "device_category",
        "carrier_or_application",
        "manufacturer",
        "model",
    ):
        value = dimensions.get(key)
        if value:
            values.append(str(value))
    return tuple(dict.fromkeys(values))


def _identity_conflicts(candidate: KnowledgeCandidate, contract: QueryContract) -> bool:
    if not contract.has_explicit_device:
        return False
    candidate_values = _candidate_identity(candidate)
    if not candidate_values:
        return False

    query_values = tuple(
        value
        for value in (
            contract.raw_device_span,
            contract.device_name,
            contract.device_category,
            contract.carrier_or_application,
            contract.manufacturer,
            contract.model,
        )
        if value
    )
    if not query_values:
        return False

    # Structured identifiers must agree exactly when both sides provide them.
    for query_value in query_values:
        query_norm = _norm(query_value)
        for candidate_value in candidate_values:
            candidate_norm = _norm(candidate_value)
            if not query_norm or not candidate_norm:
                continue
            if query_norm == candidate_norm or query_norm in candidate_norm or candidate_norm in query_norm:
                return False

    # For free-form identity spans, a low token overlap means the graph path
    # belongs to another device. This is generic normalization, not a business
    # vocabulary or keyword allow/deny list.
    query_tokens = _tokens(query_values[0])
    candidate_tokens = _tokens(candidate_values[0])
    if not query_tokens or not candidate_tokens:
        return False
    overlap = len(query_tokens & candidate_tokens) / max(len(query_tokens | candidate_tokens), 1)
    return overlap < 0.5


def _merge_key(candidate: KnowledgeCandidate) -> tuple[str, ...] | None:
    if candidate.document_id and candidate.section_id:
        return ("section", candidate.document_id, candidate.section_id)
    if candidate.document_id and candidate.source_chunk_uids:
        return ("chunks", candidate.document_id, *sorted(candidate.source_chunk_uids))
    return None


def _can_merge(left: KnowledgeCandidate, right: KnowledgeCandidate) -> bool:
    left_paths = set(left.graph_path_ids or (() if not left.path_id else (left.path_id,)))
    right_paths = set(right.graph_path_ids or (() if not right.path_id else (right.path_id,)))
    if left_paths and right_paths and left_paths.isdisjoint(right_paths):
        # A section may support multiple independent diagnostic paths.  Keep
        # those paths separate so a later scope binding cannot select the
        # wrong fault merely because the section id is shared.
        return False
    left_key = _merge_key(left)
    right_key = _merge_key(right)
    if left_key is not None and right_key is not None and left_key == right_key:
        return True
    if (
        left.document_id
        and right.document_id
        and left.document_id == right.document_id
        and set(left.source_chunk_uids) & set(right.source_chunk_uids)
    ):
        return True
    return False


def _union(*values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for group in values for item in group if item))


def _merge(left: KnowledgeCandidate, right: KnowledgeCandidate) -> KnowledgeCandidate:
    graph_candidate = (
        right
        if right.source_kind == "graph"
        else left
        if left.source_kind == "graph"
        else right
        if right.source_kind == "fused" and right.graph_path_ids
        else left
    )
    dimensions = dict(left.dimensions)
    dimensions.update(dict(right.dimensions))
    if graph_candidate is not left or graph_candidate is not right:
        dimensions.update(dict(graph_candidate.dimensions))
    dimension_labels = dict(left.dimension_labels)
    dimension_labels.update(dict(right.dimension_labels))
    if graph_candidate is not left or graph_candidate is not right:
        dimension_labels.update(dict(graph_candidate.dimension_labels))
    source_kinds = _union(left.source_kinds or (left.source_kind,), right.source_kinds or (right.source_kind,))
    provenance = "complete" if left.provenance_status == right.provenance_status == "complete" else (
        "partial" if left.provenance_status != "missing" or right.provenance_status != "missing" else "missing"
    )
    return replace(
        left,
        candidate_id=graph_candidate.candidate_id,
        source_kind="fused",
        source_kinds=source_kinds,
        dimensions=dimensions,
        dimension_labels=dimension_labels,
        section_title=graph_candidate.section_title or left.section_title,
        # A fused diagnostic candidate inherits the graph path's exact source
        # boundary. Unioning the matching section candidate here widens one
        # fault row back to the whole section and defeats server-owned scope.
        evidence_refs=graph_candidate.evidence_refs,
        pages=graph_candidate.pages,
        source_chunk_uids=graph_candidate.source_chunk_uids,
        document_version=left.document_version or right.document_version,
        path_id=graph_candidate.path_id or left.path_id or right.path_id,
        node_ids=_union(left.node_ids, right.node_ids),
        graph_path_ids=_union(left.graph_path_ids, right.graph_path_ids),
        graph_node_ids=_union(left.graph_node_ids, right.graph_node_ids),
        graph_score=max(left.graph_score, right.graph_score),
        distinguishing_features=_union(left.distinguishing_features, right.distinguishing_features),
        verification_actions=_union(left.verification_actions, right.verification_actions),
        hard_conflicts=_union(left.hard_conflicts, right.hard_conflicts),
        provenance_status=provenance,
    )


class CandidateFusionEngine:
    """Merge independently retrieved candidates without label-based joins."""

    def fuse(
        self,
        section_candidates: Iterable[KnowledgeCandidate],
        graph_candidates: Iterable[KnowledgeCandidate],
        contract: QueryContract,
        resolved_scope: object | None = None,
    ) -> tuple[KnowledgeCandidate, ...]:
        fused: list[KnowledgeCandidate] = []
        for candidate in (*tuple(section_candidates), *tuple(graph_candidates)):
            if _identity_conflicts(candidate, contract):
                candidate = replace(
                    candidate,
                    hard_conflicts=_union(candidate.hard_conflicts, ("device_identity_conflict",)),
                )
            for index, existing in enumerate(fused):
                if _can_merge(existing, candidate):
                    fused[index] = _merge(existing, candidate)
                    break
            else:
                fused.append(candidate)
        return tuple(fused)


__all__ = ["CandidateFusionEngine"]
