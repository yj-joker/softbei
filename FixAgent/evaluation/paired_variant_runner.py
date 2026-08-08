"""Deterministic three-arm runner with parallel case/repetition units."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping, Sequence

from evaluation.maintenance_eval_schema import MaintenanceEvalCase


VARIANTS = ("no_graph", "graph_shadow", "graph_full")
RequestRunner = Callable[[MaintenanceEvalCase, str, str, int], Mapping[str, Any]]


def _route_contract_from_result(result: Mapping[str, Any]) -> dict[str, Any]:
    for trace in result.get("trace_rows") or []:
        if not isinstance(trace, Mapping):
            continue
        metadata = trace.get("metadata")
        if not isinstance(metadata, Mapping):
            continue
        intent_decision = metadata.get("intent_decision")
        query_contract = metadata.get("query_contract")
        if isinstance(intent_decision, Mapping) and isinstance(query_contract, Mapping):
            return {
                "intent_decision": dict(intent_decision),
                "query_contract": dict(query_contract),
            }
    return {}


@dataclass(frozen=True)
class PairedVariantResult:
    rows: list[dict[str, Any]]
    request_order: list[dict[str, Any]]
    concurrency: int


def run_paired_variants(
    *,
    cases: Sequence[MaintenanceEvalCase],
    endpoints: Mapping[str, str],
    repetitions: int,
    concurrency: int,
    request_runner: RequestRunner,
) -> PairedVariantResult:
    if repetitions < 1 or concurrency < 1:
        raise ValueError("repetitions and concurrency must be positive")
    missing = [variant for variant in VARIANTS if not endpoints.get(variant)]
    if missing:
        raise ValueError(f"missing endpoints: {', '.join(missing)}")
    units = [
        (repetition, case_index, case)
        for repetition in range(1, repetitions + 1)
        for case_index, case in enumerate(cases)
    ]

    def run_unit(unit):
        repetition, case_index, case = unit
        unit_rows: list[dict[str, Any]] = []
        order: list[dict[str, Any]] = []
        frozen_route_contract: dict[str, Any] = {}
        unit_index = (repetition - 1) * len(cases) + case_index
        for variant_index, variant in enumerate(VARIANTS):
            sequence = unit_index * len(VARIANTS) + variant_index + 1
            request_case = case
            if frozen_route_contract:
                request_case = replace(
                    case,
                    candidate_metadata={
                        **dict(case.candidate_metadata or {}),
                        "_paired_route_contract": frozen_route_contract,
                    },
                )
            result = dict(request_runner(request_case, variant, endpoints[variant], sequence))
            if variant == "no_graph":
                frozen_route_contract = _route_contract_from_result(result)
            audit = {
                "case_id": case.case_id,
                "repetition": repetition,
                "variant": variant,
                "request_sequence": sequence,
                "route_contract_frozen": bool(
                    variant != "no_graph" and frozen_route_contract
                ),
            }
            result.update(audit)
            unit_rows.append(result)
            order.append(dict(audit))
        return unit_rows, order

    if len(units) <= 1 or concurrency == 1:
        results = [run_unit(unit) for unit in units]
    else:
        with ThreadPoolExecutor(max_workers=min(concurrency, len(units))) as executor:
            results = list(executor.map(run_unit, units))
    rows = [row for unit_rows, _ in results for row in unit_rows]
    order = [row for _, unit_order in results for row in unit_order]
    return PairedVariantResult(rows=rows, request_order=order, concurrency=concurrency)


__all__ = ["PairedVariantResult", "VARIANTS", "run_paired_variants"]
