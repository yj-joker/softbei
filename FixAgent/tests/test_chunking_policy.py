"""Procedure child-position regressions for the manual chunking policy."""

from services.knowledge.chunking_policy import build_section_index_chunks


def test_numbered_steps_receive_incrementing_child_index_within_source_chunk() -> None:
    chunks = build_section_index_chunks(
        {
            "section_title": "4.4 涨紧器",
            "text_chunks": [{
                "text": "1. 预压涨紧器。\n2. 安装本体。\n3. 释放自锁。",
                "page": 13,
                "chunk_label": "step",
            }],
            "tables": [],
            "images": [],
        },
        section_index=4,
    )

    steps = [chunk for chunk in chunks if chunk["metadata"].get("answer_role") == "procedure_step"]

    assert [chunk["metadata"]["child_index"] for chunk in steps] == [0, 1, 2]
    assert len({chunk["metadata"]["parent_chunk_id"] for chunk in steps}) == 1


def test_procedure_chunks_persist_subflow_identity_from_toc_path() -> None:
    chunks = build_section_index_chunks(
        {
            "section_title": "6.4 右曲轴箱盖与离合器",
            "text_chunks": [{
                "text": "1. 检查离合器摩擦片。\n2. 依次装入离合器部件。",
                "page": 27,
                "chunk_label": "step",
                "toc_path": (
                    "摩托车发动机维修手册 > 六、右曲轴箱盖、离合器、机油泵、水泵 "
                    "> 6.4 右曲轴箱盖与离合器 > 安装离合器"
                ),
            }],
            "tables": [],
            "images": [],
        },
        section_index=28,
    )

    steps = [chunk for chunk in chunks if chunk["metadata"].get("answer_role") == "procedure_step"]

    assert {chunk["metadata"]["procedure_action"] for chunk in steps} == {"安装"}
    assert {chunk["metadata"]["procedure_target"] for chunk in steps} == {"离合器"}
    assert len({chunk["metadata"]["procedure_scope_id"] for chunk in steps}) == 1
    assert all(chunk["metadata"]["procedure_heading"] == "安装离合器" for chunk in steps)


def test_continued_table_preserves_structured_row_source_pages_across_three_pages() -> None:
    chunks = build_section_index_chunks(
        {
            "section_title": "5.1 气缸活塞装配部件清单",
            "text_chunks": [],
            "tables": [
                {
                    "page": 17,
                    "caption": "第17页表格",
                    "rows": [
                        ["序号", "零件名称", "数量", "备注"],
                        ["1", "气缸体分部件", "1", ""],
                    ],
                },
                {
                    "page": 18,
                    "caption": "第18页表格",
                    "rows": [
                        ["序号", "零件名称", "数量", "备注"],
                        ["2", "箱体缸体垫片", "1", ""],
                    ],
                },
                {
                    "page": 19,
                    "caption": "第19页表格",
                    "rows": [
                        ["序号", "零件名称", "数量", "备注"],
                        ["3", "活塞销挡圈", "2", "挡圈必须完全装配到槽内"],
                    ],
                },
            ],
            "images": [],
        },
        section_index=5,
    )

    tables = [chunk for chunk in chunks if chunk["chunk_label"] == "table_full"]
    rows = [chunk for chunk in chunks if chunk["chunk_label"] == "table_row"]

    assert len(tables) == 1
    table_metadata = tables[0]["metadata"]
    assert table_metadata["page_span"] == [17, 18, 19]
    assert table_metadata["caption"] == "第17-19页表格"
    assert table_metadata["table_full"]["headers"] == ["序号", "零件名称", "数量", "备注"]
    assert [row["source_page"] for row in table_metadata["table_full"]["rows"]] == [17, 18, 19]
    assert [row["fields"]["零件名称"] for row in table_metadata["table_full"]["rows"]] == [
        "气缸体分部件",
        "箱体缸体垫片",
        "活塞销挡圈",
    ]
    assert [chunk["page"] for chunk in rows] == [17, 18, 19]
    assert all("table_full" not in chunk["metadata"] for chunk in rows)


def test_images_without_coordinates_do_not_receive_page_level_step_bindings() -> None:
    chunks = build_section_index_chunks(
        {
            "section_title": "5.4 安装气缸与活塞",
            "text_chunks": [
                {"text": "1. 安装全新的箱体缸体垫片。", "page": 19, "chunk_label": "step"},
                {"text": "2. 活塞与气缸必须使用相同组别。", "page": 20, "chunk_label": "step"},
            ],
            "tables": [],
            "images": [
                {"image_name": "page_019.png", "page": 19, "caption": "安装垫片图"},
                {"image_name": "page_020.png", "page": 20, "caption": "组别图"},
            ],
        },
        section_index=5,
    )

    steps_by_page = {
        chunk["page"]: chunk["id"]
        for chunk in chunks
        if chunk["chunk_label"] == "step"
    }
    images = {
        chunk["page"]: chunk
        for chunk in chunks
        if chunk["chunk_label"] == "image"
    }

    assert images[19]["metadata"]["related_step_chunk_ids"] == []
    assert images[20]["metadata"]["related_step_chunk_ids"] == []
    assert images[19]["metadata"]["binding_confidence"] == 0.0
    assert images[20]["metadata"]["binding_confidence"] == 0.0
    assert "活塞与气缸必须使用相同组别" not in images[19]["metadata"]["visual_context_text"]
    assert "安装全新的箱体缸体垫片" not in images[20]["metadata"]["visual_context_text"]
    assert images[19]["metadata"]["related_text_chunk_ids"] == []
    assert images[20]["metadata"]["related_text_chunk_ids"] == []


def test_images_without_coordinates_do_not_inherit_page_procedure_scopes() -> None:
    chunks = build_section_index_chunks(
        {
            "section_title": "6.4 右曲轴箱盖与离合器",
            "text_chunks": [
                {
                    "text": "1. 检查曲轴油封。",
                    "page": 26,
                    "chunk_label": "step",
                    "toc_path": "手册 > 6.4 右曲轴箱盖与离合器 > 安装右盖",
                },
                {
                    "text": "1. 检查离合器摩擦片。",
                    "page": 27,
                    "chunk_label": "step",
                    "toc_path": "手册 > 6.4 右曲轴箱盖与离合器 > 安装离合器",
                },
            ],
            "tables": [],
            "images": [
                {"image_name": "page_026.png", "page": 26},
                {"image_name": "page_027.png", "page": 27},
            ],
        },
        section_index=28,
    )

    scopes_by_page = {
        chunk["page"]: chunk["metadata"]["procedure_scope_id"]
        for chunk in chunks
        if chunk["metadata"].get("answer_role") == "procedure_step"
    }
    images = {
        chunk["page"]: chunk
        for chunk in chunks
        if chunk["chunk_label"] == "image"
    }

    assert images[26]["metadata"]["procedure_scope_ids"] == []
    assert images[27]["metadata"]["procedure_scope_ids"] == []


def test_same_page_images_bind_to_their_nearest_positioned_step_only() -> None:
    chunks = build_section_index_chunks(
        {
            "section_title": "5.3 Cylinder and piston inspection",
            "text_chunks": [
                {
                    "text": "1. Inspect the cylinder wall for scoring.",
                    "page": 18,
                    "chunk_label": "step",
                    "bbox": [40, 120, 330, 150],
                },
                {
                    "text": "2. Inspect the piston skirt for wear.",
                    "page": 18,
                    "chunk_label": "step",
                    "bbox": [40, 420, 330, 450],
                },
            ],
            "tables": [],
            "images": [
                {
                    "image_name": "cylinder-wall.png",
                    "page": 18,
                    "caption": "Cylinder wall inspection figure",
                    "bbox": [40, 180, 330, 330],
                    "caption_bbox": [40, 340, 330, 365],
                    "caption_confidence": 0.9,
                },
                {
                    "image_name": "piston-skirt.png",
                    "page": 18,
                    "caption": "Piston skirt inspection figure",
                    "bbox": [40, 480, 330, 630],
                    "caption_bbox": [40, 640, 330, 665],
                    "caption_confidence": 0.9,
                },
            ],
        },
        section_index=5,
    )

    steps = [chunk for chunk in chunks if chunk["chunk_label"] == "step"]
    images = [chunk for chunk in chunks if chunk["chunk_label"] == "image"]

    assert images[0]["metadata"]["related_step_chunk_ids"] == [steps[0]["id"]]
    assert images[1]["metadata"]["related_step_chunk_ids"] == [steps[1]["id"]]
    assert images[0]["metadata"]["related_text_chunk_ids"] == [steps[0]["id"]]
    assert images[1]["metadata"]["related_text_chunk_ids"] == [steps[1]["id"]]
    assert images[0]["metadata"]["binding_role"] == "positioned_step"
    assert images[1]["metadata"]["binding_role"] == "positioned_step"
    assert images[0]["metadata"]["bbox"] == [40.0, 180.0, 330.0, 330.0]
    assert images[0]["metadata"]["caption_confidence"] == 0.9


def test_positioned_general_text_does_not_inherit_all_same_page_steps() -> None:
    chunks = build_section_index_chunks(
        {
            "section_title": "Inspection notes",
            "text_chunks": [
                {
                    "text": "1. Remove the cylinder head.",
                    "page": 18,
                    "chunk_label": "step",
                    "bbox": [40, 80, 330, 110],
                    "toc_path": "Manual > Remove cylinder head",
                },
                {
                    "text": "Cylinder wall wear reference diagram.",
                    "page": 18,
                    "chunk_label": "general",
                    "bbox": [40, 300, 330, 330],
                },
            ],
            "tables": [],
            "images": [
                {
                    "image_name": "wear-reference.png",
                    "page": 18,
                    "caption": "Cylinder wall wear reference",
                    "bbox": [40, 350, 330, 520],
                }
            ],
        },
        section_index=5,
    )

    general = next(chunk for chunk in chunks if chunk["chunk_label"] == "general")
    image = next(chunk for chunk in chunks if chunk["chunk_label"] == "image")

    assert image["metadata"]["related_text_chunk_ids"] == [general["id"]]
    assert image["metadata"]["related_step_chunk_ids"] == []
    assert image["metadata"]["procedure_scope_ids"] == []
    assert image["metadata"]["binding_role"] == "positioned_text"


def test_positioned_image_prefers_text_in_the_same_column() -> None:
    chunks = build_section_index_chunks(
        {
            "section_title": "双栏检查步骤",
            "text_chunks": [
                {
                    "text": "1. 检查左栏部件。",
                    "page": 18,
                    "chunk_label": "step",
                    "bbox": [0, 100, 200, 150],
                },
                {
                    "text": "2. 检查右栏气缸内壁。",
                    "page": 18,
                    "chunk_label": "step",
                    "bbox": [300, 100, 500, 150],
                },
            ],
            "tables": [],
            "images": [
                {
                    "image_name": "right-column.png",
                    "page": 18,
                    "caption": "气缸内壁检查图",
                    "bbox": [300, 160, 500, 300],
                }
            ],
        },
        section_index=5,
    )

    steps = [chunk for chunk in chunks if chunk["chunk_label"] == "step"]
    image = next(chunk for chunk in chunks if chunk["chunk_label"] == "image")

    assert image["metadata"]["related_step_chunk_ids"] == [steps[1]["id"]]
