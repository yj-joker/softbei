"""Query-focused section title index for title-match forced recall.

在向量召回之前，先翻目录——如果 query 中的词能命中手册章节标题，
直接把那一节的全部 chunk 注入召回池，不让弱信号章节被向量检索淹没。

Design:
  精确索引: core_title → [SectionRef]    (逐字匹配)
  模糊索引: 2~3 字 ngram → [SectionRef]  (容错错别字，如"汽缸头"→"气缸头")
  特异性检查: 命中 >5 个章节 → 泛词，不强召 (如"发动机")
  上限: 最多强召 3 个章节
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CHINESE_RE = re.compile(r"[一-鿿]+")

# 节标题核心词最少中文字符数
MIN_CORE_LENGTH = 2

# 一次查询最多强召的章节数
MAX_SECTIONS_PER_QUERY = 3

# 命中章节数超过此值为泛词，不强召
GENERIC_WORD_THRESHOLD = 5


@dataclass(frozen=True)
class SectionRef:
    """一个章节引用——标题索引中的一条记录。"""

    section_id: str
    document_id: str
    core_title: str  # 去章号、去空白后的中文核心词，如 "气缸头"
    full_title: str  # 原样节标题，如 "4.7 气缸头"


class SectionTitleIndex:
    """内存级章节标题 → section_id 映射，支持精确匹配 + ngram 模糊匹配。

    在手册导入后调用 build() 一次性构造；之后只读查询。
    """

    _instance: Optional["SectionTitleIndex"] = None

    def __init__(self) -> None:
        self._exact: Dict[str, List[SectionRef]] = {}  # core_title → refs
        self._ngram: Dict[str, List[SectionRef]] = {}  # 2-3gram → refs
        self._built = False

    # ---- singleton --------------------------------------------------------

    @classmethod
    def get_instance(cls) -> "SectionTitleIndex":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Clear singleton cache. Call after code changes so next request rebuilds."""
        cls._instance = None

    # ---- build ------------------------------------------------------------

    def build(self, vector_service: Any) -> None:
        """从所有 chunk 的 metadata 聚合章节标题索引（首次调用时一次性构造）。

        不依赖 outline chunk——直接扫描全部 manual chunk，
        用 parent_section_id 去重，每个节取最长 section_title。

        失败时静默降级——索引没建成不影响主检索流程。
        """
        if self._built:
            return

        try:
            # 分页扫描全部 manual chunk（每次 1000 条）
            cursor = 0
            seen_sections: Dict[str, tuple[str, str, str]] = {}  # parent_section_id → (core_title, full_title, doc_id)
            total_scanned = 0

            while True:
                try:
                    raw = vector_service.redis.execute_command(
                        "FT.SEARCH",
                        vector_service.INDEX_NAME,
                        "@record_type:{manual}",
                        "LIMIT", str(cursor), "1000",
                        "RETURN", "3", "metadata", "document_id", "id",
                        "DIALECT", "2",
                    )
                except Exception as exc:
                    logger.warning("SectionTitleIndex build scan failed at cursor=%s: %s", cursor, exc)
                    break

                if not raw or len(raw) <= 1:
                    break

                page_count = raw[0] if isinstance(raw[0], int) else 0
                for i in range(1, len(raw), 2):
                    fields = raw[i + 1]
                    field_dict: Dict[str, str] = {}
                    for j in range(0, len(fields), 2):
                        k = fields[j]
                        v = fields[j + 1]
                        try:
                            ks = k.decode("utf-8") if isinstance(k, bytes) else str(k)
                            vs = v.decode("utf-8") if isinstance(v, bytes) else str(v)
                            field_dict[ks] = vs
                        except UnicodeDecodeError:
                            continue

                    # 解析 metadata JSON
                    metadata_raw = field_dict.get("metadata", "{}")
                    try:
                        metadata = json.loads(metadata_raw)
                    except (json.JSONDecodeError, TypeError):
                        continue

                    section_title = str(metadata.get("section_title") or "").strip()
                    if not section_title:
                        continue

                    parent_section_id = str(metadata.get("parent_section_id") or "")
                    doc_id = field_dict.get("document_id", "")

                    # 去章号 → 核心词。e.g. "4.7 气缸头" → "气缸头"
                    core_title = re.sub(r"^[\s\d.、/\\\-]+", "", section_title).strip()
                    if len(core_title) < MIN_CORE_LENGTH:
                        continue

                    # 同一节保留最长标题
                    key = f"{doc_id}:{parent_section_id}"
                    existing = seen_sections.get(key)
                    if existing is None or len(core_title) > len(existing[0]):
                        seen_sections[key] = (core_title, section_title, doc_id)

                cursor += page_count
                total_scanned += page_count
                if cursor >= page_count:
                    break

            # 建索引
            count = 0
            for _key, (core_title, full_title, doc_id) in seen_sections.items():
                parent_section_id = _key.split(":", 1)[1] if ":" in _key else ""

                ref = SectionRef(
                    section_id=parent_section_id,
                    document_id=doc_id,
                    core_title=core_title,
                    full_title=full_title,
                )

                # 精确索引
                self._exact.setdefault(core_title, []).append(ref)

                # ngram 索引：2 字 + 3 字滑动窗口
                seen: set[str] = set()
                for seg in CHINESE_RE.findall(core_title):
                    for n in (2, 3):
                        if len(seg) < n:
                            continue
                        for k in range(len(seg) - n + 1):
                            gram = seg[k : k + n]
                            if gram not in seen:
                                seen.add(gram)
                                self._ngram.setdefault(gram, []).append(ref)

                count += 1

            self._built = True
            logger.info(
                "SectionTitleIndex built: scanned %d manual chunks → %d unique sections → %d exact keys, %d ngram keys",
                total_scanned, count, len(self._exact), len(self._ngram),
            )

        except Exception as exc:
            logger.warning("SectionTitleIndex build failed: %s", exc)
            self._built = True

    # ---- query ------------------------------------------------------------

    def find(self, query: str) -> List[SectionRef]:
        """从 query 中找出命中的章节引用。

        两步匹配：
          1. 精确 — query 中的中文片段逐字等于某个 core_title
          2. 模糊 — query 的 ngram 命中索引中的 ngram（容错错别字）

        特异性保护：命中章节 > GENERIC_WORD_THRESHOLD → 判定为泛词，返回空。
        """
        if not self._built or not query:
            return []

        # 抽取 query 中的中文片段：完整词 + ngram
        query_chunks: List[str] = []
        for seg in CHINESE_RE.findall(query):
            if len(seg) >= MIN_CORE_LENGTH:
                query_chunks.append(seg)
            for n in (2, 3):
                if len(seg) >= n:
                    for k in range(len(seg) - n + 1):
                        query_chunks.append(seg[k : k + n])

        if not query_chunks:
            return []

        scored: Dict[str, tuple[SectionRef, int]] = {}  # key="doc_id:section_id" → (ref, score)

        def _key(ref: SectionRef) -> str:
            return f"{ref.document_id}:{ref.section_id}"

        for chunk in set(query_chunks):
            if len(chunk) < MIN_CORE_LENGTH:
                continue

            # 第一轮：精确匹配（分数 = 词长 × 3，完整词匹配信号更强）
            for ref in self._exact.get(chunk, []):
                k = _key(ref)
                score = len(chunk) * 3
                if k not in scored or scored[k][1] < score:
                    scored[k] = (ref, score)

            # 第二轮：ngram 模糊匹配（精确未命中才走）
            for ref in self._ngram.get(chunk, []):
                k = _key(ref)
                if k in scored:
                    continue
                score = len(chunk)
                if k not in scored or scored[k][1] < score:
                    scored[k] = (ref, score)

        # ---- 特异性检查 ----
        if len(scored) > GENERIC_WORD_THRESHOLD:
            # 命中章节太多 → 泛词，不强召，让向量检索自然竞争
            return []

        # 按分数降序取 top MAX_SECTIONS_PER_QUERY
        sorted_hits = sorted(scored.values(), key=lambda x: x[1], reverse=True)
        return [ref for ref, _score in sorted_hits[:MAX_SECTIONS_PER_QUERY]]
