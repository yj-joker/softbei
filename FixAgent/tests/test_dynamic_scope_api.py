"""API preparation must apply dynamic scope before any retrieval path."""

from __future__ import annotations

import asyncio

from api import main
from schemas.request import ChatRequest
from services.intent_router import IntentDecision
from services.retrieval.device_identity import DeviceCatalog


MANUAL_ID = "kdoc_2083453722632753154"


def _catalog() -> DeviceCatalog:
    return DeviceCatalog.from_manifests(
        [
            {
                "document_id": MANUAL_ID,
                "status": "ready",
                "device_type": "motorcycle-engine",
                "document_identity": {
                    "device_name": "摩托车发动机",
                    "device_category": "发动机",
                    "carrier_or_application": "摩托车",
                    "confidence": 0.96,
                },
            }
        ]
    )


class _IntentRouter:
    async def classify(self, message, **kwargs):
        carrier = ""
        span = ""
        if "卡车发动机" in message:
            carrier = "卡车"
            span = "卡车发动机"
        elif "履带起重机发动机" in message:
            carrier = "履带起重机"
            span = "履带起重机发动机"
        elif "摩托车发动机" in message:
            carrier = "摩托车"
            span = "摩托车发动机"
        return IntentDecision(
            target_layer="document_content",
            target_object="发动机异响",
            user_goal="查找原因",
            intent="fault_diagnosis",
            task_action="find_cause",
            confidence=0.99,
            source="llm",
            raw_device_span=span,
            device_name=span,
            device_category="发动机" if span else "",
            carrier_or_application=carrier,
            component="发动机",
            action="fault_diagnosis",
            risk_level="medium",
        )


def _prepare(monkeypatch, message: str, *, document_id: str | None = MANUAL_ID):
    async def load_catalog():
        return _catalog()

    monkeypatch.setattr(main, "get_intent_router", lambda: _IntentRouter())
    monkeypatch.setattr(main, "load_dynamic_device_catalog", load_catalog, raising=False)
    monkeypatch.setattr(main, "schedule_capture", lambda *args, **kwargs: None)
    request = ChatRequest(
        session_id="scope-api-test",
        message=message,
        document_id=document_id,
        stream=False,
    )
    return asyncio.run(main._prepare_chat_agent_input(request))


def test_explicit_truck_query_overrides_stale_motorcycle_document_selection(monkeypatch) -> None:
    prepared = _prepare(monkeypatch, "卡车发动机异响什么原因")

    assert prepared.context["scope_decision"]["status"] == "out_of_scope"
    assert prepared.context["scope_decision"]["reason"] == "device_document_conflict"
    assert prepared.context["retrieval_scope"] == {}


def test_unseen_device_query_is_not_pre_registered_but_still_blocked(monkeypatch) -> None:
    prepared = _prepare(monkeypatch, "履带起重机发动机异响什么原因")

    assert prepared.context["scope_decision"]["status"] == "out_of_scope"
    assert prepared.context["retrieval_scope"] == {}


def test_matching_motorcycle_query_can_use_selected_motorcycle_document(monkeypatch) -> None:
    prepared = _prepare(monkeypatch, "摩托车发动机气缸活塞装配部件清单")

    assert prepared.context["scope_decision"]["status"] == "in_scope"
    assert prepared.context["scope_decision"]["document_id"] == MANUAL_ID
    assert prepared.context["retrieval_scope"] == {
        "document_id": MANUAL_ID,
        "device_type": "",
    }
