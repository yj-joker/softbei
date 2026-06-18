"""对指定 case 拉 top30 检索，看 golden 究竟排第几（差一点 vs 完全召不回），
并打印 top8 命中的 chunk_id/类型，定位 procedure 检索盲区原因。

用法: python evaluation/procedure_recall_probe.py
"""
import asyncio
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.chdir(ROOT)
sys.path.insert(0, ROOT)

from evaluation.rag_eval_cli import read_dataset  # noqa: E402
from tools.knowledge_retrieval_tool import get_knowledge_retrieval_tool  # noqa: E402

TARGET = {"case_045", "case_114", "case_116", "case_122"}
DATASET = "evaluation/rag_eval_dataset_v14_image_locator.csv"


async def main():
    cases = [c for c in read_dataset(Path(DATASET)) if c.case_id in TARGET]
    tool = get_knowledge_retrieval_tool()
    for case in cases:
        res = await tool.run(query=case.question, top_k=30, **case.filters)
        items = res.data or []
        ids = [str(getattr(it, "id", "")) for it in items]
        golden = set(case.golden_chunk_ids)
        rank = next((k for k, x in enumerate(ids, 1) if x in golden), None)
        print(f"\n{case.case_id}  golden={case.golden_chunk_ids}")
        print(f"  rank_in_top30 = {rank if rank else 'NOT FOUND in top30'}")
        for k, it in enumerate(items[:8], 1):
            meta = getattr(it, "metadata", None) or {}
            mark = " <== GOLDEN" if ids[k - 1] in golden else ""
            print(f"   {k:>2}. {ids[k-1]:<26} type={meta.get('chunk_type','?'):<11}{mark}")


if __name__ == "__main__":
    asyncio.run(main())
