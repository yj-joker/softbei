from __future__ import annotations

import asyncio

from services.clarification.graph_candidates import (
    build_graph_candidates,
    unresolved_graph_dimensions,
)
from services.clarification.models import RiskLevel
from services.clarification.policy import ClarificationDecisionEngine
from services.clarification.state import ClarificationStateStore, ResolvedScope
from services.intent_router import IntentDecision
from services.retrieval.device_identity import DeviceCatalog
from services.routing.models import RouteAction
from services.routing.orchestrator import SemanticRoutingOrchestrator
from services.routing.graph_candidate_provider import JavaGraphCandidateProvider


def _records() -> list[dict]:
    return [
        {
            "pathId": "path-engine-a-clutch",
            "deviceId": "engine-a",
            "deviceName": "设备甲",
            "componentId": "clutch-a",
            "componentName": "离合器",
            "faultId": "fault-a",
            "faultName": "异响",
            "matchScore": 4,
            "componentScore": 0.92,
            "documentId": "manual-a",
            "sourceChunkUid": "chunk-a",
            "evidenceRefs": ["chunk-a", "page:12"],
            "distinguishingFeatures": ["冷机明显"],
            "verificationActions": ["检查离合器间隙"],
            "solutions": [{"id": "solution-a", "title": "检查离合器"}],
        },
        {
            "pathId": "path-engine-b-clutch",
            "deviceId": "engine-b",
            "deviceName": "设备乙",
            "componentId": "clutch-b",
            "componentName": "离合器",
            "faultId": "fault-b",
            "faultName": "异响",
            "matchScore": 4,
            "componentScore": 0.91,
            "documentId": "manual-b",
            "sourceChunkUid": "chunk-b",
            "evidenceRefs": ["chunk-b", "page:18"],
            "distinguishingFeatures": ["热机明显"],
            "verificationActions": ["检查润滑状态"],
            "solutions": [{"id": "solution-b", "title": "检查润滑"}],
        },
    ]


def test_graph_records_keep_stable_path_and_provenance() -> None:
    candidates = build_graph_candidates(_records())

    assert [item.candidate_id for item in candidates] == [
        "graph:path-engine-a-clutch",
        "graph:path-engine-b-clutch",
    ]
    first = candidates[0]
    assert first.dimensions["device_id"] == "engine-a"
    assert first.dimensions["path_id"] == "path-engine-a-clutch"
    assert first.evidence_refs == ("chunk-a", "page:12")
    assert first.pages == (12,)
    assert first.distinguishing_features == ("冷机明显",)
    assert first.verification_actions == ("检查离合器间隙",)


def test_graph_candidates_ask_for_device_and_bind_exact_graph_scope() -> None:
    candidates = build_graph_candidates(_records())
    decision = ClarificationDecisionEngine().decide(
        candidates,
        risk_level=RiskLevel.HIGH,
        unresolved_dimensions=unresolved_graph_dimensions(candidates),
    )

    assert decision.should_clarify is True
    assert decision.question is not None
    assert decision.question.dimension == "device_id"
    option = next(item for item in decision.question.options if item.value == "engine-a")
    assert option.constraints["allowed_device_ids"] == ["engine-a"]
    assert option.constraints["allowed_component_ids"] == ["clutch-a"]
    assert option.constraints["allowed_fault_ids"] == ["fault-a"]
    assert option.constraints["allowed_path_ids"] == ["path-engine-a-clutch"]
    assert option.constraints["document_id"] == "manual-a"
    assert option.constraints["allowed_evidence_refs"] == ["chunk-a", "page:12"]


def test_resolved_scope_preserves_graph_limits_and_only_narrows() -> None:
    scope = ResolvedScope.from_constraints(
        {
            "document_id": "manual-a",
            "allowed_device_ids": ["engine-a"],
            "allowed_component_ids": ["clutch-a"],
            "allowed_fault_ids": ["fault-a"],
            "allowed_path_ids": ["path-engine-a-clutch"],
        }
    )

    assert scope is not None
    assert scope.allowed_device_ids == ("engine-a",)
    narrowed = scope.narrow(
        {
            "document_id": "manual-a",
            "allowed_device_ids": ["engine-a", "foreign"],
            "allowed_path_ids": ["path-engine-a-clutch", "foreign-path"],
        }
    )
    assert narrowed.allowed_device_ids == ("engine-a",)
    assert narrowed.allowed_path_ids == ("path-engine-a-clutch",)


def test_orchestrator_uses_graph_candidates_when_sections_are_unavailable() -> None:
    decision = IntentDecision(
        intent="fault_diagnosis",
        task_action="find_cause",
        requires_graph_search=True,
        operation_intent=False,
        component="离合器",
        raw_component_span="离合器",
        risk_level="high",
    )
    candidates = build_graph_candidates(_records())

    plan = asyncio.run(
        SemanticRoutingOrchestrator().build_plan(
            query="离合器异响是什么原因",
            decision=decision,
            catalog=DeviceCatalog(()),
            section_refs=(),
            query_contract=None,
            graph_candidates=candidates,
        )
    )

    assert plan.action == RouteAction.CLARIFY
    assert plan.clarification_kind == "graph_scope"
    assert plan.clarification_question == "请确认当前需要检修的是哪台设备？"
    assert len(plan.clarification_options) == 2


def test_graph_provider_uses_structured_contract_and_returns_normalized_candidates() -> None:
    calls: list[tuple[str, str, dict]] = []

    async def request_json(method: str, url: str, **kwargs):
        calls.append((method, url, kwargs))
        return {"data": _records()}

    provider = JavaGraphCandidateProvider(
        base_url="http://java.test",
        internal_token="token",
        request_json=request_json,
    )
    contract = __import__("services.retrieval.device_identity", fromlist=["QueryContract"]).QueryContract.from_mapping(
        {
            "component": "离合器",
            "raw_component_span": "离合器",
            "task_action": "find_cause",
        },
        raw_query="离合器异响是什么原因",
    )

    candidates = asyncio.run(provider.fetch_candidates(contract))

    assert len(candidates) == 2
    assert calls[0][0] == "POST"
    assert calls[0][1].endswith("/weixiu/path/candidates")
    assert calls[0][2]["json"]["queryContract"]["component"] == "离合器"
    assert calls[0][2]["json"]["allowedDocumentIds"] == []


def test_graph_candidate_preserves_section_and_provenance_status() -> None:
    candidates = build_graph_candidates([
        {
            "pathId": "path-1",
            "deviceId": "device-1",
            "componentId": "component-1",
            "documentId": "manual-1",
            "documentVersion": "batch-7",
            "sectionId": "manual-1:6.2",
            "sourceChunkUids": ["chunk-23", "chunk-24"],
            "pages": [23, 24],
            "graphScore": 0.91,
            "provenanceStatus": "complete",
        }
    ])

    candidate = candidates[0]
    assert candidate.document_version == "batch-7"
    assert candidate.section_id == "manual-1:6.2"
    assert candidate.evidence_refs == ("chunk-23", "chunk-24")
    assert candidate.source_chunk_uids == ("chunk-23", "chunk-24")
    assert candidate.pages == (23, 24)
    assert candidate.graph_score == 0.91
    assert candidate.retrieval_score == 0.91
    assert candidate.target_score == 0.91
    assert candidate.provenance_status == "complete"
