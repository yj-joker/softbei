"""Unified finalization regressions for every knowledge-output path."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from agents.base_agent import AgentInput, AgentOutput
from api import main
from schemas.request import AgentMode, ChatRequest
from services.retrieval.evidence import EvidenceLedger


def _manual_trace() -> list[dict]:
    bundle = {
        "coverage_status": "complete",
        "aspect_support": [{
            "aspect_id": "gap",
            "aspect_text": "火花塞间隙标准",
            "supported": True,
            "evidence_ids": ["chunk-1"],
        }],
        "missing_aspect_ids": [],
        "conflict_eligible": [],
        "capabilities": {"may_cite_manual": True},
    }
    return [{
        "iteration": 1,
        "action": "tool_call",
        "tool_calls": [{
            "name": "knowledge_retrieval",
            "result_data": [{
                "id": "chunk-1",
                "content": "火花塞间隙标准为 0.7 到 0.9 mm。",
                "metadata": {
                    "qualification": "qualified",
                    "document_id": "manual-1",
                    "document_version": "v1",
                    "chunk_id": "chunk-1",
                    "page": 3,
                    "evidence_bundle": bundle,
                },
            }],
        }],
    }]


def _rule_trace() -> list[dict]:
    return [{
        "iteration": 0,
        "tool_calls": [{
            "name": "domain_rule_engine",
            "result_data": {
                "message": "优先检查活塞环磨损。",
                "status": "active",
                "rule": {
                    "rule_id": 7,
                    "status": "active",
                    "conclusion": "优先检查活塞环磨损。",
                },
                "evidence_sources": [{"document_id": "manual-1", "page": 9}],
            },
        }],
    }]


def _output(mode: str, trace: list[dict], *, tools: list[str] | None = None) -> AgentOutput:
    return AgentOutput(
        agent_name="fix_agent",
        message="旧回答",
        tools_used=["knowledge_retrieval"] if tools is None else tools,
        metadata={
            "execution_mode": mode,
            "react_trace": trace,
            "scope_decision": {"status": "in_scope", "document_id": "manual-1"},
        },
    )


@pytest.mark.parametrize(
    "mode",
    [
        "react",
        "rag_fast_path",
        "rag_table_direct",
        "manual_section_direct",
        "manual_image_direct",
    ],
)
def test_all_manual_knowledge_paths_share_final_audit(mode: str) -> None:
    output = _output(mode, _manual_trace())

    finalized = main._finalize_knowledge_output(
        "火花塞间隙标准是多少？",
        output,
        candidate_message="火花塞间隙标准为 1.2 mm。",
    )

    assert "1.2" not in finalized.message
    assert "0.7 到 0.9 mm" in finalized.message
    assert finalized.metadata["coverage_status"] == "complete"
    assert finalized.metadata["response_plan_id"].startswith("response-plan-")
    assert len(finalized.metadata["evidence_ledger_digest"]) == 64
    assert finalized.metadata["scope_decision"]["status"] == "in_scope"
    assert finalized.metadata["response_audit"]["used_fallback"] is True


def test_domain_rule_direct_uses_same_finalizer() -> None:
    output = _output(
        "domain_rule_direct",
        _rule_trace(),
        tools=["domain_rule_engine"],
    )

    finalized = main._finalize_knowledge_output(
        "发动机冒蓝烟怎么查？",
        output,
        candidate_message="优先检查活塞环磨损。",
    )

    assert finalized.message == "优先检查活塞环磨损。"
    assert finalized.metadata["coverage_status"] == "complete"
    assert finalized.metadata["response_audit"]["passed"] is True


def test_non_knowledge_chat_does_not_enter_evidence_finalizer() -> None:
    output = _output("react", [], tools=[])
    output.metadata.pop("scope_decision")

    finalized = main._finalize_knowledge_output(
        "你好",
        output,
        candidate_message="你好，需要我帮你查什么？",
    )

    assert finalized.message == "你好，需要我帮你查什么？"
    assert "coverage_status" not in finalized.metadata
    assert "response_plan_id" not in finalized.metadata


def test_stream_done_always_contains_knowledge_diagnostics() -> None:
    output = main._finalize_knowledge_output(
        "火花塞间隙标准是多少？",
        _output("rag_fast_path", _manual_trace()),
        candidate_message="火花塞间隙标准为 0.7 到 0.9 mm。",
    )
    event = {"event": "done", "data": {"tools_used": output.tools_used}}

    main._attach_stream_done_metadata(event, output.metadata)

    metadata = event["data"]["metadata"]
    assert set((
        "scope_decision",
        "coverage_status",
        "response_plan_id",
        "evidence_ledger_digest",
    )).issubset(metadata)


def test_domain_rule_direct_is_finalized_before_direct_stream(monkeypatch) -> None:
    match = {
        "message": "优先检查活塞环磨损。",
        "status": "active",
        "rule": {
            "rule_id": 7,
            "status": "active",
            "conclusion": "优先检查活塞环磨损。",
        },
        "evidence_sources": [{"document_id": "manual-1", "page": 9}],
    }

    async def _match(*args, **kwargs):
        return match

    monkeypatch.setattr(main, "match_domain_rule", _match)
    request = ChatRequest(
        session_id="rule-finalized",
        message="发动机冒蓝烟怎么查？",
        mode=AgentMode.DIAGNOSIS,
    )
    input_data = AgentInput(
        user_message=request.message,
        session_id=request.session_id,
        context={"scope_decision": {"status": "in_scope"}},
    )

    output = asyncio.run(main._try_domain_rule_direct(request, input_data))

    assert output is not None
    assert output.metadata["coverage_status"] == "complete"
    assert output.metadata["response_plan_id"].startswith("response-plan-")


def test_rag_fast_path_is_finalized_before_return(monkeypatch) -> None:
    trace_item = _manual_trace()[0]["tool_calls"][0]["result_data"][0]
    item = SimpleNamespace(
        id=trace_item["id"],
        content=trace_item["content"],
        score=0.9,
        metadata=trace_item["metadata"],
    )
    item.model_dump = lambda: trace_item

    class _Tool:
        async def run(self, **kwargs):
            return SimpleNamespace(success=True, data=[item], error=None)

    class _LLM:
        async def chat(self, **kwargs):
            return {"content": "火花塞间隙标准为 1.2 mm。"}

    monkeypatch.setattr(main, "get_knowledge_retrieval_tool", lambda: _Tool())
    monkeypatch.setattr(main, "get_llm_service", lambda: _LLM())
    monkeypatch.setattr(main, "_collect_direct_section_table_items", _async_empty)
    request = ChatRequest(
        session_id="fast-finalized",
        message="火花塞间隙标准是多少？",
        mode=AgentMode.RETRIEVAL,
    )
    input_data = AgentInput(
        user_message=request.message,
        session_id=request.session_id,
        context={"retrieval_scope": {"document_id": "manual-1"}},
    )

    output = asyncio.run(main._run_rag_fast_path(request, input_data))

    assert output is not None
    assert "1.2" not in output.message
    assert output.metadata["response_audit"]["used_fallback"] is True


async def _async_none(*args, **kwargs):
    return None


async def _async_empty(*args, **kwargs):
    return []


def test_non_stream_endpoint_audits_after_manual_override(monkeypatch) -> None:
    output = _output("react", _manual_trace())
    output.message = "火花塞间隙标准为 0.7 到 0.9 mm。"
    request = ChatRequest(
        session_id="non-stream-finalized",
        message="火花塞间隙标准是多少？",
        mode=AgentMode.RETRIEVAL,
    )
    input_data = AgentInput(
        user_message=request.message,
        session_id=request.session_id,
        context={"scope_decision": {"status": "in_scope"}},
    )

    class _Agent:
        async def run_with_react(self, input_value):
            return output

    class _Review:
        async def review(self, value, level="full"):
            return value

    monkeypatch.setattr(main, "_prepare_chat_agent_input", lambda request: _awaitable(input_data))
    monkeypatch.setattr(main, "_try_causal_follow_up_resolution", _async_none)
    monkeypatch.setattr(main, "_try_scope_guard", lambda *args: None)
    monkeypatch.setattr(main, "_try_domain_rule_direct", _async_none)
    monkeypatch.setattr(main, "_should_use_rag_fast_path", lambda request: False)
    monkeypatch.setattr(main, "get_fix_agent", lambda: _Agent())
    monkeypatch.setattr(main, "get_review_agent", lambda: _Review())
    monkeypatch.setattr(main, "_collect_direct_section_table_items", _async_empty)
    monkeypatch.setattr(main, "_format_inventory_table_answer_from_metadata", lambda *args: None)
    monkeypatch.setattr(
        main,
        "_format_manual_evidence_answer_from_metadata",
        lambda *args: "火花塞间隙标准为 1.2 mm。",
    )
    monkeypatch.setattr(main, "_collect_direct_section_images", _async_empty)
    monkeypatch.setattr(main, "_collect_direct_evidence_page_images", lambda *args: [])
    monkeypatch.setattr(main, "build_follow_up", lambda *args: None)

    response = asyncio.run(main.chat(request))

    assert "1.2" not in response.message
    assert response.metadata["coverage_status"] == "complete"


async def _awaitable(value):
    return value


def test_stream_endpoint_audits_override_and_emits_metadata(monkeypatch) -> None:
    trace = _manual_trace()
    request = ChatRequest(
        session_id="stream-finalized",
        message="火花塞间隙标准是多少？",
        mode=AgentMode.RETRIEVAL,
    )
    input_data = AgentInput(
        user_message=request.message,
        session_id=request.session_id,
        context={"scope_decision": {"status": "in_scope"}},
    )

    class _Agent:
        async def run_with_react_stream(self, input_value):
            for char in "火花塞间隙标准为 0.7 到 0.9 mm。":
                yield {"event": "token", "data": {"content": char}}
            yield {
                "event": "done",
                "data": {
                    "tools_used": ["knowledge_retrieval"],
                    "latency_ms": 5,
                    "react_trace": trace,
                    "metadata": {
                        "react_trace": trace,
                        "scope_decision": {"status": "in_scope"},
                    },
                },
            }

        async def grounded_fallback_if_unretrieved(self, input_value, used_tools):
            return None

    class _Review:
        async def review(self, value, level="full"):
            return value

        def get_inline_markers(self, message, verification):
            return []

    monkeypatch.setattr(main, "_prepare_chat_agent_input", lambda request: _awaitable(input_data))
    monkeypatch.setattr(main, "_try_causal_follow_up_resolution", _async_none)
    monkeypatch.setattr(main, "_try_scope_guard", lambda *args: None)
    monkeypatch.setattr(main, "_try_domain_rule_direct", _async_none)
    monkeypatch.setattr(main, "get_fix_agent", lambda: _Agent())
    monkeypatch.setattr(main, "get_review_agent", lambda: _Review())
    monkeypatch.setattr(main, "_collect_direct_section_table_items", _async_empty)
    monkeypatch.setattr(main, "_format_inventory_table_answer_from_metadata", lambda *args: None)
    monkeypatch.setattr(
        main,
        "_format_manual_evidence_answer_from_metadata",
        lambda *args: "火花塞间隙标准为 1.2 mm。",
    )
    monkeypatch.setattr(main, "_collect_direct_section_images", _async_empty)
    monkeypatch.setattr(main, "_collect_direct_evidence_page_images", lambda *args: [])
    monkeypatch.setattr(main, "build_follow_up", lambda *args: None)

    async def _consume():
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

    assert "1.2" not in visible
    assert "0.7 到 0.9 mm" in visible
    assert set((
        "scope_decision",
        "coverage_status",
        "response_plan_id",
        "evidence_ledger_digest",
    )).issubset(done["data"]["metadata"])


def test_direct_lookup_records_are_registered_in_evidence_ledger() -> None:
    metadata = {"react_trace": _manual_trace()}
    records = [{
        "id": "table-extra-1",
        "content": "M6×30 螺栓，扭矩 12 N·m。",
        "metadata": {
            "document_id": "manual-1",
            "document_version": "v1",
            "page": 25,
            "chunk_type": "table",
        },
    }]

    main._register_direct_manual_evidence(metadata, records, "section_table_lookup")

    ledger = EvidenceLedger.from_react_trace(metadata)
    entry = next(item for item in ledger.entries if item["evidence_id"].endswith(":table-extra-1"))
    assert entry["qualification"] == "qualified"
    assert entry["source"]["document_id"] == "manual-1"
    assert entry["source"]["page"] == 25


def test_numeric_domain_rule_fact_is_bound_to_rule_text() -> None:
    trace = _rule_trace()
    payload = trace[0]["tool_calls"][0]["result_data"]
    payload["message"] = "将火花塞间隙调整为 0.8 mm。"
    payload["rule"]["conclusion"] = payload["message"]
    output = _output("domain_rule_direct", trace, tools=["domain_rule_engine"])

    finalized = main._finalize_knowledge_output(
        "火花塞间隙如何调整？",
        output,
        candidate_message=payload["message"],
    )

    assert finalized.message == payload["message"]
    assert finalized.metadata["response_audit"]["passed"] is True


def test_numeric_graph_fact_is_bound_to_structured_record() -> None:
    trace = [{
        "iteration": 1,
        "tool_calls": [{
            "name": "java_graph_diagnosis_path",
            "result_data": {
                "raw_records": [{
                    "pathIds": ["path-1"],
                    "nodeIds": ["node-1", "node-2"],
                    "relationshipTypes": ["HAS_SOLUTION"],
                    "deviceName": "发动机",
                    "componentName": "火花塞",
                    "faultName": "间隙异常",
                    "solutionTitle": "将间隙调整为 0.8 mm",
                }],
            },
        }],
    }]
    output = _output("react", trace, tools=["java_graph_diagnosis_path"])

    finalized = main._finalize_knowledge_output(
        "火花塞间隙异常怎么处理？",
        output,
        candidate_message="将火花塞间隙调整为 0.8 mm。",
    )

    assert finalized.message == "将火花塞间隙调整为 0.8 mm。"
    assert finalized.metadata["response_audit"]["passed"] is True


@pytest.mark.parametrize(
    ("trace", "tools", "expected_source"),
    [
        (_rule_trace(), ["domain_rule_engine"], "已审核规则"),
        ([{
            "tool_calls": [{
                "name": "java_graph_diagnosis_path",
                "result_data": {"raw_records": [{
                    "pathIds": ["path-source"],
                    "nodeIds": ["node-source"],
                    "summary": "检查点火回路。",
                }]},
            }],
        }], ["java_graph_diagnosis_path"], "知识图谱"),
    ],
)
def test_deterministic_fallback_uses_actual_source_type(trace, tools, expected_source) -> None:
    output = _output("react", trace, tools=tools)

    finalized = main._finalize_knowledge_output(
        "应该怎么检查？",
        output,
        candidate_message="使用 AB120 检测仪检查。",
    )

    assert expected_source in finalized.message
    assert "来源：手册" not in finalized.message
