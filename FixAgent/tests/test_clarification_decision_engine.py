from __future__ import annotations

from services.clarification.models import KnowledgeCandidate, RiskLevel
from services.clarification.policy import ClarificationDecisionEngine, calculate_risk_level


def _candidate(
    candidate_id: str,
    *,
    document_id: str = "manual-a",
    section_id: str,
    dimensions: dict[str, str],
    identity: float = 1.0,
    target: float = 1.0,
    context: float = 1.0,
    fields: float = 1.0,
    retrieval: float = 0.9,
    hard_conflicts: tuple[str, ...] = (),
) -> KnowledgeCandidate:
    return KnowledgeCandidate(
        candidate_id=candidate_id,
        document_id=document_id,
        section_id=section_id,
        section_title=f"section {section_id}",
        dimensions=dimensions,
        identity_score=identity,
        target_score=target,
        context_score=context,
        field_score=fields,
        retrieval_score=retrieval,
        hard_conflicts=hard_conflicts,
    )


def test_high_risk_same_document_sections_clarify_on_dynamic_action_dimension() -> None:
    candidates = (
        _candidate(
            "manual-a:section-install",
            section_id="section-install",
            dimensions={"procedure_action": "耦合", "orientation": "甲侧"},
            retrieval=0.92,
        ),
        _candidate(
            "manual-a:section-remove",
            section_id="section-remove",
            dimensions={"procedure_action": "解耦", "orientation": "甲侧"},
            retrieval=0.90,
        ),
    )

    decision = ClarificationDecisionEngine().decide(
        candidates,
        risk_level=RiskLevel.HIGH,
        unresolved_dimensions=("procedure_action", "orientation"),
    )

    assert decision.should_clarify is True
    assert decision.question is not None
    assert decision.question.dimension == "procedure_action"
    assert {option.value for option in decision.question.options} == {"耦合", "解耦"}
    assert decision.selected_candidate_id == ""


def test_information_gain_prefers_even_candidate_partition_without_term_tables() -> None:
    candidates = (
        _candidate("c1", section_id="s1", dimensions={"assembly_context": "环境一", "orientation": "方向甲"}),
        _candidate("c2", section_id="s2", dimensions={"assembly_context": "环境一", "orientation": "方向甲"}),
        _candidate("c3", section_id="s3", dimensions={"assembly_context": "环境二", "orientation": "方向甲"}),
        _candidate("c4", section_id="s4", dimensions={"assembly_context": "环境二", "orientation": "方向乙"}),
    )

    decision = ClarificationDecisionEngine().decide(
        candidates,
        risk_level=RiskLevel.MEDIUM,
        unresolved_dimensions=("assembly_context", "orientation"),
    )

    assert decision.should_clarify is True
    assert decision.question is not None
    assert decision.question.dimension == "assembly_context"
    assert decision.question.score_breakdown["information_gain"] >= 0.49


def test_one_turn_resolution_beats_coarse_action_partition() -> None:
    candidates = tuple(
        _candidate(
            f"c{index}",
            section_id=f"section-{index}",
            dimensions={
                "procedure_action": "装配" if index <= 3 else "拆卸",
                "section_id": f"section-{index}",
            },
        )
        for index in range(1, 7)
    )

    decision = ClarificationDecisionEngine().decide(
        candidates,
        risk_level=RiskLevel.HIGH,
        unresolved_dimensions=("procedure_action", "section_id"),
    )

    assert decision.should_clarify is True
    assert decision.question is not None
    assert decision.question.dimension == "section_id"
    assert decision.question.score_breakdown["one_turn_resolution"] == 1.0


def test_hard_conflict_is_eliminated_instead_of_being_offset_by_retrieval_score() -> None:
    candidates = (
        _candidate(
            "wrong-device",
            document_id="manual-foreign",
            section_id="s1",
            dimensions={"document_id": "manual-foreign"},
            retrieval=1.0,
            hard_conflicts=("device_identity",),
        ),
        _candidate(
            "right-device",
            document_id="manual-right",
            section_id="s2",
            dimensions={"document_id": "manual-right"},
            retrieval=0.72,
        ),
    )

    decision = ClarificationDecisionEngine().decide(
        candidates,
        risk_level=RiskLevel.HIGH,
        unresolved_dimensions=(),
    )

    assert decision.should_clarify is False
    assert decision.selected_candidate_id == "right-device"
    assert decision.candidate_ids == ("right-device",)


def test_unique_high_confidence_candidate_answers_without_requesting_device() -> None:
    candidate = _candidate(
        "manual-a:unique-section",
        section_id="unique-section",
        dimensions={"component": "星门耦联簇", "requested_field": "构成明细"},
    )

    decision = ClarificationDecisionEngine().decide(
        (candidate,),
        risk_level=RiskLevel.LOW,
        unresolved_dimensions=(),
    )

    assert decision.should_clarify is False
    assert decision.selected_candidate_id == candidate.candidate_id
    assert decision.reason == "unique_qualified_candidate"


def test_cross_document_scope_difference_requires_clarification_even_at_low_risk() -> None:
    candidates = (
        _candidate(
            "manual-a:s1",
            document_id="manual-a",
            section_id="s1",
            dimensions={"document_id": "manual-a", "component": "共振隔离架"},
        ),
        _candidate(
            "manual-b:s2",
            document_id="manual-b",
            section_id="s2",
            dimensions={"document_id": "manual-b", "component": "共振隔离架"},
        ),
    )

    decision = ClarificationDecisionEngine().decide(
        candidates,
        risk_level=RiskLevel.LOW,
        unresolved_dimensions=("document_id",),
    )

    assert decision.should_clarify is True
    assert decision.question is not None
    assert decision.question.dimension == "document_id"


def test_risk_is_computed_from_structured_actions_and_cannot_be_lowered_by_model() -> None:
    assert calculate_risk_level(task_action="parameter_lookup") == RiskLevel.HIGH
    assert calculate_risk_level(task_action="repair_guidance") == RiskLevel.HIGH
    assert calculate_risk_level(task_action="find_cause") == RiskLevel.MEDIUM
    assert calculate_risk_level(task_action="inventory_list") == RiskLevel.LOW
    assert (
        calculate_risk_level(task_action="find_cause", model_hint=RiskLevel.LOW)
        == RiskLevel.MEDIUM
    )
    assert (
        calculate_risk_level(task_action="inventory_list", model_hint=RiskLevel.HIGH)
        == RiskLevel.HIGH
    )


def test_high_risk_incomplete_provenance_cannot_be_auto_selected() -> None:
    candidate = _candidate(
        "manual-a:procedure",
        section_id="procedure",
        dimensions={"component": "clutch"},
    )
    candidate = candidate.__class__(**{
        **candidate.__dict__,
        "provenance_status": "partial",
    })

    decision = ClarificationDecisionEngine().decide(
        (candidate,),
        risk_level=RiskLevel.HIGH,
        unresolved_dimensions=(),
    )

    assert decision.selected_candidate_id == ""
    assert decision.reason == "high_risk_provenance_incomplete"
