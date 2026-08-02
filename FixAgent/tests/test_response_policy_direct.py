from __future__ import annotations

import asyncio

from agents.base_agent import AgentInput
from api import main
from schemas.request import ChatRequest


class _FakeLLM:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []

    async def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return {"content": self.content}


class _SequenceLLM:
    def __init__(self, contents: list[str]) -> None:
        self.contents = list(contents)
        self.calls: list[dict] = []

    async def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return {"content": self.contents.pop(0)}


def test_general_ai_direct_does_not_use_knowledge_tools(monkeypatch) -> None:
    llm = _FakeLLM("级数是按顺序相加的一列数所形成的对象。")
    monkeypatch.setattr(main, "get_llm_service", lambda: llm)
    request = ChatRequest(session_id="general-1", message="讲讲级数")
    input_data = AgentInput(
        user_message=request.message,
        session_id=request.session_id,
        context={
            "intention": "chat_social",
            "turn_ts": 1,
            "intent_decision": {"intent": "chat_social", "chat_subtype": "general_knowledge"},
            "response_policy": {"mode": "GENERAL_AI", "style_profile": "general_ai"},
        },
    )

    output = asyncio.run(main._try_response_policy_direct(request, input_data))

    assert output is not None
    assert output.tools_used == []
    assert output.metadata["execution_mode"] == "general_ai_direct"
    assert "知识库" not in output.message


def test_general_ai_direct_regenerates_emoji_violation_once(monkeypatch) -> None:
    llm = _SequenceLLM([
        "🔹级数是按顺序相加的一列数所形成的对象。",
        "级数是按顺序相加的一列数所形成的对象。",
    ])
    monkeypatch.setattr(main, "get_llm_service", lambda: llm)
    request = ChatRequest(session_id="general-emoji-1", message="讲讲级数")
    input_data = AgentInput(
        user_message=request.message,
        session_id=request.session_id,
        context={
            "intention": "chat_social",
            "turn_ts": 5,
            "intent_decision": {"intent": "chat_social", "chat_subtype": "general_knowledge"},
            "response_policy": {"mode": "GENERAL_AI", "style_profile": "general_ai"},
        },
    )

    output = asyncio.run(main._try_response_policy_direct(request, input_data))

    assert output is not None
    assert output.message == "级数是按顺序相加的一列数所形成的对象。"
    assert output.metadata["style_regenerated"] is True
    assert len(llm.calls) == 2
    assert "禁止使用 emoji" in llm.calls[0]["messages"][0]["content"]


def test_maintenance_ai_fallback_enforces_source_disclaimer(monkeypatch) -> None:
    llm = _FakeLLM("可以先记录异响出现的工况和位置。")
    monkeypatch.setattr(main, "get_llm_service", lambda: llm)
    request = ChatRequest(session_id="fallback-1", message="飞机发动机运行时异响是什么原因")
    input_data = AgentInput(
        user_message=request.message,
        session_id=request.session_id,
        context={
            "intention": "fault_diagnosis",
            "turn_ts": 2,
            "intent_decision": {"intent": "fault_diagnosis"},
            "scope_decision": {"status": "out_of_scope", "reason": "unsupported_device"},
            "response_policy": {"mode": "MAINTENANCE_AI_FALLBACK", "style_profile": "maintenance_ai"},
        },
    )

    output = asyncio.run(main._try_response_policy_direct(request, input_data))

    assert output is not None
    assert output.tools_used == []
    assert all(marker in output.message for marker in ("知识库", "AI", "仅供参考"))
    assert output.metadata["source_type"] == "ai"


def test_maintenance_ai_fallback_removes_unverified_exact_values_and_citations(monkeypatch) -> None:
    llm = _FakeLLM(
        "知识库没有该设备对应文档，以下内容来自 AI，仅供参考。\n"
        "可以先记录异响出现的工况和位置。\n"
        "叶片叶尖间隙通常小于 0.1 mm。\n"
        "任何异响均需按 CCAR-121.705 / FAR 121.705 报修。\n"
        "具体拆装见手册第 18 页。"
    )
    monkeypatch.setattr(main, "get_llm_service", lambda: llm)
    request = ChatRequest(session_id="fallback-guard-1", message="飞机发动机运行时异响是什么原因")
    input_data = AgentInput(
        user_message=request.message,
        session_id=request.session_id,
        context={
            "intention": "fault_diagnosis",
            "turn_ts": 3,
            "intent_decision": {"intent": "fault_diagnosis"},
            "scope_decision": {"status": "out_of_scope", "reason": "device_document_conflict"},
            "response_policy": {"mode": "MAINTENANCE_AI_FALLBACK", "style_profile": "maintenance_ai"},
        },
    )

    output = asyncio.run(main._try_response_policy_direct(request, input_data))

    assert output is not None
    assert "记录异响出现的工况和位置" in output.message
    assert all(marker in output.message for marker in ("知识库", "AI", "仅供参考"))
    assert all(term not in output.message for term in ("0.1 mm", "CCAR-121.705", "FAR 121.705", "第 18 页"))
    assert output.metadata["fallback_safety_filters"] == [
        "unverified_measurement",
        "unverified_reference",
    ]


def test_maintenance_ai_fallback_adds_only_missing_disclaimer_parts(monkeypatch) -> None:
    llm = _FakeLLM(
        "当前知识库未找到对应设备文档，以下分析基于通用工程经验，仅供参考。"
        "可以先记录异响出现的工况和位置。"
    )
    monkeypatch.setattr(main, "get_llm_service", lambda: llm)
    request = ChatRequest(session_id="fallback-disclaimer-1", message="飞机发动机异响是什么原因")
    input_data = AgentInput(
        user_message=request.message,
        session_id=request.session_id,
        context={
            "intention": "fault_diagnosis",
            "turn_ts": 4,
            "intent_decision": {"intent": "fault_diagnosis"},
            "scope_decision": {"status": "out_of_scope", "reason": "device_document_conflict"},
            "response_policy": {"mode": "MAINTENANCE_AI_FALLBACK", "style_profile": "maintenance_ai"},
        },
    )

    output = asyncio.run(main._try_response_policy_direct(request, input_data))

    assert output is not None
    assert "AI" in output.message
    assert output.message.count("知识库") == 1
    assert output.message.count("仅供参考") == 1


def test_legacy_maintenance_fallback_uses_shared_plain_text_rules(monkeypatch) -> None:
    llm = _FakeLLM("可以先记录异常出现的工况和伴随现象。")
    monkeypatch.setattr(main, "get_llm_service", lambda: llm)
    input_data = AgentInput(
        user_message="接下来怎么排查",
        session_id="legacy-fallback-style-1",
        context={"intent_decision": {"intent": "task_chat", "policy": {}}},
    )

    result = asyncio.run(main._maintenance_fallback_answer(input_data, {}))

    assert result == "可以先记录异常出现的工况和伴随现象。"
    system_prompt = llm.calls[0]["messages"][0]["content"]
    assert "禁止使用 emoji" in system_prompt
    assert "禁止使用 Markdown" in system_prompt
