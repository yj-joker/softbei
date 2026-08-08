import time

from evaluation.maintenance_eval_schema import MaintenanceEvalCase
from evaluation.blind_eval_cli import score_frozen_responses
from evaluation.paired_variant_runner import run_paired_variants


def test_three_arm_runner_is_parallel_but_output_order_is_deterministic():
    calls = []

    def request_runner(case, variant, endpoint, request_sequence):
        calls.append((case.case_id, variant, endpoint, request_sequence))
        time.sleep(0.01)
        return {"case_id": case.case_id, "variant": variant, "final_pass": True}

    result = run_paired_variants(
        cases=[
            MaintenanceEvalCase(case_id="q1", query="问题一"),
            MaintenanceEvalCase(case_id="q2", query="问题二"),
        ],
        endpoints={
            "no_graph": "http://127.0.0.1:8001/ai/chat",
            "graph_shadow": "http://127.0.0.1:8002/ai/chat",
            "graph_full": "http://127.0.0.1:8003/ai/chat",
        },
        repetitions=2,
        concurrency=4,
        request_runner=request_runner,
    )

    assert [row["variant"] for row in result.request_order[:3]] == [
        "no_graph", "graph_shadow", "graph_full"
    ]
    assert result.concurrency == 4
    assert [row["case_id"] for row in result.rows[:3]] == ["q1", "q1", "q1"]
    assert len(calls) == 12


def test_score_frozen_responses_computes_variant_metrics_instead_of_only_counts():
    case = MaintenanceEvalCase(
        case_id="blind-score-001",
        query="问题",
        required_nuggets=["正确结论"],
    )
    result = score_frozen_responses(
        [
            {
                "case_id": "blind-score-001",
                "variant": "no_graph",
                "repetition": 1,
                "answer": "正确结论",
                "metadata": {},
                "evidence_images": [],
            },
            {
                "case_id": "blind-score-001",
                "variant": "graph_full",
                "repetition": 1,
                "answer": "错误结论",
                "metadata": {},
                "evidence_images": [],
            },
        ],
        [case],
    )

    assert result["case_count"] == 1
    assert result["variants"]["no_graph"]["summary"]["answer_correct_pass_rate"] == 1.0
    assert result["variants"]["graph_full"]["summary"]["answer_correct_pass_rate"] == 0.0
