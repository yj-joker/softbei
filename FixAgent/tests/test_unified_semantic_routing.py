from __future__ import annotations

import asyncio
from types import SimpleNamespace

from agents.base_agent import AgentInput
from api import main
from schemas.request import ChatRequest
from services.intent_router import IntentDecision
from services.retrieval.device_identity import DeviceCatalog, QueryContract
from services.retrieval.section_index import SectionRef
from services.routing.document_candidate_resolver import DocumentCandidateResolver
from services.routing.document_selection import (
    clear_pending_document_selection,
    load_pending_document_selection,
    remember_pending_document_selection,
    resolve_pending_document_selection,
)
from services.routing.entity_resolver import EntityResolver
from services.routing.executor import RouteExecutor
from services.routing.evidence_gate import EvidenceDocumentGate
from services.routing.models import RouteAction
from services.routing.orchestrator import SemanticRoutingOrchestrator


def _catalog(*documents: tuple[str, str, str, str]) -> DeviceCatalog:
    return DeviceCatalog.from_manifests(
        {
            "document_id": document_id,
            "status": "ready",
            "document_identity": {
                "device_name": device_name,
                "device_category": category,
                "carrier_or_application": carrier,
                "confidence": 0.96,
            },
        }
        for document_id, device_name, category, carrier in documents
    )


def _technical_decision(**overrides) -> IntentDecision:
    payload = {
        "target_layer": "document_content",
        "target_object": "装配明细",
        "user_goal": "查询",
        "intent": "knowledge_query",
        "task_action": "document_explain",
        "confidence": 0.98,
        "source": "llm",
        "component": "星门耦联簇",
        "action": "查询",
    }
    payload.update(overrides)
    return IntentDecision(**payload)


def test_dynamic_section_role_demotes_unseen_component_phrase_without_keyword_lists() -> None:
    query = "查询星门耦联簇装配明细"
    contract = QueryContract.from_mapping(
        {
            "raw_device_span": "星门耦联簇",
            "device_name": "星门耦联簇",
            "device_category": "机械实体",
            "component": "星门耦联簇",
            "action": "查询",
        },
        raw_query=query,
    )
    sections = (
        SectionRef("section-a", "manual-a", "星门耦联簇装配明细", "9.7 星门耦联簇装配明细"),
    )

    resolution = EntityResolver().resolve(contract, _catalog(("manual-a", "苍穹涡轮装置", "涡轮装置", "苍穹平台")), sections)

    assert resolution.contract.raw_device_span == ""
    assert resolution.entity_role == "document_component"
    assert resolution.reason == "matched_dynamic_section"


def test_compound_device_and_dynamic_section_span_recovers_catalog_identity() -> None:
    query = "查询苍穹涡轮装置的星门耦联簇装配明细"
    catalog = _catalog(("manual-a", "苍穹涡轮装置", "涡轮装置", "苍穹平台"))
    contract = QueryContract.from_mapping(
        {
            "raw_device_span": "苍穹涡轮装置的星门耦联簇",
            "device_name": "苍穹涡轮装置的星门耦联簇",
            "device_category": "涡轮装置",
            "carrier_or_application": "苍穹平台",
            "component": "星门耦联簇",
        },
        raw_query=query,
    )
    sections = (
        SectionRef("section-a", "manual-a", "星门耦联簇装配明细", "9.7 星门耦联簇装配明细"),
    )

    entity = EntityResolver().resolve(contract, catalog, sections)
    candidates = DocumentCandidateResolver().resolve(entity.contract, catalog, sections)

    assert entity.entity_role == "device_identity"
    assert entity.contract.raw_device_span == "苍穹涡轮装置"
    assert entity.reason == "resolved_compound_dynamic_identity"
    assert candidates.action == RouteAction.GROUNDED_RETRIEVAL
    assert candidates.selected_document_id == "manual-a"


def test_demoted_operation_target_marks_identity_confirmed_absent_for_requested_document() -> None:
    query = "拆卸涡轮装置时排放介质"
    catalog = _catalog(("manual-a", "苍穹涡轮装置", "涡轮装置", "苍穹平台"))
    contract = QueryContract.from_mapping(
        {
            "raw_device_span": "涡轮装置",
            "device_name": "涡轮装置",
            "device_category": "涡轮装置",
            "component": "介质",
            "action": "拆卸",
            "task_action": "repair_guidance",
        },
        raw_query=query,
    )
    sections = (
        SectionRef("section-a", "manual-a", "拆卸涡轮装置", "3.2 拆卸涡轮装置"),
    )

    entity = EntityResolver().resolve(contract, catalog, sections)
    candidates = DocumentCandidateResolver().resolve(
        entity.contract,
        catalog,
        sections,
        request_document_id="manual-a",
    )

    assert entity.contract.raw_device_span == ""
    assert entity.contract.identity_resolution == "confirmed_absent"
    assert candidates.action == RouteAction.GROUNDED_RETRIEVAL
    assert candidates.selected_document_id == "manual-a"


def test_component_span_with_conjunction_is_demoted_by_dynamic_section_role() -> None:
    query = "安装完成后星门甲标记和耦联簇乙标记要怎么对齐"
    catalog = _catalog(("manual-a", "苍穹涡轮装置", "涡轮装置", "苍穹平台"))
    contract = QueryContract.from_mapping(
        {
            "raw_device_span": "星门甲标记和耦联簇乙标记",
            "device_name": "星门甲标记和耦联簇乙标记",
            "component": "星门, 耦联簇",
            "action": "安装",
            "task_action": "formal_procedure",
        },
        raw_query=query,
    )
    sections = (
        SectionRef("section-a", "manual-a", "对正装配流程", "9.4 对正装配流程"),
    )

    entity = EntityResolver().resolve(contract, catalog, sections)
    candidates = DocumentCandidateResolver().resolve(entity.contract, catalog, sections)

    assert entity.entity_role == "document_component"
    assert entity.contract.raw_device_span == ""
    assert entity.contract.identity_resolution == "confirmed_absent"
    assert candidates.action == RouteAction.GROUNDED_RETRIEVAL
    assert candidates.selected_document_id == "manual-a"


def test_unknown_explicit_device_is_not_demoted_or_bound_to_existing_document() -> None:
    query = "海岳运载器涡轮装置异响是什么原因"
    contract = QueryContract.from_mapping(
        {
            "raw_device_span": "海岳运载器涡轮装置",
            "device_name": "海岳运载器涡轮装置",
            "device_category": "涡轮装置",
            "carrier_or_application": "海岳运载器",
            "component": "涡轮装置",
        },
        raw_query=query,
    )

    resolution = EntityResolver().resolve(contract, _catalog(("manual-a", "苍穹涡轮装置", "涡轮装置", "苍穹平台")), ())
    candidates = DocumentCandidateResolver().resolve(resolution.contract, _catalog(("manual-a", "苍穹涡轮装置", "涡轮装置", "苍穹平台")), ())

    assert resolution.contract.raw_device_span == "海岳运载器涡轮装置"
    assert resolution.entity_role == "device_identity"
    assert candidates.action == RouteAction.AI_FALLBACK
    assert candidates.selected_document_id == ""


def test_unique_dynamic_section_match_binds_document_without_explicit_device() -> None:
    catalog = _catalog(
        ("manual-a", "苍穹涡轮装置", "涡轮装置", "苍穹平台"),
        ("manual-b", "深澜液压装置", "液压装置", "深澜平台"),
    )
    contract = QueryContract.from_mapping({"component": "星门耦联簇"}, raw_query="查询星门耦联簇装配明细")
    sections = (SectionRef("section-a", "manual-a", "星门耦联簇装配明细", "9.7 星门耦联簇装配明细"),)

    resolution = DocumentCandidateResolver().resolve(contract, catalog, sections)

    assert resolution.action == RouteAction.GROUNDED_RETRIEVAL
    assert resolution.selected_document_id == "manual-a"
    assert resolution.candidate_document_ids == ("manual-a",)


def test_multiple_document_matches_require_clarification() -> None:
    catalog = _catalog(
        ("manual-a", "苍穹涡轮装置", "涡轮装置", "苍穹平台"),
        ("manual-b", "深澜液压装置", "液压装置", "深澜平台"),
    )
    contract = QueryContract.from_mapping({"component": "共振隔离架"}, raw_query="查询共振隔离架装配明细")
    sections = (
        SectionRef("section-a", "manual-a", "共振隔离架装配明细", "4.1 共振隔离架装配明细"),
        SectionRef("section-b", "manual-b", "共振隔离架装配明细", "6.2 共振隔离架装配明细"),
    )

    resolution = DocumentCandidateResolver().resolve(contract, catalog, sections)

    assert resolution.action == RouteAction.CLARIFY_DOCUMENT
    assert resolution.selected_document_id == ""
    assert resolution.candidate_document_ids == ("manual-a", "manual-b")


def test_inventory_route_is_deterministic_and_never_becomes_ai_fallback() -> None:
    decision = IntentDecision(
        target_layer="knowledge_metadata",
        intent="knowledge_inventory",
        task_action="inventory_list",
        confidence=0.99,
        source="llm",
    )

    plan = asyncio.run(
        SemanticRoutingOrchestrator().build_plan(
            query="当前知识库有哪些文档",
            decision=decision,
            catalog=DeviceCatalog(()),
            section_refs=(),
        )
    )

    assert plan.action == RouteAction.KNOWLEDGE_INVENTORY
    assert plan.allowed_tools == ("knowledge_inventory",)
    assert plan.answer_source == "inventory_tool"
    assert plan.allow_ai_fallback is False


def test_route_plan_is_immutable() -> None:
    decision = _technical_decision()
    plan = asyncio.run(
        SemanticRoutingOrchestrator().build_plan(
            query="查询星门耦联簇装配明细",
            decision=decision,
            catalog=_catalog(("manual-a", "苍穹涡轮装置", "涡轮装置", "苍穹平台")),
            section_refs=(SectionRef("section-a", "manual-a", "星门耦联簇装配明细", "9.7 星门耦联簇装配明细"),),
        )
    )

    try:
        plan.selected_document_id = "manual-b"
    except (AttributeError, TypeError):
        pass
    else:
        raise AssertionError("RoutePlan must be immutable")


def test_inventory_executor_uses_tool_data_without_calling_a_model() -> None:
    decision = IntentDecision(
        target_layer="knowledge_metadata",
        intent="knowledge_inventory",
        task_action="inventory_list",
        confidence=0.99,
        source="llm",
    )
    plan = asyncio.run(
        SemanticRoutingOrchestrator().build_plan(
            query="当前知识库有哪些文档",
            decision=decision,
            catalog=DeviceCatalog(()),
            section_refs=(),
        )
    )

    class _InventoryTool:
        async def run(self):
            return SimpleNamespace(
                success=True,
                data={
                    "source": "test-inventory",
                    "documents": [
                        {
                            "document_id": "manual-a",
                            "manual_name": "苍穹装置检修手册",
                            "status": "ready",
                            "text_count": 18,
                            "image_count": 4,
                            "table_count": 2,
                        }
                    ],
                },
                error=None,
            )

    result = asyncio.run(RouteExecutor().execute(plan, inventory_tool=_InventoryTool()))

    assert result is not None
    assert result.tools_used == ("knowledge_inventory",)
    assert "《苍穹装置检修手册》" in result.message
    assert result.metadata["answer_source"] == "inventory_tool"
    assert result.metadata["knowledge_inventory_total"] == 1


def test_document_clarification_executor_lists_dynamic_candidates() -> None:
    catalog = _catalog(
        ("manual-a", "苍穹涡轮装置", "涡轮装置", "苍穹平台"),
        ("manual-b", "深澜液压装置", "液压装置", "深澜平台"),
    )
    plan = asyncio.run(
        SemanticRoutingOrchestrator().build_plan(
            query="查询共振隔离架装配明细",
            decision=_technical_decision(component="共振隔离架"),
            catalog=catalog,
            section_refs=(
                SectionRef("section-a", "manual-a", "共振隔离架装配明细", "4.1 共振隔离架装配明细"),
                SectionRef("section-b", "manual-b", "共振隔离架装配明细", "6.2 共振隔离架装配明细"),
            ),
        )
    )

    result = asyncio.run(RouteExecutor().execute(plan))

    assert result is not None
    assert result.tools_used == ()
    assert "苍穹涡轮装置" in result.message
    assert "深澜液压装置" in result.message
    assert result.metadata["route_action"] == "clarify_document"


def test_evidence_gate_rejects_foreign_document_from_retrieval_trace() -> None:
    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": [
                            {"content": "本手册证据", "metadata": {"document_id": "manual-a"}},
                            {"content": "串台证据", "metadata": {"document_id": "manual-b"}},
                        ],
                    }
                ]
            }
        ]
    }

    audit = EvidenceDocumentGate().audit(metadata, selected_document_id="manual-a")

    assert audit.accepted is False
    assert audit.evidence_document_ids == ("manual-a", "manual-b")
    assert audit.foreign_document_ids == ("manual-b",)


def test_evidence_gate_accepts_only_selected_document() -> None:
    metadata = {
        "_deterministic_answer_document_ids": ["manual-a"],
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": [
                            {"content": "本手册证据", "metadata": {"document_id": "manual-a"}},
                        ],
                    }
                ]
            }
        ],
    }

    audit = EvidenceDocumentGate().audit(metadata, selected_document_id="manual-a")

    assert audit.accepted is True
    assert audit.foreign_document_ids == ()


def test_api_inventory_route_precedes_response_policy_and_never_calls_llm(monkeypatch) -> None:
    decision = IntentDecision(
        target_layer="knowledge_metadata",
        intent="knowledge_inventory",
        task_action="inventory_list",
        confidence=0.99,
        source="llm",
    )
    plan = asyncio.run(
        SemanticRoutingOrchestrator().build_plan(
            query="当前知识库有哪些文档",
            decision=decision,
            catalog=DeviceCatalog(()),
            section_refs=(),
        )
    )

    class _InventoryTool:
        async def run(self):
            return SimpleNamespace(
                success=True,
                data={"documents": [], "source": "test-inventory"},
                error=None,
            )

    class _ForbiddenLLM:
        async def chat(self, *args, **kwargs):
            raise AssertionError("inventory route must not call an LLM")

    monkeypatch.setattr(main, "get_knowledge_inventory_tool", lambda: _InventoryTool())
    monkeypatch.setattr(main, "get_llm_service", lambda: _ForbiddenLLM())
    request = ChatRequest(session_id="inventory-route", message="当前知识库有哪些文档")
    input_data = AgentInput(
        user_message=request.message,
        session_id=request.session_id,
        context={
            "route_plan": plan.to_dict(),
            "response_policy": {"mode": "KNOWLEDGE_INVENTORY"},
            "intent_decision": decision.model_dump(),
        },
    )

    route_output = asyncio.run(main._try_route_plan_direct(request, input_data))
    policy_output = asyncio.run(main._try_response_policy_direct(request, input_data))

    assert route_output is not None
    assert route_output.tools_used == ["knowledge_inventory"]
    assert route_output.metadata["answer_source"] == "inventory_tool"
    assert policy_output is None


def test_document_selection_round_trip_uses_server_owned_candidates() -> None:
    session_id = "document-selection-round-trip"
    pending = {
        "status": "awaiting_answer",
        "original_query": "查询共振隔离架装配明细",
        "alternatives": [
            {"document_id": "manual-a", "display_name": "苍穹涡轮装置"},
            {"document_id": "manual-b", "display_name": "深澜液压装置"},
        ],
    }
    try:
        remember_pending_document_selection(session_id, pending)
        trusted = load_pending_document_selection(
            session_id,
            client_pending={
                **pending,
                "alternatives": [{"document_id": "forged", "display_name": "伪造文档"}],
            },
        )
        resolved = resolve_pending_document_selection(trusted, "2")
    finally:
        clear_pending_document_selection(session_id)

    assert trusted == pending
    assert resolved is not None
    assert resolved["selected_document_id"] == "manual-b"
    assert resolved["original_query"] == pending["original_query"]
