"""Manual direct-output qualification regressions."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.main import _format_manual_evidence_answer_from_metadata


def _metadata(qualification: str) -> dict:
    return {
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": [{
                    "content": "1. 按手册规定执行。",
                    "metadata": {
                        "qualification": qualification,
                        "section_title": "轮胎更换",
                        "parent_section_id": "tire-change",
                        "page": 12,
                    },
                }],
            }],
        }],
    }


def test_reference_evidence_does_not_trigger_manual_template() -> None:
    assert _format_manual_evidence_answer_from_metadata("如何更换轮胎？", _metadata("reference_only")) is None


def test_excluded_evidence_does_not_trigger_manual_template() -> None:
    assert _format_manual_evidence_answer_from_metadata("如何更换轮胎？", _metadata("excluded")) is None
