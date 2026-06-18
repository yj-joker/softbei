"""列出回答从 pass 退化为 fail 的 case（base pass -> cand fail）。

用法: python evaluation/answer_regressions.py <base.csv> <cand.csv> [question_type]
"""
import csv
import sys


def load(path):
    d = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            d[r["id"]] = r
    return d


def passed(r):
    return str(r.get("answer_pass", "")).strip().lower() == "true"


def main():
    base, cand = load(sys.argv[1]), load(sys.argv[2])
    qt = sys.argv[3] if len(sys.argv) > 3 else None
    n = 0
    for cid, b in base.items():
        c = cand.get(cid)
        if not c:
            continue
        if qt and b.get("question_type") != qt:
            continue
        if passed(b) and not passed(c):
            n += 1
            print(f"[{b.get('question_type')}] {cid} hit_top5={c.get('retrieval_hit_top5')} halluc={c.get('hallucination')}")
            print(f"   Q   : {b.get('question','')[:40]}")
            print(f"   why : {c.get('judge_reason','')[:75]}")
            print(f"   v21 : {c.get('generated_answer','')[:110]}")
            print(f"   gold: {b.get('golden_answer','')[:90]}")
    print(f"\nTOTAL answer regressions{' ('+qt+')' if qt else ''}: {n}")


if __name__ == "__main__":
    main()
