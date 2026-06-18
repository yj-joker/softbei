# Tools 模块

## 模块概述

Tools 是 Agent 的**能力扩展层**，将外部能力封装为统一接口，供 FixAgent 在 ReAct 循环中调用。

核心类 `BaseTool` 基于**模板方法模式**，统一异常处理 + OpenAI function calling schema 生成。

## 工具列表

| 工具 | 文件 | 描述 | 归属 Agent |
|------|------|------|-----------|
| `knowledge_retrieval` | knowledge_retrieval_tool.py | 多路知识检索，支持文本/表格/图片证据与图文混合 | FixAgent |
| `graph_query_diagnosis_path` | graph_query_tool.py | Neo4j 图谱诊断路径查询（5分支+向量匹配） | FixAgent |
| `graph_search_devices` | graph_query_tool.py | Neo4j 设备搜索 | FixAgent |
| `search_similar_facts` | fact_retrieval_tool.py | 事实向量检索（冲突检测） | MemoryAgent |
| `document_parser` | document_tool.py | 文档解析（PDF/Word/TXT） | 知识导入流程 |

## BaseTool 基类

所有工具继承 `BaseTool`，只需实现 `_execute()` 写业务逻辑：

```python
from tools.base_tool import BaseTool, ToolResult, ToolException

class MyTool(BaseTool):
    @property
    def name(self) -> str:
        return "my_tool"

    @property
    def description(self) -> str:
        return "工具描述，供 LLM 理解何时调用"

    async def _execute(self, **kwargs) -> Any:
        if failed:
            raise ToolException(code="MY_ERROR", message="业务错误描述")
        return result
```

- `run(**kwargs) -> ToolResult` — 模板方法，统一 try/execute/catch，返回结构化结果
- `to_openai_schema() -> dict` — 生成 OpenAI function calling 格式定义，供 `chat_with_tools()` 使用
- `get_parameters_schema() -> dict` — 默认空参数 schema，子类可覆盖

## FixAgent 中的工具注册

```python
class FixAgent(BaseAgent):
    def get_tools(self) -> List[BaseTool]:
        return [
            get_knowledge_retrieval_tool(),
            get_graph_query_tool(),
            get_graph_search_device_tool(),
        ]
```

FixAgent 持有全部工具，LLM 在 ReAct 循环中自主决定调用哪些工具、以什么顺序调用。

`knowledge_retrieval` 当前会按查询意图组合文本向量、关键词、表格、图片本体和图片 summary 候选，
再做候选合并、类型多样性控制和轻量 rerank。返回结果保留 `raw_score`、
`relevance_score`、`rerank_score`、`retrieval_routes`、`retrieval_confidence`，
图片证据通过 `metadata.image_url` 给前端展示原图。

## 文件结构

```
tools/
├── __init__.py
├── base_tool.py                  # 基类: BaseTool / ToolResult / ToolException / ToolError
├── knowledge_retrieval_tool.py   # 知识库向量检索
├── fact_retrieval_tool.py        # 事实向量检索（MemoryAgent 专用）
├── graph_query_tool.py           # Neo4j 图谱查询（诊断路径 + 设备搜索）
└── document_tool.py              # 文档解析（PDF/Word/TXT，文本/表格/图片提取）
```

## 与其他模块的关系

```
tools/
    ├── services/vector_service.py — 向量检索（knowledge_retrieval / fact_retrieval）
    ├── services/graph_service.py — 图谱查询（graph_query_tool）
    ├── embeddings/text_embedding.py — 向量生成（fact_retrieval / knowledge_retrieval）
    └── agents/fix_agent.py — 通过 get_tools() 注入 ReAct 循环
```

## 技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| 向量库 | Redis Search (FT.SEARCH KNN) | 国产环境友好、高性能 |
| 图数据库 | Neo4j (Cypher) | 国产化支持好、Java 生态完善 |

## 注意事项

1. **异常规范**：业务错误抛 `ToolException(code, message)`，未知异常自动捕获为 `code="TOOL_ERROR"`
2. **参数命名**：OpenAI schema 中使用 snake_case，LLM 输出时自动转换
3. **工具数量**：FixAgent 当前注册 3 个工具，LLM 决策准确率高于 5 个工具以上的场景
