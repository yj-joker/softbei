from __future__ import annotations

from services.response_policy import (
    GENERAL_AI,
    INSUFFICIENT_EVIDENCE,
    MAINTENANCE_AI_FALLBACK,
    BLOCKED_SCOPE,
    PENDING_RETRIEVAL,
    derive_response_policy,
)
from services.intent_router import IntentDecision


def test_general_knowledge_is_general_ai_without_retrieval_or_manual_images() -> None:
    decision = IntentDecision(
        target_layer="chat",
        intent="chat_social",
        task_action="general_answer",
        confidence=1.0,
        source="rules",
    )
    policy = derive_response_policy(
        decision,
        {"status": "unknown", "reason": "no_confirmed_scope"},
        {},
        query="给我讲讲高等数学中级数的概念",
    )

    assert policy.mode == GENERAL_AI
    assert policy.allow_knowledge_retrieval is False
    assert policy.images_allowed is False
    assert policy.disclaimer_required is False


def test_missing_device_document_allows_ai_fallback_with_disclaimer() -> None:
    decision = IntentDecision(
        target_layer="document_content",
        intent="fault_diagnosis",
        task_action="find_cause",
        confidence=0.9,
        source="rules",
    )
    policy = derive_response_policy(
        decision,
        {
            "status": "out_of_scope",
            "reason": "no_matching_device_document",
            "detected_device_type": "aircraft-piston-engine",
        },
        {},
        query="飞机发动机出现异响是什么原因？",
    )

    assert policy.mode == MAINTENANCE_AI_FALLBACK
    assert policy.allow_knowledge_retrieval is False
    assert policy.disclaimer_required is True
    assert policy.source_type == "ai"


def test_explicit_document_conflict_blocks_answer_and_images() -> None:
    decision = IntentDecision(
        target_layer="document_content",
        intent="fault_diagnosis",
        task_action="find_cause",
        confidence=0.9,
        source="rules",
    )
    policy = derive_response_policy(
        decision,
        {
            "status": "out_of_scope",
            "reason": "explicit_document_conflict",
            "detected_device_type": "aircraft-piston-engine",
        },
        {},
        query="根据摩托车手册分析飞机发动机异响",
    )

    assert policy.mode == BLOCKED_SCOPE
    assert policy.allow_knowledge_retrieval is False
    assert policy.allow_ai_fallback is False
    assert policy.images_allowed is False


def test_device_document_conflict_allows_safe_ai_fallback_without_manual_evidence() -> None:
    decision = IntentDecision(
        target_layer="document_content",
        intent="fault_diagnosis",
        task_action="find_cause",
        confidence=0.9,
        source="rules",
    )
    policy = derive_response_policy(
        decision,
        {
            "status": "out_of_scope",
            "reason": "device_document_conflict",
            "detected_device_type": "aircraft-piston-engine",
            "device_type": "motorcycle-engine",
        },
        {},
        query="飞机发动机有异响通常是什么原因",
    )

    assert policy.mode == MAINTENANCE_AI_FALLBACK
    assert policy.allow_ai_fallback is True
    assert policy.allow_knowledge_retrieval is False
    assert policy.manual_citation_allowed is False
    assert policy.images_allowed is False
    assert policy.disclaimer_required is True
    assert policy.source_type == "ai"


def test_high_risk_device_document_conflict_does_not_allow_ai_parameter_guessing() -> None:
    decision = IntentDecision(
        target_layer="document_content",
        intent="parameter_query",
        task_action="parameter_lookup",
        confidence=0.9,
        source="rules",
        requires_knowledge_retrieval=True,
        requires_manual_evidence=True,
    )
    policy = derive_response_policy(
        decision,
        {
            "status": "out_of_scope",
            "reason": "device_document_conflict",
            "detected_device_type": "aircraft-piston-engine",
            "device_type": "motorcycle-engine",
        },
        {},
        query="飞机发动机磁电机点火提前角是多少？",
    )

    assert policy.mode == INSUFFICIENT_EVIDENCE
    assert policy.allow_ai_fallback is False
    assert policy.allow_knowledge_retrieval is False
    assert policy.manual_citation_allowed is False
    assert policy.images_allowed is False


def test_in_scope_maintenance_waits_for_retrieval_before_deciding_evidence_status() -> None:
    decision = IntentDecision(
        target_layer="operation_task",
        intent="maintenance_guidance",
        task_action="repair_guidance",
        requires_knowledge_retrieval=True,
        requires_manual_evidence=True,
    )
    policy = derive_response_policy(
        decision,
        {"status": "in_scope", "reason": "document_confirmed"},
        {},
        query="如何安装右曲轴箱盖？",
    )

    assert policy.mode == PENDING_RETRIEVAL
    assert policy.allow_knowledge_retrieval is True


def test_in_scope_unsupported_fault_cause_allows_safe_ai_fallback_after_retrieval() -> None:
    decision = IntentDecision(
        target_layer="document_content",
        intent="fault_diagnosis",
        task_action="find_cause",
        requires_knowledge_retrieval=True,
        requires_manual_evidence=True,
    )
    policy = derive_response_policy(
        decision,
        {"status": "in_scope", "reason": "document_confirmed"},
        {"coverage_status": "unsupported"},
        query="摩托车发动机异响是什么原因？",
    )

    assert policy.mode == MAINTENANCE_AI_FALLBACK
    assert policy.allow_ai_fallback is True
    assert policy.manual_citation_allowed is False
    assert policy.images_allowed is False


def test_in_scope_unsupported_installation_still_blocks_ai_operation_steps() -> None:
    decision = IntentDecision(
        target_layer="operation_task",
        intent="maintenance_guidance",
        task_action="repair_guidance",
        requires_knowledge_retrieval=True,
        requires_manual_evidence=True,
    )
    policy = derive_response_policy(
        decision,
        {"status": "in_scope", "reason": "document_confirmed"},
        {"coverage_status": "unsupported"},
        query="如何安装未知型号的制动总泵？",
    )

    assert policy.mode == INSUFFICIENT_EVIDENCE
    assert policy.allow_ai_fallback is False


def test_unknown_explicit_document_is_blocked_instead_of_full_library_fallback() -> None:
    decision = IntentDecision(
        target_layer="document_content",
        intent="parameter_query",
        task_action="parameter_lookup",
        requires_knowledge_retrieval=True,
        requires_manual_evidence=True,
    )
    policy = derive_response_policy(
        decision,
        {"status": "out_of_scope", "reason": "unknown_document", "requested_document_id": "missing-manual"},
        {},
        query="missing-manual 中的扭矩是多少？",
    )

    assert policy.mode == BLOCKED_SCOPE
    assert policy.allow_knowledge_retrieval is False


def test_missing_device_document_does_not_allow_ai_to_invent_exact_parameters() -> None:
    decision = IntentDecision(
        target_layer="document_content",
        intent="parameter_query",
        task_action="parameter_lookup",
        requires_knowledge_retrieval=True,
        requires_manual_evidence=True,
    )
    policy = derive_response_policy(
        decision,
        {"status": "out_of_scope", "reason": "unsupported_device", "requested_device_type": "aircraft-piston-engine"},
        {},
        query="飞机发动机磁电机点火提前角是多少？",
    )

    assert policy.mode == INSUFFICIENT_EVIDENCE
    assert policy.allow_ai_fallback is False


def test_open_vocabulary_identity_conflict_uses_ai_fallback_without_retrieval() -> None:
    decision = IntentDecision(
        target_layer="document_content",
        intent="fault_diagnosis",
        task_action="find_cause",
        requires_knowledge_retrieval=True,
        raw_device_span="履带起重机发动机",
        device_category="发动机",
        carrier_or_application="履带起重机",
    )
    policy = derive_response_policy(
        decision,
        {
            "status": "out_of_scope",
            "reason": "identity_attribute_conflict",
            "detected_device_type": "履带起重机发动机",
            "identity_conflicts": ["carrier_or_application"],
        },
        {},
        query="履带起重机发动机异响是什么原因？",
    )

    assert policy.mode == MAINTENANCE_AI_FALLBACK
    assert policy.allow_ai_fallback is True
    assert policy.allow_knowledge_retrieval is False
    assert policy.manual_citation_allowed is False
    assert policy.images_allowed is False


def test_uncertain_device_scope_never_falls_back_to_full_library_retrieval() -> None:
    decision = IntentDecision(
        target_layer="document_content",
        intent="fault_diagnosis",
        task_action="find_cause",
        requires_knowledge_retrieval=True,
        raw_device_span="发动机",
        device_category="发动机",
    )
    policy = derive_response_policy(
        decision,
        {
            "status": "unknown",
            "reason": "identity_not_distinguishing",
            "detected_device_type": "发动机",
        },
        {},
        query="发动机异响是什么原因？",
    )

    assert policy.mode == MAINTENANCE_AI_FALLBACK
    assert policy.allow_knowledge_retrieval is False
    assert policy.disclaimer_required is True
