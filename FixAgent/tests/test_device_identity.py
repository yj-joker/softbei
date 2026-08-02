"""Open-vocabulary query and document identity matching."""

from __future__ import annotations

from services.retrieval.device_identity import (
    DeviceCatalog,
    DocumentIdentity,
    QueryContract,
    compare_query_to_document,
)


def test_non_finite_confidence_never_authorizes_a_document() -> None:
    manifest = {
        "document_id": "doc-nan",
        "status": "ready",
        "document_identity": {
            "device_name": "测试设备",
            "confidence": "nan",
        },
    }

    assert DocumentIdentity.from_manifest(manifest) is None
    assert DeviceCatalog.from_manifests([manifest]).documents == ()


def test_manifest_without_ready_status_is_not_in_dynamic_catalog() -> None:
    manifest = {
        "document_id": "doc-no-status",
        "document_identity": {
            "device_name": "测试设备",
            "confidence": 0.95,
        },
    }

    assert DeviceCatalog.from_manifests([manifest]).documents == ()


MOTORCYCLE_MANIFEST = {
    "document_id": "manual-motorcycle",
    "status": "ready",
    "document_identity": {
        "device_name": "摩托车发动机",
        "device_category": "发动机",
        "carrier_or_application": "摩托车",
        "manufacturer": "",
        "model": "",
        "confidence": 0.96,
    },
}


def _contract(raw_query: str, **identity: str) -> QueryContract:
    return QueryContract.from_mapping(identity, raw_query=raw_query)


def test_dynamic_catalog_contains_only_ready_manifest_identities() -> None:
    catalog = DeviceCatalog.from_manifests(
        [
            MOTORCYCLE_MANIFEST,
            {
                "document_id": "failed-manual",
                "status": "failed",
                "document_identity": {
                    "device_name": "失效设备",
                    "device_category": "发动机",
                    "carrier_or_application": "试验平台",
                    "confidence": 0.99,
                },
            },
        ]
    )

    assert [item.document_id for item in catalog.documents] == ["manual-motorcycle"]


def test_matching_open_vocabulary_identity_binds_supported_document() -> None:
    catalog = DeviceCatalog.from_manifests([MOTORCYCLE_MANIFEST])
    query = _contract(
        "摩托车发动机的气缸活塞装配部件清单",
        raw_device_span="摩托车发动机",
        device_category="发动机",
        carrier_or_application="摩托车",
    )

    matches = catalog.match(query)

    assert len(matches) == 1
    assert matches[0].relation == "matched"
    assert matches[0].document.document_id == "manual-motorcycle"


def test_unseen_carrier_conflict_never_binds_motorcycle_manual() -> None:
    catalog = DeviceCatalog.from_manifests([MOTORCYCLE_MANIFEST])
    query = _contract(
        "履带起重机发动机异响是什么原因",
        raw_device_span="履带起重机发动机",
        device_category="发动机",
        carrier_or_application="履带起重机",
    )

    matches = catalog.match(query)

    assert len(matches) == 1
    assert matches[0].relation == "unmatched"
    assert set(matches[0].conflicts) == {"device_name", "carrier_or_application"}


def test_missing_carrier_is_uncertain_instead_of_authorizing_retrieval() -> None:
    document = DeviceCatalog.from_manifests([MOTORCYCLE_MANIFEST]).documents[0]
    query = _contract(
        "发动机异响是什么原因",
        raw_device_span="发动机",
        device_category="发动机",
    )

    result = compare_query_to_document(query, document)

    assert result.relation == "uncertain"


def test_generic_document_name_never_prefix_matches_a_more_specific_device() -> None:
    catalog = DeviceCatalog.from_manifests(
        [
            {
                "document_id": "manual-generic-engine",
                "status": "ready",
                "document_identity": {
                    "device_name": "发动机",
                    "device_category": "发动机",
                    "confidence": 0.96,
                },
            }
        ]
    )
    query = _contract(
        "飞机发动机异响是什么原因",
        raw_device_span="飞机发动机",
        device_name="飞机发动机",
        device_category="发动机",
        carrier_or_application="飞机",
    )

    result = catalog.match(query)[0]

    assert result.relation == "unmatched"
    assert result.conflicts == ("device_name",)


def test_grounded_raw_span_overrides_model_generic_device_name() -> None:
    catalog = DeviceCatalog.from_manifests([MOTORCYCLE_MANIFEST])
    query = _contract(
        "飞机发动机异响是什么原因",
        raw_device_span="飞机发动机",
        device_name="发动机",
        device_category="发动机",
    )

    assert query.device_name == "飞机发动机"
    assert catalog.match(query)[0].relation == "unmatched"


def test_model_and_manufacturer_conflicts_are_hard_scope_conflicts() -> None:
    catalog = DeviceCatalog.from_manifests(
        [
            {
                "document_id": "manual-a",
                "status": "ready",
                "document_identity": {
                    "device_name": "甲厂 X100 发动机",
                    "device_category": "发动机",
                    "carrier_or_application": "固定机组",
                    "manufacturer": "甲厂",
                    "model": "X100",
                    "confidence": 0.95,
                },
            }
        ]
    )
    query = _contract(
        "乙厂X200发动机怎么拆",
        raw_device_span="乙厂X200发动机",
        device_category="发动机",
        manufacturer="乙厂",
        model="X200",
    )

    result = catalog.match(query)[0]

    assert result.relation == "unmatched"
    assert set(result.conflicts) == {"device_name", "manufacturer", "model"}


def test_hallucinated_raw_device_span_invalidates_all_device_identity_fields() -> None:
    query = QueryContract.from_mapping(
        {
            "raw_device_span": "飞机发动机",
            "device_category": "发动机",
            "carrier_or_application": "飞机",
            "manufacturer": "虚构厂商",
        },
        raw_query="如何安装右曲轴箱盖",
    )

    assert query.raw_device_span == ""
    assert query.device_category == ""
    assert query.carrier_or_application == ""
    assert query.manufacturer == ""
    assert query.has_explicit_device is False


def test_same_device_name_is_not_rejected_for_different_category_taxonomies() -> None:
    catalog = DeviceCatalog.from_manifests(
        [
            {
                **MOTORCYCLE_MANIFEST,
                "document_identity": {
                    **MOTORCYCLE_MANIFEST["document_identity"],
                    "device_category": "内燃机",
                },
            }
        ]
    )
    query = _contract(
        "摩托车发动机异响是什么原因",
        raw_device_span="摩托车发动机",
        device_name="摩托车发动机",
        device_category="车辆动力系统",
    )

    result = catalog.match(query)[0]

    assert result.relation == "matched"
    assert "device_category" not in result.conflicts


def test_distinct_open_vocabulary_device_names_conflict_without_alias_registry() -> None:
    catalog = DeviceCatalog.from_manifests([MOTORCYCLE_MANIFEST])
    query = _contract(
        "卡车发动机异响是什么原因",
        raw_device_span="卡车发动机",
        device_name="卡车发动机",
        device_category="车辆",
    )

    result = catalog.match(query)[0]

    assert result.relation == "unmatched"
    assert result.conflicts == ("device_name",)
