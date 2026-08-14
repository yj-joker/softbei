from __future__ import annotations

import asyncio
import json

from services.intent_router import IntentRouter


class _MaintenanceBiasedLLM:
    async def chat(self, messages, **kwargs):
        return {
            "content": json.dumps(
                {
                    "target_layer": "document_content",
                    "target_object": "设备",
                    "user_goal": "查询",
                    "intent": "knowledge_query",
                    "task_action": "general_answer",
                    "confidence": 0.99,
                },
                ensure_ascii=False,
            )
        }


class _VisualBiasedLLM:
    async def chat(self, messages, **kwargs):
        return {
            "content": json.dumps(
                {
                    "target_layer": "document_content",
                    "target_object": "正时标记对齐步骤的图",
                    "user_goal": "查看对应图片",
                    "intent": "visual_identification",
                    "task_action": "visual_compare",
                    "confidence": 0.99,
                },
                ensure_ascii=False,
            )
        }


class _IdentityAwareLLM:
    def __init__(self, raw_device_span: str = "卡车发动机"):
        self.raw_device_span = raw_device_span

    async def chat(self, messages, **kwargs):
        return {
            "content": json.dumps(
                {
                    "target_layer": "document_content",
                    "target_object": "发动机异响",
                    "user_goal": "查找原因",
                    "intent": "fault_diagnosis",
                    "task_action": "find_cause",
                    "confidence": 0.99,
                    "raw_device_span": self.raw_device_span,
                    "device_name": self.raw_device_span,
                    "device_category": "发动机",
                    "carrier_or_application": "卡车",
                    "manufacturer": "",
                    "model": "",
                    "component": "发动机",
                    "action": "fault_diagnosis",
                    "orientation": "",
                    "risk_level": "medium",
                },
                ensure_ascii=False,
            )
        }


class _IntentThenIdentityLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, **kwargs):
        self.calls += 1
        if self.calls == 1:
            payload = {
                "target_layer": "document_content",
                "target_object": "发动机异响",
                "user_goal": "查找原因",
                "intent": "fault_diagnosis",
                "task_action": "find_cause",
                "confidence": 0.95,
            }
        else:
            payload = {
                "raw_device_span": "飞机发动机",
                "device_name": "飞机发动机",
                "device_category": "发动机",
                "carrier_or_application": "飞机",
                "manufacturer": "",
                "model": "",
                "component": "发动机",
                "action": "fault_diagnosis",
                "orientation": "",
                "risk_level": "medium",
            }
        return {"content": json.dumps(payload, ensure_ascii=False)}


class _DoubleMissIdentityLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, **kwargs):
        self.calls += 1
        if self.calls == 1:
            payload = {
                "target_layer": "document_content",
                "target_object": "发动机异响",
                "user_goal": "查找原因",
                "intent": "fault_diagnosis",
                "task_action": "find_cause",
                "confidence": 0.95,
                "action": "fault_diagnosis",
            }
        else:
            payload = {
                "raw_device_span": "",
                "device_name": "",
                "device_category": "",
                "carrier_or_application": "",
                "manufacturer": "",
                "model": "",
                "component": "发动机",
                "action": "fault_diagnosis",
                "orientation": "",
                "risk_level": "medium",
            }
        return {"content": json.dumps(payload, ensure_ascii=False)}


class _RepairBiasedCauseLLM:
    async def chat(self, messages, **kwargs):
        return {
            "content": json.dumps(
                {
                    "target_layer": "operation_task",
                    "target_object": "飞机发动机异响",
                    "user_goal": "维修发动机",
                    "intent": "maintenance_guidance",
                    "task_action": "repair_guidance",
                    "confidence": 0.99,
                    "raw_device_span": "",
                    "device_name": "",
                    "device_category": "",
                    "carrier_or_application": "",
                    "manufacturer": "",
                    "model": "",
                    "component": "发动机",
                    "action": "飞机",
                    "orientation": "",
                    "risk_level": "medium",
                },
                ensure_ascii=False,
            )
        }


class _UnavailableLLM:
    async def chat(self, messages, **kwargs):
        raise RuntimeError("intent model unavailable")


class _EmptyRelationshipContractLLM:
    async def chat(self, messages, **kwargs):
        return {
            "content": json.dumps(
                {
                    "target_layer": "document_content",
                    "target_object": "",
                    "user_goal": "",
                    "intent": "knowledge_query",
                    "task_action": "general_answer",
                    "confidence": 0.99,
                    "raw_device_span": "",
                    "device_name": "",
                    "component": "",
                    "raw_component_span": "",
                    "fault": "",
                    "raw_fault_span": "",
                    "symptoms": [],
                },
                ensure_ascii=False,
            )
        }


def test_general_knowledge_overrides_llm_maintenance_bias() -> None:
    decision = asyncio.run(
        IntentRouter(_MaintenanceBiasedLLM()).classify("给我讲讲高等数学中级数的概念")
    )

    assert decision.target_layer == "chat"
    assert decision.intent == "chat_social"
    assert decision.chat_subtype == "general_knowledge"
    assert decision.requires_knowledge_retrieval is False


def test_identity_and_model_questions_are_classified_as_chat_subtypes() -> None:
    router = IntentRouter(_MaintenanceBiasedLLM())
    identity = asyncio.run(router.classify("你是谁"))
    model = asyncio.run(router.classify("你的底层是什么模型"))

    assert identity.chat_subtype == "assistant_identity"
    assert model.chat_subtype == "model_information"
    assert identity.requires_knowledge_retrieval is False
    assert model.requires_knowledge_retrieval is False


def test_maintenance_operation_still_uses_knowledge_route() -> None:
    decision = asyncio.run(IntentRouter(_MaintenanceBiasedLLM()).classify("如何安装右曲轴箱盖"))

    assert decision.intent == "maintenance_guidance"
    assert decision.target_layer == "operation_task"
    assert decision.requires_knowledge_retrieval is True
    assert decision.requires_graph_search is False


def test_manual_step_image_request_overrides_visual_identification_bias() -> None:
    decision = asyncio.run(
        IntentRouter(_VisualBiasedLLM()).classify("拆卸凸轮轴前对齐正时标记，只要这一步对应的图")
    )

    assert decision.target_layer == "document_content"
    assert decision.intent == "knowledge_query"
    assert decision.requires_knowledge_retrieval is True
    assert decision.requires_image_understanding is False


def test_same_intent_call_extracts_open_vocabulary_query_identity() -> None:
    decision = asyncio.run(
        IntentRouter(_IdentityAwareLLM()).classify("卡车发动机异响是什么原因？")
    )

    assert decision.raw_device_span == "卡车发动机"
    assert decision.device_category == "发动机"
    assert decision.carrier_or_application == "卡车"
    assert decision.component == "发动机"
    assert decision.action == "fault_diagnosis"


def test_missing_primary_identity_is_completed_by_focused_query_contract_extraction() -> None:
    llm = _IntentThenIdentityLLM()

    decision = asyncio.run(
        IntentRouter(llm).classify("飞机发动机有异响通常是什么原因")
    )

    assert llm.calls == 2
    assert decision.intent == "fault_diagnosis"
    assert decision.raw_device_span == "飞机发动机"
    assert decision.carrier_or_application == "飞机"
    assert decision.component == "发动机"


def test_double_identity_miss_never_signs_confirmed_absence() -> None:
    llm = _DoubleMissIdentityLLM()

    decision = asyncio.run(
        IntentRouter(llm).classify("飞机发动机有异响通常是什么原因")
    )

    assert llm.calls == 2
    assert decision.raw_device_span == ""
    assert decision.identity_resolution == ""


def test_cause_semantics_override_llm_repair_guidance_bias() -> None:
    decision = asyncio.run(
        IntentRouter(_RepairBiasedCauseLLM()).classify("飞机发动机的异响是什么原因？")
    )

    assert decision.intent == "fault_diagnosis"
    assert decision.target_layer == "document_content"
    assert decision.task_action == "find_cause"


def test_ungrounded_device_span_from_model_is_discarded() -> None:
    decision = asyncio.run(
        IntentRouter(_IdentityAwareLLM("飞机发动机")).classify("如何安装右曲轴箱盖")
    )

    assert decision.raw_device_span == ""
    assert decision.device_category == ""
    assert decision.carrier_or_application == ""


def test_relationship_lookup_recovers_quoted_fault_when_intent_model_is_unavailable() -> None:
    decision = asyncio.run(
        IntentRouter(_UnavailableLLM()).classify(
            "已确认故障为“拨叉损坏”，请沿故障关系定位所属部件，并结合手册说明应采取的处理措施。"
        )
    )

    assert decision.intent == "fault_diagnosis"
    assert decision.target_layer == "document_content"
    assert decision.task_action == "find_cause"
    assert decision.fault == "拨叉损坏"
    assert decision.raw_fault_span == "拨叉损坏"
    assert decision.symptoms == ["拨叉损坏"]
    assert decision.component == "拨叉"
    assert decision.raw_component_span == "拨叉"
    assert decision.raw_device_span == ""
    assert decision.device_name == ""


def test_relationship_lookup_repairs_high_confidence_but_empty_llm_contract() -> None:
    decision = asyncio.run(
        IntentRouter(_EmptyRelationshipContractLLM()).classify(
            '维修判断中发现"火花塞损坏"时，它对应设备的哪个部件？'
        )
    )

    assert decision.intent == "fault_diagnosis"
    assert decision.target_layer == "document_content"
    assert decision.task_action == "find_cause"
    assert decision.fault == "火花塞损坏"
    assert decision.raw_fault_span == "火花塞损坏"
    assert decision.symptoms == ["火花塞损坏"]
    assert decision.component == "火花塞"
    assert decision.raw_component_span == "火花塞"
    assert decision.raw_device_span == ""
    assert decision.device_name == ""


def test_relationship_lookup_splits_component_from_unsmooth_state() -> None:
    decision = asyncio.run(
        IntentRouter(_UnavailableLLM()).classify(
            "已确认故障为“离合器动作不顺畅”，请沿故障关系定位所属部件。"
        )
    )

    assert decision.fault == "离合器动作不顺畅"
    assert decision.raw_fault_span == "离合器动作不顺畅"
    assert decision.component == "离合器"
    assert decision.raw_component_span == "离合器"
