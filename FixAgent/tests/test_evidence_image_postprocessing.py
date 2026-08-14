"""Response-level image post-processing regressions."""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.main import (
    _collect_direct_evidence_page_images,
    _filter_evidence_images_to_target_section,
    _image_specific_anchor_terms,
    _merge_evidence_images,
    _narrow_evidence_images_to_query_target_pages,
    _select_evidence_images_for_response,
    _text_evidence_pages,
    _apply_final_image_contract,
    _extract_evidence_images,
)
import api.main as api_main
from schemas.response import EvidenceImage


def _img(page: int, title: str = "目标章节") -> EvidenceImage:
    return EvidenceImage(
        image_url=f"http://example.test/p{page}.png",
        caption=f"page {page}",
        page=page,
        section_title=title,
        document_id="manual-doc",
    )


def test_duplicate_raw_and_summary_images_union_all_binding_edges() -> None:
    raw = EvidenceImage(
        image_url="/camshaft.png",
        source_chunk_id="image-camshaft",
        role="positioned_step",
        binding_confidence=0.95,
        step_ids=["step-align"],
        binding_schema_version=2,
        bindings=[{
            "target_id": "step-align",
            "target_type": "step",
            "relation": "layout_anchor",
            "confidence": 0.95,
        }],
    )
    summary = raw.model_copy(update={
        "image_summary": "凸轮轴安装顺序和标记图",
        "step_ids": ["step-order"],
        "bindings": [{
            "target_id": "step-order",
            "target_type": "step",
            "relation": "procedure_layout_member",
            "confidence": 0.85,
        }],
    })

    merged = api_main._merge_duplicate_evidence_image(raw, summary)

    assert merged.step_ids == ["step-align", "step-order"]
    assert [binding.target_id for binding in merged.bindings] == [
        "step-align",
        "step-order",
    ]
    assert merged.image_summary == "凸轮轴安装顺序和标记图"


def _with_final_claims(metadata: dict, source_ids: list[str] | None = None) -> dict:
    pages = [
        int(value)
        for value in metadata.get("_deterministic_answer_evidence_pages") or []
        if str(value).isdigit()
    ]
    if not pages:
        pages = [
            int(value)
            for value in metadata.get("allowed_evidence_pages") or []
            if str(value).isdigit()
        ]
    trace_items = [
        item
        for step in metadata.get("react_trace") or []
        if isinstance(step, dict)
        for call in step.get("tool_calls") or []
        if isinstance(call, dict)
        for item in call.get("result_data") or []
        if isinstance(item, dict)
    ]
    if not pages:
        pages = list(dict.fromkeys(
            int(page)
            for item in trace_items
            for page in [
                (item.get("metadata") or {}).get("page")
                or (item.get("metadata") or {}).get("page_number")
            ]
            if str(page).isdigit()
        ))
    document_ids = [
        str(value)
        for value in metadata.get("_deterministic_answer_document_ids") or []
        if str(value).strip()
    ]
    if not document_ids:
        document_ids = list(dict.fromkeys(
            str((item.get("metadata") or {}).get("document_id") or "").strip()
            for item in trace_items
            if str((item.get("metadata") or {}).get("document_id") or "").strip()
        )) or ["manual-doc"]
    ids = source_ids or [f"final-text-{page}" for page in pages]
    section_ids = [
        str(value)
        for value in metadata.get("_deterministic_answer_section_ids") or []
        if str(value).strip()
    ]
    records = []
    for index, source_id in enumerate(ids):
        page = pages[min(index, len(pages) - 1)] if pages else None
        records.append({
            "id": source_id,
            "content": f"final evidence for {source_id}",
            "metadata": {
                "document_id": document_ids[0],
                "page": page,
                "chunk_type": "text",
                "chunk_label": "step",
                "answer_role": "procedure_step",
                "parent_section_id": section_ids[0] if section_ids else "",
                "section_title": str(metadata.get("_deterministic_answer_section_title") or ""),
            },
        })
    trace = list(metadata.get("react_trace") or [])
    if records:
        trace.append({"tool_calls": [{"name": "knowledge_retrieval", "result_data": records}]})
    metadata["react_trace"] = trace
    metadata["authorized_claim_evidence_bindings"] = [{
        "claim_id": "test-final-claim",
        "evidence_ids": [
            f"manual:{document_ids[0]}:{source_id}"
            for source_id in ids
        ],
    }]
    metadata.setdefault("response_audit", {"passed": True})
    return metadata


def test_extract_evidence_images_merges_summary_into_source_image_entity() -> None:
    metadata = {
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": [
                    {
                        "id": "doc:image-summary:1",
                        "content": "气缸内壁存在纵向拉伤。",
                        "metadata": {
                            "chunk_type": "image_summary",
                            "source_image_id": "doc:image:1",
                            "image_url": "http://example.test/cylinder.png",
                            "image_title": "气缸内壁检查图",
                            "image_summary": "气缸内壁存在纵向拉伤。",
                            "page": 18,
                        },
                    },
                    {
                        "id": "doc:image:1",
                        "content": "",
                        "metadata": {
                            "chunk_type": "image",
                            "image_url": "http://example.test/cylinder.png",
                            "page": 18,
                        },
                    },
                ],
            }],
        }],
    }

    images = _extract_evidence_images(metadata)

    assert len(images) == 1
    assert images[0].source_chunk_id == "doc:image:1"
    assert images[0].image_title == "气缸内壁检查图"
    assert images[0].image_summary == "气缸内壁存在纵向拉伤。"


def test_merge_evidence_images_enriches_direct_image_with_trace_summary() -> None:
    direct = EvidenceImage(
        image_url="http://example.test/piston.png",
        page=18,
        document_id="manual-doc",
        source_chunk_id="image:piston",
        role="positioned_step",
        binding_confidence=0.95,
        step_ids=["step-check"],
    )
    traced = direct.model_copy(update={
        "image_title": "活塞及其裙部结构示意图",
        "image_summary": "红色箭头标出活塞裙部。",
    })

    merged = _merge_evidence_images([traced], [direct])

    assert len(merged) == 1
    assert merged[0].image_title == "活塞及其裙部结构示意图"
    assert merged[0].image_summary == "红色箭头标出活塞裙部。"


def test_merge_evidence_images_does_not_synthesize_strong_binding() -> None:
    role_only = EvidenceImage(
        image_url="http://example.test/starter.png",
        source_chunk_id="image-role",
        role="same_page_step",
        binding_confidence=1.0,
    )
    ids_only = EvidenceImage(
        image_url="http://example.test/starter.png",
        source_chunk_id="image-ids",
        step_ids=["step-install-starter"],
    )

    merged = _merge_evidence_images([role_only], [ids_only])

    assert len(merged) == 1
    assert not (merged[0].role == "same_page_step" and merged[0].step_ids)


def test_merge_evidence_images_keeps_complete_legacy_binding_bundle() -> None:
    weak = EvidenceImage(
        image_url="http://example.test/starter.png",
        source_chunk_id="image-weak",
        role="",
        binding_confidence=0.1,
    )
    complete = EvidenceImage(
        image_url="http://example.test/starter.png",
        source_chunk_id="image-complete",
        role="same_page_step",
        binding_confidence=1.0,
        step_ids=["step-install-starter"],
    )

    merged = _merge_evidence_images([weak], [complete])

    assert len(merged) == 1
    assert merged[0].source_chunk_id == "image-complete"
    assert merged[0].role == "same_page_step"
    assert merged[0].step_ids == ["step-install-starter"]


def test_merge_evidence_images_keeps_caption_confidence_paired() -> None:
    caption = EvidenceImage(
        image_url="http://example.test/starter.png",
        caption="安装起动电机",
        caption_confidence=0.4,
    )
    confidence_only = EvidenceImage(
        image_url="http://example.test/starter.png",
        caption="",
        caption_confidence=1.0,
    )

    merged = _merge_evidence_images([caption], [confidence_only])

    assert merged[0].caption == "安装起动电机"
    assert merged[0].caption_confidence == 0.4


def test_final_image_contract_removes_manual_images_when_policy_forbids_them() -> None:
    message, images = _apply_final_image_contract(
        "以下内容来自 AI，仅供参考。",
        [_img(32, "7.4 左曲轴箱盖")],
        {"response_policy": {"images_allowed": False}},
    )

    assert message == "以下内容来自 AI，仅供参考。"
    assert images == []


def test_final_image_contract_removes_dangling_figure_reference_without_image() -> None:
    message, images = _apply_final_image_contract(
        "如图所示，检查曲轴油封。",
        [],
        {"response_policy": {"images_allowed": True}},
    )

    assert message == "，检查曲轴油封。"
    assert images == []


def test_direct_section_images_follow_answer_procedure_scope(monkeypatch) -> None:
    class FakeVectorService:
        def get_section_records(self, document_id, section_id, limit=20, chunk_type=None):
            assert document_id == "manual-doc"
            assert section_id == "sec-combined"
            assert chunk_type == "image"
            return [
                {
                    "id": "image-cover",
                    "metadata": {
                        "chunk_type": "image",
                        "document_id": document_id,
                        "parent_section_id": section_id,
                        "page": 26,
                        "image_url": "http://example.test/cover.png",
                        "procedure_scope_ids": ["proc:install-cover"],
                    },
                },
                {
                    "id": "image-clutch",
                    "metadata": {
                        "chunk_type": "image",
                        "document_id": document_id,
                        "parent_section_id": section_id,
                        "page": 27,
                        "image_url": "http://example.test/clutch.png",
                        "procedure_scope_ids": ["proc:install-clutch"],
                    },
                },
            ]

    from services.knowledge import vector_service as vector_service_module

    monkeypatch.setattr(vector_service_module, "get_vector_service", lambda: FakeVectorService())
    metadata = {
        "original_user_message": "如何安装离合器",
        "route_plan": {
            "action": "grounded_retrieval",
            "entity_role": "document_component",
            "selected_document_id": "manual-doc",
        },
        "_deterministic_answer_procedure_scope_id": "proc:install-clutch",
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": [{
                    "content": "1. 检查离合器摩擦片。",
                    "metadata": {
                        "retrieval_plan_intent": "procedure",
                        "document_id": "manual-doc",
                        "parent_section_id": "sec-combined",
                        "section_match_ids": ["sec-combined"],
                        "chunk_type": "step_raw",
                        "context_role": "primary",
                    },
                }],
            }],
        }],
    }

    images = asyncio.run(api_main._collect_direct_section_images(metadata))

    assert [(image.page, image.source_chunk_id) for image in images] == [(27, "image-clutch")]


def test_direct_section_images_load_stable_summary_record(monkeypatch) -> None:
    class FakeVectorService:
        def get_section_records(self, document_id, section_id, limit=20, chunk_type=None):
            assert document_id == "manual-doc"
            assert section_id == "sec-check"
            assert chunk_type == "image"
            return [{
                "id": "prefix:21:img:0001",
                "metadata": {
                    "chunk_type": "image",
                    "document_id": document_id,
                    "parent_section_id": section_id,
                    "page": 18,
                    "image_url": "http://example.test/piston.png",
                    "binding_role": "positioned_step",
                    "binding_confidence": 0.95,
                },
            }]

        def get_vector_record(self, doc_id):
            assert doc_id == "prefix:21:ims:0001"
            return {
                "id": doc_id,
                "text": "红色箭头标出活塞裙部。",
                "metadata": {
                    "chunk_type": "image_summary",
                    "source_image_id": "prefix:21:img:0001",
                    "image_title": "活塞及其裙部结构示意图",
                    "image_summary": "红色箭头标出活塞裙部。",
                },
            }

    from services.knowledge import vector_service as vector_service_module

    monkeypatch.setattr(vector_service_module, "get_vector_service", lambda: FakeVectorService())
    metadata = {
        "original_user_message": "如何检查活塞裙部？请返回对应图片。",
        "route_plan": {
            "action": "grounded_retrieval",
            "entity_role": "document_component",
            "selected_document_id": "manual-doc",
        },
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": [{
                    "content": "检查活塞裙部。",
                    "metadata": {
                        "qualification": "qualified",
                        "retrieval_plan_intent": "procedure",
                        "document_id": "manual-doc",
                        "parent_section_id": "sec-check",
                        "section_match_ids": ["sec-check"],
                        "chunk_type": "step_raw",
                    },
                }],
            }],
        }],
    }

    images = asyncio.run(api_main._collect_direct_section_images(metadata))

    assert len(images) == 1
    assert images[0].image_title == "活塞及其裙部结构示意图"
    assert images[0].image_summary == "红色箭头标出活塞裙部。"


def test_text_evidence_pages_prefers_deterministic_answer_pages() -> None:
    metadata = {
        "_deterministic_answer_evidence_pages": [9],
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": [
                            {
                                "content": "table row on page 9",
                                "metadata": {
                                    "chunk_type": "table",
                                    "document_id": "manual-doc",
                                    "parent_section_id": "sec-inventory",
                                    "section_match_ids": ["sec-wrong"],
                                    "page": 9,
                                },
                            }
                        ],
                    }
                ]
            }
        ],
    }

    metadata = _with_final_claims(metadata)
    assert _text_evidence_pages(metadata) == [9]


def test_collect_direct_evidence_page_images_collects_but_gate_rejects_same_page_cross_section() -> None:
    class FakeVectorService:
        def get_page_records(self, document_id, page, chunk_type=None, limit=20):
            assert document_id == "manual-doc"
            assert page == 3
            assert chunk_type == "image"
            return [
                {
                    "id": "image-from-neighbor-section",
                    "metadata": {
                        "chunk_type": "image",
                        "document_id": "manual-doc",
                        "parent_section_id": "sec-neighbor",
                        "section_title": "1.2 check spark plug",
                        "page": 3,
                        "image_url": "http://example.test/page3.png",
                    },
                }
            ]

    metadata = {
        "original_user_message": "install spark plug",
        "_deterministic_answer_evidence_pages": [3],
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": [
                            {
                                "content": "install spark plug on page 3",
                                "metadata": {
                                    "chunk_type": "text",
                                    "document_id": "manual-doc",
                                    "parent_section_id": "sec-install",
                                    "section_match_ids": ["sec-install"],
                                    "page": 3,
                                },
                            }
                        ],
                    }
                ]
            }
        ],
    }

    metadata = _with_final_claims(metadata)
    images = _collect_direct_evidence_page_images(metadata, vector_service=FakeVectorService())

    assert [image.source_chunk_id for image in images] == ["image-from-neighbor-section"]
    assert _select_evidence_images_for_response(images, metadata) == []


def test_collect_direct_evidence_page_images_collects_but_gate_rejects_ocr_only_candidate() -> None:
    class FakeVectorService:
        def get_page_records(self, document_id, page, chunk_type=None, limit=20):
            assert document_id == "manual-doc"
            assert page == 21
            assert chunk_type == "image"
            return [
                {
                    "id": "image-substep-page",
                    "content": "第21页操作示意图",
                    "metadata": {
                        "chunk_type": "image",
                        "document_id": "manual-doc",
                        "page": 21,
                        "image_url": "http://example.test/page21.png",
                        "visual_context_text": "安装活塞销；安装活塞销挡圈；开口错开120°～180°",
                    },
                }
            ]

    metadata = {
        "original_user_message": "如何安装气缸与活塞？",
        "_deterministic_answer_evidence_pages": [21],
        "_deterministic_answer_document_ids": ["manual-doc"],
    }

    metadata = _with_final_claims(metadata)
    images = _collect_direct_evidence_page_images(metadata, vector_service=FakeVectorService())

    assert [image.source_chunk_id for image in images] == ["image-substep-page"]
    assert _select_evidence_images_for_response(images, metadata) == []


def test_collect_direct_evidence_page_images_preserves_image_binding_metadata() -> None:
    class FakeVectorService:
        def get_page_records(self, document_id, page, chunk_type=None, limit=20):
            return [
                {
                    "id": "image-cylinder-wall",
                    "content": "气缸内壁检查图",
                    "metadata": {
                        "chunk_type": "image",
                        "document_id": document_id,
                        "page": page,
                        "section_title": "5.3 检查气缸与活塞",
                        "image_url": "http://example.test/cylinder-wall.png",
                            "caption": "气缸内壁检查图",
                            "caption_confidence": 0.9,
                            "related_step_chunk_ids": ["step-check-cylinder-wall"],
                        "procedure_scope_ids": ["scope-check-cylinder"],
                            "binding_role": "positioned_step",
                            "binding_confidence": 0.95,
                    },
                }
            ]

    metadata = {
        "original_user_message": "如何检查气缸内壁？请返回对应图片。",
        "_deterministic_answer_evidence_pages": [18],
        "_deterministic_answer_document_ids": ["manual-doc"],
        "_deterministic_answer_section_title": "5.3 检查气缸与活塞",
    }

    metadata = _with_final_claims(metadata, ["step-check-cylinder-wall"])
    images = _collect_direct_evidence_page_images(
        metadata,
        vector_service=FakeVectorService(),
    )

    assert len(images) == 1
    assert images[0].step_ids == ["step-check-cylinder-wall"]
    assert images[0].step_id == "step-check-cylinder-wall"
    assert images[0].role == "positioned_step"
    assert images[0].binding_confidence == 0.95


def test_collect_direct_evidence_page_images_keeps_empty_semantics_with_step_binding() -> None:
    class FakeVectorService:
        def get_page_records(self, document_id, page, chunk_type=None, limit=20):
            return [{
                "id": "image-starter-install",
                "content": "",
                "metadata": {
                    "chunk_type": "image",
                    "document_id": document_id,
                    "page": page,
                    "section_id": "section-install-starter",
                    "section_title": "2.3 安装起动电机",
                    "image_url": "http://example.test/starter.png",
                    "caption": "",
                    "image_title": "",
                    "image_summary": "",
                    "related_step_chunk_ids": ["step-install-starter"],
                    "related_text_chunk_ids": ["text-install-starter"],
                    "procedure_scope_ids": ["scope-install-starter"],
                    "binding_role": "same_page_step",
                    "binding_confidence": 1.0,
                },
            }]

    metadata = {
        "original_user_message": "如何安装起动电机",
        "_deterministic_answer_evidence_pages": [5],
        "_deterministic_answer_document_ids": ["manual-doc"],
        "_deterministic_answer_section_ids": ["section-install-starter"],
        "_deterministic_answer_section_title": "2.3 安装起动电机",
    }

    metadata = _with_final_claims(metadata, ["step-install-starter"])
    images = _collect_direct_evidence_page_images(
        metadata,
        vector_service=FakeVectorService(),
    )

    assert len(images) == 1
    assert images[0].source_chunk_id == "image-starter-install"
    assert images[0].context_role != "page_render"
    assert images[0].step_ids == ["step-install-starter"]
    assert images[0].text_ids == ["text-install-starter"]
    assert images[0].procedure_scope_ids == ["scope-install-starter"]
    assert images[0].role == "same_page_step"


def test_page_image_query_match_does_not_use_shared_page_ocr_as_image_identity() -> None:
    record = {
        "id": "image-piston-skirt",
        "content": "活塞裙部检查图",
        "metadata": {
            "section_title": "5.3 检查气缸与活塞",
            "caption": "活塞裙部检查图",
            "visual_context_text": "检查气缸内壁是否有磨损、拉伤。检查活塞裙部。",
        },
    }

    assert api_main._page_image_matches_query("气缸内壁图片", record) is False


def test_section_rebinding_rejects_shared_page_ocr_without_image_level_binding() -> None:
    record = {
        "id": "image-neighbor-section",
        "content": "气缸内壁检查图",
        "metadata": {
            "document_id": "manual-doc",
            "section_title": "4.8 气门",
            "caption": "气缸内壁检查图",
            "visual_context_text": "5.1 气缸活塞装配部件清单。检查气缸内壁。",
        },
    }

    assert api_main._page_image_supports_safe_section_rebinding(
        "如何检查气缸内壁？",
        record,
        "5.1 气缸活塞装配部件清单",
        ["manual-doc"],
        "manual-doc",
    ) is False


def test_target_section_filter_fails_closed_when_no_image_matches() -> None:
    images = [
        EvidenceImage(
            image_url="http://example.test/valve.png",
            page=17,
            section_title="4.8 气门",
            document_id="manual-doc",
            source_chunk_id="image-valve",
        )
    ]
    metadata = {
        "_deterministic_answer_section_title": "5.1 气缸活塞装配部件清单",
    }

    metadata = _with_final_claims(metadata)
    filtered = _filter_evidence_images_to_target_section(images, metadata)

    assert filtered == []


def test_collect_direct_evidence_page_images_does_not_render_page_for_neighbor_section(
    monkeypatch,
) -> None:
    class FakeVectorService:
        def get_page_records(self, document_id, page, chunk_type=None, limit=20):
            assert document_id == "manual-doc"
            assert page == 21
            assert chunk_type == "image"
            return [{
                "id": "image-next-section",
                "content": "5.6 安装活塞环 第21页插图",
                "metadata": {
                    "chunk_type": "image",
                    "document_id": "manual-doc",
                    "page": 21,
                    "section_title": "5.6 安装活塞环",
                    "image_url": "http://example.test/next-section.png",
                    "visual_context_text": "安装活塞销和活塞销挡圈",
                },
            }]

    monkeypatch.setattr(
        api_main,
        "_render_evidence_pdf_page_image",
        lambda metadata, document_id, page: EvidenceImage(
            image_url="/files/rendered_pages/manual-doc/page_021.png",
            caption="第21页页面截图",
            page=21,
            section_title="5.4 安装气缸与活塞",
            document_id="manual-doc",
            source_chunk_id="rendered-page:manual-doc:21",
            context_role="page_render",
        ),
    )
    metadata = {
        "original_user_message": "如何安装气缸与活塞？",
        "_deterministic_answer_evidence_pages": [21],
        "_deterministic_answer_document_ids": ["manual-doc"],
        "_deterministic_answer_section_title": "5.4 安装气缸与活塞",
    }

    images = _collect_direct_evidence_page_images(
        metadata,
        vector_service=FakeVectorService(),
    )

    assert images == []


def test_collect_direct_evidence_page_images_rejects_cross_section_page_ocr_rebinding() -> None:
    class FakeVectorService:
        def get_page_records(self, document_id, page, chunk_type=None, limit=20):
            assert document_id == "manual-doc"
            assert page == 28
            assert chunk_type == "image"
            return [{
                "id": "image-misbound-to-adjacent-section",
                "content": "7.9 安装星门护罩 第28页插图",
                "metadata": {
                    "chunk_type": "image",
                    "document_id": "manual-doc",
                    "page": 28,
                    "section_title": "7.9 安装星门护罩",
                    "image_url": "http://example.test/page28.png",
                    "visual_context_text": (
                        "7.8 拆卸星门护罩。依次松开护罩紧固件，取下星门护罩。"
                    ),
                },
            }]

    metadata = {
        "original_user_message": "如何拆卸星门护罩？",
        "_deterministic_answer_evidence_pages": [28],
        "_deterministic_answer_document_ids": ["manual-doc"],
        "_deterministic_answer_section_title": "7.8 拆卸星门护罩",
    }

    images = _collect_direct_evidence_page_images(
        metadata,
        vector_service=FakeVectorService(),
    )

    assert images == []


def test_collect_direct_evidence_page_images_does_not_render_page_when_indexed_images_do_not_match_query(monkeypatch) -> None:
    class FakeVectorService:
        def get_page_records(self, document_id, page, chunk_type=None, limit=20):
            assert document_id == "manual-doc"
            assert page == 21
            assert chunk_type == "image"
            return [
                {
                    "id": "unrelated-indexed-image",
                    "content": "右曲轴箱盖装配部件清单",
                    "metadata": {
                        "chunk_type": "image",
                        "document_id": "manual-doc",
                        "page": 21,
                        "image_url": "http://example.test/unrelated.png",
                        "visual_context_text": "右曲轴箱盖 装配部件清单",
                    },
                }
            ]

    def fake_render(metadata, document_id, page):
        return EvidenceImage(
            image_url="http://example.test/rendered-page21.png",
            caption="第21页页面截图",
            page=21,
            section_title="5.6 安装活塞环",
            document_id=document_id,
            source_chunk_id=f"rendered-page:{document_id}:{page}",
            context_role="page_render",
        )

    monkeypatch.setattr(api_main, "_render_evidence_pdf_page_image", fake_render)
    metadata = {
        "original_user_message": "如何安装活塞环？",
        "_deterministic_answer_evidence_pages": [21],
        "_deterministic_answer_document_ids": ["manual-doc"],
    }

    metadata = _with_final_claims(metadata)
    images = _collect_direct_evidence_page_images(metadata, vector_service=FakeVectorService())

    assert [(image.page, image.source_chunk_id) for image in images] == [
        (21, "unrelated-indexed-image")
    ]
    assert _select_evidence_images_for_response(images, metadata) == []


def test_collect_direct_evidence_page_images_does_not_render_pdf_page_without_explicit_request(
    tmp_path,
    monkeypatch,
) -> None:
    fitz = pytest.importorskip("fitz", reason="PyMuPDF is optional on LoongArch")
    pdf_path = tmp_path / "manual.pdf"
    doc = fitz.open()
    page = doc.new_page(width=200, height=120)
    page.insert_text((20, 30), "6.8 水泵")
    page.draw_rect(fitz.Rect(40, 50, 160, 90))
    doc.save(str(pdf_path))
    doc.close()

    class FakeVectorService:
        def get_page_records(self, document_id, page, chunk_type=None, limit=20):
            assert chunk_type == "image"
            return []

    monkeypatch.setattr(api_main._settings, "local_file_storage_dir", str(tmp_path / "public"))
    monkeypatch.setattr(api_main._settings, "file_public_base_url", "/files")

    metadata = {
        "original_user_message": "安装水泵的步骤是什么？",
        "_deterministic_answer_evidence_pages": [1],
        "_deterministic_answer_document_ids": ["manual-doc"],
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": [
                            {
                                "content": "安装水泵",
                                "metadata": {
                                    "chunk_type": "step_raw",
                                    "document_id": "manual-doc",
                                    "page": 1,
                                    "file_name": pdf_path.name,
                                    "source_file_url": str(pdf_path),
                                },
                            }
                        ],
                    }
                ]
            }
        ],
    }

    images = _collect_direct_evidence_page_images(metadata, vector_service=FakeVectorService())

    assert images == []
    assert not (tmp_path / "public" / "rendered_pages").exists()


def test_query_target_page_narrowing_drops_neighbor_substeps_from_expanded_text_evidence() -> None:
    metadata = {
        "original_user_message": "安装气缸与活塞时IN标记应该朝哪里？",
        "_deterministic_answer_evidence_pages": [19, 20, 21],
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": [
                            {
                                "content": "5.4 安装气缸与活塞 将活塞头部插入气缸裙部 IN标记一侧朝向气缸后侧",
                                "metadata": {
                                    "chunk_type": "text",
                                    "document_id": "manual-doc",
                                    "parent_section_id": "sec-cylinder-install",
                                    "section_title": "5.4 安装气缸与活塞",
                                    "page": 19,
                                },
                            },
                            {
                                "content": "注意事项 气缸与活塞组别 A B C D 相同组别",
                                "metadata": {
                                    "chunk_type": "text",
                                    "document_id": "manual-doc",
                                    "parent_section_id": "sec-cylinder-install",
                                    "section_title": "5.4 安装气缸与活塞",
                                    "page": 20,
                                },
                            },
                            {
                                "content": "安装活塞销 活塞销挡圈 开口错开120°～180°",
                                "metadata": {
                                    "chunk_type": "text",
                                    "document_id": "manual-doc",
                                    "parent_section_id": "sec-cylinder-install",
                                    "section_title": "5.4 安装气缸与活塞",
                                    "page": 21,
                                },
                            },
                        ],
                    }
                ]
            }
        ],
    }
    images = [
        EvidenceImage(
            image_url="http://example.test/p19.png",
            caption="第19页插图",
            page=19,
            section_title="5.4 安装气缸与活塞",
            document_id="manual-doc",
            source_chunk_id="img-19",
        ),
        EvidenceImage(
            image_url="http://example.test/p20.png",
            caption="第20页插图",
            page=20,
            section_title="5.4 安装气缸与活塞",
            document_id="manual-doc",
            source_chunk_id="img-20",
        ),
        EvidenceImage(
            image_url="http://example.test/p21.png",
            caption="第21页插图",
            page=21,
            section_title="5.4 安装气缸与活塞",
            document_id="manual-doc",
            source_chunk_id="img-21",
        ),
    ]

    narrowed = _narrow_evidence_images_to_query_target_pages(images, metadata)

    assert [image.page for image in narrowed] == [19]


def test_query_target_page_narrowing_uses_dynamic_route_component_over_misleading_image_context() -> None:
    class FakeVectorService:
        def get_page_records(self, document_id, page, chunk_type=None, limit=20):
            contexts = {
                41: "章节总览误带星门耦联簧字样",
                42: "目标部件作业位置图",
            }
            return [
                {
                    "id": f"img-{page}",
                    "metadata": {
                        "chunk_type": "image",
                        "document_id": document_id,
                        "page": page,
                        "image_url": f"http://example.test/p{page}.png",
                        "visual_context_text": contexts[page],
                    },
                }
            ]

    metadata = {
        "original_user_message": "苍穹装置作业时星门耦联簧如何处理？",
        "route_plan": {
            "query_contract": {
                "component": "星门耦联簧",
                "action": "处理",
                "orientation": "",
            }
        },
        "_deterministic_answer_evidence_pages": [41, 42],
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": [
                            {
                                "content": "准备苍穹装置并检查通用连接。",
                                "metadata": {"chunk_type": "text", "page": 41},
                            },
                            {
                                "content": "处理星门耦联簧并确认固定状态。",
                                "metadata": {"chunk_type": "text", "page": 42},
                            },
                        ],
                    }
                ]
            }
        ],
    }
    images = [
        EvidenceImage(
            image_url="http://example.test/p41.png",
            page=41,
            document_id="manual-doc",
            source_chunk_id="img-41",
        ),
        EvidenceImage(
            image_url="http://example.test/p42.png",
            page=42,
            document_id="manual-doc",
            source_chunk_id="img-42",
        ),
    ]

    narrowed = _narrow_evidence_images_to_query_target_pages(
        images,
        metadata,
        vector_service=FakeVectorService(),
    )

    assert [image.page for image in narrowed] == [42]


def test_query_target_page_narrowing_prefers_specific_labels_over_coarse_component() -> None:
    metadata = {
        "original_user_message": "安装星门护罩时K口、M区和Q槽的防护剂分别有什么要求？",
        "route_plan": {
            "query_contract": {
                "component": "星门护罩",
                "action": "安装",
                "orientation": "",
            }
        },
        "_deterministic_answer_evidence_pages": [41, 42],
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": [
                    {
                        "content": "安装星门护罩并紧固连接件。",
                        "metadata": {"chunk_type": "step_raw", "page": 41},
                    },
                    {
                        "content": "K口不得涂防护剂；M区薄涂；Q槽均匀涂抹。",
                        "metadata": {"chunk_type": "parameter", "page": 42},
                    },
                ],
            }],
        }],
    }
    images = [_img(41), _img(42)]

    narrowed = _narrow_evidence_images_to_query_target_pages(images, metadata)

    assert [image.page for image in narrowed] == [42]


def test_query_target_page_narrowing_drops_adjacent_same_section_substep() -> None:
    metadata = {
        "original_user_message": "拆卸发动机时排放机油要拆哪两个放油螺栓？",
        "_deterministic_answer_evidence_pages": [6, 7],
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": [
                            {
                                "content": "1. 排放机油 拆下发动机左曲轴箱上的放油螺栓 拆下车架上的放油螺栓",
                                "metadata": {
                                    "chunk_type": "text",
                                    "document_id": "manual-doc",
                                    "section_title": "3.2 拆卸发动机",
                                    "page": 6,
                                },
                            },
                            {
                                "content": "2. 排放冷却液 拆下水泵盖上的放水螺栓 打开右水箱盖",
                                "metadata": {
                                    "chunk_type": "text",
                                    "document_id": "manual-doc",
                                    "section_title": "3.2 拆卸发动机",
                                    "page": 7,
                                },
                            },
                        ],
                    }
                ]
            }
        ],
    }
    images = [
        _img(6, "3.2 拆卸发动机"),
        _img(7, "3.2 拆卸发动机"),
    ]

    narrowed = _narrow_evidence_images_to_query_target_pages(images, metadata)

    assert [image.page for image in narrowed] == [6]


def test_query_target_page_narrowing_keeps_broad_multi_page_procedure() -> None:
    metadata = {
        "original_user_message": "如何安装气缸与活塞？",
        "_deterministic_answer_evidence_pages": [19, 20, 21],
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": [
                            {
                                "content": "安装全新的箱体缸体垫片 将活塞头部插入气缸裙部 IN标记",
                                "metadata": {"chunk_type": "text", "page": 19},
                            },
                            {
                                "content": "气缸与活塞组别 A B C D",
                                "metadata": {"chunk_type": "text", "page": 20},
                            },
                            {
                                "content": "安装活塞销 安装活塞销挡圈",
                                "metadata": {"chunk_type": "text", "page": 21},
                            },
                        ],
                    }
                ]
            }
        ],
    }
    images = [_img(19, "5.4 安装气缸与活塞"), _img(20, "5.4 安装气缸与活塞"), _img(21, "5.4 安装气缸与活塞")]

    narrowed = _narrow_evidence_images_to_query_target_pages(images, metadata)

    assert [image.page for image in narrowed] == [19, 20, 21]


def test_target_section_filter_drops_same_page_neighbor_section_image_for_inventory_query() -> None:
    images = [
        EvidenceImage(
            image_url="http://example.test/p17-valve.png",
            caption="4.8 气门 第17页插图",
            page=17,
            section_title="4.8 气门",
            document_id="manual-doc",
            source_chunk_id="053f60433fa4:18:img:0002",
        ),
        EvidenceImage(
            image_url="http://example.test/p17-cylinder-piston.png",
            caption="5.1 气缸活塞装配部件清单 第17页插图",
            page=17,
            section_title="5.1 气缸活塞装配部件清单",
            document_id="manual-doc",
            source_chunk_id="053f60433fa4:19:img:0000",
        ),
    ]
    metadata = {
        "original_user_message": "帮我查一下气缸活塞装配部件清单",
        "_deterministic_answer_evidence_pages": [17],
        "_deterministic_answer_section_title": "5.1 气缸活塞装配部件清单",
    }

    metadata = _with_final_claims(metadata)
    filtered = _filter_evidence_images_to_target_section(images, metadata)

    assert [(image.section_title, image.source_chunk_id) for image in filtered] == [
        ("5.1 气缸活塞装配部件清单", "053f60433fa4:19:img:0000")
    ]


def test_section_overview_does_not_use_page_ocr_to_choose_inventory_image(monkeypatch) -> None:
    """Quantity answers do not authorize an image through shared page OCR."""

    class FakeVectorService:
        def get_page_records(self, document_id, page, chunk_type=None, limit=20):
            assert document_id == "manual-doc"
            assert chunk_type == "image"
            contexts = {
                23: "离合器、机油泵装配零件清单，φ8×14 空心定位销数量2，O型圈数量1",
                24: "离合器、机油泵装配零件清单，φ10×14 空心定位销数量3，O型圈数量3",
            }
            return [
                {
                    "id": f"inventory-p{page}",
                    "metadata": {
                        "chunk_type": "image",
                        "document_id": "manual-doc",
                        "page": page,
                        "image_url": f"http://example.test/p{page}.png",
                        "visual_context_text": contexts[page],
                    },
                }
            ]

    from services.knowledge import vector_service as vector_service_module

    monkeypatch.setattr(vector_service_module, "get_vector_service", lambda: FakeVectorService())
    title = "6.2 离合器、机油泵装配零件清单"
    images = [
        EvidenceImage(
            image_url="http://example.test/p23.png",
            caption=f"{title} 第23页插图",
            page=23,
            section_title=title,
            document_id="manual-doc",
            source_chunk_id="inventory-p23",
        ),
        EvidenceImage(
            image_url="http://example.test/p24.png",
            caption=f"{title} 第24页插图",
            page=24,
            section_title=title,
            document_id="manual-doc",
            source_chunk_id="inventory-p24",
        ),
    ]
    metadata = {
        "original_user_message": (
            "离合器、机油泵装配零件清单里φ10×14空心定位销和O型圈数量是多少？"
        ),
        "route_plan": {
            "query_contract": {
                "component": "φ10×14空心定位销,O型圈",
                "action": "",
                "orientation": "",
            }
        },
        "_deterministic_answer_evidence_pages": [23],
        "_deterministic_answer_section_title": title,
        "query_understanding_selection_mode": "section_overview",
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": [
                            {
                                "content": (
                                    "9.8×2.5 丙烯酸酯胶 O型圈 数量3；"
                                    "φ10×14 空心定位销 数量3"
                                ),
                                "metadata": {
                                    "chunk_type": "table",
                                    # A cross-page table row can retain its
                                    # first-page metadata after import.
                                    "page": 23,
                                    "page_range": "23-24",
                                },
                            }
                        ],
                    }
                ]
            }
        ],
    }

    selected = _select_evidence_images_for_response(images, metadata)

    assert selected == []


def test_image_context_prefers_page_local_visual_text_over_cross_page_record_content() -> None:
    class FakeVectorService:
        def get_page_records(self, document_id, page, chunk_type=None, limit=20):
            return [
                {
                    "id": "image-41",
                    "content": "跨页检索正文误带下一页的星门耦联簧",
                    "metadata": {
                        "chunk_type": "image",
                        "document_id": document_id,
                        "page": page,
                        "image_url": "http://example.test/p41.png",
                        "visual_context_text": "本页只展示苍穹装置总览",
                    },
                }
            ]

    image = EvidenceImage(
        image_url="http://example.test/p41.png",
        page=41,
        document_id="manual-doc",
        source_chunk_id="image-41",
    )

    context = api_main._image_context_for_action_filter(
        image,
        vector_service=FakeVectorService(),
    )

    assert "本页只展示苍穹装置总览" in context
    assert "星门耦联簧" not in context


def test_inventory_image_anchor_stops_at_first_requested_part_suffix() -> None:
    anchors = _image_specific_anchor_terms(
        "零件清单里φ10×14空心定位销和O型圈数量是多少？"
    )

    assert "φ10×14空心定位销" in anchors


def test_image_anchor_extraction_is_not_limited_to_known_component_terms() -> None:
    anchors = _image_specific_anchor_terms(
        "安装星门护罩时K口、M区和Q槽的防护剂分别有什么要求？"
    )

    assert {"k口", "m区", "q槽"}.issubset(set(anchors))


def test_complete_cross_page_inventory_returns_real_section_image_not_rendered_page() -> None:
    """完整跨页清单可返回章节真实图，但不自动授权整页截图。"""
    title = "5.1 某总成装配部件清单"
    images = [
        EvidenceImage(
            image_url="http://example.test/p17.png",
            caption=f"{title} 第17页插图",
            page=17,
            section_title=title,
            document_id="manual-doc",
            source_chunk_id="image-p17",
        ),
        EvidenceImage(
            image_url="http://example.test/rendered-p18.png",
            caption="第18页页面截图",
            page=18,
            section_title=title,
            document_id="manual-doc",
            source_chunk_id="rendered-page:manual-doc:18",
            context_role="page_render",
        ),
    ]
    metadata = {
        "original_user_message": "查询某总成装配部件清单",
        "deterministic_table_answer": True,
        "_deterministic_answer_table_complete": True,
        "_deterministic_answer_evidence_pages": [17, 18],
        "_deterministic_answer_section_title": title,
        "allowed_document_ids": ["manual-doc"],
        "query_understanding_selection_mode": "evidence_pages",
        "response_policy": {"images_allowed": True},
    }
    metadata = _with_final_claims(metadata)

    selected = _select_evidence_images_for_response(images, metadata)

    assert [image.source_chunk_id for image in selected] == ["image-p17"]


def test_direct_section_images_read_qualified_results_from_evidence_envelope(monkeypatch) -> None:
    class FakeVectorService:
        def get_section_records(self, document_id, section_id, limit=20, chunk_type=None):
            assert document_id == "manual-doc"
            assert section_id == "section-target"
            assert chunk_type == "image"
            return [{
                "id": "image-target",
                "metadata": {
                    "chunk_type": "image",
                    "document_id": document_id,
                    "parent_section_id": section_id,
                    "page": 18,
                    "image_url": "http://example.test/target.png",
                },
            }]

    from services.knowledge import vector_service as vector_service_module

    monkeypatch.setattr(vector_service_module, "get_vector_service", lambda: FakeVectorService())
    metadata = {
        "original_user_message": "查询某总成装配部件清单",
        "route_plan": {
            "action": "grounded_retrieval",
            "entity_role": "document_component",
            "selected_document_id": "manual-doc",
        },
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": {
                    "evidence_status": "qualified",
                    "results": [{
                        "id": "table-target",
                        "content": "某总成装配部件清单",
                        "metadata": {
                            "qualification": "qualified",
                            "retrieval_plan_intent": "outline",
                            "document_id": "manual-doc",
                            "parent_section_id": "section-target",
                            "section_match_ids": ["section-target"],
                            "chunk_type": "table",
                            "context_role": "primary",
                        },
                    }],
                    "reference_evidence": [],
                },
            }],
        }],
    }

    images = asyncio.run(api_main._collect_direct_section_images(metadata))

    assert [(image.page, image.source_chunk_id) for image in images] == [(18, "image-target")]


def test_direct_section_images_do_not_lookup_without_resolved_route(monkeypatch) -> None:
    class UnexpectedVectorService:
        def get_section_records(self, *args, **kwargs):
            raise AssertionError("未解析路由时不得执行章节图片补查")

    from services.knowledge import vector_service as vector_service_module

    monkeypatch.setattr(vector_service_module, "get_vector_service", lambda: UnexpectedVectorService())
    metadata = {
        "original_user_message": "查询某总成装配部件清单",
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": [{
                    "content": "某总成装配部件清单",
                    "metadata": {
                        "retrieval_plan_intent": "outline",
                        "document_id": "manual-doc",
                        "parent_section_id": "section-target",
                        "section_match_ids": ["section-target"],
                        "chunk_type": "table",
                    },
                }],
            }],
        }],
    }

    assert asyncio.run(api_main._collect_direct_section_images(metadata)) == []
