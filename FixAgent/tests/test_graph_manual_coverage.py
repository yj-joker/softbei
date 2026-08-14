from __future__ import annotations

import importlib
import importlib.util

from services.retrieval.device_identity import QueryContract


def _module():
    module_name = "services.retrieval.graph_manual_coverage"
    assert importlib.util.find_spec(module_name) is not None, "Graph+Manual 覆盖合同尚未实现"
    return importlib.import_module(module_name)


def _contract() -> QueryContract:
    return QueryContract.from_mapping(
        {
            "intent": "fault_diagnosis",
            "component": "起动电机",
            "symptoms": ["不灵活"],
            "task_action": "repair_guidance",
        },
        raw_query="起动电机轴不灵活应该如何维修？",
    )


def _graph_relation() -> dict:
    return {
        "source_type": "graph",
        "qualification": "qualified",
        "relationship_types": ["OWNS", "CAUSES"],
        "component": {"name": "起动电机"},
        "fault": {"name": "起动电机轴不灵活"},
        "provenance_status": "complete",
        "source": {
            "document_id": "manual-1",
            "document_version": "v1",
            "section_id": "sec-starter",
            "source_chunk_uids": ["chunk-1"],
            "pages": [12],
        },
    }


def _manual_solution(*, document_id: str = "manual-1") -> dict:
    return {
        "source_type": "manual",
        "qualification": "qualified",
        "text": "检查起动电机轴承，若转动不灵活则更换轴承。",
        "source": {
            "document_id": document_id,
            "document_version": "v1",
            "section_id": "sec-starter",
            "chunk_uid": "chunk-2",
            "page": 12,
        },
    }


def test_graph_relation_without_manual_solution_requires_supplement() -> None:
    coverage = _module().evaluate_graph_manual_coverage(
        query=_contract(),
        graph_evidence=[_graph_relation()],
        manual_evidence=[],
    )

    assert coverage.component is True
    assert coverage.fault is True
    assert coverage.solution is False
    assert coverage.provenance is False
    assert coverage.complete is False


def test_graph_solution_label_does_not_replace_manual_repair_evidence() -> None:
    graph = {
        **_graph_relation(),
        "relationship_types": ["OWNS", "CAUSES", "HAS_SOLUTION"],
        "solution": {"title": "更换轴承", "verified": True},
    }

    coverage = _module().evaluate_graph_manual_coverage(
        query=_contract(),
        graph_evidence=[graph],
        manual_evidence=[],
    )

    assert coverage.solution is False
    assert coverage.complete is False


def test_same_document_manual_action_completes_graph_manual_coverage() -> None:
    coverage = _module().evaluate_graph_manual_coverage(
        query=_contract(),
        graph_evidence=[_graph_relation()],
        manual_evidence=[_manual_solution()],
    )

    assert coverage.component is True
    assert coverage.fault is True
    assert coverage.solution is True
    assert coverage.provenance is True
    assert coverage.complete is True


def test_foreign_manual_action_cannot_complete_graph_manual_coverage() -> None:
    coverage = _module().evaluate_graph_manual_coverage(
        query=_contract(),
        graph_evidence=[_graph_relation()],
        manual_evidence=[_manual_solution(document_id="foreign-manual")],
    )

    assert coverage.solution is False
    assert coverage.provenance is False
    assert coverage.complete is False
