"""Three-arm GraphRAG development and regression evaluation runner.

The runner validates the process-owned RAG variant exposed by each server,
keeps variants serial inside each case/repetition unit, and parallelizes
independent units with deterministic output ordering.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv

from evaluation.maintenance_eval_cli import (
    aggregate_case_rows,
    build_run_manifest,
    run_cases,
    summarize_results,
    write_rows,
    write_summary,
    write_trace_rows,
)
from evaluation.maintenance_eval_comparator import build_paired_ablation_report
from evaluation.maintenance_eval_schema import MaintenanceEvalCase, read_jsonl_datasets
from evaluation.paired_variant_runner import (
    VARIANTS,
    run_paired_variants,
)


COMPARISONS = (
    ("graph_shadow_minus_no_graph", "no_graph", "graph_shadow"),
    ("graph_full_minus_graph_shadow", "graph_shadow", "graph_full"),
    ("graph_full_minus_no_graph", "no_graph", "graph_full"),
)


def _route_contract_consistency_check(
    turn_rows_by_variant: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    grouped: dict[tuple[str, int, int], dict[str, str]] = {}
    for variant, rows in turn_rows_by_variant.items():
        for row in rows:
            signature = str(row.get("route_contract_signature") or "").strip()
            if not signature:
                continue
            key = (
                str(row.get("original_case_id") or row.get("case_id") or ""),
                int(row.get("repetition") or 1),
                int(row.get("turn_index") or 1),
            )
            grouped.setdefault(key, {})[variant] = signature
    if not grouped:
        return {
            "name": "paired_route_contract_consistency",
            "status": "not_applicable",
            "passed": True,
            "evaluated_pair_count": 0,
            "violations": [],
        }
    violations = []
    for key, signatures in grouped.items():
        missing = [variant for variant in VARIANTS if variant not in signatures]
        if missing or len(set(signatures.values())) > 1:
            violations.append({
                "original_case_id": key[0],
                "repetition": key[1],
                "turn_index": key[2],
                "missing_variants": missing,
                "signatures": signatures,
            })
    return {
        "name": "paired_route_contract_consistency",
        "status": "passed" if not violations else "failed",
        "passed": not violations,
        "evaluated_pair_count": len(grouped),
        "violations": violations,
    }


@dataclass
class PairedRunResult:
    variant_rows: dict[str, list[dict[str, Any]]]
    variant_cases: dict[str, list[MaintenanceEvalCase]]
    variant_traces: dict[str, list[dict[str, Any]]]
    request_order: list[dict[str, Any]]


def health_endpoint_for(chat_endpoint: str) -> str:
    parsed = urlsplit(chat_endpoint)
    return urlunsplit((parsed.scheme, parsed.netloc, "/health", "", ""))


def validate_endpoint_variant(
    chat_endpoint: str,
    expected_variant: str,
    *,
    timeout: int = 5,
    health_endpoint: str = "",
) -> dict[str, Any]:
    endpoint = health_endpoint or health_endpoint_for(chat_endpoint)
    request = urllib.request.Request(endpoint, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"health check failed for {endpoint}: {exc}") from exc
    reported = str(payload.get("rag_variant") or "")
    expected_canonical = "graph_full" if expected_variant == "graph" else expected_variant
    if reported != expected_canonical:
        raise RuntimeError(
            f"endpoint variant mismatch: expected {expected_canonical}, reported {reported or '<missing>'}"
        )
    return payload


def run_paired_cases(
    cases: Sequence[MaintenanceEvalCase],
    *,
    endpoints: Mapping[str, str],
    repetitions: int,
    timeout: int,
    run_id: str,
    concurrency: int = 4,
    default_device_type: str = "",
    default_document_id: str = "",
    api_token: str = "",
) -> PairedRunResult:
    if repetitions < 1:
        raise ValueError("repetitions must be at least 1")
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    missing = [variant for variant in VARIANTS if not endpoints.get(variant)]
    if missing:
        raise ValueError(f"missing endpoint for: {', '.join(missing)}")

    case_list = list(cases)
    if not case_list:
        return PairedRunResult(
            variant_rows={variant: [] for variant in VARIANTS},
            variant_cases={variant: [] for variant in VARIANTS},
            variant_traces={variant: [] for variant in VARIANTS},
            request_order=[],
        )

    def request_runner(
        case: MaintenanceEvalCase,
        variant: str,
        endpoint: str,
        request_sequence: int,
    ) -> Mapping[str, Any]:
        unit_index = (request_sequence - 1) // len(VARIANTS)
        repetition = unit_index // len(case_list) + 1
        case_index = unit_index % len(case_list) + 1
        evaluation_id = f"{case.case_id}::r{repetition}"
        variant_case = replace(case, case_id=evaluation_id)
        isolated_run_id = (
            f"{run_id}-{variant}-r{repetition}-q{case_index}-s{request_sequence}"
        )
        local_trace: list[dict[str, Any]] = []
        rows = run_cases(
            [variant_case],
            mode="api",
            endpoint=endpoint,
            timeout=timeout,
            run_id=isolated_run_id,
            default_device_type=default_device_type,
            default_document_id=default_document_id,
            api_token=api_token,
            trace_rows=local_trace,
            concurrency=1,
        )
        return {
            "original_case_id": case.case_id,
            "evaluation_case_id": evaluation_id,
            "variant_case": variant_case,
            "turn_rows": rows,
            "trace_rows": local_trace,
        }

    paired = run_paired_variants(
        cases=case_list,
        endpoints=endpoints,
        repetitions=repetitions,
        concurrency=concurrency,
        request_runner=request_runner,
    )
    variant_rows = {variant: [] for variant in VARIANTS}
    variant_cases = {variant: [] for variant in VARIANTS}
    variant_traces = {variant: [] for variant in VARIANTS}
    for result_row in paired.rows:
        variant = str(result_row["variant"])
        audit = {
            "original_case_id": str(result_row["original_case_id"]),
            "repetition": int(result_row["repetition"]),
            "pair_position": VARIANTS.index(variant) + 1,
            "request_sequence": int(result_row["request_sequence"]),
            "variant_label": variant,
            "route_contract_frozen": bool(result_row.get("route_contract_frozen")),
        }
        for row in result_row.get("turn_rows") or []:
            row.update(audit)
            variant_rows[variant].append(row)
        for trace in result_row.get("trace_rows") or []:
            trace.update(audit)
            variant_traces[variant].append(trace)
        variant_case = result_row.get("variant_case")
        if isinstance(variant_case, MaintenanceEvalCase):
            variant_cases[variant].append(variant_case)

    request_order = [
        {
            "original_case_id": str(item["case_id"]),
            "evaluation_case_id": f"{item['case_id']}::r{item['repetition']}",
            "repetition": int(item["repetition"]),
            "pair_position": VARIANTS.index(str(item["variant"])) + 1,
            "request_sequence": int(item["request_sequence"]),
            "variant_label": str(item["variant"]),
            "variant": str(item["variant"]),
        }
        for item in paired.request_order
    ]

    return PairedRunResult(
        variant_rows=variant_rows,
        variant_cases=variant_cases,
        variant_traces=variant_traces,
        request_order=request_order,
    )


def _row_number(row: Mapping[str, Any], field: str) -> int:
    try:
        return int(float(row.get(field) or 0))
    except (TypeError, ValueError):
        return 0


def _row_flag(row: Mapping[str, Any], field: str) -> bool:
    value = row.get(field)
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def _row_items(row: Mapping[str, Any], field: str) -> list[str]:
    value = row.get(field)
    if isinstance(value, (list, tuple, set, frozenset)):
        raw = value
    else:
        raw = str(value or "").split(";")
    return [str(item).strip() for item in raw if str(item).strip()]


def _violation(row: Mapping[str, Any], field: str, actual: Any) -> dict[str, Any]:
    return {
        "case_id": str(row.get("original_case_id") or row.get("case_id") or ""),
        "repetition": _row_number(row, "repetition"),
        "turn_index": _row_number(row, "turn_index"),
        "field": field,
        "actual": actual,
    }


def _zero_activity_check(
    name: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    numeric_fields: Sequence[str],
    list_fields: Sequence[str],
    flag_fields: Sequence[str],
) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
    totals = {field: sum(_row_number(row, field) for row in rows) for field in numeric_fields}
    totals.update({field: sum(len(_row_items(row, field)) for row in rows) for field in list_fields})
    totals.update({field: sum(_row_flag(row, field) for row in rows) for field in flag_fields})
    for row in rows:
        for field in numeric_fields:
            actual = _row_number(row, field)
            if actual:
                violations.append(_violation(row, field, actual))
        for field in list_fields:
            actual = _row_items(row, field)
            if actual:
                violations.append(_violation(row, field, actual))
        for field in flag_fields:
            actual = _row_flag(row, field)
            if actual:
                violations.append(_violation(row, field, actual))
    passed = not violations
    return {
        "name": name,
        "status": "passed" if passed else "failed",
        "passed": passed,
        "evaluated_turn_count": len(rows),
        "totals": totals,
        "violations": violations,
    }


def evaluate_mechanism_gate(
    turn_rows_by_variant: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    no_graph = _zero_activity_check(
        "no_graph_zero_activity",
        turn_rows_by_variant.get("no_graph") or [],
        numeric_fields=(
            "graph_candidate_query_count",
            "graph_candidate_count",
            "graph_qualified_count",
            "graph_routing_only_count",
            "graph_rejected_count",
            "graph_evidence_used_count",
            "graph_tool_call_count",
        ),
        list_fields=(
            "graph_evidence_ids",
            "graph_evidence_used_ids",
            "graph_tools_used",
        ),
        flag_fields=("graph_review_enabled",),
    )
    graph_shadow = _zero_activity_check(
        "graph_shadow_isolation",
        turn_rows_by_variant.get("graph_shadow") or [],
        numeric_fields=("graph_evidence_used_count", "graph_tool_call_count"),
        list_fields=("graph_evidence_used_ids", "graph_tools_used"),
        flag_fields=("graph_review_enabled",),
    )
    required_rows = [
        row
        for row in turn_rows_by_variant.get("graph_full") or []
        if str(row.get("graph_dependency") or "").strip().lower() == "required"
    ]
    required_totals = {
        field: sum(_row_number(row, field) for row in required_rows)
        for field in (
            "graph_candidate_count",
            "graph_qualified_count",
            "graph_evidence_used_count",
        )
    }
    if not required_rows:
        graph_full = {
            "name": "graph_full_required_chain",
            "status": "not_applicable",
            "passed": True,
            "evaluated_turn_count": 0,
            "totals": required_totals,
            "violations": [],
            "failed_turn_count": 0,
        }
    else:
        violations = []
        for row in required_rows:
            missing_fields = [
                field
                for field in (
                    "graph_candidate_count",
                    "graph_qualified_count",
                    "graph_evidence_used_count",
                )
                if _row_number(row, field) <= 0
            ]
            raw_bindings = row.get("claim_evidence_bindings") or []
            if isinstance(raw_bindings, str):
                try:
                    raw_bindings = json.loads(raw_bindings)
                except json.JSONDecodeError:
                    raw_bindings = []
            if isinstance(raw_bindings, Mapping):
                raw_bindings = [raw_bindings]
            has_graph_binding = any(
                isinstance(binding, Mapping)
                and binding.get("emitted") is not False
                and any(
                    str(evidence_id).startswith("graph:")
                    for evidence_id in binding.get("evidence_ids") or []
                )
                for binding in raw_bindings if isinstance(raw_bindings, Sequence)
            )
            if not has_graph_binding:
                missing_fields.append("claim_evidence_bindings")
            if missing_fields:
                violations.append({
                    "case_id": row.get("case_id"),
                    "original_case_id": row.get("original_case_id"),
                    "repetition": row.get("repetition"),
                    "turn_index": row.get("turn_index"),
                    "missing_fields": missing_fields,
                })
        graph_full = {
            "name": "graph_full_required_chain",
            "status": "passed" if not violations else "failed",
            "passed": not violations,
            "evaluated_turn_count": len(required_rows),
            "totals": required_totals,
            "violations": violations,
            "failed_turn_count": len(violations),
        }
    checks = {
        "no_graph_zero_activity": no_graph,
        "graph_shadow_isolation": graph_shadow,
        "graph_full_required_chain": graph_full,
        "paired_route_contract_consistency": _route_contract_consistency_check(
            turn_rows_by_variant
        ),
    }
    failed = [name for name, check in checks.items() if not check["passed"]]
    return {
        "passed": not failed,
        "exit_code": 0 if not failed else 2,
        "failed_checks": failed,
        "checks": checks,
    }


def _label_comparison_report(
    report: dict[str, Any],
    *,
    baseline_variant: str,
    comparison_variant: str,
) -> dict[str, Any]:
    """Add unambiguous arm labels while retaining legacy comparator fields."""
    report["baseline_variant"] = baseline_variant
    report["comparison_variant"] = comparison_variant
    for value in report.values():
        if not isinstance(value, dict):
            continue
        if "no_graph_rate" in value and "graph_rate" in value:
            value["baseline_rate"] = value["no_graph_rate"]
            value["comparison_rate"] = value["graph_rate"]
        mcnemar = value.get("mcnemar_exact")
        if isinstance(mcnemar, dict):
            mcnemar["baseline_only_success"] = mcnemar.get("no_graph_only_success", 0)
            mcnemar["comparison_only_success"] = mcnemar.get("graph_only_success", 0)
    latency = report.get("latency_ms")
    if isinstance(latency, dict):
        latency["by_variant"] = {
            baseline_variant: latency.get("no_graph", {}),
            comparison_variant: latency.get("graph", {}),
        }
    for key in ("token_usage", "cost"):
        metric = report.get(key)
        if isinstance(metric, dict) and metric.get("available"):
            metric["baseline_mean"] = metric.get("no_graph_mean")
            metric["comparison_mean"] = metric.get("graph_mean")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run three-arm GraphRAG evaluation.")
    parser.add_argument("--dataset", action="append", required=True, help="JSONL dataset; repeatable.")
    parser.add_argument("--no-graph-endpoint", default="http://127.0.0.1:8001/ai/chat")
    parser.add_argument("--graph-shadow-endpoint", default="http://127.0.0.1:8002/ai/chat")
    parser.add_argument("--graph-full-endpoint", default="http://127.0.0.1:8003/ai/chat")
    parser.add_argument(
        "--graph-endpoint",
        default="",
        help="Legacy alias overriding --graph-full-endpoint.",
    )
    parser.add_argument("--no-graph-health", default="")
    parser.add_argument("--graph-shadow-health", default="")
    parser.add_argument("--graph-full-health", default="")
    parser.add_argument("--graph-health", default="", help="Legacy alias for --graph-full-health.")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--health-timeout", type=int, default=5)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Maximum concurrent case/repetition pairs; variants within a pair stay serial.",
    )
    parser.add_argument("--default-device-type", default="")
    parser.add_argument("--default-document-id", default="")
    parser.add_argument("--out-dir", default="evaluation/results/kg_ablation")
    parser.add_argument("--run-name", default="")
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument("--bootstrap-seed", type=int, default=20260806)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    dataset_paths = [Path(value) for value in args.dataset]
    cases = read_jsonl_datasets(dataset_paths)
    if args.limit > 0:
        cases = cases[: args.limit]
    endpoints = {
        "no_graph": args.no_graph_endpoint,
        "graph_shadow": args.graph_shadow_endpoint,
        "graph_full": args.graph_endpoint or args.graph_full_endpoint,
    }
    health = {
        "no_graph": validate_endpoint_variant(
            endpoints["no_graph"],
            "no_graph",
            timeout=args.health_timeout,
            health_endpoint=args.no_graph_health,
        ),
        "graph_shadow": validate_endpoint_variant(
            endpoints["graph_shadow"],
            "graph_shadow",
            timeout=args.health_timeout,
            health_endpoint=args.graph_shadow_health,
        ),
        "graph_full": validate_endpoint_variant(
            endpoints["graph_full"],
            "graph_full",
            timeout=args.health_timeout,
            health_endpoint=args.graph_health or args.graph_full_health,
        ),
    }
    run_id = uuid.uuid4().hex
    started_at = datetime.now(timezone.utc).isoformat()
    run_name = args.run_name or f"kg_ablation_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    result = run_paired_cases(
        cases,
        endpoints=endpoints,
        repetitions=args.repetitions,
        timeout=args.timeout,
        run_id=run_id,
        concurrency=args.concurrency,
        default_device_type=args.default_device_type,
        default_document_id=args.default_document_id,
        api_token=os.environ.get("MAINTENANCE_EVAL_API_TOKEN") or os.environ.get("API_TOKEN", ""),
    )

    out_dir = Path(args.out_dir)
    summaries: dict[str, Any] = {}
    case_rows_by_variant: dict[str, list[dict[str, Any]]] = {}
    for variant in VARIANTS:
        basename = f"{run_name}_{variant}"
        case_rows = aggregate_case_rows(result.variant_cases[variant], result.variant_rows[variant])
        case_rows_by_variant[variant] = case_rows
        summary = summarize_results(case_rows, result.variant_rows[variant])
        summary.update({"rag_variant": variant, "repetitions": args.repetitions})
        manifest = build_run_manifest(
            run_id=run_id,
            started_at=started_at,
            dataset_paths=dataset_paths,
            cases=result.variant_cases[variant],
            turn_rows=result.variant_rows[variant],
            mode="api",
            endpoint=endpoints[variant],
            timeout=args.timeout,
            default_device_type=args.default_device_type,
            default_document_id=args.default_document_id,
        )
        manifest.update(
            {
                "paired_ablation": True,
                "expected_rag_variant": variant,
                "health": health[variant],
                "repetitions": args.repetitions,
                "concurrency": args.concurrency,
                "request_order_file": f"{run_name}_request_order.jsonl",
            }
        )
        write_rows(out_dir / f"{basename}.csv", case_rows)
        write_rows(out_dir / f"{basename}_turns.csv", result.variant_rows[variant])
        write_trace_rows(out_dir / f"{basename}_trace.jsonl", result.variant_traces[variant])
        write_summary(out_dir / f"{basename}_summary.json", summary)
        write_summary(out_dir / f"{basename}_run.json", manifest)
        summaries[variant] = summary

    write_trace_rows(out_dir / f"{run_name}_request_order.jsonl", result.request_order)
    for offset, (comparison_name, left_variant, right_variant) in enumerate(COMPARISONS):
        comparison = build_paired_ablation_report(
            case_rows_by_variant[left_variant],
            case_rows_by_variant[right_variant],
            bootstrap_samples=args.bootstrap_samples,
            seed=args.bootstrap_seed + offset * 10,
        )
        _label_comparison_report(
            comparison,
            baseline_variant=left_variant,
            comparison_variant=right_variant,
        )
        comparison.update(
            {
                "comparison": comparison_name,
                "run_name": run_name,
                "dataset_role": "development_or_regression_unless_separately_frozen",
                "experiment_labels": {
                    "baseline": left_variant,
                    "comparison": right_variant,
                },
                "concurrency": args.concurrency,
            }
        )
        write_summary(
            out_dir / f"{run_name}_{comparison_name}_comparison.json",
            comparison,
        )

    mechanism_gate = evaluate_mechanism_gate(result.variant_rows)
    mechanism_gate.update({
        "run_name": run_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })
    write_summary(out_dir / f"{run_name}_mechanism_gate.json", mechanism_gate)
    print(json.dumps({"summaries": summaries, "mechanism_gate": mechanism_gate}, ensure_ascii=False, indent=2))
    return int(mechanism_gate["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
