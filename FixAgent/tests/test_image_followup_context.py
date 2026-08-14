"""Image follow-up context restoration and projection regressions."""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import api.main as main
from schemas.request import ChatRequest
from services.grounded_turn_context import GroundedTurnContext, GroundedTurnContextStore


def _store() -> GroundedTurnContextStore:
    store = GroundedTurnContextStore()
    store.remember(
        "session-1",
        GroundedTurnContext(
            base_query="如何安装起动电机",
            resolved_query="如何安装起动电机",
            document_id="manual-1",
            device_type="motorcycle-engine",
            section_id="section-install-starter",
            section_title="2.3 安装起动电机",
            evidence_pages=(5,),
            source_chunk_ids=("step-install-starter",),
            procedure_scope_ids=("scope-install-starter",),
            created_at_ms=1,
        ),
    )
    return store


def test_image_followup_inherits_unique_previous_manual_scope(monkeypatch) -> None:
    monkeypatch.setattr(main, "_grounded_turn_context_store", _store)
    request = ChatRequest(session_id="session-1", message="步骤中的图片呢")
    context = {}

    resolved_query = main._restore_grounded_image_followup(
        request,
        request.message,
        context,
    )

    assert context["confirmed_document_id"] == "manual-1"
    assert context["confirmed_section_id"] == "section-install-starter"
    assert context["image_followup_inherited"] is True
    assert context["inherited_image_evidence"]["evidence_pages"] == [5]
    assert context["image_followup_base_query"] == "如何安装起动电机"
    assert resolved_query == "如何安装起动电机；用户追问：步骤中的图片呢"


def test_image_followup_without_previous_state_does_not_inherit(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "_grounded_turn_context_store",
        lambda: GroundedTurnContextStore(),
    )
    context = {}

    resolved = main._restore_grounded_image_followup(
        ChatRequest(session_id="missing", message="图片呢"),
        "图片呢",
        context,
    )

    assert resolved == ""
    assert "image_followup_inherited" not in context


@pytest.mark.parametrize(
    ("request_kwargs", "context", "expected_conflict"),
    [
        ({"document_id": "manual-2"}, {}, "document"),
        ({"device_type": "electric-bus"}, {}, "device_type"),
        ({}, {"confirmed_section_id": "section-other"}, "section"),
        ({}, {"resolved_scope": {"document_id": "manual-2"}}, "resolved_scope.document"),
        (
            {},
            {"resolved_scope": {"allowed_section_ids": ["section-other"]}},
            "resolved_scope.section",
        ),
        ({}, {"resolved_scope": {"pages": [17]}}, "resolved_scope.page"),
    ],
)
def test_image_followup_conflicts_do_not_override_current_scope(
    monkeypatch,
    request_kwargs: dict,
    context: dict,
    expected_conflict: str,
) -> None:
    monkeypatch.setattr(main, "_grounded_turn_context_store", _store)
    request = ChatRequest(
        session_id="session-1",
        message="图片呢",
        **request_kwargs,
    )

    resolved = main._restore_grounded_image_followup(request, request.message, context)

    assert resolved == ""
    assert context["image_followup_context_conflict"] is True
    assert expected_conflict in context["image_followup_context_conflict_fields"]
    assert context.get("image_followup_inherited") is not True


def test_targeted_image_query_and_new_upload_do_not_inherit(monkeypatch) -> None:
    monkeypatch.setattr(main, "_grounded_turn_context_store", _store)
    targeted_context = {}
    upload_context = {}

    targeted = main._restore_grounded_image_followup(
        ChatRequest(session_id="session-1", message="活塞环的图片呢"),
        "活塞环的图片呢",
        targeted_context,
    )
    uploaded = main._restore_grounded_image_followup(
        ChatRequest(
            session_id="session-1",
            message="图片呢",
            images=["http://example.test/new.png"],
        ),
        "图片呢",
        upload_context,
    )

    assert targeted == ""
    assert uploaded == ""
    assert targeted_context.get("image_followup_inherited") is not True
    assert upload_context.get("image_followup_inherited") is not True


def test_apply_inherited_image_evidence_projects_only_empty_ranges() -> None:
    input_context = {
        "resolved_image_query": "如何安装起动电机；用户追问：图片呢",
        "image_followup_base_query": "如何安装起动电机",
        "inherited_image_evidence": {
            "document_id": "manual-1",
            "device_type": "motorcycle-engine",
            "section_id": "section-install-starter",
            "section_title": "2.3 安装起动电机",
            "evidence_pages": [5],
            "source_chunk_ids": ["step-install-starter"],
            "procedure_scope_ids": ["scope-install-starter"],
        },
    }
    metadata = {"allowed_source_chunk_ids": []}

    main._apply_inherited_image_evidence(metadata, input_context)

    assert metadata["image_followup_inherited"] is True
    assert metadata["inherited_document_ids"] == ["manual-1"]
    assert metadata["inherited_non_image_source_ids"] == ["step-install-starter"]
    assert metadata["inherited_procedure_scope_ids"] == ["scope-install-starter"]
    assert metadata["_deterministic_answer_evidence_pages"] == [5]
    assert metadata["allowed_source_chunk_ids"] == ["step-install-starter"]


def test_apply_inherited_image_evidence_rejects_current_document_conflict() -> None:
    input_context = {
        "inherited_image_evidence": {
            "document_id": "manual-1",
            "evidence_pages": [5],
            "source_chunk_ids": ["step-install-starter"],
            "procedure_scope_ids": [],
        }
    }
    metadata = {
        "image_followup_inherited": True,
        "authorized_claim_evidence_bindings": [
            {"claim_id": "claim-1", "evidence_ids": ["manual:manual-2:step-2"]}
        ],
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": [{
                    "id": "step-2",
                    "metadata": {
                        "document_id": "manual-2",
                        "chunk_type": "text",
                    },
                }],
            }],
        }],
    }

    main._apply_inherited_image_evidence(metadata, input_context)

    assert metadata["image_followup_inherited"] is False
    assert "document" in metadata["image_followup_context_conflict_fields"]
    assert "inherited_document_ids" not in metadata


def test_consecutive_followups_keep_the_original_base_query(monkeypatch) -> None:
    store = _store()
    monkeypatch.setattr(main, "_grounded_turn_context_store", lambda: store)

    first_context = {}
    first = main._restore_grounded_image_followup(
        ChatRequest(session_id="session-1", message="图片呢"),
        "图片呢",
        first_context,
    )
    second_context = {}
    second = main._restore_grounded_image_followup(
        ChatRequest(session_id="session-1", message="步骤中的图片呢"),
        "步骤中的图片呢",
        second_context,
    )

    assert first.count("如何安装起动电机") == 1
    assert second.count("如何安装起动电机") == 1
    assert first_context["image_followup_base_query"] == "如何安装起动电机"
    assert second_context["image_followup_base_query"] == "如何安装起动电机"


def test_authorized_image_followup_keeps_previous_reliable_context(monkeypatch) -> None:
    store = _store()
    previous = store.load("session-1")
    monkeypatch.setattr(main, "_grounded_turn_context_store", lambda: store)
    metadata = {
        "image_followup_inherited": True,
        "image_selection_status": "ok",
        "route_plan": {"action": "grounded_retrieval"},
        "response_audit": {"passed": False},
        "coverage_status": "unsupported",
        "image_selection_contract": {
            "selected_count": 1,
            "target_document_ids": ["manual-1"],
            "target_non_image_source_ids": ["step-install-starter"],
            "target_procedure_scope_ids": ["scope-install-starter"],
            "selected_image_bindings": [{
                "source_chunk_id": "image-install-starter",
                "reason": "answer_evidence_binding",
            }],
        },
    }

    main._sync_grounded_turn_context(
        ChatRequest(session_id="session-1", message="步骤中的图片呢"),
        "步骤中的图片呢",
        metadata,
    )

    assert store.load("session-1") == previous


def test_prepare_chat_routes_followup_with_resolved_query_but_keeps_original_message(
    monkeypatch,
) -> None:
    class StopRouting(RuntimeError):
        pass

    captured = {}

    class FakeRouter:
        async def classify(self, query, *, images, context):
            captured["query"] = query
            captured["context"] = dict(context)
            raise StopRouting

    monkeypatch.setattr(main, "_grounded_turn_context_store", _store)
    monkeypatch.setattr(main, "_restore_trusted_pending_context", lambda session_id, context: context)
    monkeypatch.setattr(main, "load_pending_document_selection", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "_clarification_mode", lambda: "observe")
    monkeypatch.setattr(main, "get_intent_router", lambda: FakeRouter())
    request = ChatRequest(session_id="session-1", message="步骤中的图片呢")

    with pytest.raises(StopRouting):
        asyncio.run(main._prepare_chat_agent_input(request))

    assert captured["query"] == "如何安装起动电机；用户追问：步骤中的图片呢"
    assert captured["context"]["original_user_message"] == "步骤中的图片呢"
    assert captured["context"]["image_followup_inherited"] is True
