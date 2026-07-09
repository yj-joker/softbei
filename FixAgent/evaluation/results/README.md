# 测评结果目录

本目录只保存当前测评制度的代表性结果。

当前主测评集：

- `../maintenance_eval_dataset_v1.jsonl`

当前主评测器：

- `python -m evaluation.maintenance_eval_cli`
- `python -m evaluation.gold_dataset_validator`

## 新测评制度

旧版 `rag_eval_dataset_v14/v26/v27/v28` 已删除。那些数据主要评估检索命中和图片页码集合，无法稳定发现真实使用中的问题，例如：

- 文本答案漏步骤、调换顺序；
- 模型补充手册未写内容；
- 明明手册有答案却拒答；
- 图片页码对但顺序错；
- 图片没有绑定到对应步骤；
- 安装/拆卸相反动作混淆。

新制度以“维修任务端到端质量”为核心，按以下层级评分：

1. `required_nugget_recall`：必答信息点覆盖率。
2. `forbidden_claim_pass`：是否没有命中禁答/无依据说法。
3. `procedure_order_pass`：步骤顺序是否符合手册。
4. `image_pass`：图片召回、精确率、顺序、禁图、步骤绑定是否都通过。
5. `grounding_pass`：回答是否忠于手册证据。
6. `final_pass`：综合通过，必须同时满足文本、顺序、图片与拒答约束。

## 常用命令

校验测评集本身是否能回查到 PDF 原文证据：

```bash
python -m evaluation.gold_dataset_validator --dataset evaluation/maintenance_eval_dataset_v1.jsonl --pdf "C:/Users/27202/Desktop/摩托车发动机维修手册.pdf" --report evaluation/results/maintenance_gold_validation_report.json
```

调用本地 `/ai/chat` 做真实端到端测评：

```bash
python -m evaluation.maintenance_eval_cli --dataset evaluation/maintenance_eval_dataset_v1.jsonl --mode api --result-name maintenance_eval_v1_current
```

只用数据集内置候选答案做离线校验：

```bash
python -m evaluation.maintenance_eval_cli --dataset evaluation/maintenance_eval_dataset_v1.jsonl --mode fixture --result-name maintenance_eval_v1_fixture
```
