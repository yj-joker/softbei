import pytest

from evaluation.maintenance_eval_comparator import build_paired_ablation_report


def _row(case_id: str, passed: bool, *, latency: int, **extra):
    return {
        "id": case_id,
        "final_pass": passed,
        "grounding_pass": passed,
        "required_nugget_recall": 1.0 if passed else 0.0,
        "latency_ms": latency,
        "answerable": True,
        "question_type": "multi_hop",
        "difficulty": "hard",
        "graph_dependency": "required",
        **extra,
    }


def test_paired_report_calculates_exact_mcnemar_bootstrap_and_groups() -> None:
    no_graph = [_row(f"c{i}", False, latency=100 + i) for i in range(1, 6)]
    graph = [_row(f"c{i}", i != 5, latency=120 + i) for i in range(1, 6)]

    report = build_paired_ablation_report(
        no_graph,
        graph,
        bootstrap_samples=10_000,
        seed=7,
    )

    assert report["aligned_case_count"] == 5
    assert report["final_pass"]["no_graph_rate"] == 0.0
    assert report["final_pass"]["graph_rate"] == 0.8
    assert report["final_pass"]["difference"] == 0.8
    assert report["final_pass"]["bootstrap_samples"] == 10_000
    assert report["final_pass"]["confidence_interval_95"] == [0.4, 1.0]
    assert report["final_pass"]["mcnemar_exact"] == {
        "no_graph_only_success": 0,
        "graph_only_success": 4,
        "discordant_pairs": 4,
        "p_value": 0.125,
    }
    assert report["groups"]["question_type"]["multi_hop"]["case_count"] == 5
    assert report["groups"]["graph_dependency"]["required"]["final_pass_difference"] == 0.8


def test_paired_report_includes_latency_and_optional_usage_cost() -> None:
    no_graph = [
        _row("a", True, latency=100, total_tokens=10, cost=0.01),
        _row("b", True, latency=200, total_tokens=20, cost=0.02),
    ]
    graph = [
        _row("a", True, latency=300, total_tokens=30, cost=0.03),
        _row("b", True, latency=400, total_tokens=40, cost=0.04),
    ]

    report = build_paired_ablation_report(no_graph, graph, bootstrap_samples=100, seed=1)

    assert report["latency_ms"]["no_graph"] == {"mean": 150.0, "p50": 150.0, "p95": 195.0}
    assert report["latency_ms"]["graph"] == {"mean": 350.0, "p50": 350.0, "p95": 395.0}
    assert report["token_usage"] == {
        "available": True,
        "no_graph_mean": 15.0,
        "graph_mean": 35.0,
        "mean_difference": 20.0,
    }
    assert report["cost"] == {
        "available": True,
        "no_graph_mean": 0.015,
        "graph_mean": 0.035,
        "mean_difference": 0.02,
    }


def test_paired_report_marks_missing_pairs_and_unavailable_usage() -> None:
    report = build_paired_ablation_report(
        [_row("only-no", True, latency=100), _row("both", True, latency=100)],
        [_row("only-graph", True, latency=100), _row("both", True, latency=100)],
        bootstrap_samples=10,
    )

    assert report["aligned_case_count"] == 1
    assert report["missing_in_no_graph"] == ["only-graph"]
    assert report["missing_in_graph"] == ["only-no"]
    assert report["token_usage"] == {"available": False}
    assert report["cost"] == {"available": False}
