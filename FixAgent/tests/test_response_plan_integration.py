"""ResponsePlan integration regressions for prompts and fallback paths."""

import asyncio
from types import SimpleNamespace

import pytest

from agents.base_agent import AgentInput, AgentOutput, AgentRunContext, BaseAgent
from agents.fix_agent import FixAgent, build_fix_agent_system_prompt
from api import main
from services.retrieval.aspects import QuestionAspect
from services.retrieval.qualification import qualify_candidates


class _BombLLM:
    async def chat(self, **kwargs):
        raise AssertionError("knowledge unsupported fallback must not call an LLM")


def _knowledge_context() -> dict:
    return {
        "intent_decision": {
            "intent": "fault_diagnosis",
            "policy": {"evidence_level": "required", "requires_knowledge_retrieval": True},
        }
    }


def test_fix_agent_prompt_forbids_unsupported_generic_completion() -> None:
    prompt = build_fix_agent_system_prompt()

    assert "无合格证据时，不得用通用知识补全常见原因、参数或操作步骤" in prompt
    assert "之后仍须继续回答：基于通用专业知识" not in prompt
    assert "不得因为手册未收录就完全拒绝回答" not in prompt
    assert "0设备→图谱中无此部件，降级到通用建议" not in prompt


def test_runtime_contract_uses_four_coverage_states() -> None:
    agent = FixAgent(_BombLLM())
    context = AgentRunContext(
        user_message="火花塞间隙是多少？",
        intent_decision=_knowledge_context()["intent_decision"],
    )

    prompt = agent.get_system_prompt_for_run(context)

    for status in ("complete", "partial", "unsupported", "conflict"):
        assert status in prompt
    assert "no_evidence/empty：明确未找到当前设备资料；可给通用原理" not in prompt


def test_fix_agent_unsupported_fallback_is_deterministic(monkeypatch) -> None:
    monkeypatch.setattr("services.llm.service.get_llm_service", lambda: _BombLLM())
    agent = FixAgent(_BombLLM())
    context = AgentRunContext(
        user_message="飞机发动机坏了有哪些常见原因？",
        intent_decision=_knowledge_context()["intent_decision"],
    )

    output = asyncio.run(
        agent._generic_guidance_output(context.user_message, context, "empty_retrieval")
    )

    assert "没有找到足以回答该问题的可靠依据" in output.message
    assert output.metadata["coverage_status"] == "unsupported"
    assert output.metadata["response_plan_id"].startswith("response-plan-")
    assert output.metadata["deterministic_direct"] is True


def test_api_knowledge_fallback_does_not_call_llm(monkeypatch) -> None:
    monkeypatch.setattr(main, "get_llm_service", lambda: _BombLLM())
    input_data = AgentInput(
        user_message="飞机发动机坏了有哪些常见原因？",
        session_id="response-plan-fallback",
        context=_knowledge_context(),
    )

    answer = asyncio.run(
        main._maintenance_fallback_answer(input_data, {"scene": "maintenance"})
    )

    assert "没有找到足以回答该问题的可靠依据" in answer
    assert "常见原因包括" not in answer


def test_force_grounded_answer_audits_unbound_model_facts(monkeypatch) -> None:
    class _RetrievalTool:
        async def run(self, **kwargs):
            item = SimpleNamespace(
                id="chunk-1",
                score=0.9,
                content="火花塞间隙标准为 0.7 到 0.9 mm。",
                metadata={
                    "qualification": "qualified",
                    "document_id": "manual-1",
                    "document_version": "v1",
                    "chunk_id": "chunk-1",
                    "page": 3,
                    "evidence_bundle": {
                        "coverage_status": "complete",
                        "coverage_reason": "all_aspects_supported",
                        "aspect_support": [{
                            "aspect_id": "gap",
                            "aspect_text": "火花塞间隙标准",
                            "supported": True,
                            "evidence_ids": ["chunk-1"],
                        }],
                        "missing_aspect_ids": [],
                        "conflict_eligible": [],
                    },
                },
            )
            item.model_dump = lambda: {
                "id": item.id,
                "score": item.score,
                "content": item.content,
                "metadata": item.metadata,
            }
            return SimpleNamespace(success=True, data=[item], error=None)

    class _HallucinatingLLM:
        def __init__(self):
            self.calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            return {"content": "火花塞间隙必须调到 1.2 mm。"}

    llm = _HallucinatingLLM()
    monkeypatch.setattr(
        "tools.knowledge_retrieval_tool.get_knowledge_retrieval_tool",
        lambda: _RetrievalTool(),
    )
    monkeypatch.setattr("services.llm.service.get_llm_service", lambda: llm)
    agent = FixAgent(llm)
    input_data = AgentInput(
        user_message="火花塞间隙标准是多少？",
        session_id="response-plan-audit",
        context={"retrieval_scope": {"document_id": "manual-1"}},
    )
    context = AgentRunContext(
        user_message=input_data.user_message,
        retrieval_scope={"document_id": "manual-1"},
        intent_decision=_knowledge_context()["intent_decision"],
    )

    output = asyncio.run(agent.force_grounded_answer(input_data, context))

    assert llm.calls == 1
    assert "1.2" not in output.message
    assert "0.7 到 0.9 mm" in output.message
    assert output.metadata["response_audit"]["used_fallback"] is True
    assert output.metadata["response_plan_id"].startswith("response-plan-")


def test_main_react_path_finalizes_unsupported_trace_without_generic_cause(monkeypatch) -> None:
    payload = {
        "evidence_status": "no_evidence",
        "coverage_status": "unsupported",
        "coverage_reason": "zero_qualified_evidence",
        "aspect_support": [{
            "aspect_id": "cause",
            "aspect_text": "无法启动原因",
            "supported": False,
            "evidence_ids": [],
        }],
        "missing_aspect_ids": ["cause"],
        "conflict_eligible": [],
        "capabilities": {"may_cite_manual": False, "may_offer_generic_guidance": False},
        "results": [],
        "reference_evidence": [],
        "excluded_evidence": [],
    }

    async def _react_with_unsupported_trace(self, input_data, max_iterations=10, _event_sink=None):
        return AgentOutput(
            agent_name="fix_agent",
            message="点火线圈损坏会导致发动机无法启动。",
            tools_used=["knowledge_retrieval"],
            metadata={
                "react_trace": [{
                    "iteration": 1,
                    "action": "tool_call",
                    "tool_calls": [{
                        "name": "knowledge_retrieval",
                        "result_data": payload,
                    }],
                }],
            },
        )

    monkeypatch.setattr(BaseAgent, "run_with_react", _react_with_unsupported_trace)
    agent = FixAgent(_BombLLM())
    input_data = AgentInput(
        user_message="发动机无法启动是什么原因？",
        session_id="response-plan-main-react",
        context=_knowledge_context(),
    )

    output = asyncio.run(agent.run_with_react(input_data))

    assert "点火线圈损坏" not in output.message
    assert "没有找到足以回答该问题的可靠依据" in output.message
    assert output.metadata["coverage_status"] == "unsupported"
    assert output.metadata["response_audit"]["used_fallback"] is True


def test_force_grounded_answer_preserves_conflict_values_and_public_sources(monkeypatch) -> None:
    candidates = [
        {
            "doc_id": "gap-a",
            "content": "火花塞间隙为 0.7 mm",
            "score": 0.9,
            "metadata": {
                "document_id": "manual-1",
                "document_version": "v1",
                "device_type": "engine",
                "chunk_id": "gap-a",
                "page": 3,
                "section_title": "火花塞间隙",
                "parameter_names": ["火花塞间隙"],
                "numeric_values": ["0.7"],
                "units": ["mm"],
                "local_rerank_features": {"query_coverage": 0.9, "title_coverage": 0.9},
            },
        },
        {
            "doc_id": "gap-b",
            "content": "火花塞间隙为 0.9 mm",
            "score": 0.9,
            "metadata": {
                "document_id": "manual-1",
                "document_version": "v2",
                "device_type": "engine",
                "chunk_id": "gap-b",
                "page": 8,
                "section_title": "火花塞间隙",
                "parameter_names": ["火花塞间隙"],
                "numeric_values": ["0.9"],
                "units": ["mm"],
                "local_rerank_features": {"query_coverage": 0.9, "title_coverage": 0.9},
            },
        },
    ]
    bundle = qualify_candidates(
        "火花塞间隙是多少？",
        candidates,
        document_id="manual-1",
        device_type="engine",
        requires_strict_evidence=True,
        aspects=[QuestionAspect("gap", "火花塞间隙")],
    )
    items = []
    for row in bundle["reference_evidence"]:
        metadata = dict(row["metadata"])
        metadata["evidence_bundle"] = bundle
        dumped = {**row, "metadata": metadata}
        item = SimpleNamespace(
            id=row["doc_id"],
            score=row["score"],
            content=row["content"],
            metadata=metadata,
        )
        item.model_dump = lambda dumped=dumped: dumped
        items.append(item)

    class _ConflictTool:
        async def run(self, **kwargs):
            return SimpleNamespace(success=True, data=items, error=None)

    monkeypatch.setattr(
        "tools.knowledge_retrieval_tool.get_knowledge_retrieval_tool",
        lambda: _ConflictTool(),
    )
    monkeypatch.setattr("services.llm.service.get_llm_service", lambda: _BombLLM())
    agent = FixAgent(_BombLLM())
    input_data = AgentInput(
        user_message="火花塞间隙是多少？",
        session_id="response-plan-conflict",
        context={"retrieval_scope": {"document_id": "manual-1", "device_type": "engine"}},
    )
    context = AgentRunContext(
        user_message=input_data.user_message,
        retrieval_scope={"document_id": "manual-1", "device_type": "engine"},
        intent_decision=_knowledge_context()["intent_decision"],
    )

    output = asyncio.run(agent.force_grounded_answer(input_data, context))

    assert "0.7 mm（手册第3页，版本v1）" in output.message
    assert "0.9 mm（手册第8页，版本v2）" in output.message
    assert "gap-a" not in output.message and "gap-b" not in output.message
    assert output.metadata["coverage_status"] == "conflict"


@pytest.mark.parametrize("generation_mode", ["exception", "empty"])
def test_force_grounded_answer_generation_failure_uses_same_plan_fallback(
    monkeypatch,
    generation_mode,
) -> None:
    class _RetrievalTool:
        async def run(self, **kwargs):
            item = SimpleNamespace(
                id="chunk-1",
                score=0.9,
                content="火花塞间隙标准为 0.7 到 0.9 mm。",
                metadata={
                    "qualification": "qualified",
                    "document_id": "manual-1",
                    "document_version": "v1",
                    "chunk_id": "chunk-1",
                    "page": 3,
                    "evidence_bundle": {
                        "coverage_status": "complete",
                        "aspect_support": [{
                            "aspect_id": "gap",
                            "aspect_text": "火花塞间隙标准",
                            "supported": True,
                            "evidence_ids": ["chunk-1"],
                        }],
                        "conflict_eligible": [],
                        "capabilities": {"may_cite_manual": True},
                    },
                },
            )
            item.model_dump = lambda: {
                "id": item.id,
                "score": item.score,
                "content": item.content,
                "metadata": item.metadata,
            }
            return SimpleNamespace(success=True, data=[item], error=None)

    class _FailingOrEmptyLLM:
        def __init__(self):
            self.calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            if generation_mode == "exception":
                raise RuntimeError("generation failed")
            return {"content": ""}

    llm = _FailingOrEmptyLLM()
    monkeypatch.setattr(
        "tools.knowledge_retrieval_tool.get_knowledge_retrieval_tool",
        lambda: _RetrievalTool(),
    )
    monkeypatch.setattr("services.llm.service.get_llm_service", lambda: llm)
    agent = FixAgent(llm)
    input_data = AgentInput(
        user_message="火花塞间隙标准是多少？",
        session_id=f"response-plan-{generation_mode}",
        context={"retrieval_scope": {"document_id": "manual-1"}},
    )
    context = AgentRunContext(
        user_message=input_data.user_message,
        retrieval_scope={"document_id": "manual-1"},
        intent_decision=_knowledge_context()["intent_decision"],
    )

    output = asyncio.run(agent.force_grounded_answer(input_data, context))

    assert llm.calls == 1
    assert output is not None
    assert "0.7 到 0.9 mm" in output.message
    assert output.metadata["response_audit"]["used_fallback"] is True
