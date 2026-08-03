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


def _shuffled_procedure_trace(*, partial: bool = False) -> list[dict]:
    records = []
    for step in (4, 2, 1, 3):
        records.append({
            "id": f"derived-step-{step}",
            "content": f"{step}. 执行第{step}步。",
            "metadata": {
                "qualification": "qualified",
                "document_id": "manual-1",
                "document_version": "v1",
                "chunk_id": f"derived-step-{step}",
                "source_chunk_id": f"source-step-{step}",
                "chunk_type": "step_raw",
                "section_title": "4.4 涨紧器",
                "parent_section_id": "sec-tensioner",
                "section_match_ids": ["sec-tensioner"],
                "section_index": 4,
                "page": 13,
                "source_index": step,
            },
        })
    evidence_ids = [f"source-step-{step}" for step in (1, 2, 3, 4)]
    bundle = {
        "coverage_status": "partial" if partial else "complete",
        "aspect_support": [
            {
                "aspect_id": "procedure",
                "aspect_text": "安装步骤",
                "supported": True,
                "evidence_ids": evidence_ids,
            },
            {
                "aspect_id": "inspection",
                "aspect_text": "安装后的复检要求",
                "supported": not partial,
                "evidence_ids": evidence_ids if not partial else [],
            },
        ],
        "missing_aspect_ids": ["inspection"] if partial else [],
        "conflict_eligible": [],
        "capabilities": {"may_cite_manual": True},
    }
    for record in records:
        record["metadata"]["evidence_bundle"] = bundle
    return [{
        "iteration": 1,
        "action": "tool_call",
        "tool_calls": [{
            "name": "knowledge_retrieval",
            "result_data": records,
        }],
    }]


def _isolate_manual_formatter_from_vector_store(monkeypatch) -> None:
    class _VectorService:
        def get_section_records(self, *args, **kwargs):
            return []

        def get_page_records(self, *args, **kwargs):
            return []

    class _SectionIndex:
        def build(self, vector_service):
            return None

        def find(self, query):
            return []

    from services.knowledge import vector_service as vector_service_module
    from services.retrieval.section_index import SectionTitleIndex

    monkeypatch.setattr(vector_service_module, "get_vector_service", lambda: _VectorService())
    monkeypatch.setattr(SectionTitleIndex, "get_instance", classmethod(lambda cls: _SectionIndex()))


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


def test_real_manual_formatter_survives_final_audit_in_source_order(monkeypatch) -> None:
    _isolate_manual_formatter_from_vector_store(monkeypatch)
    trace = _shuffled_procedure_trace()
    output = _output("manual_section_direct", trace)

    formatted = main._format_manual_evidence_answer_from_metadata(
        "如何安装涨紧器？",
        output.metadata,
    )
    finalized = main._finalize_knowledge_output(
        "如何安装涨紧器？",
        output,
        candidate_message=formatted,
    )

    assert formatted is not None
    assert not finalized.message.startswith("根据手册")
    assert finalized.message.index("1. 执行第1步") < finalized.message.index("2. 执行第2步")
    assert finalized.message.index("2. 执行第2步") < finalized.message.index("3. 执行第3步")
    assert finalized.message.index("3. 执行第3步") < finalized.message.index("4. 执行第4步")
    assert finalized.message.count("执行第1步") == 1
    assert finalized.message.count("执行第2步") == 1
    assert finalized.message.count("执行第3步") == 1
    assert finalized.message.count("执行第4步") == 1
    assert finalized.metadata["response_audit"]["used_fallback"] is False


def test_partial_direct_manual_answer_appends_disclosure_without_replacing_steps(monkeypatch) -> None:
    _isolate_manual_formatter_from_vector_store(monkeypatch)
    trace = _shuffled_procedure_trace(partial=True)
    output = _output("manual_section_direct", trace)

    formatted = main._format_manual_evidence_answer_from_metadata(
        "如何安装涨紧器并在安装后复检？",
        output.metadata,
    )
    finalized = main._finalize_knowledge_output(
        "如何安装涨紧器并在安装后复检？",
        output,
        candidate_message=formatted,
    )

    assert formatted is not None
    assert all(f"{step}. 执行第{step}步" in finalized.message for step in range(1, 5))
    assert finalized.message.index("4. 执行第4步") < finalized.message.index("安装后的复检要求")
    assert "当前资料没有明确说明" in finalized.message
    assert finalized.metadata["response_audit"]["used_fallback"] is False


def test_direct_manual_answer_requalifies_stale_unsupported_bundle(monkeypatch) -> None:
    _isolate_manual_formatter_from_vector_store(monkeypatch)
    trace = _shuffled_procedure_trace()
    stale_bundle = {
        "coverage_status": "unsupported",
        "aspect_support": [],
        "missing_aspect_ids": [],
        "conflict_eligible": [],
        "capabilities": {"may_cite_manual": True},
    }
    for record in trace[0]["tool_calls"][0]["result_data"]:
        record["metadata"]["evidence_bundle"] = stale_bundle
    output = _output("manual_section_direct", trace)

    formatted = main._format_manual_evidence_answer_from_metadata(
        "如何安装涨紧器？",
        output.metadata,
    )
    finalized = main._finalize_knowledge_output(
        "如何安装涨紧器？",
        output,
        candidate_message=formatted,
    )

    assert formatted is not None
    assert finalized.metadata["coverage_status"] == "complete"
    assert finalized.metadata["response_audit"]["used_fallback"] is False
    assert all(f"{step}. 执行第{step}步" in finalized.message for step in range(1, 5))


def test_direct_manual_answer_discards_conflicts_outside_selected_source(monkeypatch) -> None:
    _isolate_manual_formatter_from_vector_store(monkeypatch)
    trace = _shuffled_procedure_trace()
    stale_bundle = {
        "coverage_status": "conflict",
        "aspect_support": [{
            "aspect_id": "procedure",
            "aspect_text": "安装步骤",
            "supported": True,
            "evidence_ids": ["source-step-1"],
        }],
        "missing_aspect_ids": [],
        "conflict_eligible": [{
            "field": "相邻章节参数",
            "values": ["11", "12"],
            "candidate_ids": ["irrelevant-table-row-1", "irrelevant-table-row-2"],
        }],
        "capabilities": {"may_cite_manual": True},
    }
    for record in trace[0]["tool_calls"][0]["result_data"]:
        record["metadata"]["evidence_bundle"] = stale_bundle
    output = _output("manual_section_direct", trace)

    formatted = main._format_manual_evidence_answer_from_metadata("如何安装涨紧器？", output.metadata)
    finalized = main._finalize_knowledge_output(
        "如何安装涨紧器？",
        output,
        candidate_message=formatted,
    )

    assert finalized.metadata["coverage_status"] == "complete"
    assert finalized.metadata["response_audit"]["used_fallback"] is False


def test_direct_manual_answer_keeps_conflict_bound_to_selected_source(monkeypatch) -> None:
    _isolate_manual_formatter_from_vector_store(monkeypatch)
    trace = _shuffled_procedure_trace()
    conflict_bundle = {
        "coverage_status": "conflict",
        "aspect_support": [{
            "aspect_id": "procedure",
            "aspect_text": "安装步骤",
            "supported": True,
            "evidence_ids": ["source-step-1"],
        }],
        "missing_aspect_ids": [],
        "conflict_eligible": [{
            "field": "第一步参数",
            "values": ["11", "12"],
            "candidate_ids": ["source-step-1"],
        }],
        "capabilities": {"may_cite_manual": True},
    }
    for record in trace[0]["tool_calls"][0]["result_data"]:
        record["metadata"]["evidence_bundle"] = conflict_bundle
    output = _output("manual_section_direct", trace)

    formatted = main._format_manual_evidence_answer_from_metadata("如何安装涨紧器？", output.metadata)
    finalized = main._finalize_knowledge_output(
        "如何安装涨紧器？",
        output,
        candidate_message=formatted,
    )

    assert finalized.metadata["coverage_status"] == "conflict"
    assert finalized.metadata["response_audit"]["used_fallback"] is True
    assert "存在冲突" in finalized.message


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
    output.metadata.update({
        "_deterministic_answer_evidence_pages": [3],
        "_deterministic_answer_document_ids": ["manual-1"],
        "_deterministic_answer_section_title": "2.1 火花塞",
        "_deterministic_answer_section_ids": ["sec-spark-plug"],
        "_deterministic_answer_table_complete": True,
    })

    main._attach_stream_done_metadata(event, output.metadata)

    metadata = event["data"]["metadata"]
    assert set((
        "scope_decision",
        "coverage_status",
        "response_plan_id",
        "evidence_ledger_digest",
        "_deterministic_answer_evidence_pages",
        "_deterministic_answer_document_ids",
        "_deterministic_answer_section_title",
        "_deterministic_answer_section_ids",
        "_deterministic_answer_table_complete",
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


def test_direct_exact_section_bundle_preserves_partial_coverage() -> None:
    metadata = {
        "scope_decision": {"status": "in_scope"},
        "original_user_message": "水泵装配里有水泵密封圈吗？叶轮轴向间隙是多少？",
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "arguments": {"source": "section_text_lookup"},
                "result_data": [{
                    "id": "water-pump-seal",
                    "content": "水泵密封圈 1 个。",
                    "metadata": {
                        "chunk_id": "water-pump-seal",
                        "original_title_match": True,
                    },
                }],
            }],
        }],
    }
    original = {
        "coverage_status": "partial",
        "aspect_support": [{
            "aspect_id": "seal",
            "aspect_text": "是否有水泵密封圈",
            "supported": True,
            "evidence_ids": ["water-pump-seal"],
        }],
        "missing_aspect_ids": ["impeller-clearance"],
        "capabilities": {"may_cite_manual": True},
    }

    bundle = main._direct_answer_evidence_bundle(metadata, original)

    assert bundle is not None
    assert bundle["coverage_status"] == "partial"
    assert bundle["missing_aspect_ids"] == ["impeller-clearance"]


def test_direct_exact_section_bundle_upgrades_stale_partial_for_complete_procedure() -> None:
    metadata = {
        "scope_decision": {"status": "in_scope"},
        "original_user_message": "如何安装右曲轴箱盖",
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "arguments": {"source": "section_text_lookup"},
                "result_data": [{
                    "id": "right-cover-install",
                    "content": "装上定位销和全新的右曲轴箱盖垫片，再盖上右曲轴箱盖。",
                    "metadata": {
                        "chunk_id": "right-cover-install",
                        "original_title_match": True,
                    },
                }],
            }],
        }],
    }
    original = {
        "coverage_status": "partial",
        "aspect_support": [{
            "aspect_id": "stale-retrieval-aspect",
            "aspect_text": "安装右曲轴箱盖",
            "supported": False,
            "evidence_ids": [],
        }],
        "missing_aspect_ids": ["stale-retrieval-aspect"],
        "capabilities": {"may_cite_manual": True},
    }

    bundle = main._direct_answer_evidence_bundle(metadata, original)

    assert bundle is not None
    assert bundle["coverage_status"] == "complete"
    assert bundle["missing_aspect_ids"] == []


def test_direct_exact_section_bundle_ignores_retrieval_only_device_context() -> None:
    metadata = {
        "scope_decision": {"status": "in_scope"},
        "original_user_message": "安装右盖时曲轴油封和离合器拉杆要注意什么？",
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "arguments": {"source": "section_text_lookup"},
                "result_data": [{
                    "id": "right-cover-install",
                    "content": (
                        "检查曲轴油封，损坏时更换；安装离合器拉杆，"
                        "使顶杆槽与右盖顶杆孔对齐。"
                    ),
                    "metadata": {
                        "chunk_id": "right-cover-install",
                        "original_title_match": True,
                    },
                }],
            }],
        }],
    }
    original = {
        "coverage_status": "partial",
        "aspect_support": [{
            "aspect_id": "expanded-retrieval-aspect",
            "aspect_text": "摩托车 右盖安装 离合器拉杆 安装注意事项",
            "supported": False,
            "evidence_ids": [],
        }],
        "missing_aspect_ids": ["expanded-retrieval-aspect"],
        "capabilities": {"may_cite_manual": True},
    }

    bundle = main._direct_answer_evidence_bundle(metadata, original)

    assert bundle is not None
    assert bundle["coverage_status"] == "complete"
    assert bundle["missing_aspect_ids"] == []


def test_direct_exact_section_bundle_keeps_non_parameter_missing_aspect_partial() -> None:
    metadata = {
        "scope_decision": {"status": "in_scope"},
        "original_user_message": "如何安装右盖并检查曲轴油封",
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "arguments": {"source": "section_text_lookup"},
                "result_data": [{
                    "id": "right-cover-install",
                    "content": "装上定位销和全新的右曲轴箱盖垫片，再盖上右曲轴箱盖。",
                    "metadata": {
                        "chunk_id": "right-cover-install",
                        "original_title_match": True,
                    },
                }],
            }],
        }],
    }
    original = {
        "coverage_status": "partial",
        "aspect_support": [
            {
                "aspect_id": "cover-install",
                "aspect_text": "安装右曲轴箱盖",
                "supported": True,
                "evidence_ids": ["right-cover-install"],
            },
            {
                "aspect_id": "seal-check",
                "aspect_text": "检查曲轴油封",
                "supported": False,
                "evidence_ids": [],
            },
        ],
        "missing_aspect_ids": ["seal-check"],
        "capabilities": {"may_cite_manual": True},
    }

    bundle = main._direct_answer_evidence_bundle(metadata, original)

    assert bundle is not None
    assert bundle["coverage_status"] == "partial"
    assert bundle["missing_aspect_ids"] == ["seal-check"]


def test_complete_exact_title_table_answer_does_not_append_stale_partial_notice() -> None:
    original_bundle = {
        "coverage_status": "partial",
        "aspect_support": [{
            "aspect_id": "inventory-query",
            "aspect_text": "摩托车发动机 气缸活塞 装配 部件清单",
            "supported": False,
            "evidence_ids": [],
        }],
        "missing_aspect_ids": ["inventory-query"],
        "conflict_eligible": [],
        "capabilities": {"may_cite_manual": True},
    }
    trace = [
        {
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": [{
                    "id": "semantic-hit",
                    "content": "气缸活塞装配部件清单",
                    "metadata": {
                        "qualification": "qualified",
                        "document_id": "manual-1",
                        "chunk_id": "semantic-hit",
                        "evidence_bundle": original_bundle,
                    },
                }],
            }],
        },
        {
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "arguments": {"source": "section_table_lookup"},
                "result_data": [{
                    "id": "complete-table",
                    "content": "1. 气缸体分部件；数量：1\n2. 箱体缸体垫片；数量：1",
                    "metadata": {
                        "qualification": "qualified",
                        "document_id": "manual-1",
                        "chunk_id": "complete-table",
                        "original_title_match": True,
                    },
                }],
            }],
        },
    ]
    output = _output("rag_table_direct", trace)
    output.metadata.update({
        "deterministic_table_answer": True,
        "_deterministic_answer_table_complete": True,
        "scope_decision": {"status": "in_scope"},
    })

    finalized = main._finalize_knowledge_output(
        "帮我查询摩托车发动机气缸活塞装配部件清单",
        output,
        candidate_message=(
            "根据手册第17-18页“5.1 气缸活塞装配部件清单”，气缸活塞装配所用部件如下：\n"
            "1. 气缸体分部件；数量：1\n"
            "2. 箱体缸体垫片；数量：1"
        ),
    )

    assert finalized.metadata["coverage_status"] == "complete"
    assert "当前资料没有明确说明" not in finalized.message


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


def test_non_stream_policy_direct_skips_all_manual_overrides(monkeypatch) -> None:
    request = ChatRequest(
        session_id="fallback-finalized",
        message="飞机在运行时发动机出现异响是什么原因？",
    )
    policy = {
        "mode": "MAINTENANCE_AI_FALLBACK",
        "manual_citation_allowed": False,
        "images_allowed": False,
    }
    input_data = AgentInput(
        user_message=request.message,
        session_id=request.session_id,
        context={
            "intent_decision": {"intent": "fault_diagnosis"},
            "scope_decision": {"status": "out_of_scope", "reason": "unsupported_device"},
            "response_policy": policy,
        },
    )
    direct_output = AgentOutput(
        agent_name="fix_agent",
        message="知识库没有该设备对应文档，以下内容来自 AI，仅供参考。",
        tools_used=[],
        metadata={
            "execution_mode": "maintenance_ai_fallback_direct",
            "deterministic_direct": True,
            "response_policy": policy,
        },
    )

    async def _direct(*args, **kwargs):
        return direct_output

    async def _unexpected_async(*args, **kwargs):
        pytest.fail("manual lookup must not run for policy-direct output")

    def _unexpected_sync(*args, **kwargs):
        pytest.fail("manual formatter must not run for policy-direct output")

    monkeypatch.setattr(main, "_prepare_chat_agent_input", lambda request: _awaitable(input_data))
    monkeypatch.setattr(main, "_try_response_policy_direct", _direct)
    monkeypatch.setattr(main, "_collect_direct_section_table_items", _unexpected_async)
    monkeypatch.setattr(main, "_format_inventory_table_answer_from_metadata", _unexpected_sync)
    monkeypatch.setattr(main, "_format_manual_evidence_answer_from_metadata", _unexpected_sync)
    monkeypatch.setattr(main, "_collect_direct_section_images", _unexpected_async)
    monkeypatch.setattr(main, "_collect_direct_evidence_page_images", _unexpected_sync)

    response = asyncio.run(main.chat(request))

    assert response.message == direct_output.message
    assert not response.tools_used
    assert response.evidence_images == []
    assert response.metadata["response_policy"]["mode"] == "MAINTENANCE_AI_FALLBACK"


def test_non_stream_endpoint_removes_emojis_from_final_answer(monkeypatch) -> None:
    request = ChatRequest(session_id="emoji-non-stream", message="介绍一下检修注意事项")
    input_data = AgentInput(user_message=request.message, session_id=request.session_id)
    direct_output = AgentOutput(
        agent_name="fix_agent",
        message="🔹检查温度 80℃。⚠️ 扭矩保持 12 N·m。👨🏽‍🔧",
        tools_used=[],
        metadata={
            "execution_mode": "general_ai_direct",
            "deterministic_direct": True,
            "response_policy": {"mode": "GENERAL_AI", "manual_citation_allowed": False},
        },
    )

    async def _direct(*args, **kwargs):
        return direct_output

    monkeypatch.setattr(main, "_prepare_chat_agent_input", lambda request: _awaitable(input_data))
    monkeypatch.setattr(main, "_try_causal_follow_up_resolution", _async_none)
    monkeypatch.setattr(main, "_try_response_policy_direct", _direct)
    monkeypatch.setattr(main, "_collect_direct_section_table_items", _async_empty)
    monkeypatch.setattr(main, "_format_inventory_table_answer_from_metadata", lambda *args: None)
    monkeypatch.setattr(main, "_format_manual_evidence_answer_from_metadata", lambda *args: None)
    monkeypatch.setattr(main, "_collect_direct_section_images", _async_empty)
    monkeypatch.setattr(main, "_collect_direct_evidence_page_images", lambda *args: [])

    response = asyncio.run(main.chat(request))

    assert response.message == "检查温度 80℃。 扭矩保持 12 N·m。"


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
        context={
            "scope_decision": {"status": "in_scope"},
            "response_policy": {
                "mode": "PENDING_RETRIEVAL",
                "manual_citation_allowed": False,
                "images_allowed": False,
            },
        },
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
    assert response.metadata["deterministic_manual_evidence_answer"] is True


def test_non_stream_table_override_does_not_read_uninitialized_manual_answer(monkeypatch) -> None:
    trace = _manual_trace()
    request = ChatRequest(
        session_id="table-override-finalized",
        message="水泵装配零件有哪些？",
        mode=AgentMode.RETRIEVAL,
    )
    input_data = AgentInput(user_message=request.message, session_id=request.session_id)

    class _Agent:
        async def run_with_react(self, _input):
            return _output("react", trace)

    class _Review:
        async def review(self, output, level="full"):
            return output

    monkeypatch.setattr(main, "_prepare_chat_agent_input", lambda request: _awaitable(input_data))
    monkeypatch.setattr(main, "_try_causal_follow_up_resolution", _async_none)
    monkeypatch.setattr(main, "_try_scope_guard", lambda *args: None)
    monkeypatch.setattr(main, "_try_domain_rule_direct", _async_none)
    monkeypatch.setattr(main, "_should_use_rag_fast_path", lambda request: False)
    monkeypatch.setattr(main, "get_fix_agent", lambda: _Agent())
    monkeypatch.setattr(main, "get_review_agent", lambda: _Review())
    monkeypatch.setattr(main, "_collect_direct_section_table_items", _async_empty)
    monkeypatch.setattr(
        main,
        "_format_inventory_table_answer_from_metadata",
        lambda *args: "水泵装配包含水泵盖和密封圈。",
    )
    monkeypatch.setattr(
        main,
        "_format_manual_evidence_answer_from_metadata",
        lambda *args: pytest.fail("table override must skip manual evidence formatting"),
    )
    monkeypatch.setattr(main, "_collect_direct_section_images", _async_empty)
    monkeypatch.setattr(main, "_collect_direct_evidence_page_images", lambda *args: [])
    monkeypatch.setattr(
        main,
        "build_follow_up",
        lambda *args: pytest.fail("table override must skip diagnostic follow-up"),
    )

    response = asyncio.run(main.chat(request))

    assert response.success is True
    assert response.metadata["deterministic_table_answer"] is True


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


def test_stream_policy_direct_removes_emojis_from_every_token(monkeypatch) -> None:
    request = ChatRequest(session_id="emoji-stream", message="介绍一下检修注意事项")
    input_data = AgentInput(user_message=request.message, session_id=request.session_id)
    direct_output = AgentOutput(
        agent_name="fix_agent",
        message="✅温度 80℃，公差 ±0.2。🇨🇳 👩🏾‍🔧",
        tools_used=[],
        metadata={"execution_mode": "general_ai_direct", "deterministic_direct": True},
    )

    async def _direct(*args, **kwargs):
        return direct_output

    monkeypatch.setattr(main, "_prepare_chat_agent_input", lambda request: _awaitable(input_data))
    monkeypatch.setattr(main, "_try_causal_follow_up_resolution", _async_none)
    monkeypatch.setattr(main, "_try_response_policy_direct", _direct)

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
    token_contents = [
        event["data"]["content"]
        for event in events
        if event.get("event") == "token"
    ]

    assert "".join(token_contents) == "温度 80℃，公差 ±0.2。 "
    assert all(content not in {"✅", "🇨", "🇳", "👩", "🏾", "🔧", "\u200d", "\ufe0f"} for content in token_contents)


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
