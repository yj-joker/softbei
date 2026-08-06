from services.knowledge.expiration import ExpirationService, _parse_verdict


def test_parse_verdict_rejects_unknown_verdict() -> None:
    assert _parse_verdict('{"verdict":"DELETE","confidence":1}') is None


def test_vector_candidate_requires_explicit_neo4j_identity() -> None:
    assert ExpirationService._metadata_node_id({"document_id": "kdoc:01:txt:0001"}) == ""
    assert ExpirationService._metadata_node_id({"graph_node_id": "solution-1"}) == "solution-1"


def test_task_summary_keeps_all_new_solution_contexts() -> None:
    summary = ExpirationService._summarize_new_nodes(object(), {
        "device_name": "测试设备",
        "faults": [{"name": "故障甲", "description": "症状甲"}],
        "solutions": [
            {"title": "方案甲", "description": "步骤甲", "faultName": "故障甲"},
            {"title": "方案乙", "description": "步骤乙", "faultName": "故障乙"},
        ],
    })

    assert summary["device_name"] == "测试设备"
    assert "方案甲" in summary["solution_title"]
    assert "方案乙" in summary["solution_title"]
    assert "故障乙" in summary["solution_summary"]
