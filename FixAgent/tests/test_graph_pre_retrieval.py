import asyncio

from services.retrieval.graph_evidence import normalize_graph_response
from services.retrieval.graph_pre_retrieval import GraphPreRetrievalService


def _route_plan(task_action: str = "find_cause") -> dict:
    return {
        "action": "grounded_retrieval",
        "intent": "fault_diagnosis",
        "task_action": task_action,
        "selected_document_id": "manual-engine",
        "authorized_device_identity": "一号发动机",
        "query_contract": {
            "raw_query": "一号发动机张紧轮异响是什么原因",
            "device_name": "一号发动机",
            "component": "张紧轮",
            "symptoms": ["异响"],
            "task_action": task_action,
        },
    }


def _scope() -> dict:
    return {
        "document_id": "manual-engine",
        "document_version": "v1",
        "allowed_path_ids": ["kgpath:device-1:component-1:fault-1"],
        "allowed_device_ids": ["device-1"],
        "allowed_component_ids": ["component-1"],
        "allowed_fault_ids": ["fault-1"],
        "allowed_section_ids": ["section-1"],
        "allowed_source_chunk_uids": ["chunk-1"],
    }


class _Provider:
    def __init__(self) -> None:
        self.calls = []

    async def retrieve_path_evidence(self, **kwargs):
        self.calls.append(kwargs)
        return normalize_graph_response({"evidence_status": "empty", "raw_records": []})


def test_graph_diagnostic_request_runs_server_controlled_pre_retrieval() -> None:
    provider = _Provider()
    service = GraphPreRetrievalService(provider=provider)

    batch = asyncio.run(service.retrieve(
        rag_variant="graph",
        route_plan=_route_plan(),
        graph_scope=_scope(),
        image_urls=["image-1"],
    ))

    assert batch.status == "empty"
    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call["fault_description"] == "异响"
    assert call["component_description"] == "张紧轮"
    assert call["allowed_path_ids"] == ["kgpath:device-1:component-1:fault-1"]


def test_stable_graph_scope_does_not_reapply_model_device_keyword() -> None:
    provider = _Provider()
    service = GraphPreRetrievalService(provider=provider)
    route = _route_plan()
    route["query_contract"].update({
        "raw_device_span": "张紧轮",
        "device_name": "张紧轮",
        "component": "张紧轮",
    })

    asyncio.run(service.retrieve(
        rag_variant="graph_full",
        route_plan=route,
        graph_scope=_scope(),
    ))

    assert provider.calls[0]["keyword"] == ""


def test_reverse_fault_lookup_uses_server_authorized_document_identity() -> None:
    provider = _Provider()
    service = GraphPreRetrievalService(provider=provider)
    route = _route_plan()
    route["query_contract"].update({
        "raw_device_span": "",
        "device_name": "",
        "identity_resolution": "",
        "component": "拨叉",
        "symptoms": ["拨叉损坏"],
    })

    asyncio.run(service.retrieve(
        rag_variant="graph_full",
        route_plan=route,
        graph_scope=_scope(),
    ))

    authorization = provider.calls[0]["authorization_context"]
    assert authorization.canonical_device_identity == "一号发动机"
    assert authorization.document_ids == ("manual-engine",)
    assert authorization.document_versions == ("v1",)
    assert authorization.section_ids == ("section-1",)
    assert authorization.source_chunk_uids == ("chunk-1",)


def test_server_authorization_is_not_built_for_document_mismatch() -> None:
    provider = _Provider()
    service = GraphPreRetrievalService(provider=provider)
    scope = _scope()
    scope["document_id"] = "manual-other"

    asyncio.run(service.retrieve(
        rag_variant="graph_full",
        route_plan=_route_plan(),
        graph_scope=scope,
    ))

    assert provider.calls[0]["authorization_context"] is None


def test_server_authorization_requires_path_and_source_scope() -> None:
    provider = _Provider()
    service = GraphPreRetrievalService(provider=provider)
    scope = _scope()
    scope["allowed_path_ids"] = []
    scope["allowed_section_ids"] = []
    scope["allowed_source_chunk_uids"] = []

    asyncio.run(service.retrieve(
        rag_variant="graph_full",
        route_plan=_route_plan(),
        graph_scope=scope,
    ))

    assert provider.calls[0]["authorization_context"] is None


def test_no_graph_variant_never_runs_graph_pre_retrieval() -> None:
    provider = _Provider()
    service = GraphPreRetrievalService(provider=provider)

    batch = asyncio.run(service.retrieve(
        rag_variant="no_graph",
        route_plan=_route_plan(),
        graph_scope=_scope(),
    ))

    assert batch.status == "not_applicable"
    assert batch.reason == "rag_variant_no_graph"
    assert provider.calls == []


def test_non_diagnostic_parameter_request_never_runs_graph_pre_retrieval() -> None:
    provider = _Provider()
    service = GraphPreRetrievalService(provider=provider)
    route = _route_plan("parameter_lookup")
    route["intent"] = "parameter_query"

    batch = asyncio.run(service.retrieve(
        rag_variant="graph",
        route_plan=route,
        graph_scope=_scope(),
    ))

    assert batch.status == "not_applicable"
    assert batch.reason == "manual_only_request"
    assert provider.calls == []


def test_ordinary_maintenance_guidance_remains_manual_only() -> None:
    provider = _Provider()
    service = GraphPreRetrievalService(provider=provider)
    route = _route_plan("repair_guidance")
    route["intent"] = "maintenance_guidance"
    route["query_contract"]["task_action"] = "repair_guidance"
    route["query_contract"]["symptoms"] = []

    batch = asyncio.run(service.retrieve(
        rag_variant="graph",
        route_plan=route,
        graph_scope=_scope(),
    ))

    assert batch.status == "not_applicable"
    assert batch.reason == "non_diagnostic_request"
    assert provider.calls == []


def test_graph_pre_retrieval_blocks_unscoped_query_fail_closed() -> None:
    provider = _Provider()
    service = GraphPreRetrievalService(provider=provider)

    batch = asyncio.run(service.retrieve(
        rag_variant="graph",
        route_plan=_route_plan(),
        graph_scope={},
    ))

    assert batch.status == "filtered_out"
    assert batch.reason == "empty_graph_scope"
    assert provider.calls == []


def test_explicit_diagnostic_contract_does_not_bypass_scope() -> None:
    provider = _Provider()
    service = GraphPreRetrievalService(provider=provider)

    batch = asyncio.run(service.retrieve(
        rag_variant="graph",
        route_plan=_route_plan(),
        graph_scope={},
    ))

    assert batch.status == "filtered_out"
    assert provider.calls == []


def test_confirmed_fault_procedure_uses_the_central_graph_policy() -> None:
    provider = _Provider()
    service = GraphPreRetrievalService(provider=provider)
    route = _route_plan("formal_procedure")
    route["intent"] = "procedure_planning"
    route["query_contract"].update({
        "intent": "procedure_planning",
        "task_action": "formal_procedure",
        "symptoms": ["bearing wear"],
    })

    batch = asyncio.run(service.retrieve(
        rag_variant="graph_full",
        route_plan=route,
        graph_scope=_scope(),
    ))

    assert batch.status == "empty"
    assert len(provider.calls) == 1


def test_pre_retrieval_consumes_route_policy_without_reclassifying_intent() -> None:
    provider = _Provider()
    service = GraphPreRetrievalService(provider=provider)
    route = _route_plan()
    route.update({
        "intent": "knowledge_query",
        "task_action": "document_explain",
        "graph_policy": {
            "pre_retrieval_enabled": True,
            "reason": "diagnostic_graph_enabled",
        },
    })

    batch = asyncio.run(service.retrieve(
        rag_variant="graph_full",
        route_plan=route,
        graph_scope=_scope(),
    ))

    assert batch.status == "empty"
    assert len(provider.calls) == 1


def test_route_policy_can_disable_pre_retrieval_even_for_diagnostic_shape() -> None:
    provider = _Provider()
    service = GraphPreRetrievalService(provider=provider)
    route = _route_plan()
    route["graph_policy"] = {
        "pre_retrieval_enabled": False,
        "reason": "manual_only_request",
    }

    batch = asyncio.run(service.retrieve(
        rag_variant="graph_full",
        route_plan=route,
        graph_scope=_scope(),
    ))

    assert batch.status == "not_applicable"
    assert batch.reason == "manual_only_request"
    assert provider.calls == []
