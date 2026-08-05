"""Fixed 20-case live evaluation for clarification and scoped retrieval."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx
from dotenv import load_dotenv


INSUFFICIENT_MARKERS = (
    "证据不足",
    "无法可靠确认",
    "当前资料未说明",
    "没有找到足以",
    "未找到足以",
)


@dataclass(frozen=True)
class Case:
    case_id: int
    query: str
    expected: str
    required: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()
    second_reply: str = "A"
    candidate_terms: tuple[str, ...] = ()


CASES = (
    Case(1, "M6×60六角法兰面螺栓的扭矩是多少？", "clarify", required=("5±1",), forbidden=("12±1.5",), candidate_terms=("右曲轴箱盖", "离合器、机油泵")),
    Case(2, "右曲轴箱盖装配部件清单中M6×60螺栓的预紧力是多少？", "answer", required=("右曲轴箱盖", "M6×60", "5±1")),
    Case(3, "离合器、机油泵装配零件清单中M6×60螺栓的扭矩是多少？", "answer", required=("离合器", "机油泵", "M6×60", "12±1.5")),
    Case(4, "传动装置装配中M6×30螺栓的预紧力和校正力分别是多少？", "answer", required=("传动装置", "M6×30", "预紧力", "校正力", "5±1", "12±1.5")),
    Case(5, "进气门和排气门的标准间隙分别是多少？", "answer", required=("进气门", "排气门", "0.13", "0.20", "0.30")),
    Case(6, "气门间隙是多少？", "answer", required=("进气门", "排气门", "0.13", "0.20", "0.30")),
    Case(7, "摩托车发动机冒蓝烟还烧机油，怎么回事？", "clarify", second_reply="B"),
    Case(8, "摩托车发动机有异响，可能是什么原因？", "clarify"),
    Case(9, "摩托车发动机中M6×60螺栓的扭矩是多少？", "clarify", required=("5±1",), forbidden=("12±1.5",), candidate_terms=("右曲轴箱盖", "离合器、机油泵")),
    Case(10, "8.1传动装置装配部件清单中M6×30螺栓的预紧力和校正力分别是多少？", "answer", required=("传动装置", "M6×30", "预紧力", "校正力", "5±1", "12±1.5")),
    Case(11, "这个曲轴箱盖的拆卸步骤是什么？", "clarify"),
    Case(12, "M6×30法兰面螺栓的扭矩是多少？", "clarify"),
    Case(13, "φ8×14空心定位销一共需要几个？", "clarify"),
    Case(14, "止推垫圈怎么安装？", "clarify"),
    Case(15, "水泵应该怎样拆装？", "answer", required=("水泵",)),
    Case(16, "气门弹簧哪一端朝上安装？", "answer", required=("气门弹簧",)),
    Case(17, "凸轮轴正时链轮的标记怎么对齐？", "answer", required=("正时", "链轮", "标记")),
    Case(18, "发动机冷机容易熄火，热机正常，先查哪里？", "clarify"),
    Case(19, "摩托车发动机冒蓝烟，但不确定出现时机", "clarify", second_reply="B"),
    Case(20, "叉车发动机的气门间隙是多少？", "safe_no_exact"),
)


def _pending(response: dict[str, Any]) -> dict[str, Any]:
    metadata = response.get("metadata")
    value = metadata.get("pending_clarification") if isinstance(metadata, dict) else None
    return value if isinstance(value, dict) else {}


def _route(response: dict[str, Any]) -> str:
    metadata = response.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    return str(metadata.get("route_action") or metadata.get("execution_mode") or "")


def _is_clarifying(response: dict[str, Any]) -> bool:
    pending = _pending(response)
    status = str(pending.get("status") or "").lower()
    return "clarif" in _route(response).lower() or status in {"awaiting", "awaiting_answer", "reasked"}


def _candidates(response: dict[str, Any]) -> list[dict[str, Any]]:
    pending = _pending(response)
    for key in ("candidates", "alternatives", "options"):
        values = pending.get(key)
        if isinstance(values, list) and values:
            return [item for item in values if isinstance(item, dict)]
    return []


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value or "").replace("·", "").replace("N.m", "Nm")


def _contains_all(message: str, terms: tuple[str, ...]) -> bool:
    compact = _compact(message)
    return all(_compact(term) in compact for term in terms)


def _contains_none(message: str, terms: tuple[str, ...]) -> bool:
    compact = _compact(message)
    return all(_compact(term) not in compact for term in terms)


def _has_insufficient_answer(message: str) -> bool:
    return any(marker in message for marker in INSUFFICIENT_MARKERS)


def _answer_pass(case: Case, response: dict[str, Any]) -> tuple[bool, list[str]]:
    message = str(response.get("message") or "")
    tools = response.get("tools_used") or []
    reasons: list[str] = []
    if _is_clarifying(response):
        reasons.append("unexpected_clarification")
    if "knowledge_retrieval" not in tools:
        reasons.append("missing_knowledge_retrieval")
    if _has_insufficient_answer(message):
        reasons.append("insufficient_answer")
    if not _contains_all(message, case.required):
        reasons.append("missing_required_content")
    if not _contains_none(message, case.forbidden):
        reasons.append("contains_forbidden_content")
    return not reasons, reasons


def _safe_fallback_pass(response: dict[str, Any]) -> tuple[bool, list[str]]:
    message = str(response.get("message") or "")
    reasons: list[str] = []
    if _is_clarifying(response):
        reasons.append("unexpected_clarification")
    if "手册第" in message or "摩托车发动机维修手册" in message:
        reasons.append("wrong_manual_source")
    if re.search(r"\d+(?:\.\d+)?\s*(?:～|~|至|-)\s*\d+(?:\.\d+)?\s*mm", message):
        reasons.append("unsafe_exact_parameter")
    if not any(marker in message for marker in ("AI", "通用知识", "仅供参考", "没有", "未找到")):
        reasons.append("missing_ai_source_notice")
    return not reasons, reasons


def _clarification_first_pass(case: Case, response: dict[str, Any]) -> tuple[bool, list[str]]:
    message = str(response.get("message") or "")
    candidates = _candidates(response)
    candidate_text = " ".join(str(item.get("label") or item.get("value") or "") for item in candidates)
    reasons: list[str] = []
    if not _is_clarifying(response):
        reasons.append("missing_clarification")
    if len(candidates) < 2:
        reasons.append("insufficient_candidates")
    if response.get("tools_used"):
        reasons.append("clarification_used_answer_tools")
    if not any(marker in message for marker in ("请确认", "请选择", "请回复", "哪一", "是否", "更符合")):
        reasons.append("question_not_visible")
    if case.candidate_terms and not _contains_all(candidate_text, case.candidate_terms):
        reasons.append("wrong_candidate_dimension")
    for item in candidates:
        constraints = item.get("constraints")
        if not isinstance(constraints, dict) or not constraints.get("document_id"):
            reasons.append("candidate_missing_document_scope")
            break
        if not (constraints.get("section_id") or constraints.get("allowed_section_ids") or constraints.get("allowed_evidence_refs")):
            reasons.append("candidate_missing_evidence_scope")
            break
    return not reasons, reasons


def _clarification_second_pass(case: Case, response: dict[str, Any]) -> tuple[bool, list[str]]:
    message = str(response.get("message") or "")
    reasons: list[str] = []
    if _is_clarifying(response):
        reasons.append("clarification_not_resolved")
    if _has_insufficient_answer(message):
        reasons.append("resolved_to_insufficient_answer")
    if not message.strip():
        reasons.append("empty_second_answer")
    if case.required and not _contains_all(message, case.required):
        reasons.append("second_answer_missing_required_content")
    if case.forbidden and not _contains_none(message, case.forbidden):
        reasons.append("second_answer_contains_forbidden_content")
    return not reasons, reasons


async def _send(client: httpx.AsyncClient, session_id: str, message: str) -> dict[str, Any]:
    response = await client.post(
        "/ai/chat",
        json={"session_id": session_id, "message": message, "mode": "chat", "stream": False},
    )
    response.raise_for_status()
    return response.json()


async def _run_case(client: httpx.AsyncClient, semaphore: asyncio.Semaphore, case: Case) -> dict[str, Any]:
    session_id = f"clarification-e2e-{case.case_id}-{uuid.uuid4().hex[:10]}"
    async with semaphore:
        started = time.perf_counter()
        try:
            first = await _send(client, session_id, case.query)
            second: dict[str, Any] | None = None
            reasons: list[str]
            if case.expected == "answer":
                passed, reasons = _answer_pass(case, first)
            elif case.expected == "safe_no_exact":
                passed, reasons = _safe_fallback_pass(first)
            else:
                first_passed, first_reasons = _clarification_first_pass(case, first)
                if _is_clarifying(first):
                    second = await _send(client, session_id, case.second_reply)
                    second_passed, second_reasons = _clarification_second_pass(case, second)
                else:
                    second_passed, second_reasons = False, ["second_turn_not_run"]
                passed = first_passed and second_passed
                reasons = first_reasons + second_reasons
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            first_message = str(first.get("message") or "")
            second_message = str((second or {}).get("message") or "")
            return {
                "id": case.case_id,
                "passed": passed,
                "expected": case.expected,
                "reasons": reasons,
                "first_route": _route(first),
                "first_clarifying": _is_clarifying(first),
                "candidate_count": len(_candidates(first)),
                "second_route": _route(second or {}),
                "second_clarifying": _is_clarifying(second or {}),
                "elapsed_ms": elapsed_ms,
                "first_preview": first_message[:220].replace("\n", " "),
                "second_preview": second_message[:260].replace("\n", " "),
            }
        except Exception as exc:
            return {
                "id": case.case_id,
                "passed": False,
                "expected": case.expected,
                "reasons": [f"request_error:{type(exc).__name__}:{exc}"],
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
            }


async def main() -> int:
    load_dotenv()
    token = str(os.getenv("API_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("API_TOKEN is required")
    # 串行执行，避免并发请求改变会话/模型状态，保证本次基线可复现。
    semaphore = asyncio.Semaphore(1)
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8000",
        headers={"X-Api-Token": token},
        timeout=httpx.Timeout(150.0),
    ) as client:
        results = await asyncio.gather(*(_run_case(client, semaphore, case) for case in CASES))

    results = sorted(results, key=lambda item: item["id"])
    passed = sum(bool(item["passed"]) for item in results)
    summary = {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "pass_rate": round(passed / len(results), 4),
        "failed_ids": [item["id"] for item in results if not item["passed"]],
    }
    print(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2))
    return 0 if passed >= 16 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
