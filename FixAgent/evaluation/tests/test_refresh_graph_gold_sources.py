from evaluation.refresh_graph_gold_sources import refresh_cases_graph_sources


def test_refresh_replaces_legacy_graph_ids_with_the_unique_stable_snapshot_record() -> None:
    cases = [{
        "id": "case-1",
        "graph_dependency": "required",
        "claim_constraints": [{
            "claim_id": "graph-path",
            "allowed_sources": [{
                "source_type": "graph",
                "document_id": "manual-1",
                "document_version": "v1",
                "device_name": "motorcycle engine",
                "component_name": "oil pump",
                "fault_name": "oil pump stuck",
                "path_ids": ["kgpath:legacy"],
                "graph_revision": "manual:manual-1:v1",
            }],
        }],
    }]
    snapshot = {"records": [{
        "document_id": "manual-1",
        "document_version": "v1",
        "device_name": "motorcycle engine",
        "component_name": "oil pump",
        "fault_name": "oil pump stuck",
        "relationship_types": ["OWNS", "CAUSES"],
        "source_chunk_uids": ["chunk-9"],
        "pages": [7],
        "graph_revision": "manual:manual-1:v1:stable-v1",
        "node_ids": ["kg:device:1", "kg:component:2", "kg:fault:3"],
        "path_ids": ["kgpath:stable"],
    }]}

    refreshed, report = refresh_cases_graph_sources(cases, snapshot)

    source = refreshed[0]["claim_constraints"][0]["allowed_sources"][0]
    assert source["path_ids"] == ["kgpath:stable"]
    assert source["graph_revision"] == "manual:manual-1:v1:stable-v1"
    assert source["chunk_ids"] == ["chunk-9"]
    assert report["updated_source_count"] == 1
    assert report["errors"] == []


def test_refresh_rejects_ambiguous_semantic_graph_source() -> None:
    cases = [{
        "id": "case-1",
        "claim_constraints": [{"allowed_sources": [{
            "source_type": "graph",
            "document_id": "manual-1",
            "document_version": "v1",
            "component_name": "oil pump",
            "fault_name": "oil pump stuck",
        }]}],
    }]
    record = {
        "document_id": "manual-1", "document_version": "v1",
        "component_name": "oil pump", "fault_name": "oil pump stuck",
        "path_ids": ["kgpath:stable"], "source_chunk_uids": ["chunk-9"],
        "pages": [7], "graph_revision": "manual:manual-1:v1:stable-v1",
        "node_ids": ["kg:device:1", "kg:component:2", "kg:fault:3"],
        "relationship_types": ["OWNS", "CAUSES"],
    }

    other = dict(record)
    other["path_ids"] = ["kgpath:other"]
    _, report = refresh_cases_graph_sources(cases, {"records": [record, other]})

    assert report["updated_source_count"] == 0
    assert report["errors"][0]["code"] == "ambiguous_graph_source_match"


def test_refresh_collapses_duplicate_rows_with_the_same_stable_path() -> None:
    cases = [{
        "id": "case-1",
        "claim_constraints": [{"allowed_sources": [{
            "source_type": "graph",
            "document_id": "manual-1",
            "document_version": "v1",
            "component_name": "oil pump",
            "fault_name": "oil pump stuck",
        }]}],
    }]
    record = {
        "document_id": "manual-1", "document_version": "v1",
        "device_name": "motorcycle engine",
        "component_name": "oil pump", "fault_name": "oil pump stuck",
        "path_ids": ["kgpath:stable"], "source_chunk_uids": ["chunk-9"],
        "pages": [7], "graph_revision": "manual:manual-1:v1:stable-v1",
        "node_ids": ["kg:device:1", "kg:component:2", "kg:fault:3"],
        "relationship_types": ["OWNS", "CAUSES"],
    }

    refreshed, report = refresh_cases_graph_sources(
        cases, {"records": [record, dict(record)]}
    )

    assert report["passed"] is True
    assert report["updated_source_count"] == 1
    assert refreshed[0]["claim_constraints"][0]["allowed_sources"][0]["path_ids"] == [
        "kgpath:stable"
    ]


def test_refresh_prefers_exact_fault_name_before_aliases() -> None:
    cases = [{
        "id": "case-1",
        "claim_constraints": [{"allowed_sources": [{
            "source_type": "graph", "document_id": "manual-1",
            "document_version": "v1", "component_name": "机油泵",
            "fault_name": "机油泵从动齿轮卡滞",
        }]}],
    }]
    base = {
        "document_id": "manual-1", "document_version": "v1",
        "device_name": "摩托车发动机", "component_name": "机油泵",
        "source_chunk_uids": ["chunk-9"], "pages": [7],
        "graph_revision": "manual:manual-1:v1:stable-v1",
        "node_ids": ["kg:device:1", "kg:component:2", "kg:fault:3"],
        "relationship_types": ["OWNS", "CAUSES"],
    }
    exact = {**base, "fault_name": "机油泵从动齿轮卡滞", "path_ids": ["kgpath:exact"]}
    alias = {**base, "fault_name": "机油泵齿轮卡滞", "path_ids": ["kgpath:alias"]}

    refreshed, report = refresh_cases_graph_sources(cases, {"records": [alias, exact]})

    assert report["passed"] is True
    source = refreshed[0]["claim_constraints"][0]["allowed_sources"][0]
    assert source["path_ids"] == ["kgpath:exact"]
