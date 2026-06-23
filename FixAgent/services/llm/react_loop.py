import json
import logging
import inspect
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional
from services.llm.evidence import build_evidence_items

logger = logging.getLogger(__name__)


def _json_default(value: Any) -> Any:
    """Convert structured tool results into JSON payloads sent back to the LLM."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _json_compatible(value: Any) -> Any:
    """Return the same JSON-ready tool payload used for the model and audit trace."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=_json_default))


class ToolExecutor:
    """Execute function-call tool handlers and normalize their results for ReAct."""

    def __init__(self, tool_handlers: Dict[str, Callable[..., Awaitable]]):
        self.tool_handlers = tool_handlers

    async def execute(self, tool_call: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
        func_name = tool_call["function"]["name"]
        try:
            func_args = json.loads(tool_call["function"].get("arguments") or "{}")
        except json.JSONDecodeError:
            func_args = {}

        call_record = {
            "name": func_name,
            "arguments": func_args,
        }

        if func_name not in self.tool_handlers:
            result_payload = {"error": f"Tool {func_name} not found"}
            call_record["result_summary"] = f"tool not found: {func_name}"
            return call_record, result_payload

        try:
            result = await self.tool_handlers[func_name](**func_args)
        except Exception as exc:
            result = {"error": str(exc)}

        result_payload = _json_compatible(result)
        evidence = build_evidence_items(func_name, result_payload)
        call_record["result_summary"] = str(result_payload)[:200]
        call_record["result_data"] = result_payload
        call_record["evidence"] = evidence
        return call_record, result_payload


class ReActLoop:
    """Small ReAct function-calling loop kept outside the model client service."""

    def __init__(self, llm_service):
        self.llm_service = llm_service

    async def run(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        tool_handlers: Dict[str, Callable[..., Awaitable]],
        max_iterations: int = 10,
        response_format: Optional[Dict[str, str]] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        trace: List[Dict[str, Any]] = []
        executor = ToolExecutor(tool_handlers)

        for i in range(max_iterations):
            step_start = time.time()
            response = await self._complete_with_tools_once(
                messages=messages,
                tools=tools,
                model=model,
                response_format=response_format,
            )

            tool_calls = response.get("tool_calls", [])
            step_duration_ms = int((time.time() - step_start) * 1000)

            if not tool_calls:
                trace.append({
                    "iteration": i + 1,
                    "action": "finish",
                    "content_preview": (response.get("content") or "")[:100],
                    "duration_ms": step_duration_ms,
                })
                response["trace"] = trace
                logger.info(
                    "[llm] tool calling completed iterations=%s last_step_ms=%s",
                    i + 1,
                    step_duration_ms,
                )
                return response

            step_info = {
                "iteration": i + 1,
                "action": "tool_call",
                "duration_ms": step_duration_ms,
                "tool_calls": [],
            }

            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": tool_calls,
            })

            for tool_call in tool_calls:
                call_record, result_payload = await executor.execute(tool_call)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": json.dumps(result_payload, ensure_ascii=False),
                })
                step_info["tool_calls"].append(call_record)

            trace.append(step_info)

        raise RuntimeError(f"Tool calling exceeded max iterations ({max_iterations})")

    async def _complete_with_tools_once(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        model: Optional[str],
        response_format: Optional[Dict[str, str]],
    ) -> Dict[str, Any]:
        complete = getattr(self.llm_service, "complete_with_tools", None)
        if complete and inspect.iscoroutinefunction(complete):
            return await complete(
                messages=messages,
                tools=tools,
                response_format=response_format,
                model=model,
            )

        legacy_chat = getattr(self.llm_service, "chat_with_tools", None)
        if legacy_chat and inspect.iscoroutinefunction(legacy_chat):
            return await legacy_chat(
                messages=messages,
                tools=tools,
                tool_handlers={},
                max_iterations=1,
                response_format=response_format,
                model=model,
            )

        raise TypeError("llm_service must provide async complete_with_tools() or chat_with_tools()")
