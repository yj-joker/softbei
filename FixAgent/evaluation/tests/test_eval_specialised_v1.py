import hashlib
import json
from pathlib import Path

from evaluation.maintenance_eval_evidence import score_turn_output
from evaluation.maintenance_eval_schema import (
    MaintenanceEvalTurn,
    read_jsonl_dataset,
    read_jsonl_datasets,
)


EVALUATION_DIR = Path(__file__).resolve().parents[1]
DATASET_PATH = EVALUATION_DIR / "datasets" / "eval_specialised_v1.jsonl"
FIXTURE_DIR = EVALUATION_DIR / "fixtures" / "rag_quality_v2_conflict"
ACTIVE_MANUAL_DOCUMENT_ID = "kdoc_2082825138343858177"


def test_specialised_v1_has_thirty_cases_with_expected_groups_and_turn_budget() -> None:
    cases = read_jsonl_dataset(DATASET_PATH)

    assert len(cases) == 30
    assert len({case.case_id for case in cases}) == 30
    assert sum(len(case.turns) if case.turns else 1 for case in cases) == 34
    assert sum(bool(case.turns) for case in cases) == 4

    groups = {case.group for case in cases}
    assert groups == {"scope_isolation", "evidence_quality", "natural_response"}
    assert sum(case.group == "scope_isolation" for case in cases) == 10
    assert sum(case.group == "evidence_quality" for case in cases) == 10
    assert sum(case.group == "natural_response" for case in cases) == 10


def test_specialised_v1_binds_the_frozen_active_manual_instead_of_placeholder_id() -> None:
    dataset_text = DATASET_PATH.read_text(encoding="utf-8")

    assert '"document_id":"manual-doc"' not in dataset_text
    assert f'"document_id":"{ACTIVE_MANUAL_DOCUMENT_ID}"' in dataset_text


def test_specialised_v1_case_ids_and_scope_traps_are_explicit() -> None:
    cases = read_jsonl_dataset(DATASET_PATH)
    by_id = {case.case_id: case for case in cases}

    assert {case.case_id for case in cases if case.group == "scope_isolation"} == {
        f"spec_scope_{index:03d}" for index in range(1, 11)
    }
    assert all(case.answerable is False for case in by_id.values() if case.group == "scope_isolation")
    assert all(case.expected_scope == "out_of_scope" for case in by_id.values() if case.group == "scope_isolation")
    assert sum(bool(case.device_type) for case in by_id.values() if case.group == "scope_isolation") >= 5
    assert sum(bool(case.document_id) for case in by_id.values() if case.group == "scope_isolation") >= 5
    assert all(case.forbidden_source_terms for case in by_id.values() if case.group == "scope_isolation")


def test_specialised_v1_evidence_cases_cover_partial_unsupported_and_conflict() -> None:
    cases = read_jsonl_dataset(DATASET_PATH)
    evidence_cases = [case for case in cases if case.group == "evidence_quality"]
    statuses = {case.expected_coverage_status for case in evidence_cases}

    assert statuses == {"partial", "unsupported", "conflict"}
    assert all(case.claim_constraints or case.conflict_constraints or case.turns for case in evidence_cases)
    assert sum(len(case.turns) > 1 for case in evidence_cases) == 2
    assert sum(bool(case.conflict_constraints) for case in evidence_cases) >= 2
    assert all(
        all(len(constraint.alternatives) >= 2 for constraint in case.conflict_constraints)
        for case in evidence_cases
    )


def test_specialised_v1_style_cases_exercise_source_modes_and_style_limits() -> None:
    cases = read_jsonl_dataset(DATASET_PATH)
    style_cases = [case for case in cases if case.group == "natural_response"]

    assert {case.source_request_mode for case in style_cases} == {"normal", "quote", "page"}
    assert all(case.style_expectation is not None or case.turns for case in style_cases)
    assert sum(len(case.turns) > 1 for case in style_cases) == 2
    assert any(case.style_expectation and case.style_expectation.max_answer_chars for case in style_cases)
    assert any(case.style_expectation and case.style_expectation.max_list_items for case in style_cases)


def test_conflict_fixture_declares_isolated_non_production_redis_guard() -> None:
    guard_path = FIXTURE_DIR / "guard.json"
    trace_path = FIXTURE_DIR / "conflict_trace.json"

    guard = json.loads(guard_path.read_text(encoding="utf-8"))
    trace = json.loads(trace_path.read_text(encoding="utf-8"))

    assert guard["required_env"]["RAG_EVAL_ISOLATED_STORE"] == "1"
    assert guard["redis"]["allow_production"] is False
    assert guard["redis"]["host"] in {"127.0.0.1", "localhost"}
    assert int(guard["redis"]["port"]) != 6379
    assert trace["fixture_name"] == "rag-quality-v2-conflict"
    assert len(trace["react_trace"]) >= 1


def test_conflict_fixture_proves_both_conflict_cases_are_deterministically_scoreable() -> None:
    cases = {case.case_id: case for case in read_jsonl_dataset(DATASET_PATH)}
    metadata = json.loads((FIXTURE_DIR / "conflict_trace.json").read_text(encoding="utf-8"))
    answers = {
        "spec_ev_007": "两个来源不一致：手册为20±2 N·m，后台规则为25±2 N·m，需要人工确认。",
        "spec_ev_008": "来源存在冲突：手册为60 N·m，图谱为65 N·m，需要人工确认。",
    }

    for case_id, answer in answers.items():
        case = cases[case_id]
        turn = MaintenanceEvalTurn(
            query=case.query,
            expected_scope=case.expected_scope,
            expected_coverage_status=case.expected_coverage_status,
            conflict_constraints=case.conflict_constraints,
            source_request_mode=case.source_request_mode,
            style_expectation=case.style_expectation,
        )
        score = score_turn_output(turn, answer, metadata)
        assert score.coverage_status == "conflict"
        assert score.conflict_handling_pass is True
        assert score.final_pass is True


def test_all_four_evaluation_datasets_form_one_unique_130_case_suite() -> None:
    paths = [
        EVALUATION_DIR / "maintenance_eval_dataset_v1.jsonl",
        EVALUATION_DIR / "maintenance_adversarial_v2.jsonl",
        EVALUATION_DIR / "maintenance_image_adversarial_v1.jsonl",
        DATASET_PATH,
    ]

    cases = read_jsonl_datasets(paths)

    assert len(cases) == 130
    assert len({case.case_id for case in cases}) == 130
    assert sum(len(case.turns) if case.turns else 1 for case in cases) == 134


def test_original_evaluation_datasets_are_unchanged() -> None:
    expected_hashes = {
        "maintenance_eval_dataset_v1.jsonl": "eb380e0ea8d3efcdf3658eea38e44b80c27cd90221852046f827e9f6f9899fb7",
        "maintenance_adversarial_v2.jsonl": "6f229171a9e7db7a0d469e9dd30044fd3568aeeb725e44cdb2aa918aaa19c01c",
        "maintenance_image_adversarial_v1.jsonl": "844edd8b6e812fc9c4ede75f675f59964ee54b941fba2f97706bef50f5271544",
    }
    for filename, expected_hash in expected_hashes.items():
        path = EVALUATION_DIR / filename
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == expected_hash
