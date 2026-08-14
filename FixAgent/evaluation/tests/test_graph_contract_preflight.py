from __future__ import annotations

import pytest

from evaluation import graph_contract_preflight
from evaluation.graph_contract_preflight import preflight_graph_contract
from evaluation.maintenance_eval_schema import (
    AllowedSource,
    ClaimConstraint,
    MaintenanceEvalCase,
)


def _case(source: AllowedSource) -> MaintenanceEvalCase:
    return MaintenanceEvalCase(
        case_id="case-1",
        graph_dependency="required",
        claim_constraints=[
            ClaimConstraint(
                claim_id="graph-path",
                answer_patterns=["fault"],
                evidence_patterns=["fault"],
                allowed_sources=[source],
            )
        ],
    )


def _snapshot(**overrides):
    record = {
        "document_id": "manual-1",
        "document_version": "v1",
        "device_name": "motorcycle engine",
        "component_name": "spark plug",
        "fault_name": "spark plug damaged",
        "relationship_types": ["OWNS", "CAUSES"],
        "source_chunk_uids": ["chunk-1"],
        "pages": [3],
        "graph_revision": "rev-2",
        "node_ids": ["new-device", "new-component", "new-fault"],
        "path_ids": ["new-path"],
    }
    record.update(overrides)
    return {"records": [record]}


def test_stable_semantics_allow_uuid_change() -> None:
    source = AllowedSource(
        source_type="graph",
        document_id="manual-1",
        document_version="v1",
        pages=[3],
        chunk_ids=["chunk-1"],
        node_ids=["old-device", "old-component", "old-fault"],
        path_ids=["kgpath:stable"],
        relationship_types=["OWNS", "CAUSES"],
        device_name="motorcycle engine",
        component_name="spark plug",
        fault_name="spark plug damaged",
        graph_revision="rev-2",
    )

    report = preflight_graph_contract(
        [_case(source)],
        _snapshot(path_ids=["kgpath:stable"]),
    )

    assert report["passed"] is True
    assert report["matched_graph_sources"] == 1
    assert report["errors"] == []


def test_preflight_rejects_semantic_match_when_stable_path_is_wrong() -> None:
    source = AllowedSource(
        source_type="graph",
        document_id="manual-1",
        document_version="v1",
        pages=[3],
        chunk_ids=["chunk-1"],
        relationship_types=["OWNS", "CAUSES"],
        path_ids=["kgpath:expected"],
        device_name="motorcycle engine",
        component_name="spark plug",
        fault_name="spark plug damaged",
        graph_revision="rev-2",
    )

    report = preflight_graph_contract(
        [_case(source)],
        _snapshot(path_ids=["kgpath:wrong"]),
    )

    assert report["passed"] is False
    assert report["matched_graph_sources"] == 0
    assert report["errors"][0]["code"] == "graph_stable_path_not_found"


def test_semantic_identity_mismatch_fails_even_when_uuid_matches() -> None:
    source = AllowedSource(
        source_type="graph",
        document_id="manual-1",
        document_version="v1",
        pages=[3],
        chunk_ids=["chunk-1"],
        node_ids=["new-device", "new-component", "new-fault"],
        path_ids=["new-path"],
        relationship_types=["OWNS", "CAUSES"],
        device_name="motorcycle engine",
        component_name="oil pump",
        fault_name="spark plug damaged",
        graph_revision="rev-2",
    )

    report = preflight_graph_contract([_case(source)], _snapshot())

    assert report["passed"] is False
    assert report["errors"][0]["code"] == "graph_source_not_found"


def test_document_version_and_revision_are_contract_dimensions() -> None:
    source = AllowedSource(
        source_type="graph",
        document_id="manual-1",
        document_version="v2",
        pages=[3],
        chunk_ids=["chunk-1"],
        relationship_types=["OWNS", "CAUSES"],
        device_name="motorcycle engine",
        component_name="spark plug",
        fault_name="spark plug damaged",
        graph_revision="rev-3",
    )

    report = preflight_graph_contract([_case(source)], _snapshot())

    assert report["passed"] is False
    assert report["errors"][0]["code"] == "graph_source_not_found"


def test_controlled_fault_alias_matches_only_within_same_source_contract() -> None:
    source = AllowedSource(
        source_type="graph",
        document_id="manual-1",
        document_version="v1",
        pages=[3],
        chunk_ids=["chunk-1"],
        relationship_types=["OWNS", "CAUSES"],
        device_name="motorcycle engine",
        component_name="oil pump",
        fault_name="机油泵卡死",
        graph_revision="rev-2",
    )

    report = preflight_graph_contract(
        [_case(source)],
        _snapshot(component_name="oil pump", fault_name="机油泵卡滞"),
    )

    assert report["passed"] is True
    assert report["matched_graph_sources"] == 1


def test_controlled_fault_alias_does_not_cross_chunk_boundary() -> None:
    source = AllowedSource(
        source_type="graph",
        document_id="manual-1",
        document_version="v1",
        pages=[3],
        chunk_ids=["chunk-1"],
        relationship_types=["OWNS", "CAUSES"],
        device_name="motorcycle engine",
        component_name="oil pump",
        fault_name="机油泵卡死",
        graph_revision="rev-2",
    )

    report = preflight_graph_contract(
        [_case(source)],
        _snapshot(
            component_name="oil pump",
            fault_name="机油泵卡滞",
            source_chunk_uids=["chunk-2"],
        ),
    )

    assert report["passed"] is False
    assert report["errors"][0]["code"] == "graph_source_not_found"


def test_generic_damage_term_is_not_a_canonical_alias() -> None:
    source = AllowedSource(
        source_type="graph",
        document_id="manual-1",
        document_version="v1",
        pages=[3],
        chunk_ids=["chunk-1"],
        relationship_types=["OWNS", "CAUSES"],
        device_name="motorcycle engine",
        component_name="oil pump",
        fault_name="机油泵损坏",
        graph_revision="rev-2",
    )

    report = preflight_graph_contract(
        [_case(source)],
        _snapshot(component_name="oil pump", fault_name="O 型圈损坏"),
    )

    assert report["passed"] is False
    assert report["errors"][0]["code"] == "graph_source_not_found"


@pytest.mark.parametrize(
    ("expected_fault", "actual_fault"),
    [
        ("起动电机不灵活", "起动电机轴不灵活"),
        ("机油泵卡死", "机油泵齿轮卡滞"),
        ("传动装置不灵活", "传动主轴转动不灵活"),
        ("传动装置不顺畅", "换档不顺畅"),
        ("轴承磨损", "轴承内圈磨损"),
    ],
)
def test_reviewed_manual_fault_aliases_are_equivalent_with_exact_scope(
    expected_fault,
    actual_fault,
) -> None:
    source = AllowedSource(
        source_type="graph",
        document_id="manual-1",
        document_version="v1",
        pages=[3],
        chunk_ids=["chunk-1"],
        relationship_types=["OWNS", "CAUSES"],
        device_name="motorcycle engine",
        component_name="spark plug",
        fault_name=expected_fault,
        graph_revision="rev-2",
    )

    report = preflight_graph_contract(
        [_case(source)],
        _snapshot(fault_name=actual_fault),
    )

    assert report["passed"] is True


def test_required_graph_source_without_stable_semantics_is_rejected() -> None:
    source = AllowedSource(
        source_type="graph",
        document_id="manual-1",
        document_version="v1",
        pages=[3],
        chunk_ids=["chunk-1"],
        node_ids=["old-device", "old-component", "old-fault"],
        path_ids=["old-path"],
        relationship_types=["OWNS", "CAUSES"],
    )

    report = preflight_graph_contract([_case(source)], _snapshot())

    assert report["passed"] is False
    assert report["errors"][0]["code"] == "stable_graph_identity_missing"


def test_snapshot_missing_source_location_and_revision_is_reported() -> None:
    source = AllowedSource(
        source_type="graph",
        document_id="manual-1",
        document_version="v1",
        pages=[3],
        chunk_ids=["chunk-1"],
        relationship_types=["OWNS", "CAUSES"],
        device_name="motorcycle engine",
        component_name="spark plug",
        fault_name="spark plug damaged",
        graph_revision="rev-2",
    )

    report = preflight_graph_contract(
        [_case(source)],
        _snapshot(source_chunk_uids=[], pages=[], graph_revision=""),
    )

    assert report["passed"] is False
    assert report["errors"][0]["code"] == "graph_source_not_found"
    warning_codes = {item["code"] for item in report["warnings"]}
    assert "snapshot_source_location_missing" in warning_codes
    assert "snapshot_graph_revision_missing" in warning_codes


def test_loads_compact_stable_snapshot_from_neo4j(monkeypatch) -> None:
    class _Result:
        def data(self):
            return [{
                "documentId": "manual-1",
                "documentVersion": "v1",
                "deviceId": "device-1",
                "deviceStableId": "kg:device:stable",
                "deviceName": "motorcycle engine",
                "componentId": "component-1",
                "componentStableId": "kg:component:stable",
                "componentName": "spark plug",
                "faultId": "fault-1",
                "faultStableId": "kg:fault:stable",
                "faultName": "spark plug damaged",
                "componentChunks": ["chunk-1"],
                "faultChunks": ["chunk-1"],
                "pageStart": 3,
                "pageEnd": 3,
                "graphRevision": "manual:manual-1:v1",
                "pathStableId": "kgpath:stable",
            }]

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def run(self, query, **params):
            assert params == {"document_id": "manual-1"}
            assert "d.stable_id" in query
            assert "c.stable_id" in query
            assert "f.stable_id" in query
            assert "causes.path_stable_id" in query
            assert "f.graph_revision" in query
            return _Result()

    class _Driver:
        def session(self, **kwargs):
            return _Session()

        def close(self):
            return None

    monkeypatch.setattr(
        graph_contract_preflight,
        "_open_neo4j_driver",
        lambda: _Driver(),
        raising=False,
    )

    snapshot = graph_contract_preflight.load_neo4j_graph_snapshot("manual-1")

    assert snapshot["record_count"] == 1
    assert snapshot["records"][0]["path_ids"] == ["kgpath:stable"]
    assert snapshot["records"][0]["source_chunk_uids"] == ["chunk-1"]
    assert snapshot["records"][0]["pages"] == [3]
