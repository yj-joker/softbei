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


def test_exact_section_title_can_promote_reference_record_to_direct_answer() -> None:
    metadata = {
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": [{
                    "id": "manual:valve:step-1",
                    "content": "1. 取下滑动挺柱。",
                    "metadata": {
                        "qualification": "reference_only",
                        "document_id": "manual-doc",
                        "section_title": "4.8 气门",
                        "parent_section_id": "sec-valve",
                        "page": 16,
                        "original_title_match": True,
                        "chunk_type": "step_raw",
                        "source_index": 1,
                    },
                }],
            }],
        }],
    }

    answer = _format_manual_evidence_answer_from_metadata("如何拆卸气门？", metadata)

    assert answer is not None
    assert answer.startswith("可以按以下顺序操作：")
