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
