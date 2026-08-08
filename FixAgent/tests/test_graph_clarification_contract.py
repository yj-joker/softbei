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
from services.retrieval.device_identity import DeviceCatalog, DocumentIdentity, QueryContract
from services.retrieval.section_index import SectionRef
from services.routing.models import RouteAction
from services.routing.orchestrator import (
    SemanticRoutingOrchestrator,
    _narrow_explicit_graph_candidates,
)
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


def test_orchestrator_prefers_explicit_component_and_symptom_graph_path() -> None:
    decision = IntentDecision(
        intent="fault_diagnosis",
        task_action="find_cause",
        requires_graph_search=True,
        operation_intent=False,
        component="空气压缩机",
        raw_component_span="空气压缩机",
        symptoms=("不工作",),
        risk_level="medium",
    )
    records = [
        {
            "pathId": "path-air-compressor-off",
            "deviceId": "bus",
            "deviceName": "纯电动客车",
            "componentId": "air-compressor",
            "componentName": "空气压缩机",
            "faultId": "fault-off",
            "faultName": "空压机不工作",
            "graphScore": 0.90,
            "documentId": "manual-bus",
            "sourceChunkUid": "chunk-air",
            "provenanceStatus": "complete",
        },
        {
            "pathId": "path-power-steering",
            "deviceId": "bus",
            "deviceName": "纯电动客车",
            "componentId": "power-steering",
            "componentName": "助力油泵",
            "faultId": "fault-pump",
            "faultName": "电动泵未工作",
            "graphScore": 0.89,
            "documentId": "manual-bus",
            "sourceChunkUid": "chunk-pump",
            "provenanceStatus": "complete",
        },
    ]
    contract = __import__("services.retrieval.device_identity", fromlist=["QueryContract"]).QueryContract(
        raw_query="纯电动客车空气压缩机不工作是什么故障",
        intent="fault_diagnosis",
        task_action="find_cause",
        component="空气压缩机",
        raw_component_span="空气压缩机",
        symptoms=("不工作",),
    )

    document = __import__(
        "services.retrieval.device_identity", fromlist=["DocumentIdentity"]
    ).DocumentIdentity(
        document_id="manual-bus",
        device_name="纯电动客车",
        confidence=1.0,
    )
    plan = __import__("asyncio").run(
        SemanticRoutingOrchestrator().build_plan(
            query=contract.raw_query,
            decision=decision,
            catalog=DeviceCatalog((document,)),
            section_refs=(
                SectionRef(
                    section_id="section-pump",
                    document_id="manual-bus",
                    core_title="助力油泵故障处理",
                    full_title="助力油泵故障处理",
                    part_name="助力油泵",
                    evidence_refs=("chunk-pump",),
                    retrieval_score=0.88,
                ),
                SectionRef(
                    section_id="section-air",
                    document_id="manual-bus",
                    core_title="空气压缩机故障处理",
                    full_title="空气压缩机故障处理",
                    part_name="空气压缩机",
                    evidence_refs=("chunk-air",),
                    retrieval_score=0.95,
                ),
            ),
            query_contract=contract,
            graph_candidates=build_graph_candidates(records, query=contract.raw_query),
        )
    )

    assert plan.action == RouteAction.GROUNDED_RETRIEVAL
    assert plan.selected_graph_candidate_id == "graph:path-air-compressor-off"
    assert plan.graph_scope["allowed_component_ids"] == ["air-compressor"]
    assert plan.graph_scope["allowed_fault_ids"] == ["fault-off"]


def test_orchestrator_prioritizes_exact_fault_phrase_over_generic_symptom() -> None:
    decision = IntentDecision(
        intent="fault_diagnosis",
        task_action="find_cause",
        requires_graph_search=True,
        operation_intent=False,
        risk_level="medium",
    )
    contract = QueryContract(
        raw_query="compressor fuse and relay damaged",
        intent="fault_diagnosis",
        task_action="find_cause",
        component="compressor",
        raw_component_span="compressor",
        symptoms=("damaged",),
    )
    records = [
        {
            "pathId": "path-control-module",
            "deviceId": "bus",
            "deviceName": "electric bus",
            "componentId": "compressor",
            "componentName": "compressor",
            "faultId": "fault-control-module",
            "faultName": "control module damaged",
            "graphScore": 0.99,
            "documentId": "manual-bus",
            "sourceChunkUid": "chunk-compressor",
            "provenanceStatus": "complete",
        },
        {
            "pathId": "path-fuse-relay",
            "deviceId": "bus",
            "deviceName": "electric bus",
            "componentId": "compressor",
            "componentName": "compressor",
            "faultId": "fault-fuse-relay",
            "faultName": "fuse and relay damaged",
            "graphScore": 0.90,
            "documentId": "manual-bus",
            "sourceChunkUid": "chunk-compressor",
            "provenanceStatus": "complete",
        },
    ]
    document = DocumentIdentity(
        document_id="manual-bus",
        device_name="electric bus",
        confidence=1.0,
    )

    plan = asyncio.run(
        SemanticRoutingOrchestrator().build_plan(
            query=contract.raw_query,
            decision=decision,
            catalog=DeviceCatalog((document,)),
            section_refs=(),
            request_document_id="manual-bus",
            query_contract=contract,
            graph_candidates=build_graph_candidates(records, query=contract.raw_query),
        )
    )

    assert plan.action == RouteAction.GROUNDED_RETRIEVAL
    assert plan.selected_graph_candidate_id == "graph:path-fuse-relay"
    assert plan.graph_scope["allowed_path_ids"] == ["path-fuse-relay"]


def test_orchestrator_uses_explicit_component_when_exact_fault_name_is_duplicated() -> None:
    decision = IntentDecision(
        intent="maintenance_guidance",
        task_action="repair_guidance",
        requires_graph_search=False,
        operation_intent=True,
        risk_level="high",
    )
    contract = QueryContract(
        raw_query="摩托车发动机的曲轴与平衡轴出现轴承磨损时应如何处理",
        intent="maintenance_guidance",
        task_action="repair_guidance",
        raw_device_span="摩托车发动机",
        device_name="摩托车发动机",
        component="曲轴与平衡轴",
        raw_component_span="曲轴与平衡轴",
        symptoms=("轴承磨损",),
    )
    records = [
        {
            "pathId": "path-transmission-bearing",
            "deviceId": "engine",
            "deviceName": "摩托车发动机",
            "componentId": "transmission",
            "componentName": "传动装置",
            "faultId": "fault-transmission-bearing",
            "faultName": "轴承磨损",
            "graphScore": 0.90,
            "documentId": "manual-engine",
            "sourceChunkUid": "chunk-transmission",
            "provenanceStatus": "complete",
        },
        {
            "pathId": "path-crank-bearing",
            "deviceId": "engine",
            "deviceName": "摩托车发动机",
            "componentId": "crank",
            "componentName": "曲轴与平衡轴",
            "faultId": "fault-crank-bearing",
            "faultName": "轴承磨损",
            "graphScore": 0.90,
            "documentId": "manual-engine",
            "sourceChunkUid": "chunk-crank",
            "provenanceStatus": "complete",
        },
    ]

    candidates = build_graph_candidates(records, query=contract.raw_query)
    narrowed, explicit = _narrow_explicit_graph_candidates(
        contract,
        candidates,
    )

    assert [candidate.candidate_id for candidate in narrowed] == ["graph:path-crank-bearing"]
    assert explicit is not None
    assert explicit.dimensions["component_id"] == "crank"


def test_orchestrator_uses_assembly_context_when_fault_name_is_duplicated() -> None:
    contract = QueryContract(
        raw_query="摩托车发动机的传动装置出现轴承磨损时应如何处理",
        intent="maintenance_guidance",
        task_action="repair_guidance",
        component="轴承",
        raw_component_span="轴承",
        assembly_context="传动装置",
        symptoms=("轴承磨损",),
    )
    candidates = build_graph_candidates([
        {
            "pathId": "path-transmission-bearing",
            "deviceId": "engine",
            "deviceName": "摩托车发动机",
            "componentId": "transmission",
            "componentName": "传动装置",
            "faultId": "fault-transmission-bearing",
            "faultName": "轴承磨损",
            "graphScore": 0.90,
            "documentId": "manual-engine",
            "sourceChunkUid": "chunk-transmission",
            "provenanceStatus": "complete",
        },
        {
            "pathId": "path-crank-bearing",
            "deviceId": "engine",
            "deviceName": "摩托车发动机",
            "componentId": "crank",
            "componentName": "曲轴与平衡轴",
            "faultId": "fault-crank-bearing",
            "faultName": "轴承磨损",
            "graphScore": 0.90,
            "documentId": "manual-engine",
            "sourceChunkUid": "chunk-crank",
            "provenanceStatus": "complete",
        },
    ])

    narrowed, explicit = _narrow_explicit_graph_candidates(contract, candidates)

    assert [candidate.candidate_id for candidate in narrowed] == [
        "graph:path-transmission-bearing"
    ]
    assert explicit is not None
    assert explicit.dimensions["component_id"] == "transmission"


def test_orchestrator_matches_exact_fault_before_lossy_component_parse() -> None:
    decision = IntentDecision(
        intent="fault_diagnosis",
        task_action="find_cause",
        requires_graph_search=True,
        operation_intent=False,
        risk_level="medium",
    )
    contract = QueryContract(
        raw_query="空调压缩机高温故障，请诊断可能原因和处理建议。",
        intent="fault_diagnosis",
        task_action="find_cause",
        # Reproduces the lossy intent parse observed in graphrag_required_020.
        component="压缩机",
        raw_component_span="压缩机",
        symptoms=("高温故障",),
    )
    records = [
        {
            "pathId": "path-air-compressor-hot",
            "deviceId": "bus",
            "deviceName": "纯电动客车",
            "componentId": "air-compressor",
            "componentName": "空气压缩机",
            "faultId": "fault-air-hot",
            "faultName": "空压机温度高保护",
            "graphScore": 0.99,
            "documentId": "manual-bus",
            "sourceChunkUid": "chunk-air",
            "provenanceStatus": "complete",
        },
        {
            "pathId": "path-ac-compressor-hot",
            "deviceId": "bus",
            "deviceName": "纯电动客车",
            "componentId": "air-conditioner",
            "componentName": "空调",
            "faultId": "fault-ac-hot",
            "faultName": "压缩机高温故障",
            "graphScore": 0.90,
            "documentId": "manual-bus",
            "sourceChunkUid": "chunk-ac",
            "provenanceStatus": "complete",
        },
    ]

    plan = asyncio.run(
        SemanticRoutingOrchestrator().build_plan(
            query=contract.raw_query,
            decision=decision,
            catalog=DeviceCatalog((DocumentIdentity(
                document_id="manual-bus",
                device_name="纯电动客车",
                confidence=1.0,
            ),)),
            section_refs=(),
            request_document_id="manual-bus",
            query_contract=contract,
            graph_candidates=build_graph_candidates(records, query=contract.raw_query),
        )
    )

    assert plan.action == RouteAction.GROUNDED_RETRIEVAL
    assert plan.selected_graph_candidate_id == "graph:path-ac-compressor-hot"
    assert plan.graph_scope["allowed_path_ids"] == ["path-ac-compressor-hot"]


def test_orchestrator_combines_explicit_multi_target_paths_in_same_manual() -> None:
    decision = IntentDecision(
        intent="fault_diagnosis",
        task_action="find_cause",
        requires_graph_search=True,
        operation_intent=False,
        risk_level="medium",
    )
    contract = QueryContract.from_mapping(
        {
            "intent": "fault_diagnosis",
            "task_action": "find_cause",
            "targets": [
                {"target_id": "compressor", "component": "空气压缩机"},
                {"target_id": "ring", "component": "活塞环"},
            ],
        },
        raw_query="空气压缩机和活塞环分别可能有什么故障",
    )
    records = [
        {
            "pathId": "path-compressor",
            "deviceId": "bus",
            "deviceName": "纯电动客车",
            "componentId": "compressor",
            "componentName": "空气压缩机",
            "faultId": "fault-compressor",
            "faultName": "无法启动",
            "graphScore": 0.92,
            "documentId": "manual-bus",
            "sourceChunkUid": "chunk-compressor",
            "provenanceStatus": "complete",
        },
        {
            "pathId": "path-ring",
            "deviceId": "bus",
            "deviceName": "纯电动客车",
            "componentId": "ring",
            "componentName": "活塞环",
            "faultId": "fault-ring",
            "faultName": "磨损",
            "graphScore": 0.91,
            "documentId": "manual-bus",
            "sourceChunkUid": "chunk-ring",
            "provenanceStatus": "complete",
        },
    ]
    plan = asyncio.run(
        SemanticRoutingOrchestrator().build_plan(
            query=contract.raw_query,
            decision=decision,
            catalog=DeviceCatalog((DocumentIdentity(
                document_id="manual-bus",
                device_name="纯电动客车",
                confidence=1.0,
            ),)),
            section_refs=(),
            query_contract=contract,
            graph_candidates=build_graph_candidates(records, query=contract.raw_query),
        )
    )

    assert plan.action == RouteAction.GROUNDED_RETRIEVAL
    assert set(plan.graph_scope["allowed_path_ids"]) == {"path-compressor", "path-ring"}
    assert set(plan.graph_scope["allowed_component_ids"]) == {"compressor", "ring"}
    assert plan.clarification_options == ()


def test_orchestrator_keeps_relation_question_with_two_targets_in_same_manual() -> None:
    decision = IntentDecision(
        intent="fault_diagnosis",
        task_action="parameter_lookup",
        requires_graph_search=True,
        requires_manual_evidence=True,
        operation_intent=False,
        risk_level="low",
    )
    contract = QueryContract.from_mapping(
        {
            "intent": "fault_diagnosis",
            "task_action": "parameter_lookup",
            "symptoms": ["压缩压力低于最小值"],
        },
        raw_query="压缩压力低于最小值时怎么判断是不是活塞环问题？",
    )
    records = [
        {
            "pathId": "path-compression",
            "deviceId": "engine",
            "deviceName": "摩托车发动机",
            "componentId": "compression",
            "componentName": "压缩压力",
            "faultId": "fault-compression",
            "faultName": "压缩压力低于最小值",
            "graphScore": 0.92,
            "documentId": "manual-engine",
            "sourceChunkUid": "chunk-compression",
            "provenanceStatus": "complete",
        },
        {
            "pathId": "path-ring",
            "deviceId": "engine",
            "deviceName": "摩托车发动机",
            "componentId": "ring",
            "componentName": "活塞环",
            "faultId": "fault-ring",
            "faultName": "活塞环问题",
            "graphScore": 0.91,
            "documentId": "manual-engine",
            "sourceChunkUid": "chunk-ring",
            "provenanceStatus": "complete",
        },
    ]

    plan = asyncio.run(
        SemanticRoutingOrchestrator().build_plan(
            query=contract.raw_query,
            decision=decision,
            catalog=DeviceCatalog((DocumentIdentity(
                document_id="manual-engine",
                device_name="摩托车发动机",
                confidence=1.0,
            ),)),
            section_refs=(),
            query_contract=contract,
            graph_candidates=build_graph_candidates(records, query=contract.raw_query),
        )
    )

    assert plan.action == RouteAction.GROUNDED_RETRIEVAL
    assert set(plan.graph_scope["allowed_path_ids"]) == {"path-compression", "path-ring"}
    assert plan.clarification_options == ()


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


def test_graph_score_is_used_when_java_omits_legacy_retrieval_scores() -> None:
    candidate = build_graph_candidates([
        {
            "pathId": "path-score",
            "deviceId": "device-1",
            "componentId": "component-1",
            "faultId": "fault-1",
            "componentName": "空气压缩机",
            "faultName": "空压机不工作",
            "documentId": "manual-1",
            "sourceChunkUid": "chunk-1",
            "graphScore": 0.91,
            "provenanceStatus": "complete",
        }
    ])[0]

    assert candidate.graph_score == 0.91
    assert candidate.retrieval_score == 0.91
    assert candidate.target_score == 0.91


def test_graph_candidate_timeout_is_unavailable_not_empty() -> None:
    async def request_json(*args, **kwargs):
        raise TimeoutError("candidate timeout")

    provider = JavaGraphCandidateProvider(
        base_url="http://java.test",
        request_json=request_json,
    )
    contract = __import__("services.retrieval.device_identity", fromlist=["QueryContract"]).QueryContract.from_mapping(
        {"component": "离合器", "task_action": "find_cause"},
        raw_query="离合器异响是什么原因",
    )

    candidates = asyncio.run(provider.fetch_candidates(contract))

    assert candidates == ()
    assert provider.retrieval_status["status"] == "unavailable"
    assert provider.retrieval_status["reason"] == "candidate_timeout"


def test_parameter_lookup_is_not_applicable_and_skips_candidate_request() -> None:
    calls = []

    async def request_json(*args, **kwargs):
        calls.append((args, kwargs))
        return {"data": _records()}

    provider = JavaGraphCandidateProvider(
        base_url="http://java.test",
        request_json=request_json,
    )
    contract = __import__("services.retrieval.device_identity", fromlist=["QueryContract"]).QueryContract.from_mapping(
        {"component": "离合器", "task_action": "parameter_lookup"},
        raw_query="离合器间隙是多少",
    )

    candidates = asyncio.run(provider.fetch_candidates(contract))

    assert candidates == ()
    assert calls == []
    assert provider.retrieval_status["status"] == "not_applicable"


def test_fault_diagnosis_contract_is_not_short_circuited_by_parameter_action() -> None:
    calls = []

    async def request_json(*args, **kwargs):
        calls.append((args, kwargs))
        return {"data": _records()}

    provider = JavaGraphCandidateProvider(
        base_url="http://java.test",
        request_json=request_json,
    )
    contract = __import__("services.retrieval.device_identity", fromlist=["QueryContract"]).QueryContract.from_mapping(
        {
            "intent": "fault_diagnosis",
            "component": "clutch",
            "symptoms": ["abnormal noise"],
            "task_action": "parameter_lookup",
        },
        raw_query="low compression is it a piston ring fault",
    )

    candidates = asyncio.run(provider.fetch_candidates(contract))

    assert candidates
    assert len(calls) == 1
    assert provider.retrieval_status["status"] == "found"


def test_unscoped_graph_evidence_query_omits_empty_allow_lists() -> None:
    calls = []

    async def request_json(method: str, url: str, **kwargs):
        calls.append(kwargs)
        return {"data": {"records": []}}

    provider = JavaGraphCandidateProvider(
        base_url="http://java.test",
        request_json=request_json,
    )

    asyncio.run(provider.retrieve_path_evidence(
        fault_description="abnormal noise",
        component_description="clutch",
    ))

    body = calls[0]["json"]
    assert "allowedPathIds" not in body
    assert "allowedDeviceIds" not in body
    assert "allowedComponentIds" not in body
    assert "allowedFaultIds" not in body


def test_non_diagnostic_request_skips_graph_candidate_query() -> None:
    calls = []

    async def request_json(*args, **kwargs):
        calls.append((args, kwargs))
        return {"data": _records()}

    provider = JavaGraphCandidateProvider(
        base_url="http://java.test",
        request_json=request_json,
    )
    contract = __import__("services.retrieval.device_identity", fromlist=["QueryContract"]).QueryContract.from_mapping(
        {"component": "火花塞", "task_action": "repair_guidance"},
        raw_query="安装火花塞时应该怎么预紧和拧紧？",
    )

    candidates = asyncio.run(provider.fetch_candidates(contract))

    assert candidates == ()
    assert calls == []
    assert provider.retrieval_status["status"] == "not_applicable"
    assert provider.retrieval_status["reason"] == "non_diagnostic_request"


def test_default_candidate_timeout_covers_java_embedding_roundtrip() -> None:
    provider = JavaGraphCandidateProvider(request_json=lambda *args, **kwargs: {})

    assert provider.timeout_seconds >= 20.0


def test_provider_retrieves_normalized_path_evidence_with_allow_lists() -> None:
    calls = []

    async def request_json(method: str, url: str, **kwargs):
        calls.append((method, url, kwargs))
        return {
            "data": {
                "records": [{
                    "pathId": "kgpath:device-1:component-1:fault-1",
                    "nodeIds": ["device-1", "component-1", "fault-1"],
                    "relationshipTypes": ["OWNS", "CAUSES"],
                    "deviceId": "device-1",
                    "deviceName": "设备甲",
                    "componentId": "component-1",
                    "componentName": "离合器",
                    "faultId": "fault-1",
                    "faultName": "异响",
                        "documentId": "manual-1",
                        "documentVersion": "v1",
                        "sectionId": "sec-1",
                        "sourceChunkUids": ["chunk-1"],
                        "pages": [12],
                        "graphRevision": "graph-v1",
                    "provenanceStatus": "complete",
                    "matchScore": 3,
                }]
            }
        }

    provider = JavaGraphCandidateProvider(
        base_url="http://java.test",
        request_json=request_json,
    )
    batch = asyncio.run(provider.retrieve_path_evidence(
        fault_description="异响",
        allowed_path_ids=["kgpath:device-1:component-1:fault-1"],
        allowed_device_ids=["device-1"],
        allowed_component_ids=["component-1"],
        allowed_fault_ids=["fault-1"],
    ))

    assert batch.status == "found"
    assert batch.evidence[0].qualification == "qualified"
    body = calls[0][2]["json"]
    assert body["allowedPathIds"] == ["kgpath:device-1:component-1:fault-1"]
    assert body["allowedDeviceIds"] == ["device-1"]


def test_provider_path_timeout_returns_unavailable_batch() -> None:
    async def request_json(*args, **kwargs):
        raise TimeoutError("path timeout")

    provider = JavaGraphCandidateProvider(
        base_url="http://java.test",
        request_json=request_json,
    )

    batch = asyncio.run(provider.retrieve_path_evidence(
        fault_description="异响",
        allowed_device_ids=["device-1"],
    ))

    assert batch.status == "unavailable"
    assert batch.reason == "graph_path_timeout"
