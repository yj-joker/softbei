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


def test_manual_step_image_request_overrides_visual_identification_bias() -> None:
    decision = asyncio.run(
        IntentRouter(_VisualBiasedLLM()).classify("拆卸凸轮轴前对齐正时标记，只要这一步对应的图")
    )

    assert decision.target_layer == "document_content"
    assert decision.intent == "knowledge_query"
    assert decision.requires_knowledge_retrieval is True
    assert decision.requires_image_understanding is False
