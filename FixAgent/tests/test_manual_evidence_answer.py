"""Deterministic manual evidence answer regressions."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.main import (
    _direct_manual_text_supports_query,
    _direct_manual_text_supports_aspect,
    _format_manual_evidence_answer_from_metadata,
    _manual_action_target,
    _manual_answer_should_refuse_detail_query,
    _manual_best_section_records,
    _manual_append_unique_records,
    _manual_query_action,
    _manual_query_kind,
    _manual_strip_next_procedure_heading,
    _manual_overrides_blocked_by_low_confidence,
    _response_policy_direct_must_defer,
)
from services.retrieval.response_plan import ResponsePlan, finalize_response


def _component_route(document_id: str, component: str, action: str) -> dict:
    return {
        "action": "grounded_retrieval",
        "entity_role": "document_component",
        "selected_document_id": document_id,
        "query_contract": {"component": component, "action": action},
    }


def test_directional_evidence_supports_inverse_orientation_answer() -> None:
    direct_text = "气门弹簧间距较密的一端必须朝下安装。"

    assert _direct_manual_text_supports_query(
        direct_text,
        "气门弹簧哪一端朝上安装？",
    )


def test_exact_section_title_can_authorize_broad_procedure_query() -> None:
    direct_text = "6.8 水泵\n1. 取下水泵盖。\n2. 安装水泵组件。"

    assert _direct_manual_text_supports_query(
        direct_text,
        "水泵应该怎样拆装？",
    )


def test_section_title_action_prefix_can_authorize_fault_treatment_query() -> None:
    direct_text = (
        "1.2 检查火花塞\n"
        "检查火花塞螺纹以及中心电极处，若有损坏或变形，则应更换火花塞。"
    )

    assert _direct_manual_text_supports_query(
        direct_text,
        "摩托车发动机的火花塞出现火花塞损坏时应如何处理？请说明故障所属部件和手册依据。",
    )


def test_graph_diagnostic_registers_manual_records_without_overriding_composed_answer(
    monkeypatch,
) -> None:
    from api import main as api_main

    record = {
        "id": "spark-plug-step",
        "content": "检查火花塞，若有损坏或变形，则应更换火花塞。",
        "metadata": {
            "qualification": "qualified",
            "document_id": "manual-doc",
            "document_version": "v1",
            "parent_section_id": "sec-spark",
            "chunk_uid": "sec-spark:text:0000",
            "section_title": "1.2 检查火花塞",
            "page": 3,
        },
    }
    metadata = {
        "route_plan": {
            **_component_route("manual-doc", "火花塞", "处理"),
            "selected_section_id": "sec-spark",
        },
        "graph_pre_retrieval": {
            "status": "found",
            "evidence": [{"qualification": "qualified"}],
        },
        "scope_decision": {"document_id": "manual-doc", "status": "in_scope"},
    }
    monkeypatch.setattr(
        api_main,
        "_manual_best_section_records",
        lambda message, kind, context: [record],
    )

    answer = _format_manual_evidence_answer_from_metadata(
        "火花塞损坏时应如何处理？",
        metadata,
    )

    assert answer is None
    assert metadata["_manual_evidence_registered_for_graph_composition"] is True
    assert metadata["_deterministic_answer_mode"] == "evidence_rendered"
    assert any(
        (call.get("arguments") or {}).get("source") == "section_text_lookup"
        for step in metadata["react_trace"]
        for call in step.get("tool_calls") or []
    )


def test_final_audit_preserves_authorized_rendered_answer_with_partial_coverage() -> None:
    plan = ResponsePlan(
        plan_id="test-plan",
        coverage_status="unsupported",
        source_mode="normal",
        allowed_evidence=(
            {
                "evidence_id": "manual:doc:chunk",
                "source_type": "manual",
                "qualification": "qualified",
                "text": "水泵拆卸步骤",
                "source": {"document_id": "doc", "chunk_id": "chunk", "page": 20},
            },
        ),
        missing_aspects=("水泵拆装",),
        conflicts=(),
        ledger_digest="digest",
    )

    result = finalize_response(
        plan,
        "可以按以下顺序操作：\n1. 拆下水泵盖。",
        evidence_rendered=True,
    )

    assert result.passed
    assert "拆下水泵盖" in result.answer


def test_low_confidence_does_not_block_authorized_structural_section_recovery() -> None:
    metadata = {
        "low_confidence_retrieval": True,
        "route_plan": _component_route("manual-doc", "水泵", "拆装"),
    }

    assert not _manual_overrides_blocked_by_low_confidence(metadata)


def test_insufficient_policy_defers_to_authorized_structural_retrieval() -> None:
    context = {
        "response_policy": {"mode": "INSUFFICIENT_EVIDENCE"},
        "route_plan": _component_route("manual-doc", "水泵", "拆装"),
    }

    assert _response_policy_direct_must_defer(context)


def test_manual_query_action_recognizes_adjustment_family() -> None:
    assert _manual_query_action("如何调整气门间隙？") == "调整"
    assert _manual_query_action("怎样调节气门间隙？") == "调整"
    assert _manual_query_action("校正气门间隙的步骤") == "调整"
    assert _manual_action_target("怎样调节气门间隙？", "调整") == "气门间隙"
    assert _manual_query_kind("怎样调节气门间隙？") == "procedure"


def test_manual_action_target_strips_generic_procedure_question_suffix() -> None:
    assert _manual_action_target("这个曲轴箱盖的拆卸步骤是什么？", "拆卸") == "曲轴箱盖"


def test_authorized_structural_lookup_initializes_vector_service_lazily(monkeypatch) -> None:
    """章节恢复不能依赖此前是否碰巧执行过其他检索工具。"""

    class FakeVectorService:
        def get_section_records(self, document_id, parent_section_id, limit=80, chunk_type=None):
            assert document_id == "manual-doc"
            assert parent_section_id == "sec-actuator"
            return [
                {
                    "doc_id": "step-1",
                    "text": "1. 先释放执行机构中的残余压力。",
                    "metadata": {
                        "chunk_type": "step_raw",
                        "document_id": "manual-doc",
                        "parent_section_id": "sec-actuator",
                        "section_title": "4.2 拆卸执行机构",
                        "page": 8,
                    },
                },
                {
                    "doc_id": "step-2",
                    "text": "2. 再对角松开固定螺栓。",
                    "metadata": {
                        "chunk_type": "step_raw",
                        "document_id": "manual-doc",
                        "parent_section_id": "sec-actuator",
                        "section_title": "4.2 拆卸执行机构",
                        "page": 8,
                    },
                },
            ]

    class FakeSectionIndex:
        def build(self, vector_service):
            assert isinstance(vector_service, FakeVectorService)

        def find(self, query):
            return [
                SimpleNamespace(
                    document_id="manual-doc",
                    section_id="sec-actuator",
                )
            ]

    from services.knowledge import vector_service as vector_service_module
    from services.retrieval.section_index import SectionTitleIndex

    fake_service = FakeVectorService()

    def get_vector_service():
        return fake_service

    monkeypatch.setattr(vector_service_module, "_vector_service", None)
    monkeypatch.setattr(vector_service_module, "get_vector_service", get_vector_service)
    monkeypatch.setattr(SectionTitleIndex, "get_instance", classmethod(lambda cls: FakeSectionIndex()))

    metadata = {
        "route_plan": _component_route("manual-doc", "执行机构", "拆卸"),
        "scope_decision": {"document_id": "manual-doc"},
    }

    answer = _format_manual_evidence_answer_from_metadata("如何拆卸执行机构？", metadata)

    assert answer is not None
    assert answer.index("先释放执行机构中的残余压力") < answer.index("再对角松开固定螺栓")


def test_direct_manual_support_allows_source_modifiers_inside_query_anchor() -> None:
    direct_text = (
        "4.2 拆卸执行机构\n"
        "2. 松开执行机构所有固定螺栓：先松下部，再对角松上部。"
    )

    assert _direct_manual_text_supports_query(
        direct_text,
        "拆卸执行机构时松开固定螺栓，是先松上部还是下部？",
    )


def test_exact_title_matches_outrank_seed_retrieval_section_labels(monkeypatch) -> None:
    from api import main as api_main

    correct = {
        "id": "valve-gap-adjustment",
        "content": "调整气门间隙。",
        "metadata": {
            "document_id": "manual-doc",
            "parent_section_id": "sec-valve-gap",
            "section_title": "4.6 气门间隙",
            "page": 15,
        },
    }
    wrong = {
        "id": "valve-removal",
        "content": "拆卸气门组件。",
        "metadata": {
            "document_id": "manual-doc",
            "parent_section_id": "sec-valve-removal",
            "section_title": "4.8 气门",
            "section_match_ids": ["sec-valve-removal"],
            "page": 16,
        },
    }
    metadata = {
        "route_plan": _component_route("manual-doc", "气门间隙", "调整"),
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": [wrong],
            }],
        }],
    }

    monkeypatch.setattr(
        api_main,
        "_manual_title_match_records",
        lambda message, document_id="": ([correct], {"sec-valve-gap"}),
    )
    monkeypatch.setattr(api_main, "_manual_title_section_match_scores", lambda message: {})
    monkeypatch.setattr(
        api_main,
        "_manual_group_score",
        lambda message, kind, records, bonus_ids, title_scores: (
            (504 if records[0]["id"] == "valve-gap-adjustment" else 523)
            + (80 if records[0]["metadata"]["parent_section_id"] in bonus_ids else 0)
        ),
    )
    monkeypatch.setattr(api_main, "_manual_expand_same_section_records", lambda records, ids, **kwargs: records)
    monkeypatch.setattr(api_main, "_manual_expand_page_boundary_records", lambda records, ids, **kwargs: records)

    records = _manual_best_section_records("如何调整气门间隙？", "procedure", metadata)

    assert [record["id"] for record in records] == ["valve-gap-adjustment"]


def test_detail_query_accepts_structured_multi_part_torque_evidence() -> None:
    query = "安装发动机时放油螺栓和放水螺栓的拧紧力矩是多少？"
    records = [
        {
            "content": (
                "安装左曲轴箱放油螺栓、车架放油螺栓、水泵盖放水螺栓。\n"
                "拧紧力矩要求：\n"
                "放油螺栓：25 ± 4 N·m\n"
                "放水螺栓：12 ± 1.5 N·m"
            )
        }
    ]

    assert not _manual_answer_should_refuse_detail_query(query, records)


def test_procedure_body_line_starting_with_action_is_not_treated_as_next_heading() -> None:
    content = (
        "3. 装上定位销和全新的右曲轴箱盖垫片：\n"
        "安装新垫片之前，在装配平面上均匀涂抹密封胶；\n"
        "安装新垫片之后，盖上箱盖并对角预紧螺栓。"
    )
    metadata = {"toc_path": "手册 > 6.4 右曲轴箱盖与离合器 > 安装右盖"}

    result = _manual_strip_next_procedure_heading(content, metadata)

    assert "安装新垫片之前" in result
    assert "安装新垫片之后" in result


def test_procedure_body_colon_label_does_not_truncate_following_diagnosis() -> None:
    content = (
        "4. 测量压缩压力：\n"
        "测量步骤：\n"
        "a. 将油门开到最大处并再次测量。\n"
        "结果分析：\n"
        "若压力升高，则检查活塞环。"
    )
    metadata = {"toc_path": "手册 > 1.4 测量压缩压力"}

    result = _manual_strip_next_procedure_heading(content, metadata)

    assert "测量步骤" in result
    assert "若压力升高，则检查活塞环" in result


def test_retrieval_expansion_after_action_uses_the_preceding_operation_target() -> None:
    expanded_aspect = "气缸与活塞 安装步骤 要求 注意事项"
    direct_text = "5.4 安装气缸与活塞。依次安装活塞销和活塞销挡圈。"

    assert _manual_action_target(expanded_aspect, "安装") == "气缸与活塞"
    assert _direct_manual_text_supports_aspect(direct_text, expanded_aspect)


def test_explicit_operation_detail_keeps_the_complete_document_subflow(monkeypatch) -> None:
    from api import main as api_main

    monkeypatch.setattr(api_main, "_manual_title_match_records", lambda message, document_id="": ([], set()))
    monkeypatch.setattr(api_main, "_manual_title_section_match_scores", lambda message: {})
    monkeypatch.setattr(
        api_main,
        "_manual_expand_same_section_records",
        lambda records, section_ids, **kwargs: records,
    )
    monkeypatch.setattr(
        api_main,
        "_manual_expand_page_boundary_records",
        lambda records, section_ids, **kwargs: records,
    )
    metadata = {
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": [
                    {
                        "id": "seal-step",
                        "content": "1. 密封处理\n在气缸头盖垫片两面涂抹耐热平面密封硅胶。",
                        "metadata": {
                            "document_id": "manual-doc",
                            "parent_section_id": "sec-head-cover",
                            "section_title": "4.5 气缸头盖",
                            "chunk_type": "step_raw",
                            "page": 13,
                            "source_index": 1,
                            "toc_path": "手册 > 4.5 气缸头盖 > 安装气缸头盖",
                        },
                    },
                    {
                        "id": "tighten-step",
                        "content": "2. 安装与拧紧\n安装气缸头盖并对角均匀拧紧至规定扭矩。",
                        "metadata": {
                            "document_id": "manual-doc",
                            "parent_section_id": "sec-head-cover",
                            "section_title": "4.5 气缸头盖",
                            "chunk_type": "step_raw",
                            "page": 14,
                            "source_index": 2,
                            "toc_path": "手册 > 4.5 气缸头盖 > 安装气缸头盖",
                        },
                    },
                ],
            }],
        }],
    }

    records = _manual_best_section_records(
        "安装气缸头盖时哪些地方要涂耐热平面密封硅胶？",
        "evidence",
        metadata,
    )

    assert [record["id"] for record in records] == ["seal-step", "tighten-step"]


def test_title_match_lookup_rejects_opposite_direction_section(monkeypatch) -> None:
    class FakeVectorService:
        def get_section_records(self, document_id, section_id, limit=80, chunk_type=None):
            title = {
                "sec-left": "7.4 左曲轴箱盖",
                "sec-right": "6.4 右曲轴箱盖",
            }[section_id]
            return [{
                "id": section_id,
                "content": f"安装{title[4:]}垫片。",
                "metadata": {
                    "document_id": document_id,
                    "parent_section_id": section_id,
                    "section_title": title,
                    "chunk_type": "step_raw",
                },
            }]

    class FakeSectionIndex:
        def build(self, vector_service):
            return None

        def find(self, query):
            return [
                SimpleNamespace(section_id="sec-left", document_id="manual-doc"),
                SimpleNamespace(section_id="sec-right", document_id="manual-doc"),
            ]

    from api import main as api_main
    from services.knowledge import vector_service as vector_service_module
    from services.retrieval.section_index import SectionTitleIndex

    monkeypatch.setattr(vector_service_module, "get_vector_service", lambda: FakeVectorService())
    monkeypatch.setattr(SectionTitleIndex, "get_instance", classmethod(lambda cls: FakeSectionIndex()))

    records, section_ids = api_main._manual_title_match_records("如何安装右曲轴箱盖")

    assert section_ids == {"sec-right"}
    assert [record["metadata"]["section_title"] for record in records] == ["6.4 右曲轴箱盖"]


def test_title_match_lookup_stays_within_scoped_document(monkeypatch) -> None:
    class FakeVectorService:
        def get_section_records(self, document_id, section_id, limit=80, chunk_type=None):
            return [{
                "id": f"{document_id}:{section_id}",
                "content": "安装右曲轴箱盖垫片。",
                "metadata": {
                    "document_id": document_id,
                    "parent_section_id": section_id,
                    "section_title": "6.4 右曲轴箱盖",
                    "chunk_type": "step_raw",
                },
            }]

    class FakeSectionIndex:
        def build(self, vector_service):
            return None

        def find(self, query):
            return [
                SimpleNamespace(section_id="sec-wrong", document_id="other-manual"),
                SimpleNamespace(section_id="sec-right", document_id="selected-manual"),
            ]

    from api import main as api_main
    from services.knowledge import vector_service as vector_service_module
    from services.retrieval.section_index import SectionTitleIndex

    monkeypatch.setattr(vector_service_module, "get_vector_service", lambda: FakeVectorService())
    monkeypatch.setattr(SectionTitleIndex, "get_instance", classmethod(lambda cls: FakeSectionIndex()))

    records, section_ids = api_main._manual_title_match_records("如何安装右曲轴箱盖")
    records = api_main._manual_records_for_scoped_document(
        records,
        {"scope_decision": {"status": "in_scope", "document_id": "selected-manual"}},
    )
    section_ids = {record["metadata"]["parent_section_id"] for record in records}

    assert section_ids == {"sec-right"}
    assert {record["metadata"]["document_id"] for record in records} == {"selected-manual"}


def test_manual_records_stay_within_resolved_section_scope() -> None:
    from api import main as api_main

    records = [
        {
            "id": "selected-step",
            "content": "选中章节的操作步骤。",
            "metadata": {
                "document_id": "manual-doc",
                "parent_section_id": "sec:selected",
            },
        },
        {
            "id": "other-step",
            "content": "其他章节的操作步骤。",
            "metadata": {
                "document_id": "manual-doc",
                "parent_section_id": "sec:other",
            },
        },
    ]

    filtered = api_main._manual_records_for_scoped_document(
        records,
        {
            "scope_decision": {
                "status": "in_scope",
                "document_id": "manual-doc",
            },
            "retrieval_scope": {
                "document_id": "manual-doc",
                "allowed_section_ids": ["sec:selected"],
            },
        },
    )

    assert [record["id"] for record in filtered] == ["selected-step"]


def test_structural_recovery_uses_server_selected_section_without_title_in_query(monkeypatch) -> None:
    from api import main as api_main

    class FakeVectorService:
        def get_section_records(self, document_id, section_id, limit=80, chunk_type=None):
            return [{
                "id": "install-thrust-washer",
                "content": "1. 安装止推垫圈并确认其贴合。",
                "metadata": {
                    "document_id": document_id,
                    "parent_section_id": section_id,
                    "section_title": "6.4 右曲轴箱盖与离合器",
                    "chunk_type": "step_raw",
                    "page": 26,
                },
            }]

    monkeypatch.setattr(
        api_main,
        "_initialized_or_injected_vector_service",
        lambda **kwargs: FakeVectorService(),
    )
    monkeypatch.setattr(api_main, "_manual_title_match_records", lambda *args: ([], set()))
    monkeypatch.setattr(api_main, "_manual_title_section_match_scores", lambda message: {})
    metadata = {
        "route_plan": _component_route("manual-doc", "止推垫圈", "安装") | {
            "selected_section_id": "sec-right-cover",
        },
        "scope_decision": {"status": "in_scope", "document_id": "manual-doc"},
        "retrieval_scope": {
            "document_id": "manual-doc",
            "allowed_section_ids": ["sec-right-cover"],
        },
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": [],
            }],
        }],
    }

    records = _manual_best_section_records("止推垫圈怎么安装？", "procedure", metadata)

    assert [record["id"] for record in records] == ["install-thrust-washer"]
    assert records[0]["metadata"]["_structural_recovery_lookup_source"] == "section_text_lookup"


def test_structural_recovery_uses_authorized_record_ids_when_section_search_is_empty(monkeypatch) -> None:
    """澄清后的章节恢复不能因 RediSearch 瞬时空结果丢失已授权文本。"""
    from api import main as api_main

    class FakeVectorService:
        def get_section_records(self, document_id, section_id, limit=80, chunk_type=None):
            return []

        def get_vector_records(self, doc_ids):
            assert doc_ids == ["step-1"]
            return [{
                "doc_id": "step-1",
                "text": "1. 先对角松开曲轴箱盖螺栓。",
                "metadata": {
                    "document_id": "manual-doc",
                    "parent_section_id": "sec-cover",
                    "section_title": "6.4 右曲轴箱盖与离合器",
                    "chunk_type": "step_raw",
                    "page": 25,
                    "toc_path": "摩托车发动机维修手册 > 6.4 右曲轴箱盖与离合器 > 拆卸右盖",
                },
            }]

    monkeypatch.setattr(
        api_main,
        "_initialized_or_injected_vector_service",
        lambda **kwargs: FakeVectorService(),
    )
    monkeypatch.setattr(api_main, "_manual_title_match_records", lambda *args: ([], set()))
    monkeypatch.setattr(api_main, "_manual_title_section_match_scores", lambda message: {})
    metadata = {
        "route_plan": _component_route("manual-doc", "曲轴箱盖", "拆卸") | {
            "selected_section_id": "sec-cover",
        },
        "scope_decision": {"status": "in_scope", "document_id": "manual-doc"},
        "retrieval_scope": {
            "document_id": "manual-doc",
            "allowed_section_ids": ["sec-cover"],
            "allowed_evidence_refs": ["step-1"],
        },
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": [],
            }],
        }],
    }

    records = api_main._manual_best_section_records(
        "这个曲轴箱盖的拆卸步骤是什么？",
        "procedure",
        metadata,
    )

    assert [record.get("id") or record.get("doc_id") for record in records] == ["step-1"]
    assert records[0]["metadata"]["_structural_recovery_lookup_source"] == "record_id_lookup"

    answer = api_main._format_manual_evidence_answer_from_metadata(
        "这个曲轴箱盖的拆卸步骤是什么？",
        metadata,
    )
    assert answer is not None
    assert "对角松开曲轴箱盖螺栓" in answer


def test_duplicate_manual_records_merge_trusted_lookup_provenance() -> None:
    merged = _manual_append_unique_records(
        [
            {
                "id": "same-step",
                "content": "1. 拆下水泵盖。",
                "metadata": {
                    "document_id": "manual-doc",
                    "parent_section_id": "sec:pump",
                    "qualification": "reference_only",
                },
            }
        ],
        [
            {
                "id": "same-step",
                "content": "1. 拆下水泵盖。",
                "metadata": {
                    "document_id": "manual-doc",
                    "parent_section_id": "sec:pump",
                    "original_title_match": True,
                },
            }
        ],
    )

    assert len(merged) == 1
    assert merged[0]["metadata"]["original_title_match"] is True


def test_directional_cover_install_answer_stays_on_target_procedure_page(monkeypatch) -> None:
    records = [
        ("remove-cover", 25, "1. 对角松开螺栓，取下右曲轴箱盖。"),
        ("check-seal", 26, "1. 检查曲轴油封，损坏时更换。"),
        ("install-lever", 26, "2. 安装离合器拉杆，并使顶杆槽与右盖顶杆孔对齐。"),
        ("install-cover", 26, "3. 装上定位销、全新的右曲轴箱盖垫片，再盖上右曲轴箱盖。"),
        ("seal-notes", 27, "a. A孔周围3mm内不得有平面密封胶。\nb. B段密封胶需要均匀抹薄、抹平。\nc. D段范围内直接涂抹平面密封硅胶。\n拆卸离合器"),
        ("remove-clutch", 27, "拆卸离合器\n1. 取下顶杆轴套组件。"),
        ("install-clutch", 27, "安装离合器\n1. 检查摩擦片并依次装入离合器部件。"),
    ]

    class FakeVectorService:
        def get_section_records(self, document_id, section_id, limit=80, chunk_type=None):
            return [
                {
                    "id": record_id,
                    "content": content,
                    "metadata": {
                        "document_id": document_id,
                        "parent_section_id": section_id,
                        "section_title": "6.4 右曲轴箱盖与离合器",
                        "chunk_type": "step_raw",
                        "page": page,
                        "source_index": index,
                    },
                }
                for index, (record_id, page, content) in enumerate(records, start=1)
            ]

        def get_page_records(self, document_id, page, chunk_type=None, limit=120):
            return []

    class FakeSectionIndex:
        def build(self, vector_service):
            return None

        def find(self, query):
            return [SimpleNamespace(section_id="sec-right", document_id="manual-doc")]

    from services.knowledge import vector_service as vector_service_module
    from services.retrieval.section_index import SectionTitleIndex

    monkeypatch.setattr(vector_service_module, "get_vector_service", lambda: FakeVectorService())
    monkeypatch.setattr(SectionTitleIndex, "get_instance", classmethod(lambda cls: FakeSectionIndex()))

    answer = _format_manual_evidence_answer_from_metadata(
        "如何安装右曲轴箱盖",
        {
            "route_plan": _component_route("manual-doc", "右曲轴箱盖", "安装"),
            "react_trace": [],
        },
    )

    assert answer is not None
    assert "检查曲轴油封" in answer
    assert "安装离合器拉杆" in answer
    assert "全新的右曲轴箱盖垫片" in answer
    assert "A孔周围3mm内不得有平面密封胶" in answer
    assert "B段密封胶需要均匀抹薄、抹平" in answer
    assert "D段范围内直接涂抹平面密封硅胶" in answer
    assert "取下右曲轴箱盖" not in answer
    assert "拆卸离合器" not in answer
    assert "装入离合器部件" not in answer
    assert "第26-27页" in answer


def test_manual_evidence_answer_selects_only_matching_procedure_subflow(monkeypatch) -> None:
    section_title = "6.4 右曲轴箱盖与离合器"
    toc_root = f"摩托车发动机维修手册 > 六、右曲轴箱盖、离合器、机油泵、水泵 > {section_title}"
    records = [
        (
            "cover-step-1",
            26,
            1,
            "source-cover",
            "安装右盖",
            "1. 检查曲轴油封，损坏时更换。",
            "procedure_step",
        ),
        (
            "cover-step-2",
            26,
            2,
            "source-cover",
            "安装右盖",
            "2. 安装离合器拉杆，并使顶杆槽与右盖顶杆孔对齐。",
            "procedure_step",
        ),
        (
            "remove-clutch",
            27,
            6,
            "source-remove-clutch",
            "拆卸离合器",
            "1. 取下顶杆轴套组件并松开离合器螺母。",
            "procedure_step",
        ),
        (
            "install-clutch-1",
            27,
            7,
            "source-install-clutch-1",
            "安装离合器",
            "1. 检查离合器各部件磨损情况。",
            "procedure_step",
        ),
        (
            "install-clutch-2a",
            27,
            8,
            "source-install-clutch-2",
            "安装离合器",
            "2. 依次放入离合器衬套止推垫、离合器衬套和滚针轴承。",
            "procedure_step",
        ),
        (
            "install-clutch-2b",
            27,
            8,
            "source-install-clutch-2",
            "安装离合器",
            "96.3×107×1 止推垫圈 19\n固定离合器，锁紧离合器螺母并校验扭力。\n"
            "注意：安装摩擦片时，摩擦片与从动片两两相隔。",
            "procedure_step",
        ),
        (
            "install-clutch-warning",
            27,
            8,
            "source-install-clutch-2",
            "安装离合器",
            "注意：安装摩擦片时，摩擦片与从动片两两相隔。",
            "safety_warning",
        ),
    ]

    class FakeVectorService:
        def get_section_records(self, document_id, section_id, limit=80, chunk_type=None):
            return [
                {
                    "id": record_id,
                    "content": content,
                    "metadata": {
                        "document_id": document_id,
                        "parent_section_id": section_id,
                        "section_title": section_title,
                        "chunk_type": "step_raw" if answer_role == "procedure_step" else "text",
                        "answer_role": answer_role,
                        "page": page,
                        "source_index": source_index,
                        "parent_chunk_id": parent_chunk_id,
                        "toc_path": f"{toc_root} > {procedure_heading}",
                    },
                }
                for (
                    record_id,
                    page,
                    source_index,
                    parent_chunk_id,
                    procedure_heading,
                    content,
                    answer_role,
                ) in records
            ]

        def get_page_records(self, document_id, page, chunk_type=None, limit=120):
            return []

    class FakeSectionIndex:
        def build(self, vector_service):
            return None

        def find(self, query):
            return [SimpleNamespace(
                section_id="sec-right-cover-clutch",
                document_id="manual-doc",
                core_title="右曲轴箱盖与离合器",
                full_title=section_title,
            )]

    from services.knowledge import vector_service as vector_service_module
    from services.retrieval.section_index import SectionTitleIndex

    monkeypatch.setattr(vector_service_module, "get_vector_service", lambda: FakeVectorService())
    monkeypatch.setattr(SectionTitleIndex, "get_instance", classmethod(lambda cls: FakeSectionIndex()))
    metadata = {
        "route_plan": _component_route("manual-doc", "离合器", "安装"),
        "react_trace": [],
    }

    answer = _format_manual_evidence_answer_from_metadata("如何安装离合器", metadata)

    assert answer is not None
    assert "检查离合器各部件磨损情况" in answer
    assert "96.3×107×1 止推垫圈 19" in answer
    assert "固定离合器，锁紧离合器螺母" in answer
    assert answer.count("摩擦片与从动片两两相隔") == 1
    assert "检查曲轴油封" not in answer
    assert "安装离合器拉杆" not in answer
    assert "取下顶杆轴套组件" not in answer
    assert "3. 96.3×107×1" not in answer
    assert "第27页" in answer
    assert metadata["_deterministic_answer_evidence_pages"] == [27]
    assert metadata["_deterministic_answer_procedure_action"] == "安装"
    assert metadata["_deterministic_answer_procedure_target"] == "离合器"
    assert metadata["_deterministic_answer_procedure_scope_id"].startswith("proc:")


def test_manual_evidence_answer_cuts_off_next_subflow_heading(monkeypatch) -> None:
    section_title = "6.4 右曲轴箱盖与离合器"
    toc_root = f"手册 > {section_title}"

    class FakeVectorService:
        def get_section_records(self, document_id, section_id, limit=80, chunk_type=None):
            return [
                {
                    "id": "remove-step-1",
                    "content": "1. 取下顶杆轴套组件并取出离合器弹簧。",
                    "metadata": {
                        "document_id": document_id,
                        "parent_section_id": section_id,
                        "section_title": section_title,
                        "chunk_type": "step_raw",
                        "page": 27,
                        "source_index": 6,
                        "parent_chunk_id": "source-remove",
                        "toc_path": f"{toc_root} > 拆卸离合器",
                    },
                },
                {
                    "id": "remove-step-2",
                    "content": (
                        "2. 松开离合器螺母并依次取出从动盘、主动盘和衬套。\n"
                        "安装离合器"
                    ),
                    "metadata": {
                        "document_id": document_id,
                        "parent_section_id": section_id,
                        "section_title": section_title,
                        "chunk_type": "step_raw",
                        "page": 27,
                        "source_index": 6,
                        "parent_chunk_id": "source-remove",
                        "toc_path": f"{toc_root} > 拆卸离合器",
                    },
                },
                {
                    "id": "install-step-1",
                    "content": "1. 检查离合器摩擦片。",
                    "metadata": {
                        "document_id": document_id,
                        "parent_section_id": section_id,
                        "section_title": section_title,
                        "chunk_type": "step_raw",
                        "page": 27,
                        "source_index": 7,
                        "parent_chunk_id": "source-install",
                        "toc_path": f"{toc_root} > 安装离合器",
                    },
                },
            ]

        def get_page_records(self, document_id, page, chunk_type=None, limit=120):
            return []

    class FakeSectionIndex:
        def build(self, vector_service):
            return None

        def find(self, query):
            return [SimpleNamespace(
                section_id="sec-combined",
                document_id="manual-doc",
                core_title="右曲轴箱盖与离合器",
                full_title=section_title,
            )]

    from services.knowledge import vector_service as vector_service_module
    from services.retrieval.section_index import SectionTitleIndex

    monkeypatch.setattr(vector_service_module, "get_vector_service", lambda: FakeVectorService())
    monkeypatch.setattr(SectionTitleIndex, "get_instance", classmethod(lambda cls: FakeSectionIndex()))

    answer = _format_manual_evidence_answer_from_metadata(
        "如何拆卸离合器",
        {
            "route_plan": _component_route("manual-doc", "离合器", "拆卸"),
            "react_trace": [],
        },
    )

    assert answer is not None
    assert answer.index("1. 取下顶杆轴套组件") < answer.index("2. 松开离合器螺母")
    assert "\n安装离合器\n" not in answer
    assert "检查离合器摩擦片" not in answer


def test_manual_evidence_answer_prefers_section_title_match_for_parameter(monkeypatch) -> None:
    class FakeVectorService:
        pass

    class FakeSectionIndex:
        def build(self, vector_service):
            return None

        def find(self, query):
            return [
                SimpleNamespace(
                    section_id="sec-spark-check",
                    document_id="manual-doc",
                    core_title="检查火花塞",
                    full_title="1.2 检查火花塞",
                )
            ]

    from services.knowledge import vector_service as vector_service_module
    from services.retrieval.section_index import SectionTitleIndex

    monkeypatch.setattr(vector_service_module, "get_vector_service", lambda: FakeVectorService())
    monkeypatch.setattr(SectionTitleIndex, "get_instance", classmethod(lambda cls: FakeSectionIndex()))

    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": [
                            {
                                "content": "气门类型 | 标准间隙范围\n进气门 | 0.13～0.20 mm\n排气门 | 0.20～0.30 mm",
                                "metadata": {
                                    "chunk_type": "table",
                                    "section_title": "4.6 气门间隙",
                                    "parent_section_id": "sec-valve-gap",
                                    "section_match_ids": ["sec-valve-gap"],
                                    "page": 15,
                                    "source_index": 1,
                                },
                            },
                            {
                                "content": "用塞尺测量火花塞间隙 a，超出范围须更换火花塞。\n间隙标准值：0.7～0.9 mm",
                                "metadata": {
                                    "chunk_type": "text",
                                    "section_title": "1.2 检查火花塞",
                                    "parent_section_id": "sec-spark-check",
                                    "page": 3,
                                    "source_index": 1,
                                },
                            },
                        ],
                    }
                ]
            }
        ]
    }

    answer = _format_manual_evidence_answer_from_metadata(
        "火花塞间隙标准是多少？",
        metadata,
    )

    assert answer is not None
    assert "0.7～0.9 mm" in answer
    assert "0.13～0.20 mm" not in answer


def test_manual_evidence_answer_preserves_full_numbered_procedure_steps_and_target_section_metadata(monkeypatch) -> None:
    class FakeVectorService:
        def get_section_records(self, document_id, parent_section_id, limit=200, chunk_type=None):
            return []

        def get_page_records(self, document_id, page, chunk_type=None, limit=120):
            return []

    class FakeSectionIndex:
        def build(self, vector_service):
            return None

        def find(self, query):
            return []

    from services.knowledge import vector_service as vector_service_module
    from services.retrieval.section_index import SectionTitleIndex

    monkeypatch.setattr(vector_service_module, "get_vector_service", lambda: FakeVectorService())
    monkeypatch.setattr(SectionTitleIndex, "get_instance", classmethod(lambda cls: FakeSectionIndex()))

    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": [
                            {
                                "id": "tensioner-step-1",
                                "content": (
                                    "1. 预压涨紧器\n"
                                    "松开并取下 M6×10 顶销螺栓（件号 25）。\n"
                                    "用手压住涨紧器顶杆，用一字螺丝刀从背部螺纹孔插入，顺时针扭紧涨紧器螺杆，直至顶杆自锁。"
                                ),
                                "metadata": {
                                    "chunk_type": "step_raw",
                                    "document_id": "manual-doc",
                                    "section_title": "4.4 涨紧器",
                                    "parent_section_id": "sec-tensioner",
                                    "section_match_ids": ["sec-tensioner"],
                                    "page": 13,
                                    "source_index": 1,
                                },
                            },
                            {
                                "id": "tensioner-step-2",
                                "content": (
                                    "2. 安装本体\n"
                                    "套入涨紧器垫片。\n"
                                    "将涨紧器及垫片装入气缸涨紧器安装位。\n"
                                    "旋入并拧紧两个 M6×30 六角法兰面螺栓（件号 1）。"
                                ),
                                "metadata": {
                                    "chunk_type": "step_raw",
                                    "document_id": "manual-doc",
                                    "section_title": "4.4 涨紧器",
                                    "parent_section_id": "sec-tensioner",
                                    "section_match_ids": ["sec-tensioner"],
                                    "page": 13,
                                    "source_index": 2,
                                },
                            },
                            {
                                "id": "tensioner-step-3",
                                "content": (
                                    "3. 释放自锁并锁紧\n"
                                    "用一字螺丝刀逆时针扭动涨紧器螺杆，使顶杆自动弹出并顶住涨紧条。\n"
                                    "旋入 M6×10 顶销螺栓（带铜垫片，件号 25），拧紧至 12 N·m。"
                                ),
                                "metadata": {
                                    "chunk_type": "step_raw",
                                    "document_id": "manual-doc",
                                    "section_title": "4.4 涨紧器",
                                    "parent_section_id": "sec-tensioner",
                                    "section_match_ids": ["sec-tensioner"],
                                    "page": 13,
                                    "source_index": 3,
                                },
                            },
                            {
                                "id": "tensioner-step-4",
                                "content": (
                                    "4. 对齐\n"
                                    "转动曲轴两圈，使磁电机转子“T”标记前的刻线与左曲轴箱盖上 M14×1.5 螺塞孔标记槽中线对齐，"
                                    "此时凸轮轴正时链轮上的刻线与气缸头装配面平齐。"
                                ),
                                "metadata": {
                                    "chunk_type": "step_raw",
                                    "document_id": "manual-doc",
                                    "section_title": "4.4 涨紧器",
                                    "parent_section_id": "sec-tensioner",
                                    "section_match_ids": ["sec-tensioner"],
                                    "page": 13,
                                    "source_index": 4,
                                },
                            },
                            {
                                "id": "head-cover-step-1",
                                "content": "1. 密封处理\n在气缸头密封帽周围均匀涂抹耐热平面密封硅胶。",
                                "metadata": {
                                    "chunk_type": "step_raw",
                                    "document_id": "manual-doc",
                                    "section_title": "4.5 气缸头盖",
                                    "parent_section_id": "sec-head-cover",
                                    "page": 13,
                                    "source_index": 5,
                                },
                            },
                        ],
                    }
                ]
            }
        ]
    }

    answer = _format_manual_evidence_answer_from_metadata("如何安装涨紧器？", metadata)

    assert answer is not None
    assert answer.startswith("可以按以下顺序操作：")
    assert not answer.startswith("根据手册")
    assert answer.endswith("（来源：手册第13页“4.4 涨紧器”）")
    assert "1. 预压涨紧器" in answer
    assert "2. 安装本体" in answer
    assert "3. 释放自锁并锁紧" in answer
    assert "4. 对齐" in answer
    assert answer.index("3. 释放自锁并锁紧") < answer.index("4. 对齐")
    assert "4.5 气缸头盖" not in answer
    assert "密封处理" not in answer
    assert "未在检索到的手册中找到" not in answer
    assert metadata["_deterministic_answer_section_title"] == "4.4 涨紧器"
    assert metadata["_deterministic_answer_section_ids"] == ["sec-tensioner"]


def test_manual_query_kind_treats_how_to_judge_fault_as_evidence_not_procedure() -> None:
    assert _manual_query_kind("压缩压力低于最小值时怎么判断是不是活塞环问题？") == "evidence"


def test_manual_partial_answer_confirms_present_part_and_discloses_missing_parameter(monkeypatch) -> None:
    class FakeVectorService:
        def get_section_records(self, document_id, parent_section_id, limit=200, chunk_type=None):
            return []

        def get_page_records(self, document_id, page, chunk_type=None, limit=120):
            return []

    class FakeSectionIndex:
        def build(self, vector_service):
            return None

        def find(self, query):
            return []

    from services.knowledge import vector_service as vector_service_module
    from services.retrieval.section_index import SectionTitleIndex

    monkeypatch.setattr(vector_service_module, "get_vector_service", lambda: FakeVectorService())
    monkeypatch.setattr(SectionTitleIndex, "get_instance", classmethod(lambda cls: FakeSectionIndex()))
    from api import main as api_main
    monkeypatch.setattr(
        api_main,
        "_manual_title_section_match_scores",
        lambda message: {"sec-water-pump": 100},
    )

    metadata = {
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": [{
                    "id": "water-pump-seal",
                    "content": "水泵装配部件清单：水泵密封圈 1 个。",
                    "metadata": {
                        "qualification": "qualified",
                        "document_id": "manual-doc",
                        "parent_section_id": "sec-water-pump",
                        "section_match_ids": ["sec-water-pump"],
                        "section_title": "6.8 水泵",
                        "chunk_type": "text",
                        "page": 25,
                    },
                }],
            }],
        }],
    }

    answer = _format_manual_evidence_answer_from_metadata(
        "水泵装配里有水泵密封圈吗？叶轮轴向间隙是多少？",
        metadata,
    )

    assert answer is not None
    assert "手册列有水泵密封圈" in answer
    assert "手册未说明叶轮轴向间隙" in answer
    assert len(answer) <= 260


def test_manual_evidence_answer_diagnostic_query_prefers_condition_evidence_over_target_procedure_title(monkeypatch) -> None:
    class FakeVectorService:
        def get_section_records(self, document_id, parent_section_id, limit=200, chunk_type=None):
            records = {
                "sec-piston-ring-remove": [
                    {
                        "id": "remove-ring",
                        "content": "拆下第一道气环、第二道气环、油环组合。",
                        "metadata": {
                            "chunk_type": "text",
                            "document_id": "manual-doc",
                            "section_title": "5.5 拆卸活塞环",
                            "parent_section_id": "sec-piston-ring-remove",
                            "page": 21,
                            "source_index": 1,
                        },
                    }
                ],
                "sec-compression-check": [
                    {
                        "id": "compression-analysis",
                        "content": (
                            "若压缩压力小于最小值：往火花塞孔倒一勺机油，并再次测量。\n"
                            "若压力比加机油前高：活塞环磨损或损坏 → 更换。\n"
                            "若压力等于加机油前：活塞、气门、气缸头垫片可能有缺陷 → 更换。"
                        ),
                        "metadata": {
                            "chunk_type": "text",
                            "document_id": "manual-doc",
                            "section_title": "1.4 压缩压力检查",
                            "parent_section_id": "sec-compression-check",
                            "page": 4,
                            "source_index": 1,
                        },
                    }
                ],
            }
            return records.get(parent_section_id, [])

    class FakeSectionIndex:
        def build(self, vector_service):
            return None

        def find(self, query):
            return [
                SimpleNamespace(
                    section_id="sec-piston-ring-remove",
                    document_id="manual-doc",
                    core_title="拆卸活塞环",
                    full_title="5.5 拆卸活塞环",
                ),
                SimpleNamespace(
                    section_id="sec-compression-check",
                    document_id="manual-doc",
                    core_title="压缩压力检查",
                    full_title="1.4 压缩压力检查",
                ),
            ]

    from services.knowledge import vector_service as vector_service_module
    from services.retrieval.section_index import SectionTitleIndex

    monkeypatch.setattr(vector_service_module, "get_vector_service", lambda: FakeVectorService())
    monkeypatch.setattr(SectionTitleIndex, "get_instance", classmethod(lambda cls: FakeSectionIndex()))

    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": [
                            {
                                "id": "compression-analysis",
                                "content": (
                                    "若压缩压力小于最小值：往火花塞孔倒一勺机油，并再次测量。\n"
                                    "若压力比加机油前高：活塞环磨损或损坏 → 更换。\n"
                                    "若压力等于加机油前：活塞、气门、气缸头垫片可能有缺陷 → 更换。"
                                ),
                                "metadata": {
                                    "chunk_type": "text",
                                    "document_id": "manual-doc",
                                    "section_title": "1.4 压缩压力检查",
                                    "parent_section_id": "sec-compression-check",
                                    "page": 4,
                                    "source_index": 1,
                                },
                            }
                        ],
                    }
                ]
            }
        ]
    }

    answer = _format_manual_evidence_answer_from_metadata(
        "压缩压力低于最小值时怎么判断是不是活塞环问题？",
        metadata,
    )

    assert answer is not None
    assert "往火花塞孔倒一勺机油" in answer
    assert "活塞环磨损或损坏" in answer
    assert "拆下第一道气环" not in answer


def test_manual_evidence_answer_parameter_query_prefers_numeric_target_evidence_over_procedure_title(monkeypatch) -> None:
    class FakeVectorService:
        def get_section_records(self, document_id, parent_section_id, limit=200, chunk_type=None):
            records = {
                "sec-spark-install": [
                    {
                        "id": "install-step",
                        "content": "1. 将火花塞放入气缸头，套上火花塞专用套筒，顺时针转动3圈预紧，然后再转动1/4圈，或拧紧至20±2 N·m。",
                        "metadata": {
                            "chunk_type": "text",
                            "document_id": "manual-doc",
                            "section_title": "1.3 安装火花塞",
                            "parent_section_id": "sec-spark-install",
                            "page": 3,
                            "source_index": 1,
                        },
                    }
                ],
                "sec-spark-check": [
                    {
                        "id": "check-gap",
                        "content": "2. 用塞尺测量火花塞间隙a，超出范围须更换火花塞。\n间隙标准值：0.7～0.9 mm",
                        "metadata": {
                            "chunk_type": "text",
                            "document_id": "manual-doc",
                            "section_title": "1.2 检查火花塞",
                            "parent_section_id": "sec-spark-check",
                            "page": 3,
                            "source_index": 1,
                        },
                    }
                ],
            }
            return records.get(parent_section_id, [])

    class FakeSectionIndex:
        def build(self, vector_service):
            return None

        def find(self, query):
            return [
                SimpleNamespace(
                    section_id="sec-spark-install",
                    document_id="manual-doc",
                    core_title="安装火花塞",
                    full_title="1.3 安装火花塞",
                ),
                SimpleNamespace(
                    section_id="sec-spark-check",
                    document_id="manual-doc",
                    core_title="检查火花塞",
                    full_title="1.2 检查火花塞",
                ),
            ]

    from services.knowledge import vector_service as vector_service_module
    from services.retrieval.section_index import SectionTitleIndex

    monkeypatch.setattr(vector_service_module, "get_vector_service", lambda: FakeVectorService())
    monkeypatch.setattr(SectionTitleIndex, "get_instance", classmethod(lambda cls: FakeSectionIndex()))

    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": [
                            {
                                "id": "check-gap",
                                "content": "2. 用塞尺测量火花塞间隙a，超出范围须更换火花塞。\n间隙标准值：0.7～0.9 mm",
                                "metadata": {
                                    "chunk_type": "text",
                                    "document_id": "manual-doc",
                                    "section_title": "1.2 检查火花塞",
                                    "parent_section_id": "sec-spark-check",
                                    "page": 3,
                                    "source_index": 1,
                                },
                            },
                        ],
                    }
                ]
            }
        ]
    }

    answer = _format_manual_evidence_answer_from_metadata(
        "火花塞间隙标准是多少？",
        metadata,
    )

    assert answer is not None
    assert "0.7～0.9 mm" in answer
    assert "20±2 N·m" not in answer


def test_manual_evidence_answer_expands_same_section_steps(monkeypatch) -> None:
    class FakeVectorService:
        def get_section_records(self, document_id, parent_section_id, limit=200, chunk_type=None):
            assert document_id == "manual-doc"
            assert parent_section_id == "sec-starter-install"
            return [
                {
                    "id": "step-1",
                    "content": "1. 预压涨紧器\n松开并取下 M6×10 顶销螺栓。",
                    "metadata": {
                        "chunk_type": "text",
                        "document_id": "manual-doc",
                        "section_title": "2.3 安装起动电机",
                        "parent_section_id": "sec-starter-install",
                        "page": 5,
                        "source_index": 1,
                    },
                },
                {
                    "id": "step-2",
                    "content": "2. 安装本体\n将起动电机头部对准左盖孔。",
                    "metadata": {
                        "chunk_type": "text",
                        "document_id": "manual-doc",
                        "section_title": "2.3 安装起动电机",
                        "parent_section_id": "sec-starter-install",
                        "page": 5,
                        "source_index": 2,
                    },
                },
                {
                    "id": "step-5",
                    "content": "5. 安装涨紧器",
                    "metadata": {
                        "chunk_type": "text",
                        "document_id": "manual-doc",
                        "section_title": "2.3 安装起动电机",
                        "parent_section_id": "sec-starter-install",
                        "page": 5,
                        "source_index": 5,
                    },
                },
            ]

    from services.knowledge import vector_service as vector_service_module

    monkeypatch.setattr(vector_service_module, "get_vector_service", lambda: FakeVectorService())

    metadata = {
        "route_plan": _component_route("manual-doc", "起动电机", "安装"),
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": [
                            {
                                "content": "1. 检查起动电机状态\n检查起动电机轴是否转动灵活。",
                                "metadata": {
                                    "chunk_type": "text",
                                    "document_id": "manual-doc",
                                    "section_title": "2.3 安装起动电机",
                                    "parent_section_id": "sec-starter-install",
                                    "section_match_ids": ["sec-starter-install"],
                                    "page": 5,
                                    "source_index": 1,
                                },
                            },
                            {
                                "content": "2. 安装本体\n将起动电机头部对准左盖孔。",
                                "metadata": {
                                    "chunk_type": "text",
                                    "document_id": "manual-doc",
                                    "section_title": "2.3 安装起动电机",
                                    "parent_section_id": "sec-starter-install",
                                    "section_match_ids": ["sec-starter-install"],
                                    "page": 5,
                                    "source_index": 2,
                                },
                            },
                        ],
                    }
                ]
            }
        ]
    }

    answer = _format_manual_evidence_answer_from_metadata(
        "安装起动电机的步骤是什么？",
        metadata,
    )

    assert answer is not None
    assert "1. 预压涨紧器" in answer
    assert "5. 安装涨紧器" in answer


def test_manual_evidence_answer_prefers_specific_title_once_and_keeps_pre_install_sealant(monkeypatch) -> None:
    class FakeVectorService:
        def get_section_records(self, document_id, parent_section_id, limit=200, chunk_type=None):
            return []

    class FakeSectionIndex:
        def build(self, vector_service):
            return None

        def find(self, query):
            return [
                SimpleNamespace(
                    section_id="sec-cover",
                    document_id="manual-doc",
                    core_title="气缸头盖",
                    full_title="4.5 气缸头盖",
                ),
                SimpleNamespace(
                    section_id="sec-head",
                    document_id="manual-doc",
                    core_title="气缸头",
                    full_title="4.7 气缸头",
                ),
            ]

    from services.knowledge import vector_service as vector_service_module
    from services.retrieval.section_index import SectionTitleIndex

    monkeypatch.setattr(vector_service_module, "get_vector_service", lambda: FakeVectorService())
    monkeypatch.setattr(SectionTitleIndex, "get_instance", classmethod(lambda cls: FakeSectionIndex()))

    cover_records = [
        {
            "content": (
                "1. 密封处理\n"
                "在气缸头密封帽周围均匀涂抹耐热平面密封硅胶。\n"
                "在气缸头装配平面和新气缸头盖垫片两面均涂抹耐热平面密封硅胶。"
            ),
            "metadata": {
                "chunk_type": "step_raw",
                "document_id": "manual-doc",
                "section_title": "4.5 气缸头盖",
                "parent_section_id": "sec-cover",
                "section_match_ids": ["sec-cover", "sec-head"],
                "page": 13,
                "source_index": 1,
            },
        },
        {
            "content": "2. 安装与拧紧\n安装气缸头盖。\n对角均匀拧紧至规定扭矩。",
            "metadata": {
                "chunk_type": "text",
                "document_id": "manual-doc",
                "section_title": "4.5 气缸头盖",
                "parent_section_id": "sec-cover",
                "section_match_ids": ["sec-cover", "sec-head"],
                "page": 14,
                "source_index": 2,
            },
        },
    ]
    noisy_head_records = [
        {
            "content": f"注意：必须测量基圆位置。\n{i}. 安装气缸头相关泛化步骤。",
            "metadata": {
                "chunk_type": "text",
                "document_id": "manual-doc",
                "section_title": "4.7 气缸头",
                "parent_section_id": "sec-head",
                "section_match_ids": ["sec-cover", "sec-head"],
                "page": 15,
                "source_index": i,
            },
        }
        for i in range(1, 9)
    ]
    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result": cover_records + noisy_head_records,
                    }
                ]
            }
        ]
    }

    answer = _format_manual_evidence_answer_from_metadata(
        "安装气缸头盖的密封处理在哪一步、拧紧在哪一步？完整说一下",
        metadata,
    )

    assert answer is not None
    assert "4.5 气缸头盖" in answer
    assert "4.7 气缸头" not in answer
    assert "在气缸头密封帽周围均匀涂抹耐热平面密封硅胶" in answer
    assert "在气缸头装配平面和新气缸头盖垫片两面均涂抹耐热平面密封硅胶" in answer
    assert "对角均匀拧紧至规定扭矩" in answer


def test_manual_evidence_answer_stops_at_opposite_action_heading(monkeypatch) -> None:
    from api import main as api_main

    monkeypatch.setattr(api_main, "_manual_title_match_records", lambda message, document_id="": ([], set()))
    monkeypatch.setattr(api_main, "_manual_title_section_match_scores", lambda message: {})
    monkeypatch.setattr(
        api_main,
        "_manual_expand_same_section_records",
        lambda records, section_match_ids, **kwargs: records,
    )
    monkeypatch.setattr(
        api_main,
        "_manual_expand_page_boundary_records",
        lambda records, section_match_ids, **kwargs: records,
    )
    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result": [
                            {
                                "content": (
                                    "拆卸气门\n"
                                    "1. 取下滑动挺柱和气门间隙调整垫片。\n"
                                    "2. 使用气门拆装器压缩气门弹簧。\n"
                                    "依次拆下气门锁夹、气门弹簧座和气门（进气门×2，排气门×2）。\n"
                                    "安装气门\n"
                                    "1. 依次安装气门、气门弹簧座和气门杆径油封。"
                                ),
                                "metadata": {
                                    "chunk_type": "step_raw",
                                    "document_id": "manual-doc",
                                    "section_title": "4.8 气门",
                                    "parent_section_id": "sec-valve",
                                    "section_match_ids": ["sec-valve"],
                                    "page": 16,
                                    "source_index": 1,
                                },
                            },
                            {
                                "content": "5.1 气缸活塞装配部件清单\nNo. 17 / 41",
                                "metadata": {
                                    "chunk_type": "step_raw",
                                    "document_id": "manual-doc",
                                    "section_title": "4.8 气门",
                                    "parent_section_id": "sec-valve",
                                    "section_match_ids": ["sec-valve"],
                                    "page": 17,
                                    "source_index": 2,
                                },
                            },
                        ],
                    }
                ]
            }
        ]
    }

    answer = _format_manual_evidence_answer_from_metadata("如何拆卸气门？", metadata)

    assert answer is not None
    assert "取下滑动挺柱和气门间隙调整垫片" in answer
    assert "进气门×2，排气门×2" in answer
    assert "依次安装气门" not in answer
    assert "5.1 气缸活塞装配部件清单" not in answer


def test_manual_evidence_answer_rescues_original_title_match_when_tool_query_drifts(monkeypatch) -> None:
    class FakeVectorService:
        def get_section_records(self, document_id, parent_section_id, limit=200, chunk_type=None):
            assert document_id == "manual-doc"
            assert parent_section_id == "sec-valve"
            return [
                {
                    "id": "install-1",
                    "content": "安装气门\n1. 依次安装：\n气门\n气门弹簧座\n气门杆径油封",
                    "metadata": {
                        "chunk_type": "step_raw",
                        "document_id": "manual-doc",
                        "section_title": "4.8 气门",
                        "parent_section_id": "sec-valve",
                        "page": 16,
                        "source_index": 2,
                    },
                },
                {
                    "id": "install-2",
                    "content": "2. 使用气门拆装器压缩弹簧，装上气门锁夹。",
                    "metadata": {
                        "chunk_type": "step_raw",
                        "document_id": "manual-doc",
                        "section_title": "4.8 气门",
                        "parent_section_id": "sec-valve",
                        "page": 17,
                        "source_index": 3,
                    },
                },
            ]

    class FakeSectionIndex:
        def build(self, vector_service):
            return None

        def find(self, query):
            return [
                SimpleNamespace(
                    section_id="sec-valve",
                    document_id="manual-doc",
                    core_title="气门",
                    full_title="4.8 气门",
                )
            ]

    from services.knowledge import vector_service as vector_service_module
    from services.retrieval.section_index import SectionTitleIndex

    monkeypatch.setattr(vector_service_module, "get_vector_service", lambda: FakeVectorService())
    monkeypatch.setattr(SectionTitleIndex, "get_instance", classmethod(lambda cls: FakeSectionIndex()))

    metadata = {
        "route_plan": _component_route("manual-doc", "气门", "安装"),
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result": [
                            {
                                "content": "气门间隙标准值\n进气门：0.13～0.20 mm\n排气门：0.20～0.30 mm",
                                "metadata": {
                                    "chunk_type": "table",
                                    "document_id": "manual-doc",
                                    "section_title": "4.6 气门间隙",
                                    "parent_section_id": "sec-gap",
                                    "page": 15,
                                    "source_index": 1,
                                },
                            }
                        ],
                    }
                ]
            }
        ]
    }

    answer = _format_manual_evidence_answer_from_metadata("如何安装气门？", metadata)

    assert answer is not None
    assert "4.8 气门" in answer
    assert "依次安装" in answer
    assert "装上气门锁夹" in answer
    assert "气门间隙标准值" not in answer


def test_manual_evidence_answer_prefers_substep_entity_over_broader_title_match(monkeypatch) -> None:
    class FakeVectorService:
        def get_section_records(self, document_id, parent_section_id, limit=200, chunk_type=None):
            return []

    class FakeSectionIndex:
        def build(self, vector_service):
            return None

        def find(self, query):
            return [
                SimpleNamespace(
                    section_id="sec-piston-ring",
                    document_id="manual-doc",
                    core_title="安装活塞环",
                    full_title="5.6 安装活塞环",
                )
            ]

    from services.knowledge import vector_service as vector_service_module
    from services.retrieval.section_index import SectionTitleIndex

    monkeypatch.setattr(vector_service_module, "get_vector_service", lambda: FakeVectorService())
    monkeypatch.setattr(SectionTitleIndex, "get_instance", classmethod(lambda cls: FakeSectionIndex()))

    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": [
                            {
                                "content": (
                                    "活塞环开口位置与角度\n"
                                    "各活塞环开口位置不得重叠；任意两环开口之间应错开120°。"
                                ),
                                "metadata": {
                                    "chunk_type": "step_raw",
                                    "document_id": "manual-doc",
                                    "section_title": "5.6 安装活塞环",
                                    "parent_section_id": "sec-piston-ring",
                                    "section_match_ids": ["sec-piston-ring"],
                                    "page": 21,
                                    "source_index": 1,
                                },
                            },
                            {
                                "content": (
                                    "5.4 安装气缸与活塞\n"
                                    "（1）安装全新的箱体缸体垫片\n"
                                    "（2）将活塞头部插入气缸裙部。"
                                ),
                                "metadata": {
                                    "chunk_type": "step_raw",
                                    "document_id": "manual-doc",
                                    "section_title": "5.4 安装气缸与活塞",
                                    "parent_section_id": "sec-cylinder-piston",
                                    "page": 19,
                                    "source_index": 1,
                                },
                            },
                            {
                                "content": (
                                    "（4）安装活塞销挡圈\n"
                                    "1. 将活塞销挡圈装入对应挡圈槽内；\n"
                                    "2. 将其开口处转动至与槽缺口相错开120°～180°的位置。\n"
                                    "检查挡圈是否变形、失去弹力，如有则必须更换；防止挡圈掉入箱体内部；"
                                    "挡圈必须完全装配到槽内；两侧挡圈安装完毕后，活塞销应有轴向间隙。"
                                ),
                                "metadata": {
                                    "chunk_type": "step_raw",
                                    "document_id": "manual-doc",
                                    "section_title": "5.4 安装气缸与活塞",
                                    "parent_section_id": "sec-cylinder-piston",
                                    "page": 21,
                                    "source_index": 9,
                                },
                            },
                        ],
                    }
                ]
            }
        ]
    }

    answer = _format_manual_evidence_answer_from_metadata(
        "安装活塞销挡圈时开口位置有什么要求？",
        metadata,
    )

    assert answer is not None
    assert "5.4 安装气缸与活塞" in answer
    assert "活塞销挡圈装入对应挡圈槽内" in answer
    assert "120°～180°" in answer
    assert "箱体缸体垫片" not in answer
    assert "活塞环开口位置" not in answer


def test_manual_evidence_answer_uses_entities_after_action_clause(monkeypatch) -> None:
    class FakeVectorService:
        def get_section_records(self, document_id, parent_section_id, limit=200, chunk_type=None):
            if parent_section_id == "sec-right-cover":
                return [
                    {
                        "id": "right-cover",
                        "content": (
                            "安装右盖\n"
                            "1. 检查曲轴油封：若损坏或内环硬化，则更换缺陷的油封，拆出的油封不能再次使用。\n"
                            "2. 安装离合器拉杆：先将扭簧斜边侧插入拉杆孔内，再将拉杆安装在右盖上。\n"
                            "注意：旋转离合器拉杆，使离合器拉杆上的顶杆槽与右盖上的顶杆孔对齐。"
                        ),
                        "metadata": {
                            "chunk_type": "step_raw",
                            "document_id": "manual-doc",
                            "section_title": "6.4 右曲轴箱盖与离合器",
                            "parent_section_id": "sec-right-cover",
                            "page": 26,
                            "source_index": 1,
                        },
                    }
                ]
            if parent_section_id == "sec-magnet":
                return [
                    {
                        "id": f"magnet-{index}",
                        "content": "安装磁电机转子离合器分部件\n依次安装止推垫圈、起动大齿、左曲轴箱盖。",
                        "metadata": {
                            "chunk_type": "step_raw",
                            "document_id": "manual-doc",
                            "section_title": "7.3 磁电机转子离合器分部件",
                            "parent_section_id": "sec-magnet",
                            "page": 31,
                            "source_index": index,
                        },
                    }
                    for index in range(20)
                ]
            return []

    class FakeSectionIndex:
        def build(self, vector_service):
            return None

        def find(self, query):
            return [
                SimpleNamespace(
                    section_id="sec-right-cover",
                    document_id="manual-doc",
                    core_title="右曲轴箱盖与离合器",
                    full_title="6.4 右曲轴箱盖与离合器",
                ),
                SimpleNamespace(
                    section_id="sec-magnet",
                    document_id="manual-doc",
                    core_title="磁电机转子离合器分部件",
                    full_title="7.3 磁电机转子离合器分部件",
                ),
            ]

    from services.knowledge import vector_service as vector_service_module
    from services.retrieval.section_index import SectionTitleIndex

    monkeypatch.setattr(vector_service_module, "get_vector_service", lambda: FakeVectorService())
    monkeypatch.setattr(SectionTitleIndex, "get_instance", classmethod(lambda cls: FakeSectionIndex()))

    answer = _format_manual_evidence_answer_from_metadata(
        "安装右盖时曲轴油封和离合器拉杆要注意什么？",
        {
            "route_plan": _component_route("manual-doc", "右盖", "安装"),
            "react_trace": [],
        },
    )

    assert answer is not None
    assert "6.4 右曲轴箱盖与离合器" in answer
    assert "曲轴油封" in answer
    assert "离合器拉杆" in answer
    assert "磁电机转子" not in answer


def test_manual_evidence_answer_does_not_treat_longer_subpart_as_action_heading(monkeypatch) -> None:
    class FakeVectorService:
        def get_section_records(self, document_id, parent_section_id, limit=200, chunk_type=None):
            return []

    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "result_data": [
                            {
                                "content": "安装离合器拉杆：先将扭簧斜边侧插入拉杆孔内。",
                                "metadata": {
                                    "chunk_type": "step_raw",
                                    "document_id": "manual-doc",
                                    "section_title": "6.4 右曲轴箱盖与离合器",
                                    "parent_section_id": "sec-right-cover",
                                    "page": 26,
                                    "source_index": 1,
                                },
                            },
                            {
                                "content": "检查离合器摩擦片，如发黑严重，则需更换一套新摩擦片。",
                                "metadata": {
                                    "chunk_type": "step_raw",
                                    "document_id": "manual-doc",
                                    "section_title": "6.4 右曲轴箱盖与离合器",
                                    "parent_section_id": "sec-right-cover",
                                    "page": 27,
                                    "source_index": 2,
                                },
                            },
                            {
                                "content": (
                                    "安装离合器\n"
                                    "注意：安装摩擦片时，摩擦片与从动片两两相隔，其中大孔摩擦片靠近从动盘侧。"
                                ),
                                "metadata": {
                                    "chunk_type": "step_raw",
                                    "document_id": "manual-doc",
                                    "section_title": "6.4 右曲轴箱盖与离合器",
                                    "parent_section_id": "sec-right-cover",
                                    "section_match_ids": ["sec-right-cover"],
                                    "page": 27,
                                    "source_index": 3,
                                },
                            },
                        ]
                    }
                ]
            }
        ]
    }

    from services.knowledge import vector_service as vector_service_module

    monkeypatch.setattr(vector_service_module, "get_vector_service", lambda: FakeVectorService())

    answer = _format_manual_evidence_answer_from_metadata(
        "安装离合器时摩擦片有什么方向或位置要求？",
        metadata,
    )

    assert answer is not None
    assert "安装离合器拉杆" not in answer
    assert "发黑严重" in answer
    assert "大孔摩擦片靠近从动盘侧" in answer


def test_manual_evidence_answer_preserves_step_source_order() -> None:
    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": [
                            {
                                "content": "2. 用尖嘴钳将高压帽套进火花塞并用力往下压紧。",
                                "metadata": {
                                    "chunk_type": "step_raw",
                                    "section_title": "1.3 安装火花塞",
                                    "parent_section_id": "sec-spark-install",
                                    "section_match_ids": ["sec-spark-install"],
                                    "page": 3,
                                    "source_index": 4,
                                },
                            },
                            {
                                "content": (
                                    "1. 将火花塞放入气缸头，套上火花塞专用套筒，"
                                    "然后顺时针转动 3 圈预紧，然后再转动 1/4 圈，"
                                    "或使用定扭扳手将其拧紧至 20 ± 2 N·m。"
                                ),
                                "metadata": {
                                    "chunk_type": "step_raw",
                                    "section_title": "1.3 安装火花塞",
                                    "parent_section_id": "sec-spark-install",
                                    "section_match_ids": ["sec-spark-install"],
                                    "page": 3,
                                    "source_index": 3,
                                },
                            },
                        ],
                    }
                ]
            }
        ]
    }

    answer = _format_manual_evidence_answer_from_metadata(
        "安装火花塞时应该怎么预紧和拧紧？",
        metadata,
    )

    assert answer is not None
    assert answer.startswith("可以按以下顺序操作：")
    assert not answer.startswith("根据手册")
    assert answer.endswith("（来源：手册第3页“1.3 安装火花塞”）")
    assert answer.index("顺时针转动 3 圈预紧") < answer.index("然后再转动 1/4 圈")
    assert answer.index("然后再转动 1/4 圈") < answer.index("高压帽套进火花塞")
    assert "NGK" not in answer


def test_manual_evidence_answer_recovers_missing_leading_step_from_section_context(monkeypatch) -> None:
    from api import main as api_main
    from services.knowledge import vector_service as vector_service_module
    from services.retrieval.section_index import SectionTitleIndex

    class _VectorService:
        def get_section_records(self, *args, **kwargs):
            return []

        def get_page_records(self, *args, **kwargs):
            return []

    class _SectionIndex:
        def build(self, vector_service):
            return None

        def find(self, query):
            return []

    monkeypatch.setattr(vector_service_module, "get_vector_service", lambda: _VectorService())
    monkeypatch.setattr(SectionTitleIndex, "get_instance", classmethod(lambda cls: _SectionIndex()))
    context_after = (
        "1. 拆下凸轮轴。\n"
        "2. 取下滑动挺柱与气门间隙调整垫片。\n"
        "3. 更换对应厚度的调整垫片。\n"
        "4.7 气缸头\n"
        "拆卸气缸头"
    )
    records = [
        {
            "id": "header",
            "content": "注意：必须测量基圆位置。调整气门间隙",
            "metadata": {
                "qualification": "qualified",
                "document_id": "manual-doc",
                "section_title": "4.6 气门间隙",
                "parent_section_id": "sec-clearance",
                "page": 15,
                "source_index": 0,
                "context_after": context_after,
            },
        },
        {
            "id": "step-2",
            "content": "2. 取下滑动挺柱与气门间隙调整垫片。",
            "metadata": {
                "qualification": "qualified",
                "document_id": "manual-doc",
                "section_title": "4.6 气门间隙",
                "parent_section_id": "sec-clearance",
                "page": 15,
                "source_index": 2,
                "chunk_type": "step_raw",
            },
        },
        {
            "id": "step-3",
            "content": "3. 更换对应厚度的调整垫片。",
            "metadata": {
                "qualification": "qualified",
                "document_id": "manual-doc",
                "section_title": "4.6 气门间隙",
                "parent_section_id": "sec-clearance",
                "page": 15,
                "source_index": 3,
                "chunk_type": "step_raw",
            },
        },
    ]
    metadata = {"react_trace": [{"tool_calls": [{"name": "knowledge_retrieval", "result_data": records}]}]}

    answer = _format_manual_evidence_answer_from_metadata("如何调整气门间隙？", metadata)

    assert answer is not None
    assert answer.index("1. 拆下凸轮轴") < answer.index("2. 取下滑动挺柱")
    assert answer.index("2. 取下滑动挺柱") < answer.index("3. 更换对应厚度")
    assert "冷却30分钟" not in answer


def test_manual_evidence_answer_keeps_multiple_parameter_values() -> None:
    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": [
                            {
                                "content": "压缩压力标准范围：1300～1900 kPa（转速：1500 r/min）。",
                                "metadata": {
                                    "chunk_type": "text",
                                    "section_title": "1.4 测量压缩压力",
                                    "parent_section_id": "sec-compression",
                                    "section_match_ids": ["sec-compression"],
                                    "page": 3,
                                    "source_index": 5,
                                },
                            },
                            {
                                "content": "压缩压力标准范围：500～900 kPa（转速：540 r/min）。",
                                "metadata": {
                                    "chunk_type": "text",
                                    "section_title": "1.4 测量压缩压力",
                                    "parent_section_id": "sec-compression",
                                    "section_match_ids": ["sec-compression"],
                                    "page": 3,
                                    "source_index": 6,
                                },
                            },
                        ],
                    }
                ]
            }
        ]
    }

    answer = _format_manual_evidence_answer_from_metadata(
        "压缩压力标准范围是多少？",
        metadata,
    )

    assert answer is not None
    assert "1300～1900 kPa" in answer
    assert "1500 r/min" in answer
    assert "500～900 kPa" in answer
    assert "540 r/min" in answer
    assert "请提供品牌型号" not in answer


def test_manual_evidence_answer_prefers_tool_anchor_over_adjacent_parameter_table(monkeypatch) -> None:
    class FakeVectorService:
        def get_section_records(self, document_id, parent_section_id, limit=200, chunk_type=None):
            if parent_section_id == "sec-valve-gap-table":
                return [
                    {
                        "id": "gap-table-full",
                        "content": (
                            "表格：第15页表格\n"
                            "气门类型 | 标准间隙范围\n"
                            "进气门 | 0.13～0.20 mm\n"
                            "排气门 | 0.20～0.30 mm"
                        ),
                        "metadata": {
                            "chunk_type": "table",
                            "document_id": "manual-doc",
                            "section_title": "4.6 气门间隙",
                            "parent_section_id": "sec-valve-gap-table",
                            "page": 15,
                            "source_index": 1,
                        },
                    }
                ]
            return []

    class FakeSectionIndex:
        def build(self, vector_service):
            return None

        def find(self, query):
            return [
                SimpleNamespace(
                    section_id="sec-valve-gap-table",
                    document_id="manual-doc",
                    core_title="气门间隙",
                    full_title="4.6 气门间隙",
                )
            ]

    from services.knowledge import vector_service as vector_service_module
    from services.retrieval.section_index import SectionTitleIndex

    monkeypatch.setattr(vector_service_module, "get_vector_service", lambda: FakeVectorService())
    monkeypatch.setattr(SectionTitleIndex, "get_instance", classmethod(lambda cls: FakeSectionIndex()))

    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": [
                            {
                                "id": "gap-measure-tool",
                                "content": "将塞尺插入凸轮轴基圆与滑动挺柱之间测量间隙。",
                                "metadata": {
                                    "chunk_type": "text",
                                    "document_id": "manual-doc",
                                    "section_title": "4.6 气门间隙",
                                    "parent_section_id": "sec-valve-gap-measure",
                                    "page": 14,
                                    "source_index": 1,
                                },
                            },
                            {
                                "id": "gap-table-full",
                                "content": (
                                    "表格：第15页表格\n"
                                    "气门类型 | 标准间隙范围\n"
                                    "进气门 | 0.13～0.20 mm\n"
                                    "排气门 | 0.20～0.30 mm"
                                ),
                                "metadata": {
                                    "chunk_type": "table",
                                    "document_id": "manual-doc",
                                    "section_title": "4.6 气门间隙",
                                    "parent_section_id": "sec-valve-gap-table",
                                    "section_match_ids": ["sec-valve-gap-table"],
                                    "page": 15,
                                    "source_index": 1,
                                },
                            },
                        ],
                    }
                ]
            }
        ]
    }

    answer = _format_manual_evidence_answer_from_metadata(
        "测量气门间隙时塞尺插在哪里？",
        metadata,
    )

    assert answer is not None
    assert "第14页" in answer
    assert "塞尺插入凸轮轴基圆与滑动挺柱之间" in answer
    assert "0.13～0.20 mm" not in answer


def test_manual_evidence_answer_rebinds_page_boundary_chunk_to_embedded_section_heading(monkeypatch) -> None:
    class FakeVectorService:
        def get_section_records(self, document_id, parent_section_id, limit=200, chunk_type=None):
            if parent_section_id == "sec-valve-gap":
                return [
                    {
                        "id": "gap-warning",
                        "content": "注意：必须测量基圆位置，非凸轮升程段。",
                        "metadata": {
                            "chunk_type": "text",
                            "document_id": "manual-doc",
                            "section_title": "4.6 气门间隙",
                            "parent_section_id": "sec-valve-gap",
                            "page": 15,
                        },
                    }
                ]
            if parent_section_id == "sec-head-cover":
                return [
                    {
                        "id": "head-cover-step",
                        "content": "在气缸头盖垫片两面涂密封胶并安装气缸头盖。",
                        "metadata": {
                            "chunk_type": "text",
                            "document_id": "manual-doc",
                            "section_title": "4.5 气缸头盖",
                            "parent_section_id": "sec-head-cover",
                            "page": 14,
                        },
                    }
                ]
            return []

    class FakeSectionIndex:
        def build(self, vector_service):
            return None

        def find(self, query):
            return [
                SimpleNamespace(
                    section_id="sec-valve-gap",
                    document_id="manual-doc",
                    core_title="气门间隙",
                    full_title="4.6 气门间隙",
                )
            ]

    from services.knowledge import vector_service as vector_service_module
    from services.retrieval.section_index import SectionTitleIndex

    monkeypatch.setattr(vector_service_module, "get_vector_service", lambda: FakeVectorService())
    monkeypatch.setattr(SectionTitleIndex, "get_instance", classmethod(lambda cls: FakeSectionIndex()))

    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": [
                            {
                                "id": "stale-boundary-chunk",
                                "content": (
                                    "4.6 气门间隙\n"
                                    "测量气门间隙\n"
                                    "拆下气缸头盖。\n"
                                    "将塞尺插入凸轮轴基圆与滑动挺柱之间测量间隙。"
                                ),
                                "metadata": {
                                    "chunk_type": "text",
                                    "document_id": "manual-doc",
                                    "section_title": "4.5 气缸头盖",
                                    "parent_section_id": "sec-head-cover",
                                    "procedure_scope_id": "proc:stale-head-cover",
                                    "procedure_heading": "安装气缸头盖",
                                    "procedure_action": "安装",
                                    "procedure_target": "气缸头盖",
                                    "toc_path": "摩托车发动机维修手册 > 4.5 气缸头盖 > 安装气缸头盖",
                                    "page": 14,
                                    "source_index": 9,
                                },
                            }
                        ],
                    }
                ]
            }
        ]
    }

    answer = _format_manual_evidence_answer_from_metadata(
        "测量气门间隙时塞尺插在哪里？",
        metadata,
    )

    assert answer is not None
    assert "塞尺插入凸轮轴基圆与滑动挺柱之间" in answer
    assert "涂密封胶" not in answer
    assert metadata["_deterministic_answer_section_title"] == "4.6 气门间隙"
    assert metadata["_deterministic_answer_evidence_pages"] == [14]


def test_manual_evidence_answer_prefers_letter_mark_anchors_over_neighbor_check_section(monkeypatch) -> None:
    class FakeVectorService:
        def get_section_records(self, document_id, parent_section_id, limit=200, chunk_type=None):
            if parent_section_id == "sec-crank-balance-check":
                return [
                    {
                        "id": "check-table",
                        "content": (
                            "表格：第39页表格\n"
                            "检查项目 | 技术要求\n"
                            "曲轴轴向跳动 | ≤ 0.03 mm\n"
                            "平衡轴表面状态 | 无刮痕、无磨损"
                        ),
                        "metadata": {
                            "chunk_type": "table",
                            "document_id": "manual-doc",
                            "section_title": "9.3 检查曲轴与平衡轴",
                            "parent_section_id": "sec-crank-balance-check",
                            "page": 39,
                            "source_index": 1,
                        },
                    }
                ]
            return []

    class FakeSectionIndex:
        def build(self, vector_service):
            return None

        def find(self, query):
            return [
                SimpleNamespace(
                    section_id="sec-crank-balance-check",
                    document_id="manual-doc",
                    core_title="检查曲轴与平衡轴",
                    full_title="9.3 检查曲轴与平衡轴",
                )
            ]

    from services.knowledge import vector_service as vector_service_module
    from services.retrieval.section_index import SectionTitleIndex

    monkeypatch.setattr(vector_service_module, "get_vector_service", lambda: FakeVectorService())
    monkeypatch.setattr(SectionTitleIndex, "get_instance", classmethod(lambda cls: FakeSectionIndex()))

    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": [
                            {
                                "id": "neighbor-check-table",
                                "content": (
                                    "表格：第39页表格\n"
                                    "检查项目 | 技术要求\n"
                                    "曲轴轴向跳动 | ≤ 0.03 mm\n"
                                    "平衡轴表面状态 | 无刮痕、无磨损"
                                ),
                                "metadata": {
                                    "chunk_type": "table",
                                    "document_id": "manual-doc",
                                    "section_title": "9.3 检查曲轴与平衡轴",
                                    "parent_section_id": "sec-crank-balance-check",
                                    "section_match_ids": ["sec-crank-balance-check"],
                                    "page": 39,
                                    "source_index": 1,
                                },
                            },
                            {
                                "id": "mark-align",
                                "content": "安装完成后，将曲轴旋转至上止点位置：曲柄上的标记（图示“C”）应与平衡轴配重块上的标记（图示“D”）对齐。",
                                "metadata": {
                                    "chunk_type": "step_raw",
                                    "document_id": "manual-doc",
                                    "section_title": "9.4 安装曲轴与平衡轴",
                                    "parent_section_id": "sec-crank-balance-install",
                                    "page": 41,
                                    "source_index": 4,
                                },
                            },
                        ],
                    }
                ]
            }
        ]
    }

    answer = _format_manual_evidence_answer_from_metadata(
        "安装完成后曲柄C标记和平衡轴D标记要怎么对齐？",
        metadata,
    )

    assert answer is not None
    assert "第41页" in answer
    assert "曲柄上的标记（图示“C”）" in answer
    assert "平衡轴配重块上的标记（图示“D”）对齐" in answer
    assert "曲轴轴向跳动" not in answer


def test_manual_evidence_answer_focuses_open_vocabulary_structured_action() -> None:
    """结构化动作不在旧动作表中时，也只能呈现与该动作直接匹配的证据。"""

    metadata = {
        "route_plan": {
            "action": "grounded_retrieval",
            "intent": "procedure_planning",
            "task_action": "formal_procedure",
            "entity_role": "document_component",
            "selected_document_id": "manual-doc",
            "selected_section_id": "sec-sync",
            "query_contract": {
                "task_action": "formal_procedure",
                "component": "星门同步盘",
                "action": "标定",
            },
        },
        "retrieval_scope": {
            "document_id": "manual-doc",
            "parent_section_id": "sec-sync",
        },
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": [
                    {
                        "id": "sync-calibration",
                        "content": "1. 标定星门同步盘刻线，使两条刻线与壳体基准面平齐。",
                        "metadata": {
                            "qualification": "qualified",
                            "chunk_type": "text",
                            "chunk_label": "step",
                            "document_id": "manual-doc",
                            "parent_section_id": "sec-sync",
                            "section_match_ids": ["sec-sync"],
                            "section_title": "4.3 星门同步盘",
                            "page": 11,
                            "source_index": 1,
                        },
                    },
                    {
                        "id": "sync-removal",
                        "content": "2. 拆下同步盘固定螺母，并取出轴套。",
                        "metadata": {
                            "qualification": "qualified",
                            "chunk_type": "text",
                            "chunk_label": "step",
                            "document_id": "manual-doc",
                            "parent_section_id": "sec-sync",
                            "section_match_ids": ["sec-sync"],
                            "section_title": "4.3 星门同步盘",
                            "page": 11,
                            "source_index": 2,
                        },
                    },
                ],
            }],
        }],
    }

    answer = _format_manual_evidence_answer_from_metadata(
        "星门同步盘的刻线怎么标定？",
        metadata,
    )

    assert answer is not None
    assert "标定星门同步盘刻线" in answer
    assert "拆下同步盘固定螺母" not in answer


def test_manual_evidence_answer_refuses_missing_model_detail_from_evidence() -> None:
    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": [
                            {
                                "content": "安装活塞环：先安装油环组合，再安装第二道气环，最后安装第一道气环。",
                                "metadata": {
                                    "chunk_type": "step_raw",
                                    "section_title": "5.6 安装活塞环",
                                    "parent_section_id": "sec-ring",
                                    "section_match_ids": ["sec-ring"],
                                    "page": 21,
                                    "source_index": 10,
                                },
                            }
                        ],
                    }
                ]
            }
        ]
    }

    answer = _format_manual_evidence_answer_from_metadata(
        "活塞环安装专用扩张器型号是什么？",
        metadata,
    )

    assert answer is not None
    assert not answer.startswith("根据手册")
    assert "手册未说明" in answer
    assert "扩张器型号" in answer
    assert "Snap-on" not in answer


def test_manual_evidence_answer_keeps_numbered_step_after_stale_previous_heading(monkeypatch) -> None:
    """OCR chunks may keep the previous subsection title on the first line."""

    class FakeVectorService:
        def get_section_records(self, document_id, parent_section_id, limit=200, chunk_type=None):
            return []

    class FakeSectionIndex:
        def build(self, vector_service):
            return None

        def find(self, query):
            return []

    from services.knowledge import vector_service as vector_service_module
    from services.retrieval.section_index import SectionTitleIndex

    monkeypatch.setattr(vector_service_module, "get_vector_service", lambda: FakeVectorService())
    monkeypatch.setattr(SectionTitleIndex, "get_instance", classmethod(lambda cls: FakeSectionIndex()))

    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": [
                            {
                                "id": "title",
                                "content": "检查磁电机转子离合器单向器",
                                "metadata": {
                                    "chunk_type": "text",
                                    "document_id": "manual-doc",
                                    "section_title": "7.3 磁电机转子离合器分部件",
                                    "parent_section_id": "sec-magneto",
                                    "section_match_ids": ["sec-magneto"],
                                    "page": 31,
                                    "source_index": 4,
                                },
                            },
                            {
                                "id": "step-1",
                                "content": (
                                    "安装减速齿轮\n"
                                    "1. 将起动大齿 “1” 安装到磁电机转子离合器 “2” 上，并固定住磁电机转子离合器。"
                                ),
                                "metadata": {
                                    "chunk_type": "text",
                                    "document_id": "manual-doc",
                                    "section_title": "7.3 磁电机转子离合器分部件",
                                    "parent_section_id": "sec-magneto",
                                    "section_match_ids": ["sec-magneto"],
                                    "page": 31,
                                    "source_index": 5,
                                },
                            },
                            {
                                "id": "step-2",
                                "content": (
                                    "安装减速齿轮\n"
                                    "2. 顺时针 A 转动起动大齿：\n"
                                    "磁电机转子离合器与起动大齿应无相对滑动。"
                                ),
                                "metadata": {
                                    "chunk_type": "text",
                                    "document_id": "manual-doc",
                                    "section_title": "7.3 磁电机转子离合器分部件",
                                    "parent_section_id": "sec-magneto",
                                    "section_match_ids": ["sec-magneto"],
                                    "page": 31,
                                    "source_index": 6,
                                },
                            },
                            {
                                "id": "next-install-section",
                                "content": (
                                    "安装磁电机转子离合器分部件\n"
                                    "1. 按爆炸图所示，依次安装以下部件：\n"
                                    "30×42×1.4 止推垫圈"
                                ),
                                "metadata": {
                                    "chunk_type": "step_raw",
                                    "document_id": "manual-doc",
                                    "section_title": "7.3 磁电机转子离合器分部件",
                                    "parent_section_id": "sec-magneto",
                                    "section_match_ids": ["sec-magneto"],
                                    "page": 32,
                                    "source_index": 7,
                                },
                            },
                        ],
                    }
                ]
            }
        ]
    }

    answer = _format_manual_evidence_answer_from_metadata(
        "磁电机转子离合器单向器怎么检查？",
        metadata,
    )

    assert answer is not None
    assert "将起动大齿" in answer
    assert "固定住磁电机转子离合器" in answer
    assert "顺时针 A 转动起动大齿" in answer
    assert "按爆炸图所示" not in answer


def test_manual_evidence_answer_ignores_outline_noise_when_specific_check_steps_exist(monkeypatch) -> None:
    """TOC-like chunks can contain the right words but wrong page/section metadata."""

    class FakeVectorService:
        def get_section_records(self, document_id, parent_section_id, limit=200, chunk_type=None):
            return []

    class FakeSectionIndex:
        def build(self, vector_service):
            return None

        def find(self, query):
            return []

    from services.knowledge import vector_service as vector_service_module
    from services.retrieval.section_index import SectionTitleIndex

    monkeypatch.setattr(vector_service_module, "get_vector_service", lambda: FakeVectorService())
    monkeypatch.setattr(SectionTitleIndex, "get_instance", classmethod(lambda cls: FakeSectionIndex()))

    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": [
                            {
                                "id": "toc-noise",
                                "content": (
                                    "7.3 磁电机转子离合器分部件\n"
                                    "拆卸磁电机转子离合器分部件\n"
                                    "检查磁电机转子离合器单向器\n"
                                    "安装磁电机转子离合器分部件"
                                ),
                                "metadata": {
                                    "chunk_type": "step_raw",
                                    "document_id": "manual-doc",
                                    "section_title": "1.1 拆卸火花塞",
                                    "parent_section_id": "sec-spark",
                                    "page": 2,
                                    "source_index": 1,
                                },
                            },
                            {
                                "id": "check-a",
                                "content": (
                                    "安装减速齿轮\n"
                                    "2. 顺时针 A 转动起动大齿：\n"
                                    "磁电机转子离合器与起动大齿应无相对滑动。"
                                ),
                                "metadata": {
                                    "chunk_type": "step_raw",
                                    "document_id": "manual-doc",
                                    "section_title": "7.3 磁电机转子离合器分部件",
                                    "parent_section_id": "sec-magneto",
                                    "section_match_ids": ["sec-magneto"],
                                    "page": 31,
                                    "source_index": 7,
                                },
                            },
                            {
                                "id": "check-b",
                                "content": (
                                    "安装减速齿轮\n"
                                    "3. 逆时针 B 转动起动大齿：\n"
                                    "应能自由转动。"
                                ),
                                "metadata": {
                                    "chunk_type": "step_raw",
                                    "document_id": "manual-doc",
                                    "section_title": "7.3 磁电机转子离合器分部件",
                                    "parent_section_id": "sec-magneto",
                                    "section_match_ids": ["sec-magneto"],
                                    "page": 31,
                                    "source_index": 8,
                                },
                            },
                        ],
                    }
                ]
            }
        ]
    }

    answer = _format_manual_evidence_answer_from_metadata(
        "检查磁电机转子离合器单向器时顺时针A和逆时针B分别应怎样？",
        metadata,
    )

    assert answer is not None
    assert "第31页" in answer
    assert "顺时针 A 转动起动大齿" in answer
    assert "无相对滑动" in answer
    assert "逆时针 B 转动起动大齿" in answer
    assert "自由转动" in answer
    assert "拆卸火花塞" not in answer


def test_manual_evidence_answer_keeps_exploded_view_part_list_continuations(monkeypatch) -> None:
    """Exploded-view install lists often continue as short unnumbered part rows."""

    class FakeVectorService:
        def get_section_records(self, document_id, parent_section_id, limit=200, chunk_type=None):
            return []

    class FakeSectionIndex:
        def build(self, vector_service):
            return None

        def find(self, query):
            return []

    from services.knowledge import vector_service as vector_service_module
    from services.retrieval.section_index import SectionTitleIndex

    monkeypatch.setattr(vector_service_module, "get_vector_service", lambda: FakeVectorService())
    monkeypatch.setattr(SectionTitleIndex, "get_instance", classmethod(lambda cls: FakeSectionIndex()))

    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": [
                            {
                                "id": "install-title",
                                "content": "安装磁电机转子离合器分部件",
                                "metadata": {
                                    "chunk_type": "text",
                                    "document_id": "manual-doc",
                                    "section_title": "7.3 磁电机转子离合器分部件",
                                    "parent_section_id": "sec-magneto",
                                    "section_match_ids": ["sec-magneto"],
                                    "page": 31,
                                    "source_index": 6,
                                },
                            },
                            {
                                "id": "install-step-1",
                                "content": "1. 按爆炸图所示，依次安装以下部件：\n30×42×1.4 止推垫圈",
                                "metadata": {
                                    "chunk_type": "step_raw",
                                    "document_id": "manual-doc",
                                    "section_title": "7.3 磁电机转子离合器分部件",
                                    "parent_section_id": "sec-magneto",
                                    "section_match_ids": ["sec-magneto"],
                                    "page": 31,
                                    "source_index": 7,
                                },
                            },
                            {
                                "id": "install-list-1",
                                "content": "26.2×38×1 止推垫圈\n滚针轴承\n半圆键\n电起动大齿",
                                "metadata": {
                                    "chunk_type": "step_raw",
                                    "document_id": "manual-doc",
                                    "section_title": "7.3 磁电机转子离合器分部件",
                                    "parent_section_id": "sec-magneto",
                                    "section_match_ids": ["sec-magneto"],
                                    "page": 31,
                                    "source_index": 8,
                                },
                            },
                            {
                                "id": "install-list-2",
                                "content": "26.2×38×1 止推垫圈\n磁电机转子离合器\n12.3×26×3 垫片\nM12×1.25×50 六角法兰面螺栓",
                                "metadata": {
                                    "chunk_type": "step_raw",
                                    "document_id": "manual-doc",
                                    "section_title": "7.3 磁电机转子离合器分部件",
                                    "parent_section_id": "sec-magneto",
                                    "section_match_ids": ["sec-magneto"],
                                    "page": 32,
                                    "source_index": 9,
                                },
                            },
                        ],
                    }
                ]
            }
        ]
    }

    answer = _format_manual_evidence_answer_from_metadata(
        "安装磁电机转子离合器分部件的爆炸图顺序是什么？",
        metadata,
    )

    assert answer is not None
    assert "30×42×1.4 止推垫圈" in answer
    assert "滚针轴承" in answer
    assert "半圆键" in answer
    assert "12.3×26×3 垫片" in answer
    assert "M12×1.25×50 六角法兰面螺栓" in answer


def test_manual_evidence_answer_does_not_treat_reinstall_phrase_as_action_heading(monkeypatch) -> None:
    class FakeVectorService:
        def get_section_records(self, document_id, parent_section_id, limit=200, chunk_type=None):
            return []

    class FakeSectionIndex:
        def build(self, vector_service):
            return None

        def find(self, query):
            return []

    from services.knowledge import vector_service as vector_service_module
    from services.retrieval.section_index import SectionTitleIndex

    monkeypatch.setattr(vector_service_module, "get_vector_service", lambda: FakeVectorService())
    monkeypatch.setattr(SectionTitleIndex, "get_instance", classmethod(lambda cls: FakeSectionIndex()))

    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": [
                            {
                                "id": "step-1",
                                "content": "1. 将传动主轴分部件和传动副轴分部件预装完成后，一同装入左曲轴箱内。",
                                "metadata": {
                                    "chunk_type": "step_raw",
                                    "document_id": "manual-doc",
                                    "section_title": "8.5 安装传动装置",
                                    "parent_section_id": "sec-transmission-install",
                                    "section_match_ids": ["sec-transmission-install"],
                                    "page": 37,
                                    "source_index": 1,
                                },
                            },
                            {
                                "id": "step-2",
                                "content": "2. 依次安装以下部件：\nL拨叉\n变速鼓\nC拨叉\nR拨叉\n拨叉轴\n换档轴",
                                "metadata": {
                                    "chunk_type": "step_raw",
                                    "document_id": "manual-doc",
                                    "section_title": "8.5 安装传动装置",
                                    "parent_section_id": "sec-transmission-install",
                                    "section_match_ids": ["sec-transmission-install"],
                                    "page": 37,
                                    "source_index": 2,
                                },
                            },
                            {
                                "id": "step-7",
                                "content": "7. 装上换档星形凸轮，检查换档是否顺畅：\n若不顺畅 → 重新安装传动装置",
                                "metadata": {
                                    "chunk_type": "step_raw",
                                    "document_id": "manual-doc",
                                    "section_title": "8.5 安装传动装置",
                                    "parent_section_id": "sec-transmission-install",
                                    "section_match_ids": ["sec-transmission-install"],
                                    "page": 37,
                                    "source_index": 7,
                                },
                            },
                            {
                                "id": "next-section",
                                "content": "9.1 曲轴、平衡轴装配部件清单\nNo. 38 / 41",
                                "metadata": {
                                    "chunk_type": "step_raw",
                                    "document_id": "manual-doc",
                                    "section_title": "8.5 安装传动装置",
                                    "parent_section_id": "sec-transmission-install",
                                    "section_match_ids": ["sec-transmission-install"],
                                    "page": 38,
                                    "source_index": 8,
                                },
                            },
                        ],
                    }
                ]
            }
        ]
    }

    answer = _format_manual_evidence_answer_from_metadata("如何安装传动装置？", metadata)

    assert answer is not None
    assert "传动主轴分部件" in answer
    assert "L拨叉" in answer
    assert "重新安装传动装置" in answer
    assert "曲轴、平衡轴装配部件清单" not in answer


def test_manual_evidence_answer_includes_page_boundary_records_with_target_title(monkeypatch) -> None:
    class FakeVectorService:
        def get_section_records(self, document_id, parent_section_id, limit=200, chunk_type=None):
            return []

        def get_page_records(self, document_id, page, chunk_type=None, limit=80):
            assert document_id == "manual-doc"
            assert page == 39
            return [
                {
                    "id": "boundary-spill",
                    "content": (
                        "9.3 检查曲轴与平衡轴\n"
                        "（1）检查轴承\n"
                        "用手拨动轴承内圈：\n"
                        "若有卡滞或磨损现象 → 更换有缺陷的轴承。\n"
                        "箱体上的曲轴轴承不可左右互换。"
                    ),
                    "metadata": {
                        "chunk_type": "text",
                        "document_id": "manual-doc",
                        "section_title": "9.2 拆卸曲轴与平衡轴",
                        "parent_section_id": "sec-wrong-previous",
                        "page": 39,
                        "source_index": 1,
                    },
                }
            ]

    class FakeSectionIndex:
        def build(self, vector_service):
            return None

        def find(self, query):
            return []

    from services.knowledge import vector_service as vector_service_module
    from services.retrieval.section_index import SectionTitleIndex

    monkeypatch.setattr(vector_service_module, "get_vector_service", lambda: FakeVectorService())
    monkeypatch.setattr(SectionTitleIndex, "get_instance", classmethod(lambda cls: FakeSectionIndex()))

    metadata = {
        "route_plan": _component_route("manual-doc", "曲轴与平衡轴", "检查"),
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": [
                            {
                                "id": "table",
                                "content": (
                                    "表格：第39页表格\n"
                                    "检查项目 | 技术要求\n"
                                    "1.曲轴轴向跳动 | ≤ 0.03 mm\n"
                                    "2.连杆大头轴向间隙 | 0.15 – 0.35 mm"
                                ),
                                "metadata": {
                                    "chunk_type": "table",
                                    "document_id": "manual-doc",
                                    "section_title": "9.3 检查曲轴与平衡轴",
                                    "parent_section_id": "sec-check-crank",
                                    "section_match_ids": ["sec-check-crank"],
                                    "page": 39,
                                },
                            }
                        ],
                    }
                ]
            }
        ]
    }

    answer = _format_manual_evidence_answer_from_metadata(
        "曲轴与平衡轴检查项目的技术要求有哪些？",
        metadata,
    )

    assert answer is not None
    assert "≤ 0.03 mm" in answer
    assert "更换有缺陷的轴承" in answer
    assert "曲轴轴承不可左右互换" in answer


def test_manual_evidence_answer_strips_embedded_next_section_heading_from_step_tail(monkeypatch) -> None:
    class FakeVectorService:
        def get_section_records(self, document_id, parent_section_id, limit=200, chunk_type=None):
            return []

        def get_page_records(self, document_id, page, chunk_type=None, limit=120):
            return []

    class FakeSectionIndex:
        def build(self, vector_service):
            return None

        def find(self, query):
            return []

    from services.knowledge import vector_service as vector_service_module
    from services.retrieval.section_index import SectionTitleIndex

    monkeypatch.setattr(vector_service_module, "get_vector_service", lambda: FakeVectorService())
    monkeypatch.setattr(SectionTitleIndex, "get_instance", classmethod(lambda cls: FakeSectionIndex()))

    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": [
                            {
                                "id": "step-1",
                                "content": "1. 预压涨紧器\n松开并取下 M6×10 顶销螺栓。",
                                "metadata": {
                                    "chunk_type": "step_raw",
                                    "document_id": "manual-doc",
                                    "section_title": "4.4 涨紧器",
                                    "parent_section_id": "sec-tensioner",
                                    "section_match_ids": ["sec-tensioner"],
                                    "page": 13,
                                    "source_index": 1,
                                },
                            },
                            {
                                "id": "step-4-with-tail-heading",
                                "content": (
                                    "4. 对齐\n"
                                    "转动曲轴两圈，使磁电机转子 “T” 标记前的刻线与左曲轴箱盖上 M14×1.5 螺塞孔标记槽中线对齐。\n"
                                    "气缸头盖"
                                ),
                                "metadata": {
                                    "chunk_type": "step_raw",
                                    "document_id": "manual-doc",
                                    "section_title": "4.4 涨紧器",
                                    "parent_section_id": "sec-tensioner",
                                    "section_match_ids": ["sec-tensioner"],
                                    "page": 13,
                                    "source_index": 4,
                                },
                            },
                        ],
                    }
                ]
            }
        ]
    }

    answer = _format_manual_evidence_answer_from_metadata("如何安装涨紧器？", metadata)

    assert answer is not None
    assert "4. 对齐" in answer
    assert "转动曲轴两圈" in answer
    assert "气缸头盖" not in answer
