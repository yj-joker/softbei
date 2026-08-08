# 知识图谱消融测评执行手册

## 1. 答辩口径

本项目的正式实验组名称为：

- `w/o KG：无图谱 Hybrid RAG`：保留相同的手册知识库、向量/关键词混合检索、模型和提示词，仅关闭图谱候选、图谱工具与图谱审核增强。
- `Full：图谱增强 Hybrid RAG`：在相同基础能力上启用图谱候选、图谱工具组和图谱审核。

不要表述为“普通 RAG vs GraphRAG”，因为项目两组都包含 Hybrid RAG。旧目录 `_exp_kg_ablation` 比较的是“纯模型 vs RAG+KG”，一次改变了两个变量，不能作为这次知识图谱消融的正式证据。

## 2. 测评体系

主结论使用项目现有的领域确定性评分器，指标包括最终通过率、必答信息点覆盖率、证据忠实度、无答案克制率、步骤顺序、图片召回/精确率及安全约束。检索层使用 `evaluation/retrieval_metrics.py` 的标准 Recall@5、MRR@5、分级 nDCG@5。旧 `rag_eval_cli.py` 中的 `recall_at_*` 实际语义是 Hit@K，现已增加 `hit_at_*` 并保留旧字段作为兼容别名。

统计报告使用 10,000 次配对 Bootstrap 95% 置信区间、Exact McNemar 检验、P50/P95 时延，并按 `question_type`、`difficulty`、`graph_dependency` 分组。Token 和成本只在服务端返回相应 metadata 时计算；没有数据时报告为 `available: false`。

Ragas 可以作为补充的 LLM-as-a-judge 结果，但不作为必须交付或主结论，避免评委追问评判模型波动、中文领域适配和复现性时削弱证据链。

## 3. 数据集定位

`evaluation/datasets/registry.json` 是数据集登记表。当前已有题目全部属于开发/回归集或机制集，不能称为冻结盲测：

- `maintenance_eval_dataset_v1.jsonl`：40 题，PDF 金标已校验，可用于本轮首跑。
- `maintenance_adversarial_v2.jsonl`：30 题，仍有 39 个金标问题，修复前不得进入正式结论。
- `maintenance_image_adversarial_v1.jsonl`：30 题，PDF 金标已校验。
- `datasets/eval_specialised_v1.jsonl`：30 case、34 turn，专项回归。
- `kg_retrieval_eval.jsonl`：63 题，仅用于图谱机制评测。

`evaluation/datasets/blind_eval_blueprint_v1.json` 定义了 80 题冻结盲测配额：事实 14、流程 14、关系消歧 16、多跳 16、跨文档 8、安全 12。它不包含伪造问题或金标。

正式盲测的私有金标建议放在 `D:/softbei-eval-private`，不提交 Git。冻结前需要双人审核问题、答案和 0–3 级 qrels，记录文件 SHA-256 与签字时间；系统运行人员在冻结前不能查看两组输出。

## 4. 启动两个实验服务

在两个独立 PowerShell 终端中执行。两个进程会读取 `FixAgent/.env`，但 `RAG_VARIANT` 由当前进程环境覆盖。

终端一（8001）：

```powershell
Set-Location C:\Users\27202\Desktop\softbei\FixAgent
$env:RAG_VARIANT = 'no_graph'
.\.venv\Scripts\python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8001
```

终端二（8002）：

```powershell
Set-Location C:\Users\27202\Desktop\softbei\FixAgent
$env:RAG_VARIANT = 'graph'
.\.venv\Scripts\python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8002
```

运行器会先访问 `/health` 并校验端口自报的 `rag_variant`，接反时立即终止，不会产生混淆结果。

## 5. 运行 40 题开发集

```powershell
Set-Location C:\Users\27202\Desktop\softbei\FixAgent
.\.venv\Scripts\python.exe -m evaluation.kg_ablation_eval_cli `
  --dataset evaluation\maintenance_eval_dataset_v1.jsonl `
  --no-graph-endpoint http://127.0.0.1:8001/ai/chat `
  --graph-endpoint http://127.0.0.1:8002/ai/chat `
  --repetitions 1 `
  --bootstrap-samples 10000 `
  --run-name maintenance_v1_dev
```

输出目录为 `evaluation/results/kg_ablation`，包括：

- 两组 case CSV、turn CSV、trace JSONL、summary JSON 和 run manifest。
- `maintenance_v1_dev_request_order.jsonl`：逐题请求先后与重复轮次。
- `maintenance_v1_dev_comparison.json`：配对差值、置信区间、McNemar、分组、时延和可选成本。

这次结果只能称为“40 题开发/回归集首轮消融结果”，不能称为冻结盲测结论。

## 6. 匿名人工盲评

从冻结盲测中抽取 20–30 题。由不参与系统开发的评审者使用 `datasets/human_blind_review_template.csv`，随机将两组答案映射为 A/B，评审者不能看到系统名称。分别判断正确性、完整性、证据忠实度、安全性和总体胜者，可填 `A`、`B` 或 `TIE`。全部提交后才解盲，并报告胜/负/平及评审一致性。

## 7. 发布前检查

1. 两组除 `RAG_VARIANT` 与端口外配置一致。
2. `/health` 自报变体正确，run manifest 中数据 SHA-256、Git commit 和端点正确。
3. `no_graph` 的图谱候选数、图谱工具调用数为 0；`graph` 组保留真实审计值。
4. 先报告总体结果，再报告图依赖题、多跳题和安全题分组。
5. 置信区间跨 0 或 McNemar 不显著时，只表述为“观察到趋势”，不宣称统计显著优越。
