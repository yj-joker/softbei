"""
服务层模块

封装外部系统和第三方 API 的访问逻辑：
- LLMService     → 百炼大模型对话
- VectorService  → Redis Stack 向量检索
- KnowledgeService → 知识入库编排

注意：图谱查询已统一收敛到 Java 端，Python 不再直连 Neo4j。
Agent 通过 tools/graph_java_tool.py 调用 Java HTTP 接口。
"""

from .llm.service import LLMService, get_llm_service
from .knowledge.vector_service import VectorService, get_vector_service
from .knowledge.service import KnowledgeService, get_knowledge_service

__all__ = [
    "LLMService",
    "get_llm_service",
    "VectorService",
    "get_vector_service",
    "KnowledgeService",
    "get_knowledge_service",
]
