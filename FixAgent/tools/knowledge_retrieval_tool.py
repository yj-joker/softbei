"""Knowledge retrieval tool for multimodal RAG evidence."""

from __future__ import annotations

import logging
import asyncio
import inspect
import re
from typing import Any, Callable, Dict, List, Optional

from embeddings.multimodal_embedding import get_multimodal_embedding
from embeddings.text_embedding import get_text_embedding
from schemas.models import VectorSearchResult
from services.retrieval.planner import build_retrieval_plan, confidence_intent
from services.retrieval.ranker import rank_candidates
from services.retrieval.context_expander import expand_retrieval_context
from services.retrieval.fusion import DEFAULT_RRF_CONSTANT, reciprocal_rank_fusion
from services.retrieval.quality import evaluate_retrieval_quality
from services.retrieval.policy import (
    diversify_candidates,
    summarize_confidence,
)
from services.retrieval.section_index import SectionTitleIndex
from services.knowledge.vector_service import build_redis_filter, escape_redis_tag_value, get_vector_service
from tools.base_tool import BaseTool, ToolException

logger = logging.getLogger(__name__)

DEFAULT_RECALL_TOP_N = 50
IMAGE_LOCATOR_LOOKUP_LIMIT = 20
IMAGE_LOCATOR_PAGE_RE = re.compile(r"(?:\u7b2c\s*)?(\d{1,3})\s*\u9875")


async def _emit_retrieval_event(
    event_sink: Optional[Callable[[Dict[str, Any]], Any]],
    event: str,
    data: Dict[str, Any],
) -> None:
    if not event_sink:
        return
    result = event_sink({"event": event, "data": data})
    if inspect.isawaitable(result):
        await result


def _count_selected_types(items: List[Dict]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in items or []:
        metadata = item.get("metadata") or {}
        chunk_type = metadata.get("chunk_type") or "text"
        if chunk_type == "image_summary":
            chunk_type = "image"
        chunk_type = str(chunk_type or "text")
        counts[chunk_type] = counts.get(chunk_type, 0) + 1
    return counts


class KnowledgeRetrievalTool(BaseTool):
    """Retrieve text, table, and image evidence from the knowledge store."""

    @property
    def name(self) -> str:
        return "knowledge_retrieval"

    @property
    def description(self) -> str:
        return (
            "Retrieve maintenance knowledge evidence from text, table, and image records. "
            "Use it for fault causes, repair steps, parameters, diagrams, and image evidence."
        )

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "User retrieval query."},
                "top_k": {"type": "integer", "default": 5, "description": "Final evidence count."},
                "category": {"type": "string", "description": "Existing category filter."},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Existing tag filter."},
                "document_id": {"type": "string", "description": "Restrict retrieval to one imported document."},
                "chunk_type": {"type": "string", "description": "text/table/image/image_summary filter."},
                "device_type": {"type": "string", "description": "Device type metadata filter."},
                "document_version": {"type": "string", "description": "Document version metadata filter."},
                "manual_type": {"type": "string", "description": "Manual type metadata filter."},
                "image_urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional user images for multimodal query embedding.",
                },
            },
            "required": ["query"],
        }

    @staticmethod
    def _build_filter(
        category: str = None,
        tags: List[str] = None,
        document_id: str = None,
        chunk_type: str = None,
        device_type: str = None,
        document_version: str = None,
        manual_type: str = None,
        record_type: str = None,
        status: str = None,
        chunk_label: str = None,
    ) -> Optional[str]:
        if not any((document_id, chunk_type, device_type, document_version, manual_type, record_type, status, chunk_label)):
            parts = []
            if category:
                parts.append(f"@category:{{{escape_redis_tag_value(category)}}}")
            if tags:
                parts.append(f"@tags:{{{'|'.join(escape_redis_tag_value(tag) for tag in tags)}}}")
            if not parts:
                return None
            return parts[0] if len(parts) == 1 else f"({' '.join(parts)})"
        return build_redis_filter(
            category=category,
            tags=tags,
            document_id=document_id,
            chunk_type=chunk_type,
            device_type=device_type,
            document_version=document_version,
            manual_type=manual_type,
            record_type=record_type,
            status=status,
            chunk_label=chunk_label,
        )

    @staticmethod
    def _canonical_id(doc: Dict) -> str:
        doc_id = doc.get("doc_id", "")
        metadata = doc.get("metadata") or {}
        if metadata.get("source_chunk_id"):
            return metadata["source_chunk_id"]
        if metadata.get("source_image_id"):
            return metadata["source_image_id"]
        if metadata.get("chunk_type") == "image_summary":
            return doc_id.replace(":ims:", ":img:")
        return doc_id

    @staticmethod
    def _is_step(item: Dict) -> bool:
        metadata = item.get("metadata") or {}
        return metadata.get("chunk_label") == "step" or metadata.get("chunk_type") == "step_raw"

    @staticmethod
    def _section_key(item: Dict) -> str:
        return (item.get("metadata") or {}).get("toc_path") or ""

    @classmethod
    def _promote_section_siblings(
        cls,
        ranked: List[Dict],
        selected: List[Dict],
        top_k: int,
        freeze_head: int = 3,
        max_promote: int = 2,
    ) -> List[Dict]:
        """同节救援（冻结表头）：仅 procedure 型查询，把漏在 top_k 之外、与表头步骤
        同一目录叶子节的步骤块，提进 top_k 的尾部槽位；表头(前 freeze_head 名)原样保留，
        因此 R@1/R@3 由构造保证不变，只可能影响 R@5 这类尾部召回。
        """
        if top_k <= freeze_head or len(selected) < top_k:
            return selected
        head = selected[:freeze_head]
        if not any(cls._is_step(it) for it in head):
            return selected
        anchor = {cls._section_key(it) for it in head if cls._is_step(it) and cls._section_key(it)}
        if not anchor:
            return selected
        selected_ids = {it.get("doc_id") for it in selected}
        rescued = [
            it for it in ranked
            if it.get("doc_id") not in selected_ids
            and cls._is_step(it)
            and cls._section_key(it) in anchor
        ][:max_promote]
        if not rescued:
            return selected
        tail = selected[freeze_head:]
        tail_sib = [it for it in tail if cls._section_key(it) in anchor]
        tail_non = [it for it in tail if cls._section_key(it) not in anchor]
        new_tail = (tail_sib + rescued + tail_non)[: top_k - freeze_head]
        return head + new_tail

    @classmethod
    def _ensure_section_image(
        cls,
        ranked: List[Dict],
        selected: List[Dict],
        top_k: int,
        plan,
        freeze_head: int = 3,
    ) -> List[Dict]:
        """图片规整（仅 procedure）：只保留主节（表头步骤所在节）的配图，
        砍掉其他节的图（治"拆发动机"带回一堆无关配图）。主节有多少张留多少张，
        不设数量上限——前文 E 已按 query 核心词收口，不会溢出。
        没图就从召回池补 1 张主节图（兜底）。"""
        if plan.intent != "procedure":
            return selected
        if not selected or top_k <= 0:
            return selected

        def section_id(item: Dict) -> str:
            return (item.get("metadata") or {}).get("parent_section_id") or ""

        head = selected[:freeze_head]
        anchor = {section_id(it) for it in head if cls._is_step(it) and section_id(it)}
        if not anchor:
            anchor = {section_id(it) for it in selected if cls._is_step(it) and section_id(it)}
        if not anchor:
            return selected

        # 分类：主节图 / 无关节图 / 非图候选
        main_images = [it for it in selected if cls._is_image_record(it) and section_id(it) in anchor]
        foreign_images = [it for it in selected if cls._is_image_record(it) and section_id(it) not in anchor]
        non_images = [it for it in selected if not cls._is_image_record(it)]

        if not main_images:
            # 主节没有图 → 从召回池补 1 张
            fallback = next(
                (it for it in ranked if cls._is_image_record(it) and section_id(it) in anchor),
                None,
            )
            if fallback is not None:
                main_images = [fallback]

        if not main_images and not foreign_images:
            return selected

        # 重建：非图候选 + 主节全量图，无关节图丢弃
        rebuilt = non_images + main_images
        # 给非图候选留空间（至少留 2 个文本位），但允许多余图溢出 top_k
        # （与 _ensure_section_steps 一致：证据优先，不硬砍）
        return rebuilt

    @classmethod
    def _ensure_section_steps(
        cls,
        selected: List[Dict],
        plan,
        vector_service,
        max_steps: int = 10,
    ) -> List[Dict]:
        """C 按节取全：procedure/parameter 意图下，把主节（selected 里占比最高的
        parent_section_id）的完整正文块从库里直接补齐——procedure 补 step_raw 步骤块、
        parameter 补 table 数值表块——按 source_index 排序，避免正文没进 top_k 导致模型
        漏步/编造（如涨紧器只回“参照气缸头”、气门间隙说“证据里没数值”）。允许超出 top_k。"""
        intent_chunk_type = {"procedure": "step_raw", "parameter": "table"}.get(plan.intent)
        if intent_chunk_type is None:
            return selected
        if not selected or not hasattr(vector_service, "get_section_records"):
            return selected

        counter: Dict[tuple, int] = {}
        score_by_sec: Dict[tuple, float] = {}
        for it in selected:
            meta = it.get("metadata") or {}
            key = (str(meta.get("document_id") or ""), str(meta.get("parent_section_id") or ""))
            if not all(key):
                continue
            counter[key] = counter.get(key, 0) + 1
            sc = float(it.get("relevance_score") or it.get("score") or 0.0)
            score_by_sec[key] = max(score_by_sec.get(key, 0.0), sc)
        if not counter:
            return selected
        document_id, parent_section_id = max(
            counter, key=lambda k: (counter[k], score_by_sec.get(k, 0.0))
        )

        try:
            records = vector_service.get_section_records(
                document_id, parent_section_id, limit=30, chunk_type=intent_chunk_type
            )
        except Exception as exc:
            logger.warning("[ensure_section_steps] 取节步骤失败: %s", exc)
            return selected
        if not records:
            return selected

        have_ids: set = set()
        have_text: set = set()
        for it in selected:
            meta = it.get("metadata") or {}
            have_ids.add(str(it.get("doc_id") or it.get("id") or ""))
            if meta.get("source_chunk_id"):
                have_ids.add(str(meta["source_chunk_id"]))
            rt = (meta.get("raw_text") or it.get("content") or it.get("text") or "").strip()
            if rt:
                have_text.add(rt)

        new_steps: List[Dict] = []
        for rec in records:
            rec = rec.model_dump() if hasattr(rec, "model_dump") else dict(rec)
            meta = dict(rec.get("metadata") or {})
            rid = str(rec.get("doc_id") or rec.get("id") or "")
            src = str(meta.get("source_chunk_id") or "")
            text = (rec.get("text") or rec.get("content") or meta.get("raw_text") or "").strip()
            if not text:
                continue
            if rid in have_ids or (src and src in have_ids) or text in have_text:
                continue
            meta["context_role"] = "section_step" if plan.intent == "procedure" else "section_param"
            new_steps.append({"doc_id": rid, "id": rid, "text": text, "content": text, "metadata": meta})
            have_text.add(text)

        if not new_steps:
            return selected

        def _order(rec: Dict) -> int:
            meta = rec.get("metadata") or {}
            try:
                return int(meta.get("source_index"))
            except (TypeError, ValueError):
                return 9999

        new_steps.sort(key=_order)
        return selected + new_steps[:max_steps]

    @staticmethod
    def _mark_route(doc: Dict, route: str) -> Dict:
        item = dict(doc)
        item["metadata"] = dict(item.get("metadata") or {})
        if item.get("relevance_score") is None and item.get("score") is not None:
            item["relevance_score"] = item["score"]
        item["routes"] = sorted(set(item.get("routes") or []) | {route})
        item["retrieval_route"] = route
        return item

    @classmethod
    def _merge_candidates(cls, candidates: List[Dict]) -> List[Dict]:
        merged: Dict[str, Dict] = {}
        for candidate in candidates:
            key = cls._canonical_id(candidate)
            if key not in merged:
                item = dict(candidate)
                item["doc_id"] = key
                merged[key] = item
                continue
            current = merged[key]
            routes = sorted(set(current.get("routes") or []) | set(candidate.get("routes") or []))
            if candidate.get("relevance_score", 0.0) > current.get("relevance_score", 0.0):
                current.update(candidate)
                current["doc_id"] = key
            current["routes"] = routes
            current_meta = current.setdefault("metadata", {})
            current_meta.update(
                {name: value for name, value in (candidate.get("metadata") or {}).items() if value not in ("", None)}
            )
        return list(merged.values())

    @staticmethod
    def _is_outline_candidate(candidate: Dict) -> bool:
        metadata = candidate.get("metadata") or {}
        return metadata.get("chunk_type") == "outline" or metadata.get("chunk_label") == "outline"

    @classmethod
    def _filter_candidates_for_plan(cls, candidates: List[Dict], plan) -> List[Dict]:
        if plan.intent == "outline":
            outline_candidates = [candidate for candidate in candidates if cls._is_outline_candidate(candidate)]
            return outline_candidates or list(candidates)
        return [candidate for candidate in candidates if not cls._is_outline_candidate(candidate)]

    @staticmethod
    def _extract_query_pages(query: str) -> List[int]:
        pages: List[int] = []
        for match in IMAGE_LOCATOR_PAGE_RE.finditer(query or ""):
            try:
                page = int(match.group(1))
            except (TypeError, ValueError):
                continue
            if page > 0 and page not in pages:
                pages.append(page)
        return pages

    @staticmethod
    def _metadata_page(metadata: Dict[str, Any]) -> Optional[int]:
        value = metadata.get("page")
        if value is None:
            value = metadata.get("page_num")
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    # —— E 查图相关性收口（image_identification）——
    # 用图自带的 章节名/图注/VLM摘要 判相关；判相关不靠重排名次，只看字面命中。
    _IMG_QUERY_STOPWORDS = (
        "帮我", "帮", "请", "给我", "给", "查一下", "查查", "查询", "查找", "查", "我",
        "看一下", "看看", "看", "找一下", "找", "想", "了解", "知道", "显示", "展示",
        "搜索", "搜", "提供", "介绍", "关于", "一下", "结构", "构造", "内部", "外形",
        "图片", "图纸", "图示", "示意图", "照片", "图像", "插图", "图", "相关", "的",
        "这个", "那个", "是什么", "有哪些", "哪些", "一张", "几张", "张", "和", "与", "及", "以及",
    )

    @classmethod
    def _query_core_terms(cls, query: str) -> List[str]:
        """从查图问题里抽出核心实体词（去掉“帮我查”“结构”“图片”等通用词）。"""
        s = query or ""
        for w in cls._IMG_QUERY_STOPWORDS:
            s = s.replace(w, " ")
        return [t for t in re.split(r"[^一-鿿A-Za-z0-9]+", s) if len(t) >= 2]

    @classmethod
    def _filter_query_images(cls, ranked, selected, top_k, plan, query):
        """E：查图意图（image_identification）下按相关性收口图片。

        判据=图的 章节名/toc/图注/正文 + 关联 VLM 摘要 是否含问题核心词（字面命中，
        不靠重排名次）。CRAG 式分级：相关图≥2 张才砍掉无关图；<2 张则原样全留——
        宁可多给几张无关图（用户一眼略过），也绝不把目标图误删。"""
        if getattr(plan, "intent", "") != "image_identification":
            return selected
        terms = cls._query_core_terms(query) if selected else []
        if not terms:
            return selected  # 抽不出核心词 → 不动（兜底）

        images = [it for it in selected if cls._is_image_record(it)]
        if len(images) <= 1:
            return selected  # 本就没几张，不折腾

        # 每张图关联的 VLM 摘要文字（image_summary 与图同 canonical_id）
        summary_by_id: Dict[str, str] = {}
        for it in ranked:
            if (it.get("metadata") or {}).get("chunk_type") == "image_summary":
                key = cls._canonical_id(it)
                summary_by_id[key] = summary_by_id.get(key, "") + " " + str(it.get("content") or it.get("text") or "")

        def is_relevant(img: Dict) -> bool:
            meta = img.get("metadata") or {}
            text = " ".join([
                str(meta.get("section_title") or ""),
                str(meta.get("toc_path") or ""),
                str(meta.get("caption") or ""),
                str(img.get("content") or img.get("text") or ""),
                summary_by_id.get(cls._canonical_id(img), ""),
            ])
            return any(term in text for term in terms)

        relevant_ids = {cls._canonical_id(im) for im in images if is_relevant(im)}
        if len(relevant_ids) < 2:
            return selected  # 相关不足 → 全留兜底，绝不误删目标

        # 够目标 → 只留相关图，无关图砍掉；非图候选原样保留
        return [
            it for it in selected
            if (not cls._is_image_record(it)) or (cls._canonical_id(it) in relevant_ids)
        ]

    @staticmethod
    def _is_image_record(record: Dict[str, Any]) -> bool:
        metadata = record.get("metadata") or {}
        return metadata.get("chunk_type") in {"image", "image_summary"}

    @staticmethod
    def _record_order(record: Dict[str, Any]) -> tuple[int, int, str]:
        metadata = record.get("metadata") or {}
        source_index = metadata.get("source_index")
        if source_index is None:
            source_index = metadata.get("image_index")
        try:
            index = int(source_index)
        except (TypeError, ValueError):
            index = 9999
        page = KnowledgeRetrievalTool._metadata_page(metadata) or 9999
        return page, index, str(record.get("doc_id") or record.get("id") or "")

    @classmethod
    def _mark_image_locator_candidate(
        cls,
        record: Dict[str, Any],
        score: float,
        reasons: List[str],
    ) -> Dict[str, Any]:
        item = dict(record)
        item["doc_id"] = cls._canonical_id(item)
        item["score"] = max(float(item.get("score") or 0.0), score)
        item["relevance_score"] = max(float(item.get("relevance_score") or 0.0), score)
        item["raw_score_type"] = "image_locator"
        metadata = dict(item.get("metadata") or {})
        metadata["image_locator_used"] = True
        metadata["image_locator_reasons"] = reasons
        item["metadata"] = metadata
        item["routes"] = sorted(set(item.get("routes") or []) | {"image_locator"})
        item["retrieval_route"] = "image_locator"
        return item

    @classmethod
    def _locate_image_candidates(
        cls,
        query: str,
        ranked_candidates: List[Dict],
        vector_service: Any,
        plan,
        document_id: str = None,
        limit: int = 5,
    ) -> List[Dict]:
        # 放行 procedure（装配/检修流程）：让步骤类问题也能主动按章节把配图捞出来，
        # 不再只服务"图像识别"意图——否则装配问题的配图全靠普通召回碰运气
        if plan.intent not in {"image_identification", "procedure"}:
            return []
        if not vector_service:
            return []

        pages = cls._extract_query_pages(query)
        seed_candidates = list(ranked_candidates or [])[: max(limit * 4, 20)]
        document_ids: List[str] = []
        section_keys: List[tuple[str, str]] = []
        for candidate in seed_candidates:
            metadata = candidate.get("metadata") or {}
            candidate_doc_id = metadata.get("document_id") or document_id
            if candidate_doc_id and candidate_doc_id not in document_ids:
                document_ids.append(candidate_doc_id)
            parent_section_id = metadata.get("parent_section_id")
            if candidate_doc_id and parent_section_id:
                section_key = (str(candidate_doc_id), str(parent_section_id))
                if section_key not in section_keys:
                    section_keys.append(section_key)
        if document_id and document_id not in document_ids:
            document_ids.insert(0, document_id)

        located: Dict[str, Dict[str, Any]] = {}

        def add_records(records: List[Dict], base_score: float, reason: str) -> None:
            for record in sorted(records or [], key=cls._record_order):
                if not cls._is_image_record(record):
                    continue
                metadata = record.get("metadata") or {}
                score = base_score
                reasons = [reason]
                record_page = cls._metadata_page(metadata)
                if pages and record_page in pages:
                    score += 0.08
                    reasons.append("page_match")
                section_key = (str(metadata.get("document_id") or ""), str(metadata.get("parent_section_id") or ""))
                if section_key in section_keys:
                    score += 0.04
                    reasons.append("section_match")
                if metadata.get("chunk_type") == "image":
                    score += 0.03
                marked = cls._mark_image_locator_candidate(record, min(score, 0.98), reasons)
                key = cls._canonical_id(marked)
                current = located.get(key)
                if not current or marked.get("relevance_score", 0.0) > current.get("relevance_score", 0.0):
                    located[key] = marked

        if pages and hasattr(vector_service, "get_page_records"):
            for doc_id in document_ids:
                for page in pages:
                    records = vector_service.get_page_records(
                        doc_id,
                        page,
                        chunk_type="image",
                        limit=IMAGE_LOCATOR_LOOKUP_LIMIT,
                    )
                    add_records(records, 0.84, "explicit_page")

        # procedure 意图即使 query 没写页码，也按"召回到的步骤块所在章节"把同节配图捞出来
        if (pages or plan.intent == "procedure") and hasattr(vector_service, "get_section_records"):
            for doc_id, parent_section_id in section_keys[: max(limit * 2, 10)]:
                records = vector_service.get_section_records(
                    doc_id,
                    parent_section_id,
                    limit=IMAGE_LOCATOR_LOOKUP_LIMIT,
                    chunk_type="image",
                )
                add_records(records, 0.78, "same_section")

        return sorted(located.values(), key=lambda item: item.get("relevance_score", 0.0), reverse=True)[:limit]

    @staticmethod
    def _average_vectors(vectors: List[List[float]]) -> Optional[List[float]]:
        valid_vectors = [vector for vector in vectors if vector]
        if not valid_vectors:
            return None
        return [sum(values) / len(valid_vectors) for values in zip(*valid_vectors)]

    async def _embed_query_vectors(self, query: str, image_urls: List[str] = None) -> Dict[str, List[float] | List[List[float]]]:
        try:
            if image_urls:
                result = await get_multimodal_embedding().embed(text=query, image_urls=image_urls)
                text_vector = result.get("text_vector")
                image_vectors = result.get("image_vectors", [])
                if not text_vector and not image_vectors:
                    raise ToolException(code="EMBEDDING_FAILED", message="multimodal embedding returned no vectors")
                return {
                    "text_vector": text_vector,
                    "image_vectors": image_vectors,
                    "image_vector": self._average_vectors(image_vectors),
                }
            return {"text_vector": await get_text_embedding().embed(query), "image_vectors": [], "image_vector": None}
        except ToolException:
            raise
        except Exception as e:
            raise ToolException(code="EMBEDDING_FAILED", message=f"embedding failed: {e}")

    async def _embed_query(self, query: str, image_urls: List[str] = None) -> List[float]:
        vectors = await self._embed_query_vectors(query, image_urls)
        text_vector = vectors.get("text_vector")
        image_vector = vectors.get("image_vector")
        if image_urls:
            fused = self._average_vectors([vector for vector in (text_vector, image_vector) if vector])
            if fused:
                return fused
        if text_vector:
            return text_vector
        if image_vector:
            return image_vector
        raise ToolException(code="EMBEDDING_FAILED", message="embedding returned no vectors")

    @staticmethod
    def _route_name(route: str, relaxed: bool = False) -> str:
        if route == "text":
            route_name = "text_vector"
        else:
            route_name = "table_vector" if route == "table" else route
        return f"{route_name}_relaxed" if relaxed else route_name

    async def _execute(
        self,
        query: str,
        top_k: int = 5,
        category: str = None,
        tags: List[str] = None,
        image_urls: List[str] = None,
        document_id: str = None,
        chunk_type: str = None,
        device_type: str = None,
        document_version: str = None,
        manual_type: str = None,
        _event_sink: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> List[VectorSearchResult]:
        query_vectors = await self._embed_query_vectors(query, image_urls)
        plan = build_retrieval_plan(query, has_images=bool(image_urls), explicit_chunk_type=chunk_type)
        confidence_type = confidence_intent(plan)
        final_top_k = max(int(top_k or 0), 0)
        recall_k = max(final_top_k * 3, DEFAULT_RECALL_TOP_N) if final_top_k else 0
        optional_filter_used = any((category, tags, device_type, document_version, manual_type))
        # B: 标题命中查找（纯字符串匹配，< 1ms，提前计算用于可观测）
        section_index = SectionTitleIndex.get_instance()
        section_match_hits = section_index.find(query) if section_index._built else []
        await _emit_retrieval_event(
            _event_sink,
            "retrieval_start",
            {
                "query": query,
                "intent": plan.intent,
                "routes": list(plan.routes),
                "topK": final_top_k,
                "recallTopN": recall_k,
                "hasImages": bool(image_urls),
                "sectionMatchCount": len(section_match_hits),
            },
        )

        def filter_for_route(route: str, relaxed: bool = False) -> Optional[str]:
            route_chunk_type = chunk_type
            if not route_chunk_type:
                if plan.intent == "outline":
                    route_chunk_type = "outline"
                if route in {"table", "table_keyword"}:
                    route_chunk_type = "table"
                elif route == "text":
                    route_chunk_type = "text"
                elif route == "image_vector":
                    route_chunk_type = "image"
                elif route in {"image_summary", "image_summary_keyword"}:
                    route_chunk_type = "image_summary"
                elif route == "step_raw":
                    route_chunk_type = "step_raw"
            return self._build_filter(
                category=None if relaxed else category,
                tags=None if relaxed else tags,
                document_id=document_id,
                chunk_type=route_chunk_type,
                device_type=device_type,  # 范围限定项即使补召回也不放宽，保证强隔离
                document_version=None if relaxed else document_version,
                manual_type=None if relaxed else manual_type,
                record_type="manual",
                status="ready",
            )

        text_vector = query_vectors.get("text_vector")
        image_vector = query_vectors.get("image_vector") or text_vector

        async def run_route(route: str, relaxed: bool = False, limit: int = None) -> List[Dict]:
            route_filter = filter_for_route(route, relaxed=relaxed)
            route_name = self._route_name(route, relaxed=relaxed)
            route_top_k = limit or recall_k
            if route in {"keyword", "table_keyword", "image_summary_keyword"}:
                if not hasattr(vector_service, "keyword_search"):
                    await _emit_retrieval_event(
                        _event_sink,
                        "retrieval_route",
                        {
                            "route": route_name,
                            "sourceRoute": route,
                            "candidateCount": 0,
                            "limit": route_top_k,
                            "relaxed": relaxed,
                            "skipped": True,
                        },
                    )
                    return []
                docs = await asyncio.to_thread(
                    vector_service.keyword_search,
                    query,
                    top_k=route_top_k,
                    include_metadata=True,
                    filter=route_filter,
                )
                marked = [self._mark_route(doc, route_name) for doc in docs]
                await _emit_retrieval_event(
                    _event_sink,
                    "retrieval_route",
                    {
                        "route": route_name,
                        "sourceRoute": route,
                        "candidateCount": len(marked),
                        "limit": route_top_k,
                        "relaxed": relaxed,
                    },
                )
                return marked

            route_vector = text_vector
            if route == "image_vector":
                route_vector = image_vector
            elif route == "image_summary":
                route_vector = text_vector or image_vector
            if not route_vector:
                await _emit_retrieval_event(
                    _event_sink,
                    "retrieval_route",
                    {
                        "route": route_name,
                        "sourceRoute": route,
                        "candidateCount": 0,
                        "limit": route_top_k,
                        "relaxed": relaxed,
                        "skipped": True,
                    },
                )
                return []
            docs = await asyncio.to_thread(
                vector_service.search,
                route_vector,
                top_k=route_top_k,
                include_metadata=True,
                filter=route_filter,
            )
            marked = [self._mark_route(doc, route_name) for doc in docs]
            await _emit_retrieval_event(
                _event_sink,
                "retrieval_route",
                {
                    "route": route_name,
                    "sourceRoute": route,
                    "candidateCount": len(marked),
                    "limit": route_top_k,
                    "relaxed": relaxed,
                },
            )
            return marked

        try:
            vector_service = get_vector_service()
            # B: 章节标题命中 → 强制召回（先翻目录再看内容）
            section_index = SectionTitleIndex.get_instance()
            section_index.build(vector_service)

            async def fetch_section_match_candidates() -> List[Dict]:
                hits = section_index.find(query)
                if not hits:
                    return []
                all_records: List[Dict] = []
                for ref in hits:
                    doc_id = ref.document_id
                    if document_id and document_id != doc_id:
                        continue  # 有文档限定则只拉该文档的匹配节
                    try:
                        records = await asyncio.to_thread(
                            vector_service.get_section_records,
                            doc_id, ref.section_id, limit=30, chunk_type=None,
                        )
                    except Exception as exc:
                        logger.debug("section_match fetch %s/%s failed: %s", doc_id, ref.section_id, exc)
                        continue
                    for record in records:
                        all_records.append(self._mark_route(record, "section_match"))
                return all_records

            route_results = await asyncio.gather(
                *(run_route(route) for route in plan.routes),
                fetch_section_match_candidates(),
            )
            candidate_lists = [list(docs) for docs in route_results]

            if not any(candidate_lists) and optional_filter_used and not document_id:
                logger.info("No evidence matched optional retrieval filters; retrying without inferred metadata filters")
                relaxed_results = await asyncio.gather(*(run_route(route, relaxed=True) for route in plan.routes))
                candidate_lists = [list(docs) for docs in relaxed_results]
        except Exception as e:
            raise ToolException(code="SEARCH_FAILED", message=f"retrieval search failed: {e}")

        image_locator_used = False
        image_locator_candidate_count = 0

        def apply_image_locator(current_merged: List[Dict], current_ranked: List[Dict]) -> tuple[List[Dict], List[Dict]]:
            nonlocal image_locator_used, image_locator_candidate_count
            locator_candidates = self._locate_image_candidates(
                query,
                current_ranked,
                vector_service,
                plan,
                document_id=document_id,
                limit=max(final_top_k, 5),
            )
            if not locator_candidates:
                return current_merged, current_ranked
            image_locator_used = True
            image_locator_candidate_count = max(image_locator_candidate_count, len(locator_candidates))
            merged_with_locator = self._filter_candidates_for_plan(
                self._merge_candidates(locator_candidates + current_merged),
                plan,
            )
            return merged_with_locator, rank_candidates(query, merged_with_locator, plan)

        fused = reciprocal_rank_fusion(
            candidate_lists,
            key_fn=self._canonical_id,
            top_k=recall_k,
            rrf_constant=DEFAULT_RRF_CONSTANT,
        )
        merged = self._filter_candidates_for_plan(self._merge_candidates(fused), plan)
        ranked = rank_candidates(query, merged, plan)
        merged, ranked = apply_image_locator(merged, ranked)
        selected = diversify_candidates(ranked, top_k=final_top_k, intent=confidence_type)
        first_quality = evaluate_retrieval_quality(plan, ranked, selected, top_k=final_top_k)
        candidate_count_before = len(merged)
        supplemental_search_used = False
        supplemental_routes: List[str] = []
        await _emit_retrieval_event(
            _event_sink,
            "retrieval_quality",
            {
                "stage": "first_pass",
                "grade": first_quality.grade,
                "score": first_quality.score,
                "candidateCount": first_quality.candidate_count,
                "bestScore": first_quality.best_score,
                "matchedTypes": first_quality.matched_types,
                "requiredTypes": first_quality.required_types,
                "reasons": first_quality.reasons,
                "shouldSupplement": first_quality.should_supplement,
                "supplementalRoutes": first_quality.supplemental_routes,
            },
        )

        if first_quality.should_supplement:
            supplemental_search_used = True
            supplemental_routes = first_quality.supplemental_routes
            supplemental_limit = max(recall_k * 2, top_k * 6, 6)
            await _emit_retrieval_event(
                _event_sink,
                "retrieval_supplement",
                {
                    "routes": supplemental_routes,
                    "limit": supplemental_limit,
                    "reasons": first_quality.reasons,
                },
            )
            try:
                supplemental_results = await asyncio.gather(
                    *(run_route(route, limit=supplemental_limit) for route in supplemental_routes)
                )
            except Exception as e:
                raise ToolException(code="SEARCH_FAILED", message=f"supplemental retrieval failed: {e}")
            candidate_lists.extend(list(docs) for docs in supplemental_results)
            fused = reciprocal_rank_fusion(
                candidate_lists,
                key_fn=self._canonical_id,
                top_k=max(recall_k, supplemental_limit),
                rrf_constant=DEFAULT_RRF_CONSTANT,
            )
            merged = self._filter_candidates_for_plan(self._merge_candidates(fused), plan)
            ranked = rank_candidates(query, merged, plan)
            merged, ranked = apply_image_locator(merged, ranked)
            selected = diversify_candidates(ranked, top_k=final_top_k, intent=confidence_type)

        selected = self._promote_section_siblings(ranked, selected, final_top_k)
        selected = self._ensure_section_image(ranked, selected, final_top_k, plan)
        selected = self._ensure_section_steps(selected, plan, vector_service)
        selected = self._filter_query_images(ranked, selected, final_top_k, plan, query)
        final_quality = evaluate_retrieval_quality(plan, ranked, selected, top_k=final_top_k)
        candidate_count_after = len(merged)
        await _emit_retrieval_event(
            _event_sink,
            "retrieval_image_locator",
            {
                "used": image_locator_used,
                "candidateCount": image_locator_candidate_count,
                "intent": plan.intent,
            },
        )
        await _emit_retrieval_event(
            _event_sink,
            "retrieval_quality",
            {
                "stage": "final",
                "grade": final_quality.grade,
                "score": final_quality.score,
                "candidateCount": final_quality.candidate_count,
                "bestScore": final_quality.best_score,
                "matchedTypes": final_quality.matched_types,
                "requiredTypes": final_quality.required_types,
                "reasons": final_quality.reasons,
                "shouldSupplement": False,
                "supplementalRoutes": supplemental_routes,
            },
        )
        confidence = summarize_confidence(selected, intent=confidence_type)
        expanded_selected = expand_retrieval_context(selected, vector_service, max_expanded=6)
        expanded_count = max(0, len(expanded_selected) - len(selected))
        await _emit_retrieval_event(
            _event_sink,
            "retrieval_expand",
            {
                "expandedCount": expanded_count,
                "primaryCount": len(selected),
                "totalCount": len(expanded_selected),
                "strategy": "parent_section_context",
            },
        )

        results: List[VectorSearchResult] = []
        for doc in expanded_selected:
            metadata = dict(doc.get("metadata") or {})
            if metadata.get("chunk_type") == "image_summary":
                metadata["source_chunk_type"] = "image_summary"
                metadata["chunk_type"] = "image"
            routes = sorted(set(doc.get("routes") or []))
            metadata["retrieval_routes"] = routes
            metadata["matched_types"] = confidence["matched_types"]
            metadata["retrieval_confidence"] = confidence["confidence"]
            metadata["retrieval_plan_intent"] = plan.intent
            metadata["requires_strict_evidence"] = plan.requires_strict_evidence
            metadata["retrieval_context_expanded_count"] = expanded_count
            metadata["adaptive_rag_enabled"] = True
            metadata["recall_top_n"] = recall_k
            metadata["final_top_k"] = final_top_k
            metadata.setdefault("rrf_enabled", False)
            metadata.setdefault("rrf_constant", DEFAULT_RRF_CONSTANT)
            metadata["first_pass_quality"] = first_quality.grade
            metadata["final_quality"] = final_quality.grade
            metadata["first_pass_quality_score"] = first_quality.score
            metadata["final_quality_score"] = final_quality.score
            metadata["first_pass_quality_reasons"] = first_quality.reasons
            metadata["final_quality_reasons"] = final_quality.reasons
            metadata["quality_reasons"] = final_quality.reasons
            metadata["required_evidence_types"] = final_quality.required_types
            metadata["supplemental_search_used"] = supplemental_search_used
            metadata["supplemental_routes"] = supplemental_routes
            metadata["image_locator_branch_used"] = image_locator_used
            metadata["image_locator_candidate_count"] = image_locator_candidate_count
            metadata["candidate_count_before"] = candidate_count_before
            metadata["candidate_count_after"] = candidate_count_after
            metadata["confidence_reason"] = {
                "best_relevance_score": confidence["best_relevance_score"],
                "candidate_count": confidence["candidate_count"],
                "dual_image_hit": confidence["dual_image_hit"],
                "intent": plan.intent,
                "confidence_intent": confidence_type,
                "first_pass_quality": first_quality.grade,
                "final_quality": final_quality.grade,
            }
            if confidence["confidence"] == "low" or final_quality.grade == "low":
                metadata["answer_policy"] = "insufficient_evidence"
            results.append(
                VectorSearchResult(
                    id=doc.get("doc_id", ""),
                    score=doc.get("relevance_score", doc.get("score", 0.0)),
                    content=doc.get("text", ""),
                    metadata=metadata,
                    raw_score=doc.get("raw_score"),
                    raw_score_type=doc.get("raw_score_type"),
                    relevance_score=doc.get("relevance_score"),
                    retrieval_route=routes[0] if routes else doc.get("retrieval_route"),
                    rerank_score=doc.get("rerank_score"),
                )
            )
        await _emit_retrieval_event(
            _event_sink,
            "retrieval_done",
            {
                "selectedCount": len(results),
                "primaryCount": len(selected),
                "expandedCount": expanded_count,
                "candidateCountBefore": candidate_count_before,
                "candidateCountAfter": candidate_count_after,
                "countsByType": _count_selected_types(expanded_selected),
                "finalQuality": final_quality.grade,
                "finalQualityScore": final_quality.score,
                "supplementalSearchUsed": supplemental_search_used,
                "supplementalRoutes": supplemental_routes,
                "imageLocatorUsed": image_locator_used,
                "imageLocatorCandidateCount": image_locator_candidate_count,
            },
        )
        return results


_retrieval_tool: Optional[KnowledgeRetrievalTool] = None


def get_knowledge_retrieval_tool() -> KnowledgeRetrievalTool:
    global _retrieval_tool
    if _retrieval_tool is None:
        _retrieval_tool = KnowledgeRetrievalTool()
    return _retrieval_tool
