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
from typing import Any, Mapping

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
    with path.open("r", encoding="utf-8", newline="") as handle:
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
    parser.add_argument("--out", default="", help="Optional path to write the JSON report.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = compare_and_build_report(Path(args.baseline), Path(args.optimized))

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    status = "REGRESSION DETECTED" if report["regression_detected"] else "NO REGRESSION"
    print(f"{status}: {report['summary']['pass_to_fail']} pass->fail, {report['summary']['fail_to_pass']} fail->pass")
    print(f"  Scope regression: {'YES' if report['scope_regression'] else 'NO'}")
    print(f"  Unsupported regression: {'YES' if report['unsupported_regression'] else 'NO'}")
    print(f"  Latency regression: {'YES' if report['latency_regression'] else 'NO'} (ratio={report['latency_ratio']})")
    return 1 if (
        report["regression_detected"]
        or report["scope_regression"]
        or report["unsupported_regression"]
        or report["latency_regression"]
    ) else 0


if __name__ == "__main__":
    raise SystemExit(main())
