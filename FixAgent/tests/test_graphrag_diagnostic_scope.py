import asyncio

from api.main import (
    _collect_direct_section_table_items,
    _format_inventory_table_answer_from_metadata,
    _format_manual_evidence_answer_from_metadata,
)


def test_inventory_table_answer_does_not_override_fault_diagnosis() -> None:
    metadata = {
        "route_plan": {
            "action": "grounded_retrieval",
            "intent": "fault_diagnosis",
            "task_action": "find_cause",
            "query_contract": {
                "intent": "fault_diagnosis",
                "task_action": "find_cause",
                "component": "助力油泵保险",
            },
        },
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": [{
                    "content": "故障现象 | 处理建议",
                    "metadata": {
                        "chunk_type": "table",
                        "section_title": "6.4 助力油泵",
                        "parent_section_id": "sec-pump",
                        "page": 60,
                        "table_full": {
                            "headers": ["故障现象", "处理建议"],
                            "rows": [
                                ["保险熔断", "更换同种规格保险"],
                                ["油泵不工作", "检查线路"],
                            ],
                        },
                    },
                }],
            }],
        }],
    }

    answer = _format_inventory_table_answer_from_metadata(
        "助力油泵保险熔断，请说明这是哪个部件的故障及处理建议。",
        metadata,
    )

    assert answer is None


def test_direct_section_table_lookup_does_not_expand_fault_diagnosis(monkeypatch) -> None:
    calls = 0

    def get_vector_service():
        nonlocal calls
        calls += 1
        raise AssertionError("diagnostic requests must not expand to a full section table")

    monkeypatch.setattr(
        "services.knowledge.vector_service.get_vector_service",
        get_vector_service,
    )
    metadata = {
        "route_plan": {
            "action": "grounded_retrieval",
            "entity_role": "document_component",
            "selected_document_id": "manual-1",
            "selected_section_id": "sec-pump",
            "intent": "fault_diagnosis",
            "task_action": "find_cause",
            "query_contract": {
                "intent": "fault_diagnosis",
                "task_action": "find_cause",
                "component": "助力油泵保险",
            },
        },
        "retrieval_scope": {
            "document_id": "manual-1",
            "allowed_section_ids": ["sec-pump"],
            "allowed_evidence_refs": ["fault-table-7"],
        },
    }

    result = asyncio.run(_collect_direct_section_table_items(
        "助力油泵保险熔断，请说明这是哪个部件的故障及处理建议。",
        metadata,
    ))

    assert result == []
    assert calls == 0


def test_manual_evidence_answer_does_not_override_fault_diagnosis(monkeypatch) -> None:
    calls = 0

    def best_section_records(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("diagnostic answers must preserve graph/manual evidence fusion")

    monkeypatch.setattr("api.main._manual_best_section_records", best_section_records)
    metadata = {
        "route_plan": {
            "action": "grounded_retrieval",
            "intent": "fault_diagnosis",
            "task_action": "find_cause",
            "query_contract": {
                "intent": "fault_diagnosis",
                "task_action": "find_cause",
                "component": "助力油泵保险",
            },
        },
    }

    answer = _format_manual_evidence_answer_from_metadata(
        "助力油泵保险熔断，请说明这是哪个部件的故障及处理建议。",
        metadata,
    )

    assert answer is None
    assert calls == 0
