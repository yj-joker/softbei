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
                "overall_status": "qualified" if qualification == "qualified" else "reference_only",
                "capabilities": {"may_cite_manual": qualification == "qualified"},
                "excluded_evidence": [],
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
