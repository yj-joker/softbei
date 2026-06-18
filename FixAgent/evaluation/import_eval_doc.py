"""导入评测知识文档（仅用于评测复现）。

document_id 固定为 v14 image_locator 所用值，使 doc_prefix(md5[:12])=eee9bf787cde，
与 rag_eval_dataset_v14_image_locator.csv 的 150 条 golden_chunk_ids 完全对齐，
保证评测可与 v14 / v18f 直接对比。
"""
import asyncio
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # fix-py，切到这里使 load_dotenv() 能读到 .env
os.chdir(ROOT)
sys.path.insert(0, ROOT)

from services.knowledge_service import get_knowledge_service  # noqa: E402

FILE = r"C:\Users\27202\Desktop\摩托车发动机维修手册.pdf"
DOC_ID = "rag_eval_motorcycle_manual_v14_image_locator"
OUT = os.path.join(HERE, "results", "import_v19_step_nocontextual.json")


async def main():
    svc = get_knowledge_service()
    result = await svc.import_document(
        file_url=FILE,
        file_type="pdf",
        document_id=DOC_ID,
        document_version="v14_image_locator",
    )
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("IMPORT DONE")
    for k in ("document_id_used", "doc_prefix", "total_pages", "text_count",
              "image_count", "image_summary_count", "table_count", "process_time_ms"):
        print(k, "=", result.get(k))


if __name__ == "__main__":
    asyncio.run(main())
