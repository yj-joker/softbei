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


def test_force_grounded_answer_preserves_pre_retrieved_graph_evidence(monkeypatch) -> None:
    class _RetrievalTool:
        async def run(self, **kwargs):
            item = SimpleNamespace(
                id="manual-chunk-1",
                score=0.9,
                content="检查张紧轮安装状态。",
                metadata={
                    "qualification": "qualified",
                    "document_id": "manual-1",
                    "document_version": "v1",
                    "chunk_id": "manual-chunk-1",
                    "page": 12,
                    "evidence_bundle": {
                        "coverage_status": "complete",
                        "aspect_support": [{
                            "aspect_id": "inspection",
                            "aspect_text": "检查方法",
                            "supported": True,
                            "evidence_ids": ["manual-chunk-1"],
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

    class _GroundedLLM:
        async def chat(self, **kwargs):
            return {"content": "可依据手册检查张紧轮安装状态。"}

    graph_batch = {
        "status": "found",
        "diagnostics": {"qualified_count": 1},
        "evidence": [{
            "evidence_id": "graph:kgpath:device-1:component-1:fault-1:none",
            "source_type": "graph",
            "qualification": "qualified",
            "path_id": "kgpath:device-1:component-1:fault-1",
            "node_ids": ["device-1", "component-1", "fault-1"],
            "relationship_types": ["OWNS", "CAUSES"],
            "claim_types": ["fault_relation"],
            "device": {"id": "device-1", "name": "一号发动机"},
            "component": {"id": "component-1", "name": "张紧轮"},
            "fault": {"id": "fault-1", "name": "轴承磨损"},
            "solution": {},
            "source": {"document_id": "manual-1", "section_id": "sec-bearing", "pages": [12]},
            "text": "一号发动机 -> OWNS -> 张紧轮 -> CAUSES -> 轴承磨损",
        }],
    }
    monkeypatch.setattr(
        "tools.knowledge_retrieval_tool.get_knowledge_retrieval_tool",
        lambda: _RetrievalTool(),
    )
    monkeypatch.setattr("services.llm.service.get_llm_service", lambda: _GroundedLLM())
    agent = FixAgent(_GroundedLLM())
    input_data = AgentInput(
        user_message="张紧轮异响如何检查？",
        session_id="response-plan-graph-retain",
        context={"retrieval_scope": {"document_id": "manual-1"}},
    )
    context = AgentRunContext(
        user_message=input_data.user_message,
        retrieval_scope={"document_id": "manual-1"},
        intent_decision=_knowledge_context()["intent_decision"],
        graph_pre_retrieval=graph_batch,
        graph_scope={"allowed_path_ids": ["kgpath:device-1:component-1:fault-1"]},
    )

    output = asyncio.run(agent.force_grounded_answer(input_data, context))

    trace = output.metadata["react_trace"]
    assert any(
        call.get("name") == "java_graph_diagnosis_path"
        and call.get("execution_status") == "server_pre_retrieval"
        for step in trace
        for call in step.get("tool_calls") or []
    )
    assert output.metadata["graph_pre_retrieval"]["evidence"][0]["evidence_id"].startswith("graph:")


def test_react_finalizer_persists_final_graph_claim_bindings() -> None:
    graph_id = "graph:kgpath:bus:compressor:fault-off:none"
    graph_entry = {
        "evidence_id": graph_id,
        "source_type": "graph",
        "qualification": "qualified",
        "path_id": "kgpath:bus:compressor:fault-off",
        "node_ids": ["bus", "compressor", "fault-off"],
        "relationship_types": ["OWNS", "CAUSES"],
        "claim_types": ["device_identity", "component_ownership", "fault_relation"],
        "supports_aspect_ids": ["device", "component", "fault-cause"],
        "device": {"id": "bus", "name": "纯电动客车"},
        "component": {"id": "compressor", "name": "空气压缩机"},
        "fault": {"id": "fault-off", "name": "空压机不工作"},
        "solution": {},
        "source": {"document_id": "manual-bus", "section_id": "sec-compressor"},
        "text": "纯电动客车 -> OWNS -> 空气压缩机 -> CAUSES -> 空压机不工作",
    }
    manual_bundle = {
        "coverage_status": "complete",
        "coverage_reason": "graph_capability_supported",
        "aspect_support": [{
            "aspect_id": "fault-cause",
            "aspect_text": "故障关系",
            "supported": True,
            "evidence_ids": [graph_id],
            "supporting_source_types": ["graph"],
            "aspect_origin": "graph_capability",
            "user_obligation": True,
        }],
        "missing_aspect_ids": [],
        "conflict_eligible": [],
        "capabilities": {"may_offer_generic_guidance": False},
    }
    trace = [
        {
            "iteration": 0,
            "action": "server_pre_retrieval",
            "tool_calls": [{
                "name": "java_graph_diagnosis_path",
                "executed": True,
                "result_data": {"status": "found", "evidence": [graph_entry]},
                "evidence": [graph_entry],
            }],
        },
        {
            "iteration": 1,
            "action": "tool_call",
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "executed": True,
                "result_data": manual_bundle,
                "evidence": [],
            }],
        },
    ]
    output = AgentOutput(
        agent_name="fix_agent",
        message="纯电动客车的空气压缩机发生空压机不工作故障。",
        tools_used=["knowledge_retrieval", "java_graph_diagnosis_path"],
        metadata={"react_trace": trace},
    )
    context = AgentRunContext(user_message="空压机不工作是什么故障？")

    finalized = FixAgent(_BombLLM())._finalize_react_knowledge_output(output, context)

    assert graph_id in finalized.metadata["allowed_evidence_refs"]
    assert finalized.metadata["authorized_claim_evidence_bindings"]
    assert finalized.metadata["graph_evidence_used_ids"] == [graph_id]
    assert finalized.metadata["claim_evidence_bindings"]
    assert all(
        graph_id in binding["evidence_ids"]
        for binding in finalized.metadata["claim_evidence_bindings"]
    )


def test_react_finalizer_fuses_graph_when_manual_bundle_is_unsupported() -> None:
    graph_id = "graph:kgpath:device-1:component-1:fault-1:none"
    graph_entry = {
        "evidence_id": graph_id,
        "source_type": "graph",
        "qualification": "qualified",
        "path_id": "kgpath:device-1:component-1:fault-1",
        "node_ids": ["device-1", "component-1", "fault-1"],
        "relationship_types": ["OWNS", "CAUSES"],
        "claim_types": ["device_identity", "component_ownership", "fault_relation"],
        "supports_aspect_ids": ["device", "component", "fault-cause"],
        "device": {"id": "device-1", "name": "engine"},
        "component": {"id": "component-1", "name": "spark-plug"},
        "fault": {"id": "fault-1", "name": "spark-plug-damaged"},
        "solution": {},
        "source": {"document_id": "manual-1", "section_id": "sec-1"},
        "text": "engine -> OWNS -> spark-plug -> CAUSES -> spark-plug-damaged",
    }
    manual_bundle = {
        "coverage_status": "unsupported",
        "coverage_reason": "zero_qualified_evidence",
        "aspect_support": [{
            "aspect_id": "aspect-manual",
            "aspect_text": "manual procedure",
            "supported": False,
            "evidence_ids": [],
        }],
        "missing_aspect_ids": ["aspect-manual"],
        "conflict_eligible": [],
    }
    trace = [
        {
            "iteration": 0,
            "action": "server_pre_retrieval",
            "tool_calls": [{
                "name": "java_graph_diagnosis_path",
                "executed": True,
                "result_data": {"status": "found", "evidence": [graph_entry]},
                "evidence": [graph_entry],
            }],
        },
        {
            "iteration": 1,
            "action": "tool_call",
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "executed": True,
                "result_data": manual_bundle,
                "evidence": [],
            }],
        },
    ]
    output = AgentOutput(
        agent_name="fix_agent",
        message="engine: spark-plug has the spark-plug-damaged fault.",
        tools_used=["knowledge_retrieval", "java_graph_diagnosis_path"],
        metadata={"react_trace": trace},
    )
    context = AgentRunContext(
        user_message=(
            "\u706b\u82b1\u585e\u51fa\u73b0\u635f\u574f\u65f6\u5e94\u5982\u4f55\u5904\u7406\uff1f"
            "\u8bf7\u8bf4\u660e\u6545\u969c\u6240\u5c5e\u90e8\u4ef6\u548c\u624b\u518c\u4f9d\u636e\u3002"
        )
    )

    finalized = FixAgent(_BombLLM())._finalize_react_knowledge_output(output, context)

    assert graph_id in finalized.metadata["allowed_evidence_refs"]
    assert finalized.metadata["authorized_claim_evidence_bindings"]
    assert finalized.metadata["graph_evidence_used_ids"] == [graph_id]
    assert finalized.metadata["claim_evidence_bindings"]
    assert all(
        graph_id in binding["evidence_ids"]
        for binding in finalized.metadata["claim_evidence_bindings"]
    )


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
