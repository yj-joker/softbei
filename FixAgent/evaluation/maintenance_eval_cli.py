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
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence


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
class MaintenanceEvalCase:
    case_id: str
    query: str
    task_type: str = ""
    intent_action: str = ""
    target_section: str = ""
    target_pages: list[int] = field(default_factory=list)
    answerable: bool = True
    required_nuggets: list[str] = field(default_factory=list)
    optional_nuggets: list[str] = field(default_factory=list)
    forbidden_claims: list[str] = field(default_factory=list)
    expected_step_order: list[str] = field(default_factory=list)
    expected_images: list[dict[str, Any]] = field(default_factory=list)
    expected_image_order: list[int] = field(default_factory=list)
    step_image_mapping: list[dict[str, Any]] = field(default_factory=list)
    forbidden_images: list[dict[str, Any]] = field(default_factory=list)
    gold_evidence: list[dict[str, Any]] = field(default_factory=list)
    difficulty: str = ""
    trap_type: list[str] = field(default_factory=list)
    candidate_answer: str = ""
    candidate_images: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CaseRunResult:
    answer: str = ""
    evidence_images: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: int = 0
    error: str = ""


def _as_int_list(value: Any) -> list[int]:
    if value is None:
        return []
    if isinstance(value, list):
        values = value
    else:
        values = [value]
    parsed: list[int] = []
    for item in values:
        try:
            if str(item).strip() != "":
                parsed.append(int(item))
        except (TypeError, ValueError):
            continue
    return parsed


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [item.strip() for item in re.split(r"[;|]", text) if item.strip()]


def _as_dict_list(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    return []


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "是", "可回答"}


def _case_from_dict(data: Mapping[str, Any]) -> MaintenanceEvalCase:
    return MaintenanceEvalCase(
        case_id=str(data.get("case_id") or data.get("id") or "").strip(),
        query=str(data.get("query") or data.get("question") or "").strip(),
        task_type=str(data.get("task_type") or "").strip(),
        intent_action=str(data.get("intent_action") or "").strip(),
        target_section=str(data.get("target_section") or "").strip(),
        target_pages=_as_int_list(data.get("target_pages")),
        answerable=_as_bool(data.get("answerable"), default=True),
        required_nuggets=_as_str_list(data.get("required_nuggets")),
        optional_nuggets=_as_str_list(data.get("optional_nuggets")),
        forbidden_claims=_as_str_list(data.get("forbidden_claims")),
        expected_step_order=_as_str_list(data.get("expected_step_order")),
        expected_images=_as_dict_list(data.get("expected_images")),
        expected_image_order=_as_int_list(data.get("expected_image_order")),
        step_image_mapping=_as_dict_list(data.get("step_image_mapping")),
        forbidden_images=_as_dict_list(data.get("forbidden_images")),
        gold_evidence=_as_dict_list(data.get("gold_evidence")),
        difficulty=str(data.get("difficulty") or "").strip(),
        trap_type=_as_str_list(data.get("trap_type")),
        candidate_answer=str(data.get("candidate_answer") or ""),
        candidate_images=_as_dict_list(data.get("candidate_images")),
    )


def read_jsonl_dataset(path: Path) -> list[MaintenanceEvalCase]:
    cases: list[MaintenanceEvalCase] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            data = json.loads(text)
            case = _case_from_dict(data)
            if not case.case_id:
                raise ValueError(f"{path}:{line_no} missing case_id")
            if not case.query:
                raise ValueError(f"{path}:{line_no} missing query")
            cases.append(case)
    return cases


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


def evaluate_case_output(
    case: MaintenanceEvalCase,
    generated_answer: str,
    evidence_images: Sequence[Mapping[str, Any]] | None = None,
    *,
    latency_ms: int = 0,
    error: str = "",
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
    final_pass = bool(
        grounding_pass
        and refusal_pass
        and procedure_order_pass
        and image_metrics["image_pass"]
        and not error
    )

    return {
        "id": case.case_id,
        "query": case.query,
        "task_type": case.task_type,
        "intent_action": case.intent_action,
        "target_section": case.target_section,
        "target_pages": ";".join(str(page) for page in case.target_pages),
        "difficulty": case.difficulty,
        "trap_type": ";".join(case.trap_type),
        "answerable": case.answerable,
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


def _chat_api_request(endpoint: str, case: MaintenanceEvalCase, timeout: int) -> CaseRunResult:
    payload = {
        "session_id": f"maintenance-eval-{case.case_id}",
        "message": case.query,
        "stream": False,
    }
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
            latency_ms=latency_ms,
        )
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return CaseRunResult(latency_ms=int((time.perf_counter() - started) * 1000), error=str(exc))


def run_cases(
    cases: Sequence[MaintenanceEvalCase],
    *,
    mode: str,
    endpoint: str,
    timeout: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        if mode == "api":
            result = _chat_api_request(endpoint, case, timeout)
        else:
            result = CaseRunResult(answer=case.candidate_answer, evidence_images=case.candidate_images)
        row = evaluate_case_output(
            case,
            result.answer,
            result.evidence_images,
            latency_ms=result.latency_ms,
            error=result.error,
        )
        rows.append(row)
        print(
            f"{index}/{len(cases)} {case.case_id} final={row['final_pass']} "
            f"nugget={row['required_nugget_recall']} order={row['procedure_order_pass']} "
            f"image={row['image_pass']} latency_ms={row['latency_ms']}",
            flush=True,
        )
    return rows


def write_rows(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "id",
        "query",
        "task_type",
        "intent_action",
        "target_section",
        "target_pages",
        "difficulty",
        "trap_type",
        "answerable",
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate maintenance manual end-to-end answer quality.")
    parser.add_argument("--dataset", required=True, help="JSONL dataset path.")
    parser.add_argument("--mode", choices=("fixture", "api"), default="api", help="Run against fixture answers or HTTP API.")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8000/ai/chat", help="Chat API endpoint for --mode api.")
    parser.add_argument("--timeout", type=int, default=120, help="Per-case HTTP timeout in seconds.")
    parser.add_argument("--limit", type=int, default=0, help="Optional case limit.")
    parser.add_argument("--out-dir", default="evaluation/results", help="Output directory.")
    parser.add_argument("--result-name", default="maintenance_eval_result", help="Output file basename.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cases = read_jsonl_dataset(Path(args.dataset))
    if args.limit and args.limit > 0:
        cases = cases[: args.limit]
    rows = run_cases(cases, mode=args.mode, endpoint=args.endpoint, timeout=args.timeout)
    summary = summarize_rows(rows)
    out_dir = Path(args.out_dir)
    write_rows(out_dir / f"{args.result_name}.csv", rows)
    write_summary(out_dir / f"{args.result_name}_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
