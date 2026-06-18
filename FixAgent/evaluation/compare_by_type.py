"""按 question_type 对比两份 retrieval_result CSV 的 Recall@1/3/5。

用法: python evaluation/compare_by_type.py <baseline.csv> <candidate.csv>
只统计 answerable 且未跳过的 case。
"""
import csv
import sys
from collections import defaultdict


def load(path):
    by_type = defaultdict(lambda: {"n": 0, "t1": 0, "t3": 0, "t5": 0})
    overall = {"n": 0, "t1": 0, "t3": 0, "t5": 0}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if str(row.get("skipped_for_recall", "")).lower() == "true":
                continue
            if str(row.get("answerable", "true")).lower() != "true":
                continue
            qt = (row.get("question_type") or "(none)").strip()
            for bucket in (by_type[qt], overall):
                bucket["n"] += 1
                bucket["t1"] += 1 if row.get("hit_top1", "").lower() == "true" else 0
                bucket["t3"] += 1 if row.get("hit_top3", "").lower() == "true" else 0
                bucket["t5"] += 1 if row.get("hit_top5", "").lower() == "true" else 0
    return by_type, overall


def rate(b, k):
    return b[k] / b["n"] if b["n"] else 0.0


def main():
    base_path, cand_path = sys.argv[1], sys.argv[2]
    base, base_all = load(base_path)
    cand, cand_all = load(cand_path)
    types = sorted(set(base) | set(cand))

    print(f"{'type':<12}{'n':>4} | {'R@1 base->cand   d':>22} | {'R@3 base->cand   d':>22} | {'R@5 base->cand   d':>22}")
    print("-" * 92)

    def line(name, b, c):
        n = c["n"] or b["n"]
        cells = []
        for k in ("t1", "t3", "t5"):
            rb, rc = rate(b, k), rate(c, k)
            cells.append(f"{rb:.3f}->{rc:.3f} {rc-rb:+.3f}")
        print(f"{name:<12}{n:>4} | {cells[0]:>22} | {cells[1]:>22} | {cells[2]:>22}")

    for t in types:
        line(t, base.get(t, {"n": 0, "t1": 0, "t3": 0, "t5": 0}),
             cand.get(t, {"n": 0, "t1": 0, "t3": 0, "t5": 0}))
    print("-" * 92)
    line("OVERALL", base_all, cand_all)


if __name__ == "__main__":
    main()
