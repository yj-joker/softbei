"""
工具层模块

Agent 可调用的工具集合，每个工具封装一种外部能力。

基类：BaseTool — 模板方法模式，统一异常处理
已实现：
- KnowledgeRetrievalTool — 向量知识库检索
- FactRetrievalTool      — 历史事实批量检索（MemoryAgent 专用）
- GraphSearchDeviceTool  — 图谱设备搜索
- DocumentParserTool     — PDF/Word 文档解析
- ConversationDetailTool — 对话细节召回（FixAgent 按需调用）
- ProcedureRecommendTool — 标准作业流程推荐（FixAgent 按需调用）
"""

from .base_tool import BaseTool, ToolResult, ToolError, ToolException
from .knowledge_retrieval_tool import KnowledgeRetrievalTool, get_knowledge_retrieval_tool
from .fact_retrieval_tool import FactRetrievalTool, get_fact_retrieval_tool
from .graph_java_tool import (
    JavaGraphDeviceSearchTool,
    JavaGraphDiagnosisPathTool,
    get_java_graph_device_search_tool,
    get_java_graph_diagnosis_path_tool,
)
from .document_tool import DocumentParserTool, get_document_parser
from .conversation_detail_tool import ConversationDetailTool, get_conversation_detail_tool
from .procedure_recommend_tool import ProcedureRecommendTool, get_procedure_recommend_tool

__all__ = [
    # 基类
    "BaseTool",
    "ToolResult",
    "ToolError",
    "ToolException",
    # 知识检索
    "KnowledgeRetrievalTool",
    "get_knowledge_retrieval_tool",
    # 事实检索
    "FactRetrievalTool",
    "get_fact_retrieval_tool",
    # 图查询
    "JavaGraphDiagnosisPathTool",
    "JavaGraphDeviceSearchTool",
    "get_java_graph_diagnosis_path_tool",
    "get_java_graph_device_search_tool",
    # 文档解析
    "DocumentParserTool",
    "get_document_parser",
    # 对话细节召回
    "ConversationDetailTool",
    "get_conversation_detail_tool",
    # 标准流程推荐
    "ProcedureRecommendTool",
    "get_procedure_recommend_tool",
]
