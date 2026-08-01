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
