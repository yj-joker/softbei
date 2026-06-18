"""
数据模型模块

Pydantic v2 模型定义，涵盖枚举、请求体、响应体、领域对象。

子模块：
- models.py   — 枚举、基础响应、领域对象
- request.py  — 所有接口的请求体
- response.py — 所有接口的响应体
- memory.py   — MemoryAgent 专用内存结构
"""

from .models import (
    AgentMode,
    VectorSearchResult,
    BaseResponse,
    ErrorResponse,
)
from .request import (
    ChatRequest,
    KnowledgeImportRequest,
    KnowledgeSearchRequest,
    MemoryConsolidateRequest,
)
from .response import (
    ChatResponse,
    KnowledgeImportResponse,
    KnowledgeSearchResponse,
    MemoryConsolidateResponse,
)

__all__ = [
    # 枚举 & 基础模型
    "AgentMode",
    "VectorSearchResult",
    "BaseResponse",
    "ErrorResponse",
    # 请求
    "ChatRequest",
    "KnowledgeImportRequest",
    "KnowledgeSearchRequest",
    "MemoryConsolidateRequest",
    # 响应
    "ChatResponse",
    "KnowledgeImportResponse",
    "KnowledgeSearchResponse",
    "MemoryConsolidateResponse",
]

