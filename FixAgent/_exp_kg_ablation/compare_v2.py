"""对比 base(纯模型) vs full(RAG+KG) 两份结果，按 task_type 分组出表。"""
import csv
import json
from collections import defaultdict

BASE = "_exp_kg_ablation/results/ablation_base.csv"
FULL = "_exp_kg_ablation/results/ablation_full_v2.csv"


def load(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def fbool(v):
    return str(v).strip().lower() == "true"


def agg(rows):
    """整体指标。"""
    n = len(rows)
    ans = [r for r in rows if fbool(r.get("answerable", "true"))]
    noans = [r for r in rows if not fbool(r.get("answerable", "true"))]
    return {
        "n": n,
        "final_pass_rate": sum(fbool(r["final_pass"]) for r in rows) / n,
        "nugget_recall": sum(fnum(r["required_nugget_recall"]) for r in ans) / len(ans) if ans else 0,
        "forbidden_pass_rate": sum(fbool(r["forbidden_claim_pass"]) for r in rows) / n,
        "grounding_pass_rate": sum(fbool(r["grounding_pass"]) for r in rows) / n,
        "noans_correct": sum(fbool(r["refusal_pass"]) for r in noans) / len(noans) if noans else None,
        "avg_latency": sum(fnum(r["latency_ms"]) for r in rows) / n,
    }


def by_type(rows):
    groups = defaultdict(list)
    for r in rows:
        groups[r.get("task_type") or "?"].append(r)
    return groups


def main():
    base = load(BASE)
    full = load(FULL)
    bmap = {r["id"]: r for r in base}

    print("=" * 78)
    print("整体对比：base(纯模型)  vs  full(RAG+KG)")
    print("=" * 78)
    b, fu = agg(base), agg(full)
    metrics = [
        ("完整通过率 final_pass", "final_pass_rate"),
        ("必答点召回 nugget_recall", "nugget_recall"),
        ("无幻觉率 forbidden_pass", "forbidden_pass_rate"),
        ("证据忠实率 grounding", "grounding_pass_rate"),
        ("拒答克制率 no_answer", "noans_correct"),
        ("平均延迟 ms", "avg_latency"),
    ]
    print(f"{'指标':<28}{'base':>12}{'full':>12}{'  Δ':>12}")
    print("-" * 78)
    for label, key in metrics:
        bv, fv = b[key], fu[key]
        if bv is None or fv is None:
            print(f"{label:<28}{str(bv):>12}{str(fv):>12}")
            continue
        if key == "avg_latency":
            print(f"{label:<28}{bv:>12.0f}{fv:>12.0f}{fv - bv:>+12.0f}")
        else:
            print(f"{label:<28}{bv:>11.1%}{fv:>11.1%}{(fv - bv) * 100:>+11.1f}pp")

    print()
    print("=" * 78)
    print("按 task_type 分组：必答点召回率  base -> full")
    print("=" * 78)
    bt, ft = by_type(base), by_type(full)
    print(f"{'task_type':<16}{'n':>4}{'base':>12}{'full':>12}{'  Δ':>12}")
    print("-" * 78)
    for t in sorted(set(bt) | set(ft)):
        br, fr = bt.get(t, []), ft.get(t, [])
        ba = [r for r in br if fbool(r.get("answerable", "true"))]
        fa = [r for r in fr if fbool(r.get("answerable", "true"))]
        bv = sum(fnum(r["required_nugget_recall"]) for r in ba) / len(ba) if ba else 0
        fv = sum(fnum(r["required_nugget_recall"]) for r in fa) / len(fa) if fa else 0
        print(f"{t:<16}{len(fr):>4}{bv:>11.1%}{fv:>11.1%}{(fv - bv) * 100:>+11.1f}pp")


if __name__ == "__main__":
    main()
