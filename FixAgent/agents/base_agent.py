"""
Agent基类模块

定义所有Agent的基类和通用接口。
采用模板方法模式，统一Agent执行流程。

【与架构文档的对应关系】
- 位置：agents/base_agent.py
- 职责：AI核心组件的父类，定义统一执行流程
- 被继承：FixAgent、ReviewAgent、MemoryAgent

【设计模式】
- 模板方法模式：run() 定义统一执行流程，子类实现具体逻辑
- 单例模式：各子Agent由调用方管理生命周期，BaseAgent不负责实例化
"""

import time
import asyncio
import logging
import json
import inspect
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, AsyncIterator, Callable
from datetime import datetime

from pydantic import BaseModel, Field

from services.llm.service import LLMService
from services.llm.react_loop import ReActLoop
from config.settings import get_settings

logger = logging.getLogger(__name__)


def _jsonable(value):
    return value.model_dump() if hasattr(value, "model_dump") else value


def _coerce_result_item(raw, index: int, max_chars: int) -> Dict[str, Any]:
    """把单条工具结果整理成 {title, content, type, page, score} 卡片。"""
    data = _jsonable(raw)
    if not isinstance(data, dict):
        return {"title": f"结果 {index + 1}", "content": str(data)[:max_chars]}
    meta = _jsonable(data.get("metadata")) or {}
    if not isinstance(meta, dict):
        meta = {}
    title = (
        meta.get("section_title")
        or meta.get("chunk_label")
        or data.get("title")
        or data.get("name")
        or data.get("deviceName")
        or data.get("faultName")
        or meta.get("document_id")
        or f"结果 {index + 1}"
    )
    content = (
        data.get("content")
        or data.get("text")
        or data.get("context")
        or data.get("summary")
        or data.get("description")
        or data.get("pathText")
        or ""
    )
    if not content:
        content = json.dumps(
            {k: v for k, v in data.items() if k != "metadata"},
            ensure_ascii=False,
        )
    score = data.get("relevance_score")
    if score is None:
        score = data.get("score")
    if score is None:
        score = data.get("matchScore")
    return {
        "title": str(title),
        "content": str(content)[:max_chars],
        "type": str(meta.get("source_chunk_type") or meta.get("chunk_type") or ""),
        "page": meta.get("page_number") or meta.get("page"),
        "score": round(float(score), 3) if isinstance(score, (int, float)) else None,
    }


def summarize_tool_result(tool_name: str, data, item_limit: int = 8, max_chars: int = 1200) -> Dict[str, Any]:
    """把工具返回结果整理成前端可读的内容：text（正文）+ items（结构化卡片）。

    - 列表结果（如知识检索）→ 拆成 items 卡片；
    - 带 context/text 的字典结果（如图谱）→ 直接用其可读正文；
    - 其他字典 → 取其中的子列表或兜底 JSON。
    """
    data = _jsonable(data)
    items: List[Dict[str, Any]] = []
    text = ""

    if isinstance(data, list):
        for index, raw in enumerate(data[:item_limit]):
            items.append(_coerce_result_item(raw, index, max_chars))
    elif isinstance(data, dict):
        text = str(data.get("context") or data.get("text") or "")
        if not text:
            for key in ("records", "raw_records", "devices", "cases", "items", "data", "evidence", "results"):
                sub = data.get(key)
                if isinstance(sub, list) and sub:
                    for index, raw in enumerate(sub[:item_limit]):
                        items.append(_coerce_result_item(raw, index, max_chars))
                    break
            if not items:
                text = json.dumps(data, ensure_ascii=False)
    else:
        text = str(data or "")

    return {"tool": tool_name, "text": text[:max_chars * 3], "items": items}


class AgentInput(BaseModel):
    """Agent输入模型"""
    user_message: str = Field(description="当前轮用户消息（纯文本）")
    session_id: str = Field(description="会话ID")
    images: Optional[List[str]] = Field(default=None, description="图片列表")
    context: Optional[Dict[str, Any]] = Field(default=None, description="结构化上下文（摘要、事实、偏好、待办）")
    conversation_history: Optional[List[Dict[str, str]]] = Field(default=None, description="多轮对话历史[{'role':'user','content':'...'}]")


class AgentRunContext(BaseModel):
    """Per-request state used by ReAct without mutating singleton agents."""
    user_message: str = ""
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    images: List[str] = Field(default_factory=list)
    enhanced_query: Optional[str] = None
    intent_decision: Dict[str, Any] = Field(default_factory=dict)
    allowed_tools: Optional[List[str]] = None
    retrieval_scope: Dict[str, Any] = Field(default_factory=dict)
    # 本轮用户消息毫秒时间戳：注入 save_memory 工具，供 Java 同轮写仲裁（漏洞#1）
    turn_ts: Optional[int] = None


class AgentOutput(BaseModel):
    """Agent输出模型"""
    agent_name: str = Field(description="Agent名称")
    message: str = Field(description="回复消息")
    intention: Optional[str] = Field(default=None, description="识别的意图")
    tools_used: List[str] = Field(default_factory=list, description="使用的工具")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")
    latency_ms: int = Field(default=0, description="执行时间")
    raw_response: Optional[Dict[str, Any]] = Field(default=None, description="原始响应")


class BaseAgent(ABC):
    """
    Agent基类

    所有专业Agent继承此类，实现：
    - name: Agent名称（抽象属性）
    - description: Agent描述（抽象属性）
    - get_system_prompt(): 返回角色定义提示词（抽象方法）
    - _execute(): 执行具体逻辑（抽象方法，可选覆盖）

    【执行流程（模板方法）】
    1. 构建消息列表（_build_messages）
    2. 调用LLM（_call_llm）
    3. 处理输出（_process_response）
    4. 返回结果（run）

    【使用示例】
    ```python
    class MyAgent(BaseAgent):
        @property
        def name(self) -> str:
            return "my_agent"

        @property
        def description(self) -> str:
            return "我的Agent"

        def get_system_prompt(self) -> str:
            return "你是一个专业的..."

        async def _execute(self, input_data: AgentInput) -> Dict[str, Any]:
            # 具体执行逻辑
            return {"message": "结果"}

    agent = MyAgent(llm_service)
    result = await agent.run(input_data)
    ```
    """

    def __init__(self, llm_service: LLMService):
        """
        初始化BaseAgent

        Args:
            llm_service: LLM服务实例，用于调用大模型
        """
        self.llm_service = llm_service

    @property
    @abstractmethod
    def name(self) -> str:
        """Agent名称"""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Agent描述"""
        pass

    @abstractmethod
    def get_system_prompt(self) -> str:
        """
        获取系统提示词

        应包含：
        - Agent角色定义
        - 能力范围
        - 输出格式要求

        Returns:
            系统提示词字符串
        """
        pass

    def get_tools(self) -> List[Any]:
        """
        获取可用工具列表

        默认返回空列表，子类可覆盖以提供具体工具。

        Returns:
            工具列表
        """
        return []

    def build_run_context(self, input_data: AgentInput) -> AgentRunContext:
        context = input_data.context or {}
        intent_decision = context.get("intent_decision") if isinstance(context.get("intent_decision"), dict) else {}
        policy = intent_decision.get("policy") if isinstance(intent_decision.get("policy"), dict) else {}
        allowed_tools = policy.get("tool_scope") or intent_decision.get("allowed_tools")
        return AgentRunContext(
            user_message=input_data.user_message or "",
            user_id=str(context["user_id"]) if context.get("user_id") else None,
            session_id=input_data.session_id,
            images=list(input_data.images or []),
            enhanced_query=str(context["enhanced_retrieval_query"]) if context.get("enhanced_retrieval_query") else None,
            intent_decision=dict(intent_decision),
            allowed_tools=[str(name) for name in allowed_tools] if isinstance(allowed_tools, list) else None,
            retrieval_scope=dict(context.get("retrieval_scope") or {}),
            turn_ts=context.get("turn_ts"),
        )

    def get_system_prompt_for_run(self, run_context: AgentRunContext) -> str:
        return self.get_system_prompt()

    def get_tools_for_run(self, run_context: AgentRunContext) -> List[Any]:
        return self.get_tools()

    def _customize_tool_kwargs_for_run(
        self,
        tool_name: str,
        kwargs: dict,
        run_context: AgentRunContext,
    ) -> dict:
        return self._customize_tool_kwargs(tool_name, kwargs)

    def _customize_tool_kwargs(self, tool_name: str, kwargs: dict) -> dict:
        """
        为特定工具注入额外参数的钩子方法

        在 ReAct 循环中，LLM 生成的工具调用参数会经过此方法处理，
        子类可覆盖以注入上下文信息（如 user_id）到特定工具中。

        Args:
            tool_name: 被调用的工具名
            kwargs: LLM 生成的原始参数

        Returns:
            处理后的参数字典
        """
        return kwargs

    @staticmethod
    def _summarize_for_log(value: Any, max_length: int = 500) -> str:
        """Return a compact, safe summary for console tool-call logs."""
        sensitive_keys = {"api_key", "token", "password", "secret", "authorization", "x-internal-token"}

        def sanitize(item):
            if isinstance(item, dict):
                cleaned = {}
                for key, val in item.items():
                    key_text = str(key)
                    if key_text.lower() in sensitive_keys or any(part in key_text.lower() for part in sensitive_keys):
                        cleaned[key_text] = "***"
                    else:
                        cleaned[key_text] = sanitize(val)
                return cleaned
            if isinstance(item, list):
                return [sanitize(val) for val in item[:5]]
            if hasattr(item, "model_dump"):
                return sanitize(item.model_dump())
            return item

        try:
            summary = json.dumps(sanitize(value), ensure_ascii=False, default=str)
        except Exception:
            summary = str(value)

        if len(summary) > max_length:
            return summary[:max_length] + "...(truncated)"
        return summary

    @staticmethod
    def _extract_tools_used_from_trace(react_trace: List[Dict[str, Any]]) -> List[str]:
        """Extract actual tool calls from ReAct trace, preserving first-use order."""
        tools_used: List[str] = []
        seen = set()
        for step in react_trace or []:
            if step.get("action") != "tool_call":
                continue
            for tool_call in step.get("tool_calls") or []:
                tool_name = tool_call.get("name")
                if tool_name and tool_name not in seen:
                    tools_used.append(tool_name)
                    seen.add(tool_name)
        return tools_used

    @staticmethod
    def _tool_accepts_event_sink(tool: Any) -> bool:
        target = getattr(tool, "_execute", None) or getattr(tool, "run", None)
        if target is None:
            return False
        try:
            signature = inspect.signature(target)
        except (TypeError, ValueError):
            return False
        return any(
            param_name == "_event_sink" or param.kind == inspect.Parameter.VAR_KEYWORD
            for param_name, param in signature.parameters.items()
        )

    @staticmethod
    async def _emit_stream_event(
        event_sink: Optional[Callable[[Dict[str, Any]], Any]],
        event: Dict[str, Any],
    ) -> None:
        if not event_sink:
            return
        result = event_sink(event)
        if inspect.isawaitable(result):
            await result

    def _build_messages(
        self,
        input_data: AgentInput,
        run_context: Optional[AgentRunContext] = None,
    ) -> List[Dict[str, str]]:
        """
        构建LLM消息列表（支持多轮对话历史和结构化上下文）

        消息结构：
        1. system: 角色定义 + 上下文信息（摘要/事实/偏好/待办）
        2. 历史对话: 按user/assistant交替排列（多轮记忆）
        3. 当前user消息: 本轮用户输入

        Args:
            input_data: Agent输入数据

        Returns:
            消息列表，格式：[{"role": "system", ...}, {"role": "user", ...}, {"role": "assistant", ...}, ...]
        """
        # ===== 1. 构建system prompt（角色 + 上下文） =====
        system_content = self.get_system_prompt_for_run(run_context or self.build_run_context(input_data))

        # 将结构化上下文注入system prompt，让LLM知道背景信息
        if input_data.context:
            context_parts = []

            # 检修任务场景上下文（步骤助手）—— 让 AI 知道工人当前在做哪个任务、哪一步
            m = input_data.context.get("maintenance")
            if isinstance(m, dict):
                lines = []
                t = m.get("task") or {}
                lines.append(
                    f"设备：{t.get('deviceName', '') or '未知'}；"
                    f"故障：{t.get('faultDescription', '') or '未填写'}；"
                    f"检修等级：{t.get('maintenanceLevel', '') or '未提供'}"
                )
                prog = m.get("progress") or {}
                if prog:
                    lines.append(
                        f"进度：当前第 {prog.get('current', '?')} 步 / 共 {prog.get('total', '?')} 步，"
                        f"已完成 {prog.get('done', 0)} 步"
                    )
                fs = m.get("focusedStep")
                if isinstance(fs, dict):
                    lines.append(f"\n【当前聚焦：第 {fs.get('sortOrder', '?')} 步】{fs.get('title', '')}")
                    if fs.get("content"):
                        lines.append(f"操作内容：{fs.get('content')}")
                    if fs.get("safetyNote"):
                        lines.append(f"安全提示：{fs.get('safetyNote')}")
                    if fs.get("checkpointItems"):
                        lines.append("检查点：" + "；".join(fs.get("checkpointItems") or []))
                    if fs.get("sources"):
                        lines.append(f"该步参考依据：{fs.get('sources')}")
                    # 执行态：让助手能针对性回答"这步为什么没过 / 怎么改 / 重传还是强制完成"
                    if fs.get("status"):
                        lines.append(f"该步当前状态：{fs.get('status')}")
                    if fs.get("aiReason"):
                        lines.append(f"AI 验收意见：{fs.get('aiReason')}")
                    if fs.get("note"):
                        lines.append(f"工人本步备注：{fs.get('note')}")
                ov = m.get("overview")
                if ov:
                    overview_lines = "\n".join(
                        f"{index}. {step}"
                        for index, step in enumerate(ov, start=1)
                    )
                    lines.append("\n全部步骤一览：\n" + overview_lines)
                rej = m.get("rejectedSteps")
                if rej:
                    rej_lines = "\n".join(
                        f"第 {r.get('sortOrder', '?')} 步「{r.get('title', '')}」未通过原因：{r.get('aiReason', '')}"
                        for r in rej
                    )
                    lines.append("\n未通过步骤的 AI 驳回理由：\n" + rej_lines)
                context_parts.append(
                    "当前检修任务：\n" + "\n".join(lines)
                    + "\n\n你是现场检修助手，正在与工人进行【连续的现场对话】。"
                    "请结合上面的任务背景、当前聚焦步骤、以及前面的对话内容来回答；"
                    "当工人追问「还不行怎么办 / 下一步呢」时，要承接前文继续给出可操作的排查与处置建议，必要时引用其它步骤。"
                    "工人若问到某一步的状态/为什么没过，优先用上面的『AI 验收意见』或『未通过步骤的 AI 驳回理由』直接解答，"
                    "并明确告诉他该怎么改、以及重新提交还是强制完成；"
                    "若工人问到某步但上文只有标题没有细节，请用步骤总览定位，并提示他『点一下那个步骤，我就能看到更多细节』。"
                    "即使知识库未直接命中，也应基于检修常识与现场情境给出务实、安全的建议，"
                    "不要简单回复「资料不足」；仅在涉及不确定的关键技术参数时提示「需现场确认」。"
                    "安全第一，简明可操作。"
                )

            # 之前的对话摘要
            if input_data.context.get("previous_summary"):
                context_parts.append(f"之前的对话摘要：\n{input_data.context['previous_summary']}")

            # 相关历史事实（向量检索得到，vector 模式）
            if input_data.context.get("relevant_facts"):
                facts = input_data.context["relevant_facts"]
                facts_str = "\n".join(
                    f"{index}. {f.get('text', f) if isinstance(f, dict) else f}"
                    for index, f in enumerate(facts, start=1)
                )
                context_parts.append(f"相关历史事实：\n{facts_str}")

            # 长期记忆目录（index 模式：注入记忆索引，条目格式 [名称] (类型) — 摘要）
            if input_data.context.get("memory_index"):
                context_parts.append(f"## 长期记忆目录\n{input_data.context['memory_index']}")

            # 用户偏好
            if input_data.context.get("user_preferences"):
                prefs = input_data.context["user_preferences"]
                prefs_str = "\n".join(
                    f"{index}. {p.get('content', p) if isinstance(p, dict) else p}"
                    for index, p in enumerate(prefs, start=1)
                )
                context_parts.append(f"用户偏好：\n{prefs_str}")

            # 会话偏好
            if input_data.context.get("session_preferences"):
                prefs = input_data.context["session_preferences"]
                prefs_str = "\n".join(
                    f"{index}. {p.get('content', p) if isinstance(p, dict) else p}"
                    for index, p in enumerate(prefs, start=1)
                )
                context_parts.append(f"当前会话偏好：\n{prefs_str}")

            # 未解决事项
            if input_data.context.get("unresolved_items"):
                items = input_data.context["unresolved_items"]
                items_str = "\n".join(
                    f"{index}. [{item.get('type', '未知')}] {item.get('content', item) if isinstance(item, dict) else item}"
                    for index, item in enumerate(items, start=1)
                )
                context_parts.append(f"待解决事项：\n{items_str}")

            # 用户画像
            if input_data.context.get("user_profile"):
                profiles = input_data.context["user_profile"]
                profile_str = "\n".join(
                    f"{index}. {profile.get('type', '未知')}：{profile.get('content', '')}"
                    for index, profile in enumerate(profiles, start=1)
                )
                context_parts.append(f"用户画像：\n{profile_str}")

            if context_parts:
                system_content += "\n\n以下是当前对话的背景信息，请据此回答用户问题：\n\n" + "\n\n".join(context_parts)

        messages = [{"role": "system", "content": system_content}]

        # ===== 2. 添加多轮对话历史（保持user/assistant角色） =====
        if input_data.conversation_history:
            for turn in input_data.conversation_history:
                role = turn.get("role", "user")
                content = turn.get("content", "")
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})

        # ===== 3. 添加当前轮用户消息 =====
        user_content = input_data.user_message

        # 添加图片信息（如有）—— 使用多模态消息格式
        if input_data.images:
            # 构建多模态 content：[{"type":"text","text":"..."},{"type":"image_url","image_url":{"url":"data:..."}}]
            content_parts = [{"type": "text", "text": user_content}]
            for img in input_data.images:
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": img}
                })
            messages.append({"role": "user", "content": content_parts})
        else:
            messages.append({"role": "user", "content": user_content})

        return messages

    async def _call_llm(
        self,
        messages: List[Dict[str, str]],
        stream: bool = False,
        model: Optional[str] = None,
        temperature: Optional[float] = None
    ) -> Dict[str, Any] | AsyncIterator[str]:
        """
        调用LLM服务

        Args:
            messages: 消息列表
            stream: 是否流式输出
            model: 模型覆盖（有图片时传 VLM 模型）
            temperature: 温度覆盖（不传则用全局默认）

        Returns:
            非流式：完整响应字典
            流式：异步生成器yield每个token
        """
        return await self.llm_service.chat(messages, stream=stream, model=model, temperature=temperature)

    def _process_response(
        self,
        raw_response: Dict[str, Any],
        tools_used: List[str],
        metadata: Dict[str, Any],
        intention: Optional[str] = None
    ) -> AgentOutput:
        """
        处理LLM原始响应，转换为AgentOutput

        Args:
            raw_response: LLM返回的原始响应
            tools_used: 使用的工具列表
            metadata: 附加元数据
            intention: 识别的用户意图

        Returns:
            AgentOutput对象
        """
        return AgentOutput(
            agent_name=self.name,
            message=raw_response.get("content", ""),
            intention=intention,
            tools_used=tools_used,
            metadata=metadata,
            raw_response=raw_response
        )

    async def run(self, input_data: AgentInput, temperature: Optional[float] = None) -> AgentOutput:
        """
        Agent执行入口（模板方法）

        执行流程：
        1. 构建消息
        2. 调用LLM
        3. 处理输出
        4. 返回结果

        异常处理：任意环节失败返回友好提示，
                  具体错误信息记录在 metadata 中供排查。

        Args:
            input_data: Agent输入数据
            temperature: 温度覆盖（不传则用全局默认，如需确定性输出可传 0.1）

        Returns:
            AgentOutput对象
        """
        start_time = time.time()

        try:
            # 1. 构建消息
            run_context = self.build_run_context(input_data)
            messages = self._build_messages(input_data, run_context)

            # 2. 有图片时切换为视觉模型
            model_override = None
            if input_data.images:
                model_override = get_settings().vlm_model
                logger.info(f"[{self.name}] 检测到图片，切换模型: {model_override}")

            # 3. 调用LLM（图片无效时降级为纯文本重试）
            try:
                response = await self._call_llm(messages, stream=False, model=model_override, temperature=temperature)
            except Exception as llm_err:
                if input_data.images and "400" in str(llm_err):
                    logger.warning(f"[{self.name}] 多模态调用失败(可能图片URL无效)，降级为纯文本重试: {llm_err}")
                    # 去掉图片，用纯文本消息重建
                    input_data_fallback = AgentInput(
                        user_message=input_data.user_message,
                        session_id=input_data.session_id,
                        images=None,  # 清除图片
                        context=input_data.context,
                        conversation_history=input_data.conversation_history,
                    )
                    messages = self._build_messages(input_data_fallback)
                    model_override = None
                    response = await self._call_llm(messages, stream=False, model=model_override, temperature=temperature)
                else:
                    raise

            # 3. 处理输出
            intention = input_data.context.get("intention") if input_data.context else None
            output = self._process_response(
                raw_response=response,
                tools_used=self.get_tools_used(input_data),
                metadata={"latency_ms": 0},
                intention=intention
            )
            output.latency_ms = int((time.time() - start_time) * 1000)
            return output

        except Exception as e:
            latency_ms = int((time.time() - start_time) * 1000)
            return AgentOutput(
                agent_name=self.name,
                message="AI服务暂时不可用，请稍后重试",
                intention=None,
                tools_used=[],
                metadata={
                    "status": "error",
                    "error_type": type(e).__name__,
                    "error_detail": str(e) or type(e).__name__,
                    "latency_ms": latency_ms
                },
                latency_ms=latency_ms
            )

    async def run_with_react(
        self,
        input_data: AgentInput,
        max_iterations: int = 10,
        _event_sink: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> AgentOutput:
        """
        ReAct 模式执行入口

        使用 LLM function calling 实现 Thought → Action → Observation 循环。
        LLM 自主决定每步调用哪个工具、何时结束、何时追问用户。

        流程：
        1. 构建消息（系统提示词 + 用户输入）
        2. 收集子类提供的工具列表
        3. 调用 chat_with_tools() 进入 ReAct 循环
        4. LLM 返回最终文本响应后退出循环
        5. 包装为 AgentOutput 返回

        Args:
            input_data: Agent 输入数据
            max_iterations: 最大工具调用轮数（防止无限循环）

        Returns:
            AgentOutput 对象
        """
        start_time = time.time()

        try:
            # 1. 构建消息
            run_context = self.build_run_context(input_data)
            messages = self._build_messages(input_data, run_context)

            # 2. 获取工具列表，转为 OpenAI schema + handler 映射
            tools = self.get_tools_for_run(run_context)
            tool_schemas = [t.to_openai_schema() for t in tools]
            tool_handlers = {}
            for tool in tools:
                def _make_handler(t):
                    async def handler(**kwargs):
                        # 允许子类为特定工具注入额外参数
                        kwargs = self._customize_tool_kwargs_for_run(t.name, kwargs, run_context)
                        tool_start = time.time()
                        logger.info(
                            "[%s][tool_start] tool=%s args=%s",
                            self.name,
                            t.name,
                            self._summarize_for_log(kwargs),
                        )
                        await self._emit_stream_event(
                            _event_sink,
                            {"event": "tool", "data": {"tool": t.name}},
                        )
                        if _event_sink and self._tool_accepts_event_sink(t):
                            kwargs["_event_sink"] = _event_sink
                        try:
                            result = await t.run(**kwargs)
                        except Exception as tool_exc:
                            duration_ms = int((time.time() - tool_start) * 1000)
                            logger.exception(
                                "[%s][tool_exception] tool=%s duration_ms=%s",
                                self.name,
                                t.name,
                                duration_ms,
                            )
                            try:
                                await self._emit_stream_event(
                                    _event_sink,
                                    {
                                        "event": "tool_result",
                                        "data": {"tool": t.name, "text": f"调用异常：{tool_exc}", "items": []},
                                    },
                                )
                            except Exception:
                                logger.exception("[%s][tool_result_emit_failed] tool=%s", self.name, t.name)
                            raise

                        duration_ms = int((time.time() - tool_start) * 1000)
                        if result.success:
                            logger.info(
                                "[%s][tool_success] tool=%s duration_ms=%s result=%s",
                                self.name,
                                t.name,
                                duration_ms,
                                self._summarize_for_log(result.data),
                            )
                            try:
                                await self._emit_stream_event(
                                    _event_sink,
                                    {
                                        "event": "tool_result",
                                        "data": summarize_tool_result(t.name, result.data),
                                    },
                                )
                            except Exception:
                                logger.exception("[%s][tool_result_emit_failed] tool=%s", self.name, t.name)
                            return result.data if result.data is not None else {"result": "success"}
                        else:
                            logger.warning(
                                "[%s][tool_failure] tool=%s duration_ms=%s error=%s",
                                self.name,
                                t.name,
                                duration_ms,
                                self._summarize_for_log(result.error),
                            )
                            err_msg = result.error.message if result.error else "unknown error"
                            try:
                                await self._emit_stream_event(
                                    _event_sink,
                                    {
                                        "event": "tool_result",
                                        "data": {"tool": t.name, "text": f"调用失败：{err_msg}", "items": []},
                                    },
                                )
                            except Exception:
                                logger.exception("[%s][tool_result_emit_failed] tool=%s", self.name, t.name)
                            return {"error": err_msg}
                    return handler
                tool_handlers[tool.name] = _make_handler(tool)

            # 3. 有图片时自动切换为视觉模型
            model_override = None
            if input_data.images:
                model_override = get_settings().vlm_model
                logger.info(f"[{self.name}] 检测到图片，切换模型: {model_override}")

            # 4. ReAct 循环（chat_with_tools 内部自动处理）
            response = await ReActLoop(self.llm_service).run(
                messages=messages,
                tools=tool_schemas,
                tool_handlers=tool_handlers,
                max_iterations=max_iterations,
                model=model_override
            )

            # 5. 处理响应
            intention = input_data.context.get("intention") if input_data.context else None
            react_trace = response.get("trace", [])
            tools_used = self._extract_tools_used_from_trace(react_trace)
            output = self._process_response(
                raw_response=response,
                tools_used=tools_used,
                metadata={
                    "execution_mode": "react",
                    "react_trace": react_trace,
                    "react_iterations": len(react_trace)
                },
                intention=intention
            )
            output.latency_ms = int((time.time() - start_time) * 1000)
            return output

        except RuntimeError as e:
            # 工具调用超出最大迭代次数
            latency_ms = int((time.time() - start_time) * 1000)
            return AgentOutput(
                agent_name=self.name,
                message="AI推理步骤超出限制，请尝试简化问题后重新提问。",
                intention=None,
                tools_used=[],
                metadata={
                    "status": "max_iterations_exceeded",
                    "error_detail": str(e),
                    "latency_ms": latency_ms
                },
                latency_ms=latency_ms
            )
        except Exception as e:
            latency_ms = int((time.time() - start_time) * 1000)
            return AgentOutput(
                agent_name=self.name,
                message="AI服务暂时不可用，请稍后重试",
                intention=None,
                tools_used=[],
                metadata={
                    "status": "error",
                    "error_type": type(e).__name__,
                    "error_detail": str(e),
                    "latency_ms": latency_ms
                },
                latency_ms=latency_ms
            )

    async def run_with_react_stream(
        self,
        input_data: AgentInput,
        max_iterations: int = 10
    ) -> AsyncIterator[dict]:
        """
        ReAct 模式流式执行入口

        先执行 ReAct 循环（工具调用阶段），完成后将最终回答和工具调用
        追踪以结构化事件流的形式逐 token 输出。

        与 run_with_react 的区别：
        - run_with_react: 返回 AgentOutput，适合非流式 API
        - run_with_react_stream: yield 事件 dict，适合 SSE 流式 API

        事件格式：
        - {"event": "status", "data": {"stage": "...", "mode": "..."}}
        - {"event": "tool", "data": {"tool": "knowledge_retrieval"}}
        - {"event": "token", "data": {"content": "..."}}
        - {"event": "done", "data": {}}
        - {"event": "error", "data": {"message": "..."}}
        """
        start_time = time.time()

        yield {
            "event": "status",
            "data": {"stage": f"{self.description}，正在分析...", "mode": self.name}
        }

        try:
            progress_queue: asyncio.Queue = asyncio.Queue()

            async def emit_progress(event: Dict[str, Any]) -> None:
                await progress_queue.put(event)

            run_task = asyncio.create_task(
                self.run_with_react(
                    input_data,
                    max_iterations,
                    _event_sink=emit_progress,
                )
            )

            while not run_task.done() or not progress_queue.empty():
                try:
                    progress_event = await asyncio.wait_for(progress_queue.get(), timeout=0.05)
                except asyncio.TimeoutError:
                    continue
                yield progress_event

            output = await run_task

            if output.metadata.get("status") == "error":
                yield {"event": "error", "data": {"message": output.message}}
                yield {"event": "done", "data": {}}
                return

            # 输出工具调用事件
            react_trace = output.metadata.get("react_trace", [])

            # 逐字流式输出最终回答
            message = output.message
            for i in range(0, len(message)):
                yield {"event": "token", "data": {"content": message[i]}}
                if i % 15 == 0:
                    await asyncio.sleep(0)

            # 附加耗时和 react_trace（供下游验证管线使用）
            latency = output.latency_ms or int((time.time() - start_time) * 1000)
            yield {
                "event": "done",
                "data": {
                    "latency_ms": latency,
                    "react_trace": react_trace,
                    "tools_used": output.tools_used,
                    "metadata": output.metadata,
                }
            }

        except Exception as e:
            yield {"event": "error", "data": {"message": str(e)}}
            yield {"event": "done", "data": {}}

    async def run_stream(self, input_data: AgentInput) -> AsyncIterator[str]:
        """
        Agent流式执行入口

        Args:
            input_data: Agent输入数据

        Yields:
            每个token
        """
        messages = self._build_messages(input_data)
        model_override = get_settings().vlm_model if input_data.images else None
        stream_iter = await self._call_llm(messages, stream=True, model=model_override)

        async for token in stream_iter:
            yield token

    def get_tools_used(self, input_data: AgentInput) -> List[str]:
        """
        获取本次执行使用的工具列表

        默认返回空列表，子类可覆盖以记录实际使用的工具。

        Args:
            input_data: Agent输入数据

        Returns:
            工具名称列表
        """
        return []

    async def run_with_context(
        self,
        user_message: str,
        session_id: str,
        images: Optional[List[str]] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> AgentOutput:
        """
        便捷执行方法

        创建一个AgentInput并执行。

        Args:
            user_message: 用户消息
            session_id: 会话ID
            images: 图片列表（可选）
            context: 上下文信息（可选）

        Returns:
            AgentOutput对象
        """
        input_data = AgentInput(
            user_message=user_message,
            session_id=session_id,
            images=images,
            context=context
        )
        return await self.run(input_data)
