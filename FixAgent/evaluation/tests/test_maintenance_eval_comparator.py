import csv
from pathlib import Path

from evaluation.maintenance_eval_comparator import compare_and_build_report


def _write_csv(path: Path, rows: list[dict]) -> None:
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def test_compare_detects_pass_to_fail_regression(tmp_path: Path):
    baseline_path = tmp_path / "baseline.csv"
    optimized_path = tmp_path / "optimized.csv"
    _write_csv(baseline_path, [{"id": "case_001", "final_pass": "True", "latency_ms": "100"}])
    _write_csv(optimized_path, [{"id": "case_001", "final_pass": "False", "latency_ms": "110"}])

    report = compare_and_build_report(baseline_path, optimized_path)

    assert report["regression_detected"] is True
    assert "case_001" in report["regressions"]
    assert report["summary"]["pass_to_fail"] == 1


def test_compare_improvement_only_no_regression(tmp_path: Path):
    baseline_path = tmp_path / "baseline.csv"
    optimized_path = tmp_path / "optimized.csv"
    _write_csv(baseline_path, [{"id": "case_001", "final_pass": "False", "latency_ms": "100"}])
    _write_csv(optimized_path, [{"id": "case_001", "final_pass": "True", "latency_ms": "100"}])

    report = compare_and_build_report(baseline_path, optimized_path)

    assert report["regression_detected"] is False
    assert report["summary"]["fail_to_pass"] == 1
    assert report["summary"]["pass_to_fail"] == 0


def test_compare_scope_regression_detected_without_final_pass_change(tmp_path: Path):
    baseline_path = tmp_path / "baseline.csv"
    optimized_path = tmp_path / "optimized.csv"
    _write_csv(
        baseline_path,
        [
            {
                "id": "case_001",
                "final_pass": "True",
                "latency_ms": "100",
                "evidence_score_available": "True",
                "evidence_scope_isolation_pass": "True",
                "evidence_unsupported_completion_free": "True",
            }
        ],
    )
    _write_csv(
        optimized_path,
        [
            {
                "id": "case_001",
                "final_pass": "True",
                "latency_ms": "100",
                "evidence_score_available": "True",
                "evidence_scope_isolation_pass": "False",
                "evidence_unsupported_completion_free": "True",
            }
        ],
    )

    report = compare_and_build_report(baseline_path, optimized_path)

    assert report["scope_regression"] is True
    assert report["regression_detected"] is False
    assert "case_001" in report["scope_regression_case_ids"]


def test_compare_latency_regression_when_ratio_exceeds_120_percent(tmp_path: Path):
    baseline_path = tmp_path / "baseline.csv"
    optimized_path = tmp_path / "optimized.csv"
    _write_csv(baseline_path, [{"id": "case_001", "final_pass": "True", "latency_ms": "100"}])
    _write_csv(optimized_path, [{"id": "case_001", "final_pass": "True", "latency_ms": "130"}])

    report = compare_and_build_report(baseline_path, optimized_path)

    assert report["latency_regression"] is True
    assert report["regression_detected"] is False


def test_compare_missing_case_ids_are_reported_not_silently_dropped(tmp_path: Path):
    baseline_path = tmp_path / "baseline.csv"
    optimized_path = tmp_path / "optimized.csv"
    _write_csv(
        baseline_path,
        [
            {"id": "case_001", "final_pass": "True", "latency_ms": "100"},
            {"id": "case_002", "final_pass": "True", "latency_ms": "100"},
        ],
    )
    _write_csv(optimized_path, [{"id": "case_001", "final_pass": "True", "latency_ms": "100"}])

    report = compare_and_build_report(baseline_path, optimized_path)

    assert report["missing_in_optimized"] == ["case_002"]
    assert report["summary"]["total_aligned"] == 1
