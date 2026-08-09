"""LLM-assisted clarification when project evidence has no usable candidates.

The model may propose one observable question, but it never defines trusted
equipment facts or a final diagnosis. All option constraints are constructed
locally from validated text.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


logger = logging.getLogger(__name__)

_ALLOWED_DIMENSIONS = {
    "symptom": "symptoms",
    "operating_condition": "operating_conditions",
}
_UNSAFE_TEXT = (
    "一定是",
    "故障原因是",
    "已经损坏",
    "立即更换",
    "拆卸",
    "安装",
    "检查",
    "维修步骤",
    "调整到",
    "扭矩为",
    "间隙为",
)

_OBSERVABLE_HINTS = (
    "无法启动", "不能启动", "启动困难", "不工作", "熄火", "异响", "啸叫",
    "撞击声", "噪声", "抖动", "振动", "冒烟", "漏油", "渗漏", "过热",
    "温度高", "温度异常", "动力不足", "加速无力", "转速不稳", "怠速不稳",
    "压力低", "压力高", "压力不足", "压力波动", "故障灯", "报警", "报码",
    "打滑", "卡滞", "失灵", "无反应", "声音", "烟色",
)
_CONDITION_HINTS = (
    "冷机", "热机", "冷车", "热车", "启动时", "启动瞬间", "加速时", "减速时",
    "怠速时", "负载时", "空载时", "行驶中", "停车后", "持续运行", "高转速",
    "低转速", "上坡时", "雨天", "高温环境", "低温环境",
)

_SYSTEM_PROMPT = """你是设备检修对话中的澄清问题规划器。

项目知识图谱和手册可能返回若干尚未确认的候选原因。你只能判断用户描述是否仍然模糊，
并在必要时提出一个现场人员容易回答的问题。你不能诊断故障、不能断言部件损坏、不能提供
参数、维修步骤或设备型号，也不能声称选项来自知识图谱。

规则：
1. 信息已经足以开展一般排查时，should_clarify=false。
2. 需要澄清时，一次只问一个信息增益最高的维度。
3. dimension 只能是 symptom、operating_condition，禁止询问部件、位置、章节或维修动作。
4. options 必须是 2 至 4 个现场可直接观察或描述的互斥选项，不得是故障原因或维修动作。
5. 候选信息只有原因或部件、无法形成至少两个具体可观察现象时，should_clarify=false，让系统降级。
6. 可依据一般检修知识给出“大致可能出现的现象”，但不得把推测写成已发生事实。
7. 只输出 JSON，不要 Markdown。

输出格式：
{
  "should_clarify": true,
  "dimension": "symptom",
  "question": "当前最明显的异常表现是哪一种？",
  "options": [
    {"label": "无法启动", "value": "无法启动"},
    {"label": "运行中异响", "value": "运行中异响"}
  ],
  "reason": "当前描述只有设备级异常，无法缩小排查范围"
}
"""


@dataclass(frozen=True)
class LLMSlotClarification:
    dimension: str
    question: str
    options: tuple[dict[str, Any], ...]
    reason: str = ""


class LLMClarificationService:
    def __init__(self, llm_service: Any) -> None:
        self.llm_service = llm_service

    async def build(
        self,
        *,
        query: str,
        query_contract: Mapping[str, Any],
        confirmed_constraints: Mapping[str, Any] | None = None,
        graph_candidates: Iterable[Any] = (),
        round_count: int = 1,
    ) -> LLMSlotClarification | None:
        public_graph_candidates = self._public_graph_candidates(graph_candidates)
        payload = {
            "user_query": str(query or "").strip(),
            "query_contract": self._public_contract(query_contract),
            "confirmed_information": self._public_constraints(confirmed_constraints),
            "clarification_round": max(1, int(round_count)),
            "knowledge_graph_status": (
                "ambiguous_candidates" if public_graph_candidates else "no_usable_candidate"
            ),
            "knowledge_graph_candidates": public_graph_candidates,
        }
        try:
            response = await self.llm_service.chat(
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                temperature=0.1,
                max_tokens=600,
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            logger.info("[clarification] LLM fallback unavailable: %s", exc)
            return None
        content = response.get("content") if isinstance(response, Mapping) else response
        data = self._parse_json(content)
        if not isinstance(data, Mapping) or data.get("should_clarify") is not True:
            return None
        dimension = str(data.get("dimension") or "").strip()
        if dimension not in _ALLOWED_DIMENSIONS:
            return None
        question = self._safe_text(data.get("question"), maximum=120)
        if not question or self._contains_unsafe_text(question):
            return None
        raw_options = data.get("options") if isinstance(data.get("options"), list) else []
        options: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in raw_options[:4]:
            if not isinstance(raw, Mapping):
                continue
            label = self._safe_text(raw.get("label"), maximum=50)
            value = self._safe_text(raw.get("value") or label, maximum=50)
            key = value.casefold()
            if (
                not label
                or not value
                or key in seen
                or self._contains_unsafe_text(f"{label} {value}")
                or not self._is_observable(value, dimension)
            ):
                continue
            seen.add(key)
            option_id = chr(ord("A") + len(options))
            constraints: dict[str, Any] = {
                "clarification_source": "llm_fallback",
                "clarification_dimension": dimension,
                "clarification_value": value,
                "clarification_round": max(1, int(round_count)),
            }
            slot = _ALLOWED_DIMENSIONS[dimension]
            constraints[slot] = value if slot == "component" else [value]
            options.append({
                "id": option_id,
                "label": label,
                "value": value,
                "constraints": constraints,
            })
        if len(options) < 2:
            return None
        return LLMSlotClarification(
            dimension=dimension,
            question=question,
            options=tuple(options),
            reason=self._safe_text(data.get("reason"), maximum=160),
        )

    @staticmethod
    def _public_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
        allowed = (
            "intent",
            "task_action",
            "device_name",
            "device_category",
            "component",
            "symptoms",
            "operating_conditions",
        )
        return {key: contract.get(key) for key in allowed if contract.get(key)}

    @staticmethod
    def _public_constraints(constraints: Mapping[str, Any] | None) -> dict[str, Any]:
        source = constraints if isinstance(constraints, Mapping) else {}
        allowed = ("component", "symptoms", "operating_conditions", "clarification_dimension")
        return {key: source.get(key) for key in allowed if source.get(key)}

    @staticmethod
    def _public_graph_candidates(candidates: Iterable[Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for candidate in tuple(candidates or ())[:6]:
            dimensions = getattr(candidate, "dimensions", {}) or {}
            item = {
                "component": str(dimensions.get("component") or "").strip(),
                "possible_fault": str(dimensions.get("fault") or "").strip(),
                "known_observations": list(
                    getattr(candidate, "distinguishing_features", ()) or ()
                )[:4],
                "verification_actions": list(
                    getattr(candidate, "verification_actions", ()) or ()
                )[:3],
            }
            cleaned = {key: value for key, value in item.items() if value}
            if cleaned:
                result.append(cleaned)
        return result

    @staticmethod
    def _is_observable(value: str, dimension: str) -> bool:
        hints = _CONDITION_HINTS if dimension == "operating_condition" else _OBSERVABLE_HINTS
        return any(hint in value for hint in hints)

    @staticmethod
    def _parse_json(content: Any) -> Mapping[str, Any] | None:
        text = str(content or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        match = re.search(r"\{[\s\S]*\}", text)
        try:
            value = json.loads(match.group(0) if match else text)
        except (json.JSONDecodeError, TypeError):
            return None
        return value if isinstance(value, Mapping) else None

    @staticmethod
    def _safe_text(value: Any, *, maximum: int) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()[:maximum]

    @staticmethod
    def _contains_unsafe_text(value: str) -> bool:
        return any(term in value for term in _UNSAFE_TEXT)


__all__ = ["LLMClarificationService", "LLMSlotClarification"]
