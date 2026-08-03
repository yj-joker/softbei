"""Conditional supplemental retrieval policy and execution tests."""

import asyncio
from types import SimpleNamespace

from services.retrieval.quality import RetrievalQualityReport
from services.retrieval.supplement import (
    CandidateRequestCache,
    decide_supplemental_retrieval,
    run_supplemental_stage,
)


def _plan(intent: str = "procedure") -> SimpleNamespace:
    return SimpleNamespace(intent=intent)


def _report(
    *,
    grade: str = "high",
    routes: list[str] | None = None,
    reasons: list[str] | None = None,
) -> RetrievalQualityReport:
    return RetrievalQualityReport(
        grade=grade,
        score=0.9 if grade == "high" else 0.4,
        reasons=reasons or [],
        matched_types=["text"],
        required_types=["text"],
        supplemental_routes=routes or [],
        should_supplement=bool(routes),
        candidate_count=2,
        best_score=0.9 if grade == "high" else 0.4,
    )


def test_complete_coverage_skips_supplement_even_when_quality_requests_it() -> None:
    decision = decide_supplemental_retrieval(
        _plan(),
        _report(grade="medium", routes=["text", "keyword"]),
        coverage_status="complete",
        missing_aspect_ids=[],
    )

    assert decision.should_supplement is False
    assert decision.routes == ()
    assert decision.reason == "coverage_complete"


def test_missing_aspect_enables_exactly_one_supplemental_stage() -> None:
    decision = decide_supplemental_retrieval(
        _plan(),
        _report(grade="high"),
        coverage_status="partial",
        missing_aspect_ids=["cycle"],
    )

    assert decision.should_supplement is True
    assert decision.routes == ("text", "keyword")
    assert decision.max_stages == 2
    assert decision.missing_aspect_ids == ("cycle",)


def test_conflict_does_not_expand_retrieval() -> None:
    decision = decide_supplemental_retrieval(
        _plan("parameter"),
        _report(grade="low", routes=["table", "keyword"]),
        coverage_status="conflict",
        missing_aspect_ids=[],
    )

    assert decision.should_supplement is False
    assert decision.reason == "coverage_conflict"


def test_supplement_failure_preserves_base_candidates() -> None:
    decision = decide_supplemental_retrieval(
        _plan(),
        _report(grade="low", routes=["text"]),
        coverage_status="unsupported",
        missing_aspect_ids=["procedure"],
    )
    base = [[{"doc_id": "base"}]]

    async def fail(_: str) -> list[dict]:
        raise TimeoutError("supplement timed out")

    result = asyncio.run(run_supplemental_stage(base, decision, fail))

    assert result.candidate_lists == base
    assert result.used is False
    assert result.failed_routes == ("text",)


def test_parallel_supplement_merge_order_follows_declared_routes() -> None:
    decision = decide_supplemental_retrieval(
        _plan(),
        _report(grade="low", routes=["text", "keyword"]),
        coverage_status="partial",
        missing_aspect_ids=["procedure"],
    )

    async def fetch(route: str) -> list[dict]:
        if route == "text":
            await asyncio.sleep(0.01)
        return [{"doc_id": route}]

    result = asyncio.run(run_supplemental_stage([[{"doc_id": "base"}]], decision, fetch))

    assert [[item["doc_id"] for item in group] for group in result.candidate_lists] == [
        ["base"],
        ["text"],
        ["keyword"],
    ]
    assert result.succeeded_routes == ("text", "keyword")
    assert result.used is True


def test_identical_query_filter_request_is_fetched_once() -> None:
    cache = CandidateRequestCache()
    calls = 0

    async def run() -> tuple[list[dict], list[dict]]:
        async def fetch() -> list[dict]:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)
            return [{"doc_id": "same"}]

        return await asyncio.gather(
            cache.get_or_fetch(("text", "scope-filter", 50), fetch),
            cache.get_or_fetch(("text", "scope-filter", 50), fetch),
        )

    first, second = asyncio.run(run())

    assert calls == 1
    assert first == second == [{"doc_id": "same"}]
    assert first is not second
