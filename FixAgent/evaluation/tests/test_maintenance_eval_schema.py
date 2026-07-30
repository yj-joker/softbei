import json
from pathlib import Path

import pytest

from evaluation.maintenance_eval_cli import build_parser
from evaluation.maintenance_eval_schema import (
    MaintenanceEvalCase,
    read_jsonl_dataset,
    read_jsonl_datasets,
)


EVALUATION_DIR = Path(__file__).resolve().parents[1]


def _write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def test_legacy_datasets_still_load_as_one_hundred_unique_cases() -> None:
    paths = [
        EVALUATION_DIR / "maintenance_eval_dataset_v1.jsonl",
        EVALUATION_DIR / "maintenance_adversarial_v2.jsonl",
        EVALUATION_DIR / "maintenance_image_adversarial_v1.jsonl",
    ]

    cases = read_jsonl_datasets(paths)

    assert len(cases) == 100
    assert len({case.case_id for case in cases}) == 100
    assert {case.dataset_source for case in cases} == {path.name for path in paths}
    assert all(case.query for case in cases)
    assert all(case.turns == [] for case in cases)


def test_read_jsonl_dataset_parses_two_turn_constraints_and_sources(tmp_path: Path) -> None:
    dataset = _write_jsonl(
        tmp_path / "quality.jsonl",
        [
            {
                "case_id": "quality_multi_001",
                "group": "natural_response",
                "device_type": "motorcycle_engine",
                "document_id": "manual-v1",
                "turns": [
                    {
                        "query": "水泵包含哪些部件？",
                        "expected_scope": "in_scope",
                        "expected_coverage_status": "complete",
                        "source_request_mode": "normal",
                        "claim_constraints": [
                            {
                                "claim_id": "pump_parts",
                                "answer_patterns": ["水泵盖", "水泵轴"],
                                "evidence_patterns": ["水泵盖", "水泵轴"],
                                "forbidden_without_evidence_patterns": ["叶轮"],
                                "missing_disclosure_patterns": ["部件没有明确说明"],
                                "allowed_sources": [
                                    {
                                        "source_type": "manual",
                                        "document_id": "manual-v1",
                                        "document_version": "1.0",
                                        "pages": [25],
                                        "chunk_ids": ["chunk-25-table"],
                                    }
                                ],
                            }
                        ],
                        "style_expectation": {
                            "allow_manual_lead": False,
                            "max_answer_chars": 260,
                            "max_list_items": 8,
                        },
                    },
                    {
                        "query": "请给我原文和页码。",
                        "source_request_mode": "quote",
                        "expected_coverage_status": "complete",
                        "conflict_constraints": [
                            {
                                "subject": "水泵锁紧扭矩",
                                "alternatives": [
                                    {
                                        "value_patterns": ["20"],
                                        "unit_patterns": ["N·m"],
                                        "allowed_sources": [
                                            {
                                                "source_type": "domain_rule",
                                                "rule_id": "rule-20",
                                                "status": "active",
                                            }
                                        ],
                                    },
                                    {
                                        "value_patterns": ["25"],
                                        "unit_patterns": ["N·m"],
                                        "allowed_sources": [
                                            {
                                                "source_type": "graph",
                                                "node_ids": ["node-pump"],
                                                "relationship_types": ["HAS_TORQUE"],
                                                "path_ids": ["path-pump-torque"],
                                            }
                                        ],
                                    },
                                ],
                                "disclosure_patterns": ["资料存在冲突"],
                            }
                        ],
                    },
                ],
            }
        ],
    )

    case = read_jsonl_dataset(dataset)[0]

    assert isinstance(case, MaintenanceEvalCase)
    assert case.query == ""
    assert case.group == "natural_response"
    assert case.dataset_source == "quality.jsonl"
    assert case.device_type == "motorcycle_engine"
    assert len(case.turns) == 2
    first_claim = case.turns[0].claim_constraints[0]
    assert first_claim.claim_id == "pump_parts"
    assert first_claim.allowed_sources[0].pages == [25]
    assert first_claim.allowed_sources[0].chunk_ids == ["chunk-25-table"]
    assert case.turns[0].style_expectation.allow_manual_lead is False
    assert case.turns[0].style_expectation.max_answer_chars == 260
    alternatives = case.turns[1].conflict_constraints[0].alternatives
    assert alternatives[0].allowed_sources[0].status == "active"
    assert alternatives[1].allowed_sources[0].path_ids == ["path-pump-torque"]


def test_read_jsonl_datasets_rejects_duplicate_ids_with_both_file_names(tmp_path: Path) -> None:
    first = _write_jsonl(tmp_path / "first.jsonl", [{"case_id": "same", "query": "问题一"}])
    second = _write_jsonl(tmp_path / "second.jsonl", [{"case_id": "same", "query": "问题二"}])

    with pytest.raises(ValueError) as exc_info:
        read_jsonl_datasets([first, second])

    message = str(exc_info.value)
    assert "same" in message
    assert "first.jsonl" in message
    assert "second.jsonl" in message


def test_turn_only_case_rejects_blank_turn_query(tmp_path: Path) -> None:
    dataset = _write_jsonl(
        tmp_path / "invalid.jsonl",
        [{"case_id": "blank-turn", "turns": [{"query": ""}]}],
    )

    with pytest.raises(ValueError, match=r"invalid\.jsonl:1.*turn 1.*query"):
        read_jsonl_dataset(dataset)


def test_cli_accepts_repeated_dataset_arguments() -> None:
    args = build_parser().parse_args(
        ["--dataset", "first.jsonl", "--dataset", "second.jsonl", "--mode", "fixture"]
    )

    assert args.dataset == ["first.jsonl", "second.jsonl"]


def test_read_jsonl_dataset_rejects_duplicate_ids_inside_one_file(tmp_path: Path) -> None:
    dataset = _write_jsonl(
        tmp_path / "duplicates.jsonl",
        [
            {"case_id": "same", "query": "问题一"},
            {"case_id": "same", "query": "问题二"},
        ],
    )

    with pytest.raises(ValueError, match=r"duplicates\.jsonl.*same.*lines 1 and 2"):
        read_jsonl_dataset(dataset)
