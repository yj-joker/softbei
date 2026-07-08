"""Response-level image post-processing regressions."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.main import (
    _align_evidence_images_to_text_evidence_pages,
    _collect_direct_evidence_page_images,
    _filter_evidence_images_by_action_context,
    _filter_evidence_images_to_target_section,
    _narrow_evidence_images_to_query_target_pages,
    _text_evidence_pages,
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


def test_evidence_images_are_not_filtered_when_text_pages_are_absent() -> None:
    images = [_img(17), _img(16)]

    aligned = _align_evidence_images_to_text_evidence_pages(images, {"react_trace": []})

    assert [image.page for image in aligned] == [16, 17]


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
    fitz = __import__("fitz")
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
