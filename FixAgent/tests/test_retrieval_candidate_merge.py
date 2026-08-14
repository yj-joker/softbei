"""Candidate merge regressions for multi-query retrieval."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.knowledge_retrieval_tool import KnowledgeRetrievalTool


def _candidate(variant_text: str, variant_source: str, score: float) -> dict:
    return {
        "doc_id": "chunk-1",
        "content": "若火花塞损坏或变形，则应更换火花塞。",
        "relevance_score": score,
        "routes": ["text_vector"],
        "metadata": {
            "chunk_id": "chunk-1",
            "query_variant_text": variant_text,
            "query_variant_source": variant_source,
            "query_variant_target_id": "",
        },
    }


def test_merge_preserves_all_query_variants_for_topic_qualification() -> None:
    merged = KnowledgeRetrievalTool._merge_candidates([
        _candidate("完整的原始问题", "original", 0.9),
        _candidate("火花塞 火花塞损坏", "component_fault", 0.8),
    ])

    assert len(merged) == 1
    assert merged[0]["metadata"]["query_variants"] == [
        {"text": "完整的原始问题", "source": "original", "target_id": ""},
        {"text": "火花塞 火花塞损坏", "source": "component_fault", "target_id": ""},
    ]
