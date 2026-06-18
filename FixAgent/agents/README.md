# Agents 模块

## 模块概述

Agent 是系统的**核心智能组件**，采用 **FixAgent + ReviewAgent** 双层架构：

- **FixAgent（统一推理）**：持有全部工具的 ReAct Agent，在单次循环中自主决策工具调用顺序和次数
- **ReviewAgent（输出审核）**：3层确定性校验（向量计算 + Neo4j查询 + 关键词匹配），零 LLM 调用，替代原有的 LLM 自我审查。支持内联标记流式输出（`get_inline_markers()`）
- **MemoryAgent（记忆整理）**：独立的 function calling Agent，由 `/ai/memory/consolidate` 端点单独调用
- **RealtimeMemoryAgent（实时记忆更新）**：轻量级检测 Agent，每轮对话后异步执行，2-3秒完成

设计原则：移除意图路由层的额外延迟，让 LLM 在 ReAct 循环中自主决策；用确定性校验替代 LLM 自我审查。

## 架构图

```
用户输入
    │
    ▼
┌──────────────────────────────────────────────────────┐
│                FixAgent (统一推理)                      │
│                                                        │
│  系统提示词融合：知识检索 + 故障诊断 + 维修指引        │
│  ReAct 循环: Think → Action → Observation → Answer    │
│                                                        │
│  可用工具:                                             │
│  ├── knowledge_retrieval (知识库向量检索)              │
│  ├── graph_query_diagnosis_path (图谱诊断路径)        │
│  └── java_graph_device_search (Java 图谱设备搜索)     │
└──────────────────────────────────────────────────────┘
    │
    │  (非流式 / 流式)
    ▼
┌──────────────────────────────────────────────────────┐
│              ReviewAgent (3层确定性校验)                │
│                                                        │
│  第1层 — 检索依据校验：输出的依据是否有检索支撑？     │
│         纯向量相似度计算，标记未验证声明               │
│                                                        │
│  第2层 — 图谱路径校验：故障-方案路径是否在Neo4j存在？ │
│         纯 Cypher 查询，标记不在图谱中的路径           │
│                                                        │
│  第3层 — 安全规则引擎：危险操作是否有安全提醒？       │
│         纯关键词匹配，缺失则自动追加                   │
│                                                        │
│  全部确定性操作，零 LLM 调用，延迟 < 500ms            │
│                                                        │
│  流式模式额外步骤：                                     │
│  └── get_inline_markers() → 定位未验证内容字符位置    │
│      → 逐字输出时插入 marker 事件                     │
└──────────────────────────────────────────────────────┘
```

## Agent 列表

| Agent | 文件 | 职责 | 执行模式 |
|-------|------|------|---------|
| `fix_agent` | fix_agent.py | 统一推理（检索+诊断+指引） | `run_with_react()` — ReAct 循环 |
| `review_agent` | review_agent.py | 输出审核（3层确定性校验 + 内联标记） | `review()` — 零 LLM 调用<br>`get_inline_markers()` — 定位未验证内容位置 |
| `memory_agent` | memory_agent.py | 记忆整理 | `run()` — function calling + Pydantic 校验 |
| `realtime_memory_agent` | realtime_memory_agent.py | 实时记忆更新 | `run()` — 单次 LLM JSON 输出 |

## ReviewAgent 3层校验细节

与旧版 LLM 自我审查的本质区别：每一层都是**真的去查了**（查向量库、查图谱、查规则表），而非让 LLM 凭感觉判断。

| 层 | 内部类 | 机制 | 延迟 | 异常时行为 |
|----|-------|------|------|-----------|
| 检索依据校验 | `_GroundingCheck` | 向量相似度（语义匹配） | ~200ms | 默认通过 |
| 图谱路径校验 | `_GraphCheck` | Neo4j Cypher 查询 | ~50ms | 仅用 trace 结果验证 |
| 安全规则引擎 | `_SafetyCheck` | 7类关键词规则匹配 | <1ms | 无异常路径 |

安全规则覆盖：高压电气 / 高温防护 / 化学品防护 / 重物吊装 / 旋转部件 / 压力容器 / 电池电源。

## 流式内联标记

流式 SSE 模式下，ReviewAgent 在完成 3 层校验后调用 `get_inline_markers()`，分析未验证内容在原文中的字符位置，返回按位置升序的标记列表。`api/main.py` 在逐字输出时，到达标记位置先发送 `marker` 事件再继续发送 `token`：

```
data: {"event": "marker", "data": {"text": "⚠️[依据不足-相似度0.21] ", "type": "grounding_unverified"}}
data: {"event": "token",  "data": {"content": "根据"}}
...
data: {"event": "marker", "data": {"text": "⚠️[图谱:方案名不在图谱中] ", "type": "graph_unverified"}}
data: {"event": "token",  "data": {"content": "..."}}
```

标记类型：
| type | 含义 | 触发条件 |
|------|------|---------|
| `grounding_unverified` | 陈述句缺乏检索依据 | 句子与所有检索证据的余弦相似度 < 0.35 |
| `graph_unverified` | 故障-方案路径未在图谱确认 | 故障名或方案名在 Neo4j 中查不到 |

前端可将 `marker` 事件渲染为黄色警告标签，内联显示在对应语句之前。

## Agent 基类

`BaseAgent` 定义统一执行流程和异常处理模板：

- `run()` — 标准模板方法
- `run_with_react()` — ReAct 入口，收集工具列表 → `chat_with_tools()` → 记录 `react_trace` 到 metadata
- `run_with_react_stream()` — ReAct 流式版本，yield SSE 事件（done 事件含 react_trace）
- `run_stream()` — 流式输出
- `run_with_context()` — 便捷方法，构造 `AgentInput` 后调用 `run()`

所有子 Agent 继承 `BaseAgent`，覆盖：

- `name` / `description` 属性
- `get_system_prompt()` — 返回角色定义提示词
- `get_tools()` — 返回可用工具列表（FixAgent 实现）
- `_build_messages()` — 自定义消息构建（MemoryAgent 覆盖）

> 注意：ReviewAgent 不继承 BaseAgent，它是独立的 `ReviewAgent` 类，接口为 `review(fix_output) -> AgentOutput`。

异常处理：任意环节失败返回 `AgentOutput` 的友好提示 + `metadata.status="error"`，不抛出。

## 文件结构

```
agents/
├── __init__.py
├── base_agent.py              # Agent 基类，含 run()/run_with_react()/run_stream()
├── fix_agent.py               # 统一推理 ReAct Agent（持有全部工具）
├── review_agent.py            # 输出审核（3层确定性校验：_GroundingCheck/_GraphCheck/_SafetyCheck）
├── memory_agent.py            # 记忆整理 function calling Agent
├── realtime_memory_agent.py   # 实时记忆更新 Agent（轻量，2-3秒）
└── README.md
```

## 与其他模块的关系

```
agents/ (Agent层)
    ├── services/llm_service.py — chat() / chat_with_tools() / stream()
    ├── services/vector_service.py — FixAgent/MemoryAgent/ReviewAgent 共用向量检索
    ├── services/graph_service.py — FixAgent/ReviewAgent 共用图谱查询
    ├── embeddings/text_embedding.py — ReviewAgent/MemoryAgent 共用向量化
    └── tools/ — FixAgent 的可用工具（通过 get_tools() 注入 ReAct 循环）
```

## ReAct Trace 可观测性

`run_with_react()` 执行后，推理轨迹写入 `AgentOutput.metadata`。ReviewAgent 完成校验后追加 `verification` 字段：

```json
{
  "execution_mode": "react",
  "react_trace": [
    {
      "iteration": 1,
      "action": "tool_call",
      "tool_calls": [
        {
          "name": "knowledge_retrieval",
          "arguments": {"query": "电动机轴承过热", "top_k": 5},
          "result_summary": "找到5条相关知识..."
        }
      ],
      "duration_ms": 1840
    },
    {
      "iteration": 2,
      "action": "finish",
      "content_preview": "电动机轴承过热通常由以下原因引起...",
      "duration_ms": 1200
    }
  ],
  "react_iterations": 2,
  "verification": {
    "grounding": {
      "unverified_claims": [],
      "total_claims": 5,
      "verified_count": 5,
      "unverified_count": 0
    },
    "graph": {
      "unverified_paths": [],
      "total_paths": 2,
      "verified_count": 2,
      "unverified_count": 0
    },
    "safety": {
      "triggered_rules": ["高温防护", "旋转部件防护"],
      "missing_warnings": [],
      "missing_count": 0,
      "appended_text": ""
    },
    "verification_latency_ms": 285
  },
  "verification_has_issues": false
}
```

## 已删除的 Agent

| Agent | 文件 | 删除原因 |
|-------|------|---------|
| `orchestrator_agent` | orchestrator_agent.py | FixAgent 统一处理，不再需要调度层 |
| `retrieval_agent` | retrieval_agent.py | 检索能力由 FixAgent 内置 |
| `diagnosis_agent` | diagnosis_agent.py | 诊断能力由 FixAgent 内置 |
| `guidance_agent` | guidance_agent.py | 指引能力由 FixAgent 内置 |
| `intention_recognizer` | intention/recognizer.py | 移除意图识别，FixAgent 自主决策 |

## 注意事项

1. **ReAct 迭代上限**：默认 max_iterations=10，超出返回已有内容
2. **ReviewAgent 校验**：在 FixAgent 完成后执行，延迟 < 500ms（生产环境），异常时默认通过
3. **安全检查自动追加**：唯一会修改原始输出的环节，缺失安全警告时自动追加
4. **MemoryAgent 独立性**：不通过 FixAgent 调度，直接由 `/ai/memory/consolidate` 调用
5. **RealtimeMemoryAgent 异步**：Java 端在 doOnComplete 后异步调用，不阻塞主对话流
6. **流式输出**：FixAgent 支持 SSE 流式。采用「先缓冲再验证」策略：ReAct 阶段实时推送进度事件，验证完成后逐字输出带内联标记的回答，未验证语句前插入 `marker` 事件
