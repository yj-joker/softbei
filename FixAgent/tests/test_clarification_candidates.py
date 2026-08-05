from __future__ import annotations

from services.clarification.candidates import build_section_candidates, unresolved_section_dimensions
from services.clarification.models import RiskLevel
from services.clarification.policy import ClarificationDecisionEngine
from services.retrieval.device_identity import DeviceCatalog
from services.retrieval.section_index import SectionRef


def _catalog() -> DeviceCatalog:
    return DeviceCatalog.from_manifests(
        (
            {
                "document_id": "manual-a",
                "status": "ready",
                "document_identity": {
                    "device_name": "苍穹装置",
                    "device_category": "试验装置",
                    "confidence": 0.98,
                },
            },
            {
                "document_id": "manual-b",
                "status": "ready",
                "document_identity": {
                    "device_name": "深澜装置",
                    "device_category": "试验装置",
                    "confidence": 0.98,
                },
            },
        )
    )


def test_same_document_sections_remain_separate_candidates() -> None:
    refs = (
        SectionRef("section-install", "manual-a", "耦联簇装配", "9.1 耦联簇装配"),
        SectionRef("section-remove", "manual-a", "耦联簇分解", "9.2 耦联簇分解"),
    )

    candidates = build_section_candidates(refs, _catalog())

    assert [candidate.candidate_id for candidate in candidates] == [
        "manual-a:section-install",
        "manual-a:section-remove",
    ]
    assert unresolved_section_dimensions(candidates) == ("section_id",)


def test_section_id_remains_available_when_semantic_dimensions_are_ambiguous() -> None:
    refs = (
        SectionRef(
            "section-install-alpha",
            "manual-a",
            "星门甲流程",
            "7.1 星门甲流程",
            procedure_action="装配",
            procedure_target="星门耦联簇",
        ),
        SectionRef(
            "section-install-beta",
            "manual-a",
            "星门乙流程",
            "7.2 星门乙流程",
            procedure_action="装配",
            procedure_target="星门涨紧簇",
        ),
        SectionRef(
            "section-remove",
            "manual-a",
            "星门丙流程",
            "7.3 星门丙流程",
            procedure_action="分解",
            procedure_target="星门耦联簇",
        ),
    )

    candidates = build_section_candidates(refs, _catalog())

    assert unresolved_section_dimensions(candidates) == (
        "procedure_action",
        "procedure_target",
        "section_id",
    )


def test_duplicate_section_refs_are_grouped_without_losing_section_label() -> None:
    refs = (
        SectionRef("section-cross-page", "manual-a", "跨页校准流程", "5.3 跨页校准流程"),
        SectionRef("section-cross-page", "manual-a", "跨页校准流程", "5.3 跨页校准流程"),
    )

    candidates = build_section_candidates(refs, _catalog())

    assert len(candidates) == 1
    assert candidates[0].dimension_labels["section_id"] == "5.3 跨页校准流程"


def test_cross_document_candidates_use_catalog_names_as_public_labels() -> None:
    refs = (
        SectionRef("shared-a", "manual-a", "共振隔离架", "4.1 共振隔离架"),
        SectionRef("shared-b", "manual-b", "共振隔离架", "6.2 共振隔离架"),
    )
    candidates = build_section_candidates(refs, _catalog())

    decision = ClarificationDecisionEngine().decide(
        candidates,
        risk_level=RiskLevel.LOW,
        unresolved_dimensions=unresolved_section_dimensions(candidates),
    )

    assert decision.should_clarify is True
    assert decision.question is not None
    assert decision.question.dimension == "document_id"
    assert {option.label for option in decision.question.options} == {"苍穹装置", "深澜装置"}


def test_same_document_question_exposes_section_titles_not_internal_ids() -> None:
    refs = (
        SectionRef("alpha", "manual-a", "星门甲流程", "7.1 星门甲流程"),
        SectionRef("beta", "manual-a", "星门乙流程", "7.2 星门乙流程"),
    )
    candidates = build_section_candidates(refs, _catalog())

    decision = ClarificationDecisionEngine().decide(
        candidates,
        risk_level=RiskLevel.HIGH,
        unresolved_dimensions=unresolved_section_dimensions(candidates),
    )

    assert decision.question is not None
    assert {option.label for option in decision.question.options} == {
        "7.1 星门甲流程",
        "7.2 星门乙流程",
    }


def test_section_candidate_preserves_real_evidence_scope() -> None:
    refs = (
        SectionRef(
            "section-install",
            "manual-a",
            "星门甲流程",
            "7.1 星门甲流程",
            procedure_action="装配",
            procedure_target="星门耦联簇",
            evidence_refs=("chunk-install-1", "chunk-install-2"),
            pages=(17, 18),
            retrieval_score=0.87,
        ),
    )

    candidate = build_section_candidates(refs, _catalog())[0]

    assert candidate.dimensions["procedure_action"] == "装配"
    assert candidate.dimensions["procedure_target"] == "星门耦联簇"
    assert candidate.evidence_refs == ("chunk-install-1", "chunk-install-2")
    assert candidate.pages == (17, 18)
    assert candidate.retrieval_score == 0.87
    assert candidate.source_kinds == ("section",)
    assert candidate.source_chunk_uids == ("chunk-install-1", "chunk-install-2")
    assert candidate.provenance_status == "complete"


def test_clarification_option_contains_only_its_real_candidate_scope() -> None:
    refs = (
        SectionRef(
            "section-install",
            "manual-a",
            "星门甲流程",
            "7.1 星门甲流程",
            procedure_action="装配",
            evidence_refs=("chunk-install",),
            pages=(17,),
            retrieval_score=0.91,
        ),
        SectionRef(
            "section-remove",
            "manual-a",
            "星门乙流程",
            "7.2 星门乙流程",
            procedure_action="分解",
            evidence_refs=("chunk-remove",),
            pages=(22,),
            retrieval_score=0.90,
        ),
    )
    candidates = build_section_candidates(refs, _catalog())

    decision = ClarificationDecisionEngine().decide(
        candidates,
        risk_level=RiskLevel.HIGH,
        unresolved_dimensions=("procedure_action",),
    )

    assert decision.question is not None
    by_value = {option.value: option for option in decision.question.options}
    install_scope = by_value["装配"].constraints
    remove_scope = by_value["分解"].constraints
    assert install_scope == {
        "procedure_action": "装配",
        "document_id": "manual-a",
        "allowed_section_ids": ["section-install"],
        "allowed_evidence_refs": ["chunk-install"],
        "allowed_source_chunk_uids": ["chunk-install"],
        "pages": [17],
    }
    assert remove_scope["allowed_section_ids"] == ["section-remove"]
    assert remove_scope["allowed_evidence_refs"] == ["chunk-remove"]
    assert "chunk-install" not in remove_scope["allowed_evidence_refs"]
