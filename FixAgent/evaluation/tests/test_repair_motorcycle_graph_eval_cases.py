from evaluation.repair_motorcycle_graph_eval_cases import repair_cases


def _case(legacy_id: str, *, fault: str, component: str) -> dict:
    return {
        "case_id": legacy_id,
        "legacy_case_id": legacy_id,
        "query": f"{component}出现{fault}时怎么办？",
        "group": component,
        "graph_dependency": "required",
        "required_nuggets": [component, fault],
        "claim_constraints": [{
            "claim_id": "graph_relation",
            "answer_patterns": [component, fault],
            "evidence_patterns": [component, fault],
            "allowed_sources": [{
                "source_type": "graph",
                "document_id": "manual-1",
                "document_version": "v1",
                "pages": [1],
                "chunk_ids": ["chunk-1"],
                "component_name": component,
                "fault_name": fault,
            }],
        }],
    }


def test_repairs_overgeneralized_oil_pump_fact() -> None:
    repaired, report = repair_cases([
        _case("motorcycle_manual_v2_graph_010", fault="机油泵变形或开裂", component="机油泵")
    ])

    row = repaired[0]
    assert "油泵座垫变形" in row["query"]
    assert row["claim_constraints"][0]["allowed_sources"][0]["fault_name"] == "油泵座垫变形"
    assert report["repaired_case_count"] == 1


def test_downgrades_unanchored_section_inference_to_manual_evidence() -> None:
    repaired, _ = repair_cases([
        _case(
            "motorcycle_manual_v2_graph_012",
            fault="换挡星形凸轮磨损",
            component="换挡星形凸轮",
        )
    ])

    row = repaired[0]
    assert row["graph_dependency"] == "none"
    source = row["claim_constraints"][0]["allowed_sources"][0]
    assert source == {
        "source_type": "manual",
        "document_id": "manual-1",
        "document_version": "v1",
        "pages": [1],
        "chunk_ids": ["chunk-1"],
    }
