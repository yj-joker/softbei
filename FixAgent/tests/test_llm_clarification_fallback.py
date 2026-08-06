from __future__ import annotations

import asyncio
import json
from dataclasses import replace

from api import main
from services.clarification.llm_fallback import LLMClarificationService
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


def _draft_payload() -> dict:
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
    assert result.dimension == "symptom"
    assert len(result.options) == 3
    assert result.options[0]["constraints"] == {
        "clarification_source": "llm_fallback",
        "clarification_dimension": "symptom",
        "clarification_value": "无法启动",
        "clarification_round": 1,
        "symptoms": ["无法启动"],
    }
    # Model output is never allowed to supply trusted graph/document IDs.
    assert "document_id" not in json.dumps(result.options, ensure_ascii=False)


def test_llm_fallback_rejects_diagnosis_or_repair_options() -> None:
    payload = _draft_payload()
    payload["options"] = [
        {"label": "一定是曲轴故障", "value": "一定是曲轴故障"},
        {"label": "立即更换轴承", "value": "立即更换轴承"},
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
    assert routed.clarification_question == "当前最明显的异常表现是哪一种？"
    assert execution is not None
    assert execution.metadata["pending_clarification"]["question"] == routed.clarification_question
    assert len(execution.metadata["pending_clarification"]["candidates"]) == 3

    state = main._clarification_state_store().create(
        "llm-question-state",
        execution.metadata["pending_clarification"],
        route_snapshot=routed.to_dict(),
    )
    assert state.question == routed.clarification_question
    assert state.to_dict()["question"] == routed.clarification_question


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


def test_usable_graph_clarification_keeps_priority_over_llm(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "get_llm_service",
        lambda: (_ for _ in ()).throw(AssertionError("LLM fallback must not run")),
    )
    decision = IntentDecision(intent="fault_diagnosis", task_action="find_cause")
    graph_plan = replace(
        _plan(),
        action=RouteAction.CLARIFY,
        clarification_kind="graph_scope",
        clarification_options=({"id": "A", "label": "驱动单元"},),
    )

    routed = asyncio.run(main._maybe_apply_llm_clarification(
        graph_plan,
        intent_decision=decision,
        graph_candidates=(object(),),
        context={},
    ))

    assert routed == graph_plan


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
