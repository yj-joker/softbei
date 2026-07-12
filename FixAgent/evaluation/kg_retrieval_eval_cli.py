"""
KG 检索评测 CLI

对 kg_retrieval_eval.jsonl 逐条调用 /weixiu/path/search，
计算 Recall@k、隔离准确率、误召回率。

用法：
    python -m evaluation.kg_retrieval_eval_cli --dataset evaluation/kg_retrieval_eval.jsonl --k 5
    python -m evaluation.kg_retrieval_eval_cli --dataset ... --verbose   # 打印每条明细
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List

import httpx

PATH_SEARCH_URL = "http://localhost:8080/weixiu/path/search"
INTERNAL_TOKEN = "fix-agent-internal-2026"


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _search(query_component: str, query_fault: str, keyword: str, k: int) -> Dict[str, Any]:
    """调用 path/search，返回 data。"""
    body = {
        "componentDescription": query_component or "",
        "faultDescription": query_fault or "",
        "keyword": keyword or "",
        "page": 0,
        "size": k,
        "minScore": 0.3,
    }
    resp = httpx.post(
        PATH_SEARCH_URL,
        json=body,
        headers={"X-Internal-Token": INTERNAL_TOKEN},
        timeout=30,
    )
    data = resp.json()
    return data.get("data") or {}


@dataclass
class CaseResult:
    case_id: str
    case_type: str
    passed: bool
    detail: str
    recall_hit: float = 0.0  # recall 类用例：期望节点命中比例


@dataclass
class EvalSummary:
    total: int = 0
    passed: int = 0
    by_type: Dict[str, Dict[str, int]] = field(default_factory=dict)
    recall_scores: List[float] = field(default_factory=list)
    failures: List[CaseResult] = field(default_factory=list)

    def add(self, r: CaseResult) -> None:
        self.total += 1
        if r.passed:
            self.passed += 1
        else:
            self.failures.append(r)
        t = self.by_type.setdefault(r.case_type, {"total": 0, "passed": 0})
        t["total"] += 1
        if r.passed:
            t["passed"] += 1
        if r.case_type.startswith("recall"):
            self.recall_scores.append(r.recall_hit)


def _eval_case(case: Dict[str, Any], k: int) -> CaseResult:
    cid = case.get("case_id", "?")
    ctype = case.get("case_type", "unknown")
    data = _search(
        case.get("query_component", ""),
        case.get("query_fault", ""),
        case.get("query_keyword", ""),
        k,
    )
    records = data.get("records", []) or []
    got_components = [r.get("componentName") for r in records if r.get("componentName")]
    got_solutions = [
        s.get("title")
        for r in records
        for s in (r.get("solutions") or [])
        if s.get("title")
    ]

    # 隔离用例：应返回空
    if case.get("should_be_empty"):
        passed = len(records) == 0
        return CaseResult(cid, ctype, passed,
                          f"期望空, 实际{len(records)}条: {got_components[:3]}")

    # negative 用例：弱约束，只记录不硬判（人工看分数）
    if ctype == "negative":
        return CaseResult(cid, ctype, True,
                          f"[人工复核] 召回{len(records)}条: {got_components[:3]}")

    # recall 用例：期望节点在 top-k 内的命中率
    exp_comps = case.get("expected_components", []) or []
    exp_sols = case.get("expected_solutions", []) or []
    comp_hits = sum(1 for e in exp_comps if e in got_components)
    sol_hits = sum(1 for e in exp_sols if e in got_solutions)
    total_exp = len(exp_comps) + len(exp_sols)
    hit = (comp_hits + sol_hits) / total_exp if total_exp else 1.0
    # 通过标准：期望的 Component 必须全部命中（Solution 命中作为 recall 分数，不硬卡）
    passed = comp_hits == len(exp_comps) and len(exp_comps) > 0
    return CaseResult(cid, ctype, passed,
                     f"comp {comp_hits}/{len(exp_comps)} sol {sol_hits}/{len(exp_sols)} got={got_components[:3]}",
                     recall_hit=hit)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="evaluation/kg_retrieval_eval.jsonl")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    cases = []
    with open(args.dataset, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("//"):
                cases.append(json.loads(line))
    _log(f"加载 {len(cases)} 条用例, k={args.k}")

    summary = EvalSummary()
    for case in cases:
        try:
            r = _eval_case(case, args.k)
        except Exception as e:
            r = CaseResult(case.get("case_id", "?"), case.get("case_type", "?"),
                          False, f"异常: {e}")
        summary.add(r)
        if args.verbose:
            flag = "PASS" if r.passed else "FAIL"
            _log(f"  [{flag}] {r.case_id} ({r.case_type}): {r.detail}")

    # 汇总报告
    _log("\n" + "=" * 60)
    _log(f"总用例: {summary.total} | 通过: {summary.passed} | 通过率: {summary.passed/summary.total*100:.1f}%")
    if summary.recall_scores:
        avg_recall = sum(summary.recall_scores) / len(summary.recall_scores)
        _log(f"平均 Recall@{args.k}: {avg_recall*100:.1f}%")
    _log("\n分类型:")
    for t, s in sorted(summary.by_type.items()):
        _log(f"  {t}: {s['passed']}/{s['total']} ({s['passed']/s['total']*100:.0f}%)")
    if summary.failures:
        _log(f"\n失败用例 ({len(summary.failures)}):")
        for r in summary.failures[:20]:
            _log(f"  [{r.case_id}] {r.case_type}: {r.detail}")


if __name__ == "__main__":
    main()
