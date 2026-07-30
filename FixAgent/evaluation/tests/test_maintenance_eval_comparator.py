import csv
import json
from pathlib import Path

from evaluation.maintenance_eval_comparator import (
    EvaluationArtifacts,
    build_strict_gate_report,
    compare_and_build_report,
    resolve_targeted_reruns,
    status_exit_code,
    main,
)


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


def _metric(numerator: int, denominator: int) -> dict:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": None if denominator == 0 else numerator / denominator,
    }


def _summary(*, core_numerator: int, fixed_templates: int, latency_total: int = 1000) -> dict:
    metrics = {
        "final_pass_rate": _metric(9, 10),
        "forbidden_claim_pass_rate": _metric(10, 10),
        "refusal_pass_rate": _metric(10, 10),
        "procedure_order_pass_rate": _metric(5, 5),
        "image_pass_rate": _metric(5, 5),
        "forbidden_image_pass_rate": _metric(5, 5),
        "evidence_nugget_coverage_rate": _metric(core_numerator, 10),
        "evidence_source_pass_rate": _metric(8, 10),
        "answer_evidence_alignment_pass_rate": _metric(8, 10),
        "scope_isolation_pass_rate": _metric(8, 10),
        "unsupported_completion_free_rate": _metric(8, 10),
        "partial_answer_correct_rate": _metric(4, 5),
        "refusal_integrity_pass_rate": _metric(8, 10),
        "fixed_template_rate": _metric(fixed_templates, 10),
        "style_proxy_pass_rate": _metric(9, 10),
    }
    return {
        "metric_counts": metrics,
        "latency_total_ms": latency_total,
        "request_count": 10,
    }


def _artifacts(
    *,
    summary: dict,
    safety_value: bool = True,
    include_case: bool = True,
) -> EvaluationArtifacts:
    case_rows = {
        "legacy_001": {
            "id": "legacy_001",
            "dataset_source": "maintenance_eval_dataset_v1.jsonl",
            "final_pass": True,
            "forbidden_claim_pass": safety_value,
            "refusal_pass": True,
            "procedure_order_pass": True,
            "image_pass": True,
        }
    }
    if not include_case:
        case_rows = {}
    return EvaluationArtifacts(case_rows=case_rows, turn_rows={}, summary=summary)


def test_strict_gate_keeps_only_when_all_floors_hold_and_core_improves() -> None:
    baseline = _artifacts(summary=_summary(core_numerator=5, fixed_templates=5))
    optimized = _artifacts(summary=_summary(core_numerator=6, fixed_templates=4))

    report = build_strict_gate_report(baseline, optimized)

    assert report["status"] == "keep"
    assert report["strict_improvement"] is True
    assert status_exit_code(report["status"]) == 0


def test_strict_gate_requires_targeted_rerun_for_one_old_safety_flip() -> None:
    baseline = _artifacts(summary=_summary(core_numerator=5, fixed_templates=5))
    optimized = _artifacts(
        summary=_summary(core_numerator=6, fixed_templates=4),
        safety_value=False,
    )

    report = build_strict_gate_report(baseline, optimized)

    assert report["status"] == "targeted_rerun_required"
    assert status_exit_code(report["status"]) == 3
    assert report["targeted_rerun_manifest"] == [
        {
            "case_id": "legacy_001",
            "row_level": "case",
            "field": "forbidden_claim_pass",
            "baseline_value": True,
            "optimized_value": False,
        }
    ]


def test_targeted_reruns_use_two_of_three_majority() -> None:
    baseline = _artifacts(summary=_summary(core_numerator=5, fixed_templates=5))
    optimized = _artifacts(
        summary=_summary(core_numerator=6, fixed_templates=4),
        safety_value=False,
    )
    report = build_strict_gate_report(baseline, optimized)
    rerun_fail = _artifacts(
        summary=_summary(core_numerator=6, fixed_templates=4),
        safety_value=False,
    )
    rerun_pass = _artifacts(
        summary=_summary(core_numerator=6, fixed_templates=4),
        safety_value=True,
    )

    sustained = resolve_targeted_reruns(report, [rerun_fail, rerun_pass])
    recovered = resolve_targeted_reruns(report, [rerun_pass, rerun_pass])

    assert sustained["status"] == "rollback_required"
    assert status_exit_code(sustained["status"]) == 2
    assert recovered["status"] == "keep"


def test_strict_gate_rolls_back_missing_cases_and_latency_over_120_percent() -> None:
    baseline = _artifacts(summary=_summary(core_numerator=5, fixed_templates=5))
    missing = _artifacts(
        summary=_summary(core_numerator=6, fixed_templates=4),
        include_case=False,
    )
    slow = _artifacts(
        summary=_summary(core_numerator=6, fixed_templates=4, latency_total=1201)
    )

    missing_report = build_strict_gate_report(baseline, missing)
    slow_report = build_strict_gate_report(baseline, slow)

    assert missing_report["status"] == "rollback_required"
    assert missing_report["missing_in_optimized"] == ["legacy_001"]
    assert slow_report["status"] == "rollback_required"
    assert slow_report["gates"]["latency_within_120_percent"]["pass"] is False


def test_strict_gate_uses_integer_ratios_and_ignores_na_metrics() -> None:
    baseline_summary = _summary(core_numerator=5, fixed_templates=5)
    optimized_summary = _summary(core_numerator=6, fixed_templates=4)
    baseline_summary["metric_counts"]["scope_isolation_pass_rate"] = _metric(1, 3)
    optimized_summary["metric_counts"]["scope_isolation_pass_rate"] = _metric(2, 6)
    baseline_summary["metric_counts"]["refusal_integrity_pass_rate"] = _metric(0, 0)
    optimized_summary["metric_counts"]["refusal_integrity_pass_rate"] = _metric(0, 0)

    report = build_strict_gate_report(
        _artifacts(summary=baseline_summary),
        _artifacts(summary=optimized_summary),
    )

    assert report["status"] == "keep"
    assert report["gates"]["scope_isolation_pass_rate_not_lower"]["comparison"] == "equal"
    assert report["gates"]["refusal_integrity_pass_rate_not_lower"]["comparison"] == "not_applicable"


def test_comparator_cli_returns_three_for_targeted_rerun(tmp_path: Path) -> None:
    baseline_csv = tmp_path / "baseline.csv"
    optimized_csv = tmp_path / "optimized.csv"
    _write_csv(
        baseline_csv,
        [
            {
                "id": "legacy_001",
                "dataset_source": "maintenance_eval_dataset_v1.jsonl",
                "final_pass": "True",
                "forbidden_claim_pass": "True",
                "refusal_pass": "True",
                "procedure_order_pass": "True",
                "image_pass": "True",
            }
        ],
    )
    _write_csv(
        optimized_csv,
        [
            {
                "id": "legacy_001",
                "dataset_source": "maintenance_eval_dataset_v1.jsonl",
                "final_pass": "True",
                "forbidden_claim_pass": "False",
                "refusal_pass": "True",
                "procedure_order_pass": "True",
                "image_pass": "True",
            }
        ],
    )
    (tmp_path / "baseline_summary.json").write_text(
        json.dumps(_summary(core_numerator=5, fixed_templates=5)), encoding="utf-8"
    )
    (tmp_path / "optimized_summary.json").write_text(
        json.dumps(_summary(core_numerator=6, fixed_templates=4)), encoding="utf-8"
    )

    exit_code = main(["--baseline", str(baseline_csv), "--optimized", str(optimized_csv)])

    assert exit_code == 3
