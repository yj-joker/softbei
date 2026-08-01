# RAG Procedure Order Provenance Design

日期：2026-07-31
状态：已批准，进入实现
目标分支：`feature/rag-quality-v2`
唯一工作目录：`C:\Users\27202\Desktop\softbei\.worktrees\rag-quality-v2`

## 1. 目标与硬门禁

本轮修复步骤型 RAG 回答的逆序、重复和审计降级问题。生产逻辑不得识别测评 case ID，不得为题目词表增加权重，不得维护固定答案映射。所有排序和去重只能依赖文档结构元数据与证据身份。

保留修改必须同时满足：

1. 31 条步骤顺序用例至少达到原始基线 `27/31`，并继续迭代争取 `31/31`。
2. 130 条完整集总通过量不低于 `59/130`。
3. 严格安全回归比较器通过。
4. 现有 Python、Java 和前端验证保持通过。

任一轮步骤顺序低于 `27/31`，撤销该轮生产代码；评测器、数据和结果文件保留。

## 2. 已验证根因

测试 PDF 的原文顺序正确。错误由下游组合链路产生：

1. 检索结果按相关性到达，步骤可能为 `4,3,2,1`。
2. 整节补齐把原文步骤追加在相关性结果之后，而不是重建文档顺序。
3. 主 chunk 与 `step_raw` 派生 chunk 使用不同记录 ID；ledger 未使用 `source_chunk_id` 归一，产生重复证据。
4. ledger 丢弃 `section_index/source_index/parent_chunk_id`，fallback 无法恢复原文顺序。
5. 确定性 formatter 输出“根据手册……”，统一审计又禁止普通模式使用该开头，31 条步骤题全部进入 fallback。
6. formatter 与 ResponsePlan 各自的单元测试都通过，但没有测试真实 formatter 输出再进入统一审计的组合路径。

## 3. 设计

### 3.1 统一证据身份

手册证据的规范身份按以下优先级确定：

1. `metadata.source_chunk_id`
2. `metadata.chunk_id`
3. 记录 `id/doc_id`

同一正文的 contextual 主向量、`step_raw` 纯文本向量和直接章节读取必须归一到同一规范身份。去重不使用问题关键词或文本权重。

### 3.2 统一文档位置

手册记录的稳定位置键为：

```text
(document_id, section_index, page, source_index, child_index, row_index, stable_id)
```

缺失整数使用稳定的最大值；已有向量缺少 `child_index` 时从稳定 chunk 后缀降级推导。新导入的父块拆分必须显式写入 `child_index`。相关性仅决定选中哪个章节，不能决定已选章节内的步骤顺序。

### 3.3 整节补齐

当 procedure 章节已被选中后：

1. 从 Redis 读取目标章节的完整 `step_raw` 集合。
2. 使用统一位置键排序。
3. 使用规范身份去重。
4. 用有序章节快照替换 selected 中同章节的零散步骤，而不是追加。
5. 其他章节证据和图片保留，但不能插入目标步骤序列内部。

### 3.4 Ledger 与 fallback

Ledger 保存规范 chunk ID、父块 ID、章节、页码和所有位置字段。ResponsePlan 的 fallback 对手册证据先按规范身份去重，再按文档位置排序；规则和图谱证据保持自己的稳定到达顺序。

### 3.5 确定性回答审计

普通回答不再由 formatter 固定输出“根据手册……”。来源放在回答末尾；用户明确索要原文或页码时仍保留对应引用形式。

由确定性 formatter 生成的正文只有在同一请求 trace 中注册了对应 direct evidence 时，才可标记为 `evidence_rendered`。此类回答：

1. 不使用自由生成答案的数字/型号正则重复审计，因为每个正文块直接来自已注册证据。
2. 仍执行范围、unsupported、conflict 和安全矛盾门禁。
3. partial 缺少披露时追加披露，不得丢弃已经正确的有序步骤。
4. 自由生成答案继续执行原有严格事实绑定审计。

## 4. 测试策略

新增测试必须覆盖：

1. 真实 formatter 与 finalizer 组合后仍保持 `1,2,3,4`。
2. 输入顺序打乱不改变输出顺序。
3. 主 chunk、`step_raw` 和 direct lookup 三路只输出一次。
4. Redis 返回顺序改变不影响整节快照。
5. partial 披露不会替换有效步骤。
6. unsupported、冲突和自由生成的无依据数字仍被拦截。
7. 新切分数据写入父块内 `child_index`。

评测顺序固定为：局部单元测试、相关 Python 套件、31 条步骤专项、130 条完整集、严格比较器、全量工程验证。

## 5. 非目标

本轮不重新设计向量模型，不新增 LLM 裁判，不按测评问题维护生产词典，不重写整个 PDF 解析器，不改变外部 API 字段。
