"""对比两份 retrieval_result CSV，列出 rank 变差的 case（base 命中更靠前、cand 变差）。

用法: python evaluation/find_regressions.py <base.csv> <cand.csv>
"""
import csv
import sys


def load(path):
    d = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            d[r["id"]] = r
    return d


def rank(r):
    v = str(r.get("rank", "")).strip()
    return int(v) if v.isdigit() else 999


def main():
    base, cand = load(sys.argv[1]), load(sys.argv[2])
    rows = []
    for cid, b in base.items():
        c = cand.get(cid)
        if not c or str(b.get("answerable", "")).lower() != "true":
            continue
        rb, rc = rank(b), rank(c)
        if rc > rb:  # 变差（含掉出 top5）
            rows.append((b.get("question_type", ""), cid, rb, rc, b.get("question", ""), c.get("top1_content", "")))
    rows.sort(key=lambda x: (x[0], x[1]))
    print(f"REGRESSIONS: {len(rows)} cases (rank base->cand)")
    for qt, cid, rb, rc, q, top1 in rows:
        rc_s = "miss" if rc >= 999 else str(rc)
        print(f"[{qt:<10}] {cid} {rb}->{rc_s} | Q={q[:28]} | cand_top1={top1[:55]}")


if __name__ == "__main__":
    main()
