"""Deterministic baseline-vs-optimized comparator for maintenance eval CSV results.

Supports the stage-10 rollback gate: no case may flip pass->fail, cross-device
scope isolation must not regress, unsupported-completion rate must not
increase, and latency must stay within 120% of baseline.
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

LATENCY_REGRESSION_RATIO = 1.2


def _to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1"):
        return True
    if text in ("false", "0"):
        return False
    return None


def load_result_csv(path: Path) -> dict[str, dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return {row["id"]: row for row in reader}


@dataclass
class CaseComparison:
    case_id: str
    baseline_pass: bool
    optimized_pass: bool

    @property
    def category(self) -> str:
        if self.baseline_pass and not self.optimized_pass:
            return "pass_to_fail"
        if not self.baseline_pass and self.optimized_pass:
            return "fail_to_pass"
        if self.baseline_pass and self.optimized_pass:
            return "both_pass"
        return "both_fail"


@dataclass
class EvaluationArtifacts:
    case_rows: Mapping[str, Mapping[str, Any]]
    turn_rows: Mapping[str, Mapping[str, Any]]
    summary: Mapping[str, Any]


STRICT_SAFETY_FIELDS = (
    "forbidden_claim_pass",
    "refusal_pass",
    "procedure_order_pass",
    "image_pass",
)
NON_DECREASING_METRICS = (
    "scope_isolation_pass_rate",
    "unsupported_completion_free_rate",
    "refusal_integrity_pass_rate",
    "evidence_source_pass_rate",
    "forbidden_claim_pass_rate",
    "forbidden_image_pass_rate",
    "style_proxy_pass_rate",
)
CORE_IMPROVEMENT_METRICS = (
    "final_pass_rate",
    "evidence_nugget_coverage_rate",
    "answer_evidence_alignment_pass_rate",
    "scope_isolation_pass_rate",
    "unsupported_completion_free_rate",
    "partial_answer_correct_rate",
)


def _metric(summary: Mapping[str, Any], name: str) -> Mapping[str, Any] | None:
    metrics = summary.get("metric_counts")
    if not isinstance(metrics, Mapping):
        return None
    value = metrics.get(name)
    return value if isinstance(value, Mapping) else None


def _compare_metric(
    baseline: Mapping[str, Any] | None,
    optimized: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not baseline or not optimized:
        return {"comparison": "not_applicable", "pass": True}
    base_denominator = int(baseline.get("denominator") or 0)
    optimized_denominator = int(optimized.get("denominator") or 0)
    if base_denominator == 0 or optimized_denominator == 0:
        return {"comparison": "not_applicable", "pass": True}
    base_numerator = int(baseline.get("numerator") or 0)
    optimized_numerator = int(optimized.get("numerator") or 0)
    left = optimized_numerator * base_denominator
    right = base_numerator * optimized_denominator
    if left > right:
        comparison = "improved"
    elif left < right:
        comparison = "declined"
    else:
        comparison = "equal"
    return {
        "comparison": comparison,
        "pass": comparison != "declined",
        "baseline": {"numerator": base_numerator, "denominator": base_denominator},
        "optimized": {"numerator": optimized_numerator, "denominator": optimized_denominator},
    }


def _fixed_template_gate(
    baseline: Mapping[str, Any] | None,
    optimized: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not baseline or not optimized:
        return {"comparison": "not_applicable", "pass": True}
    base_denominator = int(baseline.get("denominator") or 0)
    optimized_denominator = int(optimized.get("denominator") or 0)
    if base_denominator == 0 or optimized_denominator == 0:
        return {"comparison": "not_applicable", "pass": True}
    base_numerator = int(baseline.get("numerator") or 0)
    optimized_numerator = int(optimized.get("numerator") or 0)
    if base_numerator == 0:
        passed = optimized_numerator == 0
        comparison = "equal" if passed else "declined"
    elif optimized_numerator * base_denominator < base_numerator * optimized_denominator:
        passed = True
        comparison = "improved"
    else:
        passed = False
        comparison = "not_improved"
    return {
        "comparison": comparison,
        "pass": passed,
        "baseline": {"numerator": base_numerator, "denominator": base_denominator},
        "optimized": {"numerator": optimized_numerator, "denominator": optimized_denominator},
    }


def _strict_improvement_gate(
    baseline_summary: Mapping[str, Any],
    optimized_summary: Mapping[str, Any],
) -> dict[str, Any]:
    comparisons = {
        name: _compare_metric(_metric(baseline_summary, name), _metric(optimized_summary, name))
        for name in CORE_IMPROVEMENT_METRICS
    }
    improved = [name for name, result in comparisons.items() if result["comparison"] == "improved"]
    applicable = [name for name, result in comparisons.items() if result["comparison"] != "not_applicable"]
    return {
        "pass": bool(improved),
        "comparison": "improved" if improved else ("not_applicable" if not applicable else "not_improved"),
        "improved_metrics": improved,
        "metrics": comparisons,
    }


def _original_case_ids(
    rows: Mapping[str, Mapping[str, Any]],
    specialized_sources: set[str],
) -> set[str]:
    return {
        case_id
        for case_id, row in rows.items()
        if str(row.get("dataset_source") or "") not in specialized_sources
    }


def _safety_regressions(
    baseline_rows: Mapping[str, Mapping[str, Any]],
    optimized_rows: Mapping[str, Mapping[str, Any]],
    specialized_sources: set[str],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    missing_in_optimized = sorted(set(baseline_rows) - set(optimized_rows))
    missing_in_baseline = sorted(set(optimized_rows) - set(baseline_rows))
    original_ids = _original_case_ids(baseline_rows, specialized_sources)
    regressions: list[dict[str, Any]] = []
    for case_id in sorted(original_ids & set(optimized_rows)):
        baseline_row = baseline_rows[case_id]
        optimized_row = optimized_rows[case_id]
        for field in STRICT_SAFETY_FIELDS:
            if _to_bool(baseline_row.get(field)) is True and _to_bool(optimized_row.get(field)) is False:
                regressions.append(
                    {
                        "case_id": case_id,
                        "row_level": "case",
                        "field": field,
                        "baseline_value": True,
                        "optimized_value": False,
                    }
                )
    return regressions, missing_in_baseline, missing_in_optimized


def _targeted_margin(gate: Mapping[str, Any]) -> bool:
    baseline = gate.get("baseline")
    optimized = gate.get("optimized")
    if not isinstance(baseline, Mapping) or not isinstance(optimized, Mapping):
        return False
    if baseline.get("denominator") != optimized.get("denominator"):
        return False
    return abs(int(baseline.get("numerator") or 0) - int(optimized.get("numerator") or 0)) <= 1


def _metric_row_field(metric_name: str) -> tuple[str, str] | None:
    mapping = {
        "scope_isolation_pass_rate": ("turn", "evidence_scope_isolation_pass"),
        "unsupported_completion_free_rate": ("turn", "evidence_unsupported_completion_free"),
        "refusal_integrity_pass_rate": ("turn", "evidence_refusal_integrity_pass"),
        "evidence_source_pass_rate": ("turn", "evidence_source_pass"),
        "forbidden_claim_pass_rate": ("case", "forbidden_claim_pass"),
        "forbidden_image_pass_rate": ("case", "forbidden_image_pass"),
        "style_proxy_pass_rate": ("turn", "evidence_style_proxy_pass"),
        "fixed_template_rate": ("turn", "evidence_fixed_template_detected"),
        "answer_evidence_alignment_pass_rate": ("turn", "evidence_answer_alignment_pass"),
        "partial_answer_correct_rate": ("turn", "evidence_partial_answer_correct"),
        "conflict_handling_pass_rate": ("turn", "evidence_conflict_handling_pass"),
        "final_pass_rate": ("case", "final_pass"),
    }
    return mapping.get(metric_name)


def _metric_targeted_manifest(
    metric_name: str,
    baseline: EvaluationArtifacts,
    optimized: EvaluationArtifacts,
) -> list[dict[str, Any]]:
    mapping = _metric_row_field(metric_name)
    if mapping is None:
        return []
    row_level, field = mapping
    baseline_rows = baseline.case_rows if row_level == "case" else baseline.turn_rows
    optimized_rows = optimized.case_rows if row_level == "case" else optimized.turn_rows
    manifest: list[dict[str, Any]] = []
    for item_id in sorted(set(baseline_rows) & set(optimized_rows)):
        base_value = _to_bool(baseline_rows[item_id].get(field))
        optimized_value = _to_bool(optimized_rows[item_id].get(field))
        if metric_name == "fixed_template_rate":
            regressed = base_value is False and optimized_value is True
        else:
            regressed = base_value is True and optimized_value is False
        if regressed:
            manifest.append(
                {
                    "case_id": item_id,
                    "row_level": row_level,
                    "field": field,
                    "baseline_value": base_value,
                    "optimized_value": optimized_value,
                }
            )
    return manifest


def build_strict_gate_report(
    baseline: EvaluationArtifacts,
    optimized: EvaluationArtifacts,
    *,
    specialized_sources: set[str] | None = None,
) -> dict[str, Any]:
    specialized_sources = specialized_sources or {
        "eval_specialised_v1.jsonl",
        "maintenance_quality_v2.jsonl",
    }
    safety_regressions, missing_in_baseline, missing_in_optimized = _safety_regressions(
        baseline.case_rows,
        optimized.case_rows,
        specialized_sources,
    )
    gates: dict[str, Any] = {}
    gates["final_pass_rate_not_lower"] = _compare_metric(
        _metric(baseline.summary, "final_pass_rate"),
        _metric(optimized.summary, "final_pass_rate"),
    )
    for name in NON_DECREASING_METRICS:
        gates[f"{name}_not_lower"] = _compare_metric(
            _metric(baseline.summary, name),
            _metric(optimized.summary, name),
        )
    gates["fixed_template_rate_lower"] = _fixed_template_gate(
        _metric(baseline.summary, "fixed_template_rate"),
        _metric(optimized.summary, "fixed_template_rate"),
    )
    gates["strict_improvement"] = _strict_improvement_gate(
        baseline.summary,
        optimized.summary,
    )
    baseline_latency = int(baseline.summary.get("latency_total_ms") or 0)
    optimized_latency = int(optimized.summary.get("latency_total_ms") or 0)
    baseline_requests = int(baseline.summary.get("request_count") or 0)
    optimized_requests = int(optimized.summary.get("request_count") or 0)
    if baseline_latency <= 0 or baseline_requests <= 0 or optimized_requests <= 0:
        gates["latency_within_120_percent"] = {"pass": True, "comparison": "not_applicable"}
    else:
        latency_pass = optimized_latency * baseline_requests * 5 <= baseline_latency * optimized_requests * 6
        gates["latency_within_120_percent"] = {
            "pass": latency_pass,
            "comparison": "equal_or_lower" if latency_pass else "declined",
            "baseline_total_ms": baseline_latency,
            "optimized_total_ms": optimized_latency,
            "baseline_requests": baseline_requests,
            "optimized_requests": optimized_requests,
        }

    targeted_manifest = list(safety_regressions)
    if not missing_in_optimized:
        final_gate = gates["final_pass_rate_not_lower"]
        if final_gate["pass"] is False and _targeted_margin(final_gate):
            for case_id in sorted(set(baseline.case_rows) & set(optimized.case_rows)):
                if _to_bool(baseline.case_rows[case_id].get("final_pass")) is True and _to_bool(
                    optimized.case_rows[case_id].get("final_pass")
                ) is False:
                    targeted_manifest.append(
                        {
                            "case_id": case_id,
                            "row_level": "case",
                            "field": "final_pass",
                            "baseline_value": True,
                            "optimized_value": False,
                        }
                    )
    for metric_name, gate in gates.items():
        if metric_name in {"strict_improvement", "latency_within_120_percent"}:
            continue
        if gate.get("pass") is False and _targeted_margin(gate):
            gate_metric_name = metric_name.removesuffix("_not_lower")
            if metric_name == "fixed_template_rate_lower":
                gate_metric_name = "fixed_template_rate"
            targeted_manifest.extend(
                _metric_targeted_manifest(gate_metric_name, baseline, optimized)
            )
    unique_manifest: list[dict[str, Any]] = []
    seen_manifest: set[tuple[str, str, str]] = set()
    for item in targeted_manifest:
        key = (str(item["row_level"]), str(item["case_id"]), str(item["field"]))
        if key not in seen_manifest:
            seen_manifest.add(key)
            unique_manifest.append(item)
    targeted_manifest = unique_manifest
    failed_gates = [name for name, gate in gates.items() if gate.get("pass") is False]
    immediate_failures = bool(missing_in_optimized or missing_in_baseline)
    if not gates["latency_within_120_percent"]["pass"]:
        immediate_failures = True
    if not gates["strict_improvement"]["pass"]:
        immediate_failures = True
    for name in failed_gates:
        if name in {"strict_improvement", "latency_within_120_percent"}:
            continue
        if not _targeted_margin(gates[name]):
            immediate_failures = True
        elif name not in {item.get("field") for item in targeted_manifest} and name != "final_pass_rate_not_lower":
            metric_name = name.removesuffix("_not_lower")
            if name == "fixed_template_rate_lower":
                metric_name = "fixed_template_rate"
            if not _metric_targeted_manifest(metric_name, baseline, optimized):
                immediate_failures = True
        elif name == "final_pass_rate_not_lower" and not _metric_targeted_manifest(
            "final_pass_rate", baseline, optimized
        ):
            immediate_failures = True
    if immediate_failures:
        status = "rollback_required"
    elif failed_gates or targeted_manifest:
        status = "targeted_rerun_required"
    else:
        status = "keep"
    return {
        "status": status,
        "exit_code": status_exit_code(status),
        "gates": gates,
        "strict_improvement": bool(gates["strict_improvement"]["pass"]),
        "strict_safety_regressions": safety_regressions,
        "missing_in_baseline": missing_in_baseline,
        "missing_in_optimized": missing_in_optimized,
        "targeted_rerun_manifest": targeted_manifest,
        "failed_gates": failed_gates,
    }


def resolve_targeted_reruns(
    report: Mapping[str, Any],
    rerun_artifacts: Sequence[EvaluationArtifacts],
) -> dict[str, Any]:
    if report.get("status") != "targeted_rerun_required":
        return dict(report)
    manifest = list(report.get("targeted_rerun_manifest") or [])
    if len(rerun_artifacts) < 2:
        return {**report, "rerun_status": "awaiting_two_targeted_runs", "exit_code": 3}
    sustained: list[dict[str, Any]] = []
    for item in manifest:
        failures = 1 if item.get("optimized_value") is not item.get("baseline_value") else 0
        for rerun in rerun_artifacts[:2]:
            rows = rerun.case_rows if item.get("row_level") == "case" else rerun.turn_rows
            row = rows.get(str(item.get("case_id")))
            if row is None:
                failures += 1
                continue
            value = _to_bool(row.get(str(item.get("field"))))
            if value is not item.get("baseline_value"):
                failures += 1
        if failures >= 2:
            sustained.append({**item, "failure_count_of_three": failures})
    if sustained:
        return {
            **report,
            "status": "rollback_required",
            "exit_code": 2,
            "sustained_regressions": sustained,
        }
    return {
        **report,
        "status": "keep",
        "exit_code": 0,
        "sustained_regressions": [],
        "rerun_status": "recovered_by_two_of_three",
    }


def status_exit_code(status: str) -> int:
    return {"keep": 0, "targeted_rerun_required": 3, "rollback_required": 2}.get(status, 1)


def load_evaluation_artifacts(
    case_path: Path,
    *,
    turns_path: Path | None = None,
    summary_path: Path | None = None,
) -> EvaluationArtifacts:
    turns_path = turns_path or case_path.with_name(f"{case_path.stem}_turns.csv")
    summary_path = summary_path or case_path.with_name(f"{case_path.stem}_summary.json")
    case_rows = load_result_csv(case_path)
    turn_rows = load_result_csv(turns_path) if turns_path.is_file() else {}
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
    return EvaluationArtifacts(case_rows=case_rows, turn_rows=turn_rows, summary=summary)


def compare_results(
    baseline: Mapping[str, Mapping[str, Any]],
    optimized: Mapping[str, Mapping[str, Any]],
) -> tuple[list[CaseComparison], list[str], list[str]]:
    common_ids = sorted(set(baseline) & set(optimized))
    missing_in_optimized = sorted(set(baseline) - set(optimized))
    missing_in_baseline = sorted(set(optimized) - set(baseline))

    comparisons = [
        CaseComparison(
            case_id=case_id,
            baseline_pass=bool(_to_bool(baseline[case_id].get("final_pass"))),
            optimized_pass=bool(_to_bool(optimized[case_id].get("final_pass"))),
        )
        for case_id in common_ids
    ]
    return comparisons, missing_in_baseline, missing_in_optimized


def _find_evidence_regressions(
    comparisons: list[CaseComparison],
    baseline: Mapping[str, Mapping[str, Any]],
    optimized: Mapping[str, Mapping[str, Any]],
    field_name: str,
) -> list[str]:
    regressions: list[str] = []
    for comparison in comparisons:
        case_id = comparison.case_id
        baseline_row = baseline[case_id]
        optimized_row = optimized[case_id]
        if not _to_bool(baseline_row.get("evidence_score_available")):
            continue
        if not _to_bool(optimized_row.get("evidence_score_available")):
            continue
        if _to_bool(baseline_row.get(field_name)) is True and _to_bool(optimized_row.get(field_name)) is False:
            regressions.append(case_id)
    return regressions


def _avg_latency(rows: Mapping[str, Mapping[str, Any]], case_ids: list[str]) -> float:
    values: list[float] = []
    for case_id in case_ids:
        raw = rows[case_id].get("latency_ms")
        try:
            values.append(float(raw))
        except (TypeError, ValueError):
            continue
    return sum(values) / len(values) if values else 0.0


def build_report(
    comparisons: list[CaseComparison],
    baseline: Mapping[str, Mapping[str, Any]],
    optimized: Mapping[str, Mapping[str, Any]],
    missing_in_baseline: list[str],
    missing_in_optimized: list[str],
) -> dict[str, Any]:
    pass_to_fail = [c.case_id for c in comparisons if c.category == "pass_to_fail"]
    fail_to_pass = [c.case_id for c in comparisons if c.category == "fail_to_pass"]
    both_pass = [c.case_id for c in comparisons if c.category == "both_pass"]
    both_fail = [c.case_id for c in comparisons if c.category == "both_fail"]

    scope_regressions = _find_evidence_regressions(
        comparisons, baseline, optimized, "evidence_scope_isolation_pass"
    )
    unsupported_regressions = _find_evidence_regressions(
        comparisons, baseline, optimized, "evidence_unsupported_completion_free"
    )

    case_ids = [c.case_id for c in comparisons]
    baseline_avg_latency = _avg_latency(baseline, case_ids)
    optimized_avg_latency = _avg_latency(optimized, case_ids)
    latency_ratio = optimized_avg_latency / baseline_avg_latency if baseline_avg_latency else 0.0
    latency_regression = bool(baseline_avg_latency) and latency_ratio > LATENCY_REGRESSION_RATIO

    return {
        "regression_detected": bool(pass_to_fail),
        "scope_regression": bool(scope_regressions),
        "unsupported_regression": bool(unsupported_regressions),
        "latency_regression": latency_regression,
        "summary": {
            "pass_to_fail": len(pass_to_fail),
            "fail_to_pass": len(fail_to_pass),
            "both_pass": len(both_pass),
            "both_fail": len(both_fail),
            "total_aligned": len(comparisons),
        },
        "regressions": pass_to_fail,
        "improvements": fail_to_pass,
        "scope_regression_case_ids": scope_regressions,
        "unsupported_regression_case_ids": unsupported_regressions,
        "baseline_avg_latency_ms": round(baseline_avg_latency, 2),
        "optimized_avg_latency_ms": round(optimized_avg_latency, 2),
        "latency_ratio": round(latency_ratio, 4),
        "missing_in_baseline": missing_in_baseline,
        "missing_in_optimized": missing_in_optimized,
    }


def compare_and_build_report(baseline_path: Path, optimized_path: Path) -> dict[str, Any]:
    baseline = load_result_csv(baseline_path)
    optimized = load_result_csv(optimized_path)
    comparisons, missing_in_baseline, missing_in_optimized = compare_results(baseline, optimized)
    return build_report(comparisons, baseline, optimized, missing_in_baseline, missing_in_optimized)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare baseline vs optimized maintenance eval CSV results.")
    parser.add_argument("--baseline", required=True, help="Baseline result CSV path.")
    parser.add_argument("--optimized", required=True, help="Optimized result CSV path.")
    parser.add_argument("--baseline-turns", default="", help="Optional baseline turn CSV path.")
    parser.add_argument("--optimized-turns", default="", help="Optional optimized turn CSV path.")
    parser.add_argument("--baseline-summary", default="", help="Optional baseline summary JSON path.")
    parser.add_argument("--optimized-summary", default="", help="Optional optimized summary JSON path.")
    parser.add_argument(
        "--rerun",
        action="append",
        default=[],
        help="Additional targeted optimized case CSV; repeat twice for 2/3 majority.",
    )
    parser.add_argument(
        "--specialized-source",
        action="append",
        default=[],
        help="Dataset source treated as the new specialized set; may be repeated.",
    )
    parser.add_argument("--out", default="", help="Optional path to write the JSON report.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    baseline_path = Path(args.baseline)
    optimized_path = Path(args.optimized)
    baseline_summary_path = Path(args.baseline_summary) if args.baseline_summary else baseline_path.with_name(
        f"{baseline_path.stem}_summary.json"
    )
    optimized_summary_path = Path(args.optimized_summary) if args.optimized_summary else optimized_path.with_name(
        f"{optimized_path.stem}_summary.json"
    )
    strict_ready = baseline_summary_path.is_file() and optimized_summary_path.is_file()
    if strict_ready:
        baseline = load_evaluation_artifacts(
            baseline_path,
            turns_path=Path(args.baseline_turns) if args.baseline_turns else None,
            summary_path=baseline_summary_path,
        )
        optimized = load_evaluation_artifacts(
            optimized_path,
            turns_path=Path(args.optimized_turns) if args.optimized_turns else None,
            summary_path=optimized_summary_path,
        )
        report = build_strict_gate_report(
            baseline,
            optimized,
            specialized_sources=set(args.specialized_source) or None,
        )
        if args.rerun:
            reruns = [load_evaluation_artifacts(Path(path)) for path in args.rerun]
            report = resolve_targeted_reruns(report, reruns)
        status = report["status"]
        print(f"{status}: exit_code={report['exit_code']}")
    else:
        report = compare_and_build_report(baseline_path, optimized_path)
        status = "rollback_required" if report["regression_detected"] else "keep"
        report = {**report, "status": status, "exit_code": status_exit_code(status)}

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if "regression_detected" in report:
        status_text = "REGRESSION DETECTED" if report["regression_detected"] else "NO REGRESSION"
        print(
            f"{status_text}: {report['summary']['pass_to_fail']} pass->fail, "
            f"{report['summary']['fail_to_pass']} fail->pass"
        )
    return int(report["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
