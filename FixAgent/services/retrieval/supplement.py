"""Policy and bounded execution for conditional supplemental retrieval."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Sequence

from services.retrieval.quality import (
    RetrievalQualityReport,
    required_types_for_plan,
    supplemental_routes_for_plan,
)


class CandidateRequestCache:
    """Deduplicate concurrent route requests with the same immutable key."""

    def __init__(self) -> None:
        self._tasks: dict[tuple[Any, ...], asyncio.Task[List[Dict[str, Any]]]] = {}

    async def get_or_fetch(
        self,
        key: tuple[Any, ...],
        fetch: Callable[[], Awaitable[List[Dict[str, Any]]]],
    ) -> List[Dict[str, Any]]:
        task = self._tasks.get(key)
        if task is None:
            task = asyncio.create_task(fetch())
            self._tasks[key] = task
        try:
            return list(await task)
        except BaseException:
            if self._tasks.get(key) is task:
                self._tasks.pop(key, None)
            raise


@dataclass(frozen=True)
class SupplementalDecision:
    should_supplement: bool
    routes: tuple[str, ...]
    reason: str
    missing_aspect_ids: tuple[str, ...]
    max_stages: int = 2


@dataclass(frozen=True)
class SupplementalStageResult:
    candidate_lists: List[List[Dict[str, Any]]]
    succeeded_routes: tuple[str, ...]
    failed_routes: tuple[str, ...]
    used: bool


def decide_supplemental_retrieval(
    plan: Any,
    quality: RetrievalQualityReport,
    *,
    coverage_status: str,
    missing_aspect_ids: Sequence[str],
) -> SupplementalDecision:
    missing = tuple(dict.fromkeys(str(item) for item in missing_aspect_ids if str(item)))
    if coverage_status == "complete":
        return SupplementalDecision(False, (), "coverage_complete", missing)
    if coverage_status == "conflict":
        return SupplementalDecision(False, (), "coverage_conflict", missing)

    has_quality_gap = bool(quality.should_supplement or quality.grade in {"low", "medium"})
    if coverage_status not in {"partial", "unsupported"} and not has_quality_gap:
        return SupplementalDecision(False, (), "no_missing_evidence", missing)

    routes = list(quality.supplemental_routes)
    if not routes and (missing or coverage_status == "unsupported"):
        routes = supplemental_routes_for_plan(
            plan,
            required_types_for_plan(plan),
            weak_recall=True,
        )
    deduped = tuple(dict.fromkeys(routes))
    if not deduped:
        return SupplementalDecision(False, (), "no_supplemental_route", missing)
    reason = "missing_aspects" if missing else "retrieval_quality_gap"
    return SupplementalDecision(True, deduped, reason, missing)


async def run_supplemental_stage(
    base_candidate_lists: Sequence[Sequence[Dict[str, Any]]],
    decision: SupplementalDecision,
    fetch_route: Callable[[str], Awaitable[List[Dict[str, Any]]]],
) -> SupplementalStageResult:
    base = [list(group) for group in base_candidate_lists]
    if not decision.should_supplement or not decision.routes:
        return SupplementalStageResult(base, (), (), False)

    outcomes = await asyncio.gather(
        *(fetch_route(route) for route in decision.routes),
        return_exceptions=True,
    )
    succeeded: list[str] = []
    failed: list[str] = []
    supplemental: list[list[Dict[str, Any]]] = []
    for route, outcome in zip(decision.routes, outcomes):
        if isinstance(outcome, BaseException):
            failed.append(route)
            continue
        succeeded.append(route)
        supplemental.append(list(outcome or []))
    used = any(supplemental)
    return SupplementalStageResult(base + supplemental, tuple(succeeded), tuple(failed), used)
