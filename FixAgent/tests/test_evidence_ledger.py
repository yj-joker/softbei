"""Stable evidence-ledger tests for every knowledge source."""

import hashlib
import json

from services.retrieval.evidence import EvidenceLedger


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
                                    "pathIds": ["path-1"],
                                    "nodeIds": ["node-1", "node-2"],
                                    "relationshipTypes": ["HAS_FAULT"],
                                    "summary": "图谱证据",
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
        "graph:path-1",
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
