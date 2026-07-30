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
