"""Query understanding regressions for image grounding."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from services.retrieval.query_understanding import (
    is_deictic_image_followup,
    understand_query,
)


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
    assert understanding.selection_mode == "none"
    assert "图片" not in understanding.canonical_query
    assert "不需要图片" not in understanding.target_query


def test_which_page_image_request_is_single_best() -> None:
    understanding = understand_query("发动机拆卸前排放机油的插图是哪一页")

    assert understanding.intent == "image_lookup"
    assert understanding.image_mode == "single_best"
    assert understanding.confidence >= 0.8


def test_explicit_single_step_image_request_uses_single_target_contract() -> None:
    understanding = understand_query("拆卸凸轮轴前对齐正时标记，只要这一步对应的图")

    assert understanding.intent == "image_lookup"
    assert understanding.selection_mode == "single_target"


def test_corresponding_image_request_uses_single_target_contract() -> None:
    understanding = understand_query("如何检查活塞裙部？请返回对应图片。")

    assert understanding.intent == "image_lookup"
    assert understanding.image_mode == "single_best"
    assert understanding.selection_mode == "single_target"


def test_complete_procedure_image_request_uses_evidence_pages_contract() -> None:
    understanding = understand_query("如何安装气缸与活塞？给我完整步骤的相关图片")

    assert understanding.intent == "image_lookup"
    assert understanding.selection_mode == "evidence_pages"


@pytest.mark.parametrize(
    "query",
    ["图片呢", "图呢", "对应图片呢", "步骤中的图片呢", "有对应的示意图吗"],
)
def test_deictic_image_followup_requires_prior_target(query: str) -> None:
    assert is_deictic_image_followup(query) is True


@pytest.mark.parametrize(
    "query",
    [
        "活塞环的图片呢",
        "另一份手册的图片呢",
        "第5页的图片呢",
        "不要图片",
        "如何安装起动电机",
    ],
)
def test_targeted_or_negative_queries_are_not_deictic_image_followups(query: str) -> None:
    assert is_deictic_image_followup(query) is False
