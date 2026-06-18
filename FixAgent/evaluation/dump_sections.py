"""导出关键 chunk 的 section_title（验证跨章节动作词差异），写 utf-8 文件避免控制台乱码。"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.chdir(ROOT)
sys.path.insert(0, ROOT)

import redis  # noqa: E402

r = redis.Redis(host="localhost", port=6379, db=0)
# golden 与 干扰 top1 成对：case_045/122(32章 vs 31章), case_114(20 vs 24), case_116(21 vs 21 同章)
ids = [
    ("case_045 GOLDEN", "eee9bf78:32:txt:0004"),
    ("case_045 top1   ", "eee9bf78:31:txt:0004"),
    ("case_122 GOLDEN", "eee9bf78:32:txt:0003"),
    ("case_114 GOLDEN", "eee9bf78:20:txt:0008"),
    ("case_114 top1   ", "eee9bf78:24:txt:0007"),
    ("case_116 GOLDEN", "eee9bf78:21:txt:0014"),
    ("case_116 top1   ", "eee9bf78:21:txt:0016"),
]
lines = []
for tag, cid in ids:
    raw = r.hget("doc:" + cid, "metadata")
    meta = json.loads(raw) if raw else {}
    lines.append(f"[{tag}] {cid}")
    lines.append(f"    section_title = {meta.get('section_title')}")
    lines.append(f"    raw_text      = {(meta.get('raw_text') or '')[:70]}")
    lines.append("")

with open(os.path.join(HERE, "results", "section_probe.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("done")
