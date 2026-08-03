"""Schema and loaders for deterministic maintenance quality evaluation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence


VALID_SOURCE_TYPES = {"manual", "domain_rule", "graph"}
VALID_SCOPE_STATES = {"", "in_scope", "out_of_scope", "unknown"}
VALID_COVERAGE_STATES = {"", "complete", "partial", "unsupported", "conflict"}
VALID_SOURCE_MODES = {"normal", "quote", "page"}


@dataclass
class AllowedSource:
    source_type: str
    document_id: str = ""
    document_version: str = ""
    pages: list[int] = field(default_factory=list)
    chunk_ids: list[str] = field(default_factory=list)
    rule_id: str = ""
    status: str = ""
    node_ids: list[str] = field(default_factory=list)
    relationship_types: list[str] = field(default_factory=list)
    path_ids: list[str] = field(default_factory=list)


@dataclass
class ClaimConstraint:
    claim_id: str
    answer_patterns: list[str] = field(default_factory=list)
    evidence_patterns: list[str] = field(default_factory=list)
    forbidden_without_evidence_patterns: list[str] = field(default_factory=list)
    missing_disclosure_patterns: list[str] = field(default_factory=list)
    allowed_sources: list[AllowedSource] = field(default_factory=list)


@dataclass
class ConflictAlternative:
    value_patterns: list[str] = field(default_factory=list)
    unit_patterns: list[str] = field(default_factory=list)
    allowed_sources: list[AllowedSource] = field(default_factory=list)


@dataclass
class ConflictConstraint:
    subject: str
    alternatives: list[ConflictAlternative] = field(default_factory=list)
    disclosure_patterns: list[str] = field(default_factory=list)


@dataclass
class StyleExpectation:
    allow_manual_lead: bool = False
    max_answer_chars: int | None = None
    max_list_items: int | None = None


@dataclass
class MaintenanceEvalTurn:
    query: str
    task_type: str = ""
    intent_action: str = ""
    target_section: str = ""
    target_pages: list[int] = field(default_factory=list)
    answerable: bool | None = None
    required_nuggets: list[str] = field(default_factory=list)
    optional_nuggets: list[str] = field(default_factory=list)
    forbidden_claims: list[str] = field(default_factory=list)
    expected_step_order: list[str] = field(default_factory=list)
    expected_images: list[dict[str, Any]] = field(default_factory=list)
    expected_image_order: list[int] = field(default_factory=list)
    step_image_mapping: list[dict[str, Any]] = field(default_factory=list)
    forbidden_images: list[dict[str, Any]] = field(default_factory=list)
    expected_scope: str = ""
    expected_coverage_status: str = ""
    claim_constraints: list[ClaimConstraint] = field(default_factory=list)
    conflict_constraints: list[ConflictConstraint] = field(default_factory=list)
    forbidden_source_terms: list[str] = field(default_factory=list)
    source_request_mode: str = "normal"
    style_expectation: StyleExpectation | None = None
    candidate_answer: str = ""
    candidate_images: list[dict[str, Any]] = field(default_factory=list)
    candidate_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MaintenanceEvalCase:
    case_id: str
    query: str = ""
    task_type: str = ""
    intent_action: str = ""
    target_section: str = ""
    target_pages: list[int] = field(default_factory=list)
    answerable: bool = True
    required_nuggets: list[str] = field(default_factory=list)
    optional_nuggets: list[str] = field(default_factory=list)
    forbidden_claims: list[str] = field(default_factory=list)
    expected_step_order: list[str] = field(default_factory=list)
    expected_images: list[dict[str, Any]] = field(default_factory=list)
    expected_image_order: list[int] = field(default_factory=list)
    step_image_mapping: list[dict[str, Any]] = field(default_factory=list)
    forbidden_images: list[dict[str, Any]] = field(default_factory=list)
    gold_evidence: list[dict[str, Any]] = field(default_factory=list)
    difficulty: str = ""
    trap_type: list[str] = field(default_factory=list)
    candidate_answer: str = ""
    candidate_images: list[dict[str, Any]] = field(default_factory=list)
    candidate_metadata: dict[str, Any] = field(default_factory=dict)
    group: str = ""
    turns: list[MaintenanceEvalTurn] = field(default_factory=list)
    dataset_source: str = ""
    device_type: str = ""
    document_id: str = ""
    document_version: str = ""
    manual_type: str = ""
    expected_scope: str = ""
    expected_coverage_status: str = ""
    claim_constraints: list[ClaimConstraint] = field(default_factory=list)
    conflict_constraints: list[ConflictConstraint] = field(default_factory=list)
    forbidden_source_terms: list[str] = field(default_factory=list)
    source_request_mode: str = "normal"
    style_expectation: StyleExpectation | None = None


def _as_int_list(value: Any) -> list[int]:
    values = value if isinstance(value, list) else [] if value is None else [value]
    parsed: list[int] = []
    for item in values:
        try:
            if str(item).strip():
                parsed.append(int(item))
        except (TypeError, ValueError):
            continue
    return parsed


def _as_optional_int(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"expected integer, got {value!r}") from exc


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [item.strip() for item in re.split(r"[;|]", text) if item.strip()]


def _as_dict_list(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    return []


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "是", "可回答"}


def _as_optional_bool(value: Any) -> bool | None:
    return None if value is None else _as_bool(value)


def _validated_choice(value: Any, allowed: set[str], field_name: str, default: str = "") -> str:
    text = str(value or default).strip()
    if text not in allowed:
        raise ValueError(f"invalid {field_name}: {text!r}")
    return text


def _allowed_source_from_dict(data: Mapping[str, Any]) -> AllowedSource:
    source_type = _validated_choice(data.get("source_type"), VALID_SOURCE_TYPES, "source_type")
    return AllowedSource(
        source_type=source_type,
        document_id=str(data.get("document_id") or "").strip(),
        document_version=str(data.get("document_version") or "").strip(),
        pages=_as_int_list(data.get("pages")),
        chunk_ids=_as_str_list(data.get("chunk_ids")),
        rule_id=str(data.get("rule_id") or "").strip(),
        status=str(data.get("status") or "").strip(),
        node_ids=_as_str_list(data.get("node_ids")),
        relationship_types=_as_str_list(data.get("relationship_types")),
        path_ids=_as_str_list(data.get("path_ids")),
    )


def _allowed_sources(value: Any) -> list[AllowedSource]:
    return [_allowed_source_from_dict(item) for item in _as_dict_list(value)]


def _claim_constraint_from_dict(data: Mapping[str, Any]) -> ClaimConstraint:
    claim_id = str(data.get("claim_id") or "").strip()
    if not claim_id:
        raise ValueError("claim_constraint missing claim_id")
    return ClaimConstraint(
        claim_id=claim_id,
        answer_patterns=_as_str_list(data.get("answer_patterns")),
        evidence_patterns=_as_str_list(data.get("evidence_patterns")),
        forbidden_without_evidence_patterns=_as_str_list(data.get("forbidden_without_evidence_patterns")),
        missing_disclosure_patterns=_as_str_list(data.get("missing_disclosure_patterns")),
        allowed_sources=_allowed_sources(data.get("allowed_sources")),
    )


def _conflict_alternative_from_dict(data: Mapping[str, Any]) -> ConflictAlternative:
    return ConflictAlternative(
        value_patterns=_as_str_list(data.get("value_patterns") or data.get("values")),
        unit_patterns=_as_str_list(data.get("unit_patterns") or data.get("units")),
        allowed_sources=_allowed_sources(data.get("allowed_sources")),
    )


def _conflict_constraint_from_dict(data: Mapping[str, Any]) -> ConflictConstraint:
    subject = str(data.get("subject") or "").strip()
    if not subject:
        raise ValueError("conflict_constraint missing subject")
    alternatives = [
        _conflict_alternative_from_dict(item)
        for item in _as_dict_list(data.get("alternatives"))
    ]
    if len(alternatives) < 2:
        raise ValueError(f"conflict_constraint {subject!r} requires at least two alternatives")
    return ConflictConstraint(
        subject=subject,
        alternatives=alternatives,
        disclosure_patterns=_as_str_list(data.get("disclosure_patterns")),
    )


def _style_expectation(value: Any) -> StyleExpectation | None:
    if not isinstance(value, Mapping):
        return None
    return StyleExpectation(
        allow_manual_lead=_as_bool(value.get("allow_manual_lead"), default=False),
        max_answer_chars=_as_optional_int(value.get("max_answer_chars")),
        max_list_items=_as_optional_int(value.get("max_list_items")),
    )


def _turn_from_dict(data: Mapping[str, Any]) -> MaintenanceEvalTurn:
    return MaintenanceEvalTurn(
        query=str(data.get("query") or data.get("question") or "").strip(),
        task_type=str(data.get("task_type") or "").strip(),
        intent_action=str(data.get("intent_action") or "").strip(),
        target_section=str(data.get("target_section") or "").strip(),
        target_pages=_as_int_list(data.get("target_pages")),
        answerable=_as_optional_bool(data.get("answerable")),
        required_nuggets=_as_str_list(data.get("required_nuggets")),
        optional_nuggets=_as_str_list(data.get("optional_nuggets")),
        forbidden_claims=_as_str_list(data.get("forbidden_claims")),
        expected_step_order=_as_str_list(data.get("expected_step_order")),
        expected_images=_as_dict_list(data.get("expected_images")),
        expected_image_order=_as_int_list(data.get("expected_image_order")),
        step_image_mapping=_as_dict_list(data.get("step_image_mapping")),
        forbidden_images=_as_dict_list(data.get("forbidden_images")),
        expected_scope=_validated_choice(data.get("expected_scope"), VALID_SCOPE_STATES, "expected_scope"),
        expected_coverage_status=_validated_choice(
            data.get("expected_coverage_status"), VALID_COVERAGE_STATES, "expected_coverage_status"
        ),
        claim_constraints=[
            _claim_constraint_from_dict(item) for item in _as_dict_list(data.get("claim_constraints"))
        ],
        conflict_constraints=[
            _conflict_constraint_from_dict(item) for item in _as_dict_list(data.get("conflict_constraints"))
        ],
        forbidden_source_terms=_as_str_list(data.get("forbidden_source_terms")),
        source_request_mode=_validated_choice(
            data.get("source_request_mode"), VALID_SOURCE_MODES, "source_request_mode", default="normal"
        ),
        style_expectation=_style_expectation(data.get("style_expectation")),
        candidate_answer=str(data.get("candidate_answer") or ""),
        candidate_images=_as_dict_list(data.get("candidate_images")),
        candidate_metadata=dict(data.get("candidate_metadata") or {}),
    )


def _case_from_dict(data: Mapping[str, Any]) -> MaintenanceEvalCase:
    return MaintenanceEvalCase(
        case_id=str(data.get("case_id") or data.get("id") or "").strip(),
        query=str(data.get("query") or data.get("question") or "").strip(),
        task_type=str(data.get("task_type") or "").strip(),
        intent_action=str(data.get("intent_action") or "").strip(),
        target_section=str(data.get("target_section") or "").strip(),
        target_pages=_as_int_list(data.get("target_pages")),
        answerable=_as_bool(data.get("answerable"), default=True),
        required_nuggets=_as_str_list(data.get("required_nuggets")),
        optional_nuggets=_as_str_list(data.get("optional_nuggets")),
        forbidden_claims=_as_str_list(data.get("forbidden_claims")),
        expected_step_order=_as_str_list(data.get("expected_step_order")),
        expected_images=_as_dict_list(data.get("expected_images")),
        expected_image_order=_as_int_list(data.get("expected_image_order")),
        step_image_mapping=_as_dict_list(data.get("step_image_mapping")),
        forbidden_images=_as_dict_list(data.get("forbidden_images")),
        gold_evidence=_as_dict_list(data.get("gold_evidence")),
        difficulty=str(data.get("difficulty") or "").strip(),
        trap_type=_as_str_list(data.get("trap_type")),
        candidate_answer=str(data.get("candidate_answer") or ""),
        candidate_images=_as_dict_list(data.get("candidate_images")),
        candidate_metadata=dict(data.get("candidate_metadata") or {}),
        group=str(data.get("group") or "").strip(),
        turns=[_turn_from_dict(item) for item in _as_dict_list(data.get("turns"))],
        device_type=str(data.get("device_type") or "").strip(),
        document_id=str(data.get("document_id") or "").strip(),
        document_version=str(data.get("document_version") or "").strip(),
        manual_type=str(data.get("manual_type") or "").strip(),
        expected_scope=_validated_choice(data.get("expected_scope"), VALID_SCOPE_STATES, "expected_scope"),
        expected_coverage_status=_validated_choice(
            data.get("expected_coverage_status"), VALID_COVERAGE_STATES, "expected_coverage_status"
        ),
        claim_constraints=[
            _claim_constraint_from_dict(item) for item in _as_dict_list(data.get("claim_constraints"))
        ],
        conflict_constraints=[
            _conflict_constraint_from_dict(item) for item in _as_dict_list(data.get("conflict_constraints"))
        ],
        forbidden_source_terms=_as_str_list(data.get("forbidden_source_terms")),
        source_request_mode=_validated_choice(
            data.get("source_request_mode"), VALID_SOURCE_MODES, "source_request_mode", default="normal"
        ),
        style_expectation=_style_expectation(data.get("style_expectation")),
    )


def read_jsonl_dataset(path: Path) -> list[MaintenanceEvalCase]:
    cases: list[MaintenanceEvalCase] = []
    seen_lines: dict[str, int] = {}
    with path.open("r", encoding="utf-8") as source:
        for line_no, line in enumerate(source, start=1):
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            data = json.loads(text)
            if not isinstance(data, Mapping):
                raise ValueError(f"{path}:{line_no} expected an object")
            try:
                case = _case_from_dict(data)
            except ValueError as exc:
                raise ValueError(f"{path}:{line_no} {exc}") from exc
            if not case.case_id:
                raise ValueError(f"{path}:{line_no} missing case_id")
            previous_line = seen_lines.get(case.case_id)
            if previous_line is not None:
                raise ValueError(
                    f"{path} duplicate case_id {case.case_id!r} on lines {previous_line} and {line_no}"
                )
            if not case.query and not case.turns:
                raise ValueError(f"{path}:{line_no} missing query or turns")
            for turn_index, turn in enumerate(case.turns, start=1):
                if not turn.query:
                    raise ValueError(f"{path}:{line_no} turn {turn_index} missing query")
            case.dataset_source = path.name
            seen_lines[case.case_id] = line_no
            cases.append(case)
    return cases


def read_jsonl_datasets(paths: Sequence[Path]) -> list[MaintenanceEvalCase]:
    cases: list[MaintenanceEvalCase] = []
    seen: dict[str, Path] = {}
    for raw_path in paths:
        path = Path(raw_path)
        for case in read_jsonl_dataset(path):
            previous = seen.get(case.case_id)
            if previous is not None:
                raise ValueError(
                    f"duplicate case_id {case.case_id!r} in {previous.name} and {path.name}"
                )
            seen[case.case_id] = path
            cases.append(case)
    return cases
