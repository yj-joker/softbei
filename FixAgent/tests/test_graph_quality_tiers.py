from __future__ import annotations

from services.clarification.graph_candidates import build_graph_candidates
from services.retrieval.evidence import EvidenceLedger
from services.retrieval.graph_evidence import GraphAuthorizationContext, normalize_graph_response
from services.retrieval.graph_quality import GraphQualityTier, evaluate_graph_path_quality


def _record(*, score: float, provenance: str = "complete") -> dict:
    return {
        "pathId": "kgpath:d-7:c-7:f-7",
        "nodeIds": ["d-7", "c-7", "f-7"],
        "relationshipTypes": ["OWNS", "CAUSES"],
        "deviceId": "d-7",
        "deviceName": "sample-device",
        "componentId": "c-7",
        "componentName": "sample-component",
        "faultId": "f-7",
        "faultName": "sample-fault",
        "semanticScore": score,
        "documentId": "doc-7",
        "documentVersion": "v7",
        "sectionId": "sec-7",
        "sourceChunkUids": ["chunk-7"],
        "pages": [7],
        "graphRevision": "rev-7",
        "provenanceStatus": provenance,
        "distinguishingFeatures": ["sample-observation"],
    }


def test_quality_tiers_are_computed_from_generic_signals() -> None:
    assert evaluate_graph_path_quality(_record(score=0.91)).tier is GraphQualityTier.HIGH
    assert evaluate_graph_path_quality(_record(score=0.77)).tier is GraphQualityTier.MEDIUM
    assert evaluate_graph_path_quality(_record(score=0.91, provenance="partial")).tier is GraphQualityTier.MEDIUM
    assert evaluate_graph_path_quality(_record(score=0.42)).tier is GraphQualityTier.LOW


def test_high_overall_score_cannot_hide_low_component_or_fault_alignment() -> None:
    component_mismatch = {
        **_record(score=0.93),
        "componentScore": 0.41,
        "faultScore": 0.92,
    }
    fault_mismatch = {
        **_record(score=0.93),
        "componentScore": 0.91,
        "faultScore": 0.38,
    }

    assert evaluate_graph_path_quality(component_mismatch).tier is GraphQualityTier.LOW
    assert evaluate_graph_path_quality(fault_mismatch).tier is GraphQualityTier.LOW


def test_candidate_boundary_keeps_high_and_medium_but_discards_low() -> None:
    high, medium = build_graph_candidates([
        _record(score=0.91),
        {**_record(score=0.77), "pathId": "kgpath:d-8:c-8:f-8", "nodeIds": ["d-8", "c-8", "f-8"],
         "deviceId": "d-8", "componentId": "c-8", "faultId": "f-8"},
        {**_record(score=0.42), "pathId": "kgpath:d-9:c-9:f-9", "nodeIds": ["d-9", "c-9", "f-9"],
         "deviceId": "d-9", "componentId": "c-9", "faultId": "f-9"},
    ])

    assert high.quality_tier == "high"
    assert medium.quality_tier == "medium"


def test_evidence_boundary_only_qualifies_high_and_discards_low() -> None:
    high = normalize_graph_response({"status": "found", "records": [_record(score=0.91)]})
    medium = normalize_graph_response({"status": "found", "records": [_record(score=0.77)]})
    low = normalize_graph_response({"status": "found", "records": [_record(score=0.42)]})

    assert high.evidence[0].qualification == "qualified"
    assert high.evidence[0].quality_tier == "high"
    assert medium.evidence[0].qualification == "routing_only"
    assert medium.evidence[0].quality_tier == "medium"
    assert low.status == "filtered_out"
    assert low.evidence == ()
    assert low.diagnostics["discarded_count"] == 1


def _authorization(**overrides) -> GraphAuthorizationContext:
    values = {
        "canonical_device_identity": "sample-device",
        "document_ids": ("doc-7",),
        "document_versions": ("v7",),
        "source_chunk_uids": ("chunk-7",),
    }
    values.update(overrides)
    return GraphAuthorizationContext(**values)


def test_exact_authorized_medium_relation_is_qualified_without_solution_claim() -> None:
    record = {
        **_record(score=0.78),
        "componentScore": 0.76,
        "faultScore": 0.79,
        "relationshipTypes": ["OWNS", "CAUSES"],
    }

    batch = normalize_graph_response(
        {"status": "found", "records": [record]},
        scope={
            "allowed_path_ids": [record["pathId"]],
            "allowed_device_ids": [record["deviceId"]],
            "allowed_component_ids": [record["componentId"]],
            "allowed_fault_ids": [record["faultId"]],
        },
        authorization_context=_authorization(),
    )

    evidence = batch.evidence[0]
    assert evidence.qualification == "qualified"
    assert evidence.qualification_basis == "structural_exact"
    assert evidence.claim_types == ("component_ownership", "fault_relation")
    assert evidence.authorized_claim_types == evidence.claim_types
    assert "verified_solution" not in evidence.claim_types


def test_candidate_ids_cannot_authorize_the_same_medium_candidate() -> None:
    record = _record(score=0.78)

    batch = normalize_graph_response(
        {"status": "found", "records": [record]},
        scope={
            "allowed_path_ids": [record["pathId"]],
            "allowed_device_ids": [record["deviceId"]],
            "allowed_component_ids": [record["componentId"]],
            "allowed_fault_ids": [record["faultId"]],
        },
    )

    assert batch.evidence[0].qualification == "routing_only"
    assert batch.evidence[0].qualification_basis == "routing_only"


def test_independent_document_version_conflict_blocks_structural_qualification() -> None:
    record = _record(score=0.78)

    batch = normalize_graph_response(
        {"status": "found", "records": [record]},
        authorization_context=_authorization(document_versions=("v8",)),
    )

    assert batch.evidence == ()
    assert "authorization_document_version_mismatch" in batch.diagnostics["discard_reasons"]


def test_medium_path_only_cross_validates_qualified_rag_chunk() -> None:
    trace = {
        "react_trace": [{
            "tool_calls": [
                {
                    "name": "java_graph_diagnosis_path",
                    "result_data": {"status": "found", "records": [_record(score=0.77)]},
                },
                {
                    "name": "knowledge_retrieval",
                    "result_data": {
                        "qualified_evidence": [{
                            "id": "chunk-7",
                            "content": "independently retrieved manual content",
                            "metadata": {
                                "qualification": "qualified",
                                "document_id": "doc-7",
                                "document_version": "v7",
                                "chunk_uid": "chunk-7",
                                "chunk_id": "chunk-7",
                                "parent_section_id": "sec-7",
                                "page": 7,
                            },
                        }],
                    },
                },
            ],
        }],
    }

    ledger = EvidenceLedger.from_react_trace(trace)

    assert len(ledger.entries) == 1
    assert ledger.entries[0]["source_type"] == "manual"
    assert ledger.entries[0]["graph_cross_validation"] == {
        "status": "corroborated",
        "quality_tier": "medium",
        "path_ids": ["kgpath:d-7:c-7:f-7"],
    }


def test_high_path_enters_ledger_while_low_path_has_no_effect() -> None:
    trace = {
        "react_trace": [{
            "tool_calls": [{
                "name": "java_graph_diagnosis_path",
                "result_data": {
                    "status": "found",
                    "records": [_record(score=0.91), {
                        **_record(score=0.42),
                        "pathId": "kgpath:d-9:c-9:f-9",
                        "nodeIds": ["d-9", "c-9", "f-9"],
                        "deviceId": "d-9",
                        "componentId": "c-9",
                        "faultId": "f-9",
                    }],
                },
            }],
        }],
    }

    ledger = EvidenceLedger.from_react_trace(trace)

    assert [entry["path_id"] for entry in ledger.entries] == ["kgpath:d-7:c-7:f-7"]
    assert ledger.entries[0]["quality_tier"] == "high"


def test_normalized_medium_tier_cannot_be_upgraded_by_default_threshold() -> None:
    medium = normalize_graph_response(
        {"status": "found", "records": [_record(score=0.87)]},
        high_threshold=0.90,
    )
    assert medium.evidence[0].quality_tier == "medium"

    renormalized = normalize_graph_response(medium.to_dict())
    ledger = EvidenceLedger.from_react_trace({
        "react_trace": [{"tool_calls": [{
            "name": "java_graph_diagnosis_path",
            "result_data": medium.to_dict(),
        }]}],
    })

    assert renormalized.evidence[0].quality_tier == "medium"
    assert ledger.entries == []
