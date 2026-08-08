from evaluation.maintenance_eval_schema import MaintenanceEvalCase
from evaluation.paired_variant_runner import run_paired_variants


def test_paired_runner_reuses_no_graph_route_contract_for_other_arms() -> None:
    seen_contracts: dict[str, object] = {}

    def request_runner(case, variant, endpoint, sequence):
        seen_contracts[variant] = case.candidate_metadata.get("_paired_route_contract")
        if variant == "no_graph":
            return {
                "trace_rows": [{
                    "metadata": {
                        "intent_decision": {
                            "intent": "knowledge_query",
                            "task_action": "find_cause",
                        },
                        "query_contract": {
                            "raw_query": "pump fuse fault",
                            "intent": "knowledge_query",
                            "task_action": "find_cause",
                            "component": "pump",
                            "symptoms": ["fuse fault"],
                        },
                    }
                }]
            }
        return {"trace_rows": []}

    result = run_paired_variants(
        cases=[MaintenanceEvalCase(case_id="case-1", query="pump fuse fault")],
        endpoints={
            "no_graph": "http://no-graph",
            "graph_shadow": "http://shadow",
            "graph_full": "http://full",
        },
        repetitions=1,
        concurrency=1,
        request_runner=request_runner,
    )

    assert seen_contracts["no_graph"] is None
    assert seen_contracts["graph_shadow"] == seen_contracts["graph_full"]
    assert seen_contracts["graph_full"] == {
        "intent_decision": {
            "intent": "knowledge_query",
            "task_action": "find_cause",
        },
        "query_contract": {
            "raw_query": "pump fuse fault",
            "intent": "knowledge_query",
            "task_action": "find_cause",
            "component": "pump",
            "symptoms": ["fuse fault"],
        },
    }
    assert result.request_order[1]["route_contract_frozen"] is True
    assert result.request_order[2]["route_contract_frozen"] is True
