"""用 VLM 重建库里所有 image_summary(:ims:) 记录，提升图片的文本检索代理质量。

对每个 image(:img:) chunk：本地图 -> base64 -> qwen-vl 生成中文视觉摘要 -> 重新 embed
-> 覆盖/新建对应的 :ims: 记录。不改 image 本体向量，不改 chunk 边界(golden 稳定)。
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from embeddings.text_embedding import get_text_embedding
from services.knowledge.image_summary_service import get_image_summary_service
from services.knowledge.vector_service import get_vector_service


def _load_image_chunks(vs):
    res = vs.redis.execute_command(
        "FT.SEARCH", "knowledge_vectors_v2", "@chunk_type:{image}",
        "RETURN", "2", "id", "metadata", "LIMIT", "0", "500", "DIALECT", "2",
    )
    items = []
    for i in range(1, len(res), 2):
        fields = {res[i + 1][j]: res[i + 1][j + 1] for j in range(0, len(res[i + 1]), 2)}
        meta = json.loads(fields.get(b"metadata", b"{}").decode("utf-8"))
        items.append((fields.get(b"id", b"").decode("utf-8"), meta))
    return items


async def main():
    vs = get_vector_service()
    svc = get_image_summary_service()
    emb = get_text_embedding()

    items = _load_image_chunks(vs)
    print(f"image chunks found: {len(items)}")
    ok = fail = 0
    for img_id, meta in items:
        ref = svc._resolve_image_ref(meta.get("image_url", ""), meta.get("local_path", ""))
        if not ref:
            fail += 1
            print("skip(no image):", img_id)
            continue
        try:
            summary = await svc._summarize_with_llm(
                image_ref=ref,
                caption=meta.get("caption", ""),
                context_before=meta.get("context_before", ""),
                context_after=meta.get("context_after", ""),
                section_title=meta.get("section_title", ""),
            )
        except Exception as exc:
            fail += 1
            print("VLM ERR", img_id, exc)
            continue

        text = (summary or {}).get("image_summary", "").strip()
        title = (summary or {}).get("image_title", "").strip()
        if not text:
            fail += 1
            print("skip(empty summary):", img_id)
            continue

        ims_id = img_id.replace(":img:", ":ims:")
        vec = await emb.embed(text)
        new_meta = dict(meta)
        new_meta.update({
            "chunk_type": "image_summary",
            "chunk_label": "image_summary",
            "image_summary": text,
            "image_title": title,
            "summary_source": "multimodal_llm",
            "retrieval_route": "image_summary",
            "source_image_id": img_id,
            "raw_text": text,
        })
        new_meta.pop("embedding_source", None)
        vs.add_vector(doc_id=ims_id, text=text, vector=vec, metadata=new_meta)
        ok += 1
        if ok % 10 == 0:
            print(f"  rebuilt {ok} ...")

    print(f"DONE ok={ok} fail={fail}")


if __name__ == "__main__":
    asyncio.run(main())
