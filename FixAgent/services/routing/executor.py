"""Deterministic handlers for route actions that must not use an LLM."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.routing.models import RouteAction, RoutePlan


@dataclass(frozen=True)
class RouteExecution:
    message: str
    tools_used: tuple[str, ...]
    metadata: dict[str, Any]


class RouteExecutor:
    async def execute(
        self,
        plan: RoutePlan,
        *,
        inventory_tool: Any = None,
    ) -> RouteExecution | None:
        if plan.action == RouteAction.KNOWLEDGE_INVENTORY:
            return await self._execute_inventory(plan, inventory_tool)
        if plan.action == RouteAction.CLARIFY:
            return self._execute_clarification(plan)
        return None

    async def _execute_inventory(self, plan: RoutePlan, inventory_tool: Any) -> RouteExecution:
        base = self._route_metadata(plan)
        if inventory_tool is None:
            return RouteExecution(
                message="暂时无法读取知识库文档列表：库存工具不可用。",
                tools_used=(),
                metadata={**base, "status": "tool_missing"},
            )
        result = await inventory_tool.run()
        if not getattr(result, "success", False):
            error = getattr(result, "error", None)
            detail = str(getattr(error, "message", "库存查询失败"))
            return RouteExecution(
                message=f"暂时无法读取知识库文档列表：{detail}",
                tools_used=("knowledge_inventory",),
                metadata={**base, "status": "tool_error", "error_detail": detail},
            )
        data = getattr(result, "data", None) or {}
        documents = list(data.get("documents") or [])
        return RouteExecution(
            message=self._format_inventory(documents),
            tools_used=("knowledge_inventory",),
            metadata={
                **base,
                "status": "success",
                "knowledge_inventory_total": len(documents),
                "knowledge_inventory_source": data.get("source"),
            },
        )

    def _execute_clarification(self, plan: RoutePlan) -> RouteExecution:
        is_document_selection = plan.clarification_kind == "document_selection"
        lines = [
            "找到多个可能适用的知识文档，请先确认要查询哪一个："
            if is_document_selection
            else "请确认更符合哪一个候选范围："
        ]
        normalized_options: list[dict[str, Any]] = []
        for index, option in enumerate(plan.clarification_options, start=1):
            name = str(option.get("label") or option.get("display_name") or option.get("document_id") or "未命名文档")
            constraints = option.get("constraints") if isinstance(option.get("constraints"), dict) else {}
            document_id = str(
                option.get("document_id")
                or constraints.get("document_id")
                or option.get("value")
                or ""
            )
            if document_id and "document_id" not in constraints:
                constraints = {**constraints, "document_id": document_id}
            normalized_options.append({
                "id": str(option.get("id") or chr(ord("A") + index - 1)),
                "label": name,
                "value": str(option.get("value") or document_id or name),
                "document_id": document_id or str(constraints.get("document_id") or ""),
                "display_name": name,
                "constraints": constraints,
            })
            lines.append(
                f"{index}. 《{name}》（文档 ID：{document_id}）"
                if is_document_selection
                else f"{index}. {name}"
            )
        lines.append(
            "请回复序号、文档名称或文档 ID，我会只在所选文档中继续查询。"
            if is_document_selection
            else "请回复序号或候选名称，我会按所选范围继续查询。"
        )
        return RouteExecution(
            message="\n".join(lines),
            tools_used=(),
            metadata={
                **self._route_metadata(plan),
                "status": "awaiting_answer",
                "pending_clarification": {
                    "kind": plan.clarification_kind or "slot_disambiguation",
                    "status": "awaiting_answer",
                    "round_count": 1,
                    "max_rounds": 2,
                    "version": 1,
                    "topic_signature": plan.query_contract.raw_query,
                    "original_query": plan.query_contract.raw_query,
                    "candidates": normalized_options,
                    "alternatives": normalized_options,
                    "route_snapshot": plan.to_dict(),
                },
                "pending_document_selection": {
                    "status": "awaiting_answer",
                    "original_query": plan.query_contract.raw_query,
                    "alternatives": normalized_options,
                },
            },
        )

    @staticmethod
    def _route_metadata(plan: RoutePlan) -> dict[str, Any]:
        return {
            "execution_mode": f"{plan.action.value}_direct",
            "deterministic_direct": True,
            "route_action": plan.action.value,
            "answer_source": plan.answer_source,
            "selected_document_id": plan.selected_document_id,
            "selected_section_id": plan.selected_section_id,
            "candidate_document_ids": list(plan.candidate_document_ids),
            "allowed_tools": list(plan.allowed_tools),
            "route_reason": plan.reason,
        }

    @staticmethod
    def _format_inventory(documents: list[dict[str, Any]]) -> str:
        if not documents:
            return "知识库中目前没有已导入的知识文件。"
        lines = [f"知识库中目前共有 {len(documents)} 个已导入的知识文件："]
        for index, document in enumerate(documents, start=1):
            name = str(document.get("manual_name") or "").strip() or f"未命名文档 {index}"
            status = str(document.get("status") or "-").strip()
            text_count = int(document.get("text_count") or 0)
            image_count = int(document.get("image_count") or 0)
            table_count = int(document.get("table_count") or 0)
            lines.append(
                f"{index}. 《{name}》：状态 {status}，{text_count} 段文本、"
                f"{image_count} 张图片、{table_count} 个表格。"
            )
        return "\n".join(lines)
