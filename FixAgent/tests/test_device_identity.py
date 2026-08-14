"""Open-vocabulary query and document identity matching."""

from __future__ import annotations

from dataclasses import replace

import pytest

import services.retrieval.device_identity as device_identity
from services.retrieval.device_identity import (
    DeviceCatalog,
    DocumentIdentity,
    QueryContract,
    compare_query_to_document,
    infer_query_identity_from_catalog,
    reconcile_query_device_span,
)


def test_overcaptured_device_span_preserves_raw_text_and_normalizes_catalog_identity():
    catalog = DeviceCatalog((
        DocumentIdentity(
            document_id="manual-motorcycle",
            device_name="摩托车发动机",
            device_category="发动机",
            carrier_or_application="摩托车",
            confidence=0.96,
        ),
    ))
    query = QueryContract.from_mapping(
        {
            "raw_device_span": "摩托车发动机气缸活塞",
            "device_name": "摩托车发动机气缸活塞",
            "component": "气缸活塞",
            "raw_component_span": "气缸活塞",
        },
        raw_query="摩托车发动机气缸活塞装配部件清单",
    )

    reconciled = reconcile_query_device_span(query, catalog)

    assert reconciled.raw_device_span == "摩托车发动机气缸活塞"
    assert reconciled.device_name == "摩托车发动机"
    assert reconciled.identity_resolution == "catalog_exact"
    assert reconciled.component == "气缸活塞"


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


def test_catalog_binds_literal_imported_identity_when_model_omits_device_span() -> None:
    catalog = DeviceCatalog.from_manifests([MOTORCYCLE_MANIFEST])
    query = _contract(
        "摩托车发动机有异响，可能是什么原因？",
        intent="fault_diagnosis",
        task_action="find_cause",
        symptoms=("摩托车发动机有异响",),
    )

    bound = infer_query_identity_from_catalog(query, catalog)

    assert bound.raw_device_span == "摩托车发动机"
    assert bound.device_name == "摩托车发动机"
    assert bound.carrier_or_application == "摩托车"
    assert bound.identity_resolution == "catalog_exact"


def test_catalog_does_not_bind_generic_or_ambiguous_identity_heads() -> None:
    catalog = DeviceCatalog((
        DocumentIdentity(
            "manual-a",
            "摩托车发动机",
            device_category="发动机",
            carrier_or_application="摩托车",
            confidence=0.96,
        ),
        DocumentIdentity(
            "manual-b",
            "卡车发动机",
            device_category="发动机",
            carrier_or_application="卡车",
            confidence=0.96,
        ),
    ))

    generic = _contract("发动机有异响", intent="fault_diagnosis", task_action="find_cause")
    ambiguous = _contract("发动机冒蓝烟", intent="fault_diagnosis", task_action="find_cause")

    assert infer_query_identity_from_catalog(generic, catalog).raw_device_span == ""
    assert infer_query_identity_from_catalog(ambiguous, catalog).raw_device_span == ""


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


def test_grounded_raw_span_controls_effective_unsigned_device_identity() -> None:
    catalog = DeviceCatalog.from_manifests([MOTORCYCLE_MANIFEST])
    query = _contract(
        "飞机发动机异响是什么原因",
        raw_device_span="飞机发动机",
        device_name="发动机",
        device_category="发动机",
    )

    assert query.device_name == "发动机"
    assert query.effective_device_identity == "飞机发动机"
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


def test_component_and_orientation_are_not_promoted_to_device_identity() -> None:
    query = QueryContract.from_mapping(
        {
            "raw_device_span": "右曲轴箱盖",
            "device_name": "右曲轴箱盖",
            "device_category": "发动机部件",
            "carrier_or_application": "",
            "manufacturer": "",
            "model": "",
            "component": "曲轴箱盖",
            "action": "安装",
            "orientation": "右",
        },
        raw_query="如何安装右曲轴箱盖",
    )

    assert query.has_explicit_device is False
    assert query.device_name == ""
    assert query.component == "曲轴箱盖"
    assert query.action == "安装"
    assert query.orientation == "右"


def test_component_with_missing_orientation_is_not_promoted_to_device_identity() -> None:
    query = QueryContract.from_mapping(
        {
            "raw_device_span": "右曲轴箱盖",
            "device_name": "右曲轴箱盖",
            "device_category": "发动机部件",
            "component": "曲轴箱盖",
            "action": "安装",
            "orientation": "",
        },
        raw_query="如何安装右曲轴箱盖",
    )

    assert query.has_explicit_device is False
    assert query.device_name == ""
    assert query.component == "曲轴箱盖"


def test_operation_target_classified_as_component_is_not_promoted_to_device_identity() -> None:
    query = QueryContract.from_mapping(
        {
            "raw_device_span": "右盖",
            "device_name": "右盖",
            "device_category": "机械部件",
            "component": "曲轴油封, 离合器拉杆",
            "action": "安装",
        },
        raw_query="安装右盖时曲轴油封和离合器拉杆要注意什么？",
    )

    assert query.has_explicit_device is False
    assert query.device_name == ""
    assert query.component == "曲轴油封, 离合器拉杆"


def test_short_device_prefix_is_preserved_when_category_is_not_component_like() -> None:
    query = QueryContract.from_mapping(
        {
            "raw_device_span": "A型泵",
            "device_name": "A型泵",
            "device_category": "工业设备",
            "component": "泵",
            "model": "",
        },
        raw_query="A型泵如何安装",
    )

    assert query.has_explicit_device is True
    assert query.device_name == "A型泵"


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


def test_device_name_with_component_suffix_matches_dynamic_document_identity() -> None:
    catalog = DeviceCatalog.from_manifests([MOTORCYCLE_MANIFEST])
    query = _contract(
        "帮我查询摩托车发动机气缸活塞装配部件清单",
        raw_device_span="摩托车发动机气缸活塞",
        device_name="摩托车发动机气缸活塞",
        component="气缸活塞",
    )

    result = catalog.match(query)[0]

    assert result.relation == "matched"
    assert result.conflicts == ()


def _normalize(query: QueryContract, catalog: DeviceCatalog):
    normalizer = getattr(device_identity, "normalize_query_identity", None)
    assert callable(normalizer), "normalize_query_identity must be implemented"
    return normalizer(query, catalog)


@pytest.mark.parametrize(
    ("raw_span", "component", "raw_component_span", "carrier"),
    [
        ("摩托车发动机的火花塞", "火花塞", "火花塞", "摩托车发动机"),
        ("摩托车发动机机油泵", "机油泵从动齿轮", "机油泵从动齿轮", "摩托车"),
        ("摩托车发动机的传动装置", "拨叉", "拨叉", "传动装置"),
    ],
)
def test_composite_device_span_preserves_raw_text_and_signs_catalog_identity(
    raw_span: str,
    component: str,
    raw_component_span: str,
    carrier: str,
) -> None:
    catalog = DeviceCatalog.from_manifests([MOTORCYCLE_MANIFEST])
    raw_query = f"{raw_span}出现{raw_component_span}损坏"
    query = QueryContract.from_mapping(
        {
            "raw_device_span": raw_span,
            "device_name": raw_span,
            "carrier_or_application": carrier,
            "component": component,
            "raw_component_span": raw_component_span,
        },
        raw_query=raw_query,
    )

    result = _normalize(query, catalog)

    assert result.contract.raw_device_span == raw_span
    assert result.contract.device_name == "摩托车发动机"
    assert result.contract.carrier_or_application == "摩托车"
    assert result.contract.identity_resolution == "catalog_exact"
    assert result.matched_document_id == "manual-motorcycle"
    assert result.status == "matched"


def test_composite_device_span_recovers_grounded_parent_component_when_model_extracts_fault_part() -> None:
    catalog = DeviceCatalog.from_manifests([MOTORCYCLE_MANIFEST])
    raw_query = "摩托车发动机的气缸总成出现气缸内壁损伤时如何处理"
    query = QueryContract.from_mapping(
        {
            "raw_device_span": "摩托车发动机的气缸总成",
            "device_name": "摩托车发动机的气缸总成",
            "component": "气缸内壁",
            "raw_component_span": "气缸内壁",
            "fault": "气缸内壁损伤",
            "raw_fault_span": "气缸内壁损伤",
        },
        raw_query=raw_query,
    )

    result = _normalize(query, catalog)

    assert result.status == "matched"
    assert result.contract.identity_resolution == "catalog_exact"
    assert result.contract.raw_device_span == "摩托车发动机的气缸总成"
    assert result.contract.component == "气缸总成"
    assert result.contract.raw_component_span == "气缸总成"
    assert result.contract.fault == "气缸内壁损伤"


def test_composite_component_recovery_never_promotes_an_external_device() -> None:
    catalog = DeviceCatalog.from_manifests([MOTORCYCLE_MANIFEST])
    query = QueryContract.from_mapping(
        {
            "raw_device_span": "飞机发动机的气缸总成",
            "device_name": "飞机发动机的气缸总成",
            "component": "气缸内壁",
            "raw_component_span": "气缸内壁",
            "fault": "气缸内壁损伤",
            "raw_fault_span": "气缸内壁损伤",
        },
        raw_query="飞机发动机的气缸总成出现气缸内壁损伤时如何处理",
    )

    result = _normalize(query, catalog)

    assert result.status == "unmatched"
    assert result.contract.identity_resolution == ""
    assert result.contract.raw_device_span == "飞机发动机的气缸总成"
    assert result.contract.component == "气缸内壁"


def test_catalog_identity_normalization_is_idempotent() -> None:
    catalog = DeviceCatalog.from_manifests([MOTORCYCLE_MANIFEST])
    query = QueryContract.from_mapping(
        {
            "raw_device_span": "摩托车发动机的火花塞",
            "device_name": "摩托车发动机的火花塞",
            "component": "火花塞",
            "raw_component_span": "火花塞",
        },
        raw_query="摩托车发动机的火花塞损坏",
    )

    once = _normalize(query, catalog).contract
    twice = _normalize(once, catalog).contract

    assert twice == once


def test_unverified_model_device_name_cannot_override_grounded_external_span() -> None:
    catalog = DeviceCatalog.from_manifests([MOTORCYCLE_MANIFEST])
    query = QueryContract.from_mapping(
        {
            "raw_device_span": "飞机发动机的火花塞",
            "device_name": "摩托车发动机",
            "identity_resolution": "catalog_exact",
            "component": "火花塞",
            "raw_component_span": "火花塞",
        },
        raw_query="飞机发动机的火花塞损坏",
    )

    result = _normalize(query, catalog)

    assert query.device_name == "摩托车发动机"
    assert query.identity_resolution == ""
    assert result.contract.effective_device_identity == "飞机发动机的火花塞"
    assert result.contract.identity_resolution == ""
    assert result.matched_document_id == ""
    assert result.status == "unmatched"


def test_generic_identity_and_ambiguous_alias_are_never_promoted() -> None:
    shared_alias = "试验发动机"
    catalog = DeviceCatalog((
        replace(
            DeviceCatalog.from_manifests([MOTORCYCLE_MANIFEST]).documents[0],
            aliases=(shared_alias,),
        ),
        DocumentIdentity(
            document_id="manual-truck",
            device_name="卡车发动机",
            device_category="发动机",
            carrier_or_application="卡车",
            confidence=0.96,
            aliases=(shared_alias,),
        ),
    ))
    generic = QueryContract.from_mapping(
        {"raw_device_span": "发动机", "device_name": "发动机"},
        raw_query="发动机异响",
    )
    ambiguous = QueryContract.from_mapping(
        {"raw_device_span": shared_alias, "device_name": shared_alias},
        raw_query=f"{shared_alias}异响",
    )

    assert _normalize(generic, catalog).matched_document_id == ""
    assert _normalize(ambiguous, catalog).matched_document_id == ""
