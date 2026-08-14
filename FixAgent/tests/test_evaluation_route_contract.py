import asyncio

from services.intent_router import IntentDecision
from services.retrieval.device_identity import DeviceCatalog, QueryContract
from services.routing.models import EntityResolution
from services.routing.orchestrator import SemanticRoutingOrchestrator


def test_frozen_evaluation_contract_cannot_be_replaced_by_entity_resolution() -> None:
    frozen = QueryContract.from_mapping(
        {
            "intent": "knowledge_query",
            "task_action": "find_cause",
            "component": "油泵座垫",
            "identity_resolution": "confirmed_absent",
        },
        raw_query="油泵座垫变形怎么办",
    )
    mutated = QueryContract.from_mapping(
        {
            **frozen.to_dict(),
            "raw_device_span": "摩托车发动机",
            "device_name": "摩托车发动机",
            "identity_resolution": "catalog_exact",
        },
        raw_query=frozen.raw_query,
    )

    orchestrator = SemanticRoutingOrchestrator()

    class MutatingResolver:
        def resolve(self, contract, catalog, section_refs, *, graph_candidates=()):
            return EntityResolution(
                contract=mutated,
                entity_role="device_identity",
                reason="catalog_exact",
            )

    orchestrator.entity_resolver = MutatingResolver()
    decision = IntentDecision(
        intent="knowledge_query",
        task_action="find_cause",
        requires_knowledge_retrieval=True,
    )

    plan = asyncio.run(orchestrator.build_plan(
            query=frozen.raw_query,
            decision=decision,
            catalog=DeviceCatalog(()),
            section_refs=(),
            query_contract=frozen,
            preserve_query_contract=True,
        ))

    assert plan.query_contract == frozen


def test_frozen_evaluation_contract_still_receives_server_identity_normalization() -> None:
    frozen = QueryContract.from_mapping(
        {
            "intent": "fault_diagnosis",
            "task_action": "find_cause",
            "raw_device_span": "摩托车发动机的火花塞",
            "device_name": "摩托车发动机的火花塞",
            "carrier_or_application": "摩托车发动机",
            "component": "火花塞",
            "raw_component_span": "火花塞",
            "fault": "火花塞损坏",
            "raw_fault_span": "火花塞损坏",
        },
        raw_query="摩托车发动机的火花塞出现火花塞损坏",
    )
    catalog = DeviceCatalog.from_manifests([
        {
            "document_id": "manual-motorcycle",
            "status": "ready",
            "document_identity": {
                "device_name": "摩托车发动机",
                "device_category": "发动机",
                "carrier_or_application": "摩托车",
                "confidence": 0.96,
            },
        }
    ])
    decision = IntentDecision(
        intent="fault_diagnosis",
        task_action="find_cause",
        requires_knowledge_retrieval=True,
    )

    plan = asyncio.run(SemanticRoutingOrchestrator().build_plan(
        query=frozen.raw_query,
        decision=decision,
        catalog=catalog,
        section_refs=(),
        request_document_id="manual-motorcycle",
        query_contract=frozen,
        preserve_query_contract=True,
    ))

    assert plan.query_contract.raw_device_span == "摩托车发动机的火花塞"
    assert plan.query_contract.device_name == "摩托车发动机"
    assert plan.query_contract.carrier_or_application == "摩托车"
    assert plan.query_contract.identity_resolution == "catalog_exact"
    assert plan.query_contract.component == "火花塞"
    assert plan.query_contract.fault == "火花塞损坏"
