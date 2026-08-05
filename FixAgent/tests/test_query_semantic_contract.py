from __future__ import annotations

from services.intent_router import IntentDecision
from services.retrieval.device_identity import QueryContract


def test_query_contract_preserves_multiple_target_bindings() -> None:
    query = "查询星门耦联簇和月门泵组的装配数量与扭矩"
    contract = QueryContract.from_mapping(
        {
            "intent": "parameter_query",
            "task_action": "parameter_lookup",
            "requested_fields": ["quantity", "torque"],
            "targets": [
                {
                    "target_id": "target-1",
                    "raw_component_span": "星门耦联簇",
                    "component": "星门耦联簇",
                    "assembly_context": "装配",
                    "requested_fields": ["quantity", "torque"],
                },
                {
                    "target_id": "target-2",
                    "raw_component_span": "月门泵组",
                    "component": "月门泵组",
                    "assembly_context": "装配",
                    "requested_fields": ["quantity", "torque"],
                },
            ],
        },
        raw_query=query,
    )

    assert [target.component for target in contract.targets] == ["星门耦联簇", "月门泵组"]
    assert contract.targets[0].requested_fields == ("quantity", "torque")
    assert contract.targets[1].requested_fields == ("quantity", "torque")
    assert contract.requested_fields == ("quantity", "torque")


def test_query_contract_rejects_hallucinated_component_span() -> None:
    contract = QueryContract.from_mapping(
        {
            "targets": [
                {
                    "target_id": "target-1",
                    "raw_component_span": "不存在的部件",
                    "component": "不存在的部件",
                    "part_spec": "X-9000",
                }
            ]
        },
        raw_query="查询星门耦联簇的装配明细",
    )

    assert contract.targets == ()


def test_query_contract_round_trip_keeps_structured_semantics() -> None:
    query = "拆解甲侧星门耦联簇时检查规格Q7"
    original = QueryContract.from_mapping(
        {
            "raw_component_span": "甲侧星门耦联簇",
            "component": "星门耦联簇",
            "part_spec": "Q7",
            "assembly_context": "拆解工位",
            "action": "remove",
            "orientation": "甲侧",
            "requested_fields": ["inspection_item"],
            "symptoms": ["转动受阻"],
            "operating_conditions": ["拆解时"],
        },
        raw_query=query,
    )

    restored = QueryContract.from_mapping(original.to_dict(), raw_query=query)

    assert original.raw_component_span == "甲侧星门耦联簇"
    assert original.part_spec == "Q7"
    assert original.requested_fields == ("inspection_item",)
    assert original.symptoms == ("转动受阻",)
    assert original.operating_conditions == ("拆解时",)
    assert restored == original


def test_intent_decision_accepts_multi_target_schema_without_breaking_legacy_fields() -> None:
    decision = IntentDecision(
        intent="knowledge_query",
        task_action="document_explain",
        component="星门耦联簇",
        raw_component_span="星门耦联簇",
        requested_fields=["parts"],
        targets=[
            {
                "target_id": "target-1",
                "raw_component_span": "星门耦联簇",
                "component": "星门耦联簇",
                "requested_fields": ["parts"],
            }
        ],
    )

    assert decision.component == "星门耦联簇"
    assert decision.raw_component_span == "星门耦联簇"
    assert decision.targets[0]["component"] == "星门耦联簇"


def test_part_spec_plus_component_cannot_be_promoted_to_device_identity() -> None:
    query = "Q7×42星门法兰件的校正值是多少？"
    contract = QueryContract.from_mapping(
        {
            "raw_device_span": "Q7×42星门法兰件",
            "device_name": "Q7×42星门法兰件",
            "raw_component_span": "星门法兰件",
            "component": "星门法兰件",
            "part_spec": "Q7×42",
            "requested_fields": ["校正值"],
        },
        raw_query=query,
    )

    assert contract.raw_device_span == ""
    assert contract.device_name == ""
    assert contract.identity_resolution == "confirmed_absent"
    assert contract.raw_component_span == "星门法兰件"
    assert contract.part_spec == "Q7×42"
