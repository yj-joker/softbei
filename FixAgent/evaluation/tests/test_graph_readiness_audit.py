from evaluation.graph_readiness_audit import evaluate_readiness


POLICY = {
    "minimum_counts": {
        "device_count": 1,
        "component_count": 1,
        "fault_count": 1,
        "owns_count": 1,
        "causes_count": 1,
        "complete_path_count": 1,
    },
    "minimum_coverage": {
        "component_embedding_coverage": 1.0,
        "fault_embedding_coverage": 1.0,
        "path_identity_coverage": 1.0,
        "path_provenance_coverage": 0.95,
        "chunk_round_trip_coverage": 1.0,
    },
    "maximum_counts": {
        "orphan_fault_count": 0,
        "orphan_solution_count": 0,
    },
}


def _ready_metrics() -> dict:
    return {
        "device_count": 4,
        "component_count": 44,
        "fault_count": 12,
        "solution_count": 8,
        "owns_count": 44,
        "causes_count": 12,
        "has_solution_count": 8,
        "complete_path_count": 12,
        "component_embedding_coverage": 1.0,
        "fault_embedding_coverage": 1.0,
        "path_identity_coverage": 1.0,
        "path_provenance_coverage": 1.0,
        "chunk_round_trip_coverage": 1.0,
        "orphan_fault_count": 0,
        "orphan_solution_count": 0,
    }


def test_readiness_fails_with_specific_codes_when_fault_graph_is_empty() -> None:
    metrics = _ready_metrics()
    metrics.update(
        {
            "fault_count": 0,
            "causes_count": 0,
            "complete_path_count": 0,
            "component_embedding_coverage": 0.0,
            "path_identity_coverage": 0.0,
            "path_provenance_coverage": 0.0,
            "chunk_round_trip_coverage": 0.0,
        }
    )

    report = evaluate_readiness(metrics, POLICY)

    assert report["ready"] is False
    codes = {item["code"] for item in report["violations"]}
    assert codes >= {
        "fault_nodes_missing",
        "causes_edges_missing",
        "complete_paths_missing",
        "component_embedding_coverage_low",
        "path_identity_coverage_low",
        "path_provenance_coverage_low",
        "chunk_round_trip_coverage_low",
    }


def test_readiness_passes_only_when_every_policy_threshold_is_met() -> None:
    report = evaluate_readiness(_ready_metrics(), POLICY)

    assert report == {
        "ready": True,
        "metrics": _ready_metrics(),
        "violations": [],
    }


def test_readiness_rejects_orphan_faults_and_solutions() -> None:
    metrics = _ready_metrics()
    metrics["orphan_fault_count"] = 1
    metrics["orphan_solution_count"] = 2

    report = evaluate_readiness(metrics, POLICY)

    assert {item["code"] for item in report["violations"]} == {
        "orphan_fault_count_high",
        "orphan_solution_count_high",
    }
