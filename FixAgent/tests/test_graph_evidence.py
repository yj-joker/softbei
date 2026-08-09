from services.retrieval.graph_evidence import normalize_graph_response


def _production_record() -> dict:
    return {
        "pathId": "kgpath:device-1:component-1:fault-1",
        "nodeIds": ["device-1", "component-1", "fault-1"],
        "relationshipTypes": ["OWNS", "CAUSES", "HAS_SOLUTION"],
        "deviceId": "device-1",
        "deviceName": "一号发动机",
        "componentId": "component-1",
        "componentName": "张紧轮",
        "faultId": "fault-1",
        "faultName": "轴承磨损",
        "faultSeverity": "medium",
        "solutions": [
            {
                "id": "solution-1",
                "title": "更换已磨损的张紧轮轴承",
                "status": "active",
                "verified": True,
                "kind": "fault_solution",
            },
            {
                "id": "solution-draft",
                "title": "未验证方案",
                "status": "active",
                "verified": False,
                "kind": "fault_solution",
            },
        ],
        "documentId": "manual-1",
        "documentVersion": "v3",
        "sectionId": "sec-bearing",
        "sourceChunkUids": ["chunk-7"],
        "pages": [12],
        "graphRevision": "graph-2026-08-06",
        "provenanceStatus": "complete",
        "matchScore": 3,
        "faultScore": 0.92,
    }


def test_normalizes_production_java_dto_into_stable_qualified_evidence() -> None:
    batch = normalize_graph_response(
        {"evidence_status": "found", "raw_records": [_production_record()]},
        scope={
            "allowed_path_ids": ["kgpath:device-1:component-1:fault-1"],
            "allowed_device_ids": ["device-1"],
            "allowed_component_ids": ["component-1"],
            "allowed_fault_ids": ["fault-1"],
        },
    )

    assert batch.status == "found"
    assert [item.evidence_id for item in batch.evidence] == [
        "graph:kgpath:device-1:component-1:fault-1:none",
        "graph:kgpath:device-1:component-1:fault-1:solution-1",
    ]
    path, solution = batch.evidence
    assert path.qualification == "qualified"
    assert path.relationship_types == ("OWNS", "CAUSES", "HAS_SOLUTION")
    assert path.claim_types == ("device_identity", "component_ownership", "fault_relation")
    assert path.supports_aspect_ids == ("device", "component", "fault-cause")
    assert path.source.document_id == "manual-1"
    assert path.source.source_chunk_uids == ("chunk-7",)
    assert solution.qualification == "qualified"
    assert solution.solution["id"] == "solution-1"
    assert solution.claim_types == ("verified_solution",)
    assert all(item.solution.get("id") != "solution-draft" for item in batch.evidence)
    assert batch.to_dict()["evidence"][0]["source_type"] == "graph"


def test_incomplete_provenance_is_routing_only() -> None:
    record = _production_record()
    record["provenanceStatus"] = "partial"
    record["sourceChunkUids"] = []

    batch = normalize_graph_response({"evidence_status": "found", "raw_records": [record]})

    assert len(batch.evidence) == 1
    assert batch.evidence[0].qualification == "routing_only"
    assert "incomplete_provenance" in batch.evidence[0].rejection_reasons


def test_cross_scope_record_is_rejected() -> None:
    batch = normalize_graph_response(
        {"evidence_status": "found", "raw_records": [_production_record()]},
        scope={"allowed_device_ids": ["device-2"]},
    )

    assert batch.evidence == ()
    assert batch.status == "filtered_out"
    assert batch.diagnostics["low_quality_count"] == 1
    assert "outside_allowed_device_ids" in batch.diagnostics["discard_reasons"]


def test_unavailable_status_is_not_collapsed_to_empty() -> None:
    batch = normalize_graph_response(
        {
            "evidence_status": "unavailable",
            "reason": "java_connect_error",
            "raw_records": [],
        }
    )

    assert batch.status == "unavailable"
    assert batch.reason == "java_connect_error"
    assert batch.evidence == ()


def test_path_id_must_match_core_node_identity() -> None:
    record = _production_record()
    record["pathId"] = "kgpath:device-2:component-1:fault-1"

    batch = normalize_graph_response({"status": "found", "records": [record]})

    assert batch.evidence == ()
    assert "path_identity_mismatch" in batch.diagnostics["discard_reasons"]


def test_verified_solution_requires_has_solution_relationship() -> None:
    record = _production_record()
    record["relationshipTypes"] = ["OWNS", "CAUSES"]

    batch = normalize_graph_response({"status": "found", "records": [record]})

    assert len(batch.evidence) == 1
    assert batch.evidence[0].solution == {}


def test_explicit_empty_scope_rejects_all_records() -> None:
    batch = normalize_graph_response(
        {"status": "found", "records": [_production_record()]},
        scope={"allowed_path_ids": []},
    )

    assert batch.evidence == ()
    assert "empty_allowed_scope" in batch.diagnostics["discard_reasons"]
