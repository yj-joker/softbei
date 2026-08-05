from __future__ import annotations

from services.clarification.fusion import CandidateFusionEngine
from services.clarification.models import KnowledgeCandidate
from services.retrieval.device_identity import QueryContract


def _candidate(
    *,
    candidate_id: str,
    source_kind: str,
    document_id: str,
    section_id: str,
    section_title: str = "clutch",
    evidence_refs: tuple[str, ...] = (),
    device_name: str = "",
    path_id: str = "",
) -> KnowledgeCandidate:
    dimensions = {
        "document_id": document_id,
        "section_id": section_id,
        "component": section_title,
    }
    if device_name:
        dimensions["device_identity"] = device_name
    return KnowledgeCandidate(
        candidate_id=candidate_id,
        document_id=document_id,
        section_id=section_id,
        section_title=section_title,
        dimensions=dimensions,
        dimension_labels={"section_id": section_title},
        identity_score=1.0,
        target_score=0.9,
        context_score=0.8,
        field_score=0.8,
        retrieval_score=0.8,
        evidence_refs=evidence_refs,
        source_chunk_uids=tuple(item for item in evidence_refs if not item.startswith("page:")),
        source_kind=source_kind,
        source_kinds=(source_kind,),
        path_id=path_id,
        graph_path_ids=(path_id,) if path_id else (),
        graph_score=0.9 if source_kind == "graph" else 0.0,
        provenance_status="complete" if evidence_refs else "partial",
    )


def _contract(device_name: str = "") -> QueryContract:
    return QueryContract.from_mapping(
        {
            "raw_device_span": device_name,
            "component": "clutch",
            "raw_component_span": "clutch",
            "task_action": "find_cause",
        },
        raw_query=f"{device_name} clutch noise cause",
    )


def test_fusion_joins_by_document_and_section_not_display_name() -> None:
    section = _candidate(
        candidate_id="section:manual-1:6.2",
        source_kind="section",
        document_id="manual-1",
        section_id="6.2",
        evidence_refs=("chunk-23",),
    )
    graph = _candidate(
        candidate_id="graph:path-1",
        source_kind="graph",
        document_id="manual-1",
        section_id="6.2",
        evidence_refs=("chunk-23",),
        path_id="path-1",
    )

    result = CandidateFusionEngine().fuse((section,), (graph,), _contract())

    assert len(result) == 1
    assert result[0].source_kinds == ("section", "graph")
    assert result[0].graph_path_ids == ("path-1",)
    assert result[0].source_chunk_uids == ("chunk-23",)


def test_fusion_never_joins_same_label_across_documents() -> None:
    result = CandidateFusionEngine().fuse(
        (
            _candidate(
                candidate_id="section:manual-1:6.2",
                source_kind="section",
                document_id="manual-1",
                section_id="6.2",
            ),
        ),
        (
            _candidate(
                candidate_id="graph:path-2",
                source_kind="graph",
                document_id="manual-2",
                section_id="6.2",
                path_id="path-2",
            ),
        ),
        _contract(),
    )

    assert len(result) == 2


def test_explicit_device_conflict_is_a_hard_filter() -> None:
    result = CandidateFusionEngine().fuse(
        (),
        (
            _candidate(
                candidate_id="graph:path-motorcycle",
                source_kind="graph",
                document_id="motorcycle-manual",
                section_id="6.2",
                device_name="motorcycle engine",
                path_id="path-motorcycle",
            ),
        ),
        _contract("aircraft engine"),
    )

    assert "device_identity_conflict" in result[0].hard_conflicts
