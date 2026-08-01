"""Section title matching regression tests.

These tests intentionally avoid Redis/vector-service setup. They exercise the
in-memory matching behavior that decides whether a natural language query can
be narrowed to a deterministic manual section.
"""

from __future__ import annotations

import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.retrieval.section_index import SectionRef, SectionTitleIndex


def _index_with_titles(*titles: tuple[str, str, str]) -> SectionTitleIndex:
    index = SectionTitleIndex()
    index._built = True
    for section_id, core_title, full_title in titles:
        ref = SectionRef(
            section_id=section_id,
            document_id="doc",
            core_title=core_title,
            full_title=full_title,
        )
        index._exact.setdefault(core_title, []).append(ref)
    return index


def test_natural_query_hits_embedded_exact_section_title() -> None:
    index = _index_with_titles(
        ("sec:0037", "传动装置装配部件清单", "8.1 传动装置装配部件清单"),
        ("sec:0038", "传动主副轴装配部件清单", "8.2 传动主副轴装配部件清单"),
        ("sec:0042", "曲轴、平衡轴装配部件清单", "9.1 曲轴、平衡轴装配部件清单"),
    )

    hits = index.find("给我展示传动主副轴装配部件清单")

    assert [hit.section_id for hit in hits] == ["sec:0038"]


def test_action_query_hits_embedded_action_section_title() -> None:
    index = _index_with_titles(
        ("sec:0023", "拆卸活塞环", "5.5 拆卸活塞环"),
        ("sec:0024", "安装活塞环", "5.6 安装活塞环"),
    )

    hits = index.find("如何安装活塞环")

    assert [hit.section_id for hit in hits] == ["sec:0024"]


def test_object_first_action_query_hits_action_section_title() -> None:
    index = _index_with_titles(
        ("sec:0023", "拆卸活塞环", "5.5 拆卸活塞环"),
        ("sec:0024", "安装活塞环", "5.6 安装活塞环"),
    )

    hits = index.find("活塞环安装步骤")

    assert [hit.section_id for hit in hits] == ["sec:0024"]


def test_action_alias_query_hits_action_section_title() -> None:
    index = _index_with_titles(
        ("sec:0023", "拆卸活塞环", "5.5 拆卸活塞环"),
        ("sec:0024", "安装活塞环", "5.6 安装活塞环"),
    )

    assert [hit.section_id for hit in index.find("活塞环装配步骤")] == ["sec:0024"]
    assert [hit.section_id for hit in index.find("活塞环怎么装")] == ["sec:0024"]


def test_rewritten_query_hits_best_ordered_title_match() -> None:
    index = _index_with_titles(
        ("sec:0037", "传动装置装配部件清单", "8.1 传动装置装配部件清单"),
        ("sec:0038", "传动主副轴装配部件清单", "8.2 传动主副轴装配部件清单"),
        ("sec:0042", "曲轴、平衡轴装配部件清单", "9.1 曲轴、平衡轴装配部件清单"),
    )

    hits = index.find("传动主轴与副轴相关装配部件清单")

    assert [hit.section_id for hit in hits] == ["sec:0038"]


def test_partial_title_query_hits_best_ordered_title_match() -> None:
    index = _index_with_titles(
        ("sec:0037", "传动装置装配部件清单", "8.1 传动装置装配部件清单"),
        ("sec:0038", "传动主副轴装配部件清单", "8.2 传动主副轴装配部件清单"),
        ("sec:0042", "曲轴、平衡轴装配部件清单", "9.1 曲轴、平衡轴装配部件清单"),
    )

    hits = index.find("传动主副轴装配部件")

    assert [hit.section_id for hit in hits] == ["sec:0038"]


def test_ordered_title_match_requires_tail_character_to_avoid_entity_overgeneralization() -> None:
    index = _index_with_titles(
        ("sec:0024", "安装活塞环", "5.6 安装活塞环"),
    )

    hits = index.find("安装活塞销挡圈时开口位置有什么要求")

    assert hits == []


def test_explicit_direction_entity_prioritizes_matching_sections() -> None:
    index = _index_with_titles(
        ("sec-right-procedure", "右曲轴箱盖与离合器", "6.4 右曲轴箱盖与离合器"),
        ("sec-right-parts", "右曲轴箱盖装配部件清单", "6.1 右曲轴箱盖装配部件清单"),
        ("sec-left", "左曲轴箱盖", "7.4 左曲轴箱盖"),
    )

    hits = index.find("如何安装右曲轴箱盖")

    assert {hit.section_id for hit in hits} == {"sec-right-procedure", "sec-right-parts"}


def test_build_scans_every_redis_page_before_matching_titles() -> None:
    def row(record_id: str, section_id: str, title: str) -> list[object]:
        metadata = json.dumps({
            "parent_section_id": section_id,
            "section_title": title,
        }).encode()
        return [
            f"doc:{record_id}".encode(),
            [
                b"metadata", metadata,
                b"document_id", b"manual-doc",
                b"id", record_id.encode(),
            ],
        ]

    class _Redis:
        def __init__(self) -> None:
            self.offsets: list[int] = []

        def execute_command(self, *args):
            offset = int(args[args.index("LIMIT") + 1])
            self.offsets.append(offset)
            if offset == 0:
                return [3, *row("install", "sec-install", "8.5 安装传动装置"), *row("check", "sec-check", "8.4 检查传动装置")]
            if offset == 2:
                return [3, *row("remove", "sec-remove", "8.3 拆卸传动装置")]
            return [3]

    class _VectorService:
        INDEX_NAME = "knowledge_vectors_v2"

        def __init__(self) -> None:
            self.redis = _Redis()

    vector_service = _VectorService()
    index = SectionTitleIndex()

    index.build(vector_service)

    assert vector_service.redis.offsets == [0, 2]
    assert [hit.section_id for hit in index.find("传动装置拆卸按什么顺序")] == ["sec-remove"]


if __name__ == "__main__":
    test_natural_query_hits_embedded_exact_section_title()
    test_action_query_hits_embedded_action_section_title()
    test_object_first_action_query_hits_action_section_title()
    test_action_alias_query_hits_action_section_title()
    test_rewritten_query_hits_best_ordered_title_match()
    test_partial_title_query_hits_best_ordered_title_match()
    test_ordered_title_match_requires_tail_character_to_avoid_entity_overgeneralization()
    print("test_section_title_index.py OK")
