"""
手册版本升级 → 知识图谱同步服务

当新版手册上线时，与旧版做 chunk 级别的 diff，
然后按 MODIFIED / ADDED / DELETED 三类分别处理：

  DELETED  — 旧版有、新版没有的 chunk：找到对应图谱节点，进入人工审核队列
  MODIFIED — chunk_uid 相同但内容变了：用新内容重新判定，REPLACE 则更新节点，SUPPLEMENT 则补边
  ADDED    — 新版有、旧版没有的 chunk：向量搜现有节点，判定 SUPPLEMENT / REPLACE；
             无候选时交给后续 KG 抽取，避免创建孤立节点

触发方式：手册新版导入完成后，由 KnowledgeService 或 MQ Consumer 调用
    sync_result = await ManualUpgradeSync().sync(
        old_document_id="...",
        new_document_id="...",
        device_type="柴油机",
        manual_id=3,
    )
"""

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from config.settings import get_settings
from embeddings.text_embedding import get_text_embedding
from services.knowledge.expiration import (
    ExpirationService,
    _build_expiration_prompt,
    _parse_verdict,
    EXPIRATION_SYSTEM_PROMPT,
)
from services.knowledge.vector_service import build_redis_filter, get_vector_service
from services.llm.service import get_llm_service

logger = logging.getLogger(__name__)

# chunk 主类型（步骤/大纲/通用/参数/安全…），派生类型已在 list_document_chunks 中排除
_PRIMARY_CHUNK_TYPES = {"text", "outline", "table"}


# ────────────────────────── 数据结构 ──────────────────────────

@dataclass
class ChunkRecord:
    """一个 chunk 的轻量摘要，用于 diff 和 graph 操作。"""
    doc_id: str                # 向量库 doc_id
    chunk_uid: str             # 稳定身份 sec:hash:type:suffix
    raw_text: str
    content_hash: str          # sha1(raw_text) 用于变化检测
    chunk_label: str
    device_type: str
    document_id: str
    metadata: Dict[str, Any]

    @classmethod
    def from_vector_record(cls, record: Dict[str, Any]) -> Optional["ChunkRecord"]:
        meta = record.get("metadata") or {}
        chunk_uid = meta.get("chunk_uid") or ""
        if not chunk_uid:
            return None
        raw_text = meta.get("raw_text") or record.get("text") or ""
        content_hash = hashlib.sha1(raw_text.encode()).hexdigest()[:16]
        return cls(
            doc_id=record.get("doc_id", ""),
            chunk_uid=chunk_uid,
            raw_text=raw_text,
            content_hash=content_hash,
            chunk_label=meta.get("chunk_label") or meta.get("chunk_type") or "general",
            device_type=meta.get("device_type") or "",
            document_id=meta.get("document_id") or "",
            metadata=meta,
        )


@dataclass
class DiffResult:
    deleted: List[ChunkRecord] = field(default_factory=list)
    modified: List[tuple]      = field(default_factory=list)  # (old: ChunkRecord, new: ChunkRecord)
    added: List[ChunkRecord]   = field(default_factory=list)


@dataclass
class SyncSummary:
    deleted_count: int = 0
    deprecated_count: int = 0       # 实际标为 deprecated 的节点数
    modified_count: int = 0
    modified_replaced: int = 0      # REPLACE 判定后更新的节点数
    modified_supplemented: int = 0
    added_count: int = 0
    added_created: int = 0          # 新建图谱节点数
    added_enriched: int = 0         # SUPPLEMENT：补边/补属性
    review_queue: List[Dict] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


# ────────────────────────── 主服务 ──────────────────────────

class ManualUpgradeSync:
    """手册版本升级 KG 同步服务"""

    # 向量候选阈值：task/experience 来源的节点要求更高分才考虑
    _VECTOR_SCORE_THRESHOLD = 0.65
    _TASK_NODE_SCORE_THRESHOLD = 0.85
    _TOP_K = 8

    def __init__(self):
        self.settings = get_settings()
        self.vector_svc = get_vector_service()
        self.text_emb = get_text_embedding()
        self.llm = get_llm_service()
        self.expiration_svc = ExpirationService()
        self._base_url = self.settings.java_service_url
        self._token = self.settings.internal_token

    # ══════════════════════════════════════════════════════════
    #  公开入口
    # ══════════════════════════════════════════════════════════

    async def sync(
        self,
        old_document_id: str,
        new_document_id: str,
        device_type: str = "",
        manual_id: Optional[int] = None,
    ) -> SyncSummary:
        """
        主入口：对比两个版本的 chunk，同步图谱。

        Args:
            old_document_id: 旧版文档 ID（向量库 document_id）
            new_document_id: 新版文档 ID
            device_type: 设备类型（用于候选过滤）
            manual_id: 手册 ID（记录来源用）

        Returns:
            SyncSummary，包含每类操作的统计和 review_queue
        """
        logger.info(
            "[手册升级同步] 开始: old=%s new=%s device=%s",
            old_document_id, new_document_id, device_type,
        )
        summary = SyncSummary()

        try:
            diff = self._compute_diff(old_document_id, new_document_id)
            effective_device_type = device_type or next(
                (
                    c.device_type
                    for c in [
                        *diff.deleted,
                        *diff.added,
                        *[pair[0] for pair in diff.modified],
                        *[pair[1] for pair in diff.modified],
                    ]
                    if c.device_type
                ),
                "",
            )
            if not effective_device_type:
                summary.errors.append("缺少设备作用域，跳过手册升级图谱同步")
                return summary
            logger.info(
                "[手册升级同步] diff: DELETED=%d MODIFIED=%d ADDED=%d",
                len(diff.deleted), len(diff.modified), len(diff.added),
            )

            if diff.deleted:
                await self._handle_deleted(diff.deleted, summary, effective_device_type)
            if diff.modified:
                await self._handle_modified(diff.modified, summary, effective_device_type, manual_id)
            if diff.added:
                await self._handle_added(diff.added, summary, effective_device_type, manual_id)

        except Exception as e:
            logger.error("[手册升级同步] 异常: %s", e, exc_info=True)
            summary.errors.append(str(e))

        logger.info(
            "[手册升级同步] 完成: deprecated=%d replaced=%d supplemented=%d created=%d enriched=%d review=%d",
            summary.deprecated_count,
            summary.modified_replaced,
            summary.modified_supplemented,
            summary.added_created,
            summary.added_enriched,
            len(summary.review_queue),
        )
        return summary

    # ══════════════════════════════════════════════════════════
    #  第一步：计算 diff
    # ══════════════════════════════════════════════════════════

    def _compute_diff(self, old_document_id: str, new_document_id: str) -> DiffResult:
        """
        从向量库取两版本全量 chunk，按 chunk_uid 做 diff。

        chunk_uid 基于 section_title hash，跨版本稳定；
        content_hash 基于 raw_text sha1，用于检测内容变化。
        """
        old_records = self.vector_svc.list_document_chunks(old_document_id)
        new_records = self.vector_svc.list_document_chunks(new_document_id)

        old_map: Dict[str, ChunkRecord] = {}
        for r in old_records:
            cr = ChunkRecord.from_vector_record(r)
            if cr:
                old_map[cr.chunk_uid] = cr

        new_map: Dict[str, ChunkRecord] = {}
        for r in new_records:
            cr = ChunkRecord.from_vector_record(r)
            if cr:
                new_map[cr.chunk_uid] = cr

        diff = DiffResult()
        for uid, old_cr in old_map.items():
            if uid not in new_map:
                diff.deleted.append(old_cr)
            elif new_map[uid].content_hash != old_cr.content_hash:
                diff.modified.append((old_cr, new_map[uid]))

        for uid, new_cr in new_map.items():
            if uid not in old_map:
                diff.added.append(new_cr)

        return diff

    # ══════════════════════════════════════════════════════════
    #  DELETED
    # ══════════════════════════════════════════════════════════

    async def _handle_deleted(
        self,
        chunks: List[ChunkRecord],
        summary: SyncSummary,
        device_type: str,
    ) -> None:
        """
        删除的 chunk：找到来源于此 chunk 的图谱节点，统一进入人工审核队列。

        所有删除候选先进入审核队列，审核通过后才标 deprecated。
        """
        summary.deleted_count += len(chunks)

        for chunk in chunks:
            try:
                nodes = await self._find_nodes_by_chunk(chunk, device_type)
                if not nodes:
                    continue

                for node in nodes:
                    node_id = node.get("id", "")
                    summary.review_queue.append({
                        "trigger_type": "manual_deleted_chunk",
                        "device_name": device_type,
                        "verdict": "REPLACE",
                        "confidence": 0.1,
                        "reason": "chunk 已在新版手册中删除，建议人工确认是否废弃旧方案",
                        "candidate_id": node_id,
                        "candidate_solution_title": node.get("title") or "",
                        "candidate_solution_summary": node.get("description") or "",
                        "new_solution_title": "新版手册已删除该内容",
                        "new_solution_summary": f"chunk_uid={chunk.chunk_uid}",
                    })

            except Exception as e:
                logger.warning("[手册升级] DELETED chunk 处理失败: chunk=%s err=%s", chunk.chunk_uid, e)
                summary.errors.append(f"DELETED {chunk.chunk_uid}: {e}")

    # ══════════════════════════════════════════════════════════
    #  MODIFIED
    # ══════════════════════════════════════════════════════════

    async def _handle_modified(
        self,
        pairs: List[tuple],
        summary: SyncSummary,
        device_type: str,
        manual_id: Optional[int],
    ) -> None:
        """
        修改的 chunk（同 chunk_uid，内容变了）：

        1. 找到旧 chunk 对应的图谱节点（向量搜 + LLM 判定）
        2. REPLACE + confidence ≥ 0.7：更新节点 manual 来源字段（保留 task 字段）
        3. SUPPLEMENT：补充边/属性
        4. 低置信度 → 进 review_queue
        """
        summary.modified_count += len(pairs)

        for old_cr, new_cr in pairs:
            try:
                # 通过旧 chunk 的来源字段精确找到旧节点，不能用新文本向量结果冒充 Neo4j ID。
                old_nodes = await self._find_nodes_by_chunk(old_cr, device_type)
                candidates = [
                    {
                        "id": node.get("id", ""),
                        "fault_name": "",
                        "fault_description": "",
                        "solution_title": node.get("title", ""),
                        "solution_summary": node.get("description", ""),
                        "source": "manual",
                        "_source": "neo4j_source_chunk",
                    }
                    for node in old_nodes if node.get("id")
                ]
                if not candidates:
                    continue

                # LLM 判定新旧内容对比
                new_item = {
                    "device_name": device_type,
                    "fault_name": "",
                    "fault_description": new_cr.raw_text[:400],
                    "solution_title": "",
                    "solution_summary": new_cr.raw_text[:400],
                    "_context": "手册版本更新，此 chunk 内容已修改，新版内容为左侧。",
                }
                verdicts = await self.expiration_svc._llm_batch_judge(
                    new_item, candidates,
                    context="手册内容已更新，请判断新版本内容与旧图谱节点的关系。",
                )

                for v in verdicts:
                    node_id = v.get("candidate_id", "")
                    verdict = v.get("verdict", "UNRELATED")
                    confidence = v.get("confidence", 0.0)

                    if verdict == "REPLACE" and confidence >= 0.7:
                        # 更新节点 manual 字段
                        await self._call_java(
                            "/weixiu/expiration/internal/update-manual-fields",
                            {
                                "nodeId": node_id,
                                "newContent": new_cr.raw_text[:800],
                                "sourceChunkUid": new_cr.chunk_uid,
                                "sourceContentHash": new_cr.content_hash,
                                "reason": v.get("reason", ""),
                            },
                        )
                        summary.modified_replaced += 1

                    elif verdict == "SUPPLEMENT" and confidence >= 0.7:
                        # 补充关系：在原节点上加 SUPPLEMENTED_BY 边到新版 chunk 记录
                        await self._call_java(
                            "/weixiu/expiration/internal/add-supplement-edge",
                            {
                                "fromNodeId": node_id,
                                "sourceChunkUid": new_cr.chunk_uid,
                                "label": "SUPPLEMENTED_BY_MANUAL",
                                "note": new_cr.raw_text[:400],
                                "reason": v.get("reason", ""),
                            },
                        )
                        summary.modified_supplemented += 1

                    elif verdict == "REPLACE" and (v.get("need_review") or confidence < 0.7):
                        summary.review_queue.append({
                            "trigger_type": "manual_modified_chunk",
                            "device_name": device_type,
                            **v,
                            "chunk_uid": new_cr.chunk_uid,
                            "new_content_preview": new_cr.raw_text[:200],
                            "new_solution_title": new_cr.raw_text[:80].replace("\n", " "),
                            "new_solution_summary": new_cr.raw_text[:600],
                        })

            except Exception as e:
                logger.warning("[手册升级] MODIFIED chunk 处理失败: chunk=%s err=%s", new_cr.chunk_uid, e)
                summary.errors.append(f"MODIFIED {new_cr.chunk_uid}: {e}")

    # ══════════════════════════════════════════════════════════
    #  ADDED
    # ══════════════════════════════════════════════════════════

    async def _handle_added(
        self,
        chunks: List[ChunkRecord],
        summary: SyncSummary,
        device_type: str,
        manual_id: Optional[int],
    ) -> None:
        """
        新增 chunk：向量搜现有图谱节点 → LLM 判定 → SUPPLEMENT / 进审核队列。

        新增内容由后续 manual KG 抽取创建并挂接；这里仅处理它与既有 Solution 的关系。
        SUPPLEMENT → 给既有节点追加补充内容。
        REPLACE → 进入审核队列，审核通过后才废弃旧节点。
        """
        summary.added_count += len(chunks)

        for chunk in chunks:
            try:
                candidates = await self._vector_candidates_for_chunk(chunk, device_type)

                if not candidates:
                    # 无候选时交给后续 manual KG 抽取，避免创建无 Fault 关系的孤立 Solution。
                    continue

                # LLM 判定
                new_item = {
                    "device_name": device_type,
                    "fault_name": "",
                    "fault_description": chunk.raw_text[:400],
                    "solution_title": "",
                    "solution_summary": chunk.raw_text[:400],
                    "_context": "手册新版本新增内容，请判断与现有图谱节点的关系。",
                }
                verdicts = await self.expiration_svc._llm_batch_judge(
                    new_item, candidates,
                    context="新增手册章节内容，判断与已有图谱知识的关系。",
                )

                # 只取最高置信度的非 UNRELATED 判决
                best_verdict = _pick_best_verdict(verdicts)

                if best_verdict is None:
                    # 全部无关，不需要修改旧图谱；新版节点由 KG 抽取负责创建。
                    continue

                elif best_verdict["verdict"] == "SUPPLEMENT" and best_verdict.get("confidence", 0) >= 0.7:
                    # ENRICH：在现有节点上补充
                    node_id = best_verdict.get("candidate_id", "")
                    await self._call_java(
                        "/weixiu/expiration/internal/add-supplement-edge",
                        {
                            "fromNodeId": node_id,
                            "sourceChunkUid": chunk.chunk_uid,
                            "label": "SUPPLEMENTED_BY_MANUAL",
                            "note": chunk.raw_text[:400],
                            "reason": best_verdict.get("reason", ""),
                        },
                    )
                    summary.added_enriched += 1

                elif best_verdict["verdict"] == "REPLACE":
                    # 无论置信度如何，先让人工确认；不能在审核前修改旧节点状态。
                    summary.review_queue.append({
                        "trigger_type": "manual_added_chunk_replace",
                        "device_name": device_type,
                        **best_verdict,
                        "chunk_uid": chunk.chunk_uid,
                        "new_solution_title": chunk.raw_text[:80].replace("\n", " "),
                        "new_solution_summary": chunk.raw_text[:600],
                    })

                else:
                    # SUPPLEMENT 低置信度或其他结果不改变旧知识，等待后续抽取。
                    continue

            except Exception as e:
                logger.warning("[手册升级] ADDED chunk 处理失败: chunk=%s err=%s", chunk.chunk_uid, e)
                summary.errors.append(f"ADDED {chunk.chunk_uid}: {e}")

    # ══════════════════════════════════════════════════════════
    #  辅助：向量召回 + 候选过滤
    # ══════════════════════════════════════════════════════════

    async def _vector_candidates_for_chunk(
        self,
        chunk: ChunkRecord,
        device_type: str,
    ) -> List[Dict[str, Any]]:
        """
        对一个 chunk 做向量召回，返回符合阈值的图谱候选节点。

        过滤规则：
        - 向量分数 ≥ 0.65（通用阈值）
        - task/experience 来源的节点要求 ≥ 0.85（保护实战经验）
        - 最多返回 5 个候选送 LLM
        """
        query_text = chunk.raw_text[:1000]
        if not query_text.strip():
            return []

        try:
            vector = await self.text_emb.embed(query_text)
            if not device_type:
                return []
            vector_filter = build_redis_filter(
                device_type=device_type,
                record_type="manual",
                status="ready",
            )
            results = self.vector_svc.search(
                vector,
                top_k=self._TOP_K,
                include_metadata=True,
                filter=vector_filter,
            )
        except Exception as e:
            logger.warning("[手册升级] 向量召回失败: %s", e)
            return []

        candidates = []
        seen_node_ids = set()
        for r in results:
            relevance = r.get("relevance_score", 0.0)
            if relevance < self._VECTOR_SCORE_THRESHOLD:
                continue

            meta = r.get("metadata") or {}
            source = meta.get("source") or meta.get("record_type") or ""
            is_task_source = source in ("task", "experience", "fact")

            # 任务/经验来源的节点要求更高的分才考虑
            if is_task_source and relevance < self._TASK_NODE_SCORE_THRESHOLD:
                continue

            chunk_uid = meta.get("chunk_uid") or ""
            document_id = meta.get("document_id") or chunk.document_id
            if not chunk_uid or not document_id:
                continue
            try:
                nodes = await self._find_nodes_by_chunk(
                    ChunkRecord(
                        doc_id=r.get("doc_id", ""),
                        chunk_uid=chunk_uid,
                        raw_text=meta.get("raw_text") or r.get("text") or "",
                        content_hash="",
                        chunk_label=meta.get("chunk_label") or "general",
                        device_type=meta.get("device_type") or device_type,
                        document_id=document_id,
                        metadata=meta,
                    ),
                    device_type,
                )
            except Exception as e:
                logger.debug("[手册升级] 向量候选映射失败: %s", e)
                continue
            for node in nodes:
                node_id = str(node.get("id") or "")
                if not node_id or node_id in seen_node_ids:
                    continue
                seen_node_ids.add(node_id)
                candidates.append({
                    "id": node_id,
                    "text": node.get("description") or r.get("text", ""),
                    "score": relevance,
                    "fault_name": "",
                    "fault_description": "",
                    "solution_title": node.get("title") or "",
                    "solution_summary": node.get("description") or r.get("text", "")[:300],
                    "created_at": meta.get("created_at"),
                    "source": source,
                    "_source": "vector->neo4j",
                })

        # 按相关度排序，最多送 5 个
        candidates.sort(key=lambda c: c.get("score", 0), reverse=True)
        return candidates[:5]

    async def _find_nodes_by_chunk(
        self,
        chunk: ChunkRecord,
        device_type: str,
    ) -> List[Dict[str, Any]]:
        """
        找到来源于某个 chunk 的图谱节点。

        只通过 Java 端按 chunk + document 作用域精确匹配。
        不再把 Redis doc_id 当作 Neo4j 节点 ID。
        """
        # 精确查询（如果 Neo4j 节点存了 source_chunk_uid）
        try:
            resp = await self._call_java(
                "/weixiu/expiration/internal/nodes-by-chunk-uid",
                {
                    "chunkUid": chunk.chunk_uid,
                    "documentId": chunk.document_id,
                    "deviceType": device_type,
                },
            )
            nodes = resp if isinstance(resp, list) else []
            return nodes
        except Exception as e:
            logger.debug("[手册升级] 精确查 chunk 节点失败: %s", e)
            return []

    # ══════════════════════════════════════════════════════════
    #  辅助：新建图谱节点
    # ══════════════════════════════════════════════════════════

    async def _create_solution_node(
        self,
        chunk: ChunkRecord,
        device_type: str,
        manual_id: Optional[int],
    ) -> Optional[str]:
        """在 Neo4j 中创建一个新的 Solution 节点，关联到最相近的 Fault（如果找到）。

        节点带 source_chunk_uid / source_content_hash 属性，方便下次版本升级时精确定位。
        """
        try:
            resp = await self._call_java(
                "/weixiu/expiration/internal/create-solution-node",
                {
                    "title": chunk.raw_text[:80].strip().replace("\n", " "),
                    "description": chunk.raw_text[:600],
                    "deviceType": device_type,
                    "documentId": chunk.document_id,
                    "sourceChunkUid": chunk.chunk_uid,
                    "sourceContentHash": chunk.content_hash,
                    "manualId": manual_id,
                    "chunkLabel": chunk.chunk_label,
                },
            )
            if isinstance(resp, dict):
                return resp.get("nodeId") or resp.get("id")
        except Exception as e:
            logger.warning("[手册升级] 创建节点失败: chunk=%s err=%s", chunk.chunk_uid, e)
        return None

    # ══════════════════════════════════════════════════════════
    #  辅助：调用 Java 内部接口
    # ══════════════════════════════════════════════════════════

    async def _call_java(self, path: str, body: Dict[str, Any]) -> Any:
        headers = {"X-Internal-Token": self._token}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{self._base_url}{path}",
                json=body,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and str(data.get("code")) != "200":
                raise RuntimeError(f"Java internal API business error: {data.get('message', data)}")
            # 兼容 Result<T> 包装
            if isinstance(data, dict) and "data" in data:
                return data["data"]
            return data


# ────────────────────────── 工具函数 ──────────────────────────

def _pick_best_verdict(verdicts: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """从 LLM 批量判决中取最高置信度的非 UNRELATED 结果。"""
    non_unrelated = [v for v in verdicts if v.get("verdict") != "UNRELATED"]
    if not non_unrelated:
        return None
    return max(non_unrelated, key=lambda v: v.get("confidence", 0.0))


# ────────────────────────── 单例 ──────────────────────────

_sync_service: Optional[ManualUpgradeSync] = None


def get_manual_upgrade_sync() -> ManualUpgradeSync:
    global _sync_service
    if _sync_service is None:
        _sync_service = ManualUpgradeSync()
    return _sync_service
