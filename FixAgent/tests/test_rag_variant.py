import json
from types import SimpleNamespace

import pytest

from agents import base_agent as base_agent_module
from agents.base_agent import AgentInput, AgentOutput, AgentRunContext, BaseAgent
from agents.fix_agent import FixAgent
from api import main as api_main
from config import settings as settings_module
from guardrails.review_agent import _GraphCheck
from tools.base_tool import ToolResult
from tools.knowledge_retrieval_tool import (
    KnowledgeRetrievalTool,
    merge_additive_manual_results,
)
import asyncio


def _tool(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


def _agent_with_named_tools() -> FixAgent:
    agent = object.__new__(FixAgent)
    agent.llm_service = SimpleNamespace()
    agent._tools = [
        _tool("knowledge_retrieval"),
        _tool("knowledge_inventory"),
        _tool("java_graph_diagnosis_path"),
        _tool("java_graph_device_search"),
        _tool("component_reverse_device"),
        _tool("procedure_recommend"),
        _tool("recall_conversation_detail"),
        _tool("read_memory"),
        _tool("save_memory"),
        _tool("delete_memory"),
    ]
    return agent


def _manual_record(evidence_id: str, *, content: str = "", **metadata) -> dict:
    return {
        "id": evidence_id,
        "content": content or evidence_id,
        "metadata": {
            "evidence_id": evidence_id,
            "qualification": "qualified",
            "document_id": "manual-1",
            **metadata,
        },
    }


def test_additive_manual_merge_preserves_baseline_and_appends_unique_seed() -> None:
    baseline = [_manual_record("base-1"), _manual_record("shared")]
    seed = [_manual_record("shared"), _manual_record("seed-1")]

    merged = merge_additive_manual_results(baseline, seed)

    assert [item["id"] for item in merged] == ["base-1", "shared", "seed-1"]
    assert {item["id"] for item in baseline}.issubset({item["id"] for item in merged})


def test_additive_manual_merge_keeps_the_more_complete_duplicate_source() -> None:
    baseline = [_manual_record("shared", content="short")]
    seed = [_manual_record(
        "shared",
        content="complete source text",
        document_version="v3",
        chunk_uid="chunk-7",
        parent_section_id="section-2",
        page=12,
    )]

    merged = merge_additive_manual_results(baseline, seed)

    assert len(merged) == 1
    assert merged[0]["content"] == "complete source text"
    assert merged[0]["metadata"]["chunk_uid"] == "chunk-7"


def test_graph_seed_failure_returns_byte_equivalent_baseline_result() -> None:
    baseline = [_manual_record("base-1"), _manual_record("base-2")]

    class _Tool(KnowledgeRetrievalTool):
        async def _execute(self, **kwargs):
            if kwargs.get("allowed_source_chunk_uids"):
                raise RuntimeError("graph seed timeout")
            return baseline

    result = asyncio.run(_Tool().run(
        query="fault",
        document_id="manual-1",
        _graph_seed_scope={
            "server_authoritative": True,
            "document_id": "manual-1",
            "allowed_source_chunk_uids": ["chunk-graph"],
        },
    ))

    assert result.success is True
    assert result.data == baseline


def test_graph_seed_success_adds_evidence_without_removing_baseline() -> None:
    baseline = [_manual_record("base-1")]
    seed = [_manual_record("seed-1")]

    class _Tool(KnowledgeRetrievalTool):
        async def _execute(self, **kwargs):
            return seed if kwargs.get("allowed_source_chunk_uids") else baseline

    result = asyncio.run(_Tool().run(
        query="fault",
        document_id="manual-1",
        _graph_seed_scope={
            "server_authoritative": True,
            "document_id": "manual-1",
            "allowed_source_chunk_uids": ["chunk-graph"],
        },
    ))

    assert result.success is True
    assert [item["id"] for item in result.data] == ["base-1", "seed-1"]


def test_graph_seed_emits_one_merged_retrieval_stage_trace() -> None:
    events = []

    class _Tool(KnowledgeRetrievalTool):
        async def _execute(self, **kwargs):
            is_seed = bool(kwargs.get("allowed_source_chunk_uids"))
            prefix = "seed" if is_seed else "base"
            await kwargs["_event_sink"]({
                "event": "retrieval_stage",
                "data": {
                    "candidate_ids": [f"sec:{prefix}-candidate"],
                    "filtered_ids": [f"sec:{prefix}-filtered"],
                    "reranked_ids": [f"sec:{prefix}-reranked"],
                    "selected_ids": [f"sec:{prefix}-selected"],
                    "expanded_ids": [f"sec:{prefix}-expanded"],
                    "visible_ids": [f"sec:{prefix}-visible"],
                },
            })
            return [_manual_record(f"{prefix}-visible", parent_section_id=f"sec:{prefix}-visible")]

    async def sink(payload):
        events.append(payload)

    result = asyncio.run(_Tool().run(
        query="fault",
        document_id="manual-1",
        _event_sink=sink,
        _graph_seed_scope={
            "server_authoritative": True,
            "document_id": "manual-1",
            "allowed_source_chunk_uids": ["chunk-graph"],
        },
    ))

    stage_events = [event for event in events if event.get("event") == "retrieval_stage"]
    assert result.success is True
    assert len(stage_events) == 1
    assert stage_events[0]["data"]["visible_ids"] == [
        "sec:base-visible",
        "sec:seed-visible",
    ]


def test_normalize_rag_variant_defaults_to_production_and_accepts_experiment_modes() -> None:
    assert settings_module.normalize_rag_variant(None) == "production"
    assert settings_module.normalize_rag_variant(" no_graph ") == "no_graph"
    assert settings_module.normalize_rag_variant("GRAPH") == "graph_full"
    assert settings_module.normalize_rag_variant("graph_shadow") == "graph_shadow"


def test_normalize_rag_variant_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="RAG_VARIANT"):
        settings_module.normalize_rag_variant("disabled")


def test_effective_tool_arguments_expose_only_audit_allowlisted_fields() -> None:
    run_context = AgentRunContext(
        retrieval_scope={"scope_fingerprint": "manual-scope:test"},
    )

    manual = BaseAgent._effective_arguments_for_trace(
        "knowledge_retrieval",
        {
            "query": "fault cause",
            "top_k": 5,
            "document_id": "manual-1",
            "device_type": "pump",
            "allowed_section_ids": ["section-1"],
            "image_urls": ["https://private.example/image.png"],
            "user_id": "user-secret",
            "memory": {"private": "value"},
            "token": "token-secret",
        },
        run_context,
    )
    graph = BaseAgent._effective_arguments_for_trace(
        "java_graph_diagnosis_path",
        {
            "keyword": "pump",
            "fault_description": "overheating",
            "component_description": "bearing",
            "limit": 10,
            "allowed_path_ids": ["path-1"],
            "allowed_device_ids": ["device-1"],
            "allowed_component_ids": ["component-1"],
            "allowed_fault_ids": ["fault-1"],
            "image_urls": ["https://private.example/fault.png"],
            "user_id": "user-secret",
        },
        run_context,
    )
    memory = BaseAgent._effective_arguments_for_trace(
        "save_memory",
        {
            "name": "private preference",
            "content": "private value",
            "user_id": "user-secret",
            "turn_ts": 123,
        },
        run_context,
    )

    assert manual == {
        "query": "fault cause",
        "top_k": 5,
        "document_id": "manual-1",
        "device_type": "pump",
        "allowed_section_ids": ["section-1"],
        "scope_fingerprint": "manual-scope:test",
    }
    assert graph == {
        "keyword": "pump",
        "fault_description": "overheating",
        "component_description": "bearing",
        "limit": 10,
        "allowed_path_ids": ["path-1"],
        "allowed_device_ids": ["device-1"],
        "allowed_component_ids": ["component-1"],
        "allowed_fault_ids": ["fault-1"],
    }
    assert memory == {}


def test_no_graph_profile_exposes_only_manual_retrieval() -> None:
    agent = _agent_with_named_tools()
    context = AgentRunContext(experiment_tool_profile="rag_only")

    names = [tool.name for tool in agent.get_tools_for_run(context)]

    assert names == ["knowledge_retrieval"]


def test_graph_profile_exposes_manual_retrieval_and_complete_graph_suite() -> None:
    agent = _agent_with_named_tools()
    context = AgentRunContext(
        experiment_tool_profile="rag_kg",
        graph_scope={"allowed_device_ids": ["device-1"]},
    )

    names = [tool.name for tool in agent.get_tools_for_run(context)]

    assert names == [
        "knowledge_retrieval",
        "java_graph_diagnosis_path",
        "java_graph_device_search",
        "component_reverse_device",
    ]


def test_completed_graph_pre_retrieval_with_qualified_evidence_hides_graph_tools() -> None:
    agent = _agent_with_named_tools()
    context = AgentRunContext(
        experiment_tool_profile="rag_kg",
        graph_pre_retrieval={
            "status": "found",
            "reason": "controlled_server_result",
            "diagnostics": {"qualified_count": 1},
            "evidence": [{"qualification": "qualified"}],
        },
    )

    names = [tool.name for tool in agent.get_tools_for_run(context)]

    assert names == ["knowledge_retrieval"]


@pytest.mark.parametrize("status", ["not_applicable", "empty", "filtered_out", "unavailable"])
def test_completed_server_pre_retrieval_never_allows_a_second_graph_request(
    status: str,
) -> None:
    agent = _agent_with_named_tools()
    context = AgentRunContext(
        experiment_tool_profile="rag_kg",
        graph_pre_retrieval={
            "status": status,
            "reason": "fallback_allowed",
            "diagnostics": {"qualified_count": 0},
            "evidence": [],
        },
    )

    names = [tool.name for tool in agent.get_tools_for_run(context)]

    assert names == ["knowledge_retrieval"]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("not_applicable", 0),
        ("found", 1),
        ("degraded", 1),
        ("empty", 1),
        ("filtered_out", 1),
        ("unavailable", 1),
    ],
)
def test_graph_candidate_query_count_reflects_real_provider_attempt(
    status: str,
    expected: int,
) -> None:
    assert api_main._graph_candidate_query_count_for_status(status) == expected


def test_graph_tool_scope_overwrites_model_supplied_ids() -> None:
    agent = _agent_with_named_tools()
    context = AgentRunContext(
        experiment_tool_profile="rag_kg",
        graph_scope={
            "allowed_path_ids": ["kgpath:device-1:component-1:fault-1"],
            "allowed_device_ids": ["device-1"],
            "allowed_component_ids": ["component-1"],
            "allowed_fault_ids": ["fault-1"],
        },
    )

    result = agent._customize_tool_kwargs_for_run(
        "java_graph_diagnosis_path",
        {
            "allowed_path_ids": ["foreign-path"],
            "allowed_device_ids": ["foreign-device"],
            "allowed_component_ids": ["foreign-component"],
            "allowed_fault_ids": ["foreign-fault"],
        },
        context,
    )

    assert result["allowed_path_ids"] == ["kgpath:device-1:component-1:fault-1"]
    assert result["allowed_device_ids"] == ["device-1"]
    assert result["allowed_component_ids"] == ["component-1"]
    assert result["allowed_fault_ids"] == ["fault-1"]


def test_empty_graph_scope_blocks_graph_tool_call_fail_closed() -> None:
    agent = _agent_with_named_tools()
    context = AgentRunContext(experiment_tool_profile="rag_kg", graph_scope={})

    names = [tool.name for tool in agent.get_tools_for_run(context)]

    assert names == ["knowledge_retrieval"]


def _pre_retrieved_graph() -> dict:
    return {
        "status": "found",
        "reason": "",
        "diagnostics": {"qualified_count": 1},
        "evidence": [{
            "evidence_id": "graph:kgpath:device-1:component-1:fault-1:none",
            "source_type": "graph",
            "qualification": "qualified",
            "path_id": "kgpath:device-1:component-1:fault-1",
            "node_ids": ["device-1", "component-1", "fault-1"],
            "relationship_types": ["OWNS", "CAUSES"],
            "device": {"id": "device-1", "name": "一号发动机"},
            "component": {"id": "component-1", "name": "张紧轮"},
            "fault": {"id": "fault-1", "name": "轴承磨损"},
            "solution": {},
            "text": "一号发动机 -> OWNS -> 张紧轮 -> CAUSES -> 轴承磨损",
            "source": {
                "document_id": "manual-1",
                "section_id": "sec-bearing",
                "source_chunk_uids": ["chunk-1"],
                "pages": [12],
            },
        }],
    }


def test_build_run_context_preserves_server_graph_pre_retrieval() -> None:
    agent = _agent_with_named_tools()
    input_data = AgentInput(
        user_message="张紧轮异响是什么原因",
        session_id="session-1",
        context={"graph_pre_retrieval": _pre_retrieved_graph()},
    )

    context = agent.build_run_context(input_data)

    assert context.graph_pre_retrieval["status"] == "found"
    assert context.graph_pre_retrieval["evidence"][0]["path_id"].startswith("kgpath:")


def test_build_run_context_preserves_query_contract_for_internal_retrieval() -> None:
    agent = _agent_with_named_tools()
    input_data = AgentInput(
        user_message="火花塞损坏如何处理",
        session_id="query-contract-test",
        context={"query_contract": {"component": "火花塞", "fault": "火花塞损坏"}},
    )

    context = agent.build_run_context(input_data)
    kwargs = agent._customize_tool_kwargs_for_run("knowledge_retrieval", {"query": "处理"}, context)

    assert context.query_contract["component"] == "火花塞"
    assert kwargs["_query_contract"] == context.query_contract


def test_pre_retrieved_graph_is_reserved_for_additive_assembly_and_path_tool_is_hidden() -> None:
    agent = _agent_with_named_tools()
    context = AgentRunContext(
        experiment_tool_profile="rag_kg",
        graph_pre_retrieval=_pre_retrieved_graph(),
    )

    prompt = agent.get_system_prompt_for_run(context)
    tool_names = [tool.name for tool in agent.get_tools_for_run(context)]

    assert "图谱增量装配" in prompt
    assert "只生成普通 RAG 手册基础答案" in prompt
    assert "kgpath:device-1:component-1:fault-1" not in prompt
    assert tool_names == ["knowledge_retrieval"]
    assert "不要再次调用图谱工具" in prompt
    assert "java_graph_diagnosis_path" not in prompt
    assert "java_graph_device_search" not in prompt
    assert "component_reverse_device" not in prompt


def test_pre_retrieved_graph_is_seeded_into_react_trace_once() -> None:
    output = AgentOutput(agent_name="fix_agent", message="结果", metadata={"react_trace": []})
    context = AgentRunContext(graph_pre_retrieval=_pre_retrieved_graph())

    FixAgent._attach_pre_retrieved_graph(output, context)
    FixAgent._attach_pre_retrieved_graph(output, context)

    calls = output.metadata["react_trace"][0]["tool_calls"]
    assert len(calls) == 1
    assert calls[0]["name"] == "java_graph_diagnosis_path"
    assert calls[0]["result_data"]["evidence"][0]["evidence_id"].startswith("graph:")


def test_no_graph_policy_requires_manual_retrieval_but_not_graph_or_procedure() -> None:
    context = AgentRunContext(
        experiment_tool_profile="rag_only",
        intent_decision={"intent": "maintenance_guidance"},
    )

    assert FixAgent._required_tools_for_policy(context) == ["knowledge_retrieval"]


def test_graph_policy_requires_manual_retrieval_and_graph_for_diagnosis() -> None:
    context = AgentRunContext(
        experiment_tool_profile="rag_kg",
        intent_decision={"intent": "fault_diagnosis", "task_action": "find_cause"},
    )

    assert FixAgent._required_tools_for_policy(context) == [
        "knowledge_retrieval",
        "java_graph_diagnosis_path",
    ]


@pytest.mark.parametrize(
    ("intent", "task_action"),
    [
        ("maintenance_guidance", "repair_guidance"),
        ("procedure_planning", "formal_procedure"),
    ],
)
def test_graph_profile_manual_only_request_does_not_require_hidden_graph_tool(
    intent: str,
    task_action: str,
) -> None:
    context = AgentRunContext(
        experiment_tool_profile="rag_kg",
        intent_decision={"intent": intent, "task_action": task_action},
    )

    assert FixAgent._required_tools_for_policy(context) == ["knowledge_retrieval"]


@pytest.mark.parametrize("status", ["empty", "filtered_out", "unavailable"])
def test_unsuccessful_graph_pre_retrieval_is_not_recorded_as_executed_tool(
    status: str,
) -> None:
    output = AgentOutput(agent_name="fix_agent", message="结果", metadata={"react_trace": []})
    context = AgentRunContext(
        graph_pre_retrieval={
            "status": status,
            "reason": "test",
            "diagnostics": {"qualified_count": 0},
            "evidence": [],
        }
    )

    FixAgent._attach_pre_retrieved_graph(output, context)

    assert output.tools_used == []
    assert output.metadata["react_trace"] == []


def test_completed_empty_graph_pre_retrieval_does_not_remain_a_required_tool() -> None:
    context = AgentRunContext(
        experiment_tool_profile="rag_kg",
        intent_decision={"intent": "fault_diagnosis", "task_action": "find_cause"},
        graph_pre_retrieval={
            "status": "empty",
            "reason": "no_path",
            "diagnostics": {"qualified_count": 0},
            "evidence": [],
        },
    )

    assert FixAgent._required_tools_for_policy(context) == ["knowledge_retrieval"]


def test_completed_empty_graph_pre_retrieval_uses_manual_only_prompt() -> None:
    agent = _agent_with_named_tools()
    context = AgentRunContext(
        experiment_tool_profile="rag_kg",
        graph_pre_retrieval={
            "status": "empty",
            "reason": "no_path",
            "diagnostics": {"qualified_count": 0},
            "evidence": [],
        },
    )

    prompt = agent.get_system_prompt_for_run(context)

    assert "无图谱消融模式" in prompt
    assert "图谱增强消融模式" not in prompt


def test_production_required_tools_use_the_same_graph_applicability_policy() -> None:
    maintenance = AgentRunContext(
        intent_decision={
            "intent": "maintenance_guidance",
            "task_action": "repair_guidance",
            "requires_graph_search": True,
        }
    )
    diagnostic = AgentRunContext(
        intent_decision={
            "intent": "fault_diagnosis",
            "task_action": "parameter_lookup",
            "requested_fields": ["故障原因"],
        }
    )

    assert FixAgent._required_tools_for_policy(maintenance) == [
        "knowledge_retrieval",
        "procedure_recommend",
    ]
    assert FixAgent._required_tools_for_policy(diagnostic) == [
        "knowledge_retrieval",
        "java_graph_diagnosis_path",
    ]


def test_authoritative_manual_scope_clears_model_filters_and_injects_all_fields() -> None:
    agent = _agent_with_named_tools()
    context = AgentRunContext(
        retrieval_scope={
            "document_id": "manual-1",
            "document_version": "v1",
            "device_type": "",
            "parent_section_id": "section-1",
            "allowed_section_ids": ["section-1"],
            "allowed_evidence_refs": ["row-1"],
            "allowed_source_chunk_uids": ["chunk-uid-1"],
            "pages": [12, 13],
            "server_authoritative": True,
        }
    )

    result = agent._customize_tool_kwargs_for_run(
        "knowledge_retrieval",
        {
            "query": "故障原因",
            "document_id": "foreign-manual",
            "document_version": "foreign-version",
            "device_type": "纯电动客车",
            "parent_section_id": "foreign-section",
            "allowed_section_ids": ["foreign-section"],
            "allowed_evidence_refs": ["foreign-row"],
            "allowed_source_chunk_uids": ["foreign-chunk"],
            "pages": [99],
        },
        context,
    )

    assert result == {
        "query": "故障原因",
        "document_id": "manual-1",
        "document_version": "v1",
        "parent_section_id": "section-1",
        "allowed_section_ids": ["section-1"],
        "allowed_evidence_refs": ["row-1"],
        "allowed_source_chunk_uids": ["chunk-uid-1"],
        "pages": [12, 13],
    }


def test_react_trace_records_effective_server_scope_after_model_arguments_are_overridden(
    monkeypatch,
) -> None:
    class FakeLlm:
        def __init__(self) -> None:
            self.calls = 0

        async def complete_with_tools(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return {
                    "content": None,
                    "tool_calls": [{
                        "id": "call-1",
                        "function": {
                            "name": "knowledge_retrieval",
                            "arguments": json.dumps({
                                "query": "fault cause",
                                "document_id": "foreign-manual",
                                "device_type": "foreign-device",
                            }),
                        },
                    }],
                }
            return {"content": "done", "tool_calls": []}

    class RecordingTool:
        name = "knowledge_retrieval"

        def __init__(self) -> None:
            self.calls = []

        def to_openai_schema(self) -> dict:
            return {
                "type": "function",
                "function": {
                    "name": self.name,
                    "description": "test",
                    "parameters": {"type": "object", "properties": {}},
                },
            }

        async def run(self, **kwargs) -> ToolResult:
            self.calls.append(dict(kwargs))
            return ToolResult(
                success=True,
                data={"qualified_evidence": [], "aspect_support": []},
                tool_name=self.name,
            )

    async def keep_style(_service, content, **_kwargs):
        return content, False

    monkeypatch.setattr(base_agent_module, "regenerate_user_visible_text", keep_style)
    tool = RecordingTool()
    agent = FixAgent(FakeLlm())
    agent._tools = [tool]
    input_data = AgentInput(
        user_message="fault cause",
        session_id="session-1",
        context={
            "retrieval_scope": {
                "server_authoritative": True,
                "scope_fingerprint": "manual-scope:test",
                "document_id": "manual-1",
                "document_version": "v1",
                "device_type": "",
                "parent_section_id": "section-1",
                "allowed_section_ids": ["section-1"],
                "allowed_source_chunk_uids": ["chunk-1"],
                "pages": [12],
            }
        },
    )

    output = asyncio.run(BaseAgent.run_with_react(agent, input_data))

    expected_effective = {
        "query": "fault cause",
        "document_id": "manual-1",
        "document_version": "v1",
        "parent_section_id": "section-1",
        "allowed_section_ids": ["section-1"],
        "allowed_source_chunk_uids": ["chunk-1"],
        "pages": [12],
        "scope_fingerprint": "manual-scope:test",
    }
    trace_call = output.metadata["react_trace"][0]["tool_calls"][0]
    assert trace_call["arguments"]["document_id"] == "foreign-manual"
    assert trace_call["effective_arguments"] == expected_effective
    assert output.metadata["effective_tool_calls"] == [{
        "name": "knowledge_retrieval",
        "effective_arguments": expected_effective,
    }]
    assert tool.calls == [{
        key: value
        for key, value in expected_effective.items()
        if key != "scope_fingerprint"
    }]


def test_knowledge_bundle_merge_preserves_support_from_all_retrieval_calls() -> None:
    trace = [
        {
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "effective_arguments": {"scope_fingerprint": "manual-scope:one"},
                "result_data": {
                    "coverage_status": "partial",
                    "qualified_evidence": [{"doc_id": "cause-row"}],
                    "aspect_support": [{
                        "aspect_id": "fault-cause",
                        "aspect_text": "故障原因",
                        "supported": True,
                        "evidence_ids": ["cause-row"],
                    }],
                    "missing_aspect_ids": ["treatment"],
                    "capabilities": {"may_cite_manual": True},
                },
            }],
        },
        {
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "effective_arguments": {"scope_fingerprint": "manual-scope:one"},
                "result_data": {
                    "coverage_status": "partial",
                    "qualified_evidence": [{"doc_id": "treatment-row"}],
                    "aspect_support": [{
                        "aspect_id": "treatment",
                        "aspect_text": "处理建议",
                        "supported": True,
                        "evidence_ids": ["treatment-row"],
                    }],
                    "missing_aspect_ids": ["fault-cause"],
                    "capabilities": {"may_cite_manual": True},
                },
            }],
        },
    ]

    bundle = FixAgent._merged_knowledge_bundle(trace)

    assert bundle["coverage_status"] == "complete"
    assert bundle["missing_aspect_ids"] == []
    assert {row["aspect_id"] for row in bundle["aspect_support"]} == {
        "fault-cause",
        "treatment",
    }
    assert {row["doc_id"] for row in bundle["qualified_evidence"]} == {
        "cause-row",
        "treatment-row",
    }


def test_knowledge_bundle_merge_never_crosses_scope_fingerprints() -> None:
    trace = [{
        "tool_calls": [
            {
                "name": "knowledge_retrieval",
                "effective_arguments": {"scope_fingerprint": "manual-scope:old"},
                "result_data": {
                    "qualified_evidence": [{"doc_id": "old-row"}],
                    "aspect_support": [{
                        "aspect_id": "old",
                        "supported": True,
                        "evidence_ids": ["old-row"],
                    }],
                },
            },
            {
                "name": "knowledge_retrieval",
                "effective_arguments": {"scope_fingerprint": "manual-scope:new"},
                "result_data": {
                    "qualified_evidence": [{"doc_id": "new-row"}],
                    "aspect_support": [{
                        "aspect_id": "new",
                        "supported": True,
                        "evidence_ids": ["new-row"],
                    }],
                },
            },
        ],
    }]

    bundle = FixAgent._merged_knowledge_bundle(trace)

    assert [row["aspect_id"] for row in bundle["aspect_support"]] == ["new"]
    assert [row["doc_id"] for row in bundle["qualified_evidence"]] == ["new-row"]


def test_api_manual_bundle_merges_all_calls_from_latest_authoritative_scope() -> None:
    metadata = {
        "react_trace": [
            {
                "tool_calls": [{
                    "name": "knowledge_retrieval",
                    "effective_arguments": {"scope_fingerprint": "manual-scope:old"},
                    "result_data": {
                        "qualified_evidence": [{"doc_id": "old-row"}],
                        "aspect_support": [{
                            "aspect_id": "old",
                            "supported": True,
                            "evidence_ids": ["old-row"],
                        }],
                    },
                }],
            },
            {
                "tool_calls": [{
                    "name": "knowledge_retrieval",
                    "effective_arguments": {"scope_fingerprint": "manual-scope:new"},
                    "result_data": {
                        "coverage_status": "partial",
                        "qualified_evidence": [{"doc_id": "cause-row"}],
                        "aspect_support": [{
                            "aspect_id": "fault-cause",
                            "supported": True,
                            "evidence_ids": ["cause-row"],
                        }],
                        "missing_aspect_ids": ["treatment"],
                    },
                }],
            },
            {
                "tool_calls": [{
                    "name": "knowledge_retrieval",
                    "effective_arguments": {"scope_fingerprint": "manual-scope:new"},
                    "result_data": {
                        "coverage_status": "partial",
                        "qualified_evidence": [{"doc_id": "treatment-row"}],
                        "aspect_support": [{
                            "aspect_id": "treatment",
                            "supported": True,
                            "evidence_ids": ["treatment-row"],
                        }],
                        "missing_aspect_ids": ["fault-cause"],
                    },
                }],
            },
        ],
    }

    bundle = api_main._manual_bundle_from_trace(metadata)

    assert bundle["scope_fingerprint"] == "manual-scope:new"
    assert bundle["coverage_status"] == "complete"
    assert {row["doc_id"] for row in bundle["qualified_evidence"]} == {
        "cause-row",
        "treatment-row",
    }
    assert {row["aspect_id"] for row in bundle["aspect_support"]} == {
        "fault-cause",
        "treatment",
    }


def test_no_graph_prompt_has_explicit_experiment_constraint() -> None:
    agent = _agent_with_named_tools()
    context = AgentRunContext(experiment_tool_profile="rag_only")

    prompt = agent.get_system_prompt_for_run(context)

    assert "无图谱消融模式" in prompt
    assert "不得调用或声称使用知识图谱" in prompt


def test_client_context_string_cannot_enable_experiment_profile() -> None:
    agent = _agent_with_named_tools()
    input_data = AgentInput(
        user_message="测试",
        session_id="session-1",
        context={"_experiment_tool_profile": "rag_only"},
    )

    context = agent.build_run_context(input_data)

    assert context.experiment_tool_profile is None


@pytest.mark.parametrize(
    ("variant", "expected"),
    [
        ("production", True),
        ("graph_full", True),
        ("graph_shadow", True),
        ("no_graph", False),
    ],
)
def test_graph_candidate_gate_is_variant_aware(variant: str, expected: bool) -> None:
    assert api_main._graph_candidates_enabled(variant) is expected


@pytest.mark.parametrize(
    ("variant", "requested", "context", "expected"),
    [
        ("production", "full", {}, "standard"),
        (
            "graph_full",
            "full",
            {
                "graph_policy": {"graph_review_enabled": True},
                "graph_pre_retrieval": {"diagnostics": {"qualified_count": 1}},
            },
            "full",
        ),
        ("graph_full", "full", {}, "standard"),
        ("graph_shadow", "full", {}, "standard"),
        ("no_graph", "full", {}, "standard"),
        ("no_graph", "light", {}, "light"),
    ],
)
def test_review_level_runs_graph_check_only_for_qualified_graph_evidence(
    variant: str,
    requested: str,
    context: dict,
    expected: str,
) -> None:
    assert api_main._review_level_for_rag_variant(variant, requested, context) == expected


def test_graph_shadow_audit_never_reports_graph_review_enabled() -> None:
    audit = api_main._rag_variant_audit_metadata(
        context={
            "rag_variant": "graph_shadow",
            "graph_policy": {"graph_review_enabled": False},
            "graph_pre_retrieval": _pre_retrieved_graph(),
        },
        metadata={"react_trace": []},
        review_level="full",
    )

    assert audit["graph_review_enabled"] is False


def test_rag_audit_counts_graph_candidates_and_trace_tool_calls() -> None:
    metadata = {
        "claim_evidence_bindings": [{
            "claim_id": "fault-cause",
            "claim_text": "fault cause",
            "evidence_ids": ["graph:kgpath:device-1:component-1:fault-1:none"],
        }],
        "graph_evidence_used_ids": [
            "graph:kgpath:device-1:component-1:fault-1:none",
            "graph:foreign-scope",
        ],
        "react_trace": [
            {
                "tool_calls": [
                    {"name": "knowledge_retrieval"},
                    {"name": "java_graph_diagnosis_path"},
                    {
                        "name": "component_reverse_device",
                        "executed": False,
                        "execution_status": "not_registered",
                        "result_summary": "tool not found: component_reverse_device",
                    },
                ]
            },
            {"tool_calls": [{"name": "component_reverse_device"}]},
        ]
    }
    context = {
        "rag_variant": "graph_full",
        "graph_candidate_query_count": 1,
        "graph_candidate_count": 3,
        "graph_candidate_retrieval": {
            "status": "found",
            "reason": "",
            "diagnostics": {"candidate_count": 3},
        },
        "graph_scope": {"allowed_device_ids": ["device-1"]},
        "graph_policy": {"graph_review_enabled": True},
        "graph_pre_retrieval": _pre_retrieved_graph() | {
            "diagnostics": {
                "qualified_count": 1,
                "routing_only_count": 0,
                "rejected_count": 0,
                "latency_ms": 27,
            }
        },
    }

    audit = api_main._rag_variant_audit_metadata(
        context=context,
        metadata=metadata,
        review_level="full",
    )

    assert audit == {
        "rag_variant": "graph_full",
        "graph_candidate_query_count": 1,
        "graph_candidate_count": 3,
        "graph_candidate_status": "found",
        "graph_candidate_reason": "",
        "graph_retrieval_status": "found",
        "graph_retrieval_reason": "",
        "graph_scope": {"allowed_device_ids": ["device-1"]},
        "graph_qualified_count": 1,
        "graph_routing_only_count": 0,
        "graph_rejected_count": 0,
        "graph_evidence_ids": ["graph:kgpath:device-1:component-1:fault-1:none"],
        "claim_evidence_bindings": [{
            "claim_id": "fault-cause",
            "claim_text": "fault cause",
            "evidence_ids": ["graph:kgpath:device-1:component-1:fault-1:none"],
        }],
        "graph_evidence_used_ids": ["graph:kgpath:device-1:component-1:fault-1:none"],
        "graph_relationship_types": ["CAUSES", "OWNS"],
        "graph_provenance_statuses": [],
        "graph_retrieval_latency_ms": 27,
        "graph_tool_call_count": 2,
        "graph_tools_used": [
            "component_reverse_device",
            "java_graph_diagnosis_path",
        ],
        "graph_review_enabled": True,
        "intent_decision": {},
        "query_contract": {},
        "evaluation_route_contract_applied": False,
        "route_contract_signature": "0a214b008adc3599f5fb7e784b3006eb7250327d2edc08b10809f960148ced13",
    }


def test_rag_audit_preserves_explicit_empty_graph_usage() -> None:
    graph_id = "graph:kgpath:device-1:component-1:fault-1:none"
    audit = api_main._rag_variant_audit_metadata(
        context={
            "rag_variant": "graph_full",
            "graph_policy": {"graph_review_enabled": True},
            "graph_pre_retrieval": _pre_retrieved_graph() | {
                "diagnostics": {"qualified_count": 1}
            },
        },
        metadata={
            "claim_evidence_bindings": [{
                "claim_id": "fault-cause",
                "evidence_ids": [graph_id],
            }],
            "graph_evidence_used_ids": [],
            "react_trace": [],
        },
        review_level="full",
    )

    assert audit["claim_evidence_bindings"][0]["evidence_ids"] == [graph_id]
    assert audit["graph_evidence_used_ids"] == []


def test_rag_audit_signs_the_frozen_input_contract_before_graph_routing() -> None:
    frozen = {
        "intent_decision": {
            "intent": "fault_diagnosis",
            "task_action": "find_cause",
        },
        "query_contract": {
            "raw_query": "compressor fault",
            "intent": "fault_diagnosis",
            "task_action": "find_cause",
        },
    }
    baseline = api_main._rag_variant_audit_metadata(
        context={
            "rag_variant": "no_graph",
            **frozen,
        },
        metadata={},
        review_level="full",
    )
    graph_full = api_main._rag_variant_audit_metadata(
        context={
            "rag_variant": "graph_full",
            "intent_decision": frozen["intent_decision"],
            "query_contract": {
                **frozen["query_contract"],
                "component": "graph-selected-component",
            },
            "_evaluation_route_contract": frozen,
            "evaluation_route_contract_applied": True,
        },
        metadata={},
        review_level="full",
    )

    assert graph_full["route_contract_signature"] == baseline["route_contract_signature"]


def test_graph_review_uses_full_structured_path_without_node_existence_queries(monkeypatch) -> None:
    evidence = _pre_retrieved_graph()["evidence"][0] | {
        "evidence_id": "graph:kgpath:device-1:component-1:fault-1:solution-1",
        "relationship_types": ["OWNS", "CAUSES", "HAS_SOLUTION"],
        "fault": {"id": "fault-1", "name": "张紧轮轴承磨损"},
        "solution": {
            "id": "solution-1",
            "title": "建议先确认后更换",
            "status": "active",
            "verified": True,
        },
        "provenance_status": "complete",
    }
    trace = [{
        "iteration": 0,
        "action": "server_pre_retrieval",
        "tool_calls": [{
            "name": "java_graph_diagnosis_path",
            "result_data": {"status": "found", "evidence": [evidence]},
        }],
    }]

    class _NoNetwork:
        async def __aenter__(self):
            raise AssertionError("structured graph review must not query isolated nodes")

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr("guardrails.review_agent.httpx.AsyncClient", lambda *args, **kwargs: _NoNetwork())

    result = asyncio.run(_GraphCheck.run("- 张紧轮轴承磨损：建议先确认后更换", trace))

    assert result["verified_count"] == 1
    assert result["unverified_count"] == 0
    assert result["verified_paths"][0]["verified_by"] == "structured_graph_evidence"
    assert result["verified_paths"][0]["evidence_id"].startswith("graph:")
