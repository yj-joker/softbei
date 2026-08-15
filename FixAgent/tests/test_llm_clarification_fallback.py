from __future__ import annotations

import asyncio
import json
from dataclasses import replace

from api import main
from services.clarification.llm_fallback import (
    LLMClarificationService,
    build_safe_observation_fallback,
)
from services.intent_router import IntentDecision
from services.retrieval.device_identity import QueryContract
from services.routing.executor import RouteExecutor
from services.routing.models import RouteAction, RoutePlan


class _FakeLLM:
    def __init__(self, payload: dict | Exception):
        self.payload = payload
        self.calls = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.payload, Exception):
            raise self.payload
        return {"content": json.dumps(self.payload, ensure_ascii=False)}


class _SequenceLLM:
    def __init__(self, payloads: list[dict | Exception]):
        self.payloads = list(payloads)
        self.calls = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        payload = self.payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return {"content": json.dumps(payload, ensure_ascii=False)}


def _draft_payload() -> dict:
    return {
        "should_clarify": True,
        "dimension": "operating_condition",
        "question": "异响最常出现在哪个运行阶段？",
        "options": [
            {"label": "启动瞬间", "value": "启动瞬间"},
            {"label": "怠速时", "value": "怠速时"},
            {"label": "加速过程中", "value": "加速过程中"},
            {"label": "减速时", "value": "减速时"},
        ],
        "reason": "已有异常现象，但缺少发生工况",
    }


def _symptom_draft_payload() -> dict:
    return {
        "should_clarify": True,
        "dimension": "symptom",
        "question": "当前最明显的异常表现是哪一种？",
        "options": [
            {"label": "无法启动", "value": "无法启动"},
            {"label": "运行中异响", "value": "运行中异响"},
            {"label": "温度异常升高", "value": "温度异常升高"},
        ],
        "reason": "描述只有设备级异常",
    }


def _plan() -> RoutePlan:
    contract = QueryContract.from_mapping(
        {
            "intent": "fault_diagnosis",
            "task_action": "find_cause",
            "raw_device_span": "发动机",
            "device_name": "发动机",
            "symptoms": ["损坏"],
        },
        raw_query="发动机损坏了",
    )
    return RoutePlan(
        action=RouteAction.AI_FALLBACK,
        intent="fault_diagnosis",
        task_action="find_cause",
        query_contract=contract,
        entity_role="device",
        candidate_document_ids=(),
        selected_document_id="",
        allowed_tools=(),
        answer_source="maintenance_ai",
        allow_ai_fallback=True,
        reason="no_candidate",
    )


def test_llm_fallback_builds_only_local_safe_constraints() -> None:
    llm = _FakeLLM(_draft_payload())
    result = asyncio.run(LLMClarificationService(llm).build(
        query="发动机损坏了",
        query_contract=_plan().query_contract.to_dict(),
        round_count=1,
    ))

    assert result is not None
    assert result.dimension == "operating_condition"
    assert len(result.options) == 4
    assert result.options[0]["constraints"] == {
        "clarification_source": "llm_fallback",
        "clarification_generation": "llm",
        "clarification_dimension": "operating_condition",
        "clarification_value": "启动瞬间",
        "clarification_round": 1,
        "operating_conditions": ["启动瞬间"],
    }
    request_payload = json.loads(llm.calls[0]["messages"][1]["content"])
    assert request_payload["required_dimension"] == "operating_condition"
    # Model output is never allowed to supply trusted graph/document IDs.
    assert "document_id" not in json.dumps(result.options, ensure_ascii=False)


def test_llm_dimension_label_is_corrected_using_server_owned_missing_slot() -> None:
    payload = _draft_payload()
    payload["dimension"] = "symptom"
    llm = _FakeLLM(payload)

    result = asyncio.run(LLMClarificationService(llm).build(
        query="发动机异响是什么原因？",
        query_contract={"symptoms": ["异响"]},
        round_count=1,
    ))

    assert result is not None
    assert result.dimension == "operating_condition"
    assert result.question == "异响最常出现在哪个运行阶段？"
    assert [option["value"] for option in result.options] == [
        "启动瞬间",
        "怠速时",
        "加速过程中",
        "减速时",
    ]
    assert all(
        option["constraints"]["clarification_generation"] == "llm"
        for option in result.options
    )


def test_invalid_first_draft_is_retried_once_with_server_constraints() -> None:
    llm = _SequenceLLM([
        {"should_clarify": False},
        _draft_payload(),
    ])

    result = asyncio.run(LLMClarificationService(llm).build(
        query="发动机异响是什么原因？",
        query_contract={"symptoms": ["异响"]},
        round_count=1,
    ))

    assert result is not None
    assert len(llm.calls) == 2
    assert all(
        option["constraints"]["clarification_generation"] == "llm_retry"
        for option in result.options
    )
    retry_messages = llm.calls[1]["messages"]
    assert len(retry_messages) == 4
    retry_instruction = json.loads(retry_messages[-1]["content"])
    assert retry_instruction["validation_retry"] is True
    assert retry_instruction["required_dimension"] == "operating_condition"


def test_llm_accepts_device_specific_temporal_conditions_without_hardcoded_parts() -> None:
    payload = {
        "should_clarify": True,
        "dimension": "operating_condition",
        "question": "异响是在哪种工况下出现的？",
        "options": [
            {"label": "踩下离合器踏板时", "value": "踩下离合器踏板时"},
            {"label": "松开离合器踏板时", "value": "松开离合器踏板时"},
            {
                "label": "车辆行驶中（离合器结合状态下）",
                "value": "车辆行驶中（离合器结合状态下）",
            },
            {"label": "挂挡或换挡过程中", "value": "挂挡或换挡过程中"},
        ],
        "reason": "缺少异响发生时机",
    }

    result = asyncio.run(LLMClarificationService(_FakeLLM(payload)).build(
        query="离合器异响是什么原因？",
        query_contract={"symptoms": ["异响"]},
        round_count=1,
    ))

    assert result is not None
    assert len(result.options) == 4
    assert all(
        option["constraints"]["clarification_generation"] == "llm"
        for option in result.options
    )


def test_temporal_condition_validator_still_rejects_causal_or_repair_choices() -> None:
    payload = {
        "should_clarify": True,
        "dimension": "operating_condition",
        "question": "异常在什么情况下出现？",
        "options": [
            {"label": "轴承磨损后", "value": "轴承磨损后"},
            {"label": "拆卸部件时", "value": "拆卸部件时"},
            {"label": "更换离合器后", "value": "更换离合器后"},
        ],
    }

    result = asyncio.run(LLMClarificationService(_FakeLLM(payload)).build(
        query="离合器异响是什么原因？",
        query_contract={"symptoms": ["异响"]},
        round_count=1,
    ))

    assert result is None


def test_observation_options_do_not_become_document_ids_in_pending_state() -> None:
    plan = replace(
        _plan(),
        action=RouteAction.CLARIFY,
        clarification_kind="llm_slot_clarification",
        clarification_question="当前最明显的异常表现是哪一种？",
        clarification_options=(
            {
                "id": "A",
                "label": "完全无法启动",
                "value": "完全无法启动",
                "constraints": {
                    "clarification_source": "llm_fallback",
                    "clarification_dimension": "symptom",
                    "symptoms": ["完全无法启动"],
                },
            },
            {
                "id": "B",
                "label": "启动后立即熄火",
                "value": "启动后立即熄火",
                "constraints": {
                    "clarification_source": "llm_fallback",
                    "clarification_dimension": "symptom",
                    "symptoms": ["启动后立即熄火"],
                },
            },
        ),
    )

    execution = asyncio.run(RouteExecutor().execute(plan))

    assert execution is not None
    assert "直接描述现场现象" in execution.message
    assert "候选范围" not in execution.message
    options = execution.metadata["pending_clarification"]["candidates"]
    assert [item["document_id"] for item in options] == ["", ""]
    assert [item["constraints"].get("symptoms") for item in options] == [
        ["完全无法启动"],
        ["启动后立即熄火"],
    ]


def test_llm_fallback_rejects_diagnosis_or_repair_options() -> None:
    payload = _symptom_draft_payload()
    payload["options"] = [
        {"label": "一定是曲轴故障", "value": "一定是曲轴故障"},
        {"label": "立即更换轴承", "value": "立即更换轴承"},
    ]

    result = asyncio.run(LLMClarificationService(_FakeLLM(payload)).build(
        query="发动机损坏了",
        query_contract={},
    ))

    assert result is None


def test_llm_fallback_rejects_component_or_operation_choices() -> None:
    payload = _symptom_draft_payload()
    payload["options"] = [
        {"label": "传动装置", "value": "传动装置"},
        {"label": "检查火花塞", "value": "检查火花塞"},
        {"label": "曲轴与平衡轴", "value": "曲轴与平衡轴"},
    ]

    result = asyncio.run(LLMClarificationService(_FakeLLM(payload)).build(
        query="发动机损坏了",
        query_contract={},
    ))

    assert result is None


def test_llm_failure_degrades_without_creating_pending_state() -> None:
    result = asyncio.run(LLMClarificationService(_FakeLLM(RuntimeError("timeout"))).build(
        query="发动机损坏了",
        query_contract={},
    ))

    assert result is None


def test_safe_fallback_asks_for_condition_when_symptom_is_known() -> None:
    result = build_safe_observation_fallback(
        {"symptoms": ["异响"]},
        round_count=1,
    )

    assert result.dimension == "operating_condition"
    assert result.question == "异常在什么工况下最明显？"
    assert len(result.options) == 4
    assert result.options[0]["constraints"] == {
        "clarification_source": "llm_fallback",
        "clarification_generation": "safe_default",
        "clarification_dimension": "operating_condition",
        "clarification_value": "冷机或启动时最明显",
        "clarification_round": 1,
        "operating_conditions": ["冷机或启动时最明显"],
    }


def test_safe_fallback_asks_for_symptom_when_observation_is_missing() -> None:
    result = build_safe_observation_fallback({}, round_count=1)

    assert result.dimension == "symptom"
    assert result.question == "当前最明显的异常表现是哪一种？"
    assert len(result.options) == 4
    assert all(option["constraints"]["symptoms"] for option in result.options)


def test_safe_fallback_does_not_repeat_confirmed_operating_condition() -> None:
    result = build_safe_observation_fallback(
        {
            "symptoms": ["异响"],
            "operating_conditions": ["冷机或启动时最明显"],
        },
        round_count=2,
    )

    assert result.dimension == "symptom"
    assert result.question == "当前最明显的异常表现是哪一种？"
    assert all(
        option["constraints"]["clarification_round"] == 2
        for option in result.options
    )


def test_graph_miss_routes_vague_diagnosis_to_structured_llm_clarification(monkeypatch) -> None:
    llm = _FakeLLM(_draft_payload())
    monkeypatch.setattr(main, "get_llm_service", lambda: llm)
    decision = IntentDecision(intent="fault_diagnosis", task_action="find_cause")

    routed = asyncio.run(main._maybe_apply_llm_clarification(
        _plan(),
        intent_decision=decision,
        graph_candidates=(),
        context={},
    ))
    execution = asyncio.run(RouteExecutor().execute(routed))

    assert routed.action == RouteAction.CLARIFY
    assert routed.clarification_kind == "llm_slot_clarification"
    assert routed.clarification_question == "异响最常出现在哪个运行阶段？"
    assert execution is not None
    assert execution.metadata["pending_clarification"]["question"] == routed.clarification_question
    assert len(execution.metadata["pending_clarification"]["candidates"]) == 4

    state = main._clarification_state_store().create(
        "llm-question-state",
        execution.metadata["pending_clarification"],
        route_snapshot=routed.to_dict(),
    )
    assert state.question == routed.clarification_question
    assert state.to_dict()["question"] == routed.clarification_question


def test_graph_miss_uses_safe_question_when_llm_declines(monkeypatch) -> None:
    llm = _FakeLLM({"should_clarify": False})
    monkeypatch.setattr(main, "get_llm_service", lambda: llm)
    decision = IntentDecision(intent="fault_diagnosis", task_action="find_cause")

    routed = asyncio.run(main._maybe_apply_llm_clarification(
        _plan(),
        intent_decision=decision,
        graph_candidates=(),
        context={},
    ))
    execution = asyncio.run(RouteExecutor().execute(routed))

    assert routed.action == RouteAction.CLARIFY
    assert routed.reason == "safe_observation_clarification_after_llm_gap"
    assert routed.clarification_kind == "llm_slot_clarification"
    assert routed.clarification_question == "异常在什么工况下最明显？"
    assert execution is not None
    assert len(execution.metadata["pending_clarification"]["candidates"]) == 4


def test_graph_miss_uses_safe_question_when_llm_is_unavailable(monkeypatch) -> None:
    llm = _FakeLLM(RuntimeError("timeout"))
    monkeypatch.setattr(main, "get_llm_service", lambda: llm)

    routed = asyncio.run(main._maybe_apply_llm_clarification(
        _plan(),
        intent_decision=IntentDecision(intent="fault_diagnosis", task_action="find_cause"),
        graph_candidates=(),
        context={},
    ))

    assert routed.action == RouteAction.CLARIFY
    assert routed.reason == "safe_observation_clarification_after_llm_gap"
    assert len(routed.clarification_options) == 4


def test_existing_document_does_not_skip_llm_clarification_after_graph_miss(monkeypatch) -> None:
    llm = _FakeLLM(_draft_payload())
    monkeypatch.setattr(main, "get_llm_service", lambda: llm)
    decision = IntentDecision(intent="fault_diagnosis", task_action="find_cause")
    grounded_plan = replace(
        _plan(),
        action=RouteAction.GROUNDED_RETRIEVAL,
        selected_document_id="robot-manual",
        candidate_document_ids=("robot-manual",),
        allowed_tools=("knowledge_retrieval",),
        answer_source="selected_document",
        allow_ai_fallback=False,
        reason="unique_identity_match",
    )

    routed = asyncio.run(main._maybe_apply_llm_clarification(
        grounded_plan,
        intent_decision=decision,
        graph_candidates=(),
        context={},
    ))

    assert routed.action == RouteAction.CLARIFY
    assert routed.selected_document_id == "robot-manual"
    assert routed.clarification_kind == "llm_slot_clarification"
    assert len(llm.calls) == 1


def test_usable_graph_observation_keeps_priority_over_llm(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "get_llm_service",
        lambda: (_ for _ in ()).throw(AssertionError("LLM fallback must not run")),
    )
    decision = IntentDecision(intent="fault_diagnosis", task_action="find_cause")
    graph_plan = replace(
        _plan(),
        action=RouteAction.CLARIFY,
        clarification_kind="graph_observation",
        clarification_options=({"id": "A", "label": "启动时无反应"},),
    )

    routed = asyncio.run(main._maybe_apply_llm_clarification(
        graph_plan,
        intent_decision=decision,
        graph_candidates=(object(),),
        context={},
    ))

    assert routed == graph_plan


def test_ambiguous_graph_candidates_are_context_for_observation_question() -> None:
    llm = _FakeLLM(_draft_payload())
    candidates = __import__(
        "services.clarification.graph_candidates",
        fromlist=["build_graph_candidates"],
    ).build_graph_candidates([{
        "pathId": "path-bearing",
        "deviceId": "device-1",
        "componentId": "component-1",
        "componentName": "曲轴",
        "faultId": "fault-1",
        "faultName": "轴承磨损",
        "graphScore": 0.78,
        "distinguishingFeatures": ["运行中异响"],
    }])

    result = asyncio.run(LLMClarificationService(llm).build(
        query="发动机损坏了",
        query_contract=_plan().query_contract.to_dict(),
        graph_candidates=candidates,
    ))

    assert result is not None
    request_payload = json.loads(llm.calls[0]["messages"][1]["content"])
    assert request_payload["knowledge_graph_status"] == "ambiguous_candidates"
    assert request_payload["knowledge_graph_candidates"] == [{
        "component": "曲轴",
        "possible_fault": "轴承磨损",
        "known_observations": ["运行中异响"],
    }]


def test_vague_query_without_existing_symptom_can_still_ask_observation(monkeypatch) -> None:
    llm = _FakeLLM(_symptom_draft_payload())
    monkeypatch.setattr(main, "get_llm_service", lambda: llm)
    vague_contract = QueryContract.from_mapping(
        {
            "intent": "fault_diagnosis",
            "task_action": "find_cause",
            "fault": "有问题",
            "raw_fault_span": "有问题",
        },
        raw_query="发动机有问题",
    )
    vague_plan = replace(_plan(), query_contract=vague_contract)

    routed = asyncio.run(main._maybe_apply_llm_clarification(
        vague_plan,
        intent_decision=IntentDecision(intent="fault_diagnosis", task_action="find_cause"),
        graph_candidates=(),
        context={},
    ))

    assert routed.action == RouteAction.CLARIFY
    assert routed.clarification_kind == "llm_slot_clarification"


def test_misclassified_maintenance_request_uses_observation_fallback(monkeypatch) -> None:
    llm = _FakeLLM(_draft_payload())
    monkeypatch.setattr(main, "get_llm_service", lambda: llm)
    plan = replace(
        _plan(),
        intent="maintenance_guidance",
        task_action="repair_guidance",
        reason="diagnostic_ambiguity_without_observable_discriminator",
    )
    decision = IntentDecision(
        intent="maintenance_guidance",
        task_action="repair_guidance",
        symptoms=("发动机损坏",),
    )

    routed = asyncio.run(main._maybe_apply_llm_clarification(
        plan,
        intent_decision=decision,
        graph_candidates=(),
        context={},
    ))

    assert routed.action == RouteAction.CLARIFY
    assert routed.clarification_kind == "llm_slot_clarification"


def test_misclassified_vague_repair_request_without_special_reason_still_clarifies(monkeypatch) -> None:
    llm = _FakeLLM(_draft_payload())
    monkeypatch.setattr(main, "get_llm_service", lambda: llm)
    plan = replace(
        _plan(),
        intent="maintenance_guidance",
        task_action="repair_guidance",
        reason="no_matching_document",
    )
    decision = IntentDecision(
        intent="maintenance_guidance",
        task_action="repair_guidance",
        symptoms=("离合器异响",),
    )

    routed = asyncio.run(main._maybe_apply_llm_clarification(
        plan,
        intent_decision=decision,
        graph_candidates=(),
        context={},
    ))

    assert routed.action == RouteAction.CLARIFY
    assert routed.clarification_kind == "llm_slot_clarification"


def test_well_specified_diagnosis_does_not_force_llm_clarification(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "get_llm_service",
        lambda: (_ for _ in ()).throw(AssertionError("LLM clarification must not run")),
    )
    contract = QueryContract.from_mapping(
        {
            "intent": "fault_diagnosis",
            "task_action": "find_cause",
            "component": "离合器",
            "raw_component_span": "离合器",
            "fault": "连续金属摩擦声",
            "raw_fault_span": "连续金属摩擦声",
            "symptoms": ["连续金属摩擦声"],
            "operating_conditions": ["冷启动时"],
        },
        raw_query="离合器在冷启动时出现连续金属摩擦声是什么原因",
    )
    plan = replace(_plan(), query_contract=contract)

    routed = asyncio.run(main._maybe_apply_llm_clarification(
        plan,
        intent_decision=IntentDecision(intent="fault_diagnosis", task_action="find_cause"),
        graph_candidates=(),
        context={},
    ))

    assert routed == plan


def test_parameter_query_never_uses_diagnostic_llm_clarification(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "get_llm_service",
        lambda: (_ for _ in ()).throw(AssertionError("parameter query must not clarify as diagnosis")),
    )
    contract = QueryContract.from_mapping(
        {
            "intent": "parameter_query",
            "task_action": "parameter_lookup",
            "component": "法兰面螺栓",
            "raw_component_span": "法兰面螺栓",
            "part_spec": "M6x60",
            "requested_fields": ["扭矩"],
        },
        raw_query="M6x60六角法兰面螺栓的扭矩是多少",
    )
    plan = replace(_plan(), query_contract=contract)
    decision = IntentDecision(intent="parameter_query", task_action="parameter_lookup")

    routed = asyncio.run(main._maybe_apply_llm_clarification(
        plan,
        intent_decision=decision,
        graph_candidates=(),
        context={},
    ))

    assert routed == plan


def test_question_asking_for_operating_stage_is_not_repeated_as_clarification(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "get_llm_service",
        lambda: (_ for _ in ()).throw(AssertionError("knowledge question must not be repeated")),
    )
    contract = QueryContract.from_mapping(
        {
            "intent": "fault_diagnosis",
            "task_action": "find_cause",
            "fault": "异响",
            "raw_fault_span": "异响",
            "symptoms": ["异响"],
        },
        raw_query="异响最常出现在哪个运行阶段？",
    )
    plan = replace(_plan(), query_contract=contract)

    routed = asyncio.run(main._maybe_apply_llm_clarification(
        plan,
        intent_decision=IntentDecision(intent="fault_diagnosis", task_action="find_cause"),
        graph_candidates=(),
        context={},
    ))

    assert routed == plan


def test_confirmed_option_is_merged_into_next_graph_query_contract() -> None:
    contract = _plan().query_contract
    merged = main._apply_llm_clarification_constraints(contract, {
        "clarification_source": "llm_fallback",
        "clarification_dimension": "symptom",
        "clarification_value": "无法启动",
        "clarification_round": 1,
        "symptoms": ["无法启动"],
    })

    assert merged.symptoms == ("损坏", "无法启动")
    assert "用户已确认：现场现象：无法启动" in main._clarified_query_text(merged)


def test_confirmed_graph_observation_is_merged_into_answer_query() -> None:
    contract = _plan().query_contract
    merged = main._apply_llm_clarification_constraints(contract, {
        "observable_symptom": "加速阶段皮带连续啸叫",
        "document_id": "manual-engine",
        "allowed_path_ids": ["path-belt-noise"],
    })

    assert merged.symptoms == ("损坏", "加速阶段皮带连续啸叫")
    assert "用户已确认：现场现象：加速阶段皮带连续啸叫" in main._clarified_query_text(merged)


def test_two_completed_llm_rounds_do_not_start_a_third(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "get_llm_service",
        lambda: (_ for _ in ()).throw(AssertionError("third clarification must not run")),
    )
    decision = IntentDecision(intent="fault_diagnosis", task_action="find_cause")
    context = {
        "clarification_constraints": {
            "clarification_source": "llm_fallback",
            "clarification_round": 2,
        }
    }

    routed = asyncio.run(main._maybe_apply_llm_clarification(
        _plan(),
        intent_decision=decision,
        graph_candidates=(),
        context=context,
    ))

    assert routed == _plan()
