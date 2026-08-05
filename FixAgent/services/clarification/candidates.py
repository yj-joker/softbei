"""Build clarification candidates from dynamic document and section metadata."""

from __future__ import annotations

from collections import OrderedDict
from difflib import SequenceMatcher
import re
from typing import Iterable

from services.clarification.models import KnowledgeCandidate
from services.retrieval.device_identity import DeviceCatalog
from services.retrieval.section_index import SectionRef


def _source_chunks(ref: SectionRef) -> tuple[str, ...]:
    """保留可绑定的来源块标识，展示页码不作为 chunk 身份。"""
    return tuple(
        value
        for value in (getattr(ref, "evidence_refs", ()) or ())
        if str(value or "").strip() and not str(value).startswith("page:")
    )


def build_section_candidates(
    section_refs: Iterable[SectionRef],
    catalog: DeviceCatalog,
    *,
    query: str = "",
) -> tuple[KnowledgeCandidate, ...]:
    unique: OrderedDict[str, SectionRef] = OrderedDict()
    for ref in section_refs:
        document_id = str(ref.document_id or "").strip()
        section_id = str(ref.section_id or "").strip()
        if not document_id or not section_id or catalog.document(document_id) is None:
            continue
        unique.setdefault(f"{document_id}:{section_id}", ref)

    candidates: list[KnowledgeCandidate] = []
    for candidate_id, ref in unique.items():
        document = catalog.document(ref.document_id)
        document_label = (
            str(getattr(document, "device_name", "") or "").strip()
            if document is not None
            else ""
        ) or ref.document_id
        retrieval_score = max(0.0, min(1.0, float(getattr(ref, "retrieval_score", 0.0) or 0.0)))
        semantic_score = _title_query_similarity(query, ref.core_title or ref.full_title)
        target_score = semantic_score if query else retrieval_score
        context_score = semantic_score if query else retrieval_score
        dimensions = {
            "document_id": ref.document_id,
            "section_id": ref.section_id,
            "section_title": ref.core_title,
        }
        if document is not None:
            for key in (
                "device_name",
                "device_category",
                "carrier_or_application",
                "manufacturer",
                "model",
            ):
                value = str(getattr(document, key, "") or "").strip()
                if value:
                    dimensions[key] = value
        labels = {
            "document_id": document_label,
            "section_id": ref.full_title or ref.core_title or ref.section_id,
            "section_title": ref.full_title or ref.core_title,
        }
        for dimension in (
            "procedure_action",
            "procedure_target",
            "assembly_context",
            "orientation",
            "part_name",
            "parameter_field",
        ):
            value = str(getattr(ref, dimension, "") or "").strip()
            if value:
                dimensions[dimension] = value
                labels[dimension] = value
        source_chunks = _source_chunks(ref)
        candidates.append(
            KnowledgeCandidate(
                candidate_id=candidate_id,
                document_id=ref.document_id,
                section_id=ref.section_id,
                section_title=ref.full_title or ref.core_title,
                dimensions=dimensions,
                dimension_labels=labels,
                identity_score=1.0,
                target_score=target_score,
                context_score=context_score,
                field_score=0.80,
                retrieval_score=retrieval_score,
                evidence_refs=tuple(getattr(ref, "evidence_refs", ()) or ()),
                pages=tuple(getattr(ref, "pages", ()) or ()),
                source_kind="section",
                source_kinds=("section",),
                document_version=str(getattr(document, "document_version", "") or ""),
                source_chunk_uids=source_chunks,
                graph_score=0.0,
                provenance_status="complete" if source_chunks else "partial",
            )
        )
    return tuple(candidates)


def _title_query_similarity(query: str, title: str) -> float:
    normalize = lambda value: re.sub(r"[^\w\u4e00-\u9fff]+", "", str(value or "")).casefold()
    query_text = normalize(query)
    title_text = normalize(title)
    if not query_text or not title_text:
        return 0.0
    sequence = SequenceMatcher(None, title_text, query_text).ratio()
    coverage = len(title_text) / max(len(query_text), 1) if title_text in query_text else 0.0
    if coverage >= 0.5:
        return 1.0
    return round(min(1.0, 0.75 * sequence + 0.25 * coverage), 6)


def unresolved_section_dimensions(
    candidates: Iterable[KnowledgeCandidate],
) -> tuple[str, ...]:
    values = tuple(candidates)
    document_ids = {candidate.document_id for candidate in values if candidate.document_id}
    if len(document_ids) > 1:
        return ("document_id",)
    section_ids = {candidate.section_id for candidate in values if candidate.section_id}
    if len(section_ids) > 1:
        semantic_dimensions = []
        for dimension in (
            "procedure_action",
            "assembly_context",
            "orientation",
            "procedure_target",
            "part_name",
            "parameter_field",
        ):
            distinct = {
                str(candidate.dimensions.get(dimension) or "").strip()
                for candidate in values
                if str(candidate.dimensions.get(dimension) or "").strip()
            }
            if len(distinct) > 1:
                semantic_dimensions.append(dimension)
        return tuple((*semantic_dimensions, "section_id"))
    return ()
