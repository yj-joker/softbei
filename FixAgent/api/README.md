# API 模块

## 模块职责

FastAPI Web 服务入口，HTTP 接口定义、请求路由、参数校验。所有 AI 推理逻辑和输出校验由 `agents/` 完成，业务数据由 Java 后端管理。

## 接口列表

| 接口 | 方法 | 描述 | 状态 |
|------|------|------|------|
| `/ai/chat` | POST | 对话接口（FixAgent → ReviewAgent） | **已实现** |
| `/ai/chat/stream` | POST | SSE 流式响应（内联验证标记） | **已实现** |
| `/ai/knowledge/search` | POST | 直接调用 VectorService 检索 | **已实现** |
| `/ai/knowledge/import` | POST | 文档解析→向量化→入库管道 | **已实现** |
| `/ai/memory/consolidate` | POST | 记忆整理（function calling + 向量存储） | **已实现** |
| `/ai/memory/realtime_update` | POST | 实时记忆更新（轻量级检测） | **已实现** |
| `/ai/memory/search_facts` | POST | 事实记忆向量检索 | **已实现** |

### 已移除的接口

| 接口 | 移除原因 |
|------|---------|
| `/ai/retrieval` | FixAgent 统一处理，无需单独检索端点 |
| `/ai/diagnosis` | FixAgent 统一处理，无需单独诊断端点 |
| `/ai/guidance` | FixAgent 统一处理，无需单独指引端点 |
| `/ai/pipeline` | FixAgent 单次 ReAct 循环替代串行流水线 |

## 请求模型

`schemas/request.py` 中定义：

- `ChatRequest` — session_id / message(max_length=50000) / images / stream
- `KnowledgeImportRequest` — file_url / file_type / category / tags
- `KnowledgeSearchRequest` — query / top_k / category / tags
- `MemoryConsolidateRequest` — session_id / memoryMessages / memoryPreferenceVOList / memoryUnresolvedVOList
- `RealtimeUpdateRequest` — session_id / user_message / ai_response / recent_facts（实时记忆更新）

## 响应模型

`schemas/response.py` 中定义：

- `ChatResponse` — session_id / message / tools_used / verification / latency_ms
- `KnowledgeImportResponse` — file_name / total_pages / text_count / image_count / table_count / sections / extraction_summary
- `KnowledgeSearchResponse` — data(VectorSearchResult列表) / total / query_time_ms
- `MemoryConsolidateResponse` — session_id / summary(MemorySummary) / original_count / consolidated_at

`ChatResponse.verification` 字段包含 3 层校验结果：

```json
{
  "grounding": {
    "unverified_claims": [...],
    "total_claims": 5,
    "verified_count": 5,
    "unverified_count": 0
  },
  "graph": {
    "unverified_paths": [...],
    "total_paths": 2,
    "verified_count": 2,
    "unverified_count": 0
  },
  "safety": {
    "triggered_rules": ["高温防护"],
    "missing_warnings": [...],
    "appended_text": ""
  },
  "verification_latency_ms": 285
}
```

仅当校验发现问题时 `verification` 字段才非 null。

## SSE 流式事件

流式输出采用「先缓冲再验证」策略：FixAgent ReAct 阶段实时推送 status / tool 事件，
token 先缓冲不发送；ReAct 完成后跑 3 层验证管线（~300ms），
再逐字流式输出最终回答，并在未验证语句前插入内联标记。

```
data: {"event": "session_id",    "data": {"session_id": "xxx"}}
data: {"event": "status",        "data": {"message": "正在检索知识库..."}}
data: {"event": "tool",          "data": {"name": "knowledge_retrieval", "arguments": {...}}}
data: {"event": "status",        "data": {"message": "正在分析..."}}
data: {"event": "marker",        "data": {"text": "⚠️[依据不足-相似度0.21] ", "type": "grounding_unverified"}}
data: {"event": "token",         "data": {"content": "根据"}}
...（逐字流式）...
data: {"event": "marker",        "data": {"text": "⚠️[图谱:方案名不在图谱中] ", "type": "graph_unverified"}}
data: {"event": "token",         "data": {"content": "..."}}
...（逐字流式）...
data: {"event": "verification",  "data": {"has_issues": true, "summary": {...}}}
data: {"event": "done",          "data": {"tools_used": [...], "latency_ms": 3200}}
data: {"event": "error",         "data": {"message": "..."}}
```

关键变化：
- **token 延迟输出**：ReAct 完成 + 验证完成（~300ms）后才开始发送 token，确保标记可内联
- **marker 事件**：在未验证语句/路径的首次出现位置前插入，前端可渲染为黄色警告标签
- **verification 事件**：仍作为摘要推送，供前端展示总体校验状态
- **安全警告**：内联在回答末尾，作为普通 token 流式输出（不再单独切分）

## 调用关系

```
api/main.py
    ├── schemas/request.py            — 请求模型
    ├── schemas/response.py           — 响应模型
    ├── agents/fix_agent.py           — 统一推理（单例，惰性创建）
    ├── agents/review_agent.py        — 3层确定性校验 + 内联标记定位
    ├── agents/memory_agent.py        — 记忆整理
    ├── agents/realtime_memory_agent.py — 实时记忆更新
    ├── services/vector_service.py    — 向量检索（knowledge/search）
    ├── services/knowledge_service.py — 文档导入（knowledge/import）

```

## 与 Java 后端的交互

```
Java Backend                    FixAgent (Python)
  POST /ai/chat                     → FixAgent → ReviewAgent → ChatResponse
  POST /ai/chat/stream (SSE)       → FixAgent.stream → ReviewAgent → SSE 事件流
  POST /ai/knowledge/import        → KnowledgeService → KnowledgeImportResponse
  POST /ai/knowledge/search         → VectorService → KnowledgeSearchResponse
  POST /ai/memory/consolidate      → MemoryAgent → MemoryConsolidateResponse
  POST /ai/memory/realtime_update   → RealtimeMemoryAgent → 更新结果
  POST /ai/memory/search_facts      → VectorService → 相关事实列表
```

## 错误处理

- Agent 执行失败（`metadata.status="error"`）→ API 层检测后返回 error ChatResponse（不抛异常）
- ReviewAgent 校验异常（embedding/Neo4j 不可用）→ 默认通过，不阻塞回答返回
- JSON 解析失败 → 返回 error AgentOutput + warning 日志
- 请求参数校验失败 → FastAPI 自动返回 422
- 全局异常捕获 → JSONResponse(status_code=500) 返回给 Java

## 启动方式

```bash
# 开发环境（热重载）
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

# 生产环境
uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 4
```

## 文件结构

```
api/
├── __init__.py
└── main.py          # FastAPI 入口，含 /ai/* 所有端点
```

## 注意事项

1. **日志级别**：生产环境将 `logging.basicConfig(level=logging.INFO)` 改为 `WARNING`
2. **Agent 惰性初始化**：应用启动时不加载 LLM，首次请求时才创建实例
3. **会话追踪**：`session_id` 由 Java 生成并传递，用于日志分片和链路追踪
4. **超时设置**：建议 HTTP 超时 > 60s（AI 推理耗时较长）
5. **SSE 事件**：流式接口推送 session_id / status / tool / marker / token / verification / done / error 事件。marker 事件内联在 token 流中，标记未验证内容
6. **ReviewAgent 容错**：embedding 或 Neo4j 异常时默认通过，确保不阻塞用户回复
7. **流式延迟**：ReAct 完成 + 验证完成（~300ms）后才开始输出 token，status/tool 事件实时推送保持交互感
