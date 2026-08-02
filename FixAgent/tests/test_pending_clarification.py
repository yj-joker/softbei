"""Recoverable clarification state regressions."""

from services.pending_clarification import (
    build_evidence_conflict_clarification,
    clear_pending_clarification,
    load_pending_clarification,
    remember_pending_clarification,
    resolve_pending_clarification,
)


def _evidence() -> list[dict]:
    return [
        {
            "evidence_id": "manual:manual-a:gap-a",
            "source_type": "manual",
            "text": "火花塞间隙为 0.7 mm",
            "source": {"chunk_id": "gap-a", "page": 3, "document_version": "v1"},
        },
        {
            "evidence_id": "manual:manual-b:gap-b",
            "source_type": "manual",
            "text": "火花塞间隙为 0.9 mm",
            "source": {"chunk_id": "gap-b", "page": 8, "document_version": "v2"},
        },
    ]


def _conflict() -> dict:
    return {
        "field": "火花塞间隙:clearance",
        "unit": "mm",
        "alternatives": [
            {"value": "0.7", "candidate_ids": ["gap-a"]},
            {"value": "0.9", "candidate_ids": ["gap-b"]},
        ],
        "candidate_ids": ["gap-a", "gap-b"],
    }


def test_real_conflict_builds_recoverable_clarification() -> None:
    pending = build_evidence_conflict_clarification(
        "火花塞间隙是多少？",
        _conflict(),
        _evidence(),
    )

    assert pending is not None
    assert pending["status"] == "awaiting_answer"
    assert pending["clarification_id"].startswith("clarification-")
    assert pending["kind"] == "evidence_conflict"
    assert pending["evidence_refs"] == ["manual:manual-a:gap-a", "manual:manual-b:gap-b"]
    assert [item["id"] for item in pending["alternatives"]] == ["A", "B"]


def test_different_semantic_fields_are_not_a_real_conflict() -> None:
    surface_conflict = {
        **_conflict(),
        "semantic_fields": ["序号", "扭矩"],
    }

    assert build_evidence_conflict_clarification(
        "M10 螺母扭矩是多少？",
        surface_conflict,
        _evidence(),
    ) is None


def test_answer_resolves_pending_conflict_by_source_page() -> None:
    pending = build_evidence_conflict_clarification(
        "火花塞间隙是多少？",
        _conflict(),
        _evidence(),
    )

    resolved = resolve_pending_clarification(
        {"pending_clarification": pending},
        "使用手册第8页的版本",
    )

    assert resolved is not None
    assert resolved["status"] == "resolved"
    assert resolved["selected_option_id"] == "B"
    assert resolved["selected_evidence_refs"] == ["manual:manual-b:gap-b"]


def test_answer_resolves_pending_conflict_by_option_id() -> None:
    pending = build_evidence_conflict_clarification(
        "火花塞间隙是多少？",
        _conflict(),
        _evidence(),
    )

    resolved = resolve_pending_clarification(
        {"pending_clarification": pending, "selected_clarification_option_id": "A"},
        "A",
    )

    assert resolved is not None
    assert resolved["selected_evidence_refs"] == ["manual:manual-a:gap-a"]


def test_same_page_different_versions_require_the_version_to_match() -> None:
    pending = build_evidence_conflict_clarification(
        "火花塞间隙是多少？",
        _conflict(),
        [
            {
                **_evidence()[0],
                "source": {
                    **_evidence()[0]["source"],
                    "page": 8,
                    "document_id": "manual-a",
                    "device_type": "engine-a",
                },
            },
            {
                **_evidence()[1],
                "source": {
                    **_evidence()[1]["source"],
                    "page": 8,
                    "document_id": "manual-b",
                    "device_type": "engine-b",
                },
            },
        ],
    )

    ambiguous = resolve_pending_clarification(
        {"pending_clarification": pending},
        "使用第8页的值",
    )
    resolved = resolve_pending_clarification(
        {"pending_clarification": pending},
        "使用第8页版本v2的值",
    )

    assert ambiguous is None
    assert resolved is not None
    assert resolved["selected_option_id"] == "B"
    assert pending["alternatives"][1]["document_id"] == "manual-b"
    assert pending["alternatives"][1]["device_type"] == "engine-b"


def test_server_state_overrides_tampered_client_alternatives() -> None:
    session_id = "trusted-conflict-session"
    pending = build_evidence_conflict_clarification(
        "火花塞间隙是多少？",
        _conflict(),
        _evidence(),
    )
    tampered = {
        **pending,
        "alternatives": [
            {
                "id": "A",
                "value": "999",
                "unit": "mm",
                "evidence_refs": ["manual:forged"],
                "source_labels": ["手册第999页"],
            }
        ],
    }

    try:
        remember_pending_clarification(session_id, pending)
        trusted = load_pending_clarification(
            session_id,
            client_pending=tampered,
        )
    finally:
        clear_pending_clarification(session_id)

    assert trusted == pending
    assert trusted["alternatives"][0]["value"] == "0.7"
    assert trusted["alternatives"][0]["evidence_refs"] == ["manual:manual-a:gap-a"]


def test_measurement_selection_does_not_match_numeric_substrings() -> None:
    pending = {
        "clarification_id": "clarification-torque",
        "kind": "evidence_conflict",
        "subject": "紧固扭矩",
        "alternatives": [
            {
                "id": "A",
                "value": "10",
                "unit": "N·m",
                "label": "10 N·m",
                "evidence_refs": ["manual:torque-10"],
                "source_labels": ["手册第10页，版本v1"],
            },
            {
                "id": "B",
                "value": "100",
                "unit": "N·m",
                "label": "100 N·m",
                "evidence_refs": ["manual:torque-100"],
                "source_labels": ["手册第11页，版本v2"],
            },
        ],
        "evidence_refs": ["manual:torque-10", "manual:torque-100"],
        "missing_identity_fields": ["文档版本"],
        "question": "请选择 A/B。",
        "status": "awaiting_answer",
        "original_query": "紧固扭矩是多少？",
    }

    resolved = resolve_pending_clarification(
        {"pending_clarification": pending},
        "采用 100 N·m",
    )

    assert resolved is not None
    assert resolved["selected_option_id"] == "B"
    assert resolved["selected_evidence_refs"] == ["manual:torque-100"]
