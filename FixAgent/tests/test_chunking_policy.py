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
