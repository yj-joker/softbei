"""
Agent 层模块

用 LLM 自主完成某类任务的智能体（ReAct 与固定流程任务均属此类）。
零 LLM 的确定性护栏见 guardrails/；纯 JSON 函数集见 services/case/。

Agent 清单：
- FixAgent              — 统一诊断（ReAct，持有全部工具）
- MaintenanceAgent      — 检修步骤生成（RAG + 固定流程）
- MemoryAgent           — 工作记忆整理（function calling）
- MemoryReflectionAgent — 用户画像反思
- StepVerifyAgent       — 检修步骤多模态验证
- QuizAgent             — 画像驱动出题
"""

from .base_agent import BaseAgent, AgentInput, AgentOutput
from .fix_agent import FixAgent, get_fix_agent
from .maintenance_agent import MaintenanceAgent, get_maintenance_agent
from .memory_agent import MemoryAgent, get_memory_agent
from .reflection_agent import MemoryReflectionAgent, get_reflection_agent
from .step_verify_agent import StepVerifyAgent, get_step_verify_agent
from .quiz_agent import QuizAgent, get_quiz_agent

__all__ = [
    # 基类
    "BaseAgent",
    "AgentInput",
    "AgentOutput",
    # 统一诊断（ReAct）
    "FixAgent",
    "get_fix_agent",
    # 固定流程任务
    "MaintenanceAgent",
    "get_maintenance_agent",
    "MemoryAgent",
    "get_memory_agent",
    "MemoryReflectionAgent",
    "get_reflection_agent",
    "StepVerifyAgent",
    "get_step_verify_agent",
    "QuizAgent",
    "get_quiz_agent",
]
