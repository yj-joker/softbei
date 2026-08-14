"""Graph candidate requests must consume the verified device identity."""

from __future__ import annotations

from dataclasses import replace

from services.retrieval.device_identity import QueryContract
from services.routing.graph_candidate_provider import JavaGraphCandidateProvider


def test_verified_catalog_identity_is_sent_to_graph_service() -> None:
    contract = QueryContract.from_mapping(
        {
            "raw_device_span": "摩托车发动机的火花塞",
            "device_name": "摩托车发动机的火花塞",
            "component": "火花塞",
            "raw_component_span": "火花塞",
            "fault": "火花塞损坏",
            "raw_fault_span": "火花塞损坏",
        },
        raw_query="摩托车发动机的火花塞出现火花塞损坏",
    )
    contract = replace(
        contract,
        device_name="摩托车发动机",
        identity_resolution="catalog_exact",
    )

    payload = JavaGraphCandidateProvider._contract_payload(contract)

    assert contract.effective_device_identity == "摩托车发动机"
    assert payload["deviceIdentity"] == "摩托车发动机"
    assert payload["component"] == "火花塞"
    assert payload["fault"] == "火花塞损坏"


def test_unverified_model_identity_cannot_replace_grounded_raw_span() -> None:
    contract = QueryContract.from_mapping(
        {
            "raw_device_span": "飞机发动机",
            "device_name": "摩托车发动机",
        },
        raw_query="飞机发动机异响",
    )

    payload = JavaGraphCandidateProvider._contract_payload(contract)

    assert contract.effective_device_identity == "飞机发动机"
    assert payload["deviceIdentity"] == "飞机发动机"
