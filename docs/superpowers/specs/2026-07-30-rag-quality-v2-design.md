# RAG 证据质量与自然回答 V2 设计

日期：2026-07-30
状态：已完成方案讨论，待书面规格复核
目标分支：`feature/rag-quality-v2`
唯一工作目录：`C:\Users\27202\Desktop\softbei`

## 1. 背景

当前系统已经能够部署，但端到端回答仍有三类明显问题：

1. 关键词相似但设备不同的问题可能错误命中当前手册。例如用户询问“飞机发动机坏了有哪些常见原因”，系统会引用摩托车发动机装配手册。
2. 检索结果只覆盖复合问题的一部分时，模型可能自行补全缺失部分，并把整段回答视为有依据。
3. 大多数回答固定以“根据手册第 X 页……”开头，并倾向于原样倾倒章节或表格，缺少真实对话感。

现有 `maintenance_eval_cli.py` 已覆盖最终答案必答点、禁答项、步骤顺序、拒答和图片，但没有把答案中的每个信息点与 `metadata.react_trace` 中的实际文本证据对齐，也没有覆盖跨设备隔离、部分证据强答、固定模板率和多轮自然回答。因此，必须先升级测评制度，再改生产代码。

## 2. 目标

本轮交付同时完成以下目标：

1. 保留现有 100 条端到端用例原文和期望不变，新增 30 条专项用例。
2. 测评评分完全使用确定性规则，不调用任何 LLM 裁判。
3. 在生产代码未改动时，用新测评器和同一组 130 条用例建立优化前基线。
4. 为所有回答路径增加设备范围、逐要点证据覆盖和统一回答出口。
5. 完整证据时直接、自然地回答；证据不完整时只回答已证实部分；无证据或跨设备时克制拒答；证据冲突时明确冲突而不擅自选边。
6. 优化后使用完全相同的测评器、数据和运行参数复测；若触发严格退化门槛，只回滚生产代码，保留测评器、数据和前后结果。

## 3. 不在本轮范围内

以下内容明确不做：

1. 不实现“用户上报错误回答 -> 后台审核 -> 手工修订 -> Redis 向量化纠错知识”的规则沉淀闭环。
2. 不改变现有规则 payload/主数据协议，也不改变当前 `domain_rules.py` 已实现的 Embedding 与 Redis 向量发布索引。
3. 不引入 LLM-as-a-judge，也不把被测模型的自评作为得分依据。
4. 不新增顶层对外 API 字段；调试和覆盖信息继续放在已有 `metadata` 中。
5. 不重做知识库导入、向量模型、Redis 索引或知识图谱。
6. 不进行与本目标无关的前端、Java 服务或大规模目录重构。

## 4. 总体顺序

实施严格遵循以下顺序：

1. 为测评器新增失败测试。
2. 实现确定性评分与多轮执行能力。
3. 新增 30 条专项数据，保留原 100 条不变。
4. 在未修改生产代码的情况下跑一次完整基线。
5. 按独立提交修改生产链路。
6. 使用同一测评集和参数复测。
7. 执行保留/回滚判定，并在必要时验证回滚后恢复到基线行为。

## 5. 测评设计

### 5.1 数据组织

现有三份数据集保持原样：

- `evaluation/maintenance_eval_dataset_v1.jsonl`：40 条。
- `evaluation/maintenance_adversarial_v2.jsonl`：30 条。
- `evaluation/maintenance_image_adversarial_v1.jsonl`：30 条。

新增：

- `evaluation/maintenance_quality_v2.jsonl`：30 条。

测评 CLI 的 `--dataset` 改为可重复参数，同时兼容原来的单文件调用。一次运行读取四个文件、校验 `case_id` 全局唯一，并在结果中保留 `dataset_source`，从而输出一份 130 条的汇总结果，不复制原 100 条数据。

CLI 新增 `--default-device-type` 和 `--default-document-id`，作为 case 未声明范围时的统一请求范围。基线前先从隔离测评知识库确认唯一活动手册并冻结这两个值；四份数据在优化前后使用相同默认值。专项 case 可显式覆盖其中一个或两个字段。旧 100 条缺少 `source_request_mode` 时默认 `normal`，因此进入模板率统计；它们没有 `claim_constraints`，所以新的证据和风格指标不会改变其原有 `final_pass` 语义。

### 5.2 新增用例构成

新增用例固定分为三组，每组 10 条：

1. 跨设备和领域隔离：飞机发动机、汽车发动机、船用发动机、工业电机等显式超出当前手册范围的问题，以及设备名相近但文档不匹配的干扰题。
2. 部分证据、跨页复合问题和冲突：一个问题包含多个信息要点，检索只覆盖一部分；信息分散在相邻页或不同块；关键数值或版本来源冲突。冲突 case 使用仓库内固定夹具。完整测评连接独立的 Redis Stack 测评实例，该实例由当前评测语料的只读导出加固定冲突夹具构成；禁止把夹具导入部署环境使用的 Redis。优化前后复用同一个只读快照和夹具哈希。
3. 自然回答：同一事实用直接提问、追问、要求简述、要求原文等不同表达；其中 6 条为单轮、4 条为两轮会话。

多轮 case 使用结构化 `turns` 数组。一个 case 内的两轮复用同一 `session_id`，并通过现有 `conversation_history` 传递此前用户/助手消息；每轮包含用户消息和该轮期望。每个 case 在每次测评运行中使用全新且全局唯一的 session，优化前后也不复用 session。每轮分别保存结果，case 的最终通过要求两个受评轮次都通过。单轮 case 继续使用现有 `query` 字段，保持向后兼容。

统计口径固定如下：数据集始终为 130 个 case；其中 126 个单轮 case、4 个两轮 case，因此每次完整运行发出 134 次 API 请求。`final_pass_rate` 按 130 个 case 统计；逐回答指标按 134 个适用 turn 统计；平均延迟按 134 次 API 请求统计。

冲突夹具保存在 `evaluation/fixtures/rag_quality_v2_conflict/`，包含两份最小文档和机器可读 manifest，使用保留的测试 `device_type/document_id`。夹具加载器只有在 `RAG_EVAL_ISOLATED_STORE=1` 且目标 Redis 连接与部署环境连接不同的情况下才允许运行；否则立即失败，不自动降级为向生产知识库写入。隔离实例的语料导出哈希、夹具哈希和 RediSearch 索引信息写入基线/复测摘要。

### 5.3 从真实链路提取证据

`CaseRunResult` 除答案、图片和延迟外，还记录 API 返回的 `metadata`。测评器从已有 `metadata.react_trace` 中提取实际文本证据，不增加新对外字段。

测评器先把合法工具结果适配为统一 `EvidenceEnvelope`，其 `source_type` 只允许 `manual`、`domain_rule` 或 `graph`：

- `manual`：来自 `knowledge_retrieval.result_data` 的正文、表格、图片摘要和稳定来源标识。
- `domain_rule`：来自 `domain_rule_engine.result_data` 的已审核规则、状态和 `evidence_sources`；内部规范状态必须为现有协议的 `active`（业务语义为“已审核发布”），非 `active` 或缺少规则 ID 的内容不合格。
- `graph`：来自现有图谱工具 `result_data` 的节点、关系和稳定 ID；只有摘要文字而无路径身份时不合格。

递归提取 `content`、`text`、`summary`、`caption`、`image_summary` 及对应来源标识。每个 case 的 `allowed_sources.source_type` 决定可以使用哪类证据，手册 RAG case 默认只允许 `manual`。仅有截断的 `result_summary` 时可用于诊断显示，但不能让证据指标静默通过。若本应使用知识证据的回答缺少可解析 envelope，证据相关指标失败并记录 `evidence_trace_missing`，而不是退回只检查最终答案。

### 5.4 新增数据字段

在保持旧字段兼容的基础上，专项数据允许使用以下字段：

- `turns`：多轮消息及逐轮期望。
- `expected_scope`：`in_scope`、`out_of_scope` 或 `unknown`。
- `expected_coverage_status`：`complete`、`partial`、`unsupported` 或 `conflict`。
- `device_type`、`document_id`：通过现有 `/ai/chat` 请求字段复现前端已绑定的检索范围；不设置时按全库场景执行。
- `claim_constraints`：逐要点约束数组。每项具有唯一 `claim_id`，并分别声明 `answer_patterns`、`evidence_patterns`、`forbidden_without_evidence_patterns`、`missing_disclosure_patterns` 和 `allowed_sources`。
- `allowed_sources`：每项必须声明 `source_type`，并且只能使用对应类型的来源键。`manual` 使用 `document_id/document_version/pages/chunk_ids`；`domain_rule` 使用 `rule_id/status`，其中 `status` 必须为 `active`；`graph` 使用 `node_ids/relationship_types/path_ids`。未声明的可选维度不限制，已声明的维度必须命中。文本相同但来自错误设备、错误版本或错误章节时不得通过。
- `conflict_constraints`：独立描述冲突黄金事实；每项包含 `subject`、至少两个 `alternatives`，每个 alternative 分别声明值/单位匹配器和 `allowed_sources`，并声明答案必须出现的冲突披露匹配器。只有至少两个不同来源的 alternative 同时被实际证据命中，测评器才独立判定为 observed `conflict`。
- `forbidden_source_terms`：跨设备题中不得引用的设备、章节或手册术语。
- `source_request_mode`：`normal`、`quote` 或 `page`，用于区分普通回答和用户明确索要原文/页码。
- `style_expectation`：结构化对象；使用 `allow_manual_lead`、`max_answer_chars` 和 `max_list_items` 表达可确定评分的风格约束。

### 5.5 确定性指标

保留现有指标，同时新增以下指标：

1. `evidence_nugget_coverage_rate`：黄金证据信息点在实际 trace 文本中的覆盖率。
2. `evidence_source_pass_rate`：命中的证据是否同时满足该 claim 的黄金来源约束。
3. `answer_evidence_alignment_pass_rate`：每个 `claim_constraint` 的回答匹配、证据匹配和来源匹配是否一致；不能因最终文字“碰巧正确”而通过。
4. `scope_isolation_pass_rate`：显式跨设备问题是否拒绝引用不匹配手册，且未命中 `forbidden_source_terms`。
5. `unsupported_completion_free_rate`：证据缺失时是否避免自行补全。
6. `partial_answer_correct_rate`：部分证据时是否保留已证实内容，并明确说明具体缺失项。
7. `conflict_handling_pass_rate`：冲突证据是否被标明，且没有擅自选择某个值。
8. `refusal_integrity_pass_rate`：拒答提示存在，并且拒答后没有继续输出被禁止的猜测。
9. `fixed_template_rate`：`source_request_mode=normal` 的答案中，以固定来源前缀开头的比例；前缀词表同时包含“根据手册”“依据手册”“资料显示”“文档指出”等等价换词。明确索要原文或页码的 case 不进入该指标分母。
10. `style_proxy_pass_rate`：适用 case 同时满足最大字符数、最大条目数、无重复行、无同一表格的行列重复表示。该指标只作为自然度的确定性代理，不宣称完整评价语言自然度。
11. `source_mode_pass_rate`：`normal` 允许在事实后或回答末尾简短引用来源，但禁止固定来源式首句；`quote` 必须给出对应原文；`page` 必须给出可核对页码。
12. `multi_turn_pass_rate`：4 个多轮 case 中，上下文、设备范围和两个受评轮次约束全部通过的 case 比例。

所有短语匹配都采用明确的规范化、对象与数值绑定及否定上下文规则。每个 `claim_constraint` 分别匹配回答、证据和来源三侧；另外从答案确定性抽取数值、单位、型号和安全要求，要求它们能在至少一条合格证据中以相同对象绑定。该指标只覆盖已标注 claim 和可确定抽取的关键事实，不声称检测任意开放语义幻觉。数值不能只因出现在答案任意位置就算命中；拒答短语也不能掩盖后续猜测。测评逻辑不调用 LLM、Embedding 或外部评分服务。

测评器根据 trace 和黄金字段独立计算 `observed_coverage_status`，再与 `expected_coverage_status` 比较。生产代码写入 `metadata` 的覆盖状态只用于诊断，不能直接决定得分。

指标适用集合固定：`final_pass_rate` 使用全部 130 个 case；证据覆盖、来源和对齐指标使用声明了 `claim_constraints` 的 turn；范围隔离使用 `expected_scope=out_of_scope` 的 turn；无依据补全使用声明了 `forbidden_without_evidence_patterns` 的 turn；部分回答和冲突处理分别使用对应期望状态的 turn；拒答完整性使用 `answerable=false` 或 `expected_coverage_status=unsupported` 的 turn；模板和风格代理使用 `source_request_mode=normal` 的 turn；多轮指标只使用 4 个两轮 case。

### 5.6 最终通过判定

旧 case 继续执行原有必答点、禁答项、拒答、步骤、图片和 API 错误约束；当 case 声明了 `claim_constraints` 时必须取得可解析 trace，并叠加答案、证据和黄金来源对齐约束。专项 case 的 `final_pass` 还必须满足适用的范围、覆盖状态、缺失披露、冲突处理、来源模式和多轮约束。

为了防止新字段改变旧 100 条的原始语义，不给旧数据自动推导新的黄金答案；它们只共享无歧义的全局检查，例如“拒答后不得继续命中已有 forbidden_claims”。

### 5.7 测评器自身测试

在修改测评器前先增加单元测试，至少覆盖：

- trace 中完整、部分、缺失证据的提取和判定。
- 同文不同来源时的文档、版本、页码和 chunk 资格判定。
- `manual/domain_rule/graph` 三类 `EvidenceEnvelope` 的互斥来源键和资格判定。
- `conflict_constraints` 要求至少两个不同来源的 alternative 同时命中。
- `out_of_scope -> conflict -> no_evidence -> complete/partial` 覆盖状态优先级，以及冲突候选被降为 `reference_only` 后仍保留冲突状态。
- 正确拒答与“先拒答、后猜测”的区别。
- 数值与对象绑定，避免数字漂移造成假通过。
- 跨设备引用和错误章节命中。
- 普通回答、原文请求、页码请求三种来源模式。
- 单轮与多轮 session 复用。
- 130 个 case、134 次 API 请求和各指标分母的统计口径。
- 四份数据合并、重复 ID 拒绝和结果来源标记。
- 固定模板率统计。
- 表格/章节/图片直取必须登记 ledger，以及知识型 `unsupported` 不得进入三个常识兜底。

## 6. 生产链路设计

### 6.1 统一中间模型

新增以下内部中间对象，名称和职责均固定：

`ScopeDecision`

- 当前活动文档/设备范围。
- 用户显式设备实体。
- `in_scope`、`out_of_scope` 或 `unknown` 决策及原因。

`QuestionAspect`

- 从问题拆出的一个可独立回答要点。
- 目标对象、动作、参数类型和检索提示。

`EvidenceLedger`

- 当前请求内只追加、不回写的统一证据账本；条目统一为带 `source_type`、内容、稳定来源 ID、范围身份、资格状态和检索阶段的 `EvidenceEnvelope`。
- 初检、工具内部补召回、相邻章节扩展、表格/章节/图片直取、领域规则和图谱路径都必须登记；ledger 是 `react_trace`、evidence bundle、`ResponsePlan` 和出口审核的共同数据源。
- 任何没有进入 ledger 的正文、表格行、图片说明或规则内容都不能出现在知识型答案中。

`EvidenceCoverage`

- 每个要点对应的合格证据、缺失证据和冲突证据；它聚合现有 `qualify_candidates()` 的 evidence bundle，不重新实现候选资格判断。
- 总状态只允许 `complete`、`partial`、`unsupported`、`conflict`。

`ResponsePlan`

- 允许回答的事实及其证据标识。
- 不允许补全的缺失项。
- 冲突项。
- 来源展示模式。
- 回答状态和确定性回退文本所需数据。

上述对象只作为内部契约，放入已有 `metadata` 供调试和测评，不增加顶层 API 字段。ledger 中的合格条目序列化回现有 `react_trace.result_data`，避免测评器与生产审核看到不同证据。领域规则的 `EvidenceEnvelope`、ledger 条目和内部 trace 显式携带 `status=active`；现有 `_public_rule()` 对外结构不变。经后台审核的 active 规则作为一种已批准证据进入 `ResponsePlan`；本轮不改变外部规则 payload、Redis 主数据协议或发布流程。

### 6.2 设备与文档范围门禁

范围门禁在正式检索前运行，并把“允许检索的硬范围”和“用户当前意图”分开：

1. 请求中的 `document_id/device_type` 是允许检索的硬范围；两者自身不一致时直接返回 `out_of_scope` 并记录请求范围冲突。
2. 从用户当前轮识别显式设备短语；只使用可审计的别名配置和文档元数据，不把“发动机”等通用零件词直接当作设备一致。
3. 当前轮显式设备优先于会话设备；当前轮未说明设备时才继承会话已确认设备；两者都没有时意图为 `unknown`。
4. 当前意图与请求硬范围明确不匹配时返回 `out_of_scope`，不再用通用关键词召回硬范围内的不相关手册。
5. 候选证据元数据绝不反向决定用户意图，只用于现有 `qualify_candidates()` 的资格校验。跨设备、跨不兼容版本或来源身份不稳定的候选进入 `reference_only/excluded`，不能支持直接事实。

设备别名放在独立、可审计的配置中，以规范设备名和现有文档 ID 为键，不散落在检索条件中。现有文档缺少 `device_type`、版本或别名时，先查该配置；仍无法识别则标记为 `unknown`。没有明确范围冲突时，`unknown` 可以继续检索；但对显式跨设备问题，缺少设备身份的候选不能成为合格证据。本轮只补范围配置，不重导入或重建已有向量索引。

因此，“飞机发动机”不会因为包含“发动机”而引用摩托车装配清单；“发动机怎么拆”在会话已绑定当前设备时仍可正常检索。

### 6.3 复合问题拆分与独立检索

问题先按并列词、标点、对象和意图拆成若干 `QuestionAspect`。每个要点独立构造现有 `RetrievalPlan`，避免一个高分片段掩盖其他要点缺失。

每个要点只调用一次现有 `knowledge_retrieval` 工具；该工具内部最多执行 2 个检索阶段：

1. 基础阶段使用现有 planner 路由和重排。
2. 仅当质量报告或该要点覆盖结果显示缺失时，工具内部执行一次补召回，根据缺失类型扩展到表格、正文、步骤或相邻章节。
3. 若仍缺失则停止，不通过无限扩大 Top-K 假装完整。

当前工具的 `always_run` 补召回改为上述条件补召回；外层不得再为同一要点第二次调用带内部补召回的工具。相邻页和按稳定 ID 直取属于同一次工具调用的受限上下文扩展，不得启动第三轮语义搜索，并必须登记到 ledger。同一查询和相同过滤条件复用候选结果；多个要点可以并发检索，但必须保持稳定排序和来源去重，以控制延迟。

### 6.4 证据资格与覆盖状态

证据先通过现有 `services/retrieval/qualification.py` 的唯一资格入口，继续使用 `qualified/reference_only/excluded`、`conflicts` 和 `capabilities`，本轮把现有 evidence bundle 向后兼容升级为 `evidence_bundle_version=2`，增加逐要点映射和 `conflict_eligible`，不建立第二套资格状态：

- 设备、文档和版本与范围决策兼容。
- 来源标识稳定，不是仅靠标题关键字推断。
- 关键数值、型号、对象和单位在同一证据上下文中绑定。
- 分数和类型满足现有质量门禁。

`EvidenceCoverage` 消费上述 evidence bundle，并按以下唯一优先级判定；命中前一项后不再进入后一项：

1. 用户设备明确越界或问题无法形成有效要点：`unsupported`。
2. evidence bundle 的 `conflicts/conflict_eligible` 对至少一个要点存在未消解关键冲突：`conflict`。该检查先于 `qualified_evidence` 数量，因为现有资格器发现冲突后会把候选降为 `reference_only` 并清空 qualified；v2 必须保留触发冲突的候选 ID 和值，不能只保留空 qualified。
3. 零个要点有 `qualified_evidence`：`unsupported`。
4. 所有要点都有 `qualified_evidence`：`complete`。
5. 其余情况，即至少一个要点有合格证据且至少一个要点缺失：`partial`。

`ResponsePlan` 的允许事实和能力必须从 evidence bundle 的 `capabilities` 与 `EvidenceCoverage` 共同派生；`reference_only/excluded` 不能绕过能力开关进入答案。

冲突检测只针对可确定比较的关键值和不兼容来源，不用模糊语义相似度宣称两个普通句子矛盾。

### 6.5 回答生成策略

所有进入统一知识链路的路径先形成 `ResponsePlan`，再生成最终文字：

- `complete`：先直接给结论或步骤，再在必要时简短给来源。
- `partial`：只回答已有证据的部分，随后点明“关于哪个具体要点，当前资料没有明确说明”。
- `unsupported`：说明当前知识库范围或证据不足，不输出相关设备的常见原因、品牌、参数或操作猜测。
- `conflict`：列出冲突值及各自来源，要求用户确认设备版本或文档版本，不擅自选一个值。

知识型 `unsupported` 必须关闭 evidence bundle 中的 `may_offer_generic_guidance`，不得进入 `FixAgent._generic_guidance_output`、`api.main._maintenance_fallback_answer` 或 ReviewAgent 当前的证据不足放行分支。上述兜底只有在路由器已经明确判为非知识型闲聊时才能使用；知识型生成异常时只能输出 `ResponsePlan` 的确定性无依据说明。

普通问题不再固定使用“根据手册……”作为首句。只有用户明确要求“原文”“第几页”“出处”或同义表达时，才采用手册引用式回答。清单和步骤按问题所需粒度组织，不默认倾倒整页表格或重复同一表格的行、列两种表示。

自然回答不是删除来源，而是调整顺序：先解决用户问题，来源放在回答末尾或对应事实之后。安全警告、扭矩、型号等关键事实仍保持精确，不用口语化改写改变技术含义。

### 6.6 固定路径改造与统一出口

统一链路只约束需要知识依据的回答，包括手册 RAG、知识图谱、已审核领域规则、表格清单、章节直取、图片直取和知识型快速路径。问候、身份说明等不依赖知识证据的普通对话不触发检索，但仍执行现有安全规则和用户可见文本样式检查。

知识分支顺序固定为：

1. 已存在的因果追问上下文先解析，但当前轮显式切换设备时立即失效并重新做范围判断。
2. 生成 `ScopeDecision`；范围冲突直接进入 `unsupported`。
3. 设备兼容、状态为 `active`（已审核发布）的领域规则先构造 `ResponsePlan`，只接受来源模式、输出格式和安全契约检查，不再被后续 RAG 或图谱结果覆盖。
4. 未命中合格规则时才进入手册 RAG/知识图谱规划。

因此，“领域规则最高业务优先级”是指它低于正在完成的因果追问上下文、高于 RAG/图谱。`device_type` 为空的规则只有在 `evidence_sources` 能绑定当前硬范围时才可直接回答；否则降为 `reference_only`，不能无条件匹配任意设备。

当前表格清单、章节直取、图片直取、快速路径等确定性函数只负责提取结构化证据，不再直接覆盖最终答案。所有知识型回答的流式和非流式路径都必须经过同一套：

`范围门禁 -> 要点规划 -> 检索与补召回 -> 覆盖判定 -> ResponsePlan -> 生成 -> 出口审核`

流式接口在最终计划和审核完成后再发送用户可见内容，不能先流出未经审核的固定模板，再在结束事件中替换。若现有协议无法安全逐 token 审核，则本轮对知识型回答使用审核后的分段输出，保持接口事件格式兼容。所有知识型 SSE 正常结束事件的现有 `data.metadata` 都携带精简的 `scope_decision`、`coverage_status`、`response_plan_id` 和 `evidence_ledger_digest`；不在 SSE 新增顶层事件字段。集成测试分别覆盖非流式、流式、快速路径、表格、章节、图片、领域规则和非知识闲聊，证明没有绕过或误入统一链路。

### 6.7 统一出口审核

现有 `ReviewAgent` 可以继续提供声明检查，但最终门禁增加 `ResponsePlan` 契约校验：

1. 回答中的关键数值、型号、对象、单位和安全要求必须出现在允许事实及其绑定证据中。
2. `partial` 必须披露缺失项，不能把状态说成完整。
3. `unsupported` 不能出现具体故障原因、品牌、参数或操作建议。
4. `conflict` 不能无说明地只保留某一个冲突值。
5. 来源模式必须符合用户请求，普通问题不强制手册式开场。

现有 ReviewAgent 的异常 fail-open 行为只保留为诊断信息，不能把知识型 `unsupported/conflict` 改成可自由回答；当前固定为 false 的证据不足阻断标记也不能覆盖 `ResponsePlan` 状态。生成结果违反契约时不进行第二次开放式 LLM 补写，而使用 `ResponsePlan` 中的结构化事实生成确定性回退回答。这样避免一次错误生成演变成多次猜测，也控制额外延迟。

## 7. 多轮行为

多轮追问可使用当前 session 中已经确认的设备、文档和上一轮回答要点，但不能继承未证实的模型猜测。诸如“那扭矩呢”“第二步为什么”先解析到上一轮的受支持对象，再进行对应要点检索。

如果新一轮显式切换到其他设备，当前轮设备覆盖旧会话设备，但不突破请求中的 `document_id/device_type` 硬范围；发生冲突时应说明范围不匹配，而不是沿用上一轮手册。会话上下文只帮助消歧，不具备证据资格。

## 8. 错误与降级

1. 检索服务异常：有部分已验证证据时返回 `partial`；没有证据时返回 `unsupported`，不调用模型常识补全。
2. trace 或来源元数据缺失：生产链路将候选视为证据身份不完整；测评器记录明确失败原因。
3. 工具内部补召回超时：保留基础阶段已取得的证据并进入 `partial`，不启动第二次工具调用。
4. 生成失败或契约审核失败：使用确定性回退回答。
5. 流式中途异常：只发送已经通过计划约束的内容，并以现有错误事件结束，不能回退到未经证据约束的通用回答。

## 9. 性能约束

1. 不为评分引入任何 LLM 或 Embedding 调用。
2. 生产链路优先复用现有检索候选和 Embedding，不重复计算相同问题与过滤条件。
3. 复合要点并发调用检索工具，工具内部补召回仅针对缺失要点；每个要点最多 1 次工具调用、2 个内部检索阶段。
4. 优化后 134 次 API 请求的未舍入平均延迟不得超过基线的 120%。预热请求不计入延迟。
5. 基线与复测使用相同的模型提供方、模型版本、温度、`top_p`、seed（提供方支持时）、知识库指纹、服务配置、数据、超时、执行顺序和并发策略；先执行同一条预热请求，再开始计分。摘要记录 Git 提交、运行时间和上述配置，保证结果可追溯。
6. 主比较各完整运行一次。若原有 100 条出现任何安全指标由通过变失败，或某个保留门槛只相差 1 个 case，则对受影响 case 追加两次定向复测；三次中至少两次失败才认定为持续退化。追加复测不并入主运行平均延迟。

## 10. 保留与回滚门槛

所有汇总同时保存分子、分母和六位小数展示值。保留判定使用未舍入的整数计数或原始延迟，不使用展示后的四舍五入值。某指标没有适用 case 时记为 `N/A`，不以 0 代替，也不参与“不得下降”或“至少一个提升”的判断。优化结果只有同时满足以下条件才保留：

1. 130 条的总 `final_pass_rate` 不低于新测评器基线。
2. 原有 100 条中，`forbidden_claim_pass`、`refusal_pass`、`procedure_order_pass`、`image_pass` 不出现任何逐 case 的通过变失败。
3. `scope_isolation_pass_rate`、`unsupported_completion_free_rate`、`refusal_integrity_pass_rate`、`evidence_source_pass_rate`、`forbidden_claim_pass` 和 `forbidden_image_pass` 均不下降。
4. `fixed_template_rate` 必须严格低于基线，且 `style_proxy_pass_rate` 不得下降。若实测基线 `fixed_template_rate` 已为 0，则改为保持 0；其他核心正确性指标仍必须满足第 6 条的严格提升。
5. 平均延迟不超过基线的 120%。
6. 下列核心正确性指标至少一个严格提升：`final_pass_rate`、`evidence_nugget_coverage_rate`、`answer_evidence_alignment_pass_rate`、`scope_isolation_pass_rate`、`unsupported_completion_free_rate`、`partial_answer_correct_rate`。

任一条件不满足即回滚本轮生产代码提交。测评器、专项数据、优化前后结果和比较报告保留。回滚采用可审计的反向提交，不使用破坏性 `reset`。回滚完成必须同时满足：生产文件相对“优化前基线提交”的内容差异为空；测评器单元测试和生产回归测试通过；此前受影响 case 的定向 API 冒烟测试不再出现可重复退化。由于模型输出可能波动，不要求回滚后的自然语言逐字等于基线。

## 11. 提交边界

建议使用以下独立提交，便于比较和回滚：

1. 测评器单元测试及确定性评分实现。
2. 30 条专项数据。
3. 优化前基线结果和机器可读摘要（若结果体积适合版本控制；否则保留在工作目录并记录路径与哈希）。
4. 设备/文档范围门禁。
5. 要点拆分、补召回和覆盖状态。
6. `ResponsePlan`、自然回答和统一出口审核。
7. 优化后结果及前后比较报告。

回滚时只反向 4 至 6，不删除 1 至 3 和 7 中的测评证据。

## 12. 验收产物

所有产物均位于 `C:\Users\27202\Desktop\softbei`：

- 新测评器测试与实现。
- 保持原样的现有 100 条数据及新增 30 条专项数据。
- 优化前 CSV、JSON 摘要和运行配置记录。
- 生产代码及对应单元/集成测试。
- 优化后 CSV、JSON 摘要和逐 case 退化报告。
- 最终“保留”或“已回滚并验证”的明确结论。

## 13. 验收示例

用户问：“飞机发动机坏了有哪些常见原因？”

- 正确：说明当前知识库没有飞机发动机资料，无法据此判断，可请用户提供对应手册。
- 错误：引用当前摩托车发动机装配章节，或在拒答后列出飞机发动机常见故障。

用户问一个包含 A、B 两个要点的问题，而证据只有 A：

- 正确：回答 A，并明确说明 B 在当前资料中没有找到依据。
- 错误：用模型常识补出 B，或只回答 A 却声称已经完整回答。

用户普通询问水泵部件：

- 正确：直接给出所需部件；必要时在末尾标注来源。
- 错误：无论问法都以“根据手册第 25 页……”开头并倾倒整页重复表格。

用户明确问“请给我原文和页码”：

- 正确：使用手册引用式表达，给出可核对的页码和原文范围。
- 错误：为了降低模板率而隐藏用户明确要求的来源。
