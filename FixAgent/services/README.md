# Services 模块

## 模块概述

Services 是系统的**核心服务层**，封装与外部服务的交互：

- **LLMService** — 阿里云百炼 DashScope API，对话 + function calling
- **VectorService** — Redis Stack 向量库，KNN 检索 + metadata 过滤
- **GraphService** — Neo4j 图数据库，设备-部件-故障因果链查询

设计原则：单一职责、接口统一、错误处理、异步优先、单例复用。

## 服务列表

| 服务 | 文件 | 职责 | 外部依赖 |
|------|------|------|---------|
| `llm_service` | llm_service.py | 大模型对话 + function calling + ReAct 循环 | 阿里云百炼 DashScope |
| `vector_service` | vector_service.py | 向量存储 / 检索 / 删除 / 计数 | Redis Stack (FT.SEARCH) |
| `graph_service` | graph_service.py | Neo4j 只读查询 + Cypher 路径查询 | Neo4j |
| `knowledge_service` | knowledge_service.py | 文档导入编排：解析→向量化→入库 | DocumentParserTool + TextEmbedding + ImageEmbedding + VectorService |
| `file_storage` | file_storage.py | PDF/提取图片的本地静态文件或 MinIO URL 适配 | 本地文件系统 / MinIO |
| `image_summary_service` | image_summary_service.py | 图片标题与语义摘要生成，支持多模态 LLM 和上下文回退 | LLMService |
| `retrieval_policy` | retrieval_policy.py | 意图识别、轻量 rerank、类型多样性与置信度策略 | 无 |

## LLMService — 核心接口

```python
# chat() — 普通对话
response = await llm_service.chat(messages)  # [{"role": "user", "content": "..."}]
response["content"]  # 文本回复

# chat(stream=True) — 流式
async for token in llm_service.chat(messages, stream=True):
    print(token, end="", flush=True)

# chat_with_tools() — ReAct 循环（含 trace）
response = await llm_service.chat_with_tools(messages, tools, handlers)
response["content"]      # 最终文本回复
response["trace"]       # 每轮工具调用记录
response["usage"]       # token 用量统计

# response_format — JSON 约束（MemoryAgent 专用）
response = await llm_service.chat_with_tools(..., response_format={"type": "json_object"})
```

## VectorService — 核心接口

```python
# 批量添加向量
vector_service.add_vector(doc_id, text, vector, metadata, category, tags)

# 按向量检索
results = vector_service.search(vector, top_k=5, filter=filter_expr)
# 返回: [{"doc_id": "...", "text": "...", "score": 0.92, "metadata": {...}}]

# 按文本检索（自动向量化）
results = await vector_service.search_by_text("电动机轴承过热", top_k=5)
```

索引 schema： `id(TEXT) text(TEXT) vector(VECTOR,HNSW,6,FLOAT32,1024,COSINE) metadata(TEXT) category(TAG) tags(TAG) created_at(NUMERIC)`

## GraphService — 核心接口

```python
# 查询诊断路径（设备 → 部件 → 故障 → 解决方案，5分支策略 + 向量匹配）
result = graph_service.find_diagnosis_paths(
    keyword="电动机",
    component_ids=["comp_001"],
    fault_ids=["fault_001"],
    component_score_map={"comp_001": 0.92},
    fault_score_map={"fault_001": 0.88},
    page=0, size=5
)

# 设备搜索
devices = graph_service.find_devices(keyword="电动机", limit=10)

# 设备概览
overview = graph_service.get_device_overview(device_id)

# 按部件查故障
faults = graph_service.find_faults_by_component(component_id, limit=10)

# 按故障查解决方案
solutions = graph_service.find_solutions_by_fault(fault_id, verified_only=True)

# Neo4j 向量索引检索（需预先建索引）
components = graph_service.search_components_by_embedding(embedding, limit=20, min_score=0.50)
faults = graph_service.search_faults_by_embedding(embedding, limit=20, min_score=0.80)
```

## KnowledgeService — 核心接口

```python
from services.knowledge_service import get_knowledge_service

svc = get_knowledge_service()

# 导入文档：解析 PDF → 向量化 → 存入 Redis 向量库
result = await svc.import_document(
    file_url="/path/to/manual.pdf",
    file_type="pdf",
    category="维修手册",
    tags=["电动机", "轴承"],
    document_id="motor-manual-v1",
    device_type="motorcycle_engine",
    manual_type="repair_manual",
    document_version="v1",
    replace_existing=True
)
# result: {
#   "file_name": "...",
#   "total_pages": N,
#   "text_count": 125,      # 入库文本块数
#   "image_count": 30,      # 入库图片数
#   "table_count": 12,     # 入库表格数
#   "sections": [...],
#   "process_time_ms": 3200
# }
```

编排流程：

1. `DocumentParserTool` 解析 PDF，产出文本块、表格和图片元数据。
2. 文本块与表格走 `TextEmbedding` 后写入 `VectorService`。
3. 本地模式把 PDF 和提取图片复制到静态目录，MinIO 模式上传或复用对象 URL，确保可展示的原文件引用存在。
4. 图片带可访问 `image_url` 时走 `ImageEmbedding`，写入图片本体向量。
5. 图片同时生成 `image_title` / `image_summary`，`image_summary` 另走文本向量入库。
6. 图片没有可访问 URL 时保留回退逻辑，使用图注或“章节 + 第 X 页插图”文本走 `TextEmbedding`。

图片记录会在 `metadata` 中保留 `image_name`、`local_path`、`image_url`、`caption`、`page` 和 `embedding_source`：

- `embedding_source=image` 表示当前 Redis 记录使用图片本体向量。
- `embedding_source=caption_text` 表示当前 Redis 记录使用图注/默认图片文本向量，通常出现在对象存储 URL 尚未接入的本地导入流程中。
- 前端展示原图应使用 `metadata.image_url`；向量只用于检索，不能反向还原图片。

## 文件结构

```
services/
├── __init__.py
├── llm_service.py              # LLM 调用（chat / chat_with_tools / ReAct trace）
├── vector_service.py           # Redis 向量库（search / add_vector / add_vector_batch）
├── graph_service.py            # Neo4j 图数据库（find_diagnosis_paths 5分支 / find_* 只读查询 / 向量索引检索）
├── knowledge_service.py        # 文档导入编排（import_document: 解析→向量化→入库）
├── file_storage.py             # 本地静态文件 / MinIO URL 与上传适配
├── image_summary_service.py    # 图片语义摘要
├── retrieval_policy.py         # 检索排序与置信度策略
└── retrieval_eval.py           # 检索评测入口
```

## 日志输出点

关键位置输出 DEBUG/INFO 级别日志：

| 位置 | 级别 | 内容 |
|------|------|------|
| `llm_service.py` chat() | DEBUG | model / stream / msg_count |
| `llm_service.py` chat_with_tools() finish | INFO | 迭代次数、总耗时 |

## 技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| 大模型 | 阿里云百炼 DashScope (qwen-plus) | 国产、Qwen系列强、API稳定 |
| 向量库 | Redis Stack (FT.SEARCH KNN) | 国产环境友好、高性能 |
| 图数据库 | Neo4j | 国产化支持好、Java生态完善、Cypher简洁 |

## 注意事项

1. **连接池**：`httpx.AsyncClient(timeout=60s)` 复用连接（max_keepalive_connections=20）
2. **单例**：每个服务通过 `get_xxx_service()` 全局单例，避免重复创建连接
3. **向量维度**：统一 1024 维（text-embedding-v4），Redis schema 在首次访问时自动创建
4. **异步**：所有 I/O 操作使用 async/await，不阻塞事件循环
5. **Redis 索引迁移**：`add_vector` 时自动 FT.ALTER 添加 category/tags 字段（静默忽略已存在错误）
