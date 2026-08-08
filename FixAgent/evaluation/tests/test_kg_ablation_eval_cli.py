import io
import json
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from evaluation import kg_ablation_eval_cli as ablation
from evaluation.maintenance_eval_schema import MaintenanceEvalCase


class _Response:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._body


def test_validate_endpoint_variant_accepts_expected_health_payload(monkeypatch) -> None:
    captured = []

    def fake_urlopen(request, timeout):
        captured.append((request.full_url, timeout))
        return _Response({"status": "degraded", "rag_variant": "no_graph"})

    monkeypatch.setattr(ablation.urllib.request, "urlopen", fake_urlopen)

    payload = ablation.validate_endpoint_variant(
        "http://127.0.0.1:8001/ai/chat", "no_graph", timeout=3
    )

    assert payload["rag_variant"] == "no_graph"
    assert captured == [("http://127.0.0.1:8001/health", 3)]


def test_validate_endpoint_variant_rejects_crossed_ports(monkeypatch) -> None:
    monkeypatch.setattr(
        ablation.urllib.request,
        "urlopen",
        lambda request, timeout: _Response({"status": "ok", "rag_variant": "graph_full"}),
    )

    with pytest.raises(RuntimeError, match="expected no_graph.*reported graph_full"):
        ablation.validate_endpoint_variant(
            "http://127.0.0.1:8001/ai/chat", "no_graph", timeout=3
        )


def test_paired_runner_uses_three_arm_order_and_independent_run_ids(monkeypatch) -> None:
    calls = []

    def fake_run_cases(cases, **kwargs):
        case = cases[0]
        calls.append((case.case_id, kwargs["endpoint"], kwargs["run_id"]))
        variant = {
            "8001": "no_graph",
            "8002": "graph_shadow",
            "8003": "graph_full",
        }[kwargs["endpoint"].rsplit(":", 1)[-1].split("/", 1)[0]]
        return [
            {
                "id": case.case_id,
                "case_id": case.case_id,
                "rag_variant": variant,
                "final_pass": True,
                "latency_ms": 1,
            }
        ]

    monkeypatch.setattr(ablation, "run_cases", fake_run_cases)
    cases = [
        MaintenanceEvalCase(case_id="case-a", query="A"),
        MaintenanceEvalCase(case_id="case-b", query="B"),
    ]

    result = ablation.run_paired_cases(
        cases,
        endpoints={
            "no_graph": "http://127.0.0.1:8001/ai/chat",
            "graph_shadow": "http://127.0.0.1:8002/ai/chat",
            "graph_full": "http://127.0.0.1:8003/ai/chat",
        },
        repetitions=2,
        timeout=5,
        run_id="paired",
    )

    assert [item["variant"] for item in result.request_order] == [
        "no_graph",
        "graph_shadow",
        "graph_full",
        "no_graph",
        "graph_shadow",
        "graph_full",
        "no_graph",
        "graph_shadow",
        "graph_full",
        "no_graph",
        "graph_shadow",
        "graph_full",
    ]
    assert len({call[2] for call in calls}) == 12
    assert len(result.variant_rows["no_graph"]) == 4
    assert len(result.variant_rows["graph_shadow"]) == 4
    assert len(result.variant_rows["graph_full"]) == 4
    assert result.variant_rows["graph_full"][0]["original_case_id"] == "case-a"
    assert result.variant_rows["graph_full"][0]["repetition"] == 1
    assert result.variant_rows["graph_full"][0]["request_sequence"] == 3


def test_paired_runner_parallelizes_units_but_serializes_each_variant_pair(monkeypatch) -> None:
    state_lock = threading.Lock()
    active_calls = 0
    peak_active_calls = 0
    active_units: set[str] = set()
    overlapping_pair = False

    def fake_run_cases(cases, **kwargs):
        nonlocal active_calls, peak_active_calls, overlapping_pair
        case = cases[0]
        with state_lock:
            if case.case_id in active_units:
                overlapping_pair = True
            active_units.add(case.case_id)
            active_calls += 1
            peak_active_calls = max(peak_active_calls, active_calls)
        time.sleep(0.03)
        with state_lock:
            active_calls -= 1
            active_units.remove(case.case_id)
        variant = {
            "8001": "no_graph",
            "8002": "graph_shadow",
            "8003": "graph_full",
        }[kwargs["endpoint"].rsplit(":", 1)[-1].split("/", 1)[0]]
        return [
            {
                "id": case.case_id,
                "case_id": case.case_id,
                "rag_variant": variant,
                "final_pass": True,
                "latency_ms": 1,
            }
        ]

    monkeypatch.setattr(ablation, "run_cases", fake_run_cases)
    cases = [
        MaintenanceEvalCase(case_id="case-a", query="A"),
        MaintenanceEvalCase(case_id="case-b", query="B"),
    ]

    result = ablation.run_paired_cases(
        cases,
        endpoints={
            "no_graph": "http://127.0.0.1:8001/ai/chat",
            "graph_shadow": "http://127.0.0.1:8002/ai/chat",
            "graph_full": "http://127.0.0.1:8003/ai/chat",
        },
        repetitions=2,
        timeout=5,
        run_id="paired",
        concurrency=2,
    )

    assert 1 < peak_active_calls <= 2
    assert overlapping_pair is False
    assert [
        (item["original_case_id"], item["repetition"], item["variant"])
        for item in result.request_order
    ] == [
        ("case-a", 1, "no_graph"),
        ("case-a", 1, "graph_shadow"),
        ("case-a", 1, "graph_full"),
        ("case-b", 1, "no_graph"),
        ("case-b", 1, "graph_shadow"),
        ("case-b", 1, "graph_full"),
        ("case-a", 2, "no_graph"),
        ("case-a", 2, "graph_shadow"),
        ("case-a", 2, "graph_full"),
        ("case-b", 2, "no_graph"),
        ("case-b", 2, "graph_shadow"),
        ("case-b", 2, "graph_full"),
    ]
    assert [
        (row["original_case_id"], row["repetition"], row["request_sequence"])
        for row in result.variant_rows["no_graph"]
    ] == [
        ("case-a", 1, 1),
        ("case-b", 1, 4),
        ("case-a", 2, 7),
        ("case-b", 2, 10),
    ]


def test_parser_defaults_to_project_ablation_ports_and_output_dir() -> None:
    args = ablation.build_parser().parse_args(["--dataset", "eval.jsonl"])

    assert args.no_graph_endpoint == "http://127.0.0.1:8001/ai/chat"
    assert args.graph_shadow_endpoint == "http://127.0.0.1:8002/ai/chat"
    assert args.graph_full_endpoint == "http://127.0.0.1:8003/ai/chat"
    assert args.graph_endpoint == ""
    assert args.out_dir == "evaluation/results/kg_ablation"
    assert args.repetitions == 1
    assert args.concurrency == 4
    assert args.bootstrap_samples == 10_000


def test_parser_exposes_three_canonical_variant_endpoints() -> None:
    args = ablation.build_parser().parse_args(
        [
            "--dataset",
            "eval.jsonl",
            "--no-graph-endpoint",
            "http://test/no",
            "--graph-shadow-endpoint",
            "http://test/shadow",
            "--graph-full-endpoint",
            "http://test/full",
        ]
    )

    assert args.no_graph_endpoint == "http://test/no"
    assert args.graph_shadow_endpoint == "http://test/shadow"
    assert args.graph_full_endpoint == "http://test/full"


def test_three_arm_runner_parallelizes_units_but_serializes_variants(monkeypatch) -> None:
    state_lock = threading.Lock()
    active_calls = 0
    peak_active_calls = 0
    active_units: set[str] = set()
    overlapping_unit = False

    def fake_run_cases(cases, **kwargs):
        nonlocal active_calls, peak_active_calls, overlapping_unit
        case = cases[0]
        unit_id = case.case_id
        with state_lock:
            if unit_id in active_units:
                overlapping_unit = True
            active_units.add(unit_id)
            active_calls += 1
            peak_active_calls = max(peak_active_calls, active_calls)
        time.sleep(0.03)
        with state_lock:
            active_calls -= 1
            active_units.remove(unit_id)
        port = kwargs["endpoint"].rsplit(":", 1)[-1].split("/", 1)[0]
        variant = {"8001": "no_graph", "8002": "graph_shadow", "8003": "graph_full"}[port]
        return [{
            "id": case.case_id,
            "case_id": case.case_id,
            "rag_variant": variant,
            "final_pass": True,
            "latency_ms": 1,
        }]

    monkeypatch.setattr(ablation, "run_cases", fake_run_cases)
    result = ablation.run_paired_cases(
        [
            MaintenanceEvalCase(case_id="case-a", query="A"),
            MaintenanceEvalCase(case_id="case-b", query="B"),
        ],
        endpoints={
            "no_graph": "http://127.0.0.1:8001/ai/chat",
            "graph_shadow": "http://127.0.0.1:8002/ai/chat",
            "graph_full": "http://127.0.0.1:8003/ai/chat",
        },
        repetitions=2,
        timeout=5,
        run_id="three-arm",
        concurrency=2,
    )

    assert 1 < peak_active_calls <= 2
    assert overlapping_unit is False
    assert [item["variant"] for item in result.request_order[:3]] == [
        "no_graph",
        "graph_shadow",
        "graph_full",
    ]
    assert {variant: len(rows) for variant, rows in result.variant_rows.items()} == {
        "no_graph": 4,
        "graph_shadow": 4,
        "graph_full": 4,
    }


def _mechanism_row(variant: str, **overrides):
    row = {
        "case_id": "case-a::r1",
        "original_case_id": "case-a",
        "repetition": 1,
        "turn_index": 1,
        "rag_variant": variant,
        "graph_dependency": "required",
        "graph_candidate_query_count": 0,
        "graph_candidate_count": 0,
        "graph_qualified_count": 0,
        "graph_routing_only_count": 0,
        "graph_rejected_count": 0,
        "graph_evidence_ids": "",
        "graph_evidence_used_ids": "",
        "graph_evidence_used_count": 0,
        "graph_tool_call_count": 0,
        "graph_tools_used": "",
        "graph_review_enabled": False,
    }
    row.update(overrides)
    return row


def test_mechanism_gate_rejects_nonzero_no_graph_activity() -> None:
    report = ablation.evaluate_mechanism_gate({
        "no_graph": [_mechanism_row("no_graph", graph_candidate_count=1)],
        "graph_shadow": [_mechanism_row("graph_shadow")],
        "graph_full": [_mechanism_row(
            "graph_full",
            graph_candidate_count=1,
            graph_qualified_count=1,
            graph_evidence_used_count=1,
            graph_evidence_used_ids="graph:path-1",
        )],
    })

    assert report["passed"] is False
    assert report["checks"]["no_graph_zero_activity"]["passed"] is False
    assert report["checks"]["no_graph_zero_activity"]["violations"][0]["field"] == "graph_candidate_count"


def test_mechanism_gate_rejects_shadow_evidence_or_review_leakage() -> None:
    report = ablation.evaluate_mechanism_gate({
        "no_graph": [_mechanism_row("no_graph")],
        "graph_shadow": [_mechanism_row(
            "graph_shadow",
            graph_evidence_used_count=1,
            graph_evidence_used_ids="graph:path-1",
            graph_review_enabled=True,
        )],
        "graph_full": [_mechanism_row(
            "graph_full",
            graph_candidate_count=1,
            graph_qualified_count=1,
            graph_evidence_used_count=1,
            graph_evidence_used_ids="graph:path-1",
        )],
    })

    assert report["passed"] is False
    check = report["checks"]["graph_shadow_isolation"]
    assert check["passed"] is False
    assert {item["field"] for item in check["violations"]} == {
        "graph_evidence_used_count",
        "graph_evidence_used_ids",
        "graph_review_enabled",
    }


def test_mechanism_gate_rejects_zero_full_chain_for_required_subset() -> None:
    report = ablation.evaluate_mechanism_gate({
        "no_graph": [_mechanism_row("no_graph")],
        "graph_shadow": [_mechanism_row("graph_shadow", graph_candidate_count=1)],
        "graph_full": [_mechanism_row("graph_full")],
    })

    assert report["passed"] is False
    check = report["checks"]["graph_full_required_chain"]
    assert check["status"] == "failed"
    assert check["totals"] == {
        "graph_candidate_count": 0,
        "graph_qualified_count": 0,
        "graph_evidence_used_count": 0,
    }


def test_mechanism_gate_checks_every_required_row_instead_of_batch_totals() -> None:
    complete = _mechanism_row(
        "graph_full",
        case_id="complete::r1",
        original_case_id="complete",
        graph_candidate_count=1,
        graph_qualified_count=1,
        graph_evidence_used_count=1,
        graph_evidence_used_ids="graph:path-1:none",
        claim_evidence_bindings='[{"evidence_ids":["graph:path-1:none"]}]',
    )
    unbound = _mechanism_row(
        "graph_full",
        case_id="unbound::r1",
        original_case_id="unbound",
        graph_candidate_count=1,
        graph_qualified_count=1,
        graph_evidence_used_count=0,
        graph_evidence_used_ids="",
        claim_evidence_bindings="[]",
    )

    report = ablation.evaluate_mechanism_gate({
        "no_graph": [_mechanism_row("no_graph")],
        "graph_shadow": [_mechanism_row("graph_shadow")],
        "graph_full": [complete, unbound],
    })

    check = report["checks"]["graph_full_required_chain"]
    assert check["passed"] is False
    assert check["failed_turn_count"] == 1
    assert check["violations"][0]["original_case_id"] == "unbound"
    assert "graph_evidence_used_count" in check["violations"][0]["missing_fields"]
    assert "claim_evidence_bindings" in check["violations"][0]["missing_fields"]


def test_mechanism_gate_rejects_paired_route_contract_drift() -> None:
    full = _mechanism_row(
        "graph_full",
        graph_candidate_count=1,
        graph_qualified_count=1,
        graph_evidence_used_count=1,
        graph_evidence_used_ids="graph:path-1:none",
        claim_evidence_bindings='[{"evidence_ids":["graph:path-1:none"]}]',
        route_contract_signature="signature-b",
    )
    report = ablation.evaluate_mechanism_gate({
        "no_graph": [_mechanism_row("no_graph", route_contract_signature="signature-a")],
        "graph_shadow": [_mechanism_row("graph_shadow", route_contract_signature="signature-a")],
        "graph_full": [full],
    })

    check = report["checks"]["paired_route_contract_consistency"]
    assert check["passed"] is False
    assert check["violations"][0]["signatures"]["graph_full"] == "signature-b"


def test_main_records_concurrency_in_variant_manifests(monkeypatch, tmp_path: Path) -> None:
    dataset = tmp_path / "eval.jsonl"
    dataset.write_text(json.dumps({"case_id": "case-a", "query": "A"}) + "\n", encoding="utf-8")
    out_dir = tmp_path / "results"

    monkeypatch.setattr(
        ablation,
        "validate_endpoint_variant",
        lambda endpoint, expected_variant, **kwargs: {
            "status": "ok",
            "rag_variant": expected_variant,
        },
    )

    captured_concurrency: list[int] = []

    def fake_run_paired_cases(cases, **kwargs):
        captured_concurrency.append(kwargs["concurrency"])
        variant_cases = {
            variant: [replace(cases[0], case_id="case-a::r1")]
            for variant in ablation.VARIANTS
        }
        variant_rows = {
            variant: [
                {
                    "id": "case-a::r1",
                    "case_id": "case-a::r1",
                    "original_case_id": "case-a",
                    "repetition": 1,
                    "variant_label": variant,
                    "rag_variant": variant,
                    "answerable": True,
                    "final_pass": True,
                    "grounding_pass": True,
                    "required_nugget_recall": 1.0,
                    "latency_ms": 1,
                }
            ]
            for variant in ablation.VARIANTS
        }
        return ablation.PairedRunResult(
            variant_rows=variant_rows,
            variant_cases=variant_cases,
            variant_traces={variant: [] for variant in ablation.VARIANTS},
            request_order=[],
        )

    monkeypatch.setattr(ablation, "run_paired_cases", fake_run_paired_cases)

    exit_code = ablation.main(
        [
            "--dataset",
            str(dataset),
            "--out-dir",
            str(out_dir),
            "--run-name",
            "parallel",
            "--concurrency",
            "3",
            "--bootstrap-samples",
            "0",
        ]
    )

    assert exit_code == 0
    assert captured_concurrency == [3]
    for variant in ablation.VARIANTS:
        manifest = json.loads(
            (out_dir / f"parallel_{variant}_run.json").read_text(encoding="utf-8")
        )
        assert manifest["concurrency"] == 3
    assert (out_dir / "parallel_graph_shadow_minus_no_graph_comparison.json").is_file()
    assert (out_dir / "parallel_graph_full_minus_graph_shadow_comparison.json").is_file()
    assert (out_dir / "parallel_graph_full_minus_no_graph_comparison.json").is_file()
    full_vs_shadow = json.loads(
        (out_dir / "parallel_graph_full_minus_graph_shadow_comparison.json").read_text(
            encoding="utf-8"
        )
    )
    assert full_vs_shadow["baseline_variant"] == "graph_shadow"
    assert full_vs_shadow["comparison_variant"] == "graph_full"
    assert full_vs_shadow["final_pass"]["baseline_rate"] == 1.0
    assert full_vs_shadow["final_pass"]["comparison_rate"] == 1.0
    gate = json.loads(
        (out_dir / "parallel_mechanism_gate.json").read_text(encoding="utf-8")
    )
    assert gate["passed"] is True


def test_main_writes_mechanism_diagnostics_before_returning_exit_2(
    monkeypatch,
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "required.jsonl"
    dataset.write_text(
        json.dumps({
            "case_id": "case-required",
            "query": "A",
            "graph_dependency": "required",
        })
        + "\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "results"

    monkeypatch.setattr(
        ablation,
        "validate_endpoint_variant",
        lambda endpoint, expected_variant, **kwargs: {
            "status": "ok",
            "rag_variant": expected_variant,
        },
    )

    def fake_run_paired_cases(cases, **kwargs):
        variant_cases = {
            variant: [replace(cases[0], case_id="case-required::r1")]
            for variant in ablation.VARIANTS
        }
        variant_rows = {
            variant: [
                {
                    **_mechanism_row(variant),
                    "id": "case-required::r1",
                    "case_id": "case-required::r1",
                    "original_case_id": "case-required",
                    "graph_dependency": "required",
                    "variant_label": variant,
                    "answerable": True,
                    "final_pass": True,
                    "grounding_pass": True,
                    "required_nugget_recall": 1.0,
                    "latency_ms": 1,
                }
            ]
            for variant in ablation.VARIANTS
        }
        return ablation.PairedRunResult(
            variant_rows=variant_rows,
            variant_cases=variant_cases,
            variant_traces={variant: [] for variant in ablation.VARIANTS},
            request_order=[],
        )

    monkeypatch.setattr(ablation, "run_paired_cases", fake_run_paired_cases)

    exit_code = ablation.main([
        "--dataset",
        str(dataset),
        "--out-dir",
        str(out_dir),
        "--run-name",
        "gate-failure",
        "--bootstrap-samples",
        "0",
    ])

    assert exit_code == 2
    gate_path = out_dir / "gate-failure_mechanism_gate.json"
    assert gate_path.is_file()
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    assert gate["passed"] is False
    assert gate["failed_checks"] == ["graph_full_required_chain"]
    for variant in ablation.VARIANTS:
        assert (out_dir / f"gate-failure_{variant}.csv").is_file()
        assert (out_dir / f"gate-failure_{variant}_run.json").is_file()
    for comparison in (
        "graph_shadow_minus_no_graph",
        "graph_full_minus_graph_shadow",
        "graph_full_minus_no_graph",
    ):
        assert (out_dir / f"gate-failure_{comparison}_comparison.json").is_file()
