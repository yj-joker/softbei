"""Lightweight causal follow-up questions for diagnostic uncertainty.

This module intentionally keeps the first version small: it does not try to
build a full causal graph engine. Instead, it recognizes high-value demo
scenarios, compares a few candidate root causes, asks one discriminating
question, and reranks candidates when the user answers.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Mapping


FOLLOW_UP_TOOL_NAME = "causal_follow_up"
MIN_CANDIDATES = 2
MAX_OPTIONS = 4


_BLUE_SMOKE_SCENARIO = {
    "id": "engine_blue_smoke_timing",
    "match_terms": ("冒蓝烟", "蓝烟", "烧机油"),
    "question": "蓝烟主要在什么时候更明显？",
    "reason": "气门油封老化和活塞环磨损都会导致冒蓝烟、烧机油，但蓝烟出现时机是区分根因的关键现场特征。",
    "hypotheses": [
        {
            "id": "valve_seal_aging",
            "faultPart": "气门油封",
            "rootCause": "气门油封老化",
            "confidence": 0.62,
            "distinguishingFeature": "冷启动或长时间怠速后蓝烟更明显",
            "suggestedCheck": "检查气门油封密封状态，观察冷启动后短时间排烟变化。",
        },
        {
            "id": "piston_ring_wear",
            "faultPart": "活塞环",
            "rootCause": "活塞环磨损",
            "confidence": 0.59,
            "distinguishingFeature": "加速、负载或高转速时蓝烟更明显",
            "suggestedCheck": "做气缸压力或漏气量检查，确认活塞环与缸壁密封情况。",
        },
        {
            "id": "crankcase_vent_abnormal",
            "faultPart": "曲轴箱通风系统",
            "rootCause": "曲轴箱通风异常",
            "confidence": 0.47,
            "distinguishingFeature": "伴随怠速不稳、机油消耗异常或通风管路堵塞",
            "suggestedCheck": "检查曲轴箱通风管路、单向阀和油气分离状态。",
        },
    ],
    "options": [
        {
            "id": "A",
            "text": "冷启动或怠速后更明显",
            "boost": {"valve_seal_aging": 0.23, "piston_ring_wear": -0.07},
            "interpretation": "冷启动或怠速后蓝烟明显，更支持气门油封老化。",
        },
        {
            "id": "B",
            "text": "加速或负载时更明显",
            "boost": {"piston_ring_wear": 0.24, "valve_seal_aging": -0.08},
            "interpretation": "加速或负载时蓝烟明显，更支持活塞环磨损。",
        },
        {
            "id": "C",
            "text": "一直都有，并伴随怠速不稳",
            "boost": {"crankcase_vent_abnormal": 0.20},
            "interpretation": "持续冒蓝烟且怠速不稳，需要重点排查曲轴箱通风异常，同时保留机械磨损可能。",
        },
        {
            "id": "D",
            "text": "不确定",
            "boost": {},
            "interpretation": "现场现象仍不充分，建议先按低成本检查顺序验证。",
        },
    ],
}


SCENARIOS = (_BLUE_SMOKE_SCENARIO,)


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _round_score(value: float) -> float:
    return round(max(0.0, min(0.99, value)), 2)


def _scenario_matches(scenario: Mapping[str, Any], query: str) -> bool:
    compact_query = _compact(query)
    terms = [_compact(term) for term in scenario.get("match_terms") or []]
    return any(term and term in compact_query for term in terms)


def _public_hypotheses(hypotheses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for item in hypotheses:
        copied = dict(item)
        copied["confidence"] = _round_score(float(copied.get("confidence") or 0.0))
        result.append(copied)
    return sorted(result, key=lambda item: item.get("confidence", 0), reverse=True)


def _public_options(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": option.get("id"),
            "label": f"{option.get('id')}. {option.get('text')}",
            "text": option.get("text"),
        }
        for option in options[:MAX_OPTIONS]
    ]


def _option_by_answer(options: list[dict[str, Any]], answer_text: str, selected_id: Any = None) -> dict[str, Any] | None:
    selected = _compact(selected_id)
    answer = _compact(answer_text)
    for option in options:
        option_id = _compact(option.get("id"))
        option_text = _compact(option.get("text"))
        if selected and selected == option_id:
            return option
        if answer and (answer.startswith(option_id) or option_text in answer):
            return option
    return None


def build_follow_up(
    query: str,
    diagnosis_items: list[dict[str, Any]] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return one discriminating question when the diagnostic query is ambiguous."""
    del diagnosis_items, metadata
    for scenario in SCENARIOS:
        if not _scenario_matches(scenario, query):
            continue
        hypotheses = _public_hypotheses(copy.deepcopy(scenario["hypotheses"]))
        if len(hypotheses) < MIN_CANDIDATES:
            return None
        top_gap = hypotheses[0]["confidence"] - hypotheses[1]["confidence"]
        if top_gap > 0.18:
            return None
        return {
            "id": scenario["id"],
            "status": "awaiting_answer",
            "question": scenario["question"],
            "reason": scenario["reason"],
            "hypotheses": hypotheses,
            "options": _public_options(copy.deepcopy(scenario["options"])),
            "originalQuery": query,
        }
    return None


def resolve_follow_up(context: Mapping[str, Any] | None, answer_text: str) -> dict[str, Any] | None:
    """Rerank hypotheses using the user's answer to a pending follow-up."""
    if not isinstance(context, Mapping):
        return None
    pending = context.get("diagnostic_follow_up")
    if not isinstance(pending, Mapping) or pending.get("status") != "awaiting_answer":
        return None

    scenario = next((item for item in SCENARIOS if item["id"] == pending.get("id")), None)
    if scenario is None:
        return None

    option = _option_by_answer(
        copy.deepcopy(scenario["options"]),
        answer_text,
        selected_id=context.get("selected_option_id"),
    )
    if option is None:
        return None

    hypotheses = copy.deepcopy(pending.get("hypotheses") or scenario["hypotheses"])
    boosts = option.get("boost") or {}
    for item in hypotheses:
        item["confidence"] = _round_score(float(item.get("confidence") or 0.0) + float(boosts.get(item.get("id"), 0.0)))

    ranked = _public_hypotheses(hypotheses)
    top = ranked[0]
    second = ranked[1] if len(ranked) > 1 else None
    diagnosis_items = [
        {
            "priority": "一级",
            "faultPart": top.get("faultPart", ""),
            "rootCause": top.get("rootCause", ""),
            "knowledgeBasis": option.get("interpretation", "") + " 建议结合现场检查确认。",
        }
    ]
    if second:
        diagnosis_items.append(
            {
                "priority": "二级",
                "faultPart": second.get("faultPart", ""),
                "rootCause": second.get("rootCause", ""),
                "knowledgeBasis": f"作为备选根因保留，当前置信度 {second.get('confidence'):.2f}。",
            }
        )

    return {
        "id": pending.get("id"),
        "status": "resolved",
        "question": pending.get("question", scenario["question"]),
        "selectedOption": {
            "id": option.get("id"),
            "label": f"{option.get('id')}. {option.get('text')}",
            "text": option.get("text"),
        },
        "interpretation": option.get("interpretation", ""),
        "hypotheses": ranked,
        "diagnosisItems": diagnosis_items,
        "originalQuery": pending.get("originalQuery", ""),
    }


def format_follow_up_message(follow_up: Mapping[str, Any]) -> str:
    lines = [
        "当前有多个候选根因接近，我先不直接下最终结论。",
        "",
        "候选根因：",
    ]
    for idx, item in enumerate(follow_up.get("hypotheses") or [], start=1):
        lines.append(
            f"{idx}. {item.get('rootCause')}（置信度 {float(item.get('confidence') or 0):.2f}）："
            f"{item.get('distinguishingFeature')}"
        )
    lines.extend(["", f"为了缩小范围，请补充：{follow_up.get('question')}"])
    for option in follow_up.get("options") or []:
        lines.append(option.get("label") or "")
    return "\n".join(line for line in lines if line is not None)


def format_resolution_message(resolved: Mapping[str, Any]) -> str:
    hypotheses = list(resolved.get("hypotheses") or [])
    top = hypotheses[0] if hypotheses else {}
    lines = [
        f"根据你补充的现场现象：{(resolved.get('selectedOption') or {}).get('label', '')}",
        resolved.get("interpretation", ""),
        "",
        "重评分结果：",
    ]
    for idx, item in enumerate(hypotheses[:3], start=1):
        lines.append(f"{idx}. {item.get('rootCause')}：{float(item.get('confidence') or 0):.2f}")
    if top:
        lines.extend(
            [
                "",
                f"当前更可能的根因是：{top.get('rootCause')}。",
                f"建议下一步：{top.get('suggestedCheck')}",
            ]
        )
    return "\n".join(line for line in lines if line)
