# Procedure Order Provenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用稳定证据身份和文档位置恢复步骤型 RAG 的原文顺序，消除父子 chunk 重复，并让确定性回答与统一审计兼容。

**Architecture:** 新增小型 provenance 模块作为切分、Redis 读取、检索补齐、ledger 和 fallback 的共同结构契约。相关性仍负责选节，节内输出只按文档结构排序；确定性 evidence-rendered 回答使用注册证据审计，自由生成回答继续使用严格事实绑定。

**Tech Stack:** Python 3.11/3.12、pytest、FastAPI、Redis Stack、现有确定性评测 CLI。

---

### Task 1: 锁定真实组合回归

**Files:**
- Modify: `FixAgent/tests/test_unified_knowledge_output.py`
- Modify: `FixAgent/tests/test_manual_evidence_answer.py`

- [ ] **Step 1: 写 formatter -> finalizer 失败测试**

构造顺序为 `4,2,1,3` 的真实手册 trace，调用 `_format_manual_evidence_answer_from_metadata()` 后再调用 `_finalize_knowledge_output()`，断言最终输出为 `1,2,3,4`、不重复、普通模式不以“根据手册”开头且不使用 fallback。

- [ ] **Step 2: 运行红灯测试**

Run: `python -m pytest tests/test_unified_knowledge_output.py::test_real_manual_formatter_survives_final_audit_in_source_order -q`

Expected: FAIL，当前实现触发 `unsolicited_manual_lead` 并按 trace 到达顺序 fallback。

- [ ] **Step 3: 写 partial 披露失败测试**

用相同有序证据配一个缺少次要 aspect 的 bundle，断言有效步骤保留，末尾追加缺失说明，而不是整体 fallback。

- [ ] **Step 4: 运行第二个红灯测试**

Run: `python -m pytest tests/test_unified_knowledge_output.py -k "real_manual_formatter or partial_direct" -q`

Expected: FAIL，当前实现因 `partial_missing_disclosure` 替换答案。

### Task 2: 统一 provenance 契约

**Files:**
- Create: `FixAgent/services/retrieval/provenance.py`
- Create: `FixAgent/tests/test_manual_provenance.py`

- [ ] **Step 1: 写身份和位置失败测试**

测试 `source_chunk_id` 优先于派生记录 ID，并测试无序记录按 `section_index/page/source_index/child_index/row_index` 稳定排序。

- [ ] **Step 2: 运行红灯测试**

Run: `python -m pytest tests/test_manual_provenance.py -q`

Expected: FAIL，模块尚不存在。

- [ ] **Step 3: 实现最小 provenance API**

提供：

```python
canonical_manual_chunk_id(item: Mapping[str, Any]) -> str
manual_position_key(item: Mapping[str, Any]) -> tuple[Any, ...]
dedupe_and_sort_manual_records(items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]
```

实现只读取结构元数据，不读取 query，不使用关键词或相关性分数。

- [ ] **Step 4: 运行绿灯测试**

Run: `python -m pytest tests/test_manual_provenance.py -q`

Expected: PASS。

### Task 3: 修复父子 chunk 元数据和 Redis 顺序

**Files:**
- Modify: `FixAgent/services/knowledge/chunking_policy.py`
- Modify: `FixAgent/services/knowledge/vector_service.py`
- Modify: `FixAgent/tests/test_chunking_policy.py`
- Modify: `FixAgent/tests/test_vector_service_section_lookup.py`

- [ ] **Step 1: 写 child_index 与无序 Redis 失败测试**

断言一个父文本拆成多个步骤时每个子块具有递增 `child_index`；模拟 Redis 逆序返回，断言 `get_section_records()` 输出仍按 provenance 排序。

- [ ] **Step 2: 验证红灯**

Run: `python -m pytest tests/test_chunking_policy.py tests/test_vector_service_section_lookup.py -q`

Expected: 至少新增测试 FAIL。

- [ ] **Step 3: 写入 child_index 并排序 Redis 结果**

在 `_split_numbered_steps()` 的枚举位置写入 `child_index`；所有 `get_section_records/get_page_records/list_document_chunks` 的手册记录返回前使用统一 provenance 排序。

- [ ] **Step 4: 验证绿灯**

Run: `python -m pytest tests/test_chunking_policy.py tests/test_vector_service_section_lookup.py -q`

Expected: PASS。

### Task 4: 重建有序章节快照

**Files:**
- Modify: `FixAgent/tools/knowledge_retrieval_tool.py`
- Modify: `FixAgent/tests/test_retrieval_candidate_filtering.py`

- [ ] **Step 1: 写乱序 selected 与完整节快照失败测试**

selected 中放入步骤 `4,2`，Redis 返回 `3,1,4,2`，断言 `_ensure_section_steps()` 返回目标节 `1,2,3,4` 且每个规范身份一次。

- [ ] **Step 2: 验证红灯**

Run: `python -m pytest tests/test_retrieval_candidate_filtering.py -k section_steps -q`

Expected: FAIL，当前实现返回 selected 后追加缺失步骤。

- [ ] **Step 3: 用章节快照替换零散步骤**

对目标节读取、规范去重和排序；移除 selected 中同规范身份/同目标节的旧步骤，再插入完整有序快照。其他证据保留在步骤序列之后。

- [ ] **Step 4: 验证绿灯**

Run: `python -m pytest tests/test_retrieval_candidate_filtering.py -k section_steps -q`

Expected: PASS。

### Task 5: 让 ledger 和 fallback 保序去重

**Files:**
- Modify: `FixAgent/services/retrieval/evidence.py`
- Modify: `FixAgent/services/retrieval/response_plan.py`
- Modify: `FixAgent/tests/test_evidence_ledger.py`
- Modify: `FixAgent/tests/test_response_plan.py`

- [ ] **Step 1: 写父子重复和乱序 fallback 失败测试**

同一原块提供 `txt`、`srw` 和 direct 三条记录，并打乱 4 个步骤的 trace 到达顺序；断言 ledger 只有四条规范证据，fallback 为 `1,2,3,4`。

- [ ] **Step 2: 验证红灯**

Run: `python -m pytest tests/test_evidence_ledger.py tests/test_response_plan.py -k "canonical or source_order" -q`

Expected: FAIL。

- [ ] **Step 3: 保存结构来源并按 provenance 输出**

`_append_manual_entries()` 使用规范 chunk ID，并把结构字段写入 `source`；ResponsePlan 只对 manual 子集结构排序和去重，规则/图谱顺序不变。

- [ ] **Step 4: 验证绿灯与安全用例**

Run: `python -m pytest tests/test_evidence_ledger.py tests/test_response_plan.py -q`

Expected: 全部 PASS，包括 unsupported、conflict、无依据数字和安全矛盾测试。

### Task 6: 修复确定性回答与统一审计

**Files:**
- Modify: `FixAgent/api/main.py`
- Modify: `FixAgent/services/retrieval/response_plan.py`
- Modify: `FixAgent/tests/test_unified_knowledge_output.py`
- Modify: `FixAgent/tests/test_manual_evidence_answer.py`

- [ ] **Step 1: 普通 formatter 改为自然开头**

procedure 使用“可以按以下顺序操作：”，evidence/parameter 使用直接结论式引导；quote/page 模式仍由 ResponsePlan 产生可核对引用。

- [ ] **Step 2: 引入 evidence_rendered 审计模式**

仅当输出是确定性 manual/table formatter 且 trace 含 direct evidence 注册时启用。跳过自由生成文本的重复数字绑定扫描；unsupported/conflict/safety 仍为硬门禁；partial 披露缺失时追加而非替换。

- [ ] **Step 3: 运行真实组合测试**

Run: `python -m pytest tests/test_unified_knowledge_output.py tests/test_manual_evidence_answer.py tests/test_response_plan.py -q`

Expected: PASS，真实 formatter 不再触发 fallback。

- [ ] **Step 4: 运行相关生产测试**

Run: `python -m pytest tests/test_evidence_ledger.py tests/test_retrieval_candidate_filtering.py tests/test_manual_evidence_qualification.py -q`

Expected: PASS。

### Task 7: 第一轮端到端门禁

**Files:**
- Preserve: `FixAgent/evaluation/results/rag_quality_v2/`

- [ ] **Step 1: 运行全部 Python 自动化测试**

Run: `python -m pytest -q`

Expected: 0 failures。

- [ ] **Step 2: 启动与基线相同的 API 环境**

使用 `qwen-plus`、`temperature=0.7`、同一 Redis 文档 `kdoc_2082825138343858177` 和独立端口；先运行不计分 warm-up。

- [ ] **Step 3: 运行 31 条步骤专项**

从四份固定数据集筛选 `expected_step_order` 非空的 turn，保持原始 evaluator 评分逻辑和请求参数。

Expected: `procedure_order_pass_rate >= 27/31`。若低于门禁，撤销本轮生产改动并回到根因分析。

- [ ] **Step 4: 运行完整 130/134**

使用与 baseline run manifest 相同的四份数据、哈希、默认 document ID、超时和顺序。

Expected: `final_pass >= 59/130`，步骤顺序不低于 `27/31`。

- [ ] **Step 5: 运行严格比较器**

Expected: 不出现 `rollback_required`，无严格安全回归。

### Task 8: 剩余失败循环与最终验证

**Files:**
- Modify only files implicated by fresh trace evidence.

- [ ] **Step 1: 对剩余步骤失败按根因聚类**

只使用 trace 中的结构位置、章节边界、规范身份、coverage 和审计诊断；禁止以 query 词或 case ID 建生产分支。

- [ ] **Step 2: 每个新根因重复 RED-GREEN**

每轮新增一个最小失败测试、确认失败、实施单一修复、确认局部和完整相关测试通过。

- [ ] **Step 3: 重跑步骤专项**

保持 `>=27/31`，继续迭代到 `31/31` 或确认剩余项需要不违反约束的更深结构修复。

- [ ] **Step 4: 最终工程验证**

运行 Python 全量测试、Java 自动化测试、前端契约与 Vite build，再运行最终 130/134 和严格比较器。

- [ ] **Step 5: 提交最终有效修改**

只提交通过门禁的代码、测试和设计文档；评测结果保持未跟踪，不纳入提交。

## Self-review

- 计划覆盖身份、位置、父子去重、Redis 顺序、检索补齐、ledger、fallback、自然回答、审计和端到端门禁。
- 所有生产决策均来自结构元数据，没有 case ID、固定答案或新增关键词权重。
- 每项生产修改前都有会失败的测试，且保留原有 unsupported、conflict 和安全审计。
