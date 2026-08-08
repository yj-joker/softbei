import asyncio

from services.intent_router import IntentDecision
from services.retrieval.device_identity import DeviceCatalog, DocumentIdentity, QueryContract
from services.retrieval.section_index import SectionRef
from services.routing.models import RouteAction
from services.routing.orchestrator import SemanticRoutingOrchestrator
from services.routing.executor import RouteExecutor


def _catalog():
    return DeviceCatalog((
        DocumentIdentity("doc-a", "设备甲", confidence=0.95),
        DocumentIdentity("doc-b", "设备乙", confidence=0.95),
    ))


def _decision():
    return IntentDecision(
        intent="knowledge_query", task_action="formal_procedure", confidence=0.9,
        target_layer="document_content", operation_intent=True,
        requires_manual_evidence=True, allowed_tools=["knowledge_retrieval"],
    )


def test_orchestrator_emits_one_generic_clarify_action_for_multi_document_sections():
    refs = (
        SectionRef("sec-a", "doc-a", "离合器", "6.1 离合器"),
        SectionRef("sec-b", "doc-b", "离合器", "6.1 离合器"),
    )
    plan = asyncio.run(SemanticRoutingOrchestrator().build_plan(
        query="如何安装离合器", decision=_decision(), catalog=_catalog(), section_refs=refs,
        query_contract=QueryContract.from_mapping(_decision().model_dump(), raw_query="如何安装离合器"),
    ))
    assert plan.action is RouteAction.CLARIFY
    assert plan.clarification_kind == "document_selection"
    assert len(plan.clarification_options) == 2


def test_executor_does_not_run_downstream_tools_for_clarify():
    refs = (
        {"id": "A", "label": "设备甲", "constraints": {"document_id": "doc-a"}},
        {"id": "B", "label": "设备乙", "constraints": {"document_id": "doc-b"}},
    )
    from services.routing.models import RoutePlan
    plan = RoutePlan(
        action=RouteAction.CLARIFY,
        intent="knowledge_query", task_action="formal_procedure",
        query_contract=QueryContract.from_mapping({}, raw_query="如何安装离合器"),
        entity_role="document_component", candidate_document_ids=("doc-a", "doc-b"),
        selected_document_id="", allowed_tools=(), answer_source="clarification",
        allow_ai_fallback=False, reason="ambiguous", clarification_options=refs,
        clarification_kind="document_selection",
    )
    execution = asyncio.run(RouteExecutor().execute(plan, inventory_tool=object()))
    assert execution is not None
    assert execution.tools_used == ()
    assert execution.metadata["pending_clarification"]["kind"] == "document_selection"
    assert execution.metadata["pending_clarification"]["status"] == "awaiting_answer"
    assert "文档 ID：doc-a" in execution.message


def test_same_document_ambiguous_sections_use_section_clarification():
    catalog = DeviceCatalog((DocumentIdentity("doc-a", "设备甲", confidence=0.95),))
    refs = (
        SectionRef("sec-install", "doc-a", "离合器安装", "6.1 离合器安装"),
        SectionRef("sec-remove", "doc-a", "离合器拆卸", "6.2 离合器拆卸"),
    )
    plan = asyncio.run(SemanticRoutingOrchestrator().build_plan(
        query="离合器怎么处理", decision=_decision(), catalog=catalog, section_refs=refs,
        query_contract=QueryContract.from_mapping(_decision().model_dump(), raw_query="离合器怎么处理"),
    ))
    assert plan.action is RouteAction.CLARIFY
    assert plan.clarification_kind == "slot_disambiguation"
    assert {item["label"] for item in plan.clarification_options} == {"6.1 离合器安装", "6.2 离合器拆卸"}
    execution = asyncio.run(RouteExecutor().execute(plan))
    assert "请确认更符合哪一个候选范围" in execution.message
    assert "文档 ID" not in execution.message


def test_exact_full_section_title_selects_dominant_candidate_without_clarification():
    catalog = DeviceCatalog((DocumentIdentity("doc-a", "设备甲", confidence=0.95),))
    refs = (
        SectionRef("sec-pump", "doc-a", "机油泵", "6.5 机油泵"),
        SectionRef("sec-list", "doc-a", "离合器、机油泵装配零件清单", "6.2 离合器、机油泵装配零件清单"),
    )
    query = "摩托车发动机离合器、机油泵装配零件清单"
    plan = asyncio.run(SemanticRoutingOrchestrator().build_plan(
        query=query, decision=_decision(), catalog=catalog, section_refs=refs,
        query_contract=QueryContract.from_mapping(_decision().model_dump(), raw_query=query),
    ))
    assert plan.action is RouteAction.GROUNDED_RETRIEVAL
