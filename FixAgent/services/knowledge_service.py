"""
知识入库服务

编排 文档解析 → 向量化 → Redis 向量库 的完整流程。
只做编排，不自己解析、不自己向量化、不自己写 Redis。

【执行流程】
1. DocumentParserTool 解析 PDF → sections
2. text_chunks → TextEmbedding.embed_batch() → VectorService.add_vector_batch()
3. tables → 转 markdown 文本 → TextEmbedding → VectorService
4. images → 本地图读 base64 直传 ImageEmbedding（绕开 dashscope OSS 中转），URL 仅用于持久化回显
5. 返回导入统计
"""

import asyncio
import base64
import mimetypes
import os
import time
import hashlib
import logging
from typing import List, Optional

from tools.document_tool import get_document_parser
from embeddings.text_embedding import get_text_embedding
from embeddings.image_embedding import get_image_embedding
from services.file_storage import get_file_storage
from services.image_summary_service import get_image_summary_service
from services.chunking_policy import build_section_index_chunks
from services.vector_service import get_vector_service

logger = logging.getLogger(__name__)


def build_image_retrieval_text(policy_text: str, caption: str, section_title: str, page) -> str:
    """Choose the text indexed beside an image record."""
    caption = (caption or "").strip()
    if caption:
        return caption
    return f"{(section_title or '').strip()} 第{page or '?'}页插图"


# base64 直传上限：模型限图片 5MB，base64 体积 +33%，原图卡 4.5MB 留余量。
_IMAGE_BASE64_MAX_BYTES = int(4.5 * 1024 * 1024)


def encode_image_data_uri(local_path: str) -> str:
    """把本地图片读成 base64 data URI，供 embedding 直传、绕开 dashscope OSS 中转。

    返回空串表示不可用（路径无效/超限/读失败），由调用方降级为文本兜底。
    """
    if not local_path or not os.path.isfile(local_path):
        return ""
    try:
        if os.path.getsize(local_path) > _IMAGE_BASE64_MAX_BYTES:
            return ""
        mime = mimetypes.guess_type(local_path)[0] or "image/png"
        with open(local_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        return f"data:{mime};base64,{b64}"
    except OSError:
        return ""


class KnowledgeService:
    """知识入库服务"""

    # embed_batch 单批最大条数（百炼 API 限制）
    _BATCH_SIZE = 20
    _TEXT_BATCH_CONCURRENCY = 12
    _TABLE_CONCURRENCY = 12
    _IMAGE_CONCURRENCY = 12
    _IMAGE_BATCH_SIZE = 8
    _IMAGE_SUMMARY_CONCURRENCY = 3
    # 入向量文本差异化策略：长内容类(step/大纲/通用)补 contextual 上下文以提升召回，
    # 短精确类(safety/参数/表格)保留纯正文避免前缀稀释短句语义。
    # 依据 evaluation v14/v16 各题型 Recall 对比 + golden chunk_label 分布得出。
    _CONTEXTUAL_EMBED_LABELS = {"step", "outline", "general"}

    def __init__(self):
        self.parser = get_document_parser()
        self.text_emb = get_text_embedding()
        self.image_emb = get_image_embedding()
        self.file_storage = get_file_storage()
        self.image_summary_svc = get_image_summary_service()
        self.vector_svc = get_vector_service()

    @staticmethod
    async def _gather_limited(items, limit: int, worker):
        if not items:
            return []
        semaphore = asyncio.Semaphore(max(1, limit))

        async def run_one(item):
            async with semaphore:
                return await worker(item)

        return await asyncio.gather(*(run_one(item) for item in items))

    async def import_document(
        self,
        file_url: str,
        file_type: str = "pdf",
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        document_id: Optional[str] = None,
        device_type: Optional[str] = None,
        manual_type: Optional[str] = None,
        document_version: Optional[str] = None,
        replace_existing: bool = False,
        old_document_id: Optional[str] = None,
        manual_id: Optional[int] = None,
        progress_cb=None
    ) -> dict:
        try:
            return await self._import_document_impl(
                file_url=file_url,
                file_type=file_type,
                category=category,
                tags=tags,
                document_id=document_id,
                device_type=device_type,
                manual_type=manual_type,
                document_version=document_version,
                replace_existing=replace_existing,
                old_document_id=old_document_id,
                manual_id=manual_id,
                progress_cb=progress_cb,
            )
        except Exception as exc:
            if document_id:
                current = self.vector_svc.get_document_manifest(document_id) or {}
                if current.get("status") != "failed":
                    self.vector_svc.put_document_manifest(document_id, {
                        **current,
                        "document_id": document_id,
                        "status": "failed",
                        "error_message": str(exc),
                    })
            raise

    def delete_document(self, document_id: str) -> dict:
        """级联删除一个文档的全部痕迹：向量 chunk + MinIO 图片 + 状态 manifest。

        顺序关键：先查图片 URL（趁向量还在）→ 删向量 → 删 MinIO → 删 manifest。
        MinIO 删除失败仅记警告，不影响向量已清除的结果。
        """
        if not document_id:
            return {"vectors_deleted": 0, "images_deleted": 0, "manifest_deleted": False}
        image_urls = self.vector_svc.get_document_image_urls(document_id)
        vectors_deleted = self.vector_svc.delete_by_document(document_id)
        images_deleted = 0
        try:
            images_deleted = self.file_storage.delete_images(image_urls)
        except Exception as exc:
            logger.warning("[删除] MinIO 图片清理失败, documentId=%s, error=%s", document_id, exc)
        manifest_deleted = self.vector_svc.delete_document_manifest(document_id)
        logger.info(
            "[删除] documentId=%s 完成: 向量=%d, 图片=%d, manifest=%s",
            document_id, vectors_deleted, images_deleted, manifest_deleted,
        )
        return {
            "vectors_deleted": vectors_deleted,
            "images_deleted": images_deleted,
            "manifest_deleted": manifest_deleted,
        }

    async def _import_document_impl(
        self,
        file_url: str,
        file_type: str = "pdf",
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        document_id: Optional[str] = None,
        device_type: Optional[str] = None,
        manual_type: Optional[str] = None,
        document_version: Optional[str] = None,
        replace_existing: bool = False,
        old_document_id: Optional[str] = None,
        manual_id: Optional[int] = None,
        progress_cb=None
    ) -> dict:
        """
        导入文档：解析 → 向量化 → 入库

        Returns:
            {
                "file_name": str,
                "total_pages": int,
                "text_count": int,       # 入库文本块数
                "image_count": int,      # 入库图片数
                "table_count": int,      # 入库表格数
                "sections": [...],       # 各章节统计摘要
                "extraction_summary": {...},
                "process_time_ms": int
            }
        """
        t0 = time.time()

        async def emit_progress(stage: str, percent: int):
            # 进度上报「尽力而为」：失败绝不能影响导入主流程
            if progress_cb is None:
                return
            try:
                await progress_cb(stage, percent)
            except Exception as exc:
                logger.warning("[知识导入] 进度上报失败(已忽略), stage=%s, error=%s", stage, exc)

        if document_id:
            self.vector_svc.put_document_manifest(document_id, {
                "document_id": document_id,
                "source_file_url": file_url,
                "device_type": device_type,
                "manual_type": manual_type,
                "document_version": document_version,
                "status": "parsing",
                "category": category,
                "tags": tags or [],
            })

        # 1. 解析文档
        parse_result = await self.parser._execute(file_url, file_type)
        file_name = parse_result["file_name"]
        total_pages = parse_result["total_pages"]
        sections = parse_result["sections"]
        extraction_summary = parse_result["extraction_summary"]
        source_file_url = self.file_storage.ensure_document_url(file_url)

        document_id = document_id or hashlib.md5(f"{file_name}|{file_url}".encode()).hexdigest()[:12]
        doc_prefix = hashlib.md5(document_id.encode()).hexdigest()[:12]  # [:8]→[:12]：48bit，碰撞窗口推到数百万文档
        common_metadata = {
            "record_type": "manual",
            "status": "ready",
            "file_name": file_name,
            "document_id": document_id,
            "source_file_url": source_file_url,
            "device_type": device_type,
            "manual_type": manual_type,
            "document_version": document_version,
        }
        if replace_existing and old_document_id:
            # 删除旧版本的向量数据，用旧版本的 document_id（而非当前新版本的）
            logger.info("删除旧版本向量: old_document_id=%s", old_document_id)
            self.vector_svc.delete_by_document(old_document_id)
        self.vector_svc.put_document_manifest(document_id, {
            **common_metadata,
            "status": "indexing",
            "category": category,
            "tags": tags or [],
        })

        text_count = 0
        image_count = 0
        image_success_count = 0
        image_failed_count = 0
        image_embedding_failed_count = 0
        table_count = 0
        table_success_count = 0
        table_failed_count = 0
        image_summary_count = 0
        image_summary_failed_count = 0
        parsed_text_chunks_count = int((extraction_summary or {}).get("text_chunks_total") or 0)
        chunked_text_chunks_count = 0
        indexable_text_chunks_count = 0

        stage_timings_ms = {"parse_ms": int((time.time() - t0) * 1000)}
        await emit_progress("解析文档", 20)

        # 2. 先按整篇文档构建任务；chunk 策略和 doc_id 规则保持不变。
        chunk_build_started = time.time()
        text_jobs = []
        table_jobs = []
        image_jobs = []
        global_chunk_doc_ids = {}

        for sec_idx, section in enumerate(sections):
            section_title = section.get("section_title", f"第{sec_idx + 1}章")
            page_range = section.get("page_range", "")
            sec_category = category or section_title
            structured_chunks = build_section_index_chunks(section, section_index=sec_idx)
            image_policy_chunks = [
                chunk for chunk in structured_chunks
                if chunk.get("chunk_type") == "image"
            ]
            text_policy_chunks = [
                chunk for chunk in structured_chunks
                if chunk.get("chunk_type") in {"text", "outline"}
            ]
            chunked_text_chunks_count += len(text_policy_chunks)

            valid_chunks = [
                chunk for chunk in text_policy_chunks
                if len(((chunk.get("metadata") or {}).get("raw_text") or chunk.get("text") or "").strip()) >= 10
            ]
            indexable_text_chunks_count += len(valid_chunks)
            table_chunks_for_refs = [
                chunk for chunk in structured_chunks
                if chunk.get("chunk_type") == "table" and (chunk.get("text") or "").strip()
            ]
            for global_i, chunk in enumerate(valid_chunks):
                id_kind = "out" if chunk.get("chunk_type") == "outline" else "txt"
                chunk_id = f"{doc_prefix}:{sec_idx:02d}:{id_kind}:{global_i:04d}"
                global_chunk_doc_ids[chunk.get("id")] = chunk_id
                text_jobs.append({
                    "order": len(text_jobs),
                    "chunk": chunk,
                    "chunk_id": chunk_id,
                    "section_title": section_title,
                    "page_range": page_range,
                    "sec_category": sec_category,
                })
            for t_idx, table_chunk in enumerate(table_chunks_for_refs):
                table_id = f"{doc_prefix}:{sec_idx:02d}:tbl:{t_idx:04d}"
                global_chunk_doc_ids[table_chunk.get("id")] = table_id
            for policy_image in image_policy_chunks:
                policy_meta = policy_image.get("metadata") or {}
                image_index = policy_meta.get("image_index")
                if image_index is not None:
                    global_chunk_doc_ids[policy_image.get("id")] = f"{doc_prefix}:{sec_idx:02d}:img:{int(image_index):04d}"

            table_chunks = [
                chunk for chunk in structured_chunks
                if chunk.get("chunk_type") == "table" and (chunk.get("text") or "").strip()
            ]
            for t_idx, table_chunk in enumerate(table_chunks):
                table_text = table_chunk["text"]
                if not table_text.strip():
                    continue
                table_id = global_chunk_doc_ids.get(table_chunk.get("id")) or f"{doc_prefix}:{sec_idx:02d}:tbl:{t_idx:04d}"
                global_chunk_doc_ids[table_chunk.get("id")] = table_id
                table_jobs.append({
                    "order": len(table_jobs),
                    "t_idx": t_idx,
                    "table_chunk": table_chunk,
                    "table_text": table_text,
                    "table_id": table_id,
                    "section_title": section_title,
                    "page_range": page_range,
                    "sec_category": sec_category,
                })

            for img_idx, img in enumerate(section.get("images", [])):
                policy_image = next(
                    (
                        chunk for chunk in image_policy_chunks
                        if (chunk.get("metadata") or {}).get("image_index") == img_idx
                    ),
                    {},
                )
                image_jobs.append({
                    "order": len(image_jobs),
                    "img_idx": img_idx,
                    "img": img,
                    "img_id": f"{doc_prefix}:{sec_idx:02d}:img:{img_idx:04d}",
                    "summary_id": f"{doc_prefix}:{sec_idx:02d}:ims:{img_idx:04d}",
                    "policy_metadata": dict(policy_image.get("metadata") or {}),
                    "policy_text": policy_image.get("text", ""),
                    "section_title": section_title,
                    "page_range": page_range,
                    "sec_category": sec_category,
                })

        stage_timings_ms["chunk_build_ms"] = int((time.time() - chunk_build_started) * 1000)
        await emit_progress("构建索引", 35)

        # 3a. 文本块：整篇文档全局分批并发向量化，再批量入库。
        text_embedding_started = time.time()
        text_batches = [
            (batch_start, text_jobs[batch_start:batch_start + self._BATCH_SIZE])
            for batch_start in range(0, len(text_jobs), self._BATCH_SIZE)
        ]

        async def embed_text_batch(batch_spec):
            batch_start, batch = batch_spec
            vectors = await self.text_emb.embed_batch(
                [self._embed_text_for_chunk(job["chunk"]) for job in batch]
            )
            if len(vectors) != len(batch):
                raise RuntimeError("text embedding count mismatch")
            return batch_start, batch, vectors

        text_batch_results = await self._gather_limited(
            text_batches,
            self._TEXT_BATCH_CONCURRENCY,
            embed_text_batch,
        )
        stage_timings_ms["text_embedding_ms"] = int((time.time() - text_embedding_started) * 1000)

        text_docs = []
        for _, batch, vectors in sorted(text_batch_results, key=lambda item: item[0]):
            for job, vec in zip(batch, vectors):
                chunk = job["chunk"]
                chunk_metadata = {
                    **common_metadata,
                    **(chunk.get("metadata") or {}),
                    "section_title": job["section_title"],
                    "page_range": job["page_range"],
                    "chunk_type": chunk.get("chunk_type", "text"),
                    "page": chunk.get("page"),
                    "chunk_label": chunk.get("chunk_label", "general"),
                }
                chunk_metadata = self._resolve_chunk_refs(chunk_metadata, global_chunk_doc_ids)
                text_docs.append({
                    "doc_id": job["chunk_id"],
                    "text": chunk["text"],
                    "vector": vec,
                    "category": job["sec_category"],
                    "tags": tags,
                    "metadata": chunk_metadata
                })

        # 方案乙：给 step 块额外嵌一份“纯内容”向量（doc_id 用 :srw: 后缀）。
        # 召回侧 step_raw 路命中后，经 _canonical_id 的 source_chunk_id 归并回原块；
        # 原块 id/边界/带前缀向量一律不动，150 条 golden 仍可比。
        # embed_batch 对未命中缓存的文本逐条串行，整批一次性调用等于并发=1；
        # 故与文本主段一致，改为分条 _gather_limited 并发，避免成为隐藏瓶颈。
        step_raw_embedding_started = time.time()
        step_raw_inputs = [
            doc for doc in text_docs
            if (doc.get("metadata") or {}).get("chunk_label") == "step"
            and ((doc.get("metadata") or {}).get("raw_text") or "").strip()
        ]

        async def embed_step_raw(doc):
            raw_text = doc["metadata"]["raw_text"].strip()
            vec = await self.text_emb.embed(raw_text)
            return doc, raw_text, vec

        step_raw_results = await self._gather_limited(
            step_raw_inputs,
            self._TEXT_BATCH_CONCURRENCY,
            embed_step_raw,
        )
        for doc, raw_text, vec in step_raw_results:
            srw_meta = dict(doc["metadata"])
            srw_meta.update({
                "chunk_type": "step_raw",
                "chunk_label": "step_raw",
                "retrieval_route": "step_raw",
                "source_chunk_id": doc["doc_id"],
                "contextual_text": raw_text,
            })
            text_docs.append({
                "doc_id": doc["doc_id"].replace(":txt:", ":srw:"),
                "text": raw_text,
                "vector": vec,
                "category": doc.get("category"),
                "tags": doc.get("tags"),
                "metadata": srw_meta,
            })
        stage_timings_ms["step_raw_embedding_ms"] = int((time.time() - step_raw_embedding_started) * 1000)

        text_write_started = time.time()
        written = self.vector_svc.add_vector_batch(text_docs)
        if written != len(text_docs):
            self._mark_failed_import(
                document_id, common_metadata, category, tags,
                text_count, image_count, table_count, image_summary_count,
                "failed to write all text vector records",
            )
            raise RuntimeError("failed to write all text vector records")
        text_count = written
        stage_timings_ms["text_write_ms"] = int((time.time() - text_write_started) * 1000)
        await emit_progress("文本向量化", 60)

        # 3b. 表格块：整篇文档全局并发向量化，表格失败只计数不中断整份导入。
        table_embedding_started = time.time()

        async def embed_table_job(table_job):
            try:
                vec = await self.text_emb.embed(table_job["table_text"])
                return {**table_job, "vector": vec, "error": None}
            except Exception as exc:
                return {**table_job, "vector": None, "error": exc}

        table_results = await self._gather_limited(
            table_jobs,
            self._TABLE_CONCURRENCY,
            embed_table_job,
        )
        stage_timings_ms["table_embedding_ms"] = int((time.time() - table_embedding_started) * 1000)

        table_docs = []
        for table_result in sorted(table_results, key=lambda item: item["order"]):
            t_idx = table_result["t_idx"]
            table_chunk = table_result["table_chunk"]
            if table_result["error"] is not None:
                table_failed_count += 1
                logger.warning(
                    "[知识导入] 表格向量化失败, documentId=%s, section=%s, tableIndex=%s, page=%s, error=%s",
                    document_id, table_result["section_title"], t_idx, table_chunk.get("page"), table_result["error"],
                )
                continue
            table_metadata = {
                **common_metadata,
                **(table_chunk.get("metadata") or {}),
                "section_title": table_result["section_title"],
                "page_range": table_result["page_range"],
                "chunk_type": "table",
                "page": table_chunk.get("page"),
                "chunk_label": table_chunk.get("chunk_label", "table_full"),
            }
            table_metadata = self._resolve_chunk_refs(table_metadata, global_chunk_doc_ids)
            table_docs.append({
                "doc_id": table_result["table_id"],
                "text": table_result["table_text"],
                "vector": table_result["vector"],
                "category": table_result["sec_category"],
                "tags": tags,
                "metadata": table_metadata,
            })

        table_write_started = time.time()
        table_written = self.vector_svc.add_vector_batch(table_docs)
        table_count += table_written
        table_success_count += table_written
        if table_written != len(table_docs):
            table_failed_count += len(table_docs) - table_written
            logger.warning(
                "[知识导入] 表格批量入库部分失败, documentId=%s, expected=%s, written=%s",
                document_id, len(table_docs), table_written,
            )
        stage_timings_ms["table_write_ms"] = int((time.time() - table_write_started) * 1000)
        await emit_progress("表格处理", 72)

        # 3c. 图片本体：保留图片向量策略，改成整篇文档全局准备 + 批量图片向量化。
        image_prepare_started = time.time()

        async def prepare_image_job(image_job):
            img = image_job["img"]
            caption = (img.get("caption") or "").strip()
            img_name = img.get("image_name", f"img_{image_job['img_idx']}")
            local_path = img.get("local_path", "")
            try:
                image_url = await asyncio.to_thread(self.file_storage.ensure_public_url, img)
                img_text = build_image_retrieval_text(
                    image_job.get("policy_text", ""),
                    caption,
                    image_job["section_title"],
                    img.get("page", "?"),
                )
                if local_path:
                    data_uri = await asyncio.to_thread(encode_image_data_uri, local_path)
                    if data_uri:
                        embedding_input = data_uri
                        embedding_source = "image_base64"
                    else:
                        embedding_input = ""
                        embedding_source = "caption_text"
                elif image_url and image_url.startswith(("http://", "https://")):
                    embedding_input = image_url
                    embedding_source = "image_url"
                else:
                    embedding_input = ""
                    embedding_source = "caption_text"
                return {
                    **image_job,
                    "caption": caption,
                    "img_name": img_name,
                    "local_path": local_path,
                    "image_url": image_url,
                    "img_text": img_text,
                    "embedding_input": embedding_input,
                    "embedding_source": embedding_source,
                    "prepare_error": None,
                }
            except Exception as exc:
                return {**image_job, "img_name": img_name, "prepare_error": exc}

        prepared_images = await self._gather_limited(
            image_jobs,
            self._IMAGE_CONCURRENCY,
            prepare_image_job,
        )
        stage_timings_ms["image_prepare_ms"] = int((time.time() - image_prepare_started) * 1000)

        ready_image_jobs = []
        for image_job in sorted(prepared_images, key=lambda item: item["order"]):
            if image_job.get("prepare_error") is not None:
                image_failed_count += 1
                img = image_job["img"]
                logger.warning(
                    "[知识导入] 图片导入失败, documentId=%s, section=%s, imageIndex=%s, page=%s, imageName=%s, error=%s",
                    document_id, image_job["section_title"], image_job["img_idx"],
                    img.get("page"), image_job.get("img_name"), image_job["prepare_error"],
                )
                continue
            image_job["policy_metadata"] = self._resolve_chunk_refs(
                image_job.get("policy_metadata") or {},
                global_chunk_doc_ids,
            )
            ready_image_jobs.append(image_job)

        image_embedding_started = time.time()
        image_input_jobs = [job for job in ready_image_jobs if job.get("embedding_input")]
        caption_image_jobs = [job for job in ready_image_jobs if not job.get("embedding_input")]
        image_batches = [
            (batch_start, image_input_jobs[batch_start:batch_start + self._IMAGE_BATCH_SIZE])
            for batch_start in range(0, len(image_input_jobs), self._IMAGE_BATCH_SIZE)
        ]

        async def embed_image_batch(batch_spec):
            _, batch = batch_spec
            try:
                vectors = await self.image_emb.embed_batch([job["embedding_input"] for job in batch])
                if len(vectors) != len(batch):
                    raise RuntimeError("image embedding count mismatch")
                return [
                    {
                        **job,
                        "vector": vec,
                        "embedding_error": "",
                        "image_embedding_failed": False,
                        "error": None,
                    }
                    for job, vec in zip(batch, vectors)
                ]
            except Exception:
                results = []
                for job in batch:
                    try:
                        vec = await self.image_emb.embed(job["embedding_input"])
                        results.append({
                            **job,
                            "vector": vec,
                            "embedding_error": "",
                            "image_embedding_failed": False,
                            "error": None,
                        })
                    except Exception as exc:
                        try:
                            vec = await self.text_emb.embed(job["img_text"])
                            results.append({
                                **job,
                                "vector": vec,
                                "embedding_source": "caption_text_fallback",
                                "embedding_error": str(exc),
                                "image_embedding_failed": True,
                                "error": None,
                            })
                        except Exception as fallback_exc:
                            results.append({
                                **job,
                                "vector": None,
                                "embedding_source": "caption_text_fallback",
                                "embedding_error": str(exc),
                                "image_embedding_failed": True,
                                "error": fallback_exc,
                            })
                return results

        image_batch_results = await self._gather_limited(
            image_batches,
            self._IMAGE_CONCURRENCY,
            embed_image_batch,
        )
        image_embedding_results = [
            result
            for batch_result in image_batch_results
            for result in batch_result
        ]

        async def embed_caption_image(job):
            try:
                vec = await self.text_emb.embed(job["img_text"])
                return {
                    **job,
                    "vector": vec,
                    "embedding_error": "",
                    "image_embedding_failed": False,
                    "error": None,
                }
            except Exception as exc:
                return {
                    **job,
                    "vector": None,
                    "embedding_error": str(exc),
                    "image_embedding_failed": False,
                    "error": exc,
                }

        caption_image_results = await self._gather_limited(
            caption_image_jobs,
            self._TEXT_BATCH_CONCURRENCY,
            embed_caption_image,
        )
        image_embedding_results.extend(caption_image_results)
        stage_timings_ms["image_embedding_ms"] = int((time.time() - image_embedding_started) * 1000)

        image_write_started = time.time()
        successful_images = []
        for image_result in sorted(image_embedding_results, key=lambda item: item["order"]):
            img = image_result["img"]
            if image_result.get("image_embedding_failed"):
                image_embedding_failed_count += 1
                logger.warning(
                    "[知识导入] 图片向量化失败, documentId=%s, section=%s, imageIndex=%s, page=%s, imageName=%s, fallback=caption_text, error=%s",
                    document_id, image_result["section_title"], image_result["img_idx"],
                    img.get("page"), image_result["img_name"], image_result.get("embedding_error", ""),
                )
            if image_result.get("error") is not None:
                image_failed_count += 1
                logger.warning(
                    "[知识导入] 图片导入失败, documentId=%s, section=%s, imageIndex=%s, page=%s, imageName=%s, error=%s",
                    document_id, image_result["section_title"], image_result["img_idx"],
                    img.get("page"), image_result["img_name"], image_result["error"],
                )
                continue
            image_written = self.vector_svc.add_vector(
                doc_id=image_result["img_id"],
                text=image_result["img_text"],
                vector=image_result["vector"],
                category=image_result["sec_category"],
                tags=tags,
                metadata={
                    **common_metadata,
                    **image_result["policy_metadata"],
                    "section_title": image_result["section_title"],
                    "page_range": image_result["page_range"],
                    "chunk_type": "image",
                    "chunk_label": "image",
                    "page": img.get("page"),
                    "image_name": image_result["img_name"],
                    "local_path": image_result["local_path"],
                    "image_url": image_result["image_url"],
                    "caption": image_result["caption"],
                    "embedding_source": image_result["embedding_source"],
                    "embedding_error": image_result.get("embedding_error", ""),
                }
            )
            if not image_written:
                image_failed_count += 1
                logger.warning(
                    "[知识导入] 图片入库失败, documentId=%s, section=%s, imageIndex=%s, page=%s, imageName=%s, reason=vector write returned false",
                    document_id, image_result["section_title"], image_result["img_idx"],
                    img.get("page"), image_result["img_name"],
                )
                continue
            image_count += 1
            image_success_count += 1
            successful_images.append(image_result)
        stage_timings_ms["image_write_ms"] = int((time.time() - image_write_started) * 1000)
        await emit_progress("图片处理", 88)

        # 3d. 图片摘要：保留 image_summary 检索路线，摘要生成限流并发，摘要文本再全局批量向量化。
        image_summary_started = time.time()

        async def summarize_image(image_result):
            img = image_result["img"]
            try:
                summary = await self.image_summary_svc.summarize(
                    image_url=image_result["image_url"],
                    caption=image_result["caption"],
                    context_before=img.get("context_before", ""),
                    context_after=img.get("context_after", ""),
                    section_title=image_result["section_title"],
                    local_path=image_result.get("local_path", ""),
                )
                summary_text = ((summary or {}).get("image_summary") or "").strip()
                return {
                    **image_result,
                    "summary": summary or {},
                    "summary_text": summary_text,
                    "summary_error": None,
                }
            except Exception as exc:
                return {**image_result, "summary": {}, "summary_text": "", "summary_error": exc}

        summary_jobs = await self._gather_limited(
            successful_images,
            self._IMAGE_SUMMARY_CONCURRENCY,
            summarize_image,
        )
        stage_timings_ms["image_summary_ms"] = int((time.time() - image_summary_started) * 1000)

        summary_text_jobs = []
        for summary_job in sorted(summary_jobs, key=lambda item: item["order"]):
            if summary_job.get("summary_error") is not None:
                image_summary_failed_count += 1
                img = summary_job["img"]
                logger.warning(
                    "[知识导入] 图片摘要导入失败, documentId=%s, section=%s, imageIndex=%s, page=%s, imageName=%s, error=%s",
                    document_id, summary_job["section_title"], summary_job["img_idx"],
                    img.get("page"), summary_job["img_name"], summary_job["summary_error"],
                )
                continue
            if summary_job.get("summary_text"):
                summary_text_jobs.append(summary_job)

        image_summary_embedding_started = time.time()
        summary_batches = [
            (batch_start, summary_text_jobs[batch_start:batch_start + self._BATCH_SIZE])
            for batch_start in range(0, len(summary_text_jobs), self._BATCH_SIZE)
        ]

        async def embed_summary_batch(batch_spec):
            _, batch = batch_spec
            try:
                vectors = await self.text_emb.embed_batch([job["summary_text"] for job in batch])
                if len(vectors) != len(batch):
                    raise RuntimeError("image summary embedding count mismatch")
                return [
                    {**job, "summary_vector": vec, "summary_embedding_error": None}
                    for job, vec in zip(batch, vectors)
                ]
            except Exception as batch_exc:
                results = []
                for job in batch:
                    try:
                        vec = await self.text_emb.embed(job["summary_text"])
                        results.append({**job, "summary_vector": vec, "summary_embedding_error": None})
                    except Exception as exc:
                        results.append({**job, "summary_vector": None, "summary_embedding_error": exc or batch_exc})
                return results

        summary_batch_results = await self._gather_limited(
            summary_batches,
            self._TEXT_BATCH_CONCURRENCY,
            embed_summary_batch,
        )
        summary_embedding_results = [
            result
            for batch_result in summary_batch_results
            for result in batch_result
        ]
        stage_timings_ms["image_summary_embedding_ms"] = int((time.time() - image_summary_embedding_started) * 1000)

        summary_docs = []
        for summary_result in sorted(summary_embedding_results, key=lambda item: item["order"]):
            img = summary_result["img"]
            if summary_result.get("summary_embedding_error") is not None:
                image_summary_failed_count += 1
                logger.warning(
                    "[知识导入] 图片摘要向量化失败, documentId=%s, section=%s, imageIndex=%s, page=%s, imageName=%s, error=%s",
                    document_id, summary_result["section_title"], summary_result["img_idx"],
                    img.get("page"), summary_result["img_name"], summary_result["summary_embedding_error"],
                )
                continue
            summary = summary_result["summary"]
            summary_docs.append({
                "doc_id": summary_result["summary_id"],
                "text": summary_result["summary_text"],
                "vector": summary_result["summary_vector"],
                "category": summary_result["sec_category"],
                "tags": tags,
                "metadata": {
                    **common_metadata,
                    **summary_result["policy_metadata"],
                    "section_title": summary_result["section_title"],
                    "page_range": summary_result["page_range"],
                    "chunk_type": "image_summary",
                    "chunk_label": "image_summary",
                    "page": img.get("page"),
                    "image_name": summary_result["img_name"],
                    "image_url": summary_result["image_url"],
                    "image_title": summary.get("image_title", ""),
                    "image_summary": summary_result["summary_text"],
                    "summary_source": summary.get("summary_source", ""),
                    "retrieval_route": "image_summary",
                    "source_image_id": summary_result["img_id"],
                    "context_before": img.get("context_before", ""),
                    "context_after": img.get("context_after", ""),
                }
            })

        image_summary_write_started = time.time()
        summary_written = self.vector_svc.add_vector_batch(summary_docs)
        image_summary_count += summary_written
        if summary_written != len(summary_docs):
            image_summary_failed_count += len(summary_docs) - summary_written
            logger.warning(
                "[知识导入] 图片摘要批量入库部分失败, documentId=%s, expected=%s, written=%s",
                document_id, len(summary_docs), summary_written,
            )
        stage_timings_ms["image_summary_write_ms"] = int((time.time() - image_summary_write_started) * 1000)
        await emit_progress("图片摘要", 96)

        t1 = time.time()
        stage_timings_ms["total_ms"] = int((t1 - t0) * 1000)
        logger.info(
            "[知识导入] 入库完成, documentId=%s, parsed_text_chunks=%d, chunked_text_chunks=%d, indexable_text_chunks=%d, text=%d, table_success=%d, table_failed=%d, image_success=%d, image_failed=%d, image_embedding_failed=%d, image_summary_success=%d, image_summary_failed=%d, stage_timings_ms=%s",
            document_id, parsed_text_chunks_count, chunked_text_chunks_count,
            indexable_text_chunks_count, text_count, table_success_count, table_failed_count,
            image_success_count, image_failed_count, image_embedding_failed_count,
            image_summary_count, image_summary_failed_count, stage_timings_ms,
        )
        self.vector_svc.put_document_manifest(document_id, {
            **common_metadata,
            "status": "ready",
            "category": category,
            "tags": tags or [],
            "total_pages": total_pages,
            "text_count": text_count,
            "parsed_text_chunks_count": parsed_text_chunks_count,
            "chunked_text_chunks_count": chunked_text_chunks_count,
            "indexable_text_chunks_count": indexable_text_chunks_count,
            "image_count": image_count,
            "image_success_count": image_success_count,
            "image_failed_count": image_failed_count,
            "image_embedding_failed_count": image_embedding_failed_count,
            "image_summary_count": image_summary_count,
            "image_summary_failed_count": image_summary_failed_count,
            "table_count": table_count,
            "table_success_count": table_success_count,
            "table_failed_count": table_failed_count,
            "stage_timings_ms": stage_timings_ms,
            "kg_status": "pending",
            "manual_id": manual_id,
        })

        return {
            "file_name": file_name,
            "document_id": document_id,
            "document_version": document_version,
            "source_file_url": source_file_url,
            "total_pages": total_pages,
            "text_count": text_count,
            "parsed_text_chunks_count": parsed_text_chunks_count,
            "chunked_text_chunks_count": chunked_text_chunks_count,
            "indexable_text_chunks_count": indexable_text_chunks_count,
            "image_count": image_count,
            "image_success_count": image_success_count,
            "image_failed_count": image_failed_count,
            "image_embedding_failed_count": image_embedding_failed_count,
            "image_summary_count": image_summary_count,
            "image_summary_failed_count": image_summary_failed_count,
            "table_count": table_count,
            "table_success_count": table_success_count,
            "table_failed_count": table_failed_count,
            "stage_timings_ms": stage_timings_ms,
            "sections": [
                {
                    "section_title": s.get("section_title", ""),
                    "page_range": s.get("page_range", ""),
                    "text_chunks": len(s.get("text_chunks", [])),
                    "images": len(s.get("images", [])),
                    "tables": len(s.get("tables", []))
                }
                for s in sections
            ],
            "extraction_summary": extraction_summary,
            "process_time_ms": int((t1 - t0) * 1000)
        }

    @staticmethod
    def _embed_text_for_chunk(chunk: dict) -> str:
        """选择送入 embedding 的文本：长内容类补 contextual 上下文，短精确类保纯正文。

        只改变向量化输入，不改变 chunk 数量/顺序/doc_id，
        因此 evaluation 的 golden_chunk_ids 仍可复用、指标可比。
        """
        metadata = chunk.get("metadata") or {}
        label = metadata.get("chunk_label") or chunk.get("chunk_label") or ""
        if label in KnowledgeService._CONTEXTUAL_EMBED_LABELS:
            contextual = (metadata.get("contextual_text") or "").strip()
            if contextual:
                return contextual
        return chunk["text"]

    @staticmethod
    def _table_to_text(table: dict) -> str:
        """将表格 dict 转为可向量化的 markdown 文本"""
        rows = table.get("rows", [])
        if not rows:
            return ""

        lines = []
        caption = table.get("caption", "")
        if caption:
            lines.append(f"表格：{caption}")

        for row in rows:
            if row and any(cell for cell in row):
                lines.append(" | ".join(str(cell).strip() for cell in row))

        return "\n".join(lines)

    @staticmethod
    def _normalize_text_chunk(chunk) -> dict:
        if isinstance(chunk, dict):
            return {
                "text": str(chunk.get("text", "")),
                "page": chunk.get("page"),
                "chunk_label": chunk.get("chunk_label", "page"),
                "context_before": chunk.get("context_before", ""),
                "context_after": chunk.get("context_after", ""),
            }
        return {
            "text": str(chunk),
            "page": None,
            "chunk_label": "page",
            "context_before": "",
            "context_after": "",
        }

    @staticmethod
    def _resolve_chunk_refs(metadata: dict, local_chunk_doc_ids: dict) -> dict:
        resolved = dict(metadata or {})
        if not local_chunk_doc_ids:
            return resolved

        for field_name in (
            "prev_chunk_id",
            "next_chunk_id",
            "parent_table_chunk_id",
            "source_image_id",
            "summary_chunk_id",
        ):
            value = resolved.get(field_name)
            if isinstance(value, str) and value in local_chunk_doc_ids:
                resolved[field_name] = local_chunk_doc_ids[value]

        for field_name in ("related_step_chunk_ids", "related_text_chunk_ids"):
            values = resolved.get(field_name)
            if isinstance(values, list):
                resolved[field_name] = [
                    local_chunk_doc_ids.get(value, value)
                    for value in values
                    if value
                ]
        return resolved

    def _mark_failed_import(
        self,
        document_id: str,
        common_metadata: dict,
        category: Optional[str],
        tags: Optional[List[str]],
        text_count: int,
        image_count: int,
        table_count: int,
        image_summary_count: int,
        error_message: str,
    ) -> None:
        self.vector_svc.put_document_manifest(document_id, {
            **common_metadata,
            "status": "failed",
            "category": category,
            "tags": tags or [],
            "text_count": text_count,
            "image_count": image_count,
            "image_summary_count": image_summary_count,
            "table_count": table_count,
            "error_message": error_message,
        })


# 单例
_knowledge_service: Optional[KnowledgeService] = None


def get_knowledge_service() -> KnowledgeService:
    """获取知识入库服务单例"""
    global _knowledge_service
    if _knowledge_service is None:
        _knowledge_service = KnowledgeService()
    return _knowledge_service
