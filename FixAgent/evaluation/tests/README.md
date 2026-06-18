# 评测单元测试（tests/）

评测工具链的纯单元测试，**不调用任何外部 API**（embedding / LLM / 向量库全部用假数据或 mock），
快速跑完（约 0.6s）。用于守护打分算法和切块/检索策略的正确性。

## 运行

```bash
# 在 fix-py 目录下
python -m pytest evaluation/tests -q
```

## 测试文件

| 文件 | 覆盖对象 | 测什么 |
|---|---|---|
| `test_rag_eval_cli.py` | `rag_eval_cli` | 检索指标算法：Recall@k 与 MRR 的计算、MRR 按 top_k 截断 |
| `test_answer_eval_cli.py` | `answer_eval_cli` | 回答打分逻辑：数值/单位精确匹配、文本重叠打分、required_facts 优先、中英文拒答识别、无答案题误答判为幻觉、汇总指标、各题型答题结构提示 |
| `test_layered_rag_policy.py` | 分层切块与检索策略 | 目录页标记为大纲、参数文本/表格行的元数据、原文+contextual 双文本、表格行锚定父块、图片块带邻近文本、答题角色字段、大纲候选过滤、参数排序、image_locator 触发条件 |

> 注：`tests/__pycache__/` 为编译缓存，已清理；pytest 运行时会自动重建，无需手动维护。
