"""Evidence qualification regressions."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.retrieval.qualification import qualify_candidates


def _candidate(*, device_type="truck", document_id="truck-manual", content="大卡车 轮胎 更换", **metadata):
    return {
        "doc_id": "chunk-1",
        "content": content,
        "metadata": {
            "device_type": device_type,
            "document_id": document_id,
            "section_title": "轮胎更换",
            "local_rerank_features": {"query_coverage": 0.8, "title_coverage": 0.8},
            **metadata,
        },
    }


def test_matching_scoped_candidate_is_qualified() -> None:
    bundle = qualify_candidates(
        "大卡车轮胎怎么更换",
        [_candidate()],
        device_type="truck",
        document_id="truck-manual",
        requires_strict_evidence=True,
    )

    assert bundle["overall_status"] == "qualified"
    assert bundle["capabilities"]["may_cite_manual"] is True
    assert bundle["qualified_evidence"][0]["metadata"]["qualification"] == "qualified"


def test_cross_device_topic_conflict_is_excluded() -> None:
    bundle = qualify_candidates(
        "大卡车轮胎怎么更换",
        [_candidate(
            device_type="motorcycle",
            document_id="motorcycle-manual",
            content="右曲轴箱盖与离合器的拆卸步骤",
            section_title="右曲轴箱盖与离合器",
            local_rerank_features={"query_coverage": 0.0, "title_coverage": 0.0},
        )],
        device_type="truck",
        document_id="truck-manual",
        requires_strict_evidence=True,
    )

    assert bundle["overall_status"] == "no_evidence"
    assert bundle["qualified_evidence"] == []
    assert bundle["reference_evidence"] == []
    assert bundle["excluded_evidence"][0]["reasons"] == ["device_mismatch", "document_mismatch", "topic_conflict"]


def test_unscoped_candidate_is_reference_only() -> None:
    bundle = qualify_candidates("轮胎怎么更换", [_candidate()], requires_strict_evidence=False)

    assert bundle["overall_status"] == "reference_only"
    assert bundle["capabilities"]["may_cite_manual"] is False
    assert bundle["reference_evidence"][0]["metadata"]["qualification"] == "reference_only"
