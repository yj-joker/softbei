"""
工具基类模块

定义所有工具的抽象基类和统一的 ToolResult 返回模型。
被 tools/ 包下所有具体工具继承。

【与架构文档的对应关系】
- 位置：tools/base_tool.py
- 职责：统一工具接口，规范输入输出格式
- 被继承：KnowledgeRetrievalTool / GraphQueryTool / YoloTool / SamTool / DocumentTool

【设计模式】
- 模板方法模式：run() 定义统一执行流程（try/execute/catch），子类实现 _execute()
- 与 BaseAgent 保持一致风格

【ToolResult 设计说明】
- success + data 用于正常流程，Java 端按 data 类型解析
- error 是结构化对象（code + message），方便 Java 端按 code 分流处理
- tool_name 用于日志追踪

【ToolException 设计说明】
- 子类在 _execute() 中遇到特定错误时抛出 ToolException(code, message)
- run() 模板方法自动捕获 ToolException 并转为 ToolResult.failure
- 未预期的 Exception 也一并捕获，code="TOOL_ERROR"
"""

from abc import ABC, abstractmethod
from typing import Any, Optional
import logging

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ToolError(BaseModel):
    """工具执行错误模型"""
    code: str = Field(description="错误码，机器可读，如 EMBEDDING_FAILED")
    message: str = Field(description="错误描述，人类可读")


class ToolResult(BaseModel):
    """工具执行结果模型"""
    success: bool = Field(description="是否执行成功")
    data: Any = Field(default=None, description="成功时的返回数据")
    error: Optional[ToolError] = Field(default=None, description="失败时的错误信息")
    tool_name: str = Field(description="来源工具名")


class ToolException(Exception):
    """工具执行异常

    子类在 _execute() 中遇到预期的业务错误时抛出，携带错误码。
    run() 模板方法自动捕获并转为 ToolResult。
    """
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class BaseTool(ABC):
    """
    工具抽象基类

    所有具体工具继承此类，实现：
    - name: 工具标识
    - description: 工具描述（供 LLM function calling 注册）
    - _execute(): 核心业务逻辑

    模板方法 run() 统一处理：
    - 调用子类的 _execute()
    - 将返回值包装为 ToolResult(success=True)
    - 捕获 ToolException → ToolResult(success=False, error=...)
    - 捕获未知异常 → ToolResult(success=False, error.code="TOOL_ERROR")

    【使用示例】
    ```python
    class MyTool(BaseTool):
        @property
        def name(self) -> str:
            return "my_tool"

        @property
        def description(self) -> str:
            return "我的工具，做某某事"

        async def _execute(self, query: str) -> list:
            # 只写正常逻辑，异常抛 ToolException
            try:
                return await do_something(query)
            except APITimeout as e:
                raise ToolException(code="API_TIMEOUT", message=str(e))

    tool = MyTool()
    result = await tool.run(query="xxx")
    # result.success → True/False
    # result.error.code → "API_TIMEOUT" 或 "TOOL_ERROR"
    ```
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """工具标识"""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """工具描述"""
        pass

    @abstractmethod
    async def _execute(self, **kwargs) -> Any:
        """
        核心业务逻辑，子类实现

        只写正常流程，预期失败抛 ToolException(code, message)。
        无需手动包装 ToolResult —— run() 模板方法统一处理。
        """
        pass

    def get_parameters_schema(self) -> dict:
        """
        返回工具参数的 JSON Schema，供 LLM function calling 使用

        子类覆盖此方法以声明参数格式。默认返回空 schema（无参数工具）。
        """
        return {
            "type": "object",
            "properties": {},
            "required": []
        }

    def to_openai_schema(self) -> dict:
        """
        返回 OpenAI function calling 格式的工具定义

        供 BaseAgent.run_with_react() 使用，将工具列表转为 LLM 可识别的格式。
        子类可覆盖此方法（如 FactRetrievalTool）以提供完整自定义 schema。
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.get_parameters_schema()
            }
        }

    async def run(self, **kwargs) -> ToolResult:
        """
        模板方法：统一执行入口

        自动处理异常 → ToolResult，子类无需关心包装细节。
        """
        try:
            data = await self._execute(**kwargs)
            return self._success(data)
        except ToolException as e:
            return self._failure(code=e.code, message=e.message)
        except Exception as e:
            return self._failure(code="TOOL_ERROR", message=str(e))

    def _success(self, data: Any) -> ToolResult:
        """快捷构造成功结果"""
        return ToolResult(success=True, data=data, tool_name=self.name)

    def _failure(self, code: str, message: str) -> ToolResult:
        """快捷构造失败结果"""
        return ToolResult(
            success=False,
            tool_name=self.name,
            error=ToolError(code=code, message=message)
        )
