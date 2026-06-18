"""
Agent 层模块

所有 AI Agent 的实现，采用 BaseAgent 模板方法模式 + ReAct function calling 架构。

Agent 清单：
- FixAgent            — 统一诊断 Agent（ReAct，持有全部工具）
- ReviewAgent         — 输出审核 Agent（3层确定性校验，零 LLM 调用）
- MemoryAgent         — 工作记忆整理 Agent（function calling）
"""

from .base_agent import BaseAgent, AgentInput, AgentOutput
from .fix_agent import FixAgent, get_fix_agent
from .review_agent import ReviewAgent, get_review_agent
from .memory_agent import MemoryAgent, get_memory_agent

__all__ = [
    # 基类
    "BaseAgent",
    "AgentInput",
    "AgentOutput",
    # 统一诊断
    "FixAgent",
    "get_fix_agent",
    # 输出审核（3层确定性校验）
    "ReviewAgent",
    "get_review_agent",
    # 记忆整理
    "MemoryAgent",
    "get_memory_agent",
]
