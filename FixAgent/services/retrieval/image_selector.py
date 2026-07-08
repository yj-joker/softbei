"""Page-level image selection helpers.

The selector is deliberately lexical and conservative.  It is meant to run
after the normal RAG retrieval step and only choose image pages; it must not
rewrite or rerank text evidence.
"""

from __future__ import annotations

import re
import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Sequence


@dataclass(frozen=True)
class PageEvidence:
    page: int
    text: str = ""
    image_text: str = ""
    group_key: str = ""
    images: List[Dict] = field(default_factory=list)


@dataclass(frozen=True)
class PageScore:
    page: int
    score: float
    group_key: str = ""


@dataclass(frozen=True)
class GatedPageSelection:
    selected_pages: List[int]
    free_selected_pages: List[int]
    gate_triggered: bool
    reason: str
    scores: List[PageScore]


_NOISE = (
    "给我",
    "帮我",
    "让我",
    "看看",
    "展示",
    "查询",
    "查找",
    "一下",
    "图片",
    "图示",
    "插图",
    "示意图",
    "是哪张",
    "有哪些",
    "哪张",
    "哪些",
    "相关",
    "对应",
    "的",
    "和",
    "与",
    "及",
    "中",
    "吗",
)
_ACTION_SYNONYMS = {
    "拆卸": ("拆卸", "拆下", "取下", "松开", "断开", "拉出", "取出"),
    "安装": ("安装", "装上", "装入", "放入", "合上", "拧紧", "套入", "旋入"),
    "检查": ("检查", "测量", "拨动", "转动", "校验"),
}
_OPPOSITE_ACTIONS = {
    "拆卸": ("安装", "装上", "装入", "放入", "合上", "拧紧", "套入", "旋入"),
    "安装": ("拆卸", "拆下", "取下", "松开", "断开", "拉出", "取出", "检查", "测量", "拨动", "转动", "校验"),
    "检查": ("安装", "装上", "装入", "放入", "合上", "拧紧", "套入", "旋入", "拆卸", "拆下", "取下", "松开", "断开", "拉出", "取出"),
}
_INVENTORY_TERMS = ("装配部件清单", "装配零件清单", "部件清单", "零件清单", "装配清单")
_VISUAL_ATTRIBUTE_TERMS = (
    "调整垫片",
    "密端",
    "密距端",
    "朝下",
    "朝上",
    "错开",
    "开口",
    "缺口",
    "槽缺口",
    "标记",
    "方向",
    "径向",
    "轴向",
    "IN",
)
_MANDATORY_VISUAL_TERMS = ("朝下", "朝上", "错开", "开口", "槽缺口")
_LOW_SCORE_NEIGHBOR_PROCEDURE_TERMS = (
    "安装", "拆卸", "拆装", "检查", "测量", "更换", "插入", "确认", "涂抹", "排放", "取下", "装上", "装入"
)


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _query_target(query: str) -> str:
    target = _compact(query)
    for noise in _NOISE:
        target = target.replace(noise, "")
    return target


def _char_ngrams(text: str, min_n: int = 2, max_n: int = 5) -> List[str]:
    compact = _query_target(text)
    grams: List[str] = []
    for n in range(max_n, min_n - 1, -1):
        for idx in range(0, max(0, len(compact) - n + 1)):
            gram = compact[idx : idx + n]
            if gram and gram not in grams:
                grams.append(gram)
    return grams


def _query_terms(query: str) -> List[str]:
    target = _query_target(query)
    for connector in ("以及", "并且", "和", "与", "及", "或", "、", "，", ",", "；", ";"):
        target = target.replace(connector, " ")
    terms: List[str] = []
    for segment in target.split():
        if len(segment) >= 2 and segment not in terms:
            terms.append(segment)
        for gram in _char_ngrams(segment, min_n=2, max_n=8):
            if gram not in terms:
                terms.append(gram)
    if not terms:
        terms.extend(_char_ngrams(query, min_n=2, max_n=8))
    return terms


def _query_actions(query: str) -> List[str]:
    return [action for action in _ACTION_SYNONYMS if action in query]


def _orientation_penalty(query: str, text: str) -> float:
    if "右曲轴箱盖" in query and "左曲轴箱盖" in text and "右曲轴箱盖" not in text:
        return -8.0
    if "左曲轴箱盖" in query and "右曲轴箱盖" in text and "左曲轴箱盖" not in text:
        return -8.0
    return 0.0


def _inventory_bonus(query: str, text: str) -> float:
    if not any(word in query for word in ("清单", "零件", "部件", "装配")):
        return 0.0
    if not any(word in text for word in ("表格", "序号", "零件名称", "料件名称", "装配部件清单", "装配零件清单")):
        return 0.0
    return 4.0


def _procedure_inventory_penalty(query: str, text: str) -> float:
    has_procedure_signal = (
        any(action in query for action in _ACTION_SYNONYMS)
        or any(term in query for term in _VISUAL_ATTRIBUTE_TERMS)
        or bool(re.search(r"\d+(?:\.\d+)?", query))
    )
    if not has_procedure_signal:
        return 0.0
    if any(word in query for word in ("清单", "零件清单", "部件清单", "BOM")):
        return 0.0
    if any(term in text for term in _INVENTORY_TERMS):
        return -35.0
    return 0.0


def _visual_attribute_score(query: str, text: str, image_text: str) -> float:
    compact_text = _compact(text)
    compact_image_text = _compact(image_text)
    combined = compact_text + compact_image_text
    score = 0.0
    for term in _VISUAL_ATTRIBUTE_TERMS:
        if term not in query:
            continue
        if term in compact_image_text:
            score += 22.0 if term in _MANDATORY_VISUAL_TERMS else 16.0
        elif term in compact_text:
            score += 16.0 if term in _MANDATORY_VISUAL_TERMS else 12.0
        elif term in _MANDATORY_VISUAL_TERMS:
            score -= 170.0

    numbers = re.findall(r"\d+(?:\.\d+)?", query)
    for number in numbers:
        if number in combined:
            score += 12.0
        else:
            score -= 30.0
    return score


def _inventory_item_terms(query: str) -> List[str]:
    if "清单" not in query or "中" not in query:
        return []
    tail = query.split("中", 1)[1]
    for noise in (*_NOISE, "图片", "图示", "插图", "是哪张", "哪张"):
        tail = tail.replace(noise, " ")
    for connector in ("以及", "并且", "和", "与", "及", "或", "、", "，", ",", "；", ";"):
        tail = tail.replace(connector, " ")
    return [term for term in re.split(r"\s+", tail.strip()) if len(term) >= 2]


def _inventory_scope(query: str) -> str:
    if "清单" not in query or "中" not in query:
        return ""
    scope = query.split("中", 1)[0]
    for noise in (*_NOISE, "图片", "图示", "插图", "是哪张", "哪张"):
        scope = scope.replace(noise, "")
    return _compact(scope)


def _inventory_scope_matches(scope: str, text: str) -> bool:
    if not scope:
        return True
    if scope in text:
        return True
    alternatives = []
    if "装配清单" in scope:
        alternatives.extend([
            scope.replace("装配清单", "装配部件清单"),
            scope.replace("装配清单", "装配零件清单"),
        ])
    return any(alt and alt in text for alt in alternatives)


def _title_query_target(query: str) -> str:
    if "清单" in query and "中" in query:
        return _inventory_scope(query)
    target = _query_target(query)
    for noise in ("章节", "有没有", "对应", "图片", "图示", "插图", "是哪张", "哪张"):
        target = target.replace(noise, "")
    return _compact(target)


def _title_anchor_score(query: str, text: str, image_text: str) -> float:
    if not any(word in query for word in ("章节", "对应", "清单")):
        return 0.0
    target = _title_query_target(query)
    if len(target) < 3:
        return 0.0
    combined = _compact(text) + _compact(image_text)
    targets = [target]
    if "装配清单" in target:
        targets.extend([
            target.replace("装配清单", "装配部件清单"),
            target.replace("装配清单", "装配零件清单"),
        ])
    for candidate in targets:
        if candidate and re.search(r"\d+(?:\.\d+)*" + re.escape(candidate), combined):
            return 180.0
    return 0.0


def _inventory_item_score(query: str, text: str, image_text: str) -> float:
    terms = _inventory_item_terms(query)
    if not terms:
        return 0.0
    compact_text = _compact(text)
    compact_image_text = _compact(image_text)
    scope = _inventory_scope(query)
    scope_in_text = _inventory_scope_matches(scope, compact_text)
    scope_in_image_text = _inventory_scope_matches(scope, compact_image_text)
    if scope and not scope_in_text and not scope_in_image_text:
        return -160.0
    score = 0.0
    if scope:
        score += 40.0 if scope_in_text else 18.0
    for term in terms:
        if term in compact_text:
            score += 150.0
        elif term in compact_image_text:
            score += 10.0
        else:
            score -= 18.0
    return score


def _allows_low_score_supporting_neighbor(query: str) -> bool:
    return any(term in query for term in _LOW_SCORE_NEIGHBOR_PROCEDURE_TERMS)


def _base_score_page(query: str, page: PageEvidence) -> float:
    text = _compact(page.text)
    image_text = _compact(page.image_text)
    if (not text and not image_text) or not page.images:
        return -999.0

    score = 0.0
    for action in _query_actions(query):
        if any(word in text for word in _ACTION_SYNONYMS[action]):
            score += 6.0
        if image_text and any(word in image_text for word in _ACTION_SYNONYMS[action]):
            score += 4.0
        if any(word in text for word in _OPPOSITE_ACTIONS.get(action, ())):
            score -= 6.0
        if image_text and any(word in image_text for word in _OPPOSITE_ACTIONS.get(action, ())):
            score -= 24.0

    grams = _char_ngrams(query)
    for gram in grams:
        if gram in text:
            score += min(len(gram), 5) * 0.35
        if image_text and gram in image_text:
            score += min(len(gram), 6) * 0.8

    score += _inventory_bonus(query, text)
    if image_text:
        score += _inventory_bonus(query, image_text)
    score += _procedure_inventory_penalty(query, text)
    if image_text:
        score += _procedure_inventory_penalty(query, image_text)
    score += _orientation_penalty(query, text)
    if image_text:
        score += _orientation_penalty(query, image_text)
    score += _visual_attribute_score(query, text, image_text)
    score += _inventory_item_score(query, text, image_text)
    score += _title_anchor_score(query, text, image_text)
    return score


def _score_page(query: str, page: PageEvidence) -> float:
    return _base_score_page(query, page)


def _unique_pages(pages: Iterable[int]) -> List[int]:
    seen: set[int] = set()
    result: List[int] = []
    for page in pages:
        if page in seen:
            continue
        seen.add(page)
        result.append(page)
    return result


def _page_span(pages: Sequence[int]) -> int:
    return max(pages) - min(pages) if pages else 0


def _score_lookup(scores: Sequence[PageScore]) -> Dict[int, float]:
    return {score.page: score.score for score in scores}


def _best_score(pages: Sequence[int], scores: Dict[int, float]) -> float:
    matched = [scores[page] for page in pages if page in scores]
    return max(matched) if matched else 0.0


def score_pages_for_image_query(query: str, pages: Sequence[PageEvidence]) -> List[PageScore]:
    searchable = [
        (page, _compact(page.text), _compact(page.image_text))
        for page in pages
        if page.images and (page.text or page.image_text)
    ]
    terms = _query_terms(query)
    doc_count = len(searchable)
    df: Dict[str, int] = {}
    for term in terms:
        df[term] = sum(1 for _, text, image_text in searchable if term in text or term in image_text)

    scored: List[PageScore] = []
    for page, text, image_text in searchable:
        score = _base_score_page(query, page)
        for term in terms:
            idf = math.log((doc_count + 1) / (df[term] + 0.5)) + 1.0
            length_weight = 0.2 + min(len(term), 8) * 0.28
            if term in text:
                score += idf * length_weight
            if image_text and term in image_text:
                score += idf * length_weight * 1.5
        scored.append(PageScore(page=page.page, score=score, group_key=page.group_key))
    scored = [score for score in scored if score.score > 0]
    return sorted(scored, key=lambda item: (-item.score, item.page))


def select_pages_for_image_query(
    query: str,
    pages: Sequence[PageEvidence],
    image_mode: str = "same_section",
    max_pages: int | None = None,
) -> List[int]:
    """Select page numbers whose images best match the image query."""
    scored = score_pages_for_image_query(query, pages)
    if not scored:
        return []

    if image_mode == "single_best":
        return [scored[0].page]

    best_score = scored[0]
    best = best_score.score
    threshold = max(1.0, best - 8.0)
    selected = [score.page for score in scored if score.score >= threshold]
    best_page = best_score.page
    if max_pages is not None:
        selected = [best_page]
        best_group = best_score.group_key
        allow_low_score_neighbor = _allows_low_score_supporting_neighbor(query)
        same_group_neighbors = [
            score for score in scored
            if score.page != best_page
            and score.score > 0
            and (allow_low_score_neighbor or score.score >= threshold)
            and abs(score.page - best_page) == 1
            and (
                (best_group and score.group_key == best_group)
                or (not best_group and not score.group_key)
            )
        ]
        adjacent_neighbors = [
            score for score in scored
            if score.page != best_page
            and score.score > 0
            and (allow_low_score_neighbor or score.score >= threshold)
            and abs(score.page - best_page) == 1
            and score.page not in selected
            and score not in same_group_neighbors
        ]
        others = [
            score for score in scored
            if score.page != best_page
            and score.score >= threshold
            and score.page not in selected
            and score not in same_group_neighbors
            and score not in adjacent_neighbors
        ]
        for score in same_group_neighbors + adjacent_neighbors + others:
            if score.page not in selected:
                selected.append(score.page)
            if len(selected) >= max_pages:
                break
        return _unique_pages(sorted(selected))
    for score in scored:
        if score.score > 0 and abs(score.page - best_page) == 1 and score.page not in selected:
            selected.append(score.page)
    if max_pages is not None:
        selected = selected[:max_pages]
    return _unique_pages(sorted(selected))


def gated_select_pages_for_image_query(
    query: str,
    pages: Sequence[PageEvidence],
    original_pages: Sequence[int],
    image_mode: str = "same_section",
    max_pages: int | None = None,
    force_replace: bool = False,
) -> GatedPageSelection:
    """Conservatively apply page-level image rerank.

    The normal retrieval result is kept unless the page-level selector provides
    strong evidence that the current image pages are sparse, conflicting, or
    missing.  This function only selects image pages and does not affect text
    evidence.
    """
    original = _unique_pages(int(page) for page in original_pages if page is not None)
    scores = score_pages_for_image_query(query, pages)
    free_selected = select_pages_for_image_query(query, pages, image_mode=image_mode, max_pages=max_pages)
    if not free_selected:
        return GatedPageSelection(original, [], False, "keep_original_no_candidate", scores)
    if not original:
        return GatedPageSelection(free_selected, free_selected, True, "replace_missing_original_images", scores)
    if set(original) == set(free_selected):
        return GatedPageSelection(original, free_selected, False, "keep_original_same_page_set", scores)
    if force_replace:
        return GatedPageSelection(free_selected, free_selected, True, "forced_conflict_replacement", scores)

    lookup = _score_lookup(scores)
    top = scores[0]
    original_best = _best_score(original, lookup)
    score_ratio = top.score / max(original_best, 1e-6)

    if (
        len(original) == 1
        and len(free_selected) == 1
        and free_selected[0] != original[0]
        and score_ratio >= 4.0
    ):
        return GatedPageSelection(free_selected, free_selected, True, "dominant_single_page_alternative", scores)

    if (
        len(original) >= 2
        and min(original) < top.page < max(original)
        and top.page not in original
        and _page_span(original) >= 3
        and score_ratio >= 1.6
    ):
        return GatedPageSelection([top.page], free_selected, True, "bridged_sparse_original_gap", scores)

    if (
        len(original) >= 2
        and len(free_selected) >= 2
        and set(original) & set(free_selected)
        and set(original) != set(free_selected)
        and _page_span(original) >= 5
        and _page_span(free_selected) <= 2
    ):
        return GatedPageSelection(free_selected, free_selected, True, "replaced_distant_original_outlier", scores)

    if max_pages is not None and len(original) > max_pages and len(free_selected) <= max_pages:
        return GatedPageSelection(free_selected, free_selected, True, "reduced_over_wide_image_set", scores)

    return GatedPageSelection(original, free_selected, False, "keep_original_low_confidence", scores)
