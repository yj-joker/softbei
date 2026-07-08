"""Section title matching regression tests.

These tests intentionally avoid Redis/vector-service setup. They exercise the
in-memory matching behavior that decides whether a natural language query can
be narrowed to a deterministic manual section.
"""

from __future__ import annotations

import os
import sys

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


if __name__ == "__main__":
    test_natural_query_hits_embedded_exact_section_title()
    test_action_query_hits_embedded_action_section_title()
    test_object_first_action_query_hits_action_section_title()
    test_action_alias_query_hits_action_section_title()
    test_rewritten_query_hits_best_ordered_title_match()
    test_partial_title_query_hits_best_ordered_title_match()
    test_ordered_title_match_requires_tail_character_to_avoid_entity_overgeneralization()
    print("test_section_title_index.py OK")
