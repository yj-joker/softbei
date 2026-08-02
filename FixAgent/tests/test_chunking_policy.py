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


def test_image_chunks_bind_only_steps_from_the_same_source_page() -> None:
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

    assert images[19]["metadata"]["related_step_chunk_ids"] == [steps_by_page[19]]
    assert images[20]["metadata"]["related_step_chunk_ids"] == [steps_by_page[20]]
    assert images[19]["metadata"]["binding_confidence"] == 1.0
    assert images[20]["metadata"]["binding_confidence"] == 1.0
    assert "活塞与气缸必须使用相同组别" not in images[19]["metadata"]["visual_context_text"]
    assert "安装全新的箱体缸体垫片" not in images[20]["metadata"]["visual_context_text"]
    assert images[19]["metadata"]["related_text_chunk_ids"] == [steps_by_page[19]]
    assert images[20]["metadata"]["related_text_chunk_ids"] == [steps_by_page[20]]


def test_image_chunks_carry_same_page_procedure_scope_ids() -> None:
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

    assert images[26]["metadata"]["procedure_scope_ids"] == [scopes_by_page[26]]
    assert images[27]["metadata"]["procedure_scope_ids"] == [scopes_by_page[27]]
