"""Stable source-order regressions for direct Redis manual lookups."""

from __future__ import annotations

import json

from services.knowledge.vector_service import VectorService


class _RedisWithShuffledSection:
    def execute_command(self, *args):
        results: list[object] = [4]
        for step in (4, 3, 2, 1):
            metadata = {
                "document_id": "manual-1",
                "parent_section_id": "sec-tensioner",
                "chunk_type": "step_raw",
                "section_index": 4,
                "page": 13,
                "source_index": step,
                "child_index": step - 1,
                "source_chunk_id": f"source-{step}",
            }
            results.extend([
                f"doc:derived-{step}".encode(),
                [
                    b"id", f"derived-{step}".encode(),
                    b"text", f"{step}. step {step}".encode(),
                    b"metadata", json.dumps(metadata).encode(),
                ],
            ])
        return results


def test_get_section_records_restores_document_order_from_shuffled_redis_results() -> None:
    service = object.__new__(VectorService)
    service.redis = _RedisWithShuffledSection()

    records = service.get_section_records(
        "manual-1",
        "sec-tensioner",
        limit=10,
        chunk_type="step_raw",
    )

    assert [record["metadata"]["source_index"] for record in records] == [1, 2, 3, 4]


def test_get_page_records_restores_document_order_from_shuffled_redis_results() -> None:
    service = object.__new__(VectorService)
    service.redis = _RedisWithShuffledSection()

    records = service.get_page_records(
        "manual-1",
        13,
        chunk_type="step_raw",
        limit=10,
    )

    assert [record["metadata"]["source_index"] for record in records] == [1, 2, 3, 4]


def test_list_document_chunks_restores_document_order_from_shuffled_redis_results() -> None:
    service = object.__new__(VectorService)
    service.redis = _RedisWithShuffledSection()
    service._ensure_index = lambda: None

    records = service.list_document_chunks("manual-1", exclude_derived=False)

    assert [record["metadata"]["source_index"] for record in records] == [1, 2, 3, 4]
