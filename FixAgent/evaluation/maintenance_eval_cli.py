"""End-to-end maintenance manual evaluation.

This evaluator is intentionally stricter than the legacy retrieval-only CSV
evaluation.  It scores the user-facing answer as a maintenance task:

- required nugget coverage
- unsupported/forbidden claims
- procedure order
- refusal behavior
- image recall/precision/order
- step-image binding

The dataset format is JSONL because procedure/image binding is too structured
for the old flat CSV format.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

from evaluation.maintenance_eval_evidence import score_turn_output
from evaluation.maintenance_eval_schema import (
    MaintenanceEvalCase,
    MaintenanceEvalTurn,
    read_jsonl_dataset,
    read_jsonl_datasets,
)


REFUSAL_HINTS = (
    "未找到",
    "没有找到",
    "未检索到",
    "资料不足",
    "依据不足",
    "未提供",
    "未提及",
    "未说明",
    "未明确说明",
    "没有提供",
    "无法确定",
    "无法回答",
    "不能确定",
    "不在资料",
    "不在手册",
    "no relevant",
    "not found",
    "does not provide",
    "not provide",
    "insufficient",
    "cannot determine",
)

NEGATED_CLAIM_HINTS = (
    "未提及",
    "未说明",
    "未明确说明",
    "未提供",
    "没有提及",
    "没有说明",
    "没有提供",
    "不含",
    "无法确定",
    "不能确定",
    "手册未",
    "资料未",
)


METRIC_DESCRIPTIONS_CN = {
    "case_count": "测评用例总数。",
    "answerable_case_count": "手册中应当能回答的用例数量。",
    "final_pass_rate": "最终通过率；必须同时满足必答点、禁答项、拒答、步骤顺序、图片等约束。",
    "avg_required_nugget_recall": "必答信息点平均覆盖率；越高说明答案越完整。",
    "grounding_pass_rate": "证据忠实率；必答点覆盖且没有 forbidden_claims 中的无依据/错误说法。",
    "unsupported_claim_free_rate": "无禁答项命中率；越低说明模型越容易乱补或说手册没写的内容。",
    "procedure_case_count": "需要评估步骤顺序的用例数量。",
    "procedure_order_pass_rate": "步骤顺序通过率；安装/拆卸流程必须按手册顺序。",
    "image_case_count": "需要评估图片的用例数量。",
    "image_pass_rate": "图片整体通过率；要求图片召回、精确率、顺序、禁图、步骤绑定均通过。",
    "avg_image_recall": "图片平均召回率；应返回的图片/页是否都返回。",
    "avg_image_precision": "图片平均精确率；返回的图片/页是否没有多余项。",
    "image_order_pass_rate": "图片顺序通过率；返回图片顺序是否符合操作流程。",
    "step_image_binding_pass_rate": "步骤-图片绑定通过率；图片是否能绑定到对应步骤。",
    "no_answer_case_count": "手册无依据、应拒答/说明无依据的用例数量。",
    "no_answer_correct_rate": "无答案题正确克制率；不应编造手册没有的信息。",
    "avg_latency_ms": "端到端平均耗时，单位毫秒。",
}


@dataclass
class CaseRunResult:
    answer: str = ""
    evidence_images: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0
    error: str = ""


def normalize_text(value: str) -> str:
    text = (value or "").lower()
    text = text.replace("～", "-").replace("—", "-").replace("–", "-")
    text = text.replace("×", "x").replace("℃", "°c")
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[，。；：、,.。;:()\[\]（）【】“”\"'`|=]", "", text)
    text = re.sub(r"φ(\d+)个(\d+)", r"\1个φ\2", text)
    for filler in ("包括", "包含", "分别"):
        text = text.replace(filler, "")
    return text


def phrase_matched(phrase: str, answer: str) -> bool:
    phrase_norm = normalize_text(phrase)
    answer_norm = normalize_text(answer)
    if not phrase_norm:
        return True
    if phrase_norm in answer_norm:
        return True
    if phrase_norm.startswith("先") and "再" in phrase_norm:
        first, second = phrase_norm[1:].split("再", 1)
        if first and second:
            first_index = answer_norm.find(first)
            second_index = answer_norm.find(second, first_index + len(first)) if first_index >= 0 else -1
            if first_index >= 0 and second_index >= 0:
                return True
    numbers = re.findall(r"\d+(?:\.\d+)?", phrase_norm)
    if numbers and not all(number in answer_norm for number in numbers):
        return False
    chars = [ch for ch in phrase_norm if "\u4e00" <= ch <= "\u9fff"]
    if len(chars) >= 4:
        overlap = sum(1 for ch in set(chars) if ch in answer_norm) / len(set(chars))
        return overlap >= 0.8
    return False


def _matched_phrases(phrases: Sequence[str], answer: str) -> list[str]:
    return [phrase for phrase in phrases if phrase_matched(phrase, answer)]


def _is_generic_forbidden_claim(phrase_norm: str) -> bool:
    if re.search(r"\d|[a-z]", phrase_norm):
        return False
    chinese_chars = [ch for ch in phrase_norm if "\u4e00" <= ch <= "\u9fff"]
    return len(chinese_chars) < 4


def forbidden_claim_matched(phrase: str, answer: str) -> bool:
    phrase_norm = normalize_text(phrase)
    answer_norm = normalize_text(answer)
    if not phrase_norm or _is_generic_forbidden_claim(phrase_norm):
        return False
    index = answer_norm.find(phrase_norm)
    if index < 0:
        return False
    prefix_window = answer_norm[max(0, index - 16) : index]
    if any(normalize_text(hint) in prefix_window for hint in NEGATED_CLAIM_HINTS):
        return False
    return True


def _matched_forbidden_claims(phrases: Sequence[str], answer: str) -> list[str]:
    return [phrase for phrase in phrases if forbidden_claim_matched(phrase, answer)]


def _contains_refusal(answer: str) -> bool:
    text = normalize_text(answer)
    return any(normalize_text(hint) in text for hint in REFUSAL_HINTS)


def _ordered_snippet_position(text: str, needle: str, cursor: int) -> int:
    exact_index = text.find(needle, cursor)
    if exact_index >= 0:
        return exact_index
    marker_match = re.match(r"([a-z])标记(?:应)?与([a-z])标记(.+)", needle)
    if marker_match:
        first, second, tail = marker_match.groups()

        def marker_positions(marker: str, start: int) -> list[int]:
            patterns = (f"{marker}标记", f"标记图示{marker}", f"标记{marker}")
            positions = [text.find(pattern, start) for pattern in patterns]
            return sorted(position for position in positions if position >= 0)

        for first_index in marker_positions(first, cursor):
            second_positions = marker_positions(second, first_index + 1)
            if not second_positions:
                continue
            second_index = second_positions[0]
            if tail and text.find(tail, second_index + 1) < 0:
                continue
            return first_index
    for verb in ("放入", "取出", "取下"):
        if needle.startswith(verb) and len(needle) > len(verb):
            item = needle[len(verb):]
            item_index = text.find(item, cursor)
            if item_index >= 0:
                context = text[max(0, item_index - 40):item_index]
                cursor_context = text[max(0, cursor - 40):cursor]
                if f"依次{verb}" in context or f"依次{verb}" in cursor_context:
                    return max(0, item_index - len(verb))
    if len(needle) < 6:
        return -1
    chinese_chars = [ch for ch in needle if "\u4e00" <= ch <= "\u9fff"]
    if len(chinese_chars) < 4:
        return -1

    max_span = max(len(needle) + 8, int(len(needle) * 1.6))
    start = text.find(needle[0], cursor)
    while start >= 0:
        search_pos = start + 1
        last_pos = start
        matched = True
        for ch in needle[1:]:
            index = text.find(ch, search_pos)
            if index < 0:
                matched = False
                break
            last_pos = index
            search_pos = index + 1
        if matched and last_pos - start + 1 <= max_span:
            return start
        start = text.find(needle[0], start + 1)
    return -1


def _ordered_positions(snippets: Sequence[str], answer: str) -> tuple[bool, list[int]]:
    text = normalize_text(answer)
    positions: list[int] = []
    cursor = 0
    for snippet in snippets:
        needle = normalize_text(snippet)
        if not needle:
            continue
        index = _ordered_snippet_position(text, needle, cursor)
        if index < 0:
            return False, positions
        positions.append(index)
        cursor = index + len(needle)
    return True, positions


def _image_page(image: Mapping[str, Any]) -> int | None:
    for key in ("page", "pageNumber", "page_number"):
        if key in image and image.get(key) not in (None, ""):
            try:
                return int(image[key])
            except (TypeError, ValueError):
                return None
    return None


def _unique_keep_order(values: Sequence[int]) -> list[int]:
    seen: set[int] = set()
    ordered: list[int] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def _expected_image_pages(case: MaintenanceEvalCase) -> list[int]:
    explicit = [_image_page(image) for image in case.expected_images]
    pages = [page for page in explicit if page is not None]
    if not pages and case.expected_image_order:
        pages = list(case.expected_image_order)
    return _unique_keep_order(pages)


def _forbidden_image_pages(case: MaintenanceEvalCase) -> list[int]:
    pages = [_image_page(image) for image in case.forbidden_images]
    return _unique_keep_order([page for page in pages if page is not None])


def _evaluate_images(case: MaintenanceEvalCase, evidence_images: Sequence[Mapping[str, Any]], answer: str) -> dict[str, Any]:
    expected_pages = _expected_image_pages(case)
    forbidden_pages = _forbidden_image_pages(case)
    retrieved_pages = _unique_keep_order(
        [page for page in (_image_page(image) for image in evidence_images) if page is not None]
    )

    image_eval_required = bool(expected_pages or forbidden_pages or case.expected_image_order or case.step_image_mapping)
    expected_set = set(expected_pages)
    retrieved_set = set(retrieved_pages)
    if expected_set:
        matched = expected_set & retrieved_set
        image_recall = len(matched) / len(expected_set)
        image_precision = len(matched) / len(retrieved_set) if retrieved_set else 0.0
    else:
        image_recall = 1.0 if not retrieved_pages else 0.0
        image_precision = 1.0 if not retrieved_pages else 0.0

    forbidden_hit_pages = [page for page in retrieved_pages if page in set(forbidden_pages)]
    forbidden_image_pass = not forbidden_hit_pages

    expected_order = case.expected_image_order or expected_pages
    if expected_order:
        retrieved_expected_only = [page for page in retrieved_pages if page in set(expected_order)]
        image_order_pass = retrieved_expected_only == expected_order
    else:
        image_order_pass = True

    binding_failures: list[str] = []
    for item in case.step_image_mapping:
        step = str(item.get("step") or "").strip()
        page = _image_page(item)
        if step and not phrase_matched(step, answer):
            binding_failures.append(f"步骤未出现在答案中:{step}")
        if page is not None and page not in retrieved_set:
            binding_failures.append(f"步骤图片未返回:{step or page}->第{page}页")
    step_image_binding_pass = not binding_failures

    image_pass = (
        (not image_eval_required)
        or (
            image_recall >= 1.0
            and image_precision >= 1.0
            and forbidden_image_pass
            and image_order_pass
            and step_image_binding_pass
        )
    )

    return {
        "image_eval_required": image_eval_required,
        "expected_image_pages": ";".join(str(page) for page in expected_pages),
        "retrieved_image_pages": ";".join(str(page) for page in retrieved_pages),
        "forbidden_image_pages": ";".join(str(page) for page in forbidden_pages),
        "forbidden_image_hit_pages": ";".join(str(page) for page in forbidden_hit_pages),
        "image_recall": round(image_recall, 6),
        "image_precision": round(image_precision, 6),
        "forbidden_image_pass": forbidden_image_pass,
        "image_order_pass": image_order_pass,
        "step_image_binding_pass": step_image_binding_pass,
        "step_image_binding_failures": "；".join(binding_failures),
        "image_pass": image_pass,
    }


def _case_has_evidence_constraints(case: MaintenanceEvalCase) -> bool:
    return bool(
        case.expected_scope
        or case.expected_coverage_status
        or case.claim_constraints
        or case.conflict_constraints
        or case.forbidden_source_terms
        or case.source_request_mode != "normal"
        or case.style_expectation is not None
    )


def _case_to_evidence_turn(case: MaintenanceEvalCase) -> MaintenanceEvalTurn:
    return MaintenanceEvalTurn(
        query=case.query,
        task_type=case.task_type,
        intent_action=case.intent_action,
        target_section=case.target_section,
        target_pages=case.target_pages,
        answerable=case.answerable,
        expected_scope=case.expected_scope,
        expected_coverage_status=case.expected_coverage_status,
        claim_constraints=case.claim_constraints,
        conflict_constraints=case.conflict_constraints,
        forbidden_source_terms=case.forbidden_source_terms,
        source_request_mode=case.source_request_mode,
        style_expectation=case.style_expectation,
    )


def _evaluate_evidence(
    case: MaintenanceEvalCase,
    answer: str,
    metadata: Mapping[str, Any] | None,
) -> dict[str, Any]:
    score = score_turn_output(_case_to_evidence_turn(case), answer, metadata)
    if not _case_has_evidence_constraints(case):
        return {
            "evidence_score_available": False,
            "evidence_coverage_status": "",
            "evidence_final_pass": "",
            "evidence_scope_isolation_pass": "",
            "evidence_source_pass": "",
            "evidence_answer_alignment_pass": "",
            "evidence_nugget_coverage_rate": "",
            "evidence_unsupported_completion_free": "",
            "evidence_partial_answer_correct": "",
            "evidence_conflict_handling_pass": "",
            "evidence_source_style_mode_pass": "",
            "evidence_refusal_integrity_pass": "",
            "evidence_fixed_template_detected": score.fixed_template_detected,
            "evidence_style_proxy_pass": score.style_proxy_pass,
            "evidence_source_mode_pass": score.source_mode_pass,
            "evidence_diagnostics": "",
        }
    return {
        "evidence_score_available": True,
        "evidence_coverage_status": score.coverage_status,
        "evidence_final_pass": score.final_pass,
        "evidence_scope_isolation_pass": score.scope_isolation_pass,
        "evidence_source_pass": score.evidence_source_pass,
        "evidence_answer_alignment_pass": score.answer_evidence_alignment_pass,
        "evidence_nugget_coverage_rate": score.evidence_nugget_coverage_rate,
        "evidence_unsupported_completion_free": score.unsupported_completion_free,
        "evidence_partial_answer_correct": score.partial_answer_correct,
        "evidence_conflict_handling_pass": score.conflict_handling_pass,
        "evidence_source_style_mode_pass": score.source_style_mode_pass,
        "evidence_refusal_integrity_pass": score.refusal_integrity_pass,
        "evidence_fixed_template_detected": score.fixed_template_detected,
        "evidence_style_proxy_pass": score.style_proxy_pass,
        "evidence_source_mode_pass": score.source_mode_pass,
        "evidence_diagnostics": "；".join(score.diagnostics),
    }


def evaluate_case_output(
    case: MaintenanceEvalCase,
    generated_answer: str,
    evidence_images: Sequence[Mapping[str, Any]] | None = None,
    *,
    latency_ms: int = 0,
    error: str = "",
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    answer = generated_answer or ""
    evidence_images = evidence_images or []

    matched_required = _matched_phrases(case.required_nuggets, answer)
    matched_optional = _matched_phrases(case.optional_nuggets, answer)
    hit_forbidden = _matched_forbidden_claims(case.forbidden_claims, answer)

    required_total = len(case.required_nuggets)
    required_recall = len(matched_required) / required_total if required_total else (1.0 if case.answerable else 0.0)
    forbidden_claim_pass = not hit_forbidden

    if not case.answerable:
        refusal_pass = _contains_refusal(answer)
        grounding_pass = refusal_pass and forbidden_claim_pass
    else:
        refusal_pass = True
        grounding_pass = required_recall >= 1.0 and forbidden_claim_pass

    if case.expected_step_order:
        procedure_order_pass, step_positions = _ordered_positions(case.expected_step_order, answer)
    else:
        procedure_order_pass, step_positions = True, []

    image_metrics = _evaluate_images(case, evidence_images, answer)
    evidence_metrics = _evaluate_evidence(case, answer, metadata)
    final_pass = bool(
        grounding_pass
        and refusal_pass
        and procedure_order_pass
        and image_metrics["image_pass"]
        and (
            not evidence_metrics["evidence_score_available"]
            or evidence_metrics["evidence_final_pass"]
        )
        and not error
    )

    return {
        "id": case.case_id,
        "case_id": case.case_id,
        "turn_index": 1,
        "turn_count": 1,
        "request_count": 1,
        "dataset_source": case.dataset_source,
        "group": case.group,
        "query": case.query,
        "task_type": case.task_type,
        "intent_action": case.intent_action,
        "target_section": case.target_section,
        "target_pages": ";".join(str(page) for page in case.target_pages),
        "difficulty": case.difficulty,
        "trap_type": ";".join(case.trap_type),
        "answerable": case.answerable,
        "expected_scope": case.expected_scope,
        "expected_coverage_status": case.expected_coverage_status,
        "source_request_mode": case.source_request_mode,
        "claim_constraint_count": len(case.claim_constraints),
        "has_forbidden_without_evidence": any(
            constraint.forbidden_without_evidence_patterns
            for constraint in case.claim_constraints
        ),
        "generated_answer": answer,
        "required_nuggets": "；".join(case.required_nuggets),
        "matched_required_nuggets": "；".join(matched_required),
        "missing_required_nuggets": "；".join(
            nugget for nugget in case.required_nuggets if nugget not in matched_required
        ),
        "required_nugget_recall": round(required_recall, 6),
        "optional_nuggets": "；".join(case.optional_nuggets),
        "matched_optional_nuggets": "；".join(matched_optional),
        "forbidden_claims": "；".join(case.forbidden_claims),
        "hit_forbidden_claims": "；".join(hit_forbidden),
        "forbidden_claim_pass": forbidden_claim_pass,
        "refusal_pass": refusal_pass,
        "expected_step_order": "；".join(case.expected_step_order),
        "step_positions": ";".join(str(pos) for pos in step_positions),
        "procedure_order_pass": procedure_order_pass,
        "grounding_pass": grounding_pass,
        "final_pass": final_pass,
        "latency_ms": latency_ms,
        "error": error,
        **image_metrics,
        **evidence_metrics,
    }


def summarize_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    def rate(key: str, subset: Sequence[Mapping[str, Any]] | None = None) -> float:
        data = list(subset if subset is not None else rows)
        if not data:
            return 0.0
        return round(sum(1 for row in data if bool(row.get(key))) / len(data), 6)

    def avg(key: str, subset: Sequence[Mapping[str, Any]] | None = None) -> float:
        data = list(subset if subset is not None else rows)
        if not data:
            return 0.0
        return round(sum(float(row.get(key) or 0.0) for row in data) / len(data), 6)

    answerable_rows = [row for row in rows if bool(row.get("answerable"))]
    no_answer_rows = [row for row in rows if not bool(row.get("answerable"))]
    procedure_rows = [row for row in rows if str(row.get("expected_step_order") or "").strip()]
    image_rows = [row for row in rows if bool(row.get("image_eval_required"))]
    latency_rows = [row for row in rows if row.get("latency_ms") not in (None, "")]

    summary = {
        "case_count": len(rows),
        "answerable_case_count": len(answerable_rows),
        "final_pass_rate": rate("final_pass"),
        "avg_required_nugget_recall": avg("required_nugget_recall", answerable_rows),
        "grounding_pass_rate": rate("grounding_pass"),
        "unsupported_claim_free_rate": rate("forbidden_claim_pass"),
        "procedure_case_count": len(procedure_rows),
        "procedure_order_pass_rate": rate("procedure_order_pass", procedure_rows),
        "image_case_count": len(image_rows),
        "image_pass_rate": rate("image_pass", image_rows),
        "avg_image_recall": avg("image_recall", image_rows),
        "avg_image_precision": avg("image_precision", image_rows),
        "image_order_pass_rate": rate("image_order_pass", image_rows),
        "step_image_binding_pass_rate": rate("step_image_binding_pass", image_rows),
        "no_answer_case_count": len(no_answer_rows),
        "no_answer_correct_rate": rate("refusal_pass", no_answer_rows),
        "avg_latency_ms": round(sum(int(row.get("latency_ms") or 0) for row in latency_rows) / len(latency_rows), 2)
        if latency_rows
        else 0.0,
        "metric_descriptions_cn": METRIC_DESCRIPTIONS_CN,
    }
    return summary


def _chat_api_request(
    endpoint: str,
    case: MaintenanceEvalCase,
    timeout: int,
    *,
    session_id: str,
    default_device_type: str = "",
    default_document_id: str = "",
) -> CaseRunResult:
    payload: dict[str, Any] = {
        "session_id": session_id,
        "message": case.query,
        "stream": False,
    }
    device_type = case.device_type or default_device_type
    document_id = case.document_id or default_document_id
    if device_type:
        payload["device_type"] = device_type
    if document_id:
        payload["document_id"] = document_id
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8", errors="replace")
        latency_ms = int((time.perf_counter() - started) * 1000)
        data = json.loads(response_body)
        return CaseRunResult(
            answer=str(data.get("message") or ""),
            evidence_images=list(data.get("evidenceImages") or data.get("evidence_images") or []),
            metadata=dict(data.get("metadata") or {}),
            latency_ms=latency_ms,
        )
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return CaseRunResult(latency_ms=int((time.perf_counter() - started) * 1000), error=str(exc))


def _turn_to_eval_case(
    case: MaintenanceEvalCase,
    turn: MaintenanceEvalTurn,
    turn_index: int,
) -> MaintenanceEvalCase:
    return MaintenanceEvalCase(
        case_id=f"{case.case_id}:t{turn_index}",
        query=turn.query,
        device_type=case.device_type,
        document_id=case.document_id,
        document_version=case.document_version,
        manual_type=case.manual_type,
        difficulty=case.difficulty,
        trap_type=case.trap_type,
        group=case.group,
        dataset_source=case.dataset_source,
        task_type=turn.task_type or case.task_type,
        intent_action=turn.intent_action or case.intent_action,
        target_section=turn.target_section,
        target_pages=turn.target_pages,
        answerable=turn.answerable if turn.answerable is not None else True,
        required_nuggets=turn.required_nuggets,
        optional_nuggets=turn.optional_nuggets,
        forbidden_claims=turn.forbidden_claims,
        expected_step_order=turn.expected_step_order,
        expected_images=turn.expected_images,
        expected_image_order=turn.expected_image_order,
        step_image_mapping=turn.step_image_mapping,
        forbidden_images=turn.forbidden_images,
        expected_scope=turn.expected_scope,
        expected_coverage_status=turn.expected_coverage_status,
        claim_constraints=turn.claim_constraints,
        conflict_constraints=turn.conflict_constraints,
        forbidden_source_terms=turn.forbidden_source_terms,
        source_request_mode=turn.source_request_mode,
        style_expectation=turn.style_expectation,
    )


def _chat_api_request_turn(
    endpoint: str,
    case: MaintenanceEvalCase,
    turn_query: str,
    conversation_history: list[dict[str, str]],
    timeout: int,
    *,
    session_id: str,
    default_device_type: str = "",
    default_document_id: str = "",
) -> CaseRunResult:
    payload: dict[str, Any] = {
        "session_id": session_id,
        "message": turn_query,
        "stream": False,
        "conversation_history": conversation_history,
    }
    device_type = case.device_type or default_device_type
    document_id = case.document_id or default_document_id
    if device_type:
        payload["device_type"] = device_type
    if document_id:
        payload["document_id"] = document_id
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8", errors="replace")
        latency_ms = int((time.perf_counter() - started) * 1000)
        data = json.loads(response_body)
        return CaseRunResult(
            answer=str(data.get("message") or ""),
            evidence_images=list(data.get("evidenceImages") or data.get("evidence_images") or []),
            metadata=dict(data.get("metadata") or {}),
            latency_ms=latency_ms,
        )
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return CaseRunResult(latency_ms=int((time.perf_counter() - started) * 1000), error=str(exc))


def _session_id(run_id: str, case_id: str) -> str:
    safe_run_id = re.sub(r"[^A-Za-z0-9_-]+", "-", run_id).strip("-") or "run"
    safe_case_id = re.sub(r"[^A-Za-z0-9_-]+", "-", case_id).strip("-") or "case"
    return f"maintenance-eval-{safe_run_id}-{safe_case_id}"


def _decorate_turn_row(
    row: dict[str, Any],
    case: MaintenanceEvalCase,
    turn_index: int,
) -> dict[str, Any]:
    row.update(
        {
            "case_id": case.case_id,
            "turn_index": turn_index,
            "turn_count": len(case.turns) if case.turns else 1,
            "request_count": 1,
            "dataset_source": case.dataset_source,
            "group": case.group,
        }
    )
    return row


def _append_trace_row(
    trace_rows: list[dict[str, Any]] | None,
    *,
    case: MaintenanceEvalCase,
    turn_index: int,
    row: Mapping[str, Any],
    result: CaseRunResult,
) -> None:
    if trace_rows is None:
        return
    trace_rows.append(
        {
            "id": row["id"],
            "case_id": case.case_id,
            "turn_index": turn_index,
            "dataset_source": case.dataset_source,
            "query": row["query"],
            "answer": result.answer,
            "metadata": result.metadata,
            "evidence_diagnostics": row.get("evidence_diagnostics", ""),
            "error": result.error,
        }
    )


def _run_multi_turn_case(
    case: MaintenanceEvalCase,
    *,
    mode: str,
    endpoint: str,
    timeout: int,
    run_id: str,
    default_device_type: str,
    default_document_id: str,
    trace_rows: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    conversation_history: list[dict[str, str]] = []
    session_id = _session_id(run_id, case.case_id)
    for turn_index, turn in enumerate(case.turns, start=1):
        if mode == "api":
            result = _chat_api_request_turn(
                endpoint,
                case,
                turn.query,
                conversation_history,
                timeout,
                session_id=session_id,
                default_device_type=default_device_type,
                default_document_id=default_document_id,
            )
        else:
            result = CaseRunResult(
                answer=turn.candidate_answer,
                evidence_images=turn.candidate_images,
                metadata=turn.candidate_metadata,
            )
        turn_case = _turn_to_eval_case(case, turn, turn_index)
        row = evaluate_case_output(
            turn_case,
            result.answer,
            result.evidence_images,
            latency_ms=result.latency_ms,
            error=result.error,
            metadata=result.metadata,
        )
        _decorate_turn_row(row, case, turn_index)
        _append_trace_row(
            trace_rows,
            case=case,
            turn_index=turn_index,
            row=row,
            result=result,
        )
        rows.append(row)
        print(
            f"  turn {turn_index}/{len(case.turns)} {turn_case.case_id} final={row['final_pass']} "
            f"nugget={row['required_nugget_recall']} latency_ms={row['latency_ms']}",
            flush=True,
        )
        conversation_history.append({"role": "user", "content": turn.query})
        conversation_history.append({"role": "assistant", "content": result.answer})
    return rows


def run_cases(
    cases: Sequence[MaintenanceEvalCase],
    *,
    mode: str,
    endpoint: str,
    timeout: int,
    run_id: str | None = None,
    default_device_type: str = "",
    default_document_id: str = "",
    trace_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    active_run_id = run_id or uuid.uuid4().hex
    for index, case in enumerate(cases, start=1):
        if case.turns:
            print(f"{index}/{len(cases)} {case.case_id} [multi-turn x{len(case.turns)}]", flush=True)
            rows.extend(
                _run_multi_turn_case(
                    case,
                    mode=mode,
                    endpoint=endpoint,
                    timeout=timeout,
                    run_id=active_run_id,
                    default_device_type=default_device_type,
                    default_document_id=default_document_id,
                    trace_rows=trace_rows,
                )
            )
            continue
        if mode == "api":
            result = _chat_api_request(
                endpoint,
                case,
                timeout,
                session_id=_session_id(active_run_id, case.case_id),
                default_device_type=default_device_type,
                default_document_id=default_document_id,
            )
        else:
            result = CaseRunResult(
                answer=case.candidate_answer,
                evidence_images=case.candidate_images,
                metadata=case.candidate_metadata,
            )
        row = evaluate_case_output(
            case,
            result.answer,
            result.evidence_images,
            latency_ms=result.latency_ms,
            error=result.error,
            metadata=result.metadata,
        )
        _decorate_turn_row(row, case, 1)
        _append_trace_row(
            trace_rows,
            case=case,
            turn_index=1,
            row=row,
            result=result,
        )
        rows.append(row)
        print(
            f"{index}/{len(cases)} {case.case_id} final={row['final_pass']} "
            f"nugget={row['required_nugget_recall']} order={row['procedure_order_pass']} "
            f"image={row['image_pass']} latency_ms={row['latency_ms']}",
            flush=True,
        )
    return rows


_CASE_BOOLEAN_FIELDS = (
    "forbidden_claim_pass",
    "refusal_pass",
    "procedure_order_pass",
    "grounding_pass",
    "forbidden_image_pass",
    "image_order_pass",
    "step_image_binding_pass",
    "image_pass",
    "evidence_final_pass",
    "evidence_scope_isolation_pass",
    "evidence_source_pass",
    "evidence_answer_alignment_pass",
    "evidence_unsupported_completion_free",
    "evidence_partial_answer_correct",
    "evidence_conflict_handling_pass",
    "evidence_source_style_mode_pass",
    "evidence_refusal_integrity_pass",
    "evidence_style_proxy_pass",
    "evidence_source_mode_pass",
)


def _applicable_values(rows: Sequence[Mapping[str, Any]], key: str) -> list[Any]:
    return [row.get(key) for row in rows if row.get(key) not in (None, "")]


def aggregate_case_rows(
    cases: Sequence[MaintenanceEvalCase],
    turn_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Aggregate turn-level results into one stable row per dataset case."""

    by_case: dict[str, list[Mapping[str, Any]]] = {}
    for row in turn_rows:
        by_case.setdefault(str(row.get("case_id") or row.get("id") or ""), []).append(row)

    case_rows: list[dict[str, Any]] = []
    for case in cases:
        members = by_case.get(case.case_id, [])
        if not members:
            continue
        row = dict(members[0])
        row.update(
            {
                "id": case.case_id,
                "case_id": case.case_id,
                "turn_index": "",
                "turn_count": len(members),
                "request_count": len(members),
                "dataset_source": case.dataset_source,
                "group": case.group,
                "query": str(members[0].get("query") or "")
                if len(members) == 1
                else "\n".join(
                    f"T{index}: {member.get('query', '')}"
                    for index, member in enumerate(members, start=1)
                ),
                "generated_answer": str(members[0].get("generated_answer") or "")
                if len(members) == 1
                else "\n".join(
                    f"T{index}: {member.get('generated_answer', '')}"
                    for index, member in enumerate(members, start=1)
                ),
                "latency_ms": sum(int(member.get("latency_ms") or 0) for member in members),
                "error": "；".join(
                    str(member.get("error") or "")
                    for member in members
                    if str(member.get("error") or "")
                ),
                "final_pass": all(bool(member.get("final_pass")) for member in members),
                "evidence_score_available": any(
                    bool(member.get("evidence_score_available")) for member in members
                ),
            }
        )
        for key in _CASE_BOOLEAN_FIELDS:
            values = _applicable_values(members, key)
            row[key] = all(bool(value) for value in values) if values else ""
        for key in (
            "required_nugget_recall",
            "image_recall",
            "image_precision",
            "evidence_nugget_coverage_rate",
        ):
            values = [float(value) for value in _applicable_values(members, key)]
            row[key] = round(sum(values) / len(values), 6) if values else ""
        case_rows.append(row)
    return case_rows


def _metric_count(
    rows: Sequence[Mapping[str, Any]],
    key: str,
    *,
    predicate: Any | None = None,
    invert: bool = False,
) -> dict[str, Any]:
    applicable = [
        row
        for row in rows
        if (predicate is None or predicate(row)) and row.get(key) not in (None, "")
    ]
    numerator = sum(bool(row.get(key)) != invert for row in applicable)
    denominator = len(applicable)
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": round(numerator / denominator, 6) if denominator else None,
    }


def _nugget_coverage_count(turn_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    applicable = [row for row in turn_rows if int(row.get("claim_constraint_count") or 0) > 0]
    denominator = sum(int(row.get("claim_constraint_count") or 0) for row in applicable)
    numerator = sum(
        round(
            float(row.get("evidence_nugget_coverage_rate") or 0.0)
            * int(row.get("claim_constraint_count") or 0)
        )
        for row in applicable
    )
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": round(numerator / denominator, 6) if denominator else None,
    }


def summarize_results(
    case_rows: Sequence[Mapping[str, Any]],
    turn_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    summary = summarize_rows(case_rows)
    metric_counts = {
        "final_pass_rate": _metric_count(case_rows, "final_pass"),
        "forbidden_claim_pass_rate": _metric_count(case_rows, "forbidden_claim_pass"),
        "refusal_pass_rate": _metric_count(case_rows, "refusal_pass"),
        "procedure_order_pass_rate": _metric_count(
            case_rows,
            "procedure_order_pass",
            predicate=lambda row: bool(str(row.get("expected_step_order") or "").strip()),
        ),
        "image_pass_rate": _metric_count(
            case_rows,
            "image_pass",
            predicate=lambda row: bool(row.get("image_eval_required")),
        ),
        "forbidden_image_pass_rate": _metric_count(
            case_rows,
            "forbidden_image_pass",
            predicate=lambda row: bool(row.get("image_eval_required")),
        ),
        "evidence_nugget_coverage_rate": _nugget_coverage_count(turn_rows),
        "evidence_source_pass_rate": _metric_count(
            turn_rows,
            "evidence_source_pass",
            predicate=lambda row: int(row.get("claim_constraint_count") or 0) > 0,
        ),
        "answer_evidence_alignment_pass_rate": _metric_count(
            turn_rows,
            "evidence_answer_alignment_pass",
            predicate=lambda row: int(row.get("claim_constraint_count") or 0) > 0,
        ),
        "scope_isolation_pass_rate": _metric_count(
            turn_rows,
            "evidence_scope_isolation_pass",
            predicate=lambda row: row.get("expected_scope") == "out_of_scope",
        ),
        "unsupported_completion_free_rate": _metric_count(
            turn_rows,
            "evidence_unsupported_completion_free",
            predicate=lambda row: bool(row.get("has_forbidden_without_evidence")),
        ),
        "partial_answer_correct_rate": _metric_count(
            turn_rows,
            "evidence_partial_answer_correct",
            predicate=lambda row: row.get("expected_coverage_status") == "partial",
        ),
        "conflict_handling_pass_rate": _metric_count(
            turn_rows,
            "evidence_conflict_handling_pass",
            predicate=lambda row: row.get("expected_coverage_status") == "conflict",
        ),
        "refusal_integrity_pass_rate": _metric_count(
            turn_rows,
            "evidence_refusal_integrity_pass",
            predicate=lambda row: not bool(row.get("answerable"))
            or row.get("expected_coverage_status") == "unsupported",
        ),
        "fixed_template_rate": _metric_count(
            turn_rows,
            "evidence_fixed_template_detected",
            predicate=lambda row: row.get("source_request_mode") == "normal",
        ),
        "style_proxy_pass_rate": _metric_count(
            turn_rows,
            "evidence_style_proxy_pass",
            predicate=lambda row: row.get("source_request_mode") == "normal",
        ),
        "source_mode_pass_rate": _metric_count(turn_rows, "evidence_source_mode_pass"),
        "multi_turn_pass_rate": _metric_count(
            case_rows,
            "final_pass",
            predicate=lambda row: int(row.get("turn_count") or 0) > 1,
        ),
    }
    summary.update(
        {
            "case_count": len(case_rows),
            "turn_count": len(turn_rows),
            "request_count": sum(int(row.get("request_count") or 1) for row in turn_rows),
            "latency_total_ms": sum(int(row.get("latency_ms") or 0) for row in turn_rows),
            "avg_latency_ms": round(
                sum(int(row.get("latency_ms") or 0) for row in turn_rows) / len(turn_rows),
                6,
            )
            if turn_rows
            else 0.0,
            "metric_counts": metric_counts,
            "dataset_case_counts": {
                source: sum(row.get("dataset_source") == source for row in case_rows)
                for source in sorted({str(row.get("dataset_source") or "") for row in case_rows})
            },
        }
    )
    for metric_name, metric in metric_counts.items():
        summary[metric_name] = metric["rate"]
    summary["case_metrics"] = {
        key: metric_counts[key]
        for key in ("final_pass_rate", "multi_turn_pass_rate")
    }
    summary["turn_metrics"] = {
        key: value
        for key, value in metric_counts.items()
        if key not in summary["case_metrics"]
    }
    return summary


def write_rows(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "id",
        "case_id",
        "turn_index",
        "turn_count",
        "request_count",
        "dataset_source",
        "group",
        "query",
        "task_type",
        "intent_action",
        "target_section",
        "target_pages",
        "difficulty",
        "trap_type",
        "answerable",
        "expected_scope",
        "expected_coverage_status",
        "source_request_mode",
        "claim_constraint_count",
        "has_forbidden_without_evidence",
        "generated_answer",
        "required_nuggets",
        "matched_required_nuggets",
        "missing_required_nuggets",
        "required_nugget_recall",
        "optional_nuggets",
        "matched_optional_nuggets",
        "forbidden_claims",
        "hit_forbidden_claims",
        "forbidden_claim_pass",
        "refusal_pass",
        "expected_step_order",
        "step_positions",
        "procedure_order_pass",
        "grounding_pass",
        "expected_image_pages",
        "retrieved_image_pages",
        "forbidden_image_pages",
        "forbidden_image_hit_pages",
        "image_recall",
        "image_precision",
        "forbidden_image_pass",
        "image_order_pass",
        "step_image_binding_pass",
        "step_image_binding_failures",
        "image_pass",
        "image_eval_required",
        "evidence_score_available",
        "evidence_coverage_status",
        "evidence_final_pass",
        "evidence_scope_isolation_pass",
        "evidence_source_pass",
        "evidence_answer_alignment_pass",
        "evidence_nugget_coverage_rate",
        "evidence_unsupported_completion_free",
        "evidence_partial_answer_correct",
        "evidence_conflict_handling_pass",
        "evidence_source_style_mode_pass",
        "evidence_refusal_integrity_pass",
        "evidence_fixed_template_detected",
        "evidence_style_proxy_pass",
        "evidence_source_mode_pass",
        "evidence_diagnostics",
        "final_pass",
        "latency_ms",
        "error",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_summary(path: Path, summary: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def write_trace_rows(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_head() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return completed.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _sanitized_endpoint(endpoint: str) -> str:
    if not endpoint:
        return ""
    parsed = urlsplit(endpoint)
    if not parsed.hostname:
        return endpoint.split("?", 1)[0]
    host = parsed.hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def build_run_manifest(
    *,
    run_id: str,
    started_at: str,
    dataset_paths: Sequence[Path],
    cases: Sequence[MaintenanceEvalCase],
    turn_rows: Sequence[Mapping[str, Any]],
    mode: str,
    endpoint: str,
    timeout: int,
    default_device_type: str,
    default_document_id: str,
) -> dict[str, Any]:
    dataset_files = []
    for path in dataset_paths:
        resolved = path.resolve()
        dataset_files.append(
            {
                "name": path.name,
                "path": str(resolved),
                "sha256": _sha256_file(path),
                "size_bytes": path.stat().st_size,
                "case_count": sum(case.dataset_source == path.name for case in cases),
            }
        )
    model_config = {
        key: os.environ[key]
        for key in (
            "LLM_PROVIDER",
            "LLM_MODEL",
            "DASHSCOPE_MODEL",
            "LLM_TEMPERATURE",
            "LLM_TOP_P",
            "LLM_SEED",
        )
        if os.environ.get(key)
    }
    return {
        "run_id": run_id,
        "started_at": started_at,
        "git_commit": _git_head(),
        "mode": mode,
        "endpoint": _sanitized_endpoint(endpoint),
        "timeout_seconds": timeout,
        "default_device_type": default_device_type,
        "default_document_id": default_document_id,
        "case_count": len(cases),
        "turn_count": len(turn_rows),
        "request_count": len(turn_rows),
        "dataset_files": dataset_files,
        "model_config": model_config,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate maintenance manual end-to-end answer quality.")
    parser.add_argument("--dataset", action="append", required=True, help="JSONL dataset path; repeat for multiple files.")
    parser.add_argument("--mode", choices=("fixture", "api"), default="api", help="Run against fixture answers or HTTP API.")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8000/ai/chat", help="Chat API endpoint for --mode api.")
    parser.add_argument("--timeout", type=int, default=120, help="Per-case HTTP timeout in seconds.")
    parser.add_argument("--limit", type=int, default=0, help="Optional case limit.")
    parser.add_argument("--default-device-type", default="", help="Device scope used when a case omits device_type.")
    parser.add_argument("--default-document-id", default="", help="Document scope used when a case omits document_id.")
    parser.add_argument("--out-dir", default="evaluation/results", help="Output directory.")
    parser.add_argument("--result-name", default="maintenance_eval_result", help="Output file basename.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    dataset_paths = [Path(path) for path in args.dataset]
    cases = read_jsonl_datasets(dataset_paths)
    if args.limit and args.limit > 0:
        cases = cases[: args.limit]
    run_id = uuid.uuid4().hex
    started_at = datetime.now(timezone.utc).isoformat()
    trace_rows: list[dict[str, Any]] = []
    turn_rows = run_cases(
        cases,
        mode=args.mode,
        endpoint=args.endpoint,
        timeout=args.timeout,
        run_id=run_id,
        default_device_type=args.default_device_type,
        default_document_id=args.default_document_id,
        trace_rows=trace_rows,
    )
    case_rows = aggregate_case_rows(cases, turn_rows)
    summary = summarize_results(case_rows, turn_rows)
    run_manifest = build_run_manifest(
        run_id=run_id,
        started_at=started_at,
        dataset_paths=dataset_paths,
        cases=cases,
        turn_rows=turn_rows,
        mode=args.mode,
        endpoint=args.endpoint,
        timeout=args.timeout,
        default_device_type=args.default_device_type,
        default_document_id=args.default_document_id,
    )
    out_dir = Path(args.out_dir)
    write_rows(out_dir / f"{args.result_name}.csv", case_rows)
    write_rows(out_dir / f"{args.result_name}_turns.csv", turn_rows)
    write_trace_rows(out_dir / f"{args.result_name}_trace.jsonl", trace_rows)
    write_summary(out_dir / f"{args.result_name}_summary.json", summary)
    write_summary(out_dir / f"{args.result_name}_run.json", run_manifest)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
