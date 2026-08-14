import asyncio

from services.retrieval.device_identity import QueryContract
from services.routing.graph_candidate_provider import (
    JavaGraphCandidateProvider,
    get_graph_candidate_provider,
)


def test_provider_factory_returns_request_isolated_instances() -> None:
    first = get_graph_candidate_provider()
    second = get_graph_candidate_provider()

    assert first is not second


def test_parallel_candidate_results_do_not_share_timeout_state() -> None:
    async def request_json(_method, _url, **kwargs):
        query = kwargs["json"]["queryContract"]["rawQuery"]
        if query == "timeout":
            await asyncio.sleep(0)
            raise TimeoutError("one request timed out")
        await asyncio.sleep(0)
        return {"data": {"records": [], "status": "empty"}}

    provider = JavaGraphCandidateProvider(request_json=request_json)

    async def run():
        return await asyncio.gather(*(
            provider.fetch_result(QueryContract(raw_query=query, intent="fault_diagnosis"))
            for query in ("first", "timeout", "third")
        ))

    results = asyncio.run(run())

    assert [result.request_id for result in results] == ["first", "timeout", "third"]
    assert results[1].error_code == "request_timeout"
    assert results[0].error_code == ""
    assert results[2].error_code == ""
    assert results[0].retrieval_status["status"] == "empty"
    assert results[2].retrieval_status["status"] == "empty"


def test_structured_fault_contract_is_sent_to_graph_service_without_intent_enumeration() -> None:
    captured = {}

    async def request_json(_method, _url, **kwargs):
        captured.update(kwargs["json"]["queryContract"])
        return {"data": {"records": [], "status": "empty"}}

    provider = JavaGraphCandidateProvider(request_json=request_json)
    contract = QueryContract.from_mapping(
        {
            "intent": "knowledge_query",
            "task_action": "document_explain",
            "component": "油泵座垫",
            "raw_component_span": "油泵座垫",
            "fault": "变形",
            "raw_fault_span": "变形",
        },
        raw_query="油泵座垫变形时应该怎么处理",
    )

    result = asyncio.run(provider.fetch_result(contract))

    assert result.retrieval_status["status"] == "empty"
    assert captured["component"] == "油泵座垫"
    assert captured["fault"] == "变形"
    assert captured["rawFaultSpan"] == "变形"
