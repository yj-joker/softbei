"""Command line evaluator for end-to-end RAG answer quality.

The evaluator reuses the retrieval evaluation dataset, runs a RAG answer
generation path, writes one CSV row per case, and stores aggregate answer
quality metrics in JSON.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from evaluation.rag_eval_cli import (
    EvalCase,
    RetrievedItem,
    evaluate_evidence_images,
    evaluate_image_expectations,
    first_evidence_hit_for_case,
    read_dataset,
    split_ids,
)


DEFAULT_TOP_K = 5
DEFAULT_MAX_TOKENS = 512

REFUSAL_HINTS = (
    "未找到",
    "没有找到",
    "未检索到",
    "资料不足",
    "依据不足",
    "未提供",
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

UNIT_HINTS = (
    "mm",
    "cm",
    "m",
    "n·m",
    "n.m",
    "nm",
    "mpa",
    "kpa",
    "pa",
    "v",
    "a",
    "kg",
    "g",
    "ml",
    "l",
    "rpm",
    "°c",
    "℃",
)

STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "be",
    "by",
    "for",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}

CJK_STOP_CHARS = set("的是了和与及或在时后前中内外应需要将用把对其该此为并按")
CJK_STOP_CHARS.update(set("如果如有被若则必须"))


@dataclass
class AnswerRunResult:
    generated_answer: str = ""
    retrieved_items: List[RetrievedItem] = field(default_factory=list)
    latency_ms: int = 0
    retrieval_ms: int = 0
    llm_ms: int = 0
    tools_used: List[str] = field(default_factory=list)
    error: str = ""


def _normalize_text(value: str) -> str:
    text = (value or "").lower()
    replacements = {
        "～": "-",
        "—": "-",
        "–": "-",
        "－": "-",
        "Ｎ": "n",
        "ｍ": "m",
        "Ｍ": "m",
        "．": ".",
        "，": ",",
        "。": ".",
        "：": ":",
        "；": ";",
        "（": "(",
        "）": ")",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _extract_numbers(value: str) -> List[str]:
    return re.findall(r"\d+(?:\.\d+)?", _normalize_text(value))


def _extract_units(value: str) -> List[str]:
    text = _normalize_text(value).replace(" ", "")
    found = []
    for unit in UNIT_HINTS:
        if unit in text:
            found.append(unit)
    return found


def _is_cjk_char(char: str) -> bool:
    return "\u4e00" <= char <= "\u9fff"


def _meaningful_terms(value: str) -> set[str]:
    text = _normalize_text(value)
    terms = {token for token in re.findall(r"[a-z0-9]+", text) if token not in STOP_WORDS}
    cjk_chars = [char for char in text if _is_cjk_char(char) and char not in CJK_STOP_CHARS]
    terms.update(cjk_chars)
    terms.update("".join(cjk_chars[index : index + 2]) for index in range(max(len(cjk_chars) - 1, 0)))
    return {term for term in terms if term}


def _contains_refusal(value: str) -> bool:
    text = _normalize_text(value)
    return any(hint in text for hint in REFUSAL_HINTS)


def _case_facts(case: EvalCase, field_name: str) -> List[str]:
    value = getattr(case, field_name, [])
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return split_ids(str(value or ""))


def _fact_matched(fact: str, answer: str) -> bool:
    fact_text = _normalize_text(fact)
    answer_text = _normalize_text(answer)
    if not fact_text:
        return True
    if fact_text in answer_text:
        return True
    if "压力" in fact_text and "升高" in fact_text and "压力" in answer_text and "高" in answer_text:
        return True

    fact_numbers = _extract_numbers(fact)
    if fact_numbers:
        answer_numbers = set(_extract_numbers(answer))
        if not all(number in answer_numbers for number in fact_numbers):
            return False
        fact_units = _extract_units(fact)
        answer_units = _extract_units(answer)
        return not fact_units or any(unit in answer_units for unit in fact_units)

    fact_terms = _meaningful_terms(fact)
    if not fact_terms:
        return False
    answer_terms = _meaningful_terms(answer)
    overlap = len(fact_terms & answer_terms) / len(fact_terms)
    return overlap >= 0.75


def _matched_facts(facts: Sequence[str], answer: str) -> List[str]:
    return [fact for fact in facts if _fact_matched(fact, answer)]


def _score_answer(case: EvalCase, generated_answer: str) -> tuple[float, str]:
    answer = generated_answer or ""
    if not answer.strip():
        return 0.0, "模型没有生成有效回答"

    if not case.answerable:
        if _contains_refusal(answer):
            return 1.0, "无答案问题正确拒答"
        return 0.0, "无答案问题给出了确定性答案"

    required_facts = _case_facts(case, "required_facts")
    if required_facts:
        matched = _matched_facts(required_facts, answer)
        if len(matched) == len(required_facts):
            return 1.0, "必答事实全部命中"
        if matched:
            return 0.5, f"必答事实部分命中：{len(matched)}/{len(required_facts)}"
        return 0.0, "必答事实未命中"

    golden = case.golden_answer or ""
    if not golden.strip():
        return 0.0, "可回答问题缺少 golden_answer，无法自动评分"

    golden_numbers = _extract_numbers(golden)
    answer_numbers = set(_extract_numbers(answer))
    if golden_numbers:
        matched_numbers = sum(1 for number in golden_numbers if number in answer_numbers)
        golden_units = _extract_units(golden)
        answer_units = _extract_units(answer)
        unit_ok = not golden_units or any(unit in answer_units for unit in golden_units)
        if matched_numbers == len(golden_numbers) and unit_ok:
            return 1.0, "关键数值和单位匹配"
        if matched_numbers:
            return 0.5, "只匹配了部分关键数值或单位"
        return 0.0, "关键数值未匹配"

    golden_terms = _meaningful_terms(golden)
    if not golden_terms:
        return 0.0, "golden_answer 缺少可比对关键词"

    answer_terms = _meaningful_terms(answer)
    overlap = len(golden_terms & answer_terms) / len(golden_terms)
    if overlap >= 0.75:
        return 1.0, f"关键词覆盖率 {overlap:.2f}"
    if overlap >= 0.35:
        return 0.5, f"关键词覆盖率 {overlap:.2f}，答案不完整"
    return 0.0, f"关键词覆盖率 {overlap:.2f}，答案不匹配"


def _failure_type(case: EvalCase, score: float, retrieval_hit_top5: bool, hallucination: bool) -> str:
    if score >= 1.0 and (retrieval_hit_top5 or not case.answerable or not case.golden_chunk_ids):
        return "pass"
    if not case.answerable and hallucination:
        return "no_answer_false_positive"
    if score >= 1.0 and not retrieval_hit_top5:
        return "retrieval_miss_answer_correct"
    if case.answerable and not retrieval_hit_top5:
        return "retrieval_miss"
    if score == 0.5:
        return "partial_answer"
    return "answer_mismatch"


def build_answer_eval_row(
    case: EvalCase,
    generated_answer: str,
    retrieved_items: Sequence[RetrievedItem],
    latency_ms: int = 0,
    retrieval_ms: int = 0,
    llm_ms: int = 0,
    tools_used: Sequence[str] | None = None,
    error: str = "",
    top_k: int = DEFAULT_TOP_K,
    evidence_images: Sequence[Mapping[str, Any]] | None = None,
) -> Dict[str, Any]:
    items = list(retrieved_items)
    returned_ids = [item.chunk_id for item in items]
    rank, matched_evidence_key = first_evidence_hit_for_case(case, items)
    retrieval_hit_top5 = bool(rank and rank <= top_k)
    score, judge_reason = _score_answer(case, generated_answer)
    required_facts = _case_facts(case, "required_facts")
    optional_facts = _case_facts(case, "optional_facts")
    matched_required_facts = _matched_facts(required_facts, generated_answer)
    matched_optional_facts = _matched_facts(optional_facts, generated_answer)
    missing_required_facts = [fact for fact in required_facts if fact not in matched_required_facts]
    missing_optional_facts = [fact for fact in optional_facts if fact not in matched_optional_facts]
    answer_pass = score >= 1.0
    hallucination = (not case.answerable and not answer_pass) or (
        case.answerable and not retrieval_hit_top5 and score < 1.0
    )
    grounded_pass = answer_pass and (retrieval_hit_top5 or not case.answerable or not case.golden_chunk_ids)
    failure_type = _failure_type(case, score, retrieval_hit_top5, hallucination)

    if error and failure_type == "answer_mismatch":
        failure_type = "generation_error"
        judge_reason = f"生成链路失败：{error}"

    row = {
        "id": case.case_id,
        "question": case.question,
        "question_type": case.question_type,
        "difficulty": case.difficulty,
        "answerable": case.answerable,
        "golden_answer": case.golden_answer,
        "required_facts": ";".join(required_facts),
        "optional_facts": ";".join(optional_facts),
        "golden_chunk_ids": ";".join(case.golden_chunk_ids),
        "golden_evidence_keys": ";".join(case.golden_evidence_keys),
        "retrieved_chunk_ids": ";".join(returned_ids),
        "matched_evidence_key": matched_evidence_key,
        "retrieval_hit_top5": retrieval_hit_top5,
        "retrieval_rank": rank or "",
        "generated_answer": generated_answer,
        "matched_required_facts": ";".join(matched_required_facts),
        "missing_required_facts": ";".join(missing_required_facts),
        "matched_optional_facts": ";".join(matched_optional_facts),
        "missing_optional_facts": ";".join(missing_optional_facts),
        "answer_score": score,
        "answer_pass": answer_pass,
        "grounded_pass": grounded_pass,
        "hallucination": hallucination,
        "failure_type": failure_type,
        "judge_reason": judge_reason,
        "score_source": "required_facts" if required_facts else "local_rule",
        "latency_ms": latency_ms,
        "retrieval_ms": retrieval_ms,
        "llm_ms": llm_ms,
        "tools_used": ";".join(tools_used or []),
        "error": error,
        "top_k": top_k,
    }
    if evidence_images is not None:
        row.update(evaluate_evidence_images(case, evidence_images))
    else:
        row.update(evaluate_image_expectations(case, items))
    return row


def _rate(rows: Sequence[Mapping[str, Any]], field: str) -> float:
    if not rows:
        return 0.0
    return round(sum(1 for row in rows if _as_bool(row.get(field))) / len(rows), 6)


def _avg(rows: Sequence[Mapping[str, Any]], field: str) -> float:
    if not rows:
        return 0.0
    return round(sum(float(row.get(field) or 0.0) for row in rows) / len(rows), 6)


def _summarize_group(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    return {
        "case_count": len(rows),
        "answer_accuracy": _rate(rows, "answer_pass"),
        "avg_score": _avg(rows, "answer_score"),
        "grounded_rate": _rate(rows, "grounded_pass"),
        "hallucination_rate": _rate(rows, "hallucination"),
    }


def summarize_answer_eval_rows(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    all_rows = list(rows)
    answerable_rows = [row for row in all_rows if _as_bool(row.get("answerable"))]
    no_answer_rows = [row for row in all_rows if not _as_bool(row.get("answerable"))]
    image_rows = [row for row in answerable_rows if _as_bool(row.get("image_eval_required"))]
    by_type: Dict[str, List[Mapping[str, Any]]] = {}
    for row in all_rows:
        question_type = str(row.get("question_type") or "unknown")
        by_type.setdefault(question_type, []).append(row)

    return {
        "case_count": len(all_rows),
        "answerable_case_count": len(answerable_rows),
        "no_answer_case_count": len(no_answer_rows),
        "answer_accuracy": _rate(all_rows, "answer_pass"),
        "answerable_accuracy": _rate(answerable_rows, "answer_pass"),
        "avg_score": _avg(all_rows, "answer_score"),
        "answerable_avg_score": _avg(answerable_rows, "answer_score"),
        "grounded_rate": _rate(all_rows, "grounded_pass"),
        "hallucination_rate": _rate(all_rows, "hallucination"),
        "no_answer_correct_rate": _rate(no_answer_rows, "answer_pass"),
        "image_case_count": len(image_rows),
        "image_pass_rate": _rate(image_rows, "image_pass"),
        "avg_image_recall": _avg(image_rows, "image_recall"),
        "avg_image_precision": _avg(image_rows, "image_precision"),
        "by_question_type": {
            question_type: _summarize_group(group_rows)
            for question_type, group_rows in sorted(by_type.items())
        },
    }


def _item_to_dict(item: Any) -> Dict[str, Any]:
    if hasattr(item, "model_dump"):
        return item.model_dump()
    if isinstance(item, dict):
        return item
    return {
        "id": getattr(item, "id", ""),
        "score": getattr(item, "score", None),
        "content": getattr(item, "content", ""),
        "metadata": dict(getattr(item, "metadata", None) or {}),
    }


def _to_retrieved_item(item: Any) -> RetrievedItem:
    data = _item_to_dict(item)
    return RetrievedItem(
        chunk_id=str(data.get("id") or data.get("doc_id") or ""),
        score=data.get("score"),
        content=data.get("content") or data.get("text") or "",
        metadata=dict(data.get("metadata") or {}),
    )


def _format_evidence_item(item: RetrievedItem, index: int) -> str:
    metadata = item.metadata or {}
    page = metadata.get("page_number") or metadata.get("page")
    chunk_type = metadata.get("chunk_type") or metadata.get("source_chunk_type") or ""
    title = metadata.get("section_title") or metadata.get("image_title") or ""
    page_text = f", page={page}" if page else ""
    type_text = f", type={chunk_type}" if chunk_type else ""
    title_text = f", title={title}" if title else ""
    return (
        f"[evidence {index}] chunk_id={item.chunk_id}, score={item.score}{page_text}{type_text}{title_text}\n"
        f"{item.content}"
    )


def _evidence_group_name(item: RetrievedItem, index: int) -> str:
    if index == 1:
        return "核心命中证据"
    metadata = item.metadata or {}
    chunk_type = str(metadata.get("chunk_type") or metadata.get("source_chunk_type") or "")
    if chunk_type in {"table", "table_row"}:
        return "表格参数证据"
    if chunk_type in {"image", "image_summary"}:
        return "图片证据"
    if metadata.get("expanded_from") or metadata.get("expanded_reason") or metadata.get("source_id"):
        return "相邻上下文"
    return "补充证据"


def build_structured_evidence_text(evidence_items: Sequence[RetrievedItem]) -> str:
    groups: Dict[str, List[str]] = {
        "核心命中证据": [],
        "表格参数证据": [],
        "图片证据": [],
        "相邻上下文": [],
        "补充证据": [],
    }
    for index, item in enumerate(evidence_items, start=1):
        group_name = _evidence_group_name(item, index)
        groups.setdefault(group_name, []).append(_format_evidence_item(item, index))

    sections = []
    for group_name, lines in groups.items():
        if not lines:
            continue
        sections.append(f"{group_name}：\n" + "\n\n".join(lines))
    return "\n\n".join(sections)


def answer_contract_for_case(case: EvalCase) -> str:
    question_type = (case.question_type or "").strip().lower()
    contracts = {
        "inspection": (
            "请按以下结构回答。没有证据的项目写“资料中未找到明确依据”。\n"
            "检查对象：\n"
            "正常标准：\n"
            "异常表现：\n"
            "处理方式："
        ),
        "procedure": (
            "请按以下结构回答。只写证据中出现的步骤，不要补造步骤。\n"
            "前置条件：\n"
            "操作步骤：\n"
            "安全注意："
        ),
        "torque": (
            "请按以下结构回答。问题只问其中一项时，可以只答对应项。\n"
            "拧紧对象：\n"
            "扭矩值：\n"
            "是否分次：\n"
            "顺序或注意事项："
        ),
        "safety": (
            "请按以下结构回答。\n"
            "注意事项：\n"
            "原因或风险：\n"
            "正确做法："
        ),
        "spec": (
            "请直接回答标准值、范围、单位和适用条件。不要省略单位。"
        ),
        "image": (
            "请说明应返回哪张图或哪类图示，并尽量指出图示对应章节、页码或部件名称。"
        ),
        "no_answer": (
            "如果证据没有明确答案，只能回答“资料中未找到明确依据”。"
        ),
    }
    return contracts.get(question_type, "请直接回答问题，并只保留证据支持的内容。")


def build_rag_answer_messages(case: EvalCase, evidence_items: Sequence[RetrievedItem]) -> List[Dict[str, str]]:
    evidence_text = build_structured_evidence_text(evidence_items)
    if not evidence_text.strip():
        evidence_text = "未检索到可用证据。"
    answer_contract = answer_contract_for_case(case)
    return [
        {
            "role": "system",
            "content": (
                "你是维修手册 RAG 问答评测中的被测回答模型。"
                "必须只根据给定知识库证据回答。"
                "如果证据不足或手册没有明确说明，必须回答资料中未找到明确依据。"
                "不要编造参数、扭矩、方向、步骤、图片编号或页码。"
                "用中文简洁回答。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"问题：{case.question}\n\n"
                f"问题类型：{case.question_type or 'unknown'}\n\n"
                f"回答要求：\n{answer_contract}\n\n"
                f"知识库证据：\n{evidence_text}\n\n"
                "请直接给出答案。"
            ),
        },
    ]


async def run_rag_answer_case(case: EvalCase, top_k: int, max_tokens: int) -> AnswerRunResult:
    from services.llm.service import get_llm_service
    from tools.knowledge_retrieval_tool import get_knowledge_retrieval_tool

    total_t0 = time.perf_counter()
    retrieval_t0 = time.perf_counter()
    retrieval = await get_knowledge_retrieval_tool().run(query=case.question, top_k=top_k, **case.filters)
    retrieval_ms = int((time.perf_counter() - retrieval_t0) * 1000)
    if not retrieval.success:
        message = retrieval.error.message if retrieval.error else "unknown retrieval error"
        return AnswerRunResult(
            latency_ms=int((time.perf_counter() - total_t0) * 1000),
            retrieval_ms=retrieval_ms,
            tools_used=["knowledge_retrieval"],
            error=message,
        )

    retrieved_items = [_to_retrieved_item(item) for item in retrieval.data or []]
    llm_t0 = time.perf_counter()
    response = await get_llm_service().chat(
        messages=build_rag_answer_messages(case, retrieved_items),
        temperature=0.1,
        max_tokens=max_tokens,
    )
    llm_ms = int((time.perf_counter() - llm_t0) * 1000)
    return AnswerRunResult(
        generated_answer=str(response.get("content", "")),
        retrieved_items=retrieved_items,
        latency_ms=int((time.perf_counter() - total_t0) * 1000),
        retrieval_ms=retrieval_ms,
        llm_ms=llm_ms,
        tools_used=["knowledge_retrieval"],
    )


async def run_agent_answer_case(case: EvalCase, top_k: int, max_tokens: int) -> AnswerRunResult:
    from agents.base_agent import AgentInput
    from agents.fix_agent import get_fix_agent

    del top_k, max_tokens
    total_t0 = time.perf_counter()
    output = await get_fix_agent().run_with_react(
        AgentInput(
            user_message=case.question,
            session_id=f"answer-eval-{case.case_id}",
            context={"force_react": True, "disable_fast_path": True},
        )
    )
    return AnswerRunResult(
        generated_answer=output.message,
        retrieved_items=_extract_retrieved_items_from_agent_metadata(output.metadata),
        latency_ms=output.latency_ms or int((time.perf_counter() - total_t0) * 1000),
        tools_used=output.tools_used,
    )


def _extract_retrieved_items_from_agent_metadata(metadata: Mapping[str, Any]) -> List[RetrievedItem]:
    items: List[RetrievedItem] = []
    trace = metadata.get("react_trace") or []
    for step in trace:
        step_data = _item_to_dict(step)
        for tool_call in step_data.get("tool_calls") or []:
            call_data = _item_to_dict(tool_call)
            if call_data.get("name") != "knowledge_retrieval":
                continue
            result_data = call_data.get("result_data") or call_data.get("data") or call_data.get("result")
            if isinstance(result_data, dict) and isinstance(result_data.get("data"), list):
                result_data = result_data["data"]
            if isinstance(result_data, list):
                items.extend(_to_retrieved_item(item) for item in result_data)
    return items


async def run_answer_eval_cases(
    cases: Sequence[EvalCase],
    mode: str,
    top_k: int,
    max_tokens: int,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        try:
            if mode == "agent":
                result = await run_agent_answer_case(case, top_k=top_k, max_tokens=max_tokens)
            else:
                result = await run_rag_answer_case(case, top_k=top_k, max_tokens=max_tokens)
        except Exception as exc:
            result = AnswerRunResult(error=str(exc))
        row = build_answer_eval_row(
            case=case,
            generated_answer=result.generated_answer,
            retrieved_items=result.retrieved_items,
            latency_ms=result.latency_ms,
            retrieval_ms=result.retrieval_ms,
            llm_ms=result.llm_ms,
            tools_used=result.tools_used,
            error=result.error,
            top_k=top_k,
        )
        rows.append(row)
        print(
            f"{index}/{len(cases)} {case.case_id} score={row['answer_score']} "
            f"pass={row['answer_pass']} hit_top5={row['retrieval_hit_top5']} "
            f"latency_ms={row['latency_ms']}",
            flush=True,
        )
    return rows


def write_answer_rows(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "id",
        "question",
        "question_type",
        "difficulty",
        "answerable",
        "golden_answer",
        "required_facts",
        "optional_facts",
        "golden_chunk_ids",
        "golden_evidence_keys",
        "retrieved_chunk_ids",
        "matched_evidence_key",
        "retrieval_hit_top5",
        "retrieval_rank",
        "generated_answer",
        "matched_required_facts",
        "missing_required_facts",
        "matched_optional_facts",
        "missing_optional_facts",
        "answer_score",
        "answer_pass",
        "grounded_pass",
        "hallucination",
        "failure_type",
        "judge_reason",
        "score_source",
        "latency_ms",
        "retrieval_ms",
        "llm_ms",
        "tools_used",
        "error",
        "top_k",
        "expected_image_ids",
        "expected_image_pages",
        "forbidden_image_pages",
        "expected_image_count_min",
        "expected_image_count_max",
        "retrieved_image_ids",
        "retrieved_image_pages",
        "image_recall",
        "image_precision",
        "image_count_pass",
        "forbidden_image_pass",
        "image_pass",
        "missing_expected_image_ids",
        "unexpected_image_ids",
        "image_eval_required",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_summary(path: Path, summary: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate generated RAG answers against a CSV dataset.")
    parser.add_argument("--dataset", required=True, help="CSV file with id, question, golden_answer and golden_chunk_ids.")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K, help="Evidence count requested from retrieval.")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS, help="Max LLM output tokens per case.")
    parser.add_argument("--mode", choices=("rag", "agent"), default="rag", help="Answer generation path.")
    parser.add_argument("--limit", type=int, default=0, help="Optional case limit for smoke tests.")
    parser.add_argument("--out-dir", default="evaluation/results", help="Directory for result CSV/JSON files.")
    parser.add_argument("--result-name", default="answer_eval_result", help="Base name for output files.")
    return parser


async def async_main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cases = read_dataset(Path(args.dataset))
    if args.limit and args.limit > 0:
        cases = cases[: args.limit]
    rows = await run_answer_eval_cases(
        cases=cases,
        mode=args.mode,
        top_k=args.top_k,
        max_tokens=args.max_tokens,
    )
    summary = summarize_answer_eval_rows(rows)
    out_dir = Path(args.out_dir)
    write_answer_rows(out_dir / f"{args.result_name}.csv", rows)
    write_summary(out_dir / f"{args.result_name}_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
