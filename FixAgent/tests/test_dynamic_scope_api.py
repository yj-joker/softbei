"""API preparation must apply dynamic scope before any retrieval path."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from api import main
from schemas.request import ChatRequest
from services.intent_router import IntentDecision
from services.retrieval.device_identity import DeviceCatalog, QueryContract
from services.retrieval.scope import ScopeDecision
from services.clarification.state import ClarificationStateStore, ResolvedScope


MANUAL_ID = "kdoc_2083453722632753154"


def test_server_resolved_section_scope_authorizes_only_matching_unknown_route() -> None:
    assert hasattr(main, "_apply_resolved_scope_authority")
    scope = ResolvedScope.from_constraints({
        "document_id": "manual-a",
        "allowed_section_ids": ["section-a"],
        "allowed_evidence_refs": ["chunk-a"],
    })
    assert scope is not None
    unknown = ScopeDecision(
        status="unknown",
        source="request_document",
        reason="unresolved_device_identity",
        document_id="manual-a",
    )

    authorized = main._apply_resolved_scope_authority(
        unknown,
        resolved_scope=scope,
        selected_document_id="manual-a",
        selected_section_id="section-a",
    )
    wrong_section = main._apply_resolved_scope_authority(
        unknown,
        resolved_scope=scope,
        selected_document_id="manual-a",
        selected_section_id="section-b",
    )
    conflict = main._apply_resolved_scope_authority(
        ScopeDecision(
            status="out_of_scope",
            source="request_document",
            reason="device_document_conflict",
            document_id="manual-a",
        ),
        resolved_scope=scope,
        selected_document_id="manual-a",
        selected_section_id="section-a",
    )

    assert authorized.status == "in_scope"
    assert authorized.source == "resolved_clarification"
    assert authorized.reason == "server_authoritative_scope"
    assert wrong_section.status == "unknown"
    assert conflict.status == "out_of_scope"


def _catalog() -> DeviceCatalog:
    return DeviceCatalog.from_manifests(
        [
            {
                "document_id": MANUAL_ID,
                "status": "ready",
                "device_type": "motorcycle-engine",
                "document_identity": {
                    "device_name": "摩托车发动机",
                    # Match the identity currently persisted by the real import.
                    # The intent model may still call the generic head "发动机";
                    # that category wording difference must not invalidate an
                    # explicitly selected document.
                    "device_category": "内燃机",
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
        component = "发动机"
        category = ""
        task_action = "find_cause"
        if "摩托车发动机气缸活塞" in message:
            span = "摩托车发动机气缸活塞"
            component = "气缸活塞"
        elif "卡车发动机" in message:
            carrier = "卡车"
            span = "卡车发动机"
        elif "履带起重机发动机" in message:
            carrier = "履带起重机"
            span = "履带起重机发动机"
        elif "摩托车发动机" in message:
            carrier = "摩托车"
            span = "摩托车发动机"
        elif "拆卸发动机" in message:
            span = "发动机"
            category = "机械装置"
            component = "放油螺栓"
            task_action = "formal_procedure"
        elif "右曲轴箱盖" in message:
            span = "右曲轴箱盖"
            component = "曲轴箱盖"
            category = "发动机部件"
        elif "起动电机" in message:
            span = "起动电机"
            component = "负极线"
        elif "离合器、机油泵" in message:
            span = "离合器、机油泵"
            component = "摩擦片分组件"
        elif "传动主副轴装配部件清单" in message:
            span = "传动主副轴装配部件清单"
            component = "圆柱销"
            category = "机械部件"
            carrier = "装配"
        elif "曲柄C标记" in message:
            span = "曲柄C标记和平衡轴D标记"
            component = "标记"
        return IntentDecision(
            target_layer="document_content",
            target_object="发动机异响",
            user_goal="查找原因",
            intent="fault_diagnosis",
            task_action=task_action,
            confidence=0.99,
            source="llm",
            raw_device_span=span,
            device_name=span,
            device_category=category or ("发动机" if span else ""),
            carrier_or_application=carrier,
            component=component,
            action="fault_diagnosis",
            orientation="右" if component == "曲轴箱盖" else "",
            risk_level="medium",
        )

    async def refine_query_contract(self, message):
        if "拆卸发动机" in message:
            return QueryContract.from_mapping(
                {
                    "component": "放油螺栓",
                    "action": "拆卸",
                },
                raw_query=message,
            )
        raise AssertionError("unexpected focused identity refinement")


class _IntentRouterMissingExplicitDevice:
    """Simulate both intent passes missing a carrier-qualified device span."""

    async def classify(self, message, **kwargs):
        return IntentDecision(
            target_layer="document_content",
            target_object="发动机异响",
            user_goal="查找原因",
            intent="fault_diagnosis",
            task_action="find_cause",
            confidence=0.99,
            source="llm",
            component="发动机",
            action="fault_diagnosis",
            risk_level="medium",
        )


def _prepare(
    monkeypatch,
    message: str,
    *,
    document_id: str | None = MANUAL_ID,
    intent_router=None,
):
    async def load_catalog():
        return _catalog()

    monkeypatch.setattr(
        main,
        "get_intent_router",
        lambda: intent_router or _IntentRouter(),
    )
    monkeypatch.setattr(main, "load_dynamic_device_catalog", load_catalog, raising=False)
    monkeypatch.setattr(main, "schedule_capture", lambda *args, **kwargs: None)
    class _SectionIndex:
        def build(self, vector_service):
            return None

        def find(self, query):
            titles = {
                "起动电机": "2.3 安装起动电机",
                "离合器、机油泵": "6.1 离合器、机油泵装配零件清单",
                "传动主副轴装配部件清单": "8.2 传动主副轴装配部件清单",
                "曲柄C标记": "8.7 安装曲柄与平衡轴",
            }
            title = next((value for key, value in titles.items() if key in query), "")
            if not title:
                return []
            return [
                SimpleNamespace(
                    section_id="sec-document-entity",
                    document_id=MANUAL_ID,
                    core_title=title.split(" ", 1)[-1],
                    full_title=title,
                )
            ]

    from services.retrieval.section_index import SectionTitleIndex

    monkeypatch.setattr(main, "get_vector_service", lambda: object())
    monkeypatch.setattr(SectionTitleIndex, "get_instance", classmethod(lambda cls: _SectionIndex()))
    request = ChatRequest(
        session_id="scope-api-test",
        message=message,
        document_id=document_id,
        stream=False,
    )
    return asyncio.run(main._prepare_chat_agent_input(request))


def test_structured_evidence_candidates_are_not_reopened_by_weaker_title_matches(monkeypatch) -> None:
    from services.retrieval.section_index import SectionRef, SectionTitleIndex

    class _ActionIntentRouter:
        async def classify(self, message, **kwargs):
            return IntentDecision(
                target_layer="operation_task",
                intent="procedure_planning",
                task_action="formal_procedure",
                confidence=0.99,
                source="llm",
                component="星门盖",
                raw_component_span="星门盖",
                action="拆卸",
                operation_intent=True,
                requires_manual_evidence=True,
                requires_knowledge_retrieval=True,
                allowed_tools=["knowledge_retrieval"],
            )

    async def load_catalog():
        return _catalog()

    class _SectionIndex:
        def build(self, vector_service):
            return None

        def find(self, query):
            return [
                SectionRef("sec-title-only", MANUAL_ID, "安装星门盖", "7.4 安装星门盖")
            ]

        def find_exact(self, query):
            return []

        def find_evidence(self, contract):
            return [
                SectionRef("sec-right", MANUAL_ID, "右侧耦联簇", "6.4 右侧耦联簇"),
                SectionRef("sec-left", MANUAL_ID, "左侧转子簇", "7.3 左侧转子簇"),
            ]

    monkeypatch.setattr(main, "get_intent_router", lambda: _ActionIntentRouter())
    monkeypatch.setattr(main, "load_dynamic_device_catalog", load_catalog, raising=False)
    monkeypatch.setattr(main, "schedule_capture", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "get_vector_service", lambda: object())
    monkeypatch.setattr(SectionTitleIndex, "get_instance", classmethod(lambda cls: _SectionIndex()))

    prepared = asyncio.run(main._prepare_chat_agent_input(ChatRequest(
        session_id="evidence-precedence-test",
        message="星门盖怎么拆卸",
        document_id=MANUAL_ID,
        stream=False,
    )))

    labels = {
        option["label"]
        for option in prepared.context["route_plan"]["clarification_options"]
    }
    assert labels == {"6.4 右侧耦联簇", "7.3 左侧转子簇"}


def test_explicit_truck_query_overrides_stale_motorcycle_document_selection(monkeypatch) -> None:
    prepared = _prepare(monkeypatch, "卡车发动机异响什么原因")

    assert prepared.context["scope_decision"]["status"] == "out_of_scope"
    assert prepared.context["scope_decision"]["reason"] == "device_document_conflict"
    assert prepared.context["retrieval_scope"] == {}


def test_unseen_device_query_is_not_pre_registered_but_still_blocked(monkeypatch) -> None:
    prepared = _prepare(monkeypatch, "履带起重机发动机异响什么原因")

    assert prepared.context["scope_decision"]["status"] == "out_of_scope"
    assert prepared.context["retrieval_scope"] == {}


def test_missed_explicit_device_cannot_be_authorized_by_selected_document(monkeypatch) -> None:
    prepared = _prepare(
        monkeypatch,
        "飞机发动机异响通常是什么原因",
        intent_router=_IntentRouterMissingExplicitDevice(),
    )

    assert prepared.context["scope_decision"]["status"] != "in_scope"
    assert prepared.context["retrieval_scope"] == {}


def test_matching_motorcycle_query_can_use_selected_motorcycle_document(monkeypatch) -> None:
    prepared = _prepare(monkeypatch, "摩托车发动机气缸活塞装配部件清单")

    assert prepared.context["scope_decision"]["status"] == "in_scope"
    assert prepared.context["scope_decision"]["document_id"] == MANUAL_ID
    assert prepared.context["retrieval_scope"] == {
        "document_id": MANUAL_ID,
        "device_type": "",
    }


def test_component_entity_does_not_conflict_with_selected_device_document(monkeypatch) -> None:
    prepared = _prepare(monkeypatch, "如何安装右曲轴箱盖")

    assert prepared.context["scope_decision"]["status"] == "in_scope"
    assert prepared.context["scope_decision"]["document_id"] == MANUAL_ID
    assert prepared.context["retrieval_scope"] == {
        "document_id": MANUAL_ID,
        "device_type": "",
    }


def test_device_span_with_component_suffix_uses_selected_matching_document(monkeypatch) -> None:
    prepared = _prepare(monkeypatch, "帮我查询摩托车发动机气缸活塞装配部件清单")

    assert prepared.context["scope_decision"]["status"] == "in_scope"
    assert prepared.context["scope_decision"]["document_id"] == MANUAL_ID
    assert prepared.context["retrieval_scope"] == {
        "document_id": MANUAL_ID,
        "device_type": "",
    }


def test_document_section_entity_is_not_rejected_as_an_unmatched_device(monkeypatch) -> None:
    prepared = _prepare(monkeypatch, "安装起动电机时负极线应该怎么装？")

    assert prepared.context["scope_decision"]["status"] == "in_scope"
    assert prepared.context["query_contract"]["raw_device_span"] == ""
    assert prepared.context["retrieval_scope"] == {
        "document_id": MANUAL_ID,
        "device_type": "",
        "parent_section_id": "sec-document-entity",
    }


def test_selected_document_allows_unqualified_dynamic_identity_head(monkeypatch) -> None:
    prepared = _prepare(monkeypatch, "拆卸发动机时排放机油要拆哪两个放油螺栓？")

    assert prepared.context["scope_decision"]["status"] == "in_scope"
    assert prepared.context["retrieval_scope"] == {
        "document_id": MANUAL_ID,
        "device_type": "",
    }


def test_composite_section_entity_is_resolved_from_selected_document_index(monkeypatch) -> None:
    prepared = _prepare(
        monkeypatch,
        "离合器、机油泵装配零件清单中摩擦片分组件的数量是多少？",
    )

    assert prepared.context["scope_decision"]["status"] == "in_scope"
    assert prepared.context["query_contract"]["raw_device_span"] == ""


def test_semantic_section_entity_is_not_required_to_repeat_the_full_span(monkeypatch) -> None:
    prepared = _prepare(
        monkeypatch,
        "安装完成后曲柄C标记和平衡轴D标记要怎么对齐？",
    )

    assert prepared.context["scope_decision"]["status"] == "in_scope"
    assert prepared.context["query_contract"]["raw_device_span"] == ""


def test_selected_document_section_entity_ignores_false_carrier_extracted_from_title(monkeypatch) -> None:
    prepared = _prepare(
        monkeypatch,
        "传动主副轴装配部件清单里GB119.2 φ2×5圆柱销数量是多少？",
    )

    assert prepared.context["scope_decision"]["status"] == "in_scope"
    assert prepared.context["query_contract"]["raw_device_span"] == ""
    assert prepared.context["query_contract"]["carrier_or_application"] == ""


def test_resolved_clarification_preserves_authoritative_query_contract(monkeypatch) -> None:
    from services.retrieval.section_index import SectionRef, SectionTitleIndex

    session_id = "resolved-contract-authority"
    original_query = "QX-47复合锁环的校准值是多少？"
    store = ClarificationStateStore()
    store.create(
        session_id,
        {
            "kind": "slot_disambiguation",
            "topic_signature": "parameter_lookup|复合锁环|QX-47",
            "original_query": original_query,
            "candidates": [
                {
                    "id": "A",
                    "label": "4.2 星门总成参数表",
                    "constraints": {
                        "document_id": MANUAL_ID,
                        "section_id": "sec-qx47",
                        "allowed_section_ids": ["sec-qx47"],
                        "allowed_evidence_refs": ["chunk-qx47"],
                    },
                }
            ],
        },
        route_snapshot={
            "action": "clarify_document",
            "intent": "knowledge_query",
            "task_action": "parameter_lookup",
            "query_contract": {
                "raw_query": original_query,
                "intent": "knowledge_query",
                "task_action": "parameter_lookup",
                "component": "复合锁环",
                "raw_component_span": "复合锁环",
                "part_spec": "QX-47",
                "requested_fields": ["校准值"],
            },
        },
    )

    class _DegradedRouter:
        calls = 0

        async def classify(self, message, **kwargs):
            self.calls += 1
            return IntentDecision(
                target_layer="document_content",
                target_object="复合锁环",
                user_goal="查询参数",
                intent="knowledge_query",
                task_action="parameter_lookup",
                confidence=0.99,
                source="llm",
                component="锁环",
                raw_component_span="锁环",
                part_spec="",
                requested_fields=[],
                allowed_tools=["knowledge_retrieval"],
            )

    router = _DegradedRouter()

    async def load_catalog():
        return _catalog()

    class _SectionIndex:
        def build(self, vector_service):
            return None

        def find(self, query):
            return [SectionRef("sec-qx47", MANUAL_ID, "星门总成参数表", "4.2 星门总成参数表")]

        def find_exact(self, query):
            return self.find(query)

        def find_evidence(self, contract):
            return self.find(contract.raw_query)

        def refs_for_scope(self, *, document_id, section_ids):
            return [
                ref for ref in self.find(original_query)
                if ref.document_id == document_id and ref.section_id in section_ids
            ]

    monkeypatch.setattr(main, "_clarification_state_store", lambda: store)
    monkeypatch.setattr(main, "_clarification_mode", lambda: "enforce")
    monkeypatch.setattr(main, "get_intent_router", lambda: router)
    monkeypatch.setattr(main, "load_dynamic_device_catalog", load_catalog, raising=False)
    monkeypatch.setattr(main, "get_vector_service", lambda: object())
    monkeypatch.setattr(main, "schedule_capture", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "clear_pending_clarification", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "clear_pending_document_selection", lambda *args, **kwargs: None)
    monkeypatch.setattr(SectionTitleIndex, "get_instance", classmethod(lambda cls: _SectionIndex()))

    prepared = asyncio.run(main._prepare_chat_agent_input(ChatRequest(
        session_id=session_id,
        message="A",
        stream=False,
    )))

    assert router.calls == 1
    assert prepared.user_message == original_query
    assert prepared.context["query_contract"]["component"] == "复合锁环"
    assert prepared.context["query_contract"]["part_spec"] == "QX-47"
    assert prepared.context["query_contract"]["requested_fields"] == ["校准值"]
    assert prepared.context["retrieval_scope"]["allowed_evidence_refs"] == ["chunk-qx47"]
