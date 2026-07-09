"""Query understanding regressions for image grounding."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.retrieval.query_understanding import understand_query


def test_chapter_has_image_is_high_confidence_single_best() -> None:
    understanding = understand_query("气门间隙章节有没有图片")

    assert understanding.target_query == "气门间隙"
    assert understanding.intent == "image_lookup"
    assert understanding.image_mode == "single_best"
    assert understanding.confidence >= 0.8


def test_parts_list_which_image_is_high_confidence_single_best() -> None:
    understanding = understand_query("左曲轴箱盖、磁电机转子离合器装配部件清单图片是哪张")

    assert understanding.target_query == "左曲轴箱盖、磁电机转子离合器装配部件清单"
    assert understanding.intent == "image_lookup"
    assert understanding.image_mode == "single_best"
    assert understanding.confidence >= 0.8


def test_installation_diagrams_are_same_section_not_single_best() -> None:
    understanding = understand_query("安装气缸头盖的图示有哪些")

    assert understanding.target_query == "安装气缸头盖"
    assert understanding.intent == "image_lookup"
    assert understanding.image_mode == "same_section"
    assert understanding.confidence >= 0.7


def test_explicit_negative_image_request_disables_image_lookup() -> None:
    understanding = understand_query("只告诉我火花塞安装拧紧力矩是多少，不需要图片")

    assert understanding.intent == "general"
    assert understanding.image_mode == "none"
    assert "图片" not in understanding.canonical_query
    assert "不需要图片" not in understanding.target_query


def test_which_page_image_request_is_single_best() -> None:
    understanding = understand_query("发动机拆卸前排放机油的插图是哪一页")

    assert understanding.intent == "image_lookup"
    assert understanding.image_mode == "single_best"
    assert understanding.confidence >= 0.8
