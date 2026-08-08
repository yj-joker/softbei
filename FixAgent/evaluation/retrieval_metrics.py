"""Dependency-free standard ranked-retrieval metrics.

Definitions match the conventional IR measures at cutoff ``k``:
Recall counts every positively relevant qrel, MRR uses the first positive
qrel, and nDCG uses graded gains ``2**grade - 1``.
"""

from __future__ import annotations

import math
from typing import Mapping, Sequence


def _deduplicate(items: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw_item in items:
        item = str(raw_item)
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _dcg(grades: Sequence[int]) -> float:
    return sum((2**grade - 1) / math.log2(rank + 1) for rank, grade in enumerate(grades, start=1))


def evaluate_ranked_retrieval(
    qrels: Mapping[str, Mapping[str, int]],
    runs: Mapping[str, Sequence[str]],
    *,
    k: int = 5,
) -> dict:
    if k <= 0:
        raise ValueError("k must be positive")

    per_query: dict[str, dict] = {}
    raw_metrics: dict[str, list[float]] = {
        f"recall_at_{k}": [],
        f"mrr_at_{k}": [],
        f"ndcg_at_{k}": [],
    }
    skipped = 0
    for query_id in sorted(qrels):
        grades: dict[str, int] = {}
        for document_id, raw_grade in qrels[query_id].items():
            try:
                grade = int(raw_grade)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"invalid relevance grade for {query_id}/{document_id}: {raw_grade!r}"
                ) from exc
            if grade < 0:
                raise ValueError(
                    f"invalid relevance grade for {query_id}/{document_id}: {grade!r}"
                )
            grades[str(document_id)] = grade

        relevant = {document_id for document_id, grade in grades.items() if grade > 0}
        if not relevant:
            skipped += 1
            continue
        ranking = _deduplicate(runs.get(query_id, []))[:k]
        retrieved_relevant = [document_id for document_id in ranking if document_id in relevant]
        first_rank = next(
            (rank for rank, document_id in enumerate(ranking, start=1) if document_id in relevant),
            None,
        )
        ranked_grades = [grades.get(document_id, 0) for document_id in ranking]
        ideal_grades = sorted((grade for grade in grades.values() if grade > 0), reverse=True)[:k]
        ideal_dcg = _dcg(ideal_grades)
        recall = len(retrieved_relevant) / len(relevant)
        reciprocal_rank = 1 / first_rank if first_rank else 0.0
        ndcg = _dcg(ranked_grades) / ideal_dcg if ideal_dcg else 0.0
        raw_metrics[f"recall_at_{k}"].append(recall)
        raw_metrics[f"mrr_at_{k}"].append(reciprocal_rank)
        raw_metrics[f"ndcg_at_{k}"].append(ndcg)
        per_query[query_id] = {
            f"recall_at_{k}": round(recall, 6),
            f"mrr_at_{k}": round(reciprocal_rank, 6),
            f"ndcg_at_{k}": round(ndcg, 6),
            "relevant_count": len(relevant),
            "retrieved_relevant_count": len(retrieved_relevant),
            "first_relevant_rank": first_rank,
        }

    count = len(per_query)

    def average(metric: str) -> float:
        if not count:
            return 0.0
        return round(sum(raw_metrics[metric]) / count, 6)

    return {
        "cutoff": k,
        "query_count": count,
        "skipped_query_count": skipped,
        f"recall_at_{k}": average(f"recall_at_{k}"),
        f"mrr_at_{k}": average(f"mrr_at_{k}"),
        f"ndcg_at_{k}": average(f"ndcg_at_{k}"),
        "per_query": per_query,
    }
