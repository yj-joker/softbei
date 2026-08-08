import math

import pytest

from evaluation.retrieval_metrics import evaluate_ranked_retrieval


def test_standard_metrics_use_all_relevant_items_and_graded_ndcg() -> None:
    qrels = {
        "q1": {"a": 3, "b": 1},
        "q2": {"c": 2},
    }
    runs = {
        "q1": ["x", "b", "a"],
        "q2": ["c", "z"],
    }

    report = evaluate_ranked_retrieval(qrels, runs, k=2)

    q1_dcg = 1 / math.log2(3)
    q1_idcg = 7 + 1 / math.log2(3)
    assert report["query_count"] == 2
    assert report["skipped_query_count"] == 0
    assert report["recall_at_2"] == 0.75
    assert report["mrr_at_2"] == 0.75
    assert report["ndcg_at_2"] == pytest.approx(round(((q1_dcg / q1_idcg) + 1.0) / 2, 6))
    assert report["per_query"]["q1"]["recall_at_2"] == 0.5
    assert report["per_query"]["q1"]["first_relevant_rank"] == 2


def test_standard_metrics_deduplicate_run_and_skip_queries_without_positive_qrels() -> None:
    report = evaluate_ranked_retrieval(
        {"q1": {"a": 1, "b": 1}, "q2": {"z": 0}},
        {"q1": ["a", "a", "b"], "q2": ["z"]},
        k=2,
    )

    assert report["query_count"] == 1
    assert report["skipped_query_count"] == 1
    assert report["recall_at_2"] == 1.0
    assert report["mrr_at_2"] == 1.0
    assert report["ndcg_at_2"] == 1.0


@pytest.mark.parametrize("k", [0, -1])
def test_standard_metrics_reject_non_positive_cutoff(k: int) -> None:
    with pytest.raises(ValueError, match="k must be positive"):
        evaluate_ranked_retrieval({"q": {"a": 1}}, {"q": ["a"]}, k=k)
