"""Intent-driven fast paths for chat requests.

The fast path is intentionally conservative: it only handles clear intents with
enough tool evidence, then still lets the existing ReviewAgent run downstream.
Returning None means the caller should fall back to the original FixAgent path.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Dict, List, Optional

from agents.base_agent import AgentInput, AgentOutput
from services.output_style import USER_VISIBLE_PLAIN_TEXT_RULES
from services.react_loop import _json_compatible

logger = logging.getLogger(__name__)

MANUAL_IMAGE_QUERY_RE = re.compile(
    r"((知识库|手册|资料|文档).{0,20}(查找|查询|检索|搜索|查|找|返回|展示|给我|提供).{0,20}"
    r"(图片|照片|示例图|示例图片|结构图|示意图|外观图|图示|配图)|"
    r"(查找|查询|检索|搜索|查|找|返回|展示|给我|提供).{0,20}"
    r"(图片|照片|示例图|示例图片|结构图|示意图|外观图|图示|配图))"
)

_COUNT_WORDS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
}
_IMAGE_COUNT_RE = re.compile(r"(?:返回|给我|找|展示|提供|要)?\s*(?P<count>\d+|[一二两三四五六])\s*张")
DEFAULT_MANUAL_IMAGE_TOP_K = 2
MAX_MANUAL_IMAGE_TOP_K = 6


SIMPLE_RETRIEVAL_INTENTS = {
    "knowledge_query",
    "parameter_query",
    "document_understanding",
    "visual_identification",
}

COMPLEX_PARALLEL_INTENTS = {
    "fault_diagnosis",
    "maintenance_guidance",
    "procedure_planning",
}

SUPPORTED_INTENTS = SIMPLE_RETRIEVAL_INTENTS | COMPLEX_PARALLEL_INTENTS | {
    "chat_social",
    "knowledge_inventory",
}


class IntentFastPathRunner:
    """Run deterministic, intent-specific short paths before generic ReAct."""

    def __init__(
        self,
        llm_service=None,
        knowledge_retrieval_tool=None,
        knowledge_inventory_tool=None,
        graph_tool=None,
        procedure_tool=None,
    ):
        self._llm_service = llm_service
        self._knowledge_retrieval_tool = knowledge_retrieval_tool
        self._knowledge_inventory_tool = knowledge_inventory_tool
        self._graph_tool = graph_tool
        self._procedure_tool = procedure_tool

    async def run(self, input_data: AgentInput) -> Optional[AgentOutput]:
        context = input_data.context or {}
        if context.get("disable_intent_fast_path") or context.get("force_react"):
            return None

        decision = context.get("intent_decision")
        if not isinstance(decision, dict):
            return None

        intent = str(decision.get("intent") or "").strip()
        if intent not in SUPPORTED_INTENTS:
            return None

        confidence = float(decision.get("confidence") or 0.0)
        if confidence and confidence < 0.65:
            logger.info("[intent_fast_path] skip low confidence intent=%s confidence=%s", intent, confidence)
            return None

        start = time.time()
        try:
            if intent == "knowledge_inventory":
                return await self._run_knowledge_inventory(input_data, decision, start)
            if intent == "chat_social":
                return await self._run_llm_only(input_data, decision, start)
            if intent in SIMPLE_RETRIEVAL_INTENTS:
                return await self._run_simple_retrieval(input_data, decision, start)
            if intent in COMPLEX_PARALLEL_INTENTS:
                return await self._run_parallel_tools(input_data, decision, start)
        except Exception:
            logger.exception("[intent_fast_path] failed intent=%s session=%s", intent, input_data.session_id)
            return None

        return None

    async def _run_knowledge_inventory(
        self,
        input_data: AgentInput,
        decision: Dict[str, Any],
        start: float,
    ) -> Optional[AgentOutput]:
        tool_start = time.time()
        result = await self._knowledge_inventory_tool_instance().run()
        tool_ms = int((time.time() - tool_start) * 1000)
        if not result.success:
            return None

        data = result.data or {}
        documents = data.get("documents") or []
        message = self._format_knowledge_inventory(documents)
        trace = [
            self._trace_step(
                duration_ms=tool_ms,
                calls=[
                    {
                        "name": "knowledge_inventory",
                        "arguments": {},
                        "result_data": data,
                    }
                ],
            )
        ]
        return self._output(
            input_data=input_data,
            decision=decision,
            message=message,
            tools_used=["knowledge_inventory"],
            trace=trace,
            start=start,
            phase_timings={"knowledge_inventory": tool_ms},
            execution_mode="knowledge_inventory_direct",
        )

    async def _run_llm_only(
        self,
        input_data: AgentInput,
        decision: Dict[str, Any],
        start: float,
    ) -> Optional[AgentOutput]:
        llm_start = time.time()
        response = await self._llm_service_instance().chat(
            messages=self._build_messages(
                intent="chat_social",
                question=input_data.user_message,
                evidence_text="",
                images=input_data.images or [],
            ),
            temperature=0.3,
            max_tokens=600,
        )
        llm_ms = int((time.time() - llm_start) * 1000)
        content = (response.get("content") or "").strip()
        if not content:
            return None
        return self._output(
            input_data=input_data,
            decision=decision,
            message=content,
            tools_used=[],
            trace=[],
            start=start,
            phase_timings={"llm_generation": llm_ms},
            raw_response=response,
        )

    async def _run_simple_retrieval(
        self,
        input_data: AgentInput,
        decision: Dict[str, Any],
        start: float,
    ) -> Optional[AgentOutput]:
        query = self._retrieval_query(input_data, decision)
        retrieval_args = self._retrieval_args(input_data, decision, query)
        tool_start = time.time()
        result = await self._knowledge_retrieval_tool_instance().run(**retrieval_args)
        retrieval_ms = int((time.time() - tool_start) * 1000)
        if not self._has_tool_data(result):
            return None

        result_data = self._tool_result_data(result)
        evidence_text = self._format_tool_evidence("knowledge_retrieval", result_data)
        llm_start = time.time()
        response = await self._llm_service_instance().chat(
            messages=self._build_messages(
                intent=str(decision.get("intent") or "knowledge_query"),
                question=input_data.user_message,
                evidence_text=evidence_text,
                images=input_data.images or [],
            ),
            temperature=0.1,
            max_tokens=self._max_tokens_for_intent(str(decision.get("intent") or "")),
        )
        llm_ms = int((time.time() - llm_start) * 1000)
        content = (response.get("content") or "").strip()
        if not content:
            return None

        trace = [
            self._trace_step(
                duration_ms=retrieval_ms,
                calls=[
                    {
                        "name": "knowledge_retrieval",
                        "arguments": retrieval_args,
                        "result_data": result_data,
                    }
                ],
            )
        ]
        return self._output(
            input_data=input_data,
            decision=decision,
            message=content,
            tools_used=["knowledge_retrieval"],
            trace=trace,
            start=start,
            phase_timings={
                "knowledge_retrieval": retrieval_ms,
                "llm_generation": llm_ms,
            },
            raw_response=response,
        )

    async def _run_parallel_tools(
        self,
        input_data: AgentInput,
        decision: Dict[str, Any],
        start: float,
    ) -> Optional[AgentOutput]:
        query = self._retrieval_query(input_data, decision)
        target = self._target_object(input_data, decision)
        intent = str(decision.get("intent") or "")

        jobs = [
            self._call_tool(
                "knowledge_retrieval",
                self._knowledge_retrieval_tool_instance(),
                {"query": query, "top_k": 5},
            ),
            self._call_tool(
                "java_graph_diagnosis_path",
                self._graph_tool_instance(),
                self._graph_args(input_data, decision, target),
            ),
        ]
        if intent in {"maintenance_guidance", "procedure_planning"} and target:
            jobs.append(
                self._call_tool(
                    "procedure_recommend",
                    self._procedure_tool_instance(),
                    {
                        "device_type": target,
                        "fault_description": input_data.user_message,
                    },
                )
            )

        tool_results = await asyncio.gather(*jobs)
        successful = [item for item in tool_results if item["success"] and self._data_is_useful(item["data"])]
        used_tools = [item["name"] for item in successful]

        required = self._required_tools(intent)
        if any(name not in used_tools for name in required):
            logger.info(
                "[intent_fast_path] fallback intent=%s missing_required=%s used=%s",
                intent,
                required,
                used_tools,
            )
            return None

        evidence_text = "\n\n".join(
            self._format_tool_evidence(item["name"], item["data"])
            for item in successful
        )
        llm_start = time.time()
        response = await self._llm_service_instance().chat(
            messages=self._build_messages(
                intent=intent,
                question=input_data.user_message,
                evidence_text=evidence_text,
                images=input_data.images or [],
            ),
            temperature=0.1,
            max_tokens=self._max_tokens_for_intent(intent),
        )
        llm_ms = int((time.time() - llm_start) * 1000)
        content = (response.get("content") or "").strip()
        if not content:
            return None

        trace = [
            self._trace_step(
                duration_ms=max((item["duration_ms"] for item in tool_results), default=0),
                calls=[
                    {
                        "name": item["name"],
                        "arguments": item["arguments"],
                        "result_data": item["data"],
                        "error": item.get("error"),
                    }
                    for item in tool_results
                ],
            )
        ]
        timings = {item["name"]: item["duration_ms"] for item in tool_results}
        timings["llm_generation"] = llm_ms
        return self._output(
            input_data=input_data,
            decision=decision,
            message=content,
            tools_used=used_tools,
            trace=trace,
            start=start,
            phase_timings=timings,
            raw_response=response,
        )

    async def _call_tool(self, name: str, tool, arguments: Dict[str, Any]) -> Dict[str, Any]:
        started = time.time()
        try:
            result = await tool.run(**arguments)
            duration_ms = int((time.time() - started) * 1000)
            data = self._tool_result_data(result)
            return {
                "name": name,
                "arguments": arguments,
                "success": bool(getattr(result, "success", False)),
                "data": data,
                "error": self._tool_error(result),
                "duration_ms": duration_ms,
            }
        except Exception as exc:
            duration_ms = int((time.time() - started) * 1000)
            logger.warning("[intent_fast_path] tool failed name=%s error=%s", name, exc)
            return {
                "name": name,
                "arguments": arguments,
                "success": False,
                "data": None,
                "error": {
                    "type": exc.__class__.__name__,
                    "message": str(exc),
                },
                "duration_ms": duration_ms,
            }

    @staticmethod
    def _required_tools(intent: str) -> List[str]:
        if intent == "fault_diagnosis":
            return ["knowledge_retrieval", "java_graph_diagnosis_path"]
        if intent in {"maintenance_guidance", "procedure_planning"}:
            return ["knowledge_retrieval", "procedure_recommend"]
        return []

    @staticmethod
    def _has_tool_data(result) -> bool:
        return bool(getattr(result, "success", False)) and IntentFastPathRunner._data_is_useful(
            IntentFastPathRunner._tool_result_data(result)
        )

    @staticmethod
    def _data_is_useful(data: Any) -> bool:
        if data is None:
            return False
        if isinstance(data, list):
            return bool(data)
        if isinstance(data, dict):
            for key in ("data", "documents", "paths", "raw_records", "devices"):
                if isinstance(data.get(key), list) and data.get(key):
                    return True
            for key in ("context", "message", "content"):
                if str(data.get(key) or "").strip():
                    return True
            for key in ("procedures_found", "paths_found", "cases_found", "count", "total"):
                if int(data.get(key) or 0) > 0:
                    return True
            return bool(data)
        return bool(str(data).strip())

    @staticmethod
    def _tool_result_data(result) -> Any:
        return _json_compatible(getattr(result, "data", None))

    @staticmethod
    def _tool_error(result) -> Optional[Dict[str, Any]]:
        error = getattr(result, "error", None)
        if not error:
            return None
        return _json_compatible(error)

    def _build_messages(
        self,
        intent: str,
        question: str,
        evidence_text: str,
        images: List[str],
    ) -> List[Dict[str, Any]]:
        system = self._system_prompt(intent)
        if images:
            content: List[Dict[str, Any]] = [
                {
                    "type": "text",
                    "text": self._user_prompt(question, evidence_text),
                }
            ]
            for image in images:
                content.append({"type": "image_url", "image_url": {"url": image}})
            return [{"role": "system", "content": system}, {"role": "user", "content": content}]
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": self._user_prompt(question, evidence_text)},
        ]

    @staticmethod
    def _system_prompt(intent: str) -> str:
        prompts = {
            "chat_social": (
                "你是设备检修 AI 助手。当前用户是闲聊或询问你能做什么。"
                "用自然中文简洁回答。不要编造具体技术参数、维修步骤或资料来源。"
            ),
            "knowledge_query": (
                "你是设备检修知识问答助手。只基于给定证据回答。"
                "证据不足时明确说明不足，不要编造技术结论。"
                "从知识库查找图片不需要用户上传图片；如果证据中没有相关图片，只说明知识库中暂未找到相关示例图片。"
            ),
            "parameter_query": (
                "你是设备检修参数查询助手。只提取证据中的明确参数、单位、适用对象和来源。"
                "如果证据中没有明确参数，直接说明当前资料不足。"
            ),
            "document_understanding": (
                "你是维修文档解释助手。解释给定文档证据的含义，不主动扩展成维修建议。"
                "证据不足时说明需要补充文档页或上下文。"
            ),
            "visual_identification": (
                "你是设备图片识别助手。只做识别、比较和所属系统判断。"
                "不要主动生成拆装步骤、维修建议、参数标准或更换周期。"
            ),
            "fault_diagnosis": (
                "你是设备故障诊断助手。基于知识库和图谱证据输出可能原因、依据和待确认项。"
                "不要把证据不足的原因写成确定结论。"
            ),
            "maintenance_guidance": (
                "你是设备维修指导助手。基于证据输出可执行步骤、工具、安全注意和依据。"
                "涉及操作风险时必须写清断电、降温、防护和现场确认要求。"
            ),
            "procedure_planning": (
                "你是检修流程规划助手。基于证据和流程推荐生成结构化检修流程。"
                "先给清晰流程摘要和关键安全点，不编造未在证据中出现的参数。"
            ),
        }
        return prompts.get(intent, prompts["knowledge_query"]) + "\n\n" + USER_VISIBLE_PLAIN_TEXT_RULES

    @staticmethod
    def _user_prompt(question: str, evidence_text: str) -> str:
        if evidence_text:
            return f"用户问题：{question}\n\n可用证据：\n{evidence_text}\n\n请用中文回答。"
        return f"用户问题：{question}\n\n请用中文回答。"

    @staticmethod
    def _max_tokens_for_intent(intent: str) -> int:
        return {
            "chat_social": 600,
            "knowledge_query": 900,
            "parameter_query": 500,
            "document_understanding": 800,
            "visual_identification": 800,
            "fault_diagnosis": 1200,
            "maintenance_guidance": 1400,
            "procedure_planning": 1800,
        }.get(intent, 900)

    @staticmethod
    def _format_tool_evidence(tool_name: str, data: Any) -> str:
        if isinstance(data, list):
            lines = ["可用证据："]
            for index, item in enumerate(data, start=1):
                if hasattr(item, "model_dump"):
                    item = item.model_dump()
                if not isinstance(item, dict):
                    lines.append(f"{index}. {item}")
                    continue
                metadata = item.get("metadata") or {}
                content = item.get("content") or item.get("text") or item.get("summary") or ""
                page = metadata.get("page") or metadata.get("page_number")
                title = (
                    metadata.get("caption")
                    or metadata.get("image_title")
                    or metadata.get("section_title")
                    or metadata.get("title")
                    or ""
                )
                detail_lines = [f"{index}. 证据"]
                if page:
                    detail_lines.append(f"页码：第{page}页")
                if title:
                    detail_lines.append(f"说明：{title}")
                if content:
                    detail_lines.append(f"内容：{content}")
                lines.append("\n".join(detail_lines))
            return "\n".join(lines)

        if isinstance(data, dict):
            if data.get("context"):
                return f"[{tool_name}]\n{data['context']}"
            return f"[{tool_name}]\n{data}"
        return f"[{tool_name}]\n{data}"

    @staticmethod
    def _format_knowledge_inventory(documents: List[Dict[str, Any]]) -> str:
        if not documents:
            return "知识库中目前没有已导入的知识文件。"

        lines = [f"知识库中目前共有{len(documents)}个已导入的知识文件，具体如下："]
        for index, doc in enumerate(documents, start=1):
            name = str(doc.get("manual_name") or "").strip() or f"未命名手册{index}"
            status = str(doc.get("status") or "-").strip()
            text_count = int(doc.get("text_count") or 0)
            image_count = int(doc.get("image_count") or 0)
            table_count = int(doc.get("table_count") or 0)
            created_at = str(doc.get("created_at") or "").strip()
            detail = f"含{text_count}段文本、{image_count}张图片、{table_count}个表格，状态为 {status}"
            if created_at:
                detail += f"，入库时间：{created_at}"
            lines.extend(["", f"{index}. 《{name}》", detail + "。"])
        return "\n".join(lines).strip()

    @staticmethod
    def _retrieval_query(input_data: AgentInput, decision: Dict[str, Any]) -> str:
        context = input_data.context or {}
        enhanced = str(context.get("enhanced_retrieval_query") or "").strip()
        return enhanced or input_data.user_message

    @classmethod
    def _retrieval_args(cls, input_data: AgentInput, decision: Dict[str, Any], query: str) -> Dict[str, Any]:
        args: Dict[str, Any] = {"query": query, "top_k": 5}
        if cls._is_manual_image_query(input_data.user_message, decision):
            args["top_k"] = cls._requested_image_top_k(input_data.user_message, decision)
            args["chunk_type"] = "image"
        return args

    @staticmethod
    def _is_manual_image_query(message: str, decision: Dict[str, Any]) -> bool:
        target_object = str(decision.get("target_object") or "")
        user_goal = str(decision.get("user_goal") or "")
        text = " ".join(part for part in (message or "", target_object, user_goal) if part)
        return bool(MANUAL_IMAGE_QUERY_RE.search(text))

    @staticmethod
    def _requested_image_top_k(message: str, decision: Dict[str, Any]) -> int:
        target_object = str(decision.get("target_object") or "")
        user_goal = str(decision.get("user_goal") or "")
        text = " ".join(part for part in (message or "", target_object, user_goal) if part)
        match = _IMAGE_COUNT_RE.search(text)
        if not match:
            return DEFAULT_MANUAL_IMAGE_TOP_K
        raw = match.group("count")
        count = int(raw) if raw.isdigit() else _COUNT_WORDS.get(raw, DEFAULT_MANUAL_IMAGE_TOP_K)
        return min(max(count, 1), MAX_MANUAL_IMAGE_TOP_K)

    @staticmethod
    def _target_object(input_data: AgentInput, decision: Dict[str, Any]) -> str:
        target = str(decision.get("target_object") or "").strip()
        if target:
            return target
        words = (input_data.user_message or "").replace("？", "").replace("?", "").strip()
        return words[:32]

    @staticmethod
    def _graph_args(input_data: AgentInput, decision: Dict[str, Any], target: str) -> Dict[str, Any]:
        intent = str(decision.get("intent") or "")
        args: Dict[str, Any] = {"limit": 10}
        if input_data.images:
            args["image_urls"] = input_data.images
        if intent == "fault_diagnosis":
            args["fault_description"] = input_data.user_message
            if target:
                args["keyword"] = target
        elif intent in {"maintenance_guidance", "procedure_planning"}:
            args["component_description"] = target or input_data.user_message
            args["fault_description"] = input_data.user_message
        else:
            args["keyword"] = target or input_data.user_message
        return args

    @staticmethod
    def _trace_step(duration_ms: int, calls: List[Dict[str, Any]]) -> Dict[str, Any]:
        normalized_calls = []
        for call in calls:
            data = call.get("result_data")
            normalized_calls.append(
                {
                    "name": call.get("name"),
                    "arguments": call.get("arguments") or {},
                    "result_summary": str(data)[:200],
                    "result_data": data,
                    **({"error": call["error"]} if call.get("error") else {}),
                }
            )
        return {
            "iteration": 1,
            "action": "tool_call",
            "duration_ms": duration_ms,
            "tool_calls": normalized_calls,
        }

    @staticmethod
    def _output(
        input_data: AgentInput,
        decision: Dict[str, Any],
        message: str,
        tools_used: List[str],
        trace: List[Dict[str, Any]],
        start: float,
        phase_timings: Dict[str, int],
        execution_mode: str = "intent_fast_path",
        raw_response: Optional[Dict[str, Any]] = None,
    ) -> AgentOutput:
        total_ms = int((time.time() - start) * 1000)
        metadata = {
            "execution_mode": execution_mode,
            "intent_fast_path": True,
            "intent_fast_path_intent": decision.get("intent"),
            "intent_decision": decision,
            "react_trace": trace,
            "react_iterations": 1 if trace else 0,
            "phase_timings_ms": {
                **phase_timings,
                "intent_fast_path_total": total_ms,
            },
        }
        return AgentOutput(
            agent_name="fix_agent",
            message=message,
            intention=decision.get("intent"),
            tools_used=tools_used,
            metadata=metadata,
            latency_ms=total_ms,
            raw_response=raw_response,
        )

    def _llm_service_instance(self):
        if self._llm_service is None:
            from services.llm_service import get_llm_service

            self._llm_service = get_llm_service()
        return self._llm_service

    def _knowledge_retrieval_tool_instance(self):
        if self._knowledge_retrieval_tool is None:
            from tools.knowledge_retrieval_tool import get_knowledge_retrieval_tool

            self._knowledge_retrieval_tool = get_knowledge_retrieval_tool()
        return self._knowledge_retrieval_tool

    def _knowledge_inventory_tool_instance(self):
        if self._knowledge_inventory_tool is None:
            from tools.knowledge_inventory_tool import get_knowledge_inventory_tool

            self._knowledge_inventory_tool = get_knowledge_inventory_tool()
        return self._knowledge_inventory_tool

    def _graph_tool_instance(self):
        if self._graph_tool is None:
            from tools.graph_java_tool import get_java_graph_diagnosis_path_tool

            self._graph_tool = get_java_graph_diagnosis_path_tool()
        return self._graph_tool

    def _procedure_tool_instance(self):
        if self._procedure_tool is None:
            from tools.procedure_recommend_tool import get_procedure_recommend_tool

            self._procedure_tool = get_procedure_recommend_tool()
        return self._procedure_tool


_intent_fast_path_runner: Optional[IntentFastPathRunner] = None


def get_intent_fast_path_runner() -> IntentFastPathRunner:
    global _intent_fast_path_runner
    if _intent_fast_path_runner is None:
        _intent_fast_path_runner = IntentFastPathRunner()
    return _intent_fast_path_runner
