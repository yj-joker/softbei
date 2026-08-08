import json
from pathlib import Path

import pytest

from evaluation.blind_eval_governance import (
    freeze_dataset,
    leakage_check,
    materialize_question_only,
    verify_frozen_dataset,
)


def _row() -> dict:
    return {
        "schema_version": "3.0",
        "case_id": "blind-001",
        "split": "blind_test",
        "query": "一号发动机异常振动时应核对哪个部件关系？",
        "question_type": "relation_disambiguation",
        "graph_dependency": "required",
        "difficulty": "medium",
        "question_origin": "human_authored",
        "input_modality": "text",
        "image_inputs": [],
        "gold_answer": "核对张紧轮与轴承磨损故障的关系。",
        "gold_evidence": [{"chunk_id": "chunk-23", "relevance_grade": 3}],
        "author_id": "author-a",
        "reviewer_a_id": "reviewer-b",
        "reviewer_b_id": "reviewer-c",
        "review_status": "resolved",
    }


def test_materialized_questions_never_contain_gold_fields(tmp_path: Path):
    frozen = _row()
    output = materialize_question_only([frozen], tmp_path / "questions.jsonl")
    row = json.loads(output.read_text(encoding="utf-8").splitlines()[0])

    assert "gold_answer" not in row
    assert "gold_evidence" not in row
    assert "reviewer_a_id" not in row
    assert "graph_dependency" not in row
    assert "target_pages" not in row
    assert "target_section" not in row
    assert row["query"] == frozen["query"]


def test_freeze_requires_distinct_reviewers_and_detects_tampering(tmp_path: Path):
    root = tmp_path / "blind_v1"
    authoring = root / "authoring"
    authoring.mkdir(parents=True)
    (authoring / "questions_resolved.jsonl").write_text(
        json.dumps(_row(), ensure_ascii=False) + "\n", encoding="utf-8"
    )

    manifest = freeze_dataset(root)
    assert manifest["case_count"] == 1
    assert "scoring_cases.jsonl" in manifest["files"]
    assert verify_frozen_dataset(root)["verified"] is True

    with (root / "frozen" / "questions.jsonl").open("a", encoding="utf-8") as target:
        target.write("{}\n")
    with pytest.raises(ValueError, match="SHA-256"):
        verify_frozen_dataset(root)


def test_freeze_rejects_same_author_and_reviewer(tmp_path: Path):
    row = _row()
    row["reviewer_a_id"] = row["author_id"]
    root = tmp_path / "blind_v1"
    authoring = root / "authoring"
    authoring.mkdir(parents=True)
    (authoring / "questions_resolved.jsonl").write_text(
        json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="distinct"):
        freeze_dataset(root)


def test_leakage_check_rejects_existing_question_match():
    leaks = leakage_check(
        [{"case_id": "blind-001", "query": "同一个问题"}],
        ["同一个问题"],
    )

    assert leaks == [{"case_id": "blind-001", "similarity": 1.0}]
