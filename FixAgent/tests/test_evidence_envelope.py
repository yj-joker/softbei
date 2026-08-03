"""Tool evidence envelope regressions."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.base_agent import wrap_evidence_quality
from services.llm.evidence import build_evidence_items


def _result(qualification: str, content: str = "证据正文") -> dict:
    return {
        "id": qualification,
        "content": content,
        "score": 0.9,
        "metadata": {
            "qualification": qualification,
            "section_title": "测试章节",
            "evidence_bundle": {
                "evidence_bundle_version": 2,
                "overall_status": "qualified" if qualification == "qualified" else "reference_only",
                "coverage_status": "complete" if qualification == "qualified" else "unsupported",
                "coverage_reason": "test_coverage",
                "aspect_support": [{
                    "aspect_id": "gap",
                    "aspect_text": "火花塞间隙",
                    "supported": qualification == "qualified",
                    "evidence_ids": [qualification] if qualification == "qualified" else [],
                }],
                "missing_aspect_ids": [] if qualification == "qualified" else ["gap"],
                "conflict_eligible": [],
                "capabilities": {"may_cite_manual": qualification == "qualified"},
                "excluded_evidence": [{"evidence_id": "excluded-1", "reasons": ["topic_conflict"]}],
                "conflicts": [],
            },
        },
    }


def test_tool_envelope_hides_reference_body_from_llm_and_grounding() -> None:
    payload = wrap_evidence_quality(
        "knowledge_retrieval",
        [_result("qualified", "可引用正文"), _result("reference_only", "不可引用正文")],
    )

    assert [item["content"] for item in payload["results"]] == ["可引用正文"]
    assert "不可引用正文" not in str(payload["reference_evidence"])
    evidence = build_evidence_items("knowledge_retrieval", payload)
    assert [item["content"] for item in evidence] == ["可引用正文"]


def test_reference_only_envelope_has_no_grounding_evidence() -> None:
    payload = wrap_evidence_quality("knowledge_retrieval", [_result("reference_only")])

    assert payload["evidence_status"] == "reference_only"
    assert payload["results"] == []
    assert build_evidence_items("knowledge_retrieval", payload) == []


def test_reference_envelope_preserves_exact_title_provenance() -> None:
    item = _result("reference_only")
    item["metadata"]["original_title_match"] = True

    payload = wrap_evidence_quality("knowledge_retrieval", [item])

    assert payload["reference_evidence"][0]["metadata"]["original_title_match"] is True


def test_tool_envelope_preserves_v2_coverage_without_excluded_body() -> None:
    payload = wrap_evidence_quality("knowledge_retrieval", [_result("qualified")])

    assert payload["coverage_status"] == "complete"
    assert payload["coverage_reason"] == "test_coverage"
    assert payload["aspect_support"][0]["aspect_id"] == "gap"
    assert payload["missing_aspect_ids"] == []
    assert payload["conflict_eligible"] == []
    assert payload["excluded_evidence"] == [{
        "evidence_id": "excluded-1",
        "reasons": ["topic_conflict"],
    }]
    assert "content" not in str(payload["excluded_evidence"])


def test_empty_and_low_confidence_notices_forbid_knowledge_completion() -> None:
    empty = wrap_evidence_quality("knowledge_retrieval", [])
    low = wrap_evidence_quality(
        "knowledge_retrieval",
        [{
            "content": "跨设备资料",
            "metadata": {"answer_policy": "insufficient_evidence"},
        }],
    )

    for payload in (empty, low):
        notice = payload["evidence_notice"]
        assert "不得补写通用原因、参数或操作步骤" in notice
        assert "可以借鉴" not in notice
        assert "基于通用常识" not in notice
