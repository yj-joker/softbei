"""Local structural ranking for maintenance RAG candidates."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Iterable, List, Sequence

from services.retrieval.planner import RetrievalPlan


NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
LATIN_RE = re.compile(r"[a-zA-Z]+[a-zA-Z0-9_.+-]*")
CHINESE_RE = re.compile(r"[\u4e00-\u9fff]+")
UNIT_RE = re.compile(
    r"(?<![a-z])(?:"
    r"n\s*[·.\-]?\s*m|kgf\s*[·.\-]?\s*m|mpa|kpa|pa|"
    r"mm|cm|ml|l|kg|kw|w|v|a|"
    r"r\s*/\s*min|rpm|m\s*/\s*s|%|℃|°c"
    r")(?![a-z])",
    re.IGNORECASE,
)

QUESTION_STOP_TERMS = {
    "多少",
    "什么",
    "怎么",
    "如何",
    "哪里",
    "哪些",
    "是否",
    "有没有",
    "需要",
    "应该",
    "可以",
    "不能",
    "为什么",
    "是多少",
}

GENERIC_METADATA_FIELDS = (
    "section_title",
    "chapter_title",
    "chapter_path",
    "caption",
    "image_title",
    "image_summary",
    "part_name",
    "parameter_names",
    "units",
)


def _metadata(candidate: Dict[str, Any]) -> Dict[str, Any]:
    return candidate.get("metadata") or {}


def _chunk_type(candidate: Dict[str, Any]) -> str:
    value = str(_metadata(candidate).get("chunk_type", "text") or "text")
    return "image" if value == "image_summary" else value


def _chunk_label(candidate: Dict[str, Any]) -> str:
    return str(_metadata(candidate).get("chunk_label", "") or "")


def _metadata_values(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item)]
    return [str(value)] if str(value) else []


def _normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    text = text.replace("n路m", "n·m").replace("n璺痬", "n·m")
    text = text.replace("n.m", "n·m").replace("n-m", "n·m")
    return text


def _candidate_text(
    candidate: Dict[str, Any],
    fields: Sequence[str] = GENERIC_METADATA_FIELDS,
    *,
    include_body: bool = True,
) -> str:
    metadata = _metadata(candidate)
    values: List[str] = []
    if include_body:
        values.extend(
            [
                str(candidate.get("text") or ""),
                str(candidate.get("content") or ""),
            ]
        )
    for field in fields:
        values.extend(_metadata_values(metadata.get(field)))
    return _normalize_text(" ".join(value for value in values if value))


def _unique(items: Iterable[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _chinese_ngrams(text: str) -> List[str]:
    grams: List[str] = []
    for segment in CHINESE_RE.findall(text):
        length = len(segment)
        if 2 <= length <= 8 and segment not in QUESTION_STOP_TERMS:
            grams.append(segment)
        for size in (2, 3, 4):
            if length < size:
                continue
            for index in range(0, length - size + 1):
                gram = segment[index : index + size]
                if gram not in QUESTION_STOP_TERMS:
                    grams.append(gram)
    return grams


def _query_terms(query: str) -> List[str]:
    text = _normalize_text(query)
    terms: List[str] = []
    terms.extend(_chinese_ngrams(text))
    terms.extend(token for token in LATIN_RE.findall(text) if token not in QUESTION_STOP_TERMS)
    terms.extend(NUMBER_RE.findall(text))
    terms.extend(_extract_units(text))
    return _unique(terms)


def _extract_units(text: str) -> List[str]:
    normalized = _normalize_text(text)
    units = []
    for unit in UNIT_RE.findall(normalized):
        units.append(re.sub(r"\s+", "", unit.lower()).replace(".", "·").replace("-", "·"))
    return _unique(units)


def _extract_numbers(text: str) -> List[str]:
    return _unique(NUMBER_RE.findall(_normalize_text(text)))


def _coverage(terms: Sequence[str], text: str) -> float:
    if not terms:
        return 0.0
    matched = sum(1 for term in terms if term and term in text)
    return matched / len(terms)


def _overlap_count(left: Sequence[str], right: Sequence[str]) -> int:
    return len(set(left).intersection(right))


def _base_score(candidate: Dict[str, Any]) -> float:
    metadata = _metadata(candidate)
    if metadata.get("rrf_enabled") and int(metadata.get("rrf_route_count") or 0) <= 1:
        value = metadata.get("pre_rrf_relevance_score")
        try:
            return max(0.0, min(1.0, float(value or 0.0)))
        except (TypeError, ValueError):
            pass

    value = candidate.get("relevance_score")
    if value is None:
        value = candidate.get("score", 0.0)
    try:
        return max(0.0, min(1.0, float(value or 0.0)))
    except (TypeError, ValueError):
        return 0.0


def _route_consensus_bonus(candidate: Dict[str, Any], plan: RetrievalPlan) -> float:
    routes = set(candidate.get("routes") or [])
    if not routes and candidate.get("retrieval_route"):
        routes.add(str(candidate["retrieval_route"]))

    route_count_bonus = min(0.09, max(0, len(routes) - 1) * 0.035)
    planned_route_bonus = min(0.08, sum(0.02 for route in routes if route in plan.routes))
    return route_count_bonus + planned_route_bonus


def _type_alignment_bonus(
    candidate: Dict[str, Any],
    plan: RetrievalPlan,
    *,
    query_coverage: float,
    number_overlap: int,
    unit_overlap: int,
    title_hit_ratio: float = 0.0,
) -> float:
    chunk_type = _chunk_type(candidate)
    chunk_label = _chunk_label(candidate)
    bonus = 0.0

    if plan.intent == "parameter":
        has_structural_match = query_coverage >= 0.18 or number_overlap > 0 or unit_overlap > 0
        if chunk_type == "table" and has_structural_match:
            bonus += 0.045
        if chunk_label == "table_row" and has_structural_match:
            bonus += 0.035
        if chunk_type == "text" and query_coverage >= 0.30:
            bonus += 0.04
        if chunk_type == "table" and query_coverage < 0.10 and not number_overlap and not unit_overlap:
            bonus -= 0.07
    elif plan.intent == "image_identification":
        if chunk_type == "image":
            bonus += 0.18
        if chunk_label in {"image", "image_summary"}:
            bonus += 0.08
        # 章节对齐：图片类提问常指明章节，正确章节的图(标题命中查询)优先，缓解相邻章节挤占
        if chunk_type == "image":
            if title_hit_ratio >= 0.6:
                bonus += 0.14
            elif title_hit_ratio >= 0.35:
                bonus += 0.07
        # 图片意图下文本块降权，避免被 contextual 增强的 step/general 文本压过图片证据
        if chunk_type == "text":
            if query_coverage < 0.25:
                bonus -= 0.08
            elif title_hit_ratio < 0.6:
                bonus -= 0.03
    elif plan.intent == "procedure":
        if chunk_label == "step":
            bonus += 0.075
        if chunk_type == "text" and query_coverage >= 0.18:
            bonus += 0.035
        if chunk_type == "table" and query_coverage < 0.20:
            bonus -= 0.06
    elif plan.intent == "diagnosis":
        if chunk_label in {"troubleshooting", "inspection", "step"}:
            bonus += 0.06
        if chunk_type == "table" and query_coverage < 0.20:
            bonus -= 0.05

    if chunk_label == "safety" and query_coverage >= 0.18:
        bonus += 0.045
    return bonus


def _parameter_metadata_bonus(query: str, plan: RetrievalPlan, candidate: Dict[str, Any]) -> float:
    if plan.intent != "parameter":
        return 0.0

    metadata = _metadata(candidate)
    query_text = _normalize_text(query)
    names: List[str] = []
    for field_name in ("part_name", "parameter_names"):
        names.extend(_metadata_values(metadata.get(field_name)))

    for name in names:
        normalized = _normalize_text(name)
        if len(normalized) >= 2 and normalized in query_text:
            return 0.08
    return 0.0


def _completeness_adjustment(candidate: Dict[str, Any], plan: RetrievalPlan, content: str) -> float:
    metadata = _metadata(candidate)
    chunk_type = _chunk_type(candidate)
    chunk_label = _chunk_label(candidate)
    text_length = len(re.sub(r"\s+", "", content))
    adjustment = 0.0

    if chunk_type == "outline" and plan.intent != "outline":
        adjustment -= 0.22
    if metadata.get("answer_role") == "navigation" and plan.intent != "outline":
        adjustment -= 0.16
    rrf_route_count = int(metadata.get("rrf_route_count") or 0)
    if text_length < 18:
        adjustment -= 0.01 if rrf_route_count > 1 else 0.045
    elif 40 <= text_length <= 900:
        adjustment += 0.025
    if chunk_label in {"table_full", "outline"} and plan.intent != "outline":
        adjustment -= 0.025
    if any(metadata.get(field) for field in ("prev_chunk_id", "next_chunk_id", "parent_table_chunk_id", "source_image_id")):
        adjustment += 0.015
    return adjustment


def _structural_features(query: str, candidate: Dict[str, Any]) -> Dict[str, Any]:
    query_text = _normalize_text(query)
    content = _candidate_text(candidate)
    title_text = _candidate_text(
        candidate,
        fields=("section_title", "chapter_title", "chapter_path"),
        include_body=False,
    )

    terms = _query_terms(query_text)
    query_units = _extract_units(query_text)
    query_numbers = _extract_numbers(query_text)
    content_units = _extract_units(content)
    content_numbers = _extract_numbers(content)

    query_coverage = _coverage(terms, content)
    title_coverage = _coverage(terms, title_text)
    # 反向覆盖：标题里的词有多少出现在查询中。标题短、不被查询长度稀释，
    # 对“查询指明章节”的图片检索区分度更高（正确章节标题命中率明显高于相邻章节）。
    title_terms = _query_terms(title_text)
    title_hit_ratio = _coverage(title_terms, query_text)
    unit_overlap = _overlap_count(query_units, content_units)
    number_overlap = _overlap_count(query_numbers, content_numbers)

    return {
        "query_terms": terms,
        "query_coverage": round(query_coverage, 6),
        "title_coverage": round(title_coverage, 6),
        "title_hit_ratio": round(title_hit_ratio, 6),
        "unit_overlap": unit_overlap,
        "number_overlap": number_overlap,
        "content_length": len(re.sub(r"\s+", "", content)),
    }


def _structural_score(query: str, candidate: Dict[str, Any], plan: RetrievalPlan) -> tuple[float, Dict[str, Any]]:
    content = _candidate_text(candidate)
    features = _structural_features(query, candidate)
    query_coverage = float(features["query_coverage"])
    title_coverage = float(features["title_coverage"])
    title_hit_ratio = float(features["title_hit_ratio"])
    unit_overlap = int(features["unit_overlap"])
    number_overlap = int(features["number_overlap"])

    metadata = _metadata(candidate)
    route_count = int(metadata.get("rrf_route_count") or 0)
    base_weight = 0.75 if metadata.get("rrf_enabled") and route_count > 1 else 0.62
    score = _base_score(candidate) * base_weight
    score += min(0.30, query_coverage * 0.36)
    score += min(0.14, title_coverage * 0.20)
    score += min(0.10, unit_overlap * 0.05 + number_overlap * 0.05)
    score += _route_consensus_bonus(candidate, plan)
    score += _type_alignment_bonus(
        candidate,
        plan,
        query_coverage=query_coverage,
        number_overlap=number_overlap,
        unit_overlap=unit_overlap,
        title_hit_ratio=title_hit_ratio,
    )
    score += _parameter_metadata_bonus(query, plan, candidate)
    score += _completeness_adjustment(candidate, plan, content)

    if any("relaxed" in str(route) for route in candidate.get("routes") or []):
        score -= 0.06

    return max(0.0, min(1.0, score)), features


def rank_candidates(query: str, candidates: Iterable[Dict[str, Any]], plan: RetrievalPlan) -> List[Dict[str, Any]]:
    """Rank candidates with local, model-free structural signals."""
    ranked: List[tuple[float, Dict[str, Any]]] = []
    for candidate in candidates:
        score, features = _structural_score(query, candidate, plan)
        item = dict(candidate)
        metadata = dict(candidate.get("metadata") or {})
        metadata["retrieval_plan_intent"] = plan.intent
        metadata["local_rerank_features"] = {
            key: value for key, value in features.items() if key != "query_terms"
        }
        item["metadata"] = metadata
        item["rerank_score"] = round(score, 6)
        ranked.append((score, item))

    return [item for _, item in sorted(ranked, key=lambda pair: pair[0], reverse=True)]
