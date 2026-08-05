from __future__ import annotations

import asyncio
import json

import pytest

from agents.base_agent import AgentInput
from agents.base_agent import AgentOutput
from api import main
from schemas.request import ChatRequest


def _pending() -> dict:
    return {
        "clarification_id": "clarification-stable",
        "kind": "evidence_conflict",
        "subject": "紧固扭矩",
        "alternatives": [
            {
                "id": "A",
                "value": "10",
                "unit": "N·m",
                "label": "10 N·m",
                "evidence_refs": ["manual:torque-10"],
                "source_labels": ["手册第10页，版本v1"],
            },
            {
                "id": "B",
                "value": "100",
                "unit": "N·m",
                "label": "100 N·m",
                "evidence_refs": ["manual:torque-100"],
                "source_labels": ["手册第11页，版本v2"],
            },
        ],
        "evidence_refs": ["manual:torque-10", "manual:torque-100"],
        "missing_identity_fields": ["文档版本"],
        "question": "请确认文档版本，也可以直接选择 A/B。",
        "status": "awaiting_answer",
        "original_query": "右曲轴箱盖紧固扭矩是多少？",
    }


async def _awaitable(value):
    return value


async def _empty_async(*args, **kwargs):
    return []


def _prepared_input(request: ChatRequest, answer: str) -> AgentInput:
    return AgentInput(
        user_message=answer,
        session_id=request.session_id,
        context={
            "pending_clarification": _pending(),
            "intent_decision": {"intent": "chat_social"},
            "scope_decision": {"status": "unknown"},
            "response_policy": {"mode": "GENERAL_AI", "style_profile": "general_ai"},
        },
    )


def test_non_stream_pending_conflict_precedes_general_ai_and_restores_query(monkeypatch) -> None:
    request = ChatRequest(session_id="clarify-non-stream", message="B")
    input_data = _prepared_input(request, "B")

    async def _unexpected_policy(*args, **kwargs):
        pytest.fail("pending clarification must be handled before GENERAL_AI")

    class _UnexpectedReview:
        async def review(self, *args, **kwargs):
            pytest.fail("resolved clarification should be deterministic")

    monkeypatch.setattr(main, "_prepare_chat_agent_input", lambda request: _awaitable(input_data))
    monkeypatch.setattr(main, "_try_response_policy_direct", _unexpected_policy)
    monkeypatch.setattr(main, "get_review_agent", lambda: _UnexpectedReview())
    monkeypatch.setattr(main, "_collect_direct_section_table_items", _empty_async)
    monkeypatch.setattr(main, "_format_inventory_table_answer_from_metadata", lambda *args: None)
    monkeypatch.setattr(main, "_format_manual_evidence_answer_from_metadata", lambda *args: None)
    monkeypatch.setattr(main, "_collect_direct_section_images", _empty_async)
    monkeypatch.setattr(main, "_collect_direct_evidence_page_images", lambda *args: [])

    response = asyncio.run(main.chat(request))

    assert "右曲轴箱盖紧固扭矩是多少" in response.message
    assert "100 N·m" in response.message
    assert not response.message.startswith("已按你的确认采用")
    assert response.metadata["restored_query"] == _pending()["original_query"]
    assert response.metadata["selected_evidence_refs"] == ["manual:torque-100"]
    assert response.metadata["evidence_constraints"]["allowed_evidence_refs"] == ["manual:torque-100"]
    assert response.metadata["pending_clarification"]["status"] == "resolved"


def test_non_stream_deterministic_renderers_use_restored_business_query(monkeypatch) -> None:
    request = ChatRequest(session_id="clarify-restored-render", message="A")
    original_query = "星门耦联簇的校准参数是多少？"
    input_data = AgentInput(
        user_message=original_query,
        session_id=request.session_id,
        context={
            "response_policy": {"mode": "PENDING_RETRIEVAL", "manual_citation_allowed": True},
            "retrieval_scope": {
                "document_id": "manual-a",
                "allowed_section_ids": ["section-a"],
                "allowed_evidence_refs": ["chunk-a"],
            },
            "route_plan": {
                "action": "grounded_retrieval",
                "entity_role": "document_component",
                "selected_document_id": "manual-a",
            },
        },
    )
    routed = AgentOutput(
        agent_name="fix_agent",
        message="模型未能稳定组织证据。",
        tools_used=["knowledge_retrieval"],
        metadata={"deterministic_direct": True},
    )
    seen: list[tuple[str, str]] = []

    async def _no_causal(*args, **kwargs):
        return None

    async def _route(*args, **kwargs):
        return routed

    async def _collect(query: str, metadata: dict):
        seen.append(("collect", query))
        return []

    def _format_table(query: str, metadata: dict, extra_items=None):
        seen.append(("table", query))
        return "确定性证据答案"

    async def _finalize(request, input_data, output, *, candidate_message=None):
        output.message = str(candidate_message or output.message)
        return output

    monkeypatch.setattr(main, "_prepare_chat_agent_input", lambda request: _awaitable(input_data))
    monkeypatch.setattr(main, "_try_causal_follow_up_resolution", _no_causal)
    monkeypatch.setattr(main, "_try_route_plan_direct", _route)
    monkeypatch.setattr(main, "_collect_direct_section_table_items", _collect)
    monkeypatch.setattr(main, "_format_inventory_table_answer_from_metadata", _format_table)
    monkeypatch.setattr(
        main,
        "_format_manual_evidence_answer_from_metadata",
        lambda *args: pytest.fail("table answer should already be deterministic"),
    )
    monkeypatch.setattr(main, "_collect_direct_section_images", _empty_async)
    monkeypatch.setattr(main, "_collect_direct_evidence_page_images", lambda *args: [])
    monkeypatch.setattr(main, "_finalize_knowledge_output_with_fallback", _finalize)

    response = asyncio.run(main.chat(request))

    assert response.message == "确定性证据答案"
    assert seen == [("collect", original_query), ("table", original_query)]
    assert response.metadata["retrieval_scope"] == input_data.context["retrieval_scope"]


def test_stream_unresolved_clarification_repeats_same_question_before_general_ai(monkeypatch) -> None:
    request = ChatRequest(session_id="clarify-stream", message="我不确定")
    input_data = _prepared_input(request, "我不确定")

    async def _unexpected_policy(*args, **kwargs):
        pytest.fail("pending clarification must be handled before GENERAL_AI")

    monkeypatch.setattr(main, "_prepare_chat_agent_input", lambda request: _awaitable(input_data))
    monkeypatch.setattr(main, "_try_response_policy_direct", _unexpected_policy)

    async def _consume() -> str:
        response = await main.chat_stream(request)
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
        return "".join(chunks)

    payload = asyncio.run(_consume())
    events = [
        json.loads(line.removeprefix("data: "))
        for line in payload.splitlines()
        if line.startswith("data: ")
    ]
    visible = "".join(
        event["data"]["content"]
        for event in events
        if event.get("event") == "token"
    )
    done = next(event for event in events if event.get("event") == "done")

    assert visible == _pending()["question"]
    repeated = done["data"]["metadata"]["pending_clarification"]
    assert repeated["status"] == "awaiting_answer"
    assert repeated["clarification_id"] == _pending()["clarification_id"]


def test_stream_done_metadata_preserves_full_pending_clarification() -> None:
    pending = _pending()
    event = {"event": "done", "data": {}}

    main._attach_stream_done_metadata(
        event,
        {
            "coverage_status": "conflict",
            "response_plan_id": "response-plan-conflict",
            "pending_clarification": pending,
        },
    )

    assert event["data"]["metadata"]["pending_clarification"] == pending


def test_pending_diagnostic_question_is_terminal_before_evidence_finalizer(monkeypatch) -> None:
    pending = {
        "kind": "diagnostic_cause",
        "status": "awaiting_answer",
        "question": "请补充最符合现场情况的现象。",
        "alternatives": [
            {"id": "A", "label": "现象甲"},
            {"id": "B", "label": "现象乙"},
        ],
    }
    output = AgentOutput(
        agent_name="fix_agent",
        message=pending["question"],
        metadata={"pending_clarification": pending},
    )
    request = ChatRequest(session_id="diagnostic-terminal", message="设备存在异常")
    input_data = AgentInput(user_message=request.message, session_id=request.session_id)

    monkeypatch.setattr(
        main,
        "_finalize_knowledge_output",
        lambda *args, **kwargs: pytest.fail("clarification must bypass evidence finalizer"),
    )
    monkeypatch.setattr(
        main,
        "_try_post_retrieval_ai_fallback",
        lambda *args, **kwargs: pytest.fail("clarification must bypass AI fallback"),
    )

    result = asyncio.run(main._finalize_knowledge_output_with_fallback(request, input_data, output))

    assert result.message == pending["question"]
    assert result.metadata["pending_clarification"] == pending
