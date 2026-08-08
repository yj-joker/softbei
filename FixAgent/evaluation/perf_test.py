"""Python 端接口性能压测脚本（可复用）。

只测 Python AI 引擎（8000）能真实拿到数据的接口：
  1. 知识库文本检索延迟  /ai/knowledge/search
  2. AI 对话首字延迟      /ai/chat/stream （首个 SSE data 事件）
  3. AI 对话完整延迟      /ai/chat/stream （done 事件）

用法：
  set API_TOKEN=... && python evaluation/perf_test.py --rounds 10

Java 端接口（登录等）需 8080 起服务后另测，本脚本不覆盖。
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request

BASE = os.environ.get("PERF_BASE", "http://127.0.0.1:8000")
TOKEN = os.environ.get("API_TOKEN", "")

# 贴近维修现场的查询，覆盖文本/参数/步骤三类
QUERIES = [
    "气缸盖的拆卸顺序是什么",
    "曲轴的扭矩规格是多少",
    "活塞环的安装方向如何判断",
    "气门间隙的标准值是多少",
    "如何检测气缸磨损",
]


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if TOKEN:
        h["X-Api-Token"] = TOKEN
    return h


def _post_json(path: str, payload: dict, timeout: int = 120) -> tuple[int, bytes]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=body, headers=_headers(), method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def _pctl(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1))))
    return s[idx]


def bench_search(rounds: int) -> list[float]:
    lat = []
    for i in range(rounds):
        q = QUERIES[i % len(QUERIES)]
        t0 = time.perf_counter()
        _post_json("/ai/knowledge/search", {"query": q, "top_k": 10})
        lat.append((time.perf_counter() - t0) * 1000)
        print(f"  search {i+1}/{rounds} {lat[-1]:.0f}ms", flush=True)
    return lat


def bench_chat_stream(rounds: int) -> tuple[list[float], list[float]]:
    """返回 (首字延迟列表, 完整延迟列表)。"""
    first, full = [], []
    for i in range(rounds):
        q = QUERIES[i % len(QUERIES)]
        payload = {"session_id": f"perf-{i}", "message": q, "stream": True}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(BASE + "/ai/chat/stream", data=body, headers=_headers(), method="POST")
        t0 = time.perf_counter()
        first_ms = None
        with urllib.request.urlopen(req, timeout=180) as resp:
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                # 真实首字 = 第一个 token 事件（模型吐出的首个内容字），
                # 而非 session_id/status/tool 等前置元数据事件
                if first_ms is None:
                    try:
                        evt = json.loads(line[len("data:"):].strip())
                    except json.JSONDecodeError:
                        continue
                    if evt.get("event") == "token":
                        first_ms = (time.perf_counter() - t0) * 1000
        full_ms = (time.perf_counter() - t0) * 1000
        first.append(first_ms or full_ms)
        full.append(full_ms)
        print(f"  chat {i+1}/{rounds} first={first[-1]:.0f}ms full={full[-1]:.0f}ms", flush=True)
    return first, full


def _report(name: str, lat: list[float]) -> dict:
    avg = sum(lat) / len(lat) if lat else 0.0
    return {"name": name, "n": len(lat), "avg_ms": round(avg), "p95_ms": round(_pctl(lat, 95)),
            "min_ms": round(min(lat)) if lat else 0, "max_ms": round(max(lat)) if lat else 0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--out", default="evaluation/results/perf_result.json")
    args = ap.parse_args()

    if not TOKEN:
        print("WARN: 未设置 API_TOKEN，可能被 401 拦截", flush=True)

    results = []
    print("[1/2] 知识库文本检索…", flush=True)
    results.append(_report("knowledge_search", bench_search(args.rounds)))
    print("[2/2] AI 对话流式（首字/完整）…", flush=True)
    first, full = bench_chat_stream(args.rounds)
    results.append(_report("chat_first_token", first))
    results.append(_report("chat_full", full))

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\n=== 结果 ===", flush=True)
    for r in results:
        print(f"{r['name']:20s} n={r['n']} avg={r['avg_ms']}ms p95={r['p95_ms']}ms "
              f"min={r['min_ms']} max={r['max_ms']}", flush=True)
    print("saved:", args.out, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
