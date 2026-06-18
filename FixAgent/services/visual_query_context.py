from __future__ import annotations

import re
from typing import Dict, List, Optional


COMPONENT_TERMS = [
    "火花塞", "点火线圈", "喷油嘴", "燃油泵", "气门", "轴承", "皮带", "线束", "接头",
    "传感器", "滤芯", "离合器", "刹车片", "电瓶", "水泵", "油泵",
]

FAULT_TERMS = [
    "烧蚀", "积碳", "裂纹", "漏油", "漏水", "异响", "过热", "启动不了", "无法启动",
    "打不着", "冒烟", "抖动", "磨损", "断裂", "松动", "腐蚀", "变形", "报警",
]

DEVICE_TERMS = [
    "摩托车发动机", "发动机", "电动机", "柴油机", "泵", "压缩机", "减速机", "发电机",
    "点火系统", "燃油系统", "冷却系统", "制动系统",
]


def build_visual_query_context(
    user_message: str,
    enhanced_query: Optional[str],
    images: Optional[List[str]],
) -> Dict[str, object]:
    has_images = bool(images)
    if not has_images:
        return {
            "has_images": False,
            "visible_parts": [],
            "fault_signs": [],
            "device_clues": [],
            "uncertainties": [],
            "retrieval_hint": "",
        }

    text = _normalize_text(" ".join(part for part in [user_message or "", enhanced_query or ""] if part))
    visible_parts = _find_terms(text, COMPONENT_TERMS)
    fault_signs = _find_terms(text, FAULT_TERMS)
    device_clues = _find_terms(text, DEVICE_TERMS)

    retrieval_terms = _dedupe(visible_parts + fault_signs + device_clues + _keyword_tokens(enhanced_query or ""))
    return {
        "has_images": True,
        "visible_parts": visible_parts,
        "fault_signs": fault_signs,
        "device_clues": device_clues,
        "uncertainties": ["图片未经过独立视觉结构化模型确认"],
        "retrieval_hint": " ".join(retrieval_terms),
    }


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _find_terms(text: str, terms: List[str]) -> List[str]:
    matched = [term for term in terms if term and term in text]
    matched.sort(key=lambda term: text.find(term))
    return _dedupe(matched)


def _keyword_tokens(text: str) -> List[str]:
    return [
        token
        for token in re.split(r"[\s,，。；;、]+", text or "")
        if len(token.strip()) >= 2
    ]


def _dedupe(items: List[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        value = str(item or "").strip()
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result
