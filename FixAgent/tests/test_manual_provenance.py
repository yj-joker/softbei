"""Structural identity and source-order contract for manual evidence."""

from __future__ import annotations

from services.retrieval.provenance import (
    canonical_manual_chunk_id,
    dedupe_and_sort_manual_records,
    manual_position_key,
)


def _record(
    step: int,
    *,
    record_id: str | None = None,
    source_chunk_id: str | None = None,
    chunk_type: str = "step_raw",
) -> dict:
    return {
        "id": record_id or f"derived-{step}",
        "content": f"{step}. 执行第{step}步。",
        "metadata": {
            "document_id": "manual-1",
            "chunk_id": record_id or f"derived-{step}",
            "source_chunk_id": source_chunk_id or f"source-{step}",
            "chunk_type": chunk_type,
            "section_index": 4,
            "page": 13,
            "source_index": step,
            "child_index": step - 1,
            "row_index": 0,
        },
    }


def test_canonical_manual_chunk_id_prefers_source_chunk_identity() -> None:
    item = _record(2, record_id="derived-step-2", source_chunk_id="source-step-2")

    assert canonical_manual_chunk_id(item) == "source-step-2"


def test_manual_position_key_uses_structure_not_relevance_score() -> None:
    first = _record(1)
    second = _record(2)
    first["score"] = 0.01
    second["score"] = 0.99

    assert manual_position_key(first) < manual_position_key(second)


def test_dedupe_and_sort_manual_records_restores_source_order_and_prefers_raw_step() -> None:
    contextual_step_2 = _record(
        2,
        record_id="contextual-step-2",
        source_chunk_id="source-2",
        chunk_type="text",
    )
    contextual_step_2["content"] = "章节上下文：2. 执行第2步。"
    direct_step_2 = _record(
        2,
        record_id="direct-step-2",
        source_chunk_id="source-2",
        chunk_type="step_raw",
    )

    ordered = dedupe_and_sort_manual_records([
        _record(4),
        contextual_step_2,
        _record(1),
        _record(3),
        direct_step_2,
    ])

    assert [canonical_manual_chunk_id(item) for item in ordered] == [
        "source-1",
        "source-2",
        "source-3",
        "source-4",
    ]
    assert ordered[1]["metadata"]["chunk_type"] == "step_raw"
    assert ordered[1]["content"] == "2. 执行第2步。"


def test_ledger_source_shape_uses_the_same_identity_and_position_contract() -> None:
    ledger_entry = {
        "evidence_id": "manual:manual-1:derived-step-3",
        "text": "3. 执行第3步。",
        "source_type": "manual",
        "source": {
            "document_id": "manual-1",
            "chunk_id": "derived-step-3",
            "source_chunk_id": "source-step-3",
            "section_index": 4,
            "page": 13,
            "source_index": 3,
            "child_index": 2,
        },
    }

    assert canonical_manual_chunk_id(ledger_entry) == "source-step-3"
    assert manual_position_key(ledger_entry)[3] == 3
