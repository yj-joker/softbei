import json
import time
import logging
import httpx
from typing import AsyncIterator, Optional, List, Dict, Any, Callable, Awaitable
from config.settings import get_settings
from services.react_loop import _json_compatible

logger = logging.getLogger(__name__)


class LLMService:
    """
    大模型服务类

    封装阿里云百炼API，支持：
    - 同步/异步对话
    - 流式输出
    - 多轮对话上下文
    - Function calling（工具调用）
    """

    def __init__(self):
        self.settings = get_settings()
        self.api_key = self.settings.dashscope_api_key
        self.model = self.settings.llm_model
        self.api_base = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(180.0,connect=10.0),
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100)
        )

    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stream: bool = False,
        response_format: Optional[Dict[str, str]] = None,
        model: Optional[str] = None,
        seed: Optional[int] = None
    ) -> Dict[str, Any] | AsyncIterator[str]:
        """
        对话接口

        Args:
            messages: 对话消息列表，格式：[{"role": "user", "content": "..."}]
            temperature: 温度参数，控制随机性
            max_tokens: 最大生成长度
            stream: 是否流式输出
            response_format: 输出格式约束，如 {"type": "json_object"}
            model: 模型覆盖（有图片时传 VLM 模型）

        Returns:
            非流式：完整响应字典
            流式：异步生成器yield每个token
        """
        use_model = model or self.model
        params = {
            "model": use_model,
            "messages": messages,
            # 用 is not None 判断，避免 temperature=0 被 `or` 当成 falsy 而回退默认值
            "temperature": temperature if temperature is not None else self.settings.llm_temperature,
            "top_p": self.settings.llm_top_p,
            "max_tokens": max_tokens or self.settings.llm_max_tokens
        }

        if seed is not None:
            params["seed"] = seed

        if response_format:
            params["response_format"] = response_format

        if stream:
            params["stream"] = True

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        logger.debug(f"[llm] 对话调用 模型={use_model} 流式={stream} 消息数={len(messages)}")
        if stream:
            return self._stream_chat(self.client, headers, params)
        else:
            return await self._sync_chat(self.client, headers, params)

    async def _sync_chat(
        self,
        client: httpx.AsyncClient,
        headers: Dict,
        params: Dict
    ) -> Dict[str, Any]:
        """同步对话"""
        response = await client.post(
            f"{self.api_base}/chat/completions",
            headers=headers,
            json=params
        )
        response.raise_for_status()
        result = response.json()

        if "choices" in result:
            return {
                "content": result["choices"][0]["message"]["content"],
                "usage": result.get("usage", {}),
                "request_id": result.get("id")
            }
        return result

    async def _stream_chat(
        self,
        client: httpx.AsyncClient,
        headers: Dict,
        params: Dict
    ) -> AsyncIterator[str]:
        """流式对话，返回token生成器"""
        async with client.stream(
            "POST",
            f"{self.api_base}/chat/completions",
            headers=headers,
            json=params
        ) as response:
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    data_str = line[5:].strip()
                    if data_str:
                        try:
                            data = json.loads(data_str)
                            if "choices" in data and data["choices"]:
                                token = data["choices"][0]["delta"].get("content", "")
                                if token:
                                    yield token
                        except json.JSONDecodeError:
                            continue

    async def _sync_chat_with_tools(
        self,
        client: httpx.AsyncClient,
        headers: Dict,
        params: Dict
    ) -> Dict[str, Any]:
        """带工具调用的同步对话（内部方法）"""
        response = await client.post(
            f"{self.api_base}/chat/completions",
            headers=headers,
            json=params
        )
        response.raise_for_status()
        result = response.json()

        if "choices" in result and result["choices"]:
            choice = result["choices"][0]
            message = choice.get("message", {})
            return {
                "content": message.get("content", ""),
                "tool_calls": message.get("tool_calls", []),
                "finish_reason": choice.get("finish_reason", "stop"),
                "usage": result.get("usage", {}),
                "request_id": result.get("id")
            }
        return {"content": "", "tool_calls": [], "finish_reason": "error"}

    async def complete_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        response_format: Optional[Dict[str, str]] = None,
        model: Optional[str] = None
    ) -> Dict[str, Any]:
        """Run one non-streaming chat-completion request with optional tool schemas."""
        use_model = model or self.model
        params = {
            "model": use_model,
            "messages": messages,
            "temperature": self.settings.llm_temperature,
            "top_p": self.settings.llm_top_p,
            "max_tokens": self.settings.llm_max_tokens
        }

        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"

        if response_format:
            params["response_format"] = response_format

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        return await self._sync_chat_with_tools(self.client, headers, params)

    async def chat_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        tool_handlers: Dict[str, Callable[..., Awaitable]],
        max_iterations: int = 10,
        response_format: Optional[Dict[str, str]] = None,
        model: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        带工具调用的对话

        自动处理 LLM 的工具调用循环：
        1. 发送 messages + tools 给 LLM
        2. LLM 返回 tool_calls → 执行对应工具 → 结果追加到 messages
        3. 回到步骤2，直到 LLM 返回最终文本响应

        Args:
            messages: 对话消息列表
            tools: OpenAI 格式的工具定义列表
            tool_handlers: {"工具名": async_handler} 映射
            max_iterations: 最大工具调用轮数
            response_format: 输出格式约束，如 {"type": "json_object"}
            model: 模型覆盖（有图片时传 VLM 模型）

        Returns:
            最终响应字典，包含 content / usage / request_id / trace
        """
        use_model = model or self.model
        params = {
            "model": use_model,
            "messages": messages,
            "temperature": self.settings.llm_temperature,
            "top_p": self.settings.llm_top_p,
            "max_tokens": self.settings.llm_max_tokens
        }

        # 仅在有工具时才传 tools 和 tool_choice，避免空数组导致API报错
        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"

        if response_format:
            params["response_format"] = response_format

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        trace = []

        for i in range(max_iterations):
            step_start = time.time()
            response = await self._sync_chat_with_tools(self.client, headers, params)

            tool_calls = response.get("tool_calls", [])
            step_duration_ms = int((time.time() - step_start) * 1000)

            if not tool_calls:
                trace.append({
                    "iteration": i + 1,
                    "action": "finish",
                    "content_preview": (response.get("content") or "")[:100],
                    "duration_ms": step_duration_ms
                })
                response["trace"] = trace
                logger.info(f"[llm] 工具调用完成 迭代次数={i+1} 最后一步耗时={step_duration_ms}ms")
                return response

            # 收集本轮工具调用详情
            step_info = {
                "iteration": i + 1,
                "action": "tool_call",
                "duration_ms": step_duration_ms,
                "tool_calls": []
            }

            # 添加 assistant 消息（含 tool_calls）
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": tool_calls
            })

            # 执行每个工具调用
            for tc in tool_calls:
                func_name = tc["function"]["name"]
                try:
                    func_args = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    func_args = {}

                call_record = {
                    "name": func_name,
                    "arguments": func_args
                }

                if func_name in tool_handlers:
                    try:
                        result = await tool_handlers[func_name](**func_args)
                    except Exception as e:
                        result = {"error": str(e)}

                    result_payload = _json_compatible(result)
                    call_record["result_summary"] = str(result_payload)[:200]
                    call_record["result_data"] = result_payload
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(result_payload, ensure_ascii=False)
                    })
                else:
                    call_record["result_summary"] = f"tool not found: {func_name}"
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps({"error": f"Tool {func_name} not found"}, ensure_ascii=False)
                    })

                step_info["tool_calls"].append(call_record)

            trace.append(step_info)
            params["messages"] = messages

        raise RuntimeError(f"Tool calling 超出最大迭代次数 ({max_iterations})")


# 单例模式
_llm_service: Optional[LLMService] = None


def get_llm_service() -> LLMService:
    """获取LLM服务单例"""
    """保证全局只有一个 LLMService 实例,连接资源复用"""
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service
