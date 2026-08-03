# 动态设备范围与端到端 RAG 可靠性实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不登记未支持设备关键词的前提下，阻止跨设备串库，并修复清单覆盖、跨页来源、图片绑定、多轮动作、冲突恢复和流式终态的端到端回归。

**Architecture:** 扩展现有意图分类结果为当前轮唯一 `QueryContract`，用文档 manifest 生成动态 `DocumentIdentity` 目录，再进行 `matched / unmatched / uncertain` 三态范围裁决。范围未明确匹配时不产生检索过滤器；范围匹配后，所有证据统一进入一个回答计划和一次终审，表格与图片都保留结构化来源。

**Tech Stack:** Python 3.11、FastAPI、Pydantic、pytest、Redis manifest/vector index、Vue 3、Java 17/Spring Boot。

---

### Task 1: QueryContract 与动态文档目录

**Files:**
- Create: `FixAgent/services/retrieval/device_identity.py`
- Modify: `FixAgent/services/intent_router.py`
- Modify: `FixAgent/services/retrieval/scope.py`
- Test: `FixAgent/tests/test_device_identity.py`
- Test: `FixAgent/tests/test_scope_gate.py`

- [ ] **Step 1: 写入失败测试**

测试直接构造动态目录，不传 `external_devices`，覆盖：摩托车匹配；卡车和任意留出设备不匹配；缺少载体时不确定；当前轮显式设备覆盖会话绑定；非匹配和不确定均无检索过滤器；模型返回的 `raw_device_span` 不在原问题中时拒绝该字段。

```python
def test_unseen_carrier_conflict_never_binds_motorcycle_manual():
    catalog = DeviceCatalog.from_manifests([MOTORCYCLE_MANIFEST])
    query = QueryContract.from_mapping({
        "raw_device_span": "履带起重机发动机",
        "device_category": "发动机",
        "carrier_or_application": "履带起重机",
    }, raw_query="履带起重机发动机异响是什么原因")
    decision = resolve_scope(query, catalog=catalog)
    assert decision.status == "unmatched"
    assert decision.retrieval_filter() == {}
```

- [ ] **Step 2: 运行 RED**

Run: `FixAgent/.venv/Scripts/python.exe -m pytest FixAgent/tests/test_device_identity.py FixAgent/tests/test_scope_gate.py -q`

Expected: 因 `device_identity`、`QueryContract` 和三态范围 API 尚不存在而失败。

- [ ] **Step 3: 最小实现**

`QueryContract.from_mapping()` 校验 `raw_device_span` 必须是当前问题的连续原文；`DeviceCatalog.from_manifests()` 只读取 ready manifest 的 `document_identity` 或导入元数据；`compare_identity()` 对载体、型号、制造商的显式冲突返回 `unmatched`，缺信息返回 `uncertain`，完全一致返回 `matched`。候选相似度只选候选，不能改变三态裁决结果。

- [ ] **Step 4: 运行 GREEN**

Run: `FixAgent/.venv/Scripts/python.exe -m pytest FixAgent/tests/test_device_identity.py FixAgent/tests/test_scope_gate.py -q`

Expected: 全部通过，且测试夹具没有 `external_devices`。

### Task 2: 导入期设备身份解析与 manifest 回填

**Files:**
- Modify: `FixAgent/services/knowledge/service.py`
- Modify: `FixAgent/services/knowledge/manual_kg_extractor.py`
- Test: `FixAgent/tests/test_document_identity_import.py`

- [ ] **Step 1: 写入失败测试**

覆盖用户显式设备元数据优先、从手册标题和开头文本结构化抽取、低置信度不授权检索、身份写入 manifest 并传播到文本/表格/图片公共元数据。

- [ ] **Step 2: 运行 RED**

Run: `FixAgent/.venv/Scripts/python.exe -m pytest FixAgent/tests/test_document_identity_import.py -q`

Expected: manifest 中缺少 `document_identity` 和 `index_revision`。

- [ ] **Step 3: 最小实现**

在解析完成、建立 `common_metadata` 前调用可独立测试的身份解析器，写入：

```python
{
    "device_name": identity.device_name,
    "device_category": identity.device_category,
    "carrier_or_application": identity.carrier_or_application,
    "manufacturer": identity.manufacturer,
    "model": identity.model,
    "identity_confidence": identity.confidence,
}
```

每次重导入递增 `index_revision`，不再由静态 `scope_registry.json` 承担运行时文档身份。

- [ ] **Step 4: 运行 GREEN**

Run: `FixAgent/.venv/Scripts/python.exe -m pytest FixAgent/tests/test_document_identity_import.py -q`

Expected: 全部通过。

### Task 3: API 范围门禁与唯一回答策略

**Files:**
- Modify: `FixAgent/api/main.py`
- Modify: `FixAgent/services/response_policy.py`
- Modify: `FixAgent/config/scope_registry.json`
- Test: `FixAgent/tests/test_response_policy_direct.py`
- Test: `FixAgent/tests/test_dynamic_scope_api.py`

- [ ] **Step 1: 写入失败测试**

通过最终 `/ai/chat` 和 `/ai/chat/stream` 入口断言：飞机、卡车和一个生产文件中未出现的留出设备均走 AI 通用兜底，手册引用和图片为零；闲聊、高数和模型身份走模型原生回答；`unmatched / uncertain` 不能进入 RAG fast path。

- [ ] **Step 2: 运行 RED**

Run: `FixAgent/.venv/Scripts/python.exe -m pytest FixAgent/tests/test_dynamic_scope_api.py FixAgent/tests/test_response_policy_direct.py -q`

Expected: 卡车或留出设备仍可进入摩托车检索，或缺少动态目录元数据。

- [ ] **Step 3: 最小实现**

`_prepare_chat_agent_input()` 使用同一次意图分类产生的 `QueryContract`，从 manifest 构建目录后裁决范围。只在 `matched` 时设置非空 `retrieval_scope`。删除生产 `external_devices`；AI 通用兜底仅做语义约束，不固定开头和项目符号结构。

- [ ] **Step 4: 运行 GREEN**

Run: `FixAgent/.venv/Scripts/python.exe -m pytest FixAgent/tests/test_dynamic_scope_api.py FixAgent/tests/test_response_policy_direct.py -q`

Expected: 全部通过。

### Task 4: 清单覆盖、唯一渲染与跨页来源

**Files:**
- Modify: `FixAgent/services/retrieval/qualification.py`
- Modify: `FixAgent/services/retrieval/response_plan.py`
- Modify: `FixAgent/api/main.py`
- Modify: `FixAgent/services/knowledge/chunking_policy.py`
- Test: `FixAgent/tests/test_evidence_qualification.py`
- Test: `FixAgent/tests/test_response_plan.py`
- Test: `FixAgent/tests/test_inventory_table_answer.py`

- [ ] **Step 1: 写入失败测试**

气缸活塞清单测试必须断言：8 行各一次、图片摘要不进入正文、两个原表序号 6 均保留并注明原表如此、来源为 17–18 页、`coverage_status=complete`、审计通过、末尾无“当前资料没有明确说明”。

- [ ] **Step 2: 运行 RED**

Run: `FixAgent/.venv/Scripts/python.exe -m pytest FixAgent/tests/test_evidence_qualification.py FixAgent/tests/test_response_plan.py FixAgent/tests/test_inventory_table_answer.py -q`

Expected: 当前辅助跨页表被丢弃、来源只剩 17 页或审计回退。

- [ ] **Step 3: 最小实现**

必答点只来自原始问题，并按完整逻辑表格聚合判断；确定性证据渲染答案的普通来源前缀只修正文案，不触发证据拼接回退；表格合并使用 `continuation_id / parent_section_id / page_span`，按 `(source_page, source_index, row_index)` 排序并按完整行身份去重。

- [ ] **Step 4: 运行 GREEN**

Run: `FixAgent/.venv/Scripts/python.exe -m pytest FixAgent/tests/test_evidence_qualification.py FixAgent/tests/test_response_plan.py FixAgent/tests/test_inventory_table_answer.py -q`

Expected: 全部通过。

### Task 5: 图片、动作方向与多轮隔离

**Files:**
- Modify: `FixAgent/services/retrieval/image_selector.py`
- Modify: `FixAgent/services/retrieval/query_understanding.py`
- Modify: `FixAgent/tools/knowledge_retrieval_tool.py`
- Test: `FixAgent/tests/test_image_page_selector.py`
- Test: `FixAgent/tests/test_response_image_contract.py`
- Test: `FixAgent/tests/test_query_understanding.py`

- [ ] **Step 1: 写入失败测试**

覆盖右曲轴箱盖只选 26–27 页右盖图片；安装与拆卸不能互串；当前轮“离合器”不能继承上一轮“右曲轴箱盖”；单步骤不带相邻页，多步骤跨页选择覆盖回答步骤的最小图片集合。

- [ ] **Step 2: 运行 RED**

Run: `FixAgent/.venv/Scripts/python.exe -m pytest FixAgent/tests/test_image_page_selector.py FixAgent/tests/test_response_image_contract.py FixAgent/tests/test_query_understanding.py -q`

Expected: 至少一个动作、方向或跨页绑定断言失败。

- [ ] **Step 3: 最小实现**

当前轮显式 `component/action/orientation` 覆盖历史；图片只在最终证据集合确定后选择一次；动作、方向、禁图页为硬过滤，步骤覆盖决定数量和顺序。

- [ ] **Step 4: 运行 GREEN**

Run: `FixAgent/.venv/Scripts/python.exe -m pytest FixAgent/tests/test_image_page_selector.py FixAgent/tests/test_response_image_contract.py FixAgent/tests/test_query_understanding.py -q`

Expected: 全部通过。

### Task 6: 冲突反问和第二轮恢复

**Files:**
- Modify: `FixAgent/services/pending_clarification.py`
- Modify: `FixAgent/api/main.py`
- Test: `FixAgent/tests/test_pending_clarification.py`
- Test: `FixAgent/tests/test_pending_clarification_api.py`
- Modify: `FixAgent/evaluation/fixtures/rag_quality_v2_conflict/conflict_trace.json`

- [ ] **Step 1: 写入失败测试**

三例冲突必须分别注入两份同设备、同版本适用范围、同部件、同动作、同字段同单位但不同值的合格证据；首轮返回反问并保存证据，第二轮按页码、版本或选项恢复原问题继续回答。

- [ ] **Step 2: 运行 RED**

Run: `FixAgent/.venv/Scripts/python.exe -m pytest FixAgent/tests/test_pending_clarification.py FixAgent/tests/test_pending_clarification_api.py -q`

Expected: 冲突未保存或第二轮未恢复时失败。

- [ ] **Step 3: 最小实现**

统一 `PendingClarification` 的存取、解析和清理；普通数量、序号及不同字段数字不进入冲突候选。

- [ ] **Step 4: 运行 GREEN**

Run: `FixAgent/.venv/Scripts/python.exe -m pytest FixAgent/tests/test_pending_clarification.py FixAgent/tests/test_pending_clarification_api.py -q`

Expected: 全部通过。

### Task 7: SSE 终态与三端一致性

**Files:**
- Modify: `FixAgent/api/main.py`
- Modify: `weixiu/src/main/java/ai/weixiu/service/impl/AiServiceImpl.java`
- Modify: `weixiu/src/main/java/ai/weixiu/service/support/AiReplyStreamCoordinator.java`
- Modify: `fix-/src/stores/aiChatStore.js`
- Modify: `fix-/src/utils/chatStreamTerminal.js`
- Test: `weixiu/src/test/java/ai/weixiu/ai/AiReplyStreamCoordinatorTest.java`
- Test: `fix-/tests/chatStreamTerminal.spec.js`

- [ ] **Step 1: 写入失败测试**

覆盖正常、审计超时、异常、重复终态和断流；每个请求只接受一个 `done/error`，终态后 Java 持久化完成且前端清理校验加载状态。

- [ ] **Step 2: 运行 RED**

Run: `cd weixiu && mvnw.cmd -Dtest=AiReplyStreamCoordinatorTest test`

Run: `cd fix- && npm test -- --run tests/chatStreamTerminal.spec.js`

Expected: 重复终态或无终态路径失败。

- [ ] **Step 3: 最小实现**

Python 统一终态发送器；Java 以终态事件而非连接关闭作为完成依据；前端按事件 ID 幂等并在任一终态清理状态。

- [ ] **Step 4: 运行 GREEN**

重复 Step 2 命令，Expected: 全部通过。

### Task 8: 真实对话与测评门禁

**Files:**
- Modify: `FixAgent/evaluation/datasets/eval_specialised_v1.jsonl`
- Modify: `FixAgent/evaluation/tests/test_eval_specialised_v1.py`
- Modify: `weixiu/src/main/resources/sql/fix.sql`

- [ ] **Step 1: 静态与单元回归**

Run: `FixAgent/.venv/Scripts/python.exe -m pytest FixAgent/tests -q`

- [ ] **Step 2: 启动最新工作树服务并检查版本**

服务日志和健康检查必须指向当前工作树、当前 commit、当前文档 ID 和 `index_revision`。

- [ ] **Step 3: 运行用户指定真实问题**

逐一运行：飞机发动机异响、卡车发动机异响、留出设备异响、高等数学级数、你是谁、底层模型、右曲轴箱盖安装、离合器安装、离合器拆卸、摩托车发动机气缸活塞装配部件清单。

- [ ] **Step 4: 运行专项测评**

必须达到：步骤顺序 `31/31`；图片至少 `25/30`；冲突 `3/3` 且第二轮恢复；未支持设备错误引用率 0；SSE 终态到达率 100%；流式与非流式正文、来源、覆盖和图片一致。

- [ ] **Step 5: 同步 SQL 与提交**

`fix.sql` 保持与本轮数据库变更一致。只暂存本轮实际修改文件，运行最终 diff 审查和完整验证后提交。

---

## 自审结果

- 规格覆盖：动态范围、兜底、表格、图片、多轮、冲突、SSE、真实测评均有对应任务。
- 占位符检查：无 TBD、TODO 或“稍后实现”。
- 类型一致性：统一使用 `QueryContract`、`DocumentIdentity`、`DeviceCatalog`、`ScopeDecision` 和 `PendingClarification`；范围状态只使用 `matched / unmatched / uncertain`。
- 安全边界：生产代码和配置不登记未支持设备名称；只有动态目录明确匹配时允许检索。
