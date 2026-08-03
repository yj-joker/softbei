# 统一语义路由与文档隔离设计

## 目标

将意图识别、实体角色判定、候选文档选择、工具许可和回答来源收敛为一份不可变 `RoutePlan`，使流式与非流式接口执行同一决策，避免部件短语被当成设备、库存查询被 AI 兜底截获，以及跨设备证据进入回答。

## 约束

- 不登记设备名、部件名、完整问句或别名白名单。
- 设备身份来自当前轮结构化语义抽取，并且原文跨度必须可验证。
- 部件角色只能由动态章节目录、动态文档身份目录和语义角色交叉确认。
- 未指定设备时：唯一高置信文档直接绑定；多个文档反问；完全无匹配才允许受控 AI 兜底。
- 库存查询只能由库存工具回答，禁止调用生成模型。
- 所有证据必须属于 `RoutePlan.selected_document_id`。

## 路由流水线

1. `IntentRouter` 只负责结构化意图和开放词表实体抽取。
2. `EntityResolver` 校验原文跨度，并用动态章节目录区分设备身份与文档内对象。
3. `DocumentCandidateResolver` 计算零个、一个或多个候选文档。
4. `SemanticRoutingOrchestrator` 生成不可变 `RoutePlan`。
5. `RouteExecutor` 执行库存、反问等确定性处理器；检索类计划将绑定范围传给既有 RAG。
6. 工具执行后，既有证据覆盖率逻辑再推导最终回答策略。
7. 输出前执行单文档证据门禁并记录结构化路由日志。

## 决策矩阵

| 状态 | 动作 | 工具 | 回答来源 |
|---|---|---|---|
| 闲聊/通用知识 | `GENERAL_AI` | 无 | 模型通用能力 |
| 知识库库存 | `KNOWLEDGE_INVENTORY` | `knowledge_inventory` | MySQL 库存数据 |
| 唯一候选文档 | `GROUNDED_RETRIEVAL` | `knowledge_retrieval` | 绑定文档证据 |
| 多候选文档 | `CLARIFY_DOCUMENT` | 无 | 确定性反问 |
| 明确设备与库存文档冲突 | `AI_FALLBACK` | 无 | 带来源声明的受控 AI |
| 高风险且无证据 | `INSUFFICIENT_EVIDENCE` | 无 | 确定性拒绝精确结论 |

## 日志字段

每轮记录 `intent`、`entity_role`、`candidate_document_ids`、`selected_document_id`、`route_action`、`allowed_tools`、`answer_source`、`reason`，最终输出再记录实际工具和证据文档集合。
