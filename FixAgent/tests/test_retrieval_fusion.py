"""Multi-query metadata preservation for reciprocal-rank fusion."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.retrieval.fusion import reciprocal_rank_fusion


def _candidate(text: str, source: str, score: float) -> dict:
    return {
        "doc_id": "chunk-1",
        "content": "若有损坏或变形，则应更换火花塞。",
        "relevance_score": score,
        "metadata": {
            "query_variant_text": text,
            "query_variant_source": source,
            "query_variant_target_id": "",
        },
    }


def test_rrf_preserves_query_variants_from_every_contributing_list() -> None:
    fused = reciprocal_rank_fusion(
        [
            [_candidate("完整的原始问题", "original", 0.9)],
            [_candidate("火花塞 火花塞损坏", "component_fault", 0.8)],
        ],
        key_fn=lambda item: str(item.get("doc_id") or ""),
    )

    assert fused[0]["metadata"]["query_variants"] == [
        {"text": "完整的原始问题", "source": "original", "target_id": ""},
        {"text": "火花塞 火花塞损坏", "source": "component_fault", "target_id": ""},
    ]
