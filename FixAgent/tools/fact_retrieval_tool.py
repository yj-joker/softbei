"""
事实检索工具

供 MemoryAgent 的 LLM function calling 调用。
LLM 提取候选事实后，批量搜索向量库中相似的历史事实，用于冲突检测。

【调用链】
LLM tool_call → _execute(queries) → VectorService.search_by_text() → 返回相似事实列表

【与其他模块的关系】
- 上游：MemoryAgent（通过 function calling）
- 下游：services/vector_service.py → Redis 向量库
- 继承：tools/base_tool.py 的 BaseTool
"""

import asyncio
import logging
from typing import List, Optional

from tools.base_tool import BaseTool

logger = logging.getLogger(__name__)


class FactRetrievalTool(BaseTool):
    """
    事实向量检索工具

    接收多个候选事实关键词，在向量库中检索相似历史事实。
    返回 Top-3 相似结果供 LLM 判断冲突。
    """

    @property
    def name(self) -> str:
        return "search_similar_facts"

    @property
    def description(self) -> str:
        return (
            "在已有事实库中批量搜索与候选事实语义相似的历史事实。"
            "先收集所有候选事实的关键词，一次性批量调用此工具。"
            "返回每条关键词对应的 Top-3 相似事实。"
        )

    def to_openai_schema(self) -> dict:
        """返回 OpenAI function calling 格式的工具定义（覆盖基类以提供完整参数schema）"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "queries": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "候选事实的关键词列表，每个元素是一条候选事实的核心关键词"
                                "（如设备型号、错误码、故障部位），不是完整句子"
                            )
                        }
                    },
                    "required": ["queries"]
                }
            }
        }

    async def _execute(self, queries: list, top_k: int = 3) -> dict:
        """
        批量检索相似历史事实

        Args:
            queries: 候选事实关键词列表
            top_k: 每条关键词返回的最相似结果数

        Returns:
            {"results": {"关键词1": [{"id": "", "content": "", "score": 0.92}, ...], ...}}
        """
        from services.vector_service import build_redis_filter, get_vector_service

        vector_service = get_vector_service()
        fact_filter = build_redis_filter(record_type="fact", status="active")

        async def search_one(query: str) -> tuple:
            results = await vector_service.search_by_text(query, top_k=top_k, filter=fact_filter)
            similar = [
                {
                    "id": r["doc_id"],
                    "content": r.get("text", ""),
                    "score": round(r["score"], 4)
                }
                for r in results
            ]
            return query, similar

        tasks = [search_one(q) for q in queries]
        pairs = await asyncio.gather(*tasks)

        return {"results": dict(pairs)}


# 单例
_fact_retrieval_tool: Optional[FactRetrievalTool] = None


def get_fact_retrieval_tool() -> FactRetrievalTool:
    global _fact_retrieval_tool
    if _fact_retrieval_tool is None:
        _fact_retrieval_tool = FactRetrievalTool()
    return _fact_retrieval_tool
