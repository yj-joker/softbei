"""Reliable single-document turn context storage regressions."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.grounded_turn_context import (
    GroundedTurnContext,
    GroundedTurnContextStore,
    context_from_successful_answer,
)


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.setex_calls: list[tuple[str, int, str]] = []
        self.deleted: list[str] = []

    def setex(self, key: str, ttl: int, payload: str) -> None:
        self.setex_calls.append((key, ttl, payload))
        self.values[key] = payload

    def get(self, key: str):
        return self.values.get(key)

    def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.values.pop(key, None)


def _context() -> GroundedTurnContext:
    return GroundedTurnContext(
        base_query="如何安装起动电机",
        document_id="manual-1",
        resolved_query="如何安装起动电机",
        section_id="section-install-starter",
        evidence_pages=(5,),
        source_chunk_ids=("step-install-starter",),
        procedure_scope_ids=("scope-install-starter",),
        created_at_ms=1,
    )


def _successful_metadata() -> dict:
    return {
        "route_plan": {"action": "grounded_retrieval"},
        "response_audit": {"passed": True},
        "coverage_status": "complete",
        "_deterministic_answer_section_ids": ["section-install-starter"],
        "_deterministic_answer_section_title": "2.3 安装起动电机",
        "image_selection_contract": {
            "target_document_ids": ["manual-1"],
            "target_non_image_source_ids": ["step-install-starter"],
            "target_procedure_scope_ids": ["scope-install-starter"],
            "target_pages": [5],
        },
    }


def test_memory_round_trip_and_empty_session_are_safe() -> None:
    store = GroundedTurnContextStore()

    store.remember("session-1", _context())
    store.remember("", _context())

    assert store.load("session-1") == _context()
    assert store.load("") is None


def test_redis_uses_stable_prefix_setex_and_ttl() -> None:
    redis = _FakeRedis()
    store = GroundedTurnContextStore(redis_client=redis, ttl_seconds=900)

    store.remember("session-1", _context())

    key, ttl, payload = redis.setex_calls[0]
    assert key == "fixagent:grounded-turn:session-1"
    assert ttl == 900
    assert json.loads(payload)["document_id"] == "manual-1"
    assert store.load("session-1") == _context()


def test_expired_memory_entry_is_removed() -> None:
    now = [100.0]
    store = GroundedTurnContextStore(ttl_seconds=10, now=lambda: now[0])
    store.remember("session-1", _context())

    now[0] = 111.0

    assert store.load("session-1") is None


def test_bad_json_is_cleared_without_raising() -> None:
    redis = _FakeRedis()
    key = "fixagent:grounded-turn:session-1"
    redis.values[key] = "{bad-json"
    store = GroundedTurnContextStore(redis_client=redis)

    assert store.load("session-1") is None
    assert redis.deleted == [key]


def test_context_from_successful_answer_keeps_only_evidence_boundary() -> None:
    metadata = _successful_metadata()
    metadata["answer"] = "不应保存的答案全文"

    context = context_from_successful_answer("如何安装起动电机", metadata)

    assert context is not None
    assert context.base_query == "如何安装起动电机"
    assert context.document_id == "manual-1"
    assert context.evidence_pages == (5,)
    assert context.source_chunk_ids == ("step-install-starter",)
    assert "answer" not in context.to_dict()


def test_context_rejects_multiple_documents() -> None:
    metadata = _successful_metadata()
    metadata["image_selection_contract"]["target_document_ids"] = [
        "manual-1",
        "manual-2",
    ]

    assert context_from_successful_answer("如何安装起动电机", metadata) is None


def test_context_rejects_fallback_and_failed_audit() -> None:
    fallback = _successful_metadata()
    fallback["execution_mode"] = "maintenance_ai_fallback_after_retrieval"
    failed_audit = _successful_metadata()
    failed_audit["response_audit"] = {"passed": False}

    assert context_from_successful_answer("问题", fallback) is None
    assert context_from_successful_answer("问题", failed_audit) is None


def test_context_rejects_allowed_candidates_without_final_sources() -> None:
    metadata = _successful_metadata()
    metadata["allowed_source_chunk_ids"] = ["step-candidate"]
    metadata["image_selection_contract"]["target_non_image_source_ids"] = []
    metadata["image_selection_contract"]["target_procedure_scope_ids"] = []

    assert context_from_successful_answer("问题", metadata) is None


def test_context_rejects_pages_without_final_sources_or_scopes() -> None:
    metadata = _successful_metadata()
    metadata["image_selection_contract"]["target_non_image_source_ids"] = []
    metadata["image_selection_contract"]["target_procedure_scope_ids"] = []
    metadata["image_selection_contract"]["target_pages"] = [5]

    assert context_from_successful_answer("问题", metadata) is None


def test_from_dict_reads_legacy_query_field_as_stable_base_query() -> None:
    context = GroundedTurnContext.from_dict({
        "query": "如何安装起动电机",
        "document_id": "manual-1",
        "evidence_pages": [5, "bad"],
    })

    assert context.base_query == "如何安装起动电机"
    assert context.resolved_query == "如何安装起动电机"
    assert context.evidence_pages == (5,)
