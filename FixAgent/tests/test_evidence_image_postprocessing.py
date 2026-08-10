"""Response-level image post-processing regressions."""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.main import (
    _align_evidence_images_to_text_evidence_pages,
    _collect_direct_evidence_page_images,
    _filter_evidence_images_by_action_context,
    _filter_evidence_images_to_target_section,
    _image_specific_anchor_terms,
    _narrow_evidence_images_to_query_target_pages,
    _select_evidence_images_for_response,
    _text_evidence_pages,
    _apply_final_image_contract,
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


def test_evidence_images_follow_text_evidence_pages_and_are_sorted() -> None:
    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": [
                            {
                                "content": "5.4 安装气缸与活塞 安装全新的箱体缸体垫片",
                                "metadata": {
                                    "chunk_type": "step_raw",
                                    "document_id": "manual-doc",
                                    "parent_section_id": "sec-cylinder-install",
                                    "section_match_ids": ["sec-cylinder-install"],
                                    "page": 19,
                                },
                            },
                            {
                                "content": "活塞与气缸均分为 A、B、C、D 四组",
                                "metadata": {
                                    "chunk_type": "text",
                                    "document_id": "manual-doc",
                                    "parent_section_id": "sec-cylinder-install",
                                    "section_match_ids": ["sec-cylinder-install"],
                                    "page": 20,
                                },
                            },
                            {
                                "content": "安装活塞销；安装活塞销挡圈",
                                "metadata": {
                                    "chunk_type": "step_raw",
                                    "document_id": "manual-doc",
                                    "parent_section_id": "sec-cylinder-install",
                                    "section_match_ids": ["sec-cylinder-install"],
                                    "page": 21,
                                },
                            },
                        ],
                    }
                ]
            }
        ]
    }

    images = [_img(20), _img(19), _img(18), _img(21)]

    aligned = _align_evidence_images_to_text_evidence_pages(images, metadata)

    assert [image.page for image in aligned] == [19, 20, 21]


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


def test_evidence_images_are_not_filtered_when_text_pages_are_absent() -> None:
    images = [_img(17), _img(16)]

    aligned = _align_evidence_images_to_text_evidence_pages(images, {"react_trace": []})

    assert [image.page for image in aligned] == [16, 17]


def test_evidence_image_alignment_keeps_all_deterministic_answer_pages() -> None:
    metadata = {
        "original_user_message": "安装右盖时曲轴油封和离合器拉杆要注意什么？",
        "_deterministic_answer_evidence_pages": [26, 27],
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": [
                    {
                        "content": "安装右盖：检查曲轴油封并安装离合器拉杆。",
                        "metadata": {"chunk_type": "step_raw", "page": 26},
                    },
                    {
                        "content": "A孔周围3mm内不得有密封胶；随后章节为拆卸离合器。",
                        "metadata": {"chunk_type": "text", "page": 27},
                    },
                ],
            }],
        }],
    }

    aligned = _align_evidence_images_to_text_evidence_pages(
        [_img(26, "6.4 右曲轴箱盖与离合器"), _img(27, "6.4 右曲轴箱盖与离合器")],
        metadata,
    )

    assert [image.page for image in aligned] == [26, 27]


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

    assert _text_evidence_pages(metadata) == [9]


def test_evidence_image_alignment_keeps_adjacent_query_matched_continuation_image() -> None:
    images = [
        _img(21, "5.6 安装活塞环"),
        EvidenceImage(
            image_url="http://example.test/p22-ring.png",
            caption="5.6 安装活塞环 第22页插图",
            page=22,
            section_title="5.6 安装活塞环",
            document_id="manual-doc",
            source_chunk_id="ring-p22",
        ),
        EvidenceImage(
            image_url="http://example.test/p22-cover.png",
            caption="6.1 右曲轴箱盖装配部件清单 第22页插图",
            page=22,
            section_title="6.1 右曲轴箱盖装配部件清单",
            document_id="manual-doc",
            source_chunk_id="cover-p22",
        ),
    ]
    metadata = {
        "original_user_message": "如何安装活塞环？",
        "_deterministic_answer_evidence_pages": [21],
    }

    aligned = _align_evidence_images_to_text_evidence_pages(images, metadata)

    assert [(image.page, image.source_chunk_id) for image in aligned] == [
        (21, ""),
        (22, "ring-p22"),
    ]


def test_evidence_image_alignment_does_not_keep_adjacent_page_for_inventory_query() -> None:
    images = [
        _img(23, "6.2 离合器、机油泵装配零件清单"),
        EvidenceImage(
            image_url="http://example.test/p24.png",
            caption="6.2 离合器、机油泵装配零件清单 第24页插图",
            page=24,
            section_title="6.2 离合器、机油泵装配零件清单",
            document_id="manual-doc",
            source_chunk_id="inventory-p24",
        ),
    ]
    metadata = {
        "original_user_message": "离合器、机油泵装配零件清单中摩擦片分组件和离合器从动片数量是多少？",
        "_deterministic_answer_evidence_pages": [23],
    }

    aligned = _align_evidence_images_to_text_evidence_pages(images, metadata)

    assert [(image.page, image.source_chunk_id) for image in aligned] == [(23, "")]


def test_collect_direct_evidence_page_images_uses_same_page_cross_section() -> None:
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

    images = _collect_direct_evidence_page_images(metadata, vector_service=FakeVectorService())

    assert [image.page for image in images] == [3]
    assert images[0].source_chunk_id == "image-from-neighbor-section"


def test_collect_direct_evidence_page_images_keeps_images_from_deterministic_pages_even_when_caption_is_substep() -> None:
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

    images = _collect_direct_evidence_page_images(metadata, vector_service=FakeVectorService())

    assert [image.page for image in images] == [21]
    assert images[0].source_chunk_id == "image-substep-page"


def test_collect_direct_evidence_page_images_renders_page_when_indexed_image_is_neighbor_section(
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

    assert [image.source_chunk_id for image in images] == ["rendered-page:manual-doc:21"]


def test_collect_direct_evidence_page_images_rebinds_misbound_image_from_page_local_visual_context() -> None:
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

    assert [image.source_chunk_id for image in images] == [
        "image-misbound-to-adjacent-section"
    ]


def test_collect_direct_evidence_page_images_renders_page_when_indexed_images_do_not_match_query(monkeypatch) -> None:
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

    images = _collect_direct_evidence_page_images(metadata, vector_service=FakeVectorService())

    assert [(image.page, image.source_chunk_id) for image in images] == [
        (21, "rendered-page:manual-doc:21")
    ]


def test_action_context_filter_keeps_neutral_image_when_page_is_text_evidence() -> None:
    class FakeVectorService:
        def get_page_records(self, document_id, page, chunk_type=None, limit=20):
            contexts = {
                19: "5.4 安装气缸与活塞 安装全新的箱体缸体垫片",
                20: "5.4 安装气缸与活塞 活塞与气缸组别 组装时必须使用相同组别",
                21: "活塞环开口位置与角度 安装活塞销 安装活塞销挡圈 拆卸活塞环",
            }
            return [
                {
                    "id": f"img-{page}",
                    "content": f"第{page}页插图",
                    "metadata": {
                        "chunk_type": "image",
                        "document_id": "manual-doc",
                        "page": page,
                        "image_url": f"http://example.test/p{page}.png",
                        "visual_context_text": contexts[page],
                    },
                }
            ]

    metadata = {
        "original_user_message": "如何安装气缸与活塞？",
        "_deterministic_answer_evidence_pages": [19, 20, 21],
    }
    images = [_img(19), _img(20), _img(21)]

    filtered = _filter_evidence_images_by_action_context(
        images,
        metadata,
        vector_service=FakeVectorService(),
    )

    assert [image.page for image in filtered] == [19, 20, 21]


def test_action_context_filter_keeps_negative_scored_image_when_page_is_text_evidence() -> None:
    class FakeVectorService:
        def get_page_records(self, document_id, page, chunk_type=None, limit=20):
            contexts = {
                26: "安装右盖 检查曲轴油封 安装离合器拉杆",
                27: (
                    "A孔周围3mm内不得有平面密封胶。"
                    "B段密封胶需要均匀抹薄、抹平。"
                    "D段范围内直接涂抹平面密封硅胶。"
                    "拆卸离合器。"
                ),
            }
            return [
                {
                    "id": f"img-{page}",
                    "content": f"第{page}页插图",
                    "metadata": {
                        "chunk_type": "image",
                        "document_id": "manual-doc",
                        "page": page,
                        "image_url": f"http://example.test/p{page}.png",
                        "visual_context_text": contexts[page],
                    },
                }
            ]

    metadata = {
        "original_user_message": "安装右盖时曲轴油封和离合器拉杆要注意什么？",
        "_deterministic_answer_evidence_pages": [26, 27],
    }
    images = [_img(26), _img(27)]

    filtered = _filter_evidence_images_by_action_context(
        images,
        metadata,
        vector_service=FakeVectorService(),
    )

    assert [image.page for image in filtered] == [26, 27]


def test_action_context_filter_prefers_install_page_over_adjacent_disassembly_page() -> None:
    class FakeVectorService:
        def get_page_records(self, document_id, page, chunk_type=None, limit=20):
            records = {
                16: [
                    {
                        "id": "img-disassembly",
                        "content": "4.8 气门 第16页插图",
                        "metadata": {
                            "chunk_type": "image",
                            "document_id": "manual-doc",
                            "page": 16,
                            "image_url": "http://example.test/p16.png",
                            "visual_context_text": "拆卸气门 取下滑动挺柱 使用气门拆装器压缩气门弹簧",
                        },
                    }
                ],
                17: [
                    {
                        "id": "img-install",
                        "content": "4.8 气门 第17页插图",
                        "metadata": {
                            "chunk_type": "image",
                            "document_id": "manual-doc",
                            "page": 17,
                            "image_url": "http://example.test/p17.png",
                            "visual_context_text": "安装气门 装上气门锁夹 安装气门间隙调整垫片和滑动挺柱",
                        },
                    }
                ],
            }
            return records.get(page, [])

    metadata = {"original_user_message": "如何安装气门？"}
    images = [_img(16), _img(17)]

    filtered = _filter_evidence_images_by_action_context(
        images,
        metadata,
        vector_service=FakeVectorService(),
    )

    assert [image.page for image in filtered] == [17]


def test_action_context_filter_drops_negative_evidence_page_when_strong_action_image_exists() -> None:
    class FakeVectorService:
        def get_page_records(self, document_id, page, chunk_type=None, limit=20):
            contexts = {
                16: (
                    "4.8 气门 拆卸气门 取下滑动挺柱 使用气门拆装器压缩气门弹簧 "
                    "依次拆下气门锁夹 气门弹簧上圈 气门外弹簧 气门内弹簧 "
                    "安装气门 依次安装气门 气门弹簧座 气门杆径油封"
                ),
                17: "4.8 气门 安装气门 装上气门锁夹 安装气门间隙调整垫片和滑动挺柱",
            }
            return [
                {
                    "id": f"img-{page}",
                    "content": contexts[page],
                    "metadata": {
                        "chunk_type": "image",
                        "document_id": "manual-doc",
                        "page": page,
                        "image_url": f"http://example.test/p{page}.png",
                        "visual_context_text": contexts[page],
                    },
                }
            ]

    metadata = {
        "original_user_message": "如何安装气门？",
        "_deterministic_answer_evidence_pages": [16, 17],
    }
    images = [
        _img(16, "4.8 气门"),
        _img(17, "4.8 气门"),
    ]

    filtered = _filter_evidence_images_by_action_context(
        images,
        metadata,
        vector_service=FakeVectorService(),
    )

    assert [image.page for image in filtered] == [17]


def test_action_context_filter_keeps_later_evidence_page_even_when_next_install_section_bleeds_in() -> None:
    class FakeVectorService:
        def get_page_records(self, document_id, page, chunk_type=None, limit=20):
            contexts = {
                11: "4.3 凸轮轴 拆卸凸轮轴 拆下气缸头盖 对角拧松座盖螺栓 取下凸轮轴座盖",
                12: (
                    "4.3 凸轮轴 先取下进气凸轮轴 再取下排气凸轮轴 "
                    "检查凸轮轴 安装凸轮轴 安装顺序 安装座盖 安装涨紧器"
                ),
            }
            return [
                {
                    "id": f"img-{page}",
                    "content": contexts[page],
                    "metadata": {
                        "chunk_type": "image",
                        "document_id": "manual-doc",
                        "page": page,
                        "image_url": f"http://example.test/p{page}.png",
                        "visual_context_text": contexts[page],
                    },
                }
            ]

    metadata = {
        "original_user_message": "拆卸凸轮轴时先取下进气还是排气凸轮轴？",
        "_deterministic_answer_evidence_pages": [11, 12],
    }
    images = [
        _img(11, "4.3 凸轮轴"),
        _img(12, "4.3 凸轮轴"),
    ]

    filtered = _filter_evidence_images_by_action_context(
        images,
        metadata,
        vector_service=FakeVectorService(),
    )

    assert [image.page for image in filtered] == [11, 12]


def test_collect_direct_evidence_page_images_renders_pdf_page_when_no_indexed_image(tmp_path, monkeypatch) -> None:
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

    assert [image.page for image in images] == [1]
    assert images[0].image_url.startswith("/files/rendered_pages/")
    assert images[0].source_chunk_id == "rendered-page:manual-doc:1"


def test_action_context_filter_drops_unrelated_inventory_image_on_text_evidence_page() -> None:
    class FakeVectorService:
        def get_page_records(self, document_id, page, chunk_type=None, limit=20):
            contexts = {
                35: "8.2 传动主副轴装配部件清单 序号 料件名称 数量 渐开线花键垫圈",
                36: "8.3 拆卸传动装置 松开箱体所有螺栓 依次取下换挡轴 拨叉轴 传动主轴 传动副轴",
            }
            return [
                {
                    "id": f"img-{page}",
                    "content": contexts[page],
                    "metadata": {
                        "chunk_type": "image",
                        "document_id": "manual-doc",
                        "page": page,
                        "image_url": f"http://example.test/p{page}.png",
                        "visual_context_text": contexts[page],
                    },
                }
            ]

    metadata = {
        "original_user_message": "如何拆卸传动装置？",
        "_deterministic_answer_evidence_pages": [35, 36],
    }
    images = [
        _img(35, "8.2 传动主副轴装配部件清单"),
        _img(36, "8.3 拆卸传动装置"),
    ]

    filtered = _filter_evidence_images_by_action_context(
        images,
        metadata,
        vector_service=FakeVectorService(),
    )

    assert [image.page for image in filtered] == [36]


def test_action_context_filter_falls_back_to_text_evidence_pages_not_all_images_when_context_is_mixed() -> None:
    class FakeVectorService:
        def get_page_records(self, document_id, page, chunk_type=None, limit=20):
            contexts = {
                16: (
                    "4.8 气门 拆卸气门 取下滑动挺柱 使用气门拆装器压缩气门弹簧 "
                    "安装气门 装上 装入 放入 合上 拧紧 套入 旋入 "
                    "气缸活塞装配部件清单 序号 零件名称 数量"
                ),
                17: "安装气门 装上气门锁夹 安装气门间隙调整垫片和滑动挺柱",
            }
            return [
                {
                    "id": f"img-{page}",
                    "content": contexts[page],
                    "metadata": {
                        "chunk_type": "image",
                        "document_id": "manual-doc",
                        "page": page,
                        "image_url": f"http://example.test/p{page}.png",
                        "visual_context_text": contexts[page],
                    },
                }
            ]

    metadata = {
        "original_user_message": "如何拆卸气门？",
        "_deterministic_answer_evidence_pages": [16],
    }
    images = [
        _img(16, "4.8 气门"),
        EvidenceImage(
            image_url="http://example.test/p17.png",
            caption="第17页插图",
            page=17,
            section_title="4.8 气门",
            document_id="manual-doc",
            source_chunk_id="img-17",
            context_role="direct_lookup",
        ),
    ]

    filtered = _filter_evidence_images_by_action_context(
        images,
        metadata,
        vector_service=FakeVectorService(),
    )

    assert [image.page for image in filtered] == [16]


def test_action_context_filter_keeps_evidence_page_with_procedure_context_even_when_inventory_text_bleeds_in() -> None:
    class FakeVectorService:
        def get_page_records(self, document_id, page, chunk_type=None, limit=20):
            contexts = {
                21: (
                    "5.6 安装活塞环 活塞环开口位置与角度 任意两环开口之间应错开120° "
                    "安装活塞销 安装活塞销挡圈 拆卸 取下 松开 断开 拉出 取出 "
                    "序号 零件名称 数量"
                ),
                22: "5.6 安装活塞环 装入活塞一环环槽内 R标记面朝活塞顶部",
            }
            return [
                {
                    "id": f"img-{page}",
                    "content": contexts[page],
                    "metadata": {
                        "chunk_type": "image",
                        "document_id": "manual-doc",
                        "page": page,
                        "image_url": f"http://example.test/p{page}.png",
                        "visual_context_text": contexts[page],
                    },
                }
            ]

    metadata = {
        "original_user_message": "如何安装活塞环？",
        "_deterministic_answer_evidence_pages": [21],
    }
    images = [
        EvidenceImage(
            image_url="http://example.test/p21.png",
            caption="第21页插图",
            page=21,
            section_title="5.6 安装活塞环",
            document_id="manual-doc",
            source_chunk_id="img-21",
            context_role="page_lookup",
        ),
        EvidenceImage(
            image_url="http://example.test/p22.png",
            caption="第22页插图",
            page=22,
            section_title="5.6 安装活塞环",
            document_id="manual-doc",
            source_chunk_id="img-22",
            context_role="direct_lookup",
        ),
    ]

    filtered = _filter_evidence_images_by_action_context(
        images,
        metadata,
        vector_service=FakeVectorService(),
    )

    assert [image.page for image in filtered] == [21, 22]


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

    filtered = _filter_evidence_images_to_target_section(images, metadata)

    assert [(image.section_title, image.source_chunk_id) for image in filtered] == [
        ("5.1 气缸活塞装配部件清单", "053f60433fa4:19:img:0000")
    ]


def test_section_overview_uses_visual_context_to_choose_cross_page_inventory_image(monkeypatch) -> None:
    """A stale text-evidence page must not hide the image that covers all requested parts."""

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

    assert [image.page for image in selected] == [24]


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


def test_complete_cross_page_inventory_keeps_rendered_missing_page() -> None:
    """完整跨页清单缺少内嵌图时，页面截图仍属于必要证据图。"""
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

    selected = _select_evidence_images_for_response(images, metadata)

    assert [image.page for image in selected] == [17, 18]


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
