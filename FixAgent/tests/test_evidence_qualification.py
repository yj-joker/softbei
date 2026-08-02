"""Evidence qualification regressions."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.retrieval.qualification import _detect_conflicts, qualify_candidates


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


def _torque_evidence(evidence_id: str, *, seq: str, quantity: str, torque: str) -> dict:
    return {
        "content": f"序号={seq}；零件名称=M10螺母；数量={quantity}；扭矩={torque} N·m",
        "metadata": {
            "evidence_id": evidence_id,
            "part_name": "M10螺母",
            "parameter_names": ["M10螺母", "扭矩"],
            "parameter_type": "torque",
            "units": ["N·m"],
            "numeric_values": [
                {"raw": seq},
                {"raw": "10"},
                {"raw": quantity},
                {"raw": torque, "unit": "N·m"},
            ],
        },
    }


def test_sequence_and_quantity_numbers_do_not_create_torque_conflict() -> None:
    conflicts = _detect_conflicts(
        [
            _torque_evidence("row-1", seq="1", quantity="4", torque="60 ± 5"),
            _torque_evidence("row-2", seq="8", quantity="6", torque="60 ± 5"),
        ]
    )

    assert conflicts == []


def test_same_part_different_torque_is_a_real_conflict() -> None:
    conflicts = _detect_conflicts(
        [
            _torque_evidence("row-1", seq="1", quantity="4", torque="60 ± 5"),
            _torque_evidence("row-2", seq="8", quantity="6", torque="55 ± 5"),
        ]
    )

    assert len(conflicts) == 1
    assert conflicts[0]["field"] == "M10螺母:torque"
    assert conflicts[0]["unit"] == "N·m"
    assert conflicts[0]["values"] == ["55 ± 5", "60 ± 5"]


def test_same_semantic_field_normalizes_unit_spelling_before_conflict_detection() -> None:
    conflicts = _detect_conflicts([
        {
            "doc_id": "row-a",
            "metadata": {
                "evidence_id": "row-a",
                "parameter_values": [{"field": "右盖:拧紧力矩", "value": "10.0", "unit": "N*m"}],
            },
        },
        {
            "doc_id": "row-b",
            "metadata": {
                "evidence_id": "row-b",
                "parameter_values": [{"field": "右盖:紧固扭矩", "value": "12", "unit": "N·m"}],
            },
        },
    ])

    assert len(conflicts) == 1
    assert conflicts[0]["unit"] == "N·m"


def test_different_semantic_columns_or_actions_do_not_conflict() -> None:
    conflicts = _detect_conflicts([
        {
            "doc_id": "row-standard",
            "metadata": {
                "evidence_id": "row-standard",
                "action": "安装",
                "parameter_values": [
                    {"field": "气门:标准值:间隙", "value": "0.10", "unit": "mm"},
                    {"field": "气门:维修极限:间隙", "value": "0.20", "unit": "mm"},
                ],
            },
        },
        {
            "doc_id": "row-remove",
            "metadata": {
                "evidence_id": "row-remove",
                "action": "拆卸",
                "parameter_values": [
                    {"field": "气门:标准值:间隙", "value": "0.30", "unit": "mm"},
                ],
            },
        },
    ])

    assert conflicts == []


def test_multiple_values_from_one_evidence_record_are_not_cross_source_conflict() -> None:
    conflicts = _detect_conflicts([
        {
            "doc_id": "row-only",
            "metadata": {
                "evidence_id": "row-only",
                "parameter_values": [
                    {"field": "气门:间隙", "value": "0.10", "unit": "mm"},
                    {"field": "气门:间隙", "value": "0.20", "unit": "mm"},
                ],
            },
        },
    ])

    assert conflicts == []
