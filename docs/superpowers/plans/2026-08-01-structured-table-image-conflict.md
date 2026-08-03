# Structured Table, Image Binding, and Conflict Clarification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复跨页表格来源和精确字段、图片多图/少图/错页/绑定，以及证据冲突反问无法在下一轮恢复的问题。

**Architecture:** 分块阶段写入稳定的结构化表格行和步骤图片绑定；检索完成后根据最终证据构建唯一图片选择契约；证据冲突和根因追问共享可序列化澄清状态。读取端保留旧索引兼容分支，允许先上线代码再重建索引。

**Tech Stack:** Python 3、FastAPI、Pydantic/dataclass、现有向量检索服务、pytest、维护测评 CLI。

---

## 本轮验收门槛（2026-08-01）

- `maintenance_image_adversarial_v1` 图片专项至少通过 `25/30`。
- 完整 130 例测评中的步骤顺序保持 `31/31（100%）`，不得回退。
- 图片选择必须同时覆盖：精确目标页收窄、真实跨步骤跨页保留、明确禁图返回空集，以及单目标只保留一个目标页。
- 冲突处理 `0/3` 必须通过测评输入与运行轨迹验证根因；不得把未注入第二独立来源误判为反问状态机失败。

---

### Task 1: 跨页表格结构与精确字段

**Files:**
- Modify: `FixAgent/services/knowledge/chunking_policy.py`
- Modify: `FixAgent/api/main.py`
- Modify: `FixAgent/services/retrieval/qualification.py`
- Test: `FixAgent/tests/test_chunking_policy.py`
- Test: `FixAgent/tests/test_inventory_table_answer.py`
- Test: `FixAgent/tests/test_evidence_qualification.py`

- [ ] **Step 1: 写跨页行来源失败测试**

```python
def test_continued_table_preserves_structured_row_source_pages() -> None:
    chunks = build_section_index_chunks(section_with_tables_on_pages_17_and_18)
    table = next(item for item in chunks if item["chunk_label"] == "table_full")
    assert table["metadata"]["page_span"] == [17, 18]
    assert [row["source_page"] for row in table["metadata"]["table_full"]["rows"]] == [17, 18]
    assert [item["page"] for item in chunks if item["chunk_label"] == "table_row"] == [17, 18]
```

- [ ] **Step 2: 运行测试确认 RED**

Run: `python -m pytest FixAgent/tests/test_chunking_policy.py -q`
Expected: 新断言因 `page_span/table_full/source_page` 不存在或续页行仍为第一页而失败。

- [ ] **Step 3: 实现结构化表格元数据**

在 `_merge_continued_tables()` 中维护与行并行的 `row_source_pages`；在 `build_section_index_chunks()` 中生成稳定 `table_id`、`continuation_id`、整数数组 `page_span` 及结构化行：

```python
structured_rows = [
    {
        "row_id": f"{table_id}:row:{row_index:04d}",
        "source_page": row_source_pages[data_offset + row_index],
        "source_index": row_index,
        "fields": {header: _as_text(row[col]) if col < len(row) else "" for col, header in enumerate(headers)},
    }
    for row_index, row in enumerate(data_rows)
]
table_meta.update({
    "table_id": table_id,
    "continuation_id": table_id,
    "page_span": page_span,
    "table_full": {"table_id": table_id, "continuation_id": table_id, "page_span": page_span,
                   "headers": headers, "rows": structured_rows},
})
```

- [ ] **Step 4: 写结构化读取和冲突字段失败测试**

```python
def test_inventory_answer_reads_fields_and_uses_matching_row_page() -> None:
    answer = _format_inventory_table_answer_from_metadata(query, metadata_with_structured_rows)
    assert "M10" in answer and "数量：4" in answer and "扭矩：60±5 N·m" in answer
    assert "第18页" in answer

def test_sequence_and_quantity_numbers_do_not_create_torque_conflict() -> None:
    assert qualify_candidates(query, candidates_with_same_torque_but_different_seq_and_quantity)["conflicts"] == []
```

- [ ] **Step 5: 运行测试确认 RED**

Run: `python -m pytest FixAgent/tests/test_inventory_table_answer.py FixAgent/tests/test_evidence_qualification.py -q`
Expected: 结构化 `fields/source_page` 未被读取，或无关数字仍产生冲突。

- [ ] **Step 6: 实现结构化优先读取和语义冲突键**

扩展 `_inventory_rows_from_table_full()` 直接读取 `fields` 并保留 `source_page/row_id`；答案引用命中行的页码。`_detect_conflicts()` 仅比较同一 `parameter_names/parameter_type/unit` 的测量值，并排除序号、编号、数量列产生的数字。

- [ ] **Step 7: 运行表格回归确认 GREEN**

Run: `python -m pytest FixAgent/tests/test_chunking_policy.py FixAgent/tests/test_inventory_table_answer.py FixAgent/tests/test_evidence_qualification.py -q`
Expected: 全部通过。

### Task 2: 最终证据驱动的图片选择契约

**Files:**
- Modify: `FixAgent/services/retrieval/query_understanding.py`
- Modify: `FixAgent/services/retrieval/image_selector.py`
- Modify: `FixAgent/tools/knowledge_retrieval_tool.py`
- Modify: `FixAgent/services/knowledge/chunking_policy.py`
- Modify: `FixAgent/api/main.py`
- Test: `FixAgent/tests/test_query_understanding.py`
- Test: `FixAgent/tests/test_image_page_selector.py`
- Test: `FixAgent/tests/test_evidence_image_postprocessing.py`
- Test: `FixAgent/tests/test_chunking_policy.py`

- [ ] **Step 1: 写模式、三页覆盖和单步骤隔离失败测试**

```python
def test_multi_page_procedure_has_no_two_page_cap() -> None:
    selected = select_pages_for_image_query("拆卸全部步骤", pages_19_20_21, image_mode="evidence_pages")
    assert selected == [19, 20, 21]

def test_single_target_does_not_add_adjacent_step_page() -> None:
    selected = select_pages_for_image_query("安装活塞环这一步图片", pages_11_12, image_mode="single_target")
    assert selected == [11]
```

- [ ] **Step 2: 运行测试确认 RED**

Run: `python -m pytest FixAgent/tests/test_query_understanding.py FixAgent/tests/test_image_page_selector.py FixAgent/tests/test_evidence_image_postprocessing.py -q`
Expected: 新模式未识别、三页被截为两页或单目标混入相邻页。

- [ ] **Step 3: 实现图片选择契约和取消固定页数**

新增可序列化 `ImageSelectionContract`，将旧 `single_best/same_section` 兼容映射到 `single_target/section_overview`。`knowledge_retrieval_tool._apply_page_image_selector()` 不再设置 `same_section=2`，而是使用显式页或最终证据页数；`single_target` 保持一页。

- [ ] **Step 4: 写步骤绑定失败测试**

```python
def test_image_chunks_bind_only_steps_from_same_source_page() -> None:
    chunks = build_section_index_chunks(section_with_steps_and_images_on_two_pages)
    image_11 = next(item for item in chunks if item["metadata"].get("image_name") == "p11.png")
    assert image_11["metadata"]["related_step_chunk_ids"] == [page_11_step_id]
    assert image_11["metadata"]["binding_confidence"] == 1.0
```

- [ ] **Step 5: 运行绑定测试确认 RED**

Run: `python -m pytest FixAgent/tests/test_chunking_policy.py -q`
Expected: 图片仍绑定全节前五个步骤而失败。

- [ ] **Step 6: 实现同页步骤绑定和 API 单次终选**

分块时按页保存 `step_chunk_ids_by_page/text_chunk_ids_by_page`，图片优先绑定同页步骤。API 构建一次契约并按 `document_id/section_id/action/orientation/explicit_pages/excluded_pages` 过滤；最终最小覆盖选择替代四个顺序敏感的独立后处理调用，旧函数保留为兼容包装器。

- [ ] **Step 7: 运行图片回归确认 GREEN**

Run: `python -m pytest FixAgent/tests/test_query_understanding.py FixAgent/tests/test_image_page_selector.py FixAgent/tests/test_evidence_image_postprocessing.py FixAgent/tests/test_chunking_policy.py FixAgent/tests/test_retrieval_candidate_filtering.py -q`
Expected: 全部通过。

### Task 3: 通用冲突澄清与下一轮恢复

**Files:**
- Create: `FixAgent/services/pending_clarification.py`
- Modify: `FixAgent/services/causal_followup.py`
- Modify: `FixAgent/services/retrieval/response_plan.py`
- Modify: `FixAgent/api/main.py`
- Test: `FixAgent/tests/test_pending_clarification.py`
- Test: `FixAgent/tests/test_causal_followup.py`
- Test: `FixAgent/tests/test_response_plan.py`
- Test: `FixAgent/tests/test_response_plan_integration.py`

- [ ] **Step 1: 写真实冲突、表面冲突和恢复失败测试**

```python
def test_real_conflict_builds_recoverable_clarification() -> None:
    pending = build_evidence_conflict_clarification(query, conflict, evidence)
    assert pending["status"] == "awaiting_answer"
    assert pending["clarification_id"].startswith("clarification-")
    assert pending["evidence_refs"]

def test_different_semantic_fields_are_not_a_real_conflict() -> None:
    assert build_evidence_conflict_clarification(query, surface_conflict, evidence) is None

def test_answer_resolves_pending_conflict_by_option() -> None:
    resolved = resolve_pending_clarification(context, "使用手册第18页")
    assert resolved["status"] == "resolved"
    assert resolved["selected_evidence_refs"] == [expected_ref]
```

- [ ] **Step 2: 运行测试确认 RED**

Run: `python -m pytest FixAgent/tests/test_pending_clarification.py FixAgent/tests/test_response_plan.py FixAgent/tests/test_response_plan_integration.py -q`
Expected: 通用状态模块不存在，冲突计划没有可恢复状态。

- [ ] **Step 3: 实现 `PendingClarification` 和冲突分类**

使用冻结 dataclass 保存 `clarification_id/kind/subject/alternatives/evidence_refs/missing_identity_fields/question/status/original_query`；ID 由规范化内容 SHA-256 生成。替代项包含显示标签、值、适用范围和证据引用；解析支持选项 ID、页码、型号和版本文本。

- [ ] **Step 4: 接入 ResponsePlan 和现有根因追问**

`ResponsePlan` 在 `coverage_status=conflict` 时生成并序列化 `pending_clarification`，确定性反问展示替代值、来源及最小身份问题。`causal_followup` 通过适配器输出相同公共字段，同时继续接受旧 `diagnostic_follow_up` 上下文。

- [ ] **Step 5: 接入 API 下一轮恢复**

主流程先检查 `context.pending_clarification`；解析成功后把 `selected_evidence_refs` 写回当前证据约束并重新构建回答计划，解析不充分时重复同一问题但不丢失 `clarification_id`。

- [ ] **Step 6: 运行冲突和多轮回归确认 GREEN**

Run: `python -m pytest FixAgent/tests/test_pending_clarification.py FixAgent/tests/test_causal_followup.py FixAgent/tests/test_response_plan.py FixAgent/tests/test_response_plan_integration.py FixAgent/tests/test_unified_knowledge_output.py -q`
Expected: 全部通过。

### Task 4: 回归、索引与完整测评

**Files:**
- Verify: `FixAgent/evaluation/maintenance_eval_cli.py`
- Output: `FixAgent/evaluation/results/rag_quality_v2/run_20260801_structured/`

- [ ] **Step 1: 运行本轮相关测试集**

Run: `python -m pytest FixAgent/tests/test_chunking_policy.py FixAgent/tests/test_inventory_table_answer.py FixAgent/tests/test_evidence_qualification.py FixAgent/tests/test_query_understanding.py FixAgent/tests/test_image_page_selector.py FixAgent/tests/test_evidence_image_postprocessing.py FixAgent/tests/test_pending_clarification.py FixAgent/tests/test_causal_followup.py FixAgent/tests/test_response_plan.py FixAgent/tests/test_response_plan_integration.py FixAgent/tests/test_retrieval_candidate_filtering.py FixAgent/tests/test_unified_knowledge_output.py -q`
Expected: 全部通过。

- [ ] **Step 2: 重建当前手册文本/图片索引并保留旧字段兼容**

Run: 使用仓库现有 `FixAgent/evaluation/rebuild_text_vectors.py` 参数和当前测评环境配置重建索引。
Expected: 命令退出码 0，新表格记录包含 `table_full.rows[].source_page/fields`，新图片记录包含同页步骤绑定。

- [ ] **Step 3: 运行完整 130 条测评**

Run: 按 `maintenance_eval_cli.py --help` 和上一轮 `optimized_130_trace.jsonl` 中记录的相同数据集、模型、索引与超时参数执行，输出到 `run_20260801_structured/full/`。
Expected: 生成逐例 CSV、summary JSON 和 trace JSONL，case 数为 130。

- [ ] **Step 4: 汇总结果和失败根因**

读取 summary 和逐例结果，报告：总通过率、必答点覆盖率、Grounding、图片整体/召回/精确率/顺序/绑定、步骤顺序、冲突处理、多轮、范围隔离、无答案克制，以及仍失败 case ID 与归类原因。

## 自检

- 每个生产改动前都有能观察到的失败测试。
- 新表格和图片元数据均有旧索引兼容读取路径。
- 图片页数由最终回答证据决定，没有固定两页上限。
- 冲突只比较同一语义字段，并能在下一轮通过稳定 ID 恢复。
- 验收覆盖表格、图片、冲突、多轮和步骤顺序，没有扩大到已确认方案之外。
