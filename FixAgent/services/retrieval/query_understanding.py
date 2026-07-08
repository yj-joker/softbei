"""Lightweight query understanding for retrieval post-processing.

This module is intentionally heuristic and side-effect free. It does not
rewrite the query used for embedding or keyword search; callers can use the
metadata to make conservative post-retrieval decisions.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Dict


@dataclass(frozen=True)
class QueryUnderstanding:
    canonical_query: str
    target_query: str
    intent: str
    image_mode: str
    confidence: float

    def to_metadata(self) -> Dict[str, object]:
        return asdict(self)


_IMAGE_HINTS = (
    "图片",
    "插图",
    "图示",
    "示意图",
    "结构图",
    "位置图",
    "图纸",
    "配图",
)
_NEGATIVE_IMAGE_HINTS = (
    "不需要图片",
    "不要图片",
    "无需图片",
    "不用图片",
    "别返回图片",
    "不要返回图片",
    "不要返回任何图片",
    "不需要图",
    "不要图",
    "无需图",
    "不用图",
    "不需要配图",
    "不要配图",
    "无需配图",
    "不用配图",
    "别配图",
)
_SINGLE_IMAGE_HINTS = (
    "是哪张",
    "哪张",
    "是哪一页",
    "哪一页",
    "哪页",
    "第几页",
    "有没有图片",
    "有没有图",
    "有无图片",
    "有无图",
    "是否有图片",
    "是否有图",
)
_MULTI_IMAGE_HINTS = (
    "有哪些",
    "哪些",
    "在哪里",
    "有几张",
)
_TARGET_NOISE_PHRASES = (
    "给我展示",
    "帮我展示",
    "给我看看",
    "让我看看",
    "帮我看看",
    "请展示",
    "查询",
    "查找",
    "查一下",
    "看看",
    "显示",
    "展示",
    "对应的图片是哪张",
    "对应图片是哪张",
    "图片是哪张",
    "图示是哪张",
    "插图是哪张",
    "插图是哪一页",
    "图示是哪一页",
    "图片是哪一页",
    "是哪一页",
    "哪一页",
    "哪页",
    "第几页",
    "是哪张",
    "有没有图片",
    "有没有图",
    "有无图片",
    "有无图",
    "是否有图片",
    "是否有图",
    "相关图示在哪里",
    "相关图片在哪里",
    "图示有哪些",
    "图片有哪些",
    "插图有哪些",
    "示意图有哪些",
    "相关图示",
    "相关图片",
    "相关插图",
    "章节",
    "图片",
    "图示",
    "插图",
    "示意图",
    "配图",
    "相关",
    "对应",
    "的",
    "吗",
    "？",
    "?",
)


def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def has_negative_image_request(query: str) -> bool:
    """Return True when the user explicitly asks not to return images."""
    normalized = _normalize_spaces(query)
    compact = re.sub(r"\s+", "", normalized)
    return any(hint in normalized or hint in compact for hint in _NEGATIVE_IMAGE_HINTS)


def _strip_target_noise(query: str) -> str:
    target = _normalize_spaces(query)
    for phrase in (*_TARGET_NOISE_PHRASES, *_NEGATIVE_IMAGE_HINTS, "只告诉我", "只回答", "只列出"):
        target = target.replace(phrase, " ")
    target = re.sub(r"\s+", "", target)
    target = target.strip(" ，,。；;：:")
    return target or _normalize_spaces(query)


def understand_query(query: str) -> QueryUnderstanding:
    """Return conservative metadata about the user's retrieval question."""
    normalized = _normalize_spaces(query)
    if has_negative_image_request(normalized):
        target = _strip_target_noise(normalized)
        return QueryUnderstanding(
            canonical_query=target or normalized,
            target_query=target,
            intent="general",
            image_mode="none",
            confidence=0.9 if target else 0.7,
        )

    has_image_signal = any(hint in normalized for hint in _IMAGE_HINTS)
    has_single_signal = any(hint in normalized for hint in _SINGLE_IMAGE_HINTS)
    has_multi_signal = any(hint in normalized for hint in _MULTI_IMAGE_HINTS)

    if not has_image_signal:
        target = _strip_target_noise(normalized)
        return QueryUnderstanding(
            canonical_query=normalized,
            target_query=target,
            intent="general",
            image_mode="none",
            confidence=0.4 if target else 0.0,
        )

    target = _strip_target_noise(normalized)
    if has_single_signal:
        image_mode = "single_best"
        confidence = 0.9 if target else 0.6
    elif has_multi_signal:
        image_mode = "same_section"
        confidence = 0.78 if target else 0.55
    else:
        image_mode = "same_section"
        confidence = 0.65 if target else 0.45

    return QueryUnderstanding(
        canonical_query=f"{target} 图片" if target else normalized,
        target_query=target,
        intent="image_lookup",
        image_mode=image_mode,
        confidence=confidence,
    )
