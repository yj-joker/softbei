# 对话路由与回答契约实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**目标：** 在保留现有意图路由和步骤顺序能力的前提下，修复通用问题误检索、跨设备引用、左右件反转、图片不一致和固定模板问题。

**架构：** 复用 `IntentDecision`、`ScopeDecision`、证据覆盖和 `ResponsePlan`，新增一个由这些状态确定性派生的 `ResponsePolicy`。所有回答出口读取同一策略；表达随机性只影响语言组织，不影响事实、来源、方向、参数、步骤和安全要求。

**技术栈：** Python、Pydantic、FastAPI/SSE、pytest、现有 RAG 与测评 CLI。

---

### Task 1：锁定现有基线与路由失败用例

**文件：**
- 新建：`FixAgent/tests/test_response_policy.py`
- 修改：`FixAgent/tests/test_intent_router.py`（若已有则追加）
- 修改：`FixAgent/tests/test_scope_guard.py`（若已有则追加）

- [ ] 写失败测试：高等数学、你是谁、底层模型均不应进入知识检索；未知设备文档状态必须区别于显式文档冲突。
- [ ] 运行 `python -m pytest FixAgent/tests/test_response_policy.py -q`，确认因策略类型和通用路由分支不存在而失败。
- [ ] 记录当前完整测评摘要中的 130 case / 134 turn 指标，作为本轮比较基线。

### Task 2：实现通用对话子类型与响应策略

**文件：**
- 新建：`FixAgent/services/response_policy.py`
- 修改：`FixAgent/services/intent_router.py`
- 修改：`FixAgent/services/retrieval/scope.py`
- 测试：`FixAgent/tests/test_response_policy.py`、`FixAgent/tests/test_intent_router.py`

- [ ] 为 `IntentDecision` 增加可序列化的 `chat_subtype`，规则覆盖 `social_chat/general_knowledge/assistant_identity/model_information`。
- [ ] 将规则兜底从“未识别即 knowledge_query”改为：无设备/文档/检修语义时走 chat；保留明确检修词的故障、步骤和参数路由。
- [ ] 将 scope reason 拆为 `no_matching_device_document`、`explicit_document_conflict`、`document_not_found` 等状态，保留旧字段兼容。
- [ ] 实现 `ResponsePolicy` 和 `derive_response_policy(intent, scope, evidence, risk)`，覆盖 GENERAL_AI、GROUNDED_KNOWLEDGE、PARTIAL_GROUNDED、MAINTENANCE_AI_FALLBACK、INSUFFICIENT_EVIDENCE、BLOCKED_SCOPE、EVIDENCE_CONFLICT。
- [ ] 运行 Task 1 测试，确认绿灯。

### Task 3：接入主流程，修复无资料和跨设备行为

**文件：**
- 修改：`FixAgent/api/main.py`
- 修改：`FixAgent/services/retrieval/response_plan.py`
- 测试：`FixAgent/tests/test_unified_knowledge_output.py`、`FixAgent/tests/test_scope_guard.py`

- [ ] 写失败测试：飞机问题在只有摩托车手册时不能出现摩托车来源，必须包含 AI 参考提示；显式冲突必须阻断且不调用通用检修回答。
- [ ] 在 `_prepare_chat_agent_input` 后派生并写入 `context.response_policy`，主流程和流式流程优先处理 GENERAL_AI、BLOCKED_SCOPE 与 MAINTENANCE_AI_FALLBACK。
- [ ] 为 AI fallback 添加语义免责声明和来源元数据；仅对低/中风险问题开放通用建议，高风险参数和操作转为 INSUFFICIENT_EVIDENCE。
- [ ] 禁止 unsupported 知识路径继续返回固定“没有手册”模板作为所有场景答案；该模板只保留给真正的证据不足/阻断场景。
- [ ] 运行相关测试和现有 response plan 测试，确认绿灯。

### Task 4：增加实体方向与回答范围门禁

**文件：**
- 新建：`FixAgent/services/retrieval/query_constraints.py`
- 修改：`FixAgent/services/retrieval/qualification.py`
- 修改：`FixAgent/services/retrieval/response_plan.py`
- 测试：`FixAgent/tests/test_query_constraints.py`、`FixAgent/tests/test_manual_evidence_qualification.py`、`FixAgent/tests/test_response_plan.py`

- [ ] 写失败测试：右曲轴箱盖不能接受左曲轴箱盖证据；安装不能接受拆卸证据；答案不能扩展到未请求的相邻部件。
- [ ] 从查询提取设备、部件、方向、动作和请求要点，建立左/右、安装/拆卸、连接/断开等硬冲突表。
- [ ] 在 candidate qualification 阶段标记 `entity_conflict` 并排除冲突证据；在 response plan 阶段限制回答范围并审计相反实体。
- [ ] 运行局部测试，确认右盖目标章节只接受右盖相关记录。

### Task 5：统一图片和来源契约

**文件：**
- 修改：`FixAgent/api/main.py`
- 修改：`FixAgent/services/retrieval/response_plan.py`
- 测试：`FixAgent/tests/test_unified_knowledge_output.py`、`FixAgent/tests/test_manual_evidence_answer.py`

- [ ] 写失败测试：GENERAL_AI、AI fallback、BLOCKED_SCOPE 不返回手册图片；文本出现“如图所示”但没有匹配图时必须被改写或审计失败。
- [ ] 让流式和非流式路径共享 `images_allowed`、章节/页码/动作绑定和最终图片筛选。
- [ ] 在 metadata 中统一记录 `response_policy`、`source_type`、`knowledge_status`、`evidence_ids`、`evidence_pages`、`disclaimer` 和审计结果。
- [ ] 运行图片和统一出口测试，确保步骤顺序与既有图片行为不退化。

### Task 6：实现受控表达多样性

**文件：**
- 新建：`FixAgent/services/response_style.py`
- 修改：`FixAgent/api/main.py`
- 修改：`FixAgent/services/retrieval/response_plan.py`
- 测试：`FixAgent/tests/test_response_style.py`、`FixAgent/tests/test_response_plan.py`

- [ ] 写失败测试：同一策略下不同 session 可产生不同表达；同一 session 的事实、方向、参数、步骤和免责声明语义保持一致。
- [ ] 实现按 `session_id + turn_id` 稳定选择的审核表达变体；将温度按策略控制，禁止全局升温。
- [ ] 对手册参数/步骤使用低随机性，对 GENERAL_AI 和 AI fallback 使用中等或较高随机性；审计只放行必需事实和提示语义。
- [ ] 运行风格测试并检查固定模板率统计接口。

### Task 7：规则沉淀范围和生命周期字段

**文件：**
- 修改：`FixAgent/services/domain_rules.py`（如实际模块路径不同，以现有规则服务为准）
- 修改：`FixAgent/api/main.py` 的规则直达元数据
- 测试：现有 domain rule 测试及新增 `FixAgent/tests/test_rule_scope_metadata.py`

- [ ] 写失败测试：跨设备规则不能命中；非 active 规则不能作为正式证据；规则必须保留版本、适用设备和 evidence_sources。
- [ ] 增加内部规则元数据校验和候选/审核/active/deprecated 状态约束，保持对外 payload 兼容。
- [ ] 让 response policy 按范围门禁优先于规则命中；规则不得覆盖显式设备冲突。
- [ ] 运行规则和证据 ledger 测试。

### Task 8：扩充测评集并完成回归

**文件：**
- 新建或修改：`FixAgent/evaluation/maintenance_quality_v3.jsonl`
- 修改：`FixAgent/evaluation/maintenance_eval_cli.py` 及相关 schema/scorer
- 输出：`FixAgent/evaluation/results/rag_quality_v3/`

- [ ] 添加通用知识、身份、模型信息、无设备文档、显式冲突、左右件、回答聚焦、图片一致性、多轮切换和规则版本用例。
- [ ] 增加误检索率、跨设备引用率、免责声明覆盖率、模板重复率和多次生成事实一致率指标。
- [ ] 先运行局部测试，再运行全部 Python 测试。
- [ ] 使用相同模型、索引、数据和超时跑 130/134 基线与优化后测评；记录 scope、refusal、image、procedure、multi-turn 和最终通过率。
- [ ] 只有在硬门禁满足且无步骤顺序回归时才保留生产改动；否则保留测评器/数据/结果并撤销对应生产提交。

## 自检

- 覆盖了通用路由、范围分型、AI fallback、实体一致性、图片契约、受控随机性、规则治理和测评门禁。
- 没有把 `response_policy` 设计成第二套意图识别。
- 每个生产任务均先写失败测试；不修改现有用户未提交改动。
