"""Deterministic retrieval scope decisions."""

from __future__ import annotations

from services.retrieval.scope import ScopeRegistry, decide_scope, get_scope_registry


MANUAL_ID = "manual-motorcycle"


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
            "external_devices": [
                {
                    "device_type": "aircraft-piston-engine",
                    "display_name": "飞机活塞发动机",
                    "aliases": ["飞机活塞发动机", "飞机发动机", "航空发动机"],
                },
                {
                    "device_type": "diesel-generator",
                    "display_name": "柴油发电机",
                    "aliases": ["柴油发电机", "diesel-generator"],
                },
            ],
        }
    )


def test_aircraft_query_is_rejected_against_motorcycle_manual() -> None:
    decision = decide_scope(
        "飞机活塞发动机功率下降有哪些常见原因？",
        request_document_id=MANUAL_ID,
        request_device_type="aircraft-piston-engine",
        registry=_registry(),
    )

    assert decision.status == "out_of_scope"
    assert decision.document_id == MANUAL_ID
    assert decision.detected_device_type == "aircraft-piston-engine"
    assert decision.reason == "device_document_conflict"


def test_aircraft_phrase_with_separated_engine_term_is_detected_without_explicit_scope() -> None:
    decision = decide_scope(
        "飞机在运行时发动机出现异响是什么原因？",
        registry=get_scope_registry(),
    )

    assert decision.status == "out_of_scope"
    assert decision.detected_device_type == "aircraft-piston-engine"
    assert decision.reason == "unsupported_device"


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


def test_explicit_device_switch_invalidates_confirmed_session_scope() -> None:
    decision = decide_scope(
        "改问柴油发电机，启动困难先检查什么？",
        session_document_id=MANUAL_ID,
        registry=_registry(),
    )

    assert decision.status == "out_of_scope"
    assert decision.detected_device_type == "diesel-generator"
    assert decision.reason == "explicit_device_switch"


def test_audited_query_alias_can_bind_a_supported_manual() -> None:
    decision = decide_scope(
        "摩托车发动机的火花塞间隙是多少？",
        registry=_registry(),
    )

    assert decision.status == "in_scope"
    assert decision.source == "audited_alias"
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
    decision = decide_scope(
        "改问柴油发电机，启动困难先检查什么？",
        session_document_id=MANUAL_ID,
        registry=_registry(),
    )

    assert decision.status == "out_of_scope"
    assert decision.retrieval_filter() == {"document_id": "", "device_type": ""}
