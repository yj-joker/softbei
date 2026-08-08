"""Stable evidence-ledger tests for every knowledge source."""

import hashlib
import json

from services.retrieval.evidence import EvidenceLedger
from services.llm.react_loop import ToolExecutor
import asyncio


def test_ledger_appends_deduplicates_and_serializes_stably() -> None:
    ledger = EvidenceLedger()
    entry = {
        "evidence_id": "manual:manual-1:chunk-1",
        "source_type": "manual",
        "text": "火花塞间隙为 0.7 到 0.9 mm",
        "qualification": "qualified",
        "source": {"document_id": "manual-1", "chunk_id": "chunk-1", "page": 3},
    }

    ledger.append(entry)
    ledger.append(dict(reversed(list(entry.items()))))

    assert len(ledger.entries) == 1
    expected_json = json.dumps([entry], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert ledger.canonical_json() == expected_json
    assert ledger.digest == hashlib.sha256(expected_json.encode("utf-8")).hexdigest()


def test_ledger_collects_manual_rule_and_graph_from_react_trace() -> None:
    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": {
                            "qualified_evidence": [
                                {
                                    "id": "chunk-1",
                                    "content": "手册证据",
                                    "metadata": {
                                        "qualification": "qualified",
                                        "document_id": "manual-1",
                                        "document_version": "v1",
                                        "chunk_id": "chunk-1",
                                        "page": 3,
                                    },
                                }
                            ]
                        },
                    },
                    {
                        "name": "domain_rule_engine",
                        "result_data": {
                            "rule_id": "rule-1",
                            "status": "active",
                            "content": "已审核规则",
                            "evidence_sources": ["manual-1:3"],
                        },
                    },
                    {
                        "name": "java_graph_diagnosis_path",
                        "result_data": {
                            "raw_records": [
                                {
                                    "pathId": "kgpath:device-1:component-1:fault-1",
                                    "nodeIds": ["device-1", "component-1", "fault-1"],
                                    "relationshipTypes": ["OWNS", "CAUSES"],
                                    "deviceId": "device-1",
                                    "deviceName": "一号发动机",
                                    "componentId": "component-1",
                                    "componentName": "张紧轮",
                                    "faultId": "fault-1",
                                    "faultName": "轴承磨损",
                                    "documentId": "manual-1",
                                    "documentVersion": "v1",
                                    "sectionId": "sec-bearing",
                                    "sourceChunkUids": ["chunk-graph-1"],
                                    "pages": [12],
                                    "graphRevision": "graph-v1",
                                    "provenanceStatus": "complete",
                                    "matchScore": 3,
                                }
                            ]
                        },
                    },
                ]
            }
        ]
    }

    ledger = EvidenceLedger.from_react_trace(metadata)

    assert [entry["source_type"] for entry in ledger.entries] == [
        "manual",
        "domain_rule",
        "graph",
    ]
    assert [entry["evidence_id"] for entry in ledger.entries] == [
        "manual:manual-1:chunk-1",
        "domain_rule:rule-1",
        "graph:kgpath:device-1:component-1:fault-1:none",
    ]
    graph_entry = ledger.entries[2]
    assert graph_entry["path_id"] == "kgpath:device-1:component-1:fault-1"
    assert graph_entry["relationship_types"] == ["OWNS", "CAUSES"]
    assert graph_entry["source"]["source_chunk_uids"] == ["chunk-graph-1"]


def test_manual_ledger_preserves_stable_chunk_and_table_identifiers() -> None:
    metadata = {
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": {
                    "qualified_evidence": [{
                        "doc_id": "vector-row-1",
                        "content": "manual evidence",
                        "metadata": {
                            "qualification": "qualified",
                            "document_id": "manual-1",
                            "chunk_id": "chunk-1",
                            "chunk_uid": "chunk-uid-1",
                            "source_chunk_uid": "source-uid-1",
                            "source_chunk_uids": ["source-uid-1", "source-uid-2"],
                            "table_id": "table-1",
                        },
                    }],
                },
            }],
        }],
    }

    ledger = EvidenceLedger.from_react_trace(metadata)

    source = ledger.entries[0]["source"]
    assert source["chunk_uid"] == "chunk-uid-1"
    assert source["source_chunk_uids"] == [
        "source-uid-1",
        "source-uid-2",
        "chunk-uid-1",
    ]
    assert source["table_id"] == "table-1"


def test_manual_ledger_accepts_chunk_uid_as_primary_identity_without_legacy_id() -> None:
    metadata = {
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": {
                    "qualified_evidence": [{
                        "content": "stable uid evidence",
                        "metadata": {
                            "qualification": "qualified",
                            "document_id": "manual-1",
                            "chunk_uid": "chunk-uid-only",
                            "source_chunk_uids": ["source-parent-1"],
                        },
                    }],
                },
            }],
        }],
    }

    ledger = EvidenceLedger.from_react_trace(metadata)

    assert [entry["evidence_id"] for entry in ledger.entries] == [
        "manual:manual-1:chunk-uid-only"
    ]
    assert ledger.entries[0]["source"]["chunk_uid"] == "chunk-uid-only"
    assert ledger.entries[0]["source"]["source_chunk_uids"] == [
        "source-parent-1",
        "chunk-uid-only",
    ]


def test_ledger_ignores_unstable_or_inactive_sources() -> None:
    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {"name": "knowledge_retrieval", "result_data": [{"content": "no identity"}]},
                    {
                        "name": "domain_rule_engine",
                        "result_data": {"rule_id": "draft-1", "status": "draft", "content": "draft"},
                    },
                    {
                        "name": "java_graph_diagnosis_path",
                        "result_data": {"raw_records": [{"summary": "no path identity"}]},
                    },
                ]
            }
        ]
    }

    assert EvidenceLedger.from_react_trace(metadata).entries == []


def test_ledger_consumes_server_pre_normalized_graph_evidence() -> None:
    normalized = {
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
        "confidence": 3.0,
        "graph_revision": "graph-v1",
        "provenance_status": "complete",
        "claim_types": ["device_identity", "component_ownership", "fault_relation"],
        "supports_aspect_ids": ["device", "component", "fault-cause"],
        "text": "一号发动机 -> OWNS -> 张紧轮 -> CAUSES -> 轴承磨损",
        "source": {
            "document_id": "manual-1",
            "document_version": "v1",
            "section_id": "sec-bearing",
            "source_chunk_uids": ["chunk-1"],
            "pages": [12],
        },
    }
    metadata = {
        "react_trace": [{
            "iteration": 0,
            "action": "server_pre_retrieval",
            "tool_calls": [{
                "name": "java_graph_diagnosis_path",
                "result_data": {"status": "found", "evidence": [normalized]},
            }],
        }],
    }

    ledger = EvidenceLedger.from_react_trace(metadata)

    assert len(ledger.entries) == 1
    assert ledger.entries[0]["evidence_id"] == normalized["evidence_id"]
    assert ledger.entries[0]["claim_types"] == normalized["claim_types"]
    assert ledger.entries[0]["source"]["document_version"] == "v1"


def test_unknown_tool_is_not_marked_as_an_actual_execution() -> None:
    async def run() -> tuple[dict, dict]:
        return await ToolExecutor({}).execute({
            "id": "call-1",
            "function": {"name": "component_reverse_device", "arguments": "{}"},
        })

    call, payload = asyncio.run(run())

    assert payload["error"] == "Tool component_reverse_device not found"
    assert call["executed"] is False
    assert call["execution_status"] == "not_registered"


def test_ledger_canonicalizes_parent_child_representations_and_keeps_source_position() -> None:
    def item(step: int, record_id: str, chunk_type: str, text: str) -> dict:
        return {
            "id": record_id,
            "content": text,
            "metadata": {
                "qualification": "qualified",
                "document_id": "manual-1",
                "chunk_id": record_id,
                "source_chunk_id": f"source-{step}",
                "chunk_type": chunk_type,
                "parent_chunk_id": "parent-procedure",
                "parent_section_id": "sec-tensioner",
                "section_index": 4,
                "page": 13,
                "source_index": step,
                "child_index": step - 1,
            },
        }

    metadata = {
        "react_trace": [
            {
                "tool_calls": [{
                    "name": "knowledge_retrieval",
                    "result_data": [
                        item(2, "contextual-2", "text", "章节上下文：2. 执行第2步。"),
                        item(1, "contextual-1", "step_raw", "1. 执行第1步。"),
                    ],
                }],
            },
            {
                "tool_calls": [{
                    "name": "knowledge_retrieval",
                    "result_data": [
                        item(2, "direct-2", "step_raw", "2. 执行第2步。"),
                    ],
                }],
            },
        ],
    }

    ledger = EvidenceLedger.from_react_trace(metadata)

    assert [entry["evidence_id"] for entry in ledger.entries] == [
        "manual:manual-1:source-2",
        "manual:manual-1:source-1",
    ]
    step_2 = ledger.entries[0]
    assert step_2["text"] == "2. 执行第2步。"
    assert step_2["source"]["chunk_id"] == "source-2"
    assert step_2["source"]["source_chunk_id"] == "source-2"
    assert step_2["source"]["parent_chunk_id"] == "parent-procedure"
    assert step_2["source"]["parent_section_id"] == "sec-tensioner"
    assert step_2["source"]["section_index"] == 4
    assert step_2["source"]["source_index"] == 2
    assert step_2["source"]["child_index"] == 1
