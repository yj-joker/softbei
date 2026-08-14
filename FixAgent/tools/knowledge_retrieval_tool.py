"""Knowledge retrieval tool for multimodal RAG evidence."""

from __future__ import annotations

import logging
import asyncio
import inspect
import re
from typing import Any, Callable, Dict, Iterable, List, Optional

from embeddings.multimodal_embedding import get_multimodal_embedding
from embeddings.text_embedding import get_text_embedding
from schemas.models import VectorSearchResult
from services.retrieval.planner import build_retrieval_plan, confidence_intent
from services.retrieval.provenance import dedupe_and_sort_manual_records
from services.retrieval.manual_scope import apply_authoritative_manual_scope
from services.retrieval.ranker import rank_candidates
from services.retrieval.context_expander import expand_retrieval_context
from services.retrieval.fusion import DEFAULT_RRF_CONSTANT, reciprocal_rank_fusion
from services.retrieval.quality import evaluate_retrieval_quality
from services.retrieval.policy import (
    diversify_candidates,
    summarize_confidence,
)
from services.retrieval.qualification import qualify_candidates
from services.retrieval.aspects import split_question_aspects
from services.retrieval.supplement import (
    CandidateRequestCache,
    decide_supplemental_retrieval,
    run_supplemental_stage,
)
from services.retrieval.image_selector import PageEvidence, gated_select_pages_for_image_query
from services.retrieval.query_constraints import (
    candidate_constraint_conflicts,
    extract_query_constraints,
)
from services.retrieval.query_understanding import has_negative_image_request, understand_query
from services.retrieval.query_variants import (
    QueryVariant,
    build_query_variants,
    build_variant_route_pairs,
)
from services.retrieval.section_index import SectionTitleIndex
from services.knowledge.vector_service import build_redis_filter, escape_redis_tag_value, get_vector_service
from tools.base_tool import BaseTool, ToolException

logger = logging.getLogger(__name__)

DEFAULT_RECALL_TOP_N = 50
IMAGE_LOCATOR_LOOKUP_LIMIT = 20
PAGE_SELECTOR_SCAN_LIMIT = 60
PAGE_SELECTOR_LOOKUP_LIMIT = 80
IMAGE_LOCATOR_PAGE_RE = re.compile(r"(?:\u7b2c\s*)?(\d{1,3})\s*\u9875")


def merge_additive_manual_results(
    baseline: Any,
    graph_seed: Any,
) -> list[Any]:
    """Union manual evidence without allowing graph seed to remove baseline IDs."""
    merged: list[Any] = []
    positions: dict[str, int] = {}
    for origin, values in (("baseline", baseline or ()), ("graph_seed", graph_seed or ())):
        for index, item in enumerate(values):
            key = _manual_evidence_key(item, fallback=f"{origin}:{index}")
            position = positions.get(key)
            if position is None:
                positions[key] = len(merged)
                merged.append(item)
                continue
            if _manual_source_completeness(item) > _manual_source_completeness(merged[position]):
                merged[position] = item
    return merged


def _manual_evidence_key(item: Any, *, fallback: str) -> str:
    payload = item if isinstance(item, dict) else {}
    metadata = (
        payload.get("metadata")
        if isinstance(payload, dict)
        else getattr(item, "metadata", {})
    ) or {}
    evidence_id = str(
        payload.get("evidence_id")
        or metadata.get("evidence_id")
        or payload.get("id")
        or getattr(item, "id", "")
        or ""
    ).strip()
    if evidence_id:
        return f"evidence:{evidence_id}"
    document_id = str(metadata.get("document_id") or payload.get("document_id") or "").strip()
    chunk_uid = str(
        metadata.get("chunk_uid")
        or metadata.get("source_chunk_uid")
        or payload.get("chunk_uid")
        or ""
    ).strip()
    if document_id and chunk_uid:
        return f"chunk_uid:{document_id}:{chunk_uid}"
    chunk_id = str(
        metadata.get("chunk_id")
        or metadata.get("source_chunk_id")
        or payload.get("chunk_id")
        or payload.get("doc_id")
        or getattr(item, "doc_id", "")
        or ""
    ).strip()
    if document_id and chunk_id:
        return f"chunk_id:{document_id}:{chunk_id}"
    return f"opaque:{fallback}"


def _manual_source_completeness(item: Any) -> tuple[int, int]:
    payload = item if isinstance(item, dict) else {}
    metadata = (
        payload.get("metadata")
        if isinstance(payload, dict)
        else getattr(item, "metadata", {})
    ) or {}
    source_fields = (
        "document_id",
        "document_version",
        "chunk_uid",
        "source_chunk_uid",
        "chunk_id",
        "source_chunk_id",
        "parent_section_id",
        "page",
    )
    populated = sum(metadata.get(field) not in (None, "", [], ()) for field in source_fields)
    content = str(payload.get("content") or getattr(item, "content", "") or "")
    return populated, len(content)


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

    _PAGE_RECORD_CACHE: Dict[tuple[int, str, int, str], List[Dict[str, Any]]] = {}

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
                "parent_section_id": {"type": "string", "description": "Restrict retrieval to one imported section."},
                "allowed_section_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Server-resolved section allow-list from clarification.",
                },
                "allowed_evidence_refs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Server-resolved evidence allow-list from clarification.",
                },
                "allowed_source_chunk_uids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Server-resolved stable source chunk identities.",
                },
                "pages": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Server-resolved page window; two values form a closed range.",
                },
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
        parent_section_id: str = None,
    ) -> Optional[str]:
        if not any((document_id, chunk_type, device_type, document_version, manual_type, record_type, status, chunk_label, parent_section_id)):
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
            parent_section_id=parent_section_id,
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

    @classmethod
    def _filter_to_resolved_scope(
        cls,
        candidates: List[Dict],
        *,
        allowed_section_ids: Iterable[str] = (),
        allowed_evidence_refs: Iterable[str] = (),
        allowed_source_chunk_uids: Iterable[str] = (),
        pages: Iterable[int] = (),
    ) -> List[Dict]:
        """Apply a non-relaxable allow-list after every retrieval expansion."""
        sections = {str(value).strip() for value in allowed_section_ids if str(value).strip()}
        evidence = {
            str(value).strip() for value in allowed_evidence_refs if str(value).strip()
        }
        source_uids = {
            str(value).strip() for value in allowed_source_chunk_uids if str(value).strip()
        }
        page_values: list[int] = []
        for value in pages:
            try:
                page = int(value)
            except (TypeError, ValueError):
                continue
            if page > 0 and page not in page_values:
                page_values.append(page)
        if len(page_values) == 2:
            start, end = sorted(page_values)
            allowed_pages = set(range(start, end + 1))
        else:
            allowed_pages = set(page_values)
        if not sections and not evidence and not source_uids and not allowed_pages:
            return list(candidates)
        filtered: List[Dict] = []
        for candidate in candidates:
            metadata = candidate.get("metadata") or {}
            section_id = str(metadata.get("parent_section_id") or "").strip()
            if sections and section_id not in sections:
                continue
            identifiers = {
                str(candidate.get("doc_id") or "").strip(),
                str(metadata.get("id") or "").strip(),
                str(metadata.get("chunk_id") or "").strip(),
                str(metadata.get("chunk_uid") or "").strip(),
                str(metadata.get("row_id") or "").strip(),
                str(metadata.get("table_id") or "").strip(),
                str(metadata.get("parent_chunk_id") or "").strip(),
                str(metadata.get("continuation_id") or "").strip(),
                str(metadata.get("source_chunk_id") or "").strip(),
                str(metadata.get("source_image_id") or "").strip(),
                str(cls._canonical_id(candidate) or "").strip(),
            }
            identifiers.discard("")
            if evidence and not identifiers.intersection(evidence):
                continue
            stable_source_ids = {
                str(metadata.get("chunk_uid") or "").strip(),
                str(metadata.get("source_chunk_uid") or "").strip(),
                *(
                    str(value).strip()
                    for value in metadata.get("source_chunk_uids") or ()
                ),
            }
            stable_source_ids.discard("")
            if source_uids and stable_source_ids and not stable_source_ids.intersection(source_uids):
                continue
            raw_page = metadata.get("page")
            if raw_page is None:
                raw_page = metadata.get("page_number")
            try:
                candidate_page = int(raw_page)
            except (TypeError, ValueError):
                candidate_page = 0
            if allowed_pages and candidate_page not in allowed_pages:
                continue
            filtered.append(candidate)
        return filtered

    @classmethod
    def _load_authoritative_scope_records(
        cls,
        vector_service,
        *,
        document_id: str,
        allowed_section_ids: Iterable[str] = (),
        allowed_evidence_refs: Iterable[str] = (),
        allowed_source_chunk_uids: Iterable[str] = (),
        pages: Iterable[int] = (),
        limit: int = 30,
    ) -> List[Dict]:
        """Load server-locked manual records without depending on global Top-K."""
        if not document_id:
            return []
        sections = tuple(dict.fromkeys(
            str(value).strip() for value in allowed_section_ids if str(value).strip()
        ))
        if not sections or not hasattr(vector_service, "get_section_records"):
            return []
        records: List[Dict] = []
        for section_id in sections:
            records.extend(vector_service.get_section_records(
                document_id,
                section_id,
                limit=max(1, int(limit)),
                chunk_type=None,
            ))
        return cls._filter_to_resolved_scope(
            records,
            allowed_section_ids=sections,
            allowed_evidence_refs=allowed_evidence_refs,
            allowed_source_chunk_uids=allowed_source_chunk_uids,
            pages=pages,
        )

    @staticmethod
    def _has_usable_qualified_evidence(items: Any) -> bool:
        for item in items or ():
            metadata = (
                item.get("metadata")
                if isinstance(item, dict)
                else getattr(item, "metadata", {})
            ) or {}
            if (
                metadata.get("qualification") == "qualified"
                and metadata.get("answer_policy") != "insufficient_evidence"
            ):
                return True
        return False

    @staticmethod
    def _mark_graph_manual_fallback(items: Any, graph_failure_reason: str) -> None:
        for item in items or ():
            if isinstance(item, dict):
                metadata = item.setdefault("metadata", {})
            else:
                metadata = getattr(item, "metadata", None)
                if metadata is None:
                    metadata = {}
                    setattr(item, "metadata", metadata)
            metadata["manual_fallback_reason"] = "graph_failed_manual_fallback"
            metadata["graph_failure_reason"] = (
                graph_failure_reason or "graph_scope_evidence_unusable"
            )

    async def run(self, **kwargs):
        """Run ordinary RAG first and treat graph locators as additive seeds."""
        graph_seed_scope = kwargs.pop("_graph_seed_scope", None)
        if graph_seed_scope:
            event_sink = kwargs.get("_event_sink")
            stage_traces: list[dict[str, Any]] = []

            async def capture_additive_events(payload: Dict[str, Any]) -> None:
                if payload.get("event") == "retrieval_stage":
                    stage_traces.append(dict(payload.get("data") or {}))
                    return
                if event_sink is not None:
                    result = event_sink(payload)
                    if inspect.isawaitable(result):
                        await result

            additive_kwargs = dict(kwargs)
            if event_sink is not None:
                additive_kwargs["_event_sink"] = capture_additive_events
            baseline = await super().run(**additive_kwargs)
            if not baseline.success:
                return baseline
            seed_kwargs = apply_authoritative_manual_scope(additive_kwargs, graph_seed_scope)
            seed = await super().run(**seed_kwargs)
            if not seed.success or not seed.data:
                if event_sink is not None and stage_traces:
                    await _emit_retrieval_event(event_sink, "retrieval_stage", stage_traces[0])
                return baseline
            merged = merge_additive_manual_results(baseline.data, seed.data)
            if event_sink is not None:
                stage_fields = (
                    "candidate_ids",
                    "filtered_ids",
                    "reranked_ids",
                    "selected_ids",
                    "expanded_ids",
                )
                merged_trace: dict[str, list[str]] = {}
                for field in stage_fields:
                    merged_trace[field] = list(dict.fromkeys(
                        identifier
                        for trace in stage_traces
                        for identifier in trace.get(field) or ()
                        if str(identifier).strip()
                    ))
                merged_trace["visible_ids"] = self._retrieval_stage_ids(merged)
                await _emit_retrieval_event(event_sink, "retrieval_stage", merged_trace)
            return self._success(merged)

        # Compatibility for stored route snapshots created before additive
        # scopes were introduced.  New requests never enter this branch.
        allow_fallback = bool(kwargs.pop("_allow_graph_scope_fallback", False))
        graph_failure_reason = str(kwargs.pop("_graph_failure_reason", "") or "")
        first = await super().run(**kwargs)
        if (
            not allow_fallback
            or not first.success
        ):
            return first

        locator_keys = {
            "allowed_evidence_refs",
            "allowed_source_chunk_uids",
            "allowed_section_ids",
            "parent_section_id",
            "pages",
        }
        if not any(kwargs.get(key) for key in locator_keys):
            return first
        fallback_kwargs = {
            key: value for key, value in kwargs.items() if key not in locator_keys
        }
        fallback = await super().run(**fallback_kwargs)
        if fallback.success and self._has_usable_qualified_evidence(fallback.data):
            self._mark_graph_manual_fallback(fallback.data, graph_failure_reason)
            return fallback
        return first

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
        """图片规整（仅 procedure）：只保留主节（表头步骤所在节 + section_match 确定性命中的节）的配图，
        砍掉其他节的图（治"拆发动机"带回一堆无关配图）。
        当 plan.section_match_ids 非空时，直接使用确定性信号确定主节；
        主节图 = selected 中已有的 + ranked 中遗漏的全部补回，不设数量上限。
        无确定性信号时回退到从 selected 头部猜测主节的旧逻辑。"""
        sm_ids = getattr(plan, "section_match_ids", None) or []
        if plan.intent not in {"procedure", "outline"}:
            return selected
        if plan.intent == "outline" and not sm_ids:
            return selected
        if not selected or top_k <= 0:
            return selected

        def section_id(item: Dict) -> str:
            return (item.get("metadata") or {}).get("parent_section_id") or ""

        # anchor 来源：确定性信号优先，sm_ids 非空时不混入 head_anchor
        # 避免头部排名浮动污染锚点导致图片集漂移
        if sm_ids:
            anchor = set(sm_ids)
        else:
            head = selected[:freeze_head]
            anchor = {section_id(it) for it in head if cls._is_step(it) and section_id(it)}
            if not anchor:
                anchor = {section_id(it) for it in selected if cls._is_step(it) and section_id(it)}
        if not anchor:
            return selected

        # 分类：主节图 / 无关节图 / 非图候选
        main_in_selected = [it for it in selected if cls._is_image_record(it) and section_id(it) in anchor]
        foreign_images = [it for it in selected if cls._is_image_record(it) and section_id(it) not in anchor]
        non_images = [it for it in selected if not cls._is_image_record(it)]

        # 补全遗漏：ranked 中属于 anchor 但未进入 selected 的图
        selected_canonical = {cls._canonical_id(it) for it in main_in_selected}
        main_in_ranked = [
            it for it in ranked
            if cls._is_image_record(it)
            and section_id(it) in anchor
            and cls._canonical_id(it) not in selected_canonical
        ]
        # 按 canonical_id 去重
        seen_extra: set = set()
        extra_images: List[Dict] = []
        for it in main_in_ranked:
            cid = cls._canonical_id(it)
            if cid not in seen_extra:
                seen_extra.add(cid)
                extra_images.append(it)
        main_images = main_in_selected + extra_images

        if not main_images and not foreign_images:
            return selected

        # 重建：非图候选 + 主节全量图，无关节图丢弃
        rebuilt = non_images + main_images
        return rebuilt

    @staticmethod
    def _source_order(rec: Dict) -> tuple:
        """原文顺序键：表块按 (table_index, 整表在前, row_index)，其余按 source_index。"""
        meta = rec.get("metadata") or {}

        def _int(value, default):
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        source_index = _int(meta.get("source_index"), 9999)
        row_index = _int(meta.get("row_index"), -1)  # table_full 无 row_index → 排本表最前
        return (source_index, row_index)

    @classmethod
    def _ensure_section_steps(
        cls,
        selected: List[Dict],
        plan,
        vector_service,
        max_steps: int = 10,
        max_sections: int = 2,
    ) -> List[Dict]:
        """C 按节取全：procedure/parameter/outline(清单) 意图下，把主节的完整正文块从库里
        直接补齐——procedure 补 step_raw 步骤块、parameter/outline 补 table 表块——按原文
        顺序排序，避免正文没进 top_k 导致模型漏步/漏行/编造（如跨页续表只剩上半张、涨紧器
        只回“参照气缸头”）。主节优先取 section_match 确定性信号命中的节（防止头部排名
        浮动导致主节漂移），回退 selected 里占比最高的节，最多补 max_sections 个节
        （覆盖跨节长流程）。允许超出 top_k。"""
        intent_chunk_type = {"procedure": "step_raw", "parameter": "table", "outline": "table"}.get(plan.intent)
        if intent_chunk_type is None:
            return selected
        if not selected or not hasattr(vector_service, "get_section_records"):
            return selected

        counter: Dict[tuple, int] = {}
        score_by_sec: Dict[tuple, float] = {}
        provenance_by_sec: Dict[tuple, Dict[str, Any]] = {}
        for it in selected:
            meta = it.get("metadata") or {}
            key = (str(meta.get("document_id") or ""), str(meta.get("parent_section_id") or ""))
            if not all(key):
                continue
            counter[key] = counter.get(key, 0) + 1
            sc = float(it.get("relevance_score") or it.get("score") or 0.0)
            score_by_sec[key] = max(score_by_sec.get(key, 0.0), sc)
            provenance = provenance_by_sec.setdefault(key, {
                "routes": [],
                "query_variants": [],
                "local_rerank_features": {},
                "feature_score": -1.0,
            })
            provenance["routes"] = sorted(set(provenance["routes"]) | set(it.get("routes") or []))
            for variant in cls._candidate_query_variants(it):
                if variant not in provenance["query_variants"]:
                    provenance["query_variants"].append(variant)
            if meta.get("local_rerank_features") and sc >= provenance["feature_score"]:
                provenance["local_rerank_features"] = dict(meta["local_rerank_features"])
                provenance["feature_score"] = sc
        if not counter:
            return selected
        sm_ids = {str(sid) for sid in (getattr(plan, "section_match_ids", None) or [])}
        target_sections = sorted(
            counter,
            key=lambda k: (k[1] in sm_ids, counter[k], score_by_sec.get(k, 0.0)),
            reverse=True,
        )[:max_sections]

        snapshots: List[Dict] = []
        completed_sections: set[tuple[str, str]] = set()
        for document_id, parent_section_id in target_sections:
            section_provenance = provenance_by_sec.get((document_id, parent_section_id), {})
            try:
                records = vector_service.get_section_records(
                    document_id, parent_section_id, limit=30, chunk_type=intent_chunk_type
                )
            except Exception as exc:
                logger.warning("[ensure_section_steps] 取节步骤失败: %s", exc)
                continue
            section_records: List[Dict] = []
            for rec in records or []:
                rec = rec.model_dump() if hasattr(rec, "model_dump") else dict(rec)
                meta = dict(rec.get("metadata") or {})
                rid = str(rec.get("doc_id") or rec.get("id") or "")
                text = (rec.get("text") or rec.get("content") or meta.get("raw_text") or "").strip()
                if not text:
                    continue
                meta["context_role"] = "section_step" if plan.intent == "procedure" else "section_param"
                if section_provenance.get("query_variants"):
                    meta["query_variants"] = list(section_provenance["query_variants"])
                if section_provenance.get("local_rerank_features"):
                    meta["local_rerank_features"] = dict(section_provenance["local_rerank_features"])
                section_records.append({
                    "doc_id": rid,
                    "id": rid,
                    "text": text,
                    "content": text,
                    "metadata": meta,
                    "routes": list(section_provenance.get("routes") or []),
                })
            ordered = dedupe_and_sort_manual_records(section_records)
            if not ordered:
                continue
            remaining = max_steps - len(snapshots)
            if remaining <= 0:
                break
            snapshots.extend(ordered[:remaining])
            completed_sections.add((document_id, parent_section_id))

        if not snapshots:
            return selected

        def is_replaced_section_item(item: Dict) -> bool:
            meta = item.get("metadata") or {}
            key = (
                str(meta.get("document_id") or ""),
                str(meta.get("parent_section_id") or ""),
            )
            if key not in completed_sections:
                return False
            if plan.intent == "procedure":
                return (
                    meta.get("chunk_type") == "step_raw"
                    or meta.get("chunk_label") in {"step", "step_raw"}
                    or meta.get("answer_role") == "procedure_step"
                )
            return meta.get("chunk_type") == "table"

        retained = [item for item in selected if not is_replaced_section_item(item)]
        return snapshots + retained

    @staticmethod
    def _mark_route(doc: Dict, route: str) -> Dict:
        item = dict(doc)
        item["metadata"] = dict(item.get("metadata") or {})
        if item.get("relevance_score") is None and item.get("score") is not None:
            item["relevance_score"] = item["score"]
        item["routes"] = sorted(set(item.get("routes") or []) | {route})
        item["retrieval_route"] = route
        return item

    @staticmethod
    def _retrieval_stage_ids(items: Iterable[Dict[str, Any]]) -> List[str]:
        identifiers: List[str] = []
        seen: set[str] = set()
        for item in items or ():
            payload = item if isinstance(item, dict) else {}
            metadata = (
                payload.get("metadata")
                if isinstance(item, dict)
                else getattr(item, "metadata", {})
            ) or {}
            identifier = str(
                metadata.get("parent_section_id")
                or metadata.get("chunk_uid")
                or metadata.get("source_chunk_uid")
                or metadata.get("chunk_id")
                or payload.get("doc_id")
                or getattr(item, "doc_id", "")
                or ""
            ).strip()
            if identifier and identifier not in seen:
                seen.add(identifier)
                identifiers.append(identifier)
        return identifiers

    @classmethod
    def _build_retrieval_stage_trace(
        cls,
        *,
        fused: Iterable[Dict[str, Any]],
        filtered: Iterable[Dict[str, Any]],
        reranked: Iterable[Dict[str, Any]],
        selected: Iterable[Dict[str, Any]],
        expanded: Iterable[Dict[str, Any]],
        visible: Iterable[Dict[str, Any]],
    ) -> Dict[str, List[str]]:
        return {
            "candidate_ids": cls._retrieval_stage_ids(fused),
            "filtered_ids": cls._retrieval_stage_ids(filtered),
            "reranked_ids": cls._retrieval_stage_ids(reranked),
            "selected_ids": cls._retrieval_stage_ids(selected),
            "expanded_ids": cls._retrieval_stage_ids(expanded),
            "visible_ids": cls._retrieval_stage_ids(visible),
        }

    @classmethod
    def _merge_candidates(cls, candidates: List[Dict]) -> List[Dict]:
        merged: Dict[str, Dict] = {}
        for candidate in candidates:
            key = cls._canonical_id(candidate)
            if key not in merged:
                item = dict(candidate)
                item["doc_id"] = key
                item["metadata"] = dict(candidate.get("metadata") or {})
                item["metadata"]["query_variants"] = cls._candidate_query_variants(candidate)
                merged[key] = item
                continue
            current = merged[key]
            query_variants = cls._candidate_query_variants(current)
            for variant in cls._candidate_query_variants(candidate):
                if variant not in query_variants:
                    query_variants.append(variant)
            routes = sorted(set(current.get("routes") or []) | set(candidate.get("routes") or []))
            if candidate.get("relevance_score", 0.0) > current.get("relevance_score", 0.0):
                current.update(candidate)
                current["doc_id"] = key
            current["routes"] = routes
            current_meta = current.setdefault("metadata", {})
            current_meta.update(
                {name: value for name, value in (candidate.get("metadata") or {}).items() if value not in ("", None)}
            )
            current_meta["query_variants"] = query_variants
        return list(merged.values())

    @staticmethod
    def _candidate_query_variants(candidate: Dict) -> List[Dict[str, str]]:
        metadata = candidate.get("metadata") or {}
        variants: List[Dict[str, str]] = []
        existing = metadata.get("query_variants") or []
        if isinstance(existing, list):
            for value in existing:
                if not isinstance(value, dict) or not str(value.get("text") or "").strip():
                    continue
                variants.append({
                    "text": str(value.get("text") or "").strip(),
                    "source": str(value.get("source") or "").strip(),
                    "target_id": str(value.get("target_id") or "").strip(),
                })
        text = str(metadata.get("query_variant_text") or "").strip()
        if text:
            current = {
                "text": text,
                "source": str(metadata.get("query_variant_source") or "").strip(),
                "target_id": str(metadata.get("query_variant_target_id") or "").strip(),
            }
            if current not in variants:
                variants.append(current)
        return variants

    @staticmethod
    def _is_outline_candidate(candidate: Dict) -> bool:
        metadata = candidate.get("metadata") or {}
        return metadata.get("chunk_type") == "outline" or metadata.get("chunk_label") == "outline"

    @classmethod
    def _filter_candidates_for_plan(cls, candidates: List[Dict], plan) -> List[Dict]:
        if plan.intent == "outline":
            section_match_ids = {str(sid) for sid in (getattr(plan, "section_match_ids", None) or [])}
            outline_candidates: List[Dict] = []
            section_match_candidates: List[Dict] = []
            table_candidates: List[Dict] = []
            image_candidates: List[Dict] = []
            for candidate in candidates:
                if cls._is_outline_candidate(candidate):
                    outline_candidates.append(candidate)
                    continue
                routes = candidate.get("routes") or []
                if "section_match" in routes:
                    section_match_candidates.append(candidate)
                    continue
                metadata = candidate.get("metadata") or {}
                chunk_type = metadata.get("chunk_type") or ""
                parent_section_id = str(metadata.get("parent_section_id") or "")
                if chunk_type in {"image", "image_summary"} and parent_section_id in section_match_ids:
                    image_candidates.append(candidate)
                    continue
                if chunk_type == "table" and (not section_match_ids or parent_section_id in section_match_ids):
                    table_candidates.append(candidate)
            if section_match_ids:
                all_retained = section_match_candidates + table_candidates + image_candidates
                if all_retained:
                    retained_ids = {cls._canonical_id(c) for c in all_retained}
                    return [c for c in candidates if cls._canonical_id(c) in retained_ids]
            if outline_candidates:
                all_retained = outline_candidates + section_match_candidates + table_candidates + image_candidates
                if all_retained:
                    retained_ids = {cls._canonical_id(c) for c in all_retained}
                    return [c for c in candidates if cls._canonical_id(c) in retained_ids]
                return list(candidates)
            return list(candidates)
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

    # —— E 图片证据收口 ——
    # 只处理 image/image_summary；文本、表格、步骤证据原样保留，避免影响既有文本 RAG。
    _IMG_QUERY_STOPWORDS = (
        "帮我", "帮", "请", "给我", "给", "查一下", "查查", "查询", "查找", "查", "我",
        "看一下", "看看", "看", "找一下", "找", "想", "了解", "知道", "显示", "展示",
        "搜索", "搜", "提供", "介绍", "关于", "一下", "结构", "构造", "内部", "外形",
        "图片", "图纸", "图示", "示意图", "照片", "图像", "插图", "图", "相关", "的",
        "章节", "对应", "这个", "那个", "是什么", "有哪些", "哪些", "一张", "几张", "张", "和", "与", "及", "以及",
    )
    _IMG_QUERY_ACTION_PREFIXES = ("安装", "拆卸", "检查", "调整", "检修", "维修", "更换")
    _IMG_QUERY_CONNECTORS = (
        "并且", "以及", "对应的", "对应", "时候", "时", "之前", "之后", "前", "后",
        "并", "和", "与", "及", "或", "、", "，", ",", "；", ";", "：", ":",
    )

    @classmethod
    def _query_core_terms(cls, query: str) -> List[str]:
        """从查图问题里抽出核心实体词（去掉“帮我查”“结构”“图片”等通用词）。"""
        s = query or ""
        for w in cls._IMG_QUERY_STOPWORDS:
            s = s.replace(w, " ")
        for w in cls._IMG_QUERY_CONNECTORS:
            s = s.replace(w, " ")
        return [t for t in re.split(r"[^一-鿿A-Za-z0-9]+", s) if len(t) >= 2]

    @classmethod
    def _expanded_query_core_terms(cls, query: str) -> List[str]:
        terms = cls._query_core_terms(query)
        expanded: List[str] = []
        for term in terms:
            if term not in expanded:
                expanded.append(term)
            for prefix in cls._IMG_QUERY_ACTION_PREFIXES:
                if term.startswith(prefix) and len(term) > len(prefix) + 1:
                    stripped = term[len(prefix):]
                    if stripped not in expanded:
                        expanded.append(stripped)
        return expanded

    @classmethod
    def _text_anchor_score(cls, query: str, text: str) -> float:
        compact = re.sub(r"\s+", "", text or "")
        if not compact:
            return 0.0
        score = 0.0
        for term in cls._expanded_query_core_terms(query):
            term = re.sub(r"\s+", "", term or "")
            if len(term) < 2:
                continue
            if term in compact:
                score += min(len(term), 10)
                continue
            if len(term) < 4:
                continue
            grams = [term[index : index + 4] for index in range(0, len(term) - 3)]
            if not grams:
                continue
            matched = sum(1 for gram in grams if gram in compact)
            if matched / len(grams) >= 0.6:
                score += min(len(term), 10) * 0.5
        for atom in ("朝下", "朝上", "错开", "开口", "槽缺口", "缺口", "调整垫片", "O型圈", "定位销"):
            if atom in (query or "") and atom in compact:
                score += 4.0
        for number in re.findall(r"\d+(?:\.\d+)?", query or ""):
            if number in compact:
                score += 4.0
        return score

    @classmethod
    def _strong_text_anchor_pages(cls, ranked: List[Dict], selected: List[Dict], query: str) -> List[int]:
        best_by_page: Dict[int, float] = {}
        for item in list(selected or []) + list(ranked or [])[:30]:
            metadata = item.get("metadata") or {}
            chunk_type = metadata.get("chunk_type") or "text"
            if chunk_type in {"image", "image_summary", "step_raw"}:
                continue
            page = cls._metadata_page(metadata)
            if page is None:
                continue
            score = cls._text_anchor_score(query, cls._record_text_for_page_selector(item))
            if chunk_type == "text":
                score += 2.0
            if score <= 0:
                continue
            best_by_page[page] = max(best_by_page.get(page, 0.0), score)
        if not best_by_page:
            return []
        best = max(best_by_page.values())
        threshold = max(4.0, best * 0.6)
        return [
            page
            for page, _score in sorted(
                ((page, score) for page, score in best_by_page.items() if score >= threshold),
                key=lambda item: (-item[1], item[0]),
            )[:3]
        ]

    @staticmethod
    def _has_hard_visual_constraint(query: str) -> bool:
        return (
            any(term in query for term in ("朝下", "朝上", "错开", "开口", "槽缺口"))
            or bool(re.search(r"\d+(?:\.\d+)?", query or ""))
        )

    @classmethod
    def _filter_query_images(cls, ranked, selected, top_k, plan, query, query_understanding=None):
        """Post-filter image evidence while keeping text/table evidence untouched.

        优先用确定性信号收口：
        1. 用户显式问“第 N 页” → 只保留该页图片；
        2. 高置信 query_understanding 只对图片做 single_best/same_section 收口；
        3. 章节标题索引已命中 section_match_ids → 只保留命中章节图片；
        4. 只有纯查图且缺少确定性信号时，才回退到原来的关键词相关性过滤。
        """
        if not selected:
            return selected

        if has_negative_image_request(query):
            return [it for it in selected if not cls._is_image_record(it)]

        images = [it for it in selected if cls._is_image_record(it)]
        if len(images) <= 1:
            return selected

        def keep_images(allowed_ids: set[str]) -> List[Dict]:
            return [
                it for it in selected
                if (not cls._is_image_record(it)) or (cls._canonical_id(it) in allowed_ids)
            ]

        locked_selector_ids = {
            cls._canonical_id(it)
            for it in images
            if (it.get("metadata") or {}).get("page_selector_used")
        }
        if locked_selector_ids:
            return keep_images(locked_selector_ids)

        pages = set(cls._extract_query_pages(query))
        if pages:
            page_matched_ids = {
                cls._canonical_id(it)
                for it in images
                if cls._metadata_page(it.get("metadata") or {}) in pages
            }
            if page_matched_ids:
                return keep_images(page_matched_ids)

        def build_summary_by_id() -> Dict[str, str]:
            summary_by_id: Dict[str, str] = {}
            for it in ranked:
                if (it.get("metadata") or {}).get("chunk_type") == "image_summary":
                    key = cls._canonical_id(it)
                    summary_by_id[key] = summary_by_id.get(key, "") + " " + str(it.get("content") or it.get("text") or "")
            return summary_by_id

        def image_text(img: Dict, summary_by_id: Dict[str, str]) -> str:
            meta = img.get("metadata") or {}
            return " ".join([
                str(meta.get("section_title") or ""),
                str(meta.get("toc_path") or ""),
                str(meta.get("caption") or ""),
                str(img.get("content") or img.get("text") or ""),
                summary_by_id.get(cls._canonical_id(img), ""),
            ])

        def score_image(img: Dict, terms: List[str], summary_by_id: Dict[str, str]) -> int:
            if not terms:
                return 0
            text = image_text(img, summary_by_id)
            return sum(1 for term in terms if term and term in text)

        qu_confidence = float(getattr(query_understanding, "confidence", 0.0) or 0.0)
        qu_intent = str(getattr(query_understanding, "intent", "") or "")
        qu_mode = str(getattr(query_understanding, "image_mode", "") or "")
        qu_target = str(getattr(query_understanding, "target_query", "") or "")
        if qu_intent == "image_lookup" and qu_confidence >= 0.75 and qu_mode in {"single_best", "same_section"}:
            target_terms = cls._expanded_query_core_terms(qu_target or query)
            summary_by_id = build_summary_by_id()
            scored_images = [(score_image(it, target_terms, summary_by_id), it) for it in images]
            positive_images = [(score, it) for score, it in scored_images if score > 0]
            if qu_mode == "single_best":
                if positive_images:
                    best_score = max(score for score, _ in positive_images)
                    for score, img in positive_images:
                        if score == best_score:
                            return keep_images({cls._canonical_id(img)})
                section_match_ids_for_fallback = {
                    str(sid) for sid in (getattr(plan, "section_match_ids", None) or []) if sid
                }
                if section_match_ids_for_fallback:
                    for img in images:
                        parent_section_id = str((img.get("metadata") or {}).get("parent_section_id") or "")
                        if parent_section_id in section_match_ids_for_fallback:
                            return keep_images({cls._canonical_id(img)})
            else:
                relevant_ids = {cls._canonical_id(img) for _, img in positive_images}
                # same_section 是多图语义：至少识别出两张相关图时才删旁图，避免误删目标图。
                if len(relevant_ids) >= 2 and len(relevant_ids) < len(images):
                    return keep_images(relevant_ids)

        section_match_ids = {str(sid) for sid in (getattr(plan, "section_match_ids", None) or []) if sid}
        if section_match_ids and getattr(plan, "intent", "") in {"outline", "procedure", "image_identification"}:
            section_matched_ids = {
                cls._canonical_id(it)
                for it in images
                if str((it.get("metadata") or {}).get("parent_section_id") or "") in section_match_ids
            }
            if section_matched_ids:
                return keep_images(section_matched_ids)

        if getattr(plan, "intent", "") != "image_identification":
            return selected
        terms = cls._query_core_terms(query) if selected else []
        if not terms:
            return selected  # 抽不出核心词 → 不动（兜底）

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
        return keep_images(relevant_ids)

    @staticmethod
    def _is_image_record(record: Dict[str, Any]) -> bool:
        metadata = record.get("metadata") or {}
        return metadata.get("chunk_type") in {"image", "image_summary"}

    @classmethod
    def _page_records_cached(
        cls,
        vector_service: Any,
        document_id: str,
        page: int,
        chunk_type: Optional[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        if not vector_service or not hasattr(vector_service, "get_page_records"):
            return []
        cache_key = (id(vector_service), str(document_id), int(page), str(chunk_type or "*"))
        cached = cls._PAGE_RECORD_CACHE.get(cache_key)
        if cached is not None:
            return list(cached[:limit])
        try:
            records = vector_service.get_page_records(document_id, page, chunk_type=chunk_type, limit=limit)
        except Exception as exc:
            logger.debug("[page_image_selector] get_page_records failed: %s", exc)
            records = []
        cls._PAGE_RECORD_CACHE[cache_key] = list(records or [])
        return list((records or [])[:limit])

    @classmethod
    def _record_text_for_page_selector(cls, record: Dict[str, Any]) -> str:
        metadata = record.get("metadata") or {}
        return " ".join(
            str(value or "")
            for value in (
                record.get("content"),
                record.get("text"),
                metadata.get("raw_text"),
                metadata.get("section_title"),
                metadata.get("toc_path"),
                metadata.get("caption"),
            )
        )

    @classmethod
    def _document_ids_for_page_selector(
        cls,
        ranked: List[Dict],
        selected: List[Dict],
        document_id: Optional[str],
    ) -> List[str]:
        doc_ids: List[str] = []
        if document_id:
            doc_ids.append(str(document_id))
        for item in list(selected or []) + list(ranked or [])[:30]:
            metadata = item.get("metadata") or {}
            doc_id = metadata.get("document_id")
            if doc_id and str(doc_id) not in doc_ids:
                doc_ids.append(str(doc_id))
        return doc_ids

    @classmethod
    def _apply_page_image_selector(
        cls,
        ranked: List[Dict],
        selected: List[Dict],
        plan,
        query: str,
        query_understanding,
        vector_service: Any,
        document_id: Optional[str] = None,
    ) -> List[Dict]:
        if not selected or not vector_service:
            return selected
        if cls._extract_query_pages(query):
            return selected
        if getattr(query_understanding, "intent", "") != "image_lookup":
            return selected
        query_understanding_confidence = float(getattr(query_understanding, "confidence", 0.0) or 0.0)
        if query_understanding_confidence < 0.75:
            return selected

        image_mode = str(getattr(query_understanding, "image_mode", "") or "same_section")
        selection_mode = str(getattr(query_understanding, "selection_mode", "") or "")
        high_confidence_single_best = (
            image_mode == "single_best" or selection_mode == "single_target"
        ) and query_understanding_confidence >= 0.85
        max_pages = 1 if image_mode == "single_best" or selection_mode == "single_target" else None
        doc_ids = cls._document_ids_for_page_selector(ranked, selected, document_id)
        if not doc_ids:
            return selected

        selected_image_pages = [
            page
            for page in (cls._metadata_page((item.get("metadata") or {})) for item in selected if cls._is_image_record(item))
            if page is not None
        ]
        unique_selected_image_pages = sorted(set(selected_image_pages))
        locked_selector_pages: set[int] = set()
        for item in selected:
            if not cls._is_image_record(item):
                continue
            metadata = item.get("metadata") or {}
            if not metadata.get("page_selector_used"):
                continue
            for page in metadata.get("page_selector_pages") or []:
                try:
                    locked_selector_pages.add(int(page))
                except (TypeError, ValueError):
                    continue
        if locked_selector_pages:
            non_images = [item for item in selected if not cls._is_image_record(item)]
            locked_images = [
                item
                for item in selected
                if cls._is_image_record(item)
                and (cls._metadata_page(item.get("metadata") or {}) in locked_selector_pages)
            ]
            return non_images + locked_images
        current_image_text = " ".join(
            cls._record_text_for_page_selector(item)
            for item in selected
            if cls._is_image_record(item)
        )
        for page in unique_selected_image_pages:
            page_records = cls._page_records_cached(
                vector_service,
                doc_ids[0],
                page,
                chunk_type=None,
                limit=PAGE_SELECTOR_LOOKUP_LIMIT,
            )
            current_image_text += " " + " ".join(cls._record_text_for_page_selector(record) for record in page_records)
        orientation_conflict = any(
            reason.startswith("direction:")
            for reason in candidate_constraint_conflicts(
                extract_query_constraints(query),
                {"content": current_image_text, "metadata": {}},
            )
        )
        selected_has_images = any(cls._is_image_record(item) for item in selected)
        action_missing = False
        action_map = {
            "拆卸": ("拆卸", "拆下", "取下", "松开", "断开", "拉出", "取出"),
            "安装": ("安装", "装上", "装入", "放入", "合上", "拧紧", "套入", "旋入"),
            "检查": ("检查", "测量", "拨动", "转动", "校验"),
        }
        for action, synonyms in action_map.items():
            if action in query and not any(word in current_image_text for word in synonyms):
                action_missing = True
                break
        cross_page_parts_hint = (
            "中" in query
            and any(term in query for term in ("O型圈", "定位销", "垫圈", "齿轮", "链轮", "水泵轴"))
            and any(word in query for word in ("清单", "零件", "部件", "装配"))
        )
        too_many_image_pages = max_pages is not None and len(unique_selected_image_pages) > max_pages
        sparse_image_page_gap = (
            len(unique_selected_image_pages) >= 2
            and (max(unique_selected_image_pages) - min(unique_selected_image_pages)) >= 3
        )
        should_apply = (
            (not selected_has_images)
            or too_many_image_pages
            or orientation_conflict
            or action_missing
            or cross_page_parts_hint
            or sparse_image_page_gap
            or high_confidence_single_best
        )
        if not should_apply:
            return selected

        anchor_pages = cls._strong_text_anchor_pages(ranked, selected, query)
        inventory_query = "清单" in query or "BOM" in query
        specific_inventory_item_query = inventory_query and "中" in query
        anchor_limited_scan = bool(
            high_confidence_single_best
            and anchor_pages
            and (not inventory_query or specific_inventory_item_query)
        )
        full_scan = (not selected_has_images) or orientation_conflict or (high_confidence_single_best and not anchor_limited_scan)

        seed_pages: set[int] = set()
        page_context_by_page: Dict[int, List[str]] = {}
        if anchor_limited_scan:
            for page in anchor_pages:
                seed_pages.update({page - 1, page, page + 1})
        else:
            for item in list(selected or []) + list(ranked or [])[:30]:
                metadata = item.get("metadata") or {}
                page = cls._metadata_page(metadata)
                if page is None:
                    continue
                seed_pages.update({page - 1, page, page + 1})
                if metadata.get("chunk_type") != "step_raw":
                    page_context_by_page.setdefault(page, []).append(cls._record_text_for_page_selector(item))
        seed_pages = {page for page in seed_pages if 1 <= page <= PAGE_SELECTOR_SCAN_LIMIT}
        if not seed_pages:
            full_scan = True

        chosen_images: List[Dict[str, Any]] = []
        selected_page_numbers: List[int] = []
        preferred_section_ids = {
            str(section_id)
            for section_id in (getattr(plan, "section_match_ids", None) or [])
            if section_id
        }
        title_section_query = (
            "章节" in query
            or ("对应" in query and "中" not in query)
        )
        for doc_id in doc_ids[:2]:
            page_evidence: List[PageEvidence] = []
            images_by_page: Dict[int, List[Dict[str, Any]]] = {}
            images_by_page_group: Dict[tuple[int, str], List[Dict[str, Any]]] = {}
            pages_to_scan = range(1, PAGE_SELECTOR_SCAN_LIMIT + 1) if full_scan else sorted(seed_pages)
            for page in pages_to_scan:
                page_records = cls._page_records_cached(
                    vector_service,
                    doc_id,
                    page,
                    chunk_type=None,
                    limit=PAGE_SELECTOR_LOOKUP_LIMIT,
                )
                page_images = cls._page_records_cached(
                    vector_service,
                    doc_id,
                    page,
                    chunk_type="image",
                    limit=IMAGE_LOCATOR_LOOKUP_LIMIT,
                )
                if not page_images:
                    continue
                page_image_summaries = cls._page_records_cached(
                    vector_service,
                    doc_id,
                    page,
                    chunk_type="image_summary",
                    limit=IMAGE_LOCATOR_LOOKUP_LIMIT,
                )
                images_by_page[page] = page_images

                def group_key(record: Dict[str, Any]) -> str:
                    return str((record.get("metadata") or {}).get("parent_section_id") or "")

                groups: List[str] = []
                for record in list(page_images) + list(page_image_summaries):
                    key = group_key(record)
                    if key not in groups:
                        groups.append(key)
                if not groups:
                    groups = [""]

                for group in groups:
                    if (
                        title_section_query
                        and preferred_section_ids
                        and group
                        and group not in preferred_section_ids
                    ):
                        continue
                    group_images = [
                        record for record in page_images
                        if (not group) or group_key(record) == group
                    ]
                    if not group_images:
                        continue
                    group_records = [
                        record for record in page_records
                        if group_key(record) in {"", group}
                    ]
                    group_summaries = [
                        record for record in page_image_summaries
                        if (not group) or group_key(record) == group
                    ]
                    fallback_context = [] if group_records or page_records else list(page_context_by_page.get(page, []))
                    text = " ".join(
                        fallback_context
                        + [cls._record_text_for_page_selector(record) for record in group_records + group_images]
                    )
                    image_text = " ".join(
                        cls._record_text_for_page_selector(record)
                        for record in list(group_summaries) + list(group_images)
                    )
                    page_evidence.append(
                        PageEvidence(
                            page=page,
                            text=text,
                            image_text=image_text,
                            group_key=group,
                            images=group_images,
                        )
                    )
                    images_by_page_group[(page, group)] = group_images

                page_text_fallback_allowed = (
                    cls._has_hard_visual_constraint(query)
                    or ("清单" in query and "中" in query)
                )
                if page_text_fallback_allowed and page_records:
                    page_record_text = " ".join(
                        cls._record_text_for_page_selector(record) for record in page_records
                    )
                    if cls._text_anchor_score(query, page_record_text) >= 4.0:
                        all_image_text = " ".join(
                            cls._record_text_for_page_selector(record)
                            for record in list(page_image_summaries) + list(page_images)
                        )
                        page_group = "__page_text__"
                        page_evidence.append(
                            PageEvidence(
                                page=page,
                                text=" ".join(
                                    [page_record_text]
                                    + [cls._record_text_for_page_selector(record) for record in page_images]
                                ),
                                image_text=all_image_text,
                                group_key=page_group,
                                images=page_images,
                            )
                        )
                        images_by_page_group[(page, page_group)] = page_images

            gate = gated_select_pages_for_image_query(
                query,
                page_evidence,
                original_pages=unique_selected_image_pages,
                image_mode=image_mode,
                max_pages=max_pages,
                force_replace=bool(
                    (not selected_has_images)
                    or too_many_image_pages
                    or orientation_conflict
                    or action_missing
                    or cross_page_parts_hint
                    or high_confidence_single_best
                ),
            )
            pages = gate.selected_pages
            if not gate.gate_triggered:
                continue
            if not pages:
                continue
            selected_page_numbers.extend(pages)
            best_group_by_page: Dict[int, str] = {}
            best_group_score_by_page: Dict[int, float] = {}
            for score in gate.scores:
                if score.page not in pages:
                    continue
                current = best_group_score_by_page.get(score.page, float("-inf"))
                if score.score > current:
                    best_group_score_by_page[score.page] = score.score
                    best_group_by_page[score.page] = score.group_key
            for page in pages:
                page_group = best_group_by_page.get(page, "")
                page_images = images_by_page_group.get((page, page_group)) or images_by_page.get(page, [])
                for image in page_images:
                    item = cls._mark_image_locator_candidate(
                        image,
                        0.96,
                        ["page_selector", f"page:{page}", image_mode],
                    )
                    metadata = dict(item.get("metadata") or {})
                    metadata["page_selector_used"] = True
                    metadata["page_selector_pages"] = pages
                    metadata["page_selector_gate_reason"] = gate.reason
                    metadata["page_selector_free_pages"] = gate.free_selected_pages
                    item["metadata"] = metadata
                    chosen_images.append(item)
            if chosen_images:
                break

        if not chosen_images:
            return selected

        seen: set[str] = set()
        unique_images: List[Dict[str, Any]] = []
        for image in sorted(chosen_images, key=cls._record_order):
            key = cls._canonical_id(image)
            if key in seen:
                continue
            seen.add(key)
            unique_images.append(image)

        non_images = [item for item in selected if not cls._is_image_record(item)]
        return non_images + unique_images

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
        sm_ids = getattr(plan, "section_match_ids", None) or []
        if plan.intent not in {"image_identification", "procedure"} and not (plan.intent == "outline" and sm_ids):
            return []
        if not vector_service:
            return []

        pages = cls._extract_query_pages(query)
        seed_candidates = list(ranked_candidates or [])[: max(limit * 4, 20)]
        document_ids: List[str] = []
        section_keys: List[tuple[str, str]] = []

        # 当 plan.section_match_ids 非空时，直接使用确定性信号作为需要补图的节
        if sm_ids:
            for candidate in seed_candidates:
                metadata = candidate.get("metadata") or {}
                candidate_doc_id = metadata.get("document_id") or document_id
                if candidate_doc_id and candidate_doc_id not in document_ids:
                    document_ids.append(candidate_doc_id)
            if not document_ids and document_id:
                document_ids.append(document_id)
            for doc_id in document_ids:
                for sid in sm_ids:
                    section_key = (doc_id, sid)
                    if section_key not in section_keys:
                        section_keys.append(section_key)
        else:
            # 回退：从 ranked 前 N 名推测需要补图的节
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
        if (pages or plan.intent in {"procedure", "outline"}) and hasattr(vector_service, "get_section_records"):
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
        parent_section_id: str = None,
        allowed_section_ids: List[str] = None,
        allowed_evidence_refs: List[str] = None,
        allowed_source_chunk_uids: List[str] = None,
        pages: List[int] = None,
        _query_contract: Optional[Dict[str, Any]] = None,
        _event_sink: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> List[VectorSearchResult]:
        resolved_sections = tuple(dict.fromkeys(
            str(value).strip() for value in (allowed_section_ids or ()) if str(value).strip()
        ))
        resolved_evidence = tuple(dict.fromkeys(
            str(value).strip() for value in (allowed_evidence_refs or ()) if str(value).strip()
        ))
        resolved_source_uids = tuple(dict.fromkeys(
            str(value).strip() for value in (allowed_source_chunk_uids or ()) if str(value).strip()
        ))
        resolved_pages = tuple(dict.fromkeys(
            int(value) for value in (pages or ()) if str(value).strip().isdigit() and int(value) > 0
        ))
        if parent_section_id and not resolved_sections:
            resolved_sections = (str(parent_section_id),)
        query_understanding = understand_query(query)
        question_aspects = split_question_aspects(query)
        query_variants = build_query_variants(
            query,
            _query_contract,
            aspects=question_aspects,
        )
        variant_vectors = await asyncio.gather(*(
            self._embed_query_vectors(
                variant.text,
                image_urls if variant.source == "original" else None,
            )
            for variant in query_variants
        ))
        query_vectors_by_text = {
            variant.text: vectors for variant, vectors in zip(query_variants, variant_vectors)
        }
        query_vectors = query_vectors_by_text.get(query) or variant_vectors[0]
        # B: 标题命中查找（纯字符串匹配，< 1ms），提前 build 并传入 plan
        section_index = SectionTitleIndex.get_instance()
        try:
            vector_service = get_vector_service()
            section_index.build(vector_service)
        except Exception:
            vector_service = None
        section_match_hits = section_index.find(query)
        if resolved_sections:
            section_match_hits = [
                ref for ref in section_match_hits
                if ref.section_id in resolved_sections
            ]
        sm_ids = list(resolved_sections) if resolved_sections else [ref.section_id for ref in section_match_hits]
        plan = build_retrieval_plan(query, has_images=bool(image_urls), explicit_chunk_type=chunk_type, section_match_ids=sm_ids)
        logger.debug("[knowledge_retrieval] plan.section_match_ids=%s  intent=%s", sm_ids, plan.intent)
        confidence_type = confidence_intent(plan)
        final_top_k = max(int(top_k or 0), 0)
        recall_k = max(final_top_k * 3, DEFAULT_RECALL_TOP_N) if final_top_k else 0
        optional_filter_used = any((category, tags, device_type, document_version, manual_type, parent_section_id))
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
                "queryUnderstanding": query_understanding.to_metadata(),
                "queryVariants": [
                    {
                        "text": variant.text,
                        "source": variant.source,
                        "targetId": variant.target_id,
                    }
                    for variant in query_variants
                ],
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
                parent_section_id=parent_section_id,
            )

        text_vector = query_vectors.get("text_vector")
        image_vector = query_vectors.get("image_vector") or text_vector
        route_cache = CandidateRequestCache()

        async def run_route(
            route: str,
            variant: QueryVariant,
            relaxed: bool = False,
            limit: int = None,
        ) -> List[Dict]:
            route_filter = filter_for_route(route, relaxed=relaxed)
            route_name = self._route_name(route, relaxed=relaxed)
            route_top_k = limit or recall_k
            cache_key = (route, variant.text, relaxed, route_filter, route_top_k)

            def mark_variant(doc: Dict) -> Dict:
                marked = self._mark_route(doc, route_name)
                metadata = marked.setdefault("metadata", {})
                metadata["query_variant_source"] = variant.source
                metadata["query_variant_target_id"] = variant.target_id
                metadata["query_variant_text"] = variant.text
                return marked

            async def fetch_route() -> List[Dict]:
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
                        variant.text,
                        top_k=route_top_k,
                        include_metadata=True,
                        filter=route_filter,
                    )
                    marked = [mark_variant(doc) for doc in docs]
                    await _emit_retrieval_event(
                        _event_sink,
                        "retrieval_route",
                        {
                            "route": route_name,
                            "sourceRoute": route,
                            "candidateCount": len(marked),
                            "limit": route_top_k,
                                "relaxed": relaxed,
                                "queryVariantSource": variant.source,
                                "queryVariantTargetId": variant.target_id,
                            },
                        )
                    return marked

                vectors = query_vectors_by_text[variant.text]
                route_vector = vectors.get("text_vector")
                if route == "image_vector":
                    route_vector = vectors.get("image_vector") or route_vector
                elif route == "image_summary":
                    route_vector = route_vector or vectors.get("image_vector")
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
                marked = [mark_variant(doc) for doc in docs]
                await _emit_retrieval_event(
                    _event_sink,
                    "retrieval_route",
                    {
                        "route": route_name,
                        "sourceRoute": route,
                        "candidateCount": len(marked),
                        "limit": route_top_k,
                        "relaxed": relaxed,
                        "queryVariantSource": variant.source,
                        "queryVariantTargetId": variant.target_id,
                    },
                )
                return marked

            return await route_cache.get_or_fetch(cache_key, fetch_route)

        async def run_route_group(route: str, limit: int = None) -> List[Dict]:
            variant_results = await asyncio.gather(*(
                run_route(route, variant, limit=limit) for variant in query_variants
            ))
            return self._merge_candidates([
                item for result in variant_results for item in result
            ])

        try:
            # vector_service / section_index 已在上面 build 过了，直接复用
            if vector_service is None:
                vector_service = get_vector_service()

            async def fetch_section_match_candidates() -> List[Dict]:
                if resolved_sections and document_id:
                    records = await asyncio.to_thread(
                        self._load_authoritative_scope_records,
                        vector_service,
                        document_id=document_id,
                        allowed_section_ids=resolved_sections,
                        allowed_evidence_refs=resolved_evidence,
                        allowed_source_chunk_uids=resolved_source_uids,
                        pages=resolved_pages,
                        limit=max(30, recall_k),
                    )
                    return [self._mark_route(record, "authoritative_scope") for record in records]
                hits = section_match_hits  # 复用提前计算的匹配结果
                if not hits:
                    return []
                all_records: List[Dict] = []
                # outline 意图（目录/清单）不需要 step_raw 块，避免拆装步骤挤占部件清单
                skip_types = {"step_raw"} if plan.intent == "outline" else set()
                for ref in hits:
                    doc_id = ref.document_id
                    if document_id and document_id != doc_id:
                        continue
                    try:
                        records = await asyncio.to_thread(
                            vector_service.get_section_records,
                            doc_id, ref.section_id, limit=30, chunk_type=None,
                        )
                    except Exception as exc:
                        logger.debug("section_match fetch %s/%s failed: %s", doc_id, ref.section_id, exc)
                        continue
                    for record in records:
                        if skip_types and (record.get("metadata") or {}).get("chunk_type") in skip_types:
                            continue
                        all_records.append(self._mark_route(record, "section_match"))
                return all_records

            route_results = await asyncio.gather(
                *(
                    run_route(route, variant)
                    for route, variant in build_variant_route_pairs(plan.routes, query_variants)
                ),
                fetch_section_match_candidates(),
            )
            candidate_lists = [
                self._filter_to_resolved_scope(
                    list(docs),
                    allowed_section_ids=resolved_sections,
                    allowed_evidence_refs=resolved_evidence,
                    allowed_source_chunk_uids=resolved_source_uids,
                    pages=resolved_pages,
                )
                for docs in route_results
            ]

            if not any(candidate_lists) and optional_filter_used and not document_id:
                logger.info("No evidence matched optional retrieval filters; retrying without inferred metadata filters")
                relaxed_results = await asyncio.gather(*(
                    run_route(route, variant, relaxed=True)
                    for route, variant in build_variant_route_pairs(plan.routes, query_variants)
                ))
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
            merged_with_locator = self._filter_to_resolved_scope(
                merged_with_locator,
                allowed_section_ids=resolved_sections,
                allowed_evidence_refs=resolved_evidence,
                allowed_source_chunk_uids=resolved_source_uids,
                pages=resolved_pages,
            )
            return merged_with_locator, rank_candidates(query, merged_with_locator, plan)

        fused = reciprocal_rank_fusion(
            candidate_lists,
            key_fn=self._canonical_id,
            top_k=recall_k,
            rrf_constant=DEFAULT_RRF_CONSTANT,
        )
        merged = self._filter_candidates_for_plan(self._merge_candidates(fused), plan)
        merged = self._filter_to_resolved_scope(
            merged,
            allowed_section_ids=resolved_sections,
            allowed_evidence_refs=resolved_evidence,
            allowed_source_chunk_uids=resolved_source_uids,
            pages=resolved_pages,
        )
        ranked = rank_candidates(query, merged, plan)
        merged, ranked = apply_image_locator(merged, ranked)
        selected = diversify_candidates(ranked, top_k=final_top_k, intent=confidence_type)

        # 首轮质量与证据覆盖共同决定是否执行唯一一次补召。
        first_quality = evaluate_retrieval_quality(plan, ranked, selected, top_k=final_top_k)
        first_qualification = qualify_candidates(
            query,
            selected,
            document_id=document_id,
            device_type=device_type,
            document_version=document_version,
            manual_type=manual_type,
            requires_strict_evidence=plan.requires_strict_evidence,
            aspects=question_aspects,
            allowed_section_ids=resolved_sections,
            allowed_evidence_refs=resolved_evidence,
            allowed_source_chunk_uids=resolved_source_uids,
        )
        supplemental_decision = decide_supplemental_retrieval(
            plan,
            first_quality,
            coverage_status=first_qualification["coverage_status"],
            missing_aspect_ids=first_qualification["missing_aspect_ids"],
        )
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
                "coverageStatus": first_qualification["coverage_status"],
                "missingAspectIds": first_qualification["missing_aspect_ids"],
                "shouldSupplement": supplemental_decision.should_supplement,
                "supplementalRoutes": list(supplemental_decision.routes),
            },
        )

        supplemental_routes = list(supplemental_decision.routes)
        if supplemental_decision.should_supplement:
            supplemental_limit = max(recall_k * 2, top_k * 6, 6)
            await _emit_retrieval_event(
                _event_sink,
                "retrieval_supplement",
                {
                    "routes": supplemental_routes,
                    "limit": supplemental_limit,
                    "reasons": first_quality.reasons + [supplemental_decision.reason],
                },
            )
            supplemental_stage = await run_supplemental_stage(
                candidate_lists,
                supplemental_decision,
                lambda route: run_route_group(route, limit=supplemental_limit),
            )
            candidate_lists = supplemental_stage.candidate_lists
            supplemental_search_used = supplemental_stage.used
            if supplemental_stage.failed_routes:
                logger.warning(
                    "Supplemental retrieval routes failed; preserving base results: %s",
                    list(supplemental_stage.failed_routes),
                )
            if supplemental_stage.used:
                fused = reciprocal_rank_fusion(
                    candidate_lists,
                    key_fn=self._canonical_id,
                    top_k=max(recall_k, supplemental_limit),
                    rrf_constant=DEFAULT_RRF_CONSTANT,
                )
                merged = self._filter_candidates_for_plan(self._merge_candidates(fused), plan)
                merged = self._filter_to_resolved_scope(
                    merged,
                    allowed_section_ids=resolved_sections,
                    allowed_evidence_refs=resolved_evidence,
                    allowed_source_chunk_uids=resolved_source_uids,
                    pages=resolved_pages,
                )
                ranked = rank_candidates(query, merged, plan)
                merged, ranked = apply_image_locator(merged, ranked)
                selected = diversify_candidates(ranked, top_k=final_top_k, intent=confidence_type)

        selected = self._promote_section_siblings(ranked, selected, final_top_k)
        selected = self._ensure_section_image(ranked, selected, final_top_k, plan)
        selected = self._ensure_section_steps(selected, plan, vector_service)
        selected = self._filter_query_images(
            ranked,
            selected,
            final_top_k,
            plan,
            query,
            query_understanding=query_understanding,
        )
        selected = self._apply_page_image_selector(
            ranked,
            selected,
            plan,
            query,
            query_understanding,
            vector_service,
            document_id=document_id,
        )
        selected = self._filter_to_resolved_scope(
            selected,
            allowed_section_ids=resolved_sections,
            allowed_evidence_refs=resolved_evidence,
            allowed_source_chunk_uids=resolved_source_uids,
            pages=resolved_pages,
        )
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
        expanded_selected = self._ensure_section_image(ranked, expanded_selected, len(expanded_selected), plan)
        expanded_selected = self._filter_query_images(
            ranked,
            expanded_selected,
            len(expanded_selected),
            plan,
            query,
            query_understanding=query_understanding,
        )
        expanded_selected = self._apply_page_image_selector(
            ranked,
            expanded_selected,
            plan,
            query,
            query_understanding,
            vector_service,
            document_id=document_id,
        )
        expanded_selected = self._filter_to_resolved_scope(
            expanded_selected,
            allowed_section_ids=resolved_sections,
            allowed_evidence_refs=resolved_evidence,
            allowed_source_chunk_uids=resolved_source_uids,
            pages=resolved_pages,
        )
        qualification = qualify_candidates(
            query,
            expanded_selected,
            document_id=document_id,
            device_type=device_type,
            document_version=document_version,
            manual_type=manual_type,
            requires_strict_evidence=plan.requires_strict_evidence,
            aspects=question_aspects,
            allowed_section_ids=resolved_sections,
            allowed_evidence_refs=resolved_evidence,
            allowed_source_chunk_uids=resolved_source_uids,
        )
        qualified_docs = qualification["qualified_evidence"]
        reference_docs = qualification["reference_evidence"]
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
        retrieval_stage_trace = self._build_retrieval_stage_trace(
            fused=fused,
            filtered=merged,
            reranked=ranked,
            selected=selected,
            expanded=expanded_selected,
            visible=qualified_docs + reference_docs,
        )
        await _emit_retrieval_event(
            _event_sink,
            "retrieval_stage",
            retrieval_stage_trace,
        )

        results: List[VectorSearchResult] = []
        visible_docs = qualified_docs + reference_docs
        for doc in visible_docs:
            metadata = dict(doc.get("metadata") or {})
            if metadata.get("chunk_type") == "image_summary":
                metadata["source_chunk_type"] = "image_summary"
                metadata["chunk_type"] = "image"
            routes = sorted(set(doc.get("routes") or []))
            metadata["retrieval_routes"] = routes
            metadata["matched_types"] = confidence["matched_types"]
            metadata["retrieval_confidence"] = confidence["confidence"]
            metadata["retrieval_plan_intent"] = plan.intent
            metadata["query_understanding"] = query_understanding.to_metadata()
            metadata["query_understanding_intent"] = query_understanding.intent
            metadata["query_understanding_image_mode"] = query_understanding.image_mode
            metadata["query_understanding_selection_mode"] = query_understanding.selection_mode
            metadata["query_understanding_confidence"] = query_understanding.confidence
            # 透传 section_match_ids 给下游（api/main.py 直取通道 + review_agent 步骤校验依赖此信号）
            metadata["section_match_ids"] = sm_ids
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
            metadata["retrieval_stage_trace"] = retrieval_stage_trace
            metadata["confidence_reason"] = {
                "best_relevance_score": confidence["best_relevance_score"],
                "candidate_count": confidence["candidate_count"],
                "dual_image_hit": confidence["dual_image_hit"],
                "intent": plan.intent,
                "confidence_intent": confidence_type,
                "first_pass_quality": first_quality.grade,
                "final_quality": final_quality.grade,
            }
            metadata["evidence_bundle"] = {
                key: value
                for key, value in qualification.items()
                if key not in {"qualified_evidence", "reference_evidence"}
            }
            metadata["evidence_overall_status"] = qualification["overall_status"]
            metadata["evidence_capabilities"] = qualification["capabilities"]
            if metadata.get("qualification") != "qualified":
                metadata["answer_policy"] = "reference_only"
            elif confidence["confidence"] == "low" or final_quality.grade == "low":
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
