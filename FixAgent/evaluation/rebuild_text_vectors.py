"""就地重建 text-chunk 向量，用于 A/B 测试差异化 contextual 策略 (P0-1)。

只重写 step/outline/general 这三类 chunk 的向量（P0-1 的差异化范围），
safety / table / image 一律不动。chunk 的 doc_id 完全不变，
因此 evaluation 的 golden_chunk_ids 仍然可复用、Recall@K/MRR 可比。

  --mode contextual : 用 metadata.contextual_text 重建（差异化 P0-1，带 Section/Page 前缀）
  --mode clean      : 用 metadata.raw_text / text 重建（恢复 baseline，纯正文）

两种模式互为逆操作，可随时来回切换做对照实验。
用法（在 fix-py 目录下）：
  python -m evaluation.rebuild_text_vectors --mode contextual --prefix eee9bf78
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import redis

from config.settings import get_settings
from embeddings.text_embedding import get_text_embedding

CONTEXTUAL_LABELS = {"step", "outline", "general"}


def collect_targets(r: redis.Redis, prefix: str, mode: str):
    targets = []
    for key in r.scan_iter(f"doc:{prefix}:*".encode()):
        meta_raw = r.hget(key, b"metadata")
        if not meta_raw:
            continue
        meta = json.loads(meta_raw)
        if meta.get("chunk_type") not in ("text", "outline"):
            continue
        if meta.get("chunk_label") not in CONTEXTUAL_LABELS:
            continue
        if mode == "contextual":
            text = (meta.get("contextual_text") or "").strip()
        else:
            text = (meta.get("raw_text") or "").strip()
            if not text:
                raw = r.hget(key, b"text")
                text = raw.decode("utf-8") if raw else ""
        if text:
            targets.append((key, text))
    return targets


async def rebuild(targets, r: redis.Redis):
    emb = get_text_embedding()
    vectors = await emb.embed_batch([text for _, text in targets])
    if len(vectors) != len(targets):
        raise RuntimeError("embedding count mismatch")
    pipe = r.pipeline(transaction=False)
    for (key, _), vec in zip(targets, vectors):
        pipe.hset(key, b"vector", struct.pack(f"{len(vec)}f", *vec))
    pipe.execute()
    return len(targets)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", default="eee9bf78", help="doc_id 前缀（doc_prefix）")
    parser.add_argument("--mode", choices=["contextual", "clean"], required=True)
    args = parser.parse_args()

    s = get_settings()
    r = redis.Redis(
        host=s.redis_host,
        port=s.redis_port,
        password=s.redis_password or None,
        db=s.redis_db,
        decode_responses=False,
    )
    targets = collect_targets(r, args.prefix, args.mode)
    print(f"mode={args.mode} prefix={args.prefix} targets={len(targets)}")
    if not targets:
        print("no targets, nothing to rebuild")
        return
    count = asyncio.run(rebuild(targets, r))
    print(f"rebuilt vectors = {count}")


if __name__ == "__main__":
    main()
