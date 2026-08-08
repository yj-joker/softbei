"""Evidence-bound causal follow-up questions for diagnostic uncertainty."""

from __future__ import annotations

import math
import re
from typing import Any, Mapping

from services.pending_clarification import build_diagnostic_clarification


FOLLOW_UP_TOOL_NAME = "causal_follow_up"
MIN_CANDIDATES = 2
MAX_OPTIONS = 4


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _round_score(value: float) -> float:
    return round(max(0.0, min(0.99, value)), 2)


def _requests_possible_causes(query: str) -> bool:
    compact_query = _compact(query)
    return bool(re.search(r"可能(?:的)?(?:故障)?原因", compact_query))


def _public_hypotheses(hypotheses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for item in hypotheses:
        copied = dict(item)
        copied["confidence"] = _round_score(float(copied.get("confidence") or 0.0))
        result.append(copied)
    return sorted(result, key=lambda item: item.get("confidence", 0), reverse=True)


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
    """Compatibility wrapper around the evidence-bound implementation."""
    return build_evidence_follow_up(query, metadata, diagnosis_items=diagnosis_items)


def build_evidence_follow_up(
    query: str,
    metadata: Mapping[str, Any] | None,
    *,
    diagnosis_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """仅根据本轮检索/图谱返回的结构化候选生成诊断反问。

    该入口不读取固定场景、设备或部件词表；候选不足时直接返回 None。
    """
    if _requests_possible_causes(query):
        return None
    source = metadata if isinstance(metadata, Mapping) else {}
    raw_candidates = source.get("diagnostic_candidates") or source.get("cause_candidates") or ()
    candidates = [dict(item) for item in raw_candidates if isinstance(item, Mapping)]
    candidates.extend(
        dict(item) for item in (diagnosis_items or ()) if isinstance(item, Mapping)
    )
    candidates.extend(_graph_candidates_from_trace(source))
    candidates = _normalize_candidates(candidates)
    if len(candidates) < MIN_CANDIDATES:
        return None
    candidates = candidates[:MAX_OPTIONS]
    discriminator = _best_discriminator(candidates)
    if discriminator is None:
        return None
    dimension, question, groups = discriminator
    alternatives = []
    for index, (label, grouped) in enumerate(groups):
        option_id = chr(ord("A") + index)
        alternatives.append({
            "id": option_id,
            "label": f"{option_id}. {label}",
            "text": label,
            "constraints": {
                "diagnostic_candidate_ids": [item["id"] for item in grouped],
                "diagnostic_dimension": dimension,
                "diagnostic_value": label,
            },
        })
    hypotheses = _public_hypotheses(candidates)
    clarification = build_diagnostic_clarification(
        scenario_id="evidence",
        query=query,
        subject="故障原因",
        question=question,
        alternatives=alternatives,
    )
    return {
        **clarification,
        "id": "evidence",
        "status": "awaiting_answer",
        "question": question,
        "question_dimension": dimension,
        "hypotheses": hypotheses,
        "options": alternatives,
        "originalQuery": query,
    }


def _graph_candidates_from_trace(metadata: Mapping[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for step in metadata.get("react_trace") or ():
        if not isinstance(step, Mapping):
            continue
        for call in step.get("tool_calls") or ():
            if not isinstance(call, Mapping) or call.get("name") != "java_graph_diagnosis_path":
                continue
            result = call.get("result_data") or call.get("data") or call.get("result") or {}
            if isinstance(result, Mapping) and isinstance(result.get("data"), Mapping):
                result = result["data"]
            records = result.get("raw_records") if isinstance(result, Mapping) else ()
            for record in records or ():
                if not isinstance(record, Mapping):
                    continue
                score = record.get("faultScore") or record.get("componentScore")
                if score is None:
                    match_score = float(record.get("matchScore") or 0.0)
                    score = min(0.95, 0.50 + 0.10 * match_score)
                solutions = [
                    item for item in record.get("solutions") or () if isinstance(item, Mapping)
                ]
                candidates.append({
                    "id": str(record.get("faultId") or record.get("id") or ""),
                    "faultPart": str(record.get("componentName") or ""),
                    "rootCause": str(record.get("faultName") or ""),
                    "confidence": score,
                    "suggestedCheck": str(
                        record.get("suggestedCheck")
                        or record.get("distinguishingFeature")
                        or (solutions[0].get("title") if solutions else "")
                        or ""
                    ),
                    "distinguishingFeature": str(record.get("distinguishingFeature") or ""),
                })
    return candidates


def _normalize_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, candidate in enumerate(candidates, start=1):
        root_cause = str(candidate.get("rootCause") or candidate.get("root_cause") or "").strip()
        fault_part = str(candidate.get("faultPart") or candidate.get("fault_part") or "").strip()
        if not root_cause:
            continue
        key = (root_cause.casefold(), fault_part.casefold())
        if key in seen:
            continue
        seen.add(key)
        try:
            confidence = float(candidate.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        normalized.append({
            "id": str(candidate.get("id") or f"candidate-{index}"),
            "faultPart": fault_part,
            "rootCause": root_cause,
            "confidence": _round_score(confidence),
            "distinguishingFeature": str(
                candidate.get("distinguishingFeature")
                or candidate.get("distinguishing_feature")
                or ""
            ).strip(),
            "suggestedCheck": str(
                candidate.get("suggestedCheck")
                or candidate.get("suggested_check")
                or ""
            ).strip(),
        })
    return normalized


def _best_discriminator(
    candidates: list[dict[str, Any]],
) -> tuple[str, str, list[tuple[str, list[dict[str, Any]]]]] | None:
    definitions = (
        ("distinguishingFeature", "请补充最符合现场情况的现象，或直接选择选项："),
        ("faultPart", "异常更接近下列哪个部位？"),
        ("suggestedCheck", "下列哪项检查结果或处置线索更符合现场情况？"),
    )
    scored = []
    total = len(candidates)
    base_entropy = math.log2(total) if total > 1 else 0.0
    for tie_rank, (dimension, question) in enumerate(definitions):
        grouped: dict[str, list[dict[str, Any]]] = {}
        for candidate in candidates:
            value = str(candidate.get(dimension) or "").strip()
            if value:
                grouped.setdefault(value, []).append(candidate)
        covered = sum(len(items) for items in grouped.values())
        if len(grouped) < 2 or covered != total:
            continue
        residual = sum(
            (len(items) / total) * math.log2(len(items))
            for items in grouped.values()
            if len(items) > 1
        )
        information_gain = 1.0 if base_entropy == 0 else (base_entropy - residual) / base_entropy
        scored.append((information_gain, -tie_rank, dimension, question, list(grouped.items())))
    if not scored:
        return None
    _, _, dimension, question, groups = max(scored, key=lambda item: (item[0], item[1]))
    return dimension, question, groups[:MAX_OPTIONS]


def resolve_follow_up(context: Mapping[str, Any] | None, answer_text: str) -> dict[str, Any] | None:
    """Rerank hypotheses using the user's answer to a pending follow-up."""
    if not isinstance(context, Mapping):
        return None
    pending = context.get("diagnostic_follow_up")
    if not isinstance(pending, Mapping):
        common_pending = context.get("pending_clarification")
        if isinstance(common_pending, Mapping) and common_pending.get("kind") == "diagnostic_cause":
            pending = common_pending
    if not isinstance(pending, Mapping) or pending.get("status") != "awaiting_answer":
        return None

    if str(pending.get("id") or "") != "evidence":
        return None
    options = [
        dict(item)
        for item in pending.get("options") or pending.get("alternatives") or ()
        if isinstance(item, Mapping)
    ]
    option = _option_by_answer(
        options,
        answer_text,
        selected_id=context.get("selected_option_id") or context.get("selected_clarification_option_id"),
    )
    if option is None:
        return None
    hypotheses = [
        dict(item) for item in pending.get("hypotheses") or () if isinstance(item, Mapping)
    ]
    constraints = option.get("constraints") if isinstance(option.get("constraints"), Mapping) else {}
    selected_ids = {
        str(value) for value in constraints.get("diagnostic_candidate_ids") or () if str(value)
    }
    selected = [item for item in hypotheses if str(item.get("id") or "") in selected_ids]
    remaining = [item for item in hypotheses if item not in selected]
    ranked = selected + remaining
    top = ranked[0] if ranked else {}
    return {
        "id": "evidence",
        "status": "resolved",
        "question": pending.get("question", ""),
        "selectedOption": dict(option),
        "interpretation": str(option.get("text") or option.get("label") or ""),
        "hypotheses": ranked,
        "diagnosisItems": ([{
            "priority": "一级",
            "faultPart": top.get("faultPart", ""),
            "rootCause": top.get("rootCause", ""),
            "knowledgeBasis": str(option.get("text") or option.get("label") or ""),
        }] if top else []),
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
