"""Deterministic retrieval scope decisions."""

from __future__ import annotations

import json
from pathlib import Path

from services.retrieval.device_identity import DeviceCatalog, QueryContract
from services.retrieval.scope import ScopeRegistry, decide_scope, get_scope_registry


MANUAL_ID = "manual-motorcycle"
ACTIVE_MANUAL_ID = "kdoc_2083453722632753154"


def _dynamic_catalog() -> DeviceCatalog:
    return DeviceCatalog.from_manifests(
        [
            {
                "document_id": MANUAL_ID,
                "status": "ready",
                "device_type": "motorcycle-engine",
                "document_identity": {
                    "device_name": "摩托车发动机",
                    "device_category": "发动机",
                    "carrier_or_application": "摩托车",
                    "confidence": 0.96,
                },
            }
        ]
    )


def _registry() -> ScopeRegistry:
    return ScopeRegistry.from_dict(
        {
            "documents": [
                {
                    "document_id": MANUAL_ID,
                    "device_type": "motorcycle-engine",
                    "display_name": "摩托车发动机",
                    "aliases": ["摩托车发动机", "摩托车", "motorcycle-engine"],
                }
            ],
        }
    )


def test_aircraft_query_is_rejected_against_motorcycle_manual() -> None:
    query = "飞机活塞发动机功率下降有哪些常见原因？"
    decision = decide_scope(
        query,
        request_document_id=MANUAL_ID,
        request_device_type="aircraft-piston-engine",
        query_contract=QueryContract.from_mapping(
            {
                "raw_device_span": "飞机活塞发动机",
                "device_category": "发动机",
                "carrier_or_application": "飞机",
            },
            raw_query=query,
        ),
        catalog=_dynamic_catalog(),
    )

    assert decision.status == "out_of_scope"
    assert decision.document_id == MANUAL_ID
    assert decision.detected_device_type == "飞机活塞发动机"
    assert decision.reason == "device_document_conflict"


def test_aircraft_phrase_with_separated_engine_term_is_detected_without_explicit_scope() -> None:
    query = "飞机在运行时发动机出现异响是什么原因？"
    decision = decide_scope(
        query,
        query_contract=QueryContract.from_mapping(
            {
                "raw_device_span": "飞机在运行时发动机",
                "device_category": "发动机",
                "carrier_or_application": "飞机",
            },
            raw_query=query,
        ),
        catalog=_dynamic_catalog(),
    )

    assert decision.status == "out_of_scope"
    assert decision.detected_device_type == "飞机在运行时发动机"
    assert decision.reason == "identity_attribute_conflict"


def test_generic_engine_question_inherits_confirmed_session_manual() -> None:
    decision = decide_scope(
        "发动机怎么拆？",
        session_document_id=MANUAL_ID,
        registry=_registry(),
    )

    assert decision.status == "in_scope"
    assert decision.source == "session_document"
    assert decision.document_id == MANUAL_ID
    assert decision.device_type == "motorcycle-engine"


def test_unknown_requested_document_is_out_of_scope() -> None:
    decision = decide_scope(
        "火花塞间隙是多少？",
        request_document_id="nonexistent-manual",
        registry=_registry(),
    )

    assert decision.status == "out_of_scope"
    assert decision.reason == "unknown_document"


def test_default_registry_does_not_authorize_any_static_manual_identity() -> None:
    decision = decide_scope(
        "火花塞间隙是多少？",
        request_document_id=ACTIVE_MANUAL_ID,
        registry=get_scope_registry(),
    )

    assert decision.status == "out_of_scope"
    assert decision.reason == "unknown_document"


def test_explicit_device_switch_invalidates_confirmed_session_scope() -> None:
    query = "改问柴油发电机，启动困难先检查什么？"
    decision = decide_scope(
        query,
        session_document_id=MANUAL_ID,
        query_contract=QueryContract.from_mapping(
            {
                "raw_device_span": "柴油发电机",
                "device_category": "发电机",
                "carrier_or_application": "固定发电机组",
            },
            raw_query=query,
        ),
        catalog=_dynamic_catalog(),
    )

    assert decision.status == "out_of_scope"
    assert decision.detected_device_type == "柴油发电机"
    assert decision.reason == "explicit_device_switch"


def test_audited_query_alias_can_bind_a_supported_manual() -> None:
    query = "摩托车发动机的火花塞间隙是多少？"
    decision = decide_scope(
        query,
        query_contract=QueryContract.from_mapping(
            {
                "raw_device_span": "摩托车发动机",
                "device_category": "发动机",
                "carrier_or_application": "摩托车",
            },
            raw_query=query,
        ),
        catalog=_dynamic_catalog(),
    )

    assert decision.status == "in_scope"
    assert decision.source == "query_identity"
    assert decision.document_id == MANUAL_ID
    assert decision.device_type == "motorcycle-engine"


def test_document_scope_filter_does_not_add_a_redundant_device_tag() -> None:
    decision = decide_scope(
        "火花塞间隙是多少？",
        request_document_id=MANUAL_ID,
        registry=_registry(),
    )

    assert decision.retrieval_filter() == {
        "document_id": MANUAL_ID,
        "device_type": "",
    }


def test_out_of_scope_decision_never_exposes_retrieval_filter() -> None:
    query = "改问柴油发电机，启动困难先检查什么？"
    decision = decide_scope(
        query,
        session_document_id=MANUAL_ID,
        query_contract=QueryContract.from_mapping(
            {
                "raw_device_span": "柴油发电机",
                "device_category": "发电机",
                "carrier_or_application": "固定发电机组",
            },
            raw_query=query,
        ),
        catalog=_dynamic_catalog(),
    )

    assert decision.status == "out_of_scope"
    assert decision.retrieval_filter() == {}


def test_production_scope_config_does_not_register_unsupported_devices() -> None:
    config_path = Path(__file__).resolve().parents[1] / "config" / "scope_registry.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))

    assert "external_devices" not in payload
    assert payload.get("documents") == []


def test_dynamic_query_identity_matches_supported_manifest_without_external_aliases() -> None:
    query = "摩托车发动机的火花塞间隙是多少？"
    decision = decide_scope(
        query,
        query_contract=QueryContract.from_mapping(
            {
                "raw_device_span": "摩托车发动机",
                "device_category": "发动机",
                "carrier_or_application": "摩托车",
            },
            raw_query=query,
        ),
        catalog=_dynamic_catalog(),
    )

    assert decision.status == "in_scope"
    assert decision.source == "query_identity"
    assert decision.document_id == MANUAL_ID
    assert decision.retrieval_filter() == {"document_id": MANUAL_ID, "device_type": ""}


def test_unseen_device_identity_is_rejected_without_registering_its_name() -> None:
    query = "履带起重机发动机异响是什么原因？"
    decision = decide_scope(
        query,
        query_contract=QueryContract.from_mapping(
            {
                "raw_device_span": "履带起重机发动机",
                "device_category": "发动机",
                "carrier_or_application": "履带起重机",
            },
            raw_query=query,
        ),
        catalog=_dynamic_catalog(),
    )

    assert decision.status == "out_of_scope"
    assert decision.reason == "identity_attribute_conflict"
    assert decision.retrieval_filter() == {}


def test_uncertain_device_identity_never_exposes_a_retrieval_filter() -> None:
    query = "发动机异响是什么原因？"
    decision = decide_scope(
        query,
        query_contract=QueryContract.from_mapping(
            {
                "raw_device_span": "发动机",
                "device_category": "发动机",
            },
            raw_query=query,
        ),
        catalog=_dynamic_catalog(),
    )

    assert decision.status == "unknown"
    assert decision.reason == "identity_not_distinguishing"
    assert decision.retrieval_filter() == {}


def test_catalog_fallback_does_not_promote_generic_document_name_to_explicit_identity() -> None:
    query = "飞机发动机异响是什么原因？"
    catalog = DeviceCatalog.from_manifests(
        [
            {
                "document_id": "generic-engine-manual",
                "status": "ready",
                "document_identity": {
                    "device_name": "发动机",
                    "device_category": "发动机",
                    "carrier_or_application": "摩托车",
                    "confidence": 0.96,
                },
            }
        ]
    )

    decision = decide_scope(
        query,
        query_contract=QueryContract.from_mapping({}, raw_query=query),
        catalog=catalog,
    )

    assert decision.status == "unknown"
    assert decision.retrieval_filter() == {}


def test_catalog_never_reconstructs_missing_query_identity_from_supported_names() -> None:
    query = "帮我查询摩托车发动机气缸活塞装配部件清单"

    decision = decide_scope(
        query,
        query_contract=QueryContract.from_mapping({}, raw_query=query),
        catalog=_dynamic_catalog(),
    )

    assert decision.status == "unknown"
    assert decision.retrieval_filter() == {}


def test_explicit_query_identity_overrides_conflicting_session_document() -> None:
    query = "卡车发动机异响是什么原因？"
    decision = decide_scope(
        query,
        query_contract=QueryContract.from_mapping(
            {
                "raw_device_span": "卡车发动机",
                "device_category": "发动机",
                "carrier_or_application": "卡车",
            },
            raw_query=query,
        ),
        session_document_id=MANUAL_ID,
        catalog=_dynamic_catalog(),
    )

    assert decision.status == "out_of_scope"
    assert decision.reason == "explicit_device_switch"
    assert decision.retrieval_filter() == {}
