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
import threading
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from services.retrieval.query_constraints import extract_query_constraints
from services.retrieval.procedure_scope import procedure_scope_from_metadata

logger = logging.getLogger(__name__)

CHINESE_RE = re.compile(r"[一-鿿]+")

# 节标题核心词最少中文字符数
MIN_CORE_LENGTH = 2

# 一次查询最多强召的章节数
MAX_SECTIONS_PER_QUERY = 3

# 命中章节数超过此值为泛词，不强召
GENERIC_WORD_THRESHOLD = 5
# 单个 ngram 命中 section 数超过此值 → 该 ngram 判定为泛词噪声，不计分
NGRAM_NOISE_THRESHOLD = 5

# 标题字符按顺序出现在 query 中的最低覆盖率。
# 用于处理“主副轴”被改写成“主轴与副轴”等自然表达。
ORDERED_TITLE_COVERAGE_THRESHOLD = 0.80
ATOMIC_TARGET_COVERAGE_THRESHOLD = 0.25
ACTION_TITLE_ALIASES = {
    "安装": ("安装", "装配", "装上", "装"),
    "拆卸": ("拆卸", "拆除", "拆下", "拆"),
    "检查": ("检查", "检修", "查看", "看"),
    "测量": ("测量", "检测", "测试", "测"),
    "更换": ("更换", "替换", "换"),
    "调整": ("调整", "调节", "校正"),
}
_TRAILING_ACTION_REQUEST_RE = re.compile(
    r"(?:"
    r"(?:怎么|如何|怎样)(?:进行)?(?:安装|装配|拆卸|拆除|检查|检修|查看|测量|检测|测试|更换|替换|调整|调节|校正)(?:一下)?"
    r"|(?:安装|装配|拆卸|拆除|检查|检修|查看|测量|检测|测试|更换|替换|调整|调节|校正)(?:的)?(?:步骤|流程|方法|要求|注意事项)"
    r")$"
)


def _compact_chinese(text: str) -> str:
    return "".join(CHINESE_RE.findall(text or ""))


def _compact_evidence(text: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return re.sub(r"[^0-9a-z一-鿿]+", "", normalized)


def _is_atomic_target_statement(raw_text: str, anchors: List[str]) -> bool:
    """Whether one source clause is primarily about the extracted target."""
    if not raw_text or not anchors:
        return False
    anchor_size = sum(len(anchor) for anchor in dict.fromkeys(anchors))
    for clause in re.split(r"[\r\n。；;！？!?]+", raw_text):
        compact_clause = _compact_evidence(clause)
        if not compact_clause or not all(anchor in compact_clause for anchor in anchors):
            continue
        if anchor_size / len(compact_clause) >= ATOMIC_TARGET_COVERAGE_THRESHOLD:
            return True
    return False


def _strip_trailing_action_request(compact_query: str) -> str:
    """Remove request grammar from an entity-first query before stem scoring."""
    stripped = _TRAILING_ACTION_REQUEST_RE.sub("", compact_query or "")
    return stripped or compact_query


def _lcs_length(left: str, right: str) -> int:
    """Return longest common subsequence length for two short Chinese strings."""
    if not left or not right:
        return 0
    previous = [0] * (len(right) + 1)
    for left_char in left:
        current = [0]
        for index, right_char in enumerate(right, start=1):
            if left_char == right_char:
                current.append(previous[index - 1] + 1)
            else:
                current.append(max(previous[index], current[-1]))
        previous = current
    return previous[-1]


def _query_has_action_alias(compact_query: str, obj: str, aliases: tuple[str, ...]) -> bool:
    for alias in aliases:
        if len(alias) > 1 and alias in compact_query:
            return True
        if len(alias) == 1 and alias in compact_query.replace(obj, ""):
            return True
    return False


@dataclass(frozen=True)
class SectionRef:
    """一个章节引用——标题索引中的一条记录。"""

    section_id: str
    document_id: str
    core_title: str  # 去章号、去空白后的中文核心词，如 "气缸头"
    full_title: str  # 原样节标题，如 "4.7 气缸头"
    procedure_action: str = ""
    procedure_target: str = ""
    assembly_context: str = ""
    orientation: str = ""
    part_name: str = ""
    parameter_field: str = ""
    evidence_refs: tuple[str, ...] = ()
    pages: tuple[int, ...] = ()
    retrieval_score: float = 0.0


class SectionTitleIndex:
    """内存级章节标题 → section_id 映射，支持精确匹配 + ngram 模糊匹配。

    在手册导入后调用 build() 一次性构造；之后只读查询。
    """

    _instance: Optional["SectionTitleIndex"] = None

    def __init__(self) -> None:
        self._exact: Dict[str, List[SectionRef]] = {}  # core_title → refs
        self._ngram: Dict[str, List[SectionRef]] = {}  # 2-3gram → refs
        self._section_refs: Dict[str, SectionRef] = {}
        self._section_evidence: Dict[str, List[str]] = {}
        self._section_contexts: Dict[str, List[str]] = {}
        self._section_records: Dict[str, List[Dict[str, str]]] = {}
        self._built = False
        self._build_lock = threading.Lock()

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
        """Build the index once, even when the first requests arrive concurrently."""
        if self._built:
            return
        with self._build_lock:
            if self._built:
                return
            self._build_unlocked(vector_service)

    def _build_unlocked(self, vector_service: Any) -> None:
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
                        "RETURN", "4", "metadata", "document_id", "id", "text",
                        "DIALECT", "2",
                    )
                except Exception as exc:
                    logger.warning("SectionTitleIndex build scan failed at cursor=%s: %s", cursor, exc)
                    break

                if not raw or len(raw) <= 1:
                    break

                total_count = raw[0] if isinstance(raw[0], int) else 0
                returned_count = max(0, (len(raw) - 1) // 2)
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
                    evidence_text = _compact_evidence(field_dict.get("text", ""))
                    if evidence_text:
                        self._section_evidence.setdefault(key, []).append(evidence_text)
                    procedure_scope = procedure_scope_from_metadata(metadata)
                    scope_action = procedure_scope.action if procedure_scope is not None else ""
                    scope_target = procedure_scope.target if procedure_scope is not None else ""
                    scope_heading = procedure_scope.heading if procedure_scope is not None else ""
                    context = " ".join(
                        str(value or "")
                        for value in (
                            scope_action,
                            scope_target,
                            scope_heading,
                            metadata.get("procedure_action"),
                            metadata.get("procedure_target"),
                            metadata.get("assembly_context"),
                            metadata.get("orientation"),
                        )
                    )
                    compact_context = _compact_evidence(context)
                    self._section_records.setdefault(key, []).append({
                        "record_id": str(field_dict.get("id") or metadata.get("id") or metadata.get("chunk_id") or ""),
                        "page": str(metadata.get("page_number") or metadata.get("page") or ""),
                        "raw_text": field_dict.get("text", ""),
                        "text": evidence_text,
                        "context": compact_context,
                        "target": _compact_evidence(scope_target),
                        "procedure_action": str(metadata.get("procedure_action") or scope_action or "").strip(),
                        "procedure_target": str(metadata.get("procedure_target") or scope_target or "").strip(),
                        "assembly_context": str(metadata.get("assembly_context") or "").strip(),
                        "orientation": str(metadata.get("orientation") or "").strip(),
                        "part_name": str(metadata.get("part_name") or "").strip(),
                        "parameter_field": str(metadata.get("parameter_field") or "").strip(),
                        "parameter_query_candidate": bool(metadata.get("parameter_query_candidate")),
                    })
                    if procedure_scope is not None:
                        self._section_contexts.setdefault(key, []).append(
                            compact_context
                        )

                cursor += returned_count
                total_scanned += returned_count
                if returned_count == 0 or cursor >= total_count:
                    break

            # 建索引
            count = 0
            for _key, (core_title, full_title, doc_id) in seen_sections.items():
                parent_section_id = _key.split(":", 1)[1] if ":" in _key else ""
                records = tuple(self._section_records.get(_key, ()))

                def unique_record_value(field: str) -> str:
                    values = tuple(dict.fromkeys(
                        str(record.get(field) or "").strip()
                        for record in records
                        if str(record.get(field) or "").strip()
                    ))
                    return values[0] if len(values) == 1 else ""

                evidence_refs = tuple(dict.fromkeys(
                    str(record.get("record_id") or "").strip()
                    for record in records
                    if str(record.get("record_id") or "").strip()
                ))
                pages = tuple(dict.fromkeys(
                    int(str(record.get("page") or "").strip())
                    for record in records
                    if str(record.get("page") or "").strip().isdigit()
                ))

                ref = SectionRef(
                    section_id=parent_section_id,
                    document_id=doc_id,
                    core_title=core_title,
                    full_title=full_title,
                    procedure_action=unique_record_value("procedure_action"),
                    procedure_target=unique_record_value("procedure_target"),
                    assembly_context=unique_record_value("assembly_context"),
                    orientation=unique_record_value("orientation"),
                    part_name=unique_record_value("part_name"),
                    parameter_field=unique_record_value("parameter_field"),
                    evidence_refs=evidence_refs,
                    pages=pages,
                )
                self._section_refs[_key] = ref

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

    def refs_for_scope(
        self,
        *,
        document_id: str,
        section_ids: Iterable[str],
    ) -> List[SectionRef]:
        """Return only imported section references inside an explicit scope."""
        allowed = {str(value).strip() for value in section_ids if str(value).strip()}
        if not self._built or not document_id or not allowed:
            return []
        return [
            ref
            for ref in self._section_refs.values()
            if ref.document_id == document_id and ref.section_id in allowed
        ]

    def find_evidence(self, contract: Any) -> List[SectionRef]:
        """Locate sections whose imported body proves an open-vocabulary target.

        Candidate anchors come only from the structured query contract. Every
        distinct anchor must occur in the same imported section; no domain word
        list, regex vocabulary, or hand-tuned keyword weight is involved.
        """
        if not self._built:
            return []
        anchors: List[str] = []
        strong_anchors: List[str] = []
        strong_values = [getattr(contract, "part_spec", "")]
        values = [
            getattr(contract, "part_spec", ""),
            getattr(contract, "raw_component_span", ""),
            getattr(contract, "component", ""),
        ]
        for target in tuple(getattr(contract, "targets", ()) or ()):
            strong_values.append(getattr(target, "part_spec", ""))
            values.extend((
                getattr(target, "part_spec", ""),
                getattr(target, "raw_component_span", ""),
                getattr(target, "component", ""),
            ))
        for value in values:
            normalized = _compact_evidence(value)
            if len(normalized) >= 2 and normalized not in anchors:
                anchors.append(normalized)
        for value in strong_values:
            normalized = _compact_evidence(value)
            if len(normalized) >= 2 and normalized not in strong_anchors:
                strong_anchors.append(normalized)
        if not anchors:
            return []
        required_anchors = strong_anchors or anchors

        requested_fields = {
            _compact_evidence(value)
            for value in (getattr(contract, "requested_fields", ()) or ())
            if _compact_evidence(value)
        }

        def supports_parameter_request(record: Dict[str, Any]) -> bool:
            if bool(record.get("parameter_query_candidate")):
                return True
            record_text = str(record.get("text") or "")
            return bool(
                requested_fields
                and any(field in record_text for field in requested_fields)
            )
        contract_action = _compact_evidence(getattr(contract, "action", ""))
        action_values = [
            getattr(contract, "action", "")
        ] if not (
            getattr(contract, "task_action", "") == "parameter_lookup"
            and contract_action
            and contract_action in requested_fields
        ) else []
        contextual_values = [
            getattr(contract, "assembly_context", ""),
            getattr(contract, "orientation", ""),
        ]
        for target in tuple(getattr(contract, "targets", ()) or ()):
            target_action = _compact_evidence(getattr(target, "action", ""))
            if not (
                getattr(contract, "task_action", "") == "parameter_lookup"
                and target_action
                and target_action in requested_fields
            ):
                action_values.append(getattr(target, "action", ""))
            contextual_values.extend((
                getattr(target, "assembly_context", ""),
                getattr(target, "orientation", ""),
            ))
        action_anchors: List[str] = []
        for value in action_values:
            normalized = _compact_evidence(value)
            if len(normalized) >= 2 and normalized not in action_anchors:
                action_anchors.append(normalized)
        contextual_anchors: List[str] = []
        for value in contextual_values:
            normalized = _compact_evidence(value)
            if len(normalized) >= 2 and normalized not in contextual_anchors:
                contextual_anchors.append(normalized)
        if action_anchors or contextual_anchors:
            contextual_matches = []
            for key, records in self._section_records.items():
                ref = self._section_refs.get(key)
                if ref is None:
                    continue
                section_target = _compact_evidence(ref.full_title or ref.core_title)
                for record in records:
                    record_context = f"{record['context']}{record['text']}"
                    if action_anchors and (
                        not record["context"]
                        or not all(anchor in record["context"] for anchor in action_anchors)
                    ):
                        continue
                    contextual_text = record_context
                    if getattr(contract, "task_action", "") == "parameter_lookup":
                        contextual_text = f"{section_target}{contextual_text}"
                    if not all(anchor in contextual_text for anchor in contextual_anchors):
                        continue
                    structured_target = f"{section_target}{record['target']}"
                    parameter_record_match = bool(
                        getattr(contract, "task_action", "") == "parameter_lookup"
                        and supports_parameter_request(record)
                        and all(anchor in record["text"] for anchor in required_anchors)
                    )
                    if (
                        all(anchor in structured_target for anchor in required_anchors)
                        or _is_atomic_target_statement(record["raw_text"], required_anchors)
                        or parameter_record_match
                    ):
                        contextual_matches.append(ref)
                        break
            if contextual_matches:
                return contextual_matches
            # A structured action/context was requested but no imported
            # evidence proves it. Do not reopen the object-only candidate set.
            return []

        matched_evidence: List[SectionRef] = []
        for key, fragments in self._section_evidence.items():
            evidence = "".join(fragments)
            contexts = self._section_contexts.get(key, [])
            if getattr(contract, "task_action", "") == "parameter_lookup":
                matched = any(
                    supports_parameter_request(record)
                    and all(anchor in str(record.get("text") or "") for anchor in required_anchors)
                    for record in self._section_records.get(key, ())
                )
            else:
                matched = (
                    all(anchor in evidence for anchor in required_anchors)
                    or any(all(anchor in context for anchor in required_anchors) for context in contexts)
                )
            if matched:
                ref = self._section_refs.get(key)
                if ref is not None:
                    matched_evidence.append(ref)
        return matched_evidence

    # ---- query ------------------------------------------------------------

    def find_exact(self, query: str) -> List[SectionRef]:
        """Return maximal section titles embedded verbatim in the query."""
        if not self._built or not query:
            return []
        compact_query = _compact_chinese(query)
        matches: List[SectionRef] = []
        for core_title, refs in self._exact.items():
            compact_core = _compact_chinese(core_title)
            if len(compact_core) >= MIN_CORE_LENGTH and compact_core in compact_query:
                matches.extend(refs)
        maximal: List[SectionRef] = []
        for ref in sorted(matches, key=lambda item: len(_compact_chinese(item.core_title)), reverse=True):
            core = _compact_chinese(ref.core_title)
            if any(
                core != other and core in other
                for item in matches
                if (other := _compact_chinese(item.core_title))
            ):
                continue
            if ref not in maximal:
                maximal.append(ref)
        return maximal[:MAX_SECTIONS_PER_QUERY]

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

        # 强命中：章节核心标题完整出现在自然问句里。
        #
        # 例如：
        #   query = "给我展示传动主副轴装配部件清单"
        #   core  = "传动主副轴装配部件清单"
        #
        # 旧逻辑只拿整段 query 和 2/3-gram 去索引里查，完整标题前面多了
        # "给我展示" 时不会精确命中；而 "装配/部件清单" 又容易被泛词过滤。
        # 这里先按完整标题子串收窄，命中后直接返回，避免相邻清单章节混入。
        compact_query = _compact_chinese(query)
        stem_query = _strip_trailing_action_request(compact_query)
        embedded_exact: Dict[str, tuple[SectionRef, int]] = {}
        for core_title, refs in self._exact.items():
            compact_core = _compact_chinese(core_title)
            if len(compact_core) < MIN_CORE_LENGTH or compact_core not in compact_query:
                continue
            score = len(compact_core) * 3
            for ref in refs:
                k = _key(ref)
                if k not in embedded_exact or embedded_exact[k][1] < score:
                    embedded_exact[k] = (ref, score)

        longest_exact_length = max(
            (len(_compact_chinese(ref.core_title)) for ref, _score in embedded_exact.values()),
            default=0,
        )
        longer_shared_prefix_exists = False
        if embedded_exact:
            for core_title in self._exact:
                compact_core = _compact_chinese(core_title)
                prefix_length = 0
                for left, right in zip(compact_core, stem_query):
                    if left != right:
                        break
                    prefix_length += 1
                if prefix_length <= longest_exact_length or prefix_length < 6:
                    continue
                if min(
                    prefix_length / max(len(compact_core), 1),
                    prefix_length / max(len(stem_query), 1),
                ) >= 0.60:
                    longer_shared_prefix_exists = True
                    break

        if embedded_exact and not longer_shared_prefix_exists:
            sorted_hits = sorted(embedded_exact.values(), key=lambda x: x[1], reverse=True)
            maximal_hits = []
            for ref, score in sorted_hits:
                core = _compact_chinese(ref.core_title)
                if any(
                    core != other_core and core in other_core
                    for other_ref, _other_score in sorted_hits
                    if (other_core := _compact_chinese(other_ref.core_title))
                ):
                    continue
                maximal_hits.append((ref, score))
            return [ref for ref, _score in maximal_hits[:MAX_SECTIONS_PER_QUERY]]

        shared_entity_stems: Dict[str, tuple[SectionRef, int]] = {}
        for core_title, refs in self._exact.items():
            compact_core = _compact_chinese(core_title)
            if len(compact_core) < 6 or len(stem_query) < 6:
                continue
            prefix_length = 0
            for left, right in zip(compact_core, stem_query):
                if left != right:
                    break
                prefix_length += 1
            core_coverage = prefix_length / len(compact_core)
            query_coverage = prefix_length / len(stem_query)
            if prefix_length < 6 or min(core_coverage, query_coverage) < 0.60:
                continue
            score = 4000 + prefix_length * 20 + int(min(core_coverage, query_coverage) * 100)
            for ref in refs:
                k = _key(ref)
                if k not in shared_entity_stems or shared_entity_stems[k][1] < score:
                    shared_entity_stems[k] = (ref, score)

        if shared_entity_stems:
            sorted_hits = sorted(shared_entity_stems.values(), key=lambda x: x[1], reverse=True)
            best_score = sorted_hits[0][1]
            strong_hits = [
                (ref, score) for ref, score in sorted_hits
                if score >= best_score - 50
            ]
            return [ref for ref, _score in strong_hits[:MAX_SECTIONS_PER_QUERY]]

        embedded_exact = {}
        for core_title, refs in self._exact.items():
            compact_core = _compact_chinese(core_title)
            if len(compact_core) < MIN_CORE_LENGTH or compact_core not in compact_query:
                continue
            score = len(compact_core) * 3
            for ref in refs:
                k = _key(ref)
                if k not in embedded_exact or embedded_exact[k][1] < score:
                    embedded_exact[k] = (ref, score)

        if embedded_exact:
            sorted_hits = sorted(embedded_exact.values(), key=lambda x: x[1], reverse=True)
            return [ref for ref, _score in sorted_hits[:MAX_SECTIONS_PER_QUERY]]

        constraints = extract_query_constraints(query)
        if constraints.required_terms:
            entity_matches: Dict[str, tuple[SectionRef, int]] = {}
            required_terms = tuple(_compact_chinese(term) for term in constraints.required_terms)
            forbidden_terms = tuple(_compact_chinese(term) for term in constraints.forbidden_terms)
            for core_title, refs in self._exact.items():
                compact_core = _compact_chinese(core_title)
                if not all(term and term in compact_core for term in required_terms):
                    continue
                if any(term and term in compact_core for term in forbidden_terms):
                    continue
                score = 3000 + sum(len(term) for term in required_terms) * 10
                for ref in refs:
                    k = _key(ref)
                    entity_matches[k] = (ref, score)
            if entity_matches:
                sorted_hits = sorted(entity_matches.values(), key=lambda x: x[1], reverse=True)
                return [ref for ref, _score in sorted_hits[:MAX_SECTIONS_PER_QUERY]]

        object_first_action_matches: Dict[str, tuple[SectionRef, int]] = {}
        for core_title, refs in self._exact.items():
            compact_core = _compact_chinese(core_title)
            matched_action = next(
                (action for action in ACTION_TITLE_ALIASES if compact_core.startswith(action)),
                "",
            )
            if not matched_action:
                continue
            obj = compact_core[len(matched_action):]
            if len(obj) < MIN_CORE_LENGTH:
                continue
            action_aliases = ACTION_TITLE_ALIASES.get(matched_action, (matched_action,))
            if not _query_has_action_alias(compact_query, obj, action_aliases) or obj not in compact_query:
                continue
            score = 2000 + len(obj) * 3 + len(matched_action)
            for ref in refs:
                k = _key(ref)
                if k not in object_first_action_matches or object_first_action_matches[k][1] < score:
                    object_first_action_matches[k] = (ref, score)

        if object_first_action_matches:
            sorted_hits = sorted(object_first_action_matches.values(), key=lambda x: x[1], reverse=True)
            best_score = sorted_hits[0][1]
            strong_hits = [
                (ref, score) for ref, score in sorted_hits
                if score >= best_score - 50
            ]
            return [ref for ref, _score in strong_hits[:MAX_SECTIONS_PER_QUERY]]

        ordered_title_matches: Dict[str, tuple[SectionRef, int]] = {}
        for core_title, refs in self._exact.items():
            compact_core = _compact_chinese(core_title)
            if len(compact_core) < 4:
                continue
            if compact_core[-1] not in compact_query and not compact_core.startswith(compact_query):
                continue
            matched = _lcs_length(compact_core, compact_query)
            coverage = matched / len(compact_core)
            if matched < 4 or coverage < ORDERED_TITLE_COVERAGE_THRESHOLD:
                continue
            score = int(coverage * 1000) + len(compact_core)
            for ref in refs:
                k = _key(ref)
                if k not in ordered_title_matches or ordered_title_matches[k][1] < score:
                    ordered_title_matches[k] = (ref, score)

        if ordered_title_matches:
            sorted_hits = sorted(ordered_title_matches.values(), key=lambda x: x[1], reverse=True)
            best_score = sorted_hits[0][1]
            strong_hits = [
                (ref, score) for ref, score in sorted_hits
                if score >= best_score - 50
            ]
            return [ref for ref, _score in strong_hits[:MAX_SECTIONS_PER_QUERY]]

        for chunk in set(query_chunks):
            if len(chunk) < MIN_CORE_LENGTH:
                continue

            # 第一轮：精确匹配（分数 = 词长 × 3，完整词匹配信号更强）
            for ref in self._exact.get(chunk, []):
                k = _key(ref)
                score = len(chunk) * 3
                if k not in scored or scored[k][1] < score:
                    scored[k] = (ref, score)

            # 第二轮：ngram 模糊匹配（精确未命中才走，泛词噪声跳过）
            ngram_refs = self._ngram.get(chunk, [])
            if len(ngram_refs) > NGRAM_NOISE_THRESHOLD:
                continue  # 该 ngram 命中太多 section → 泛词，不计分
            for ref in ngram_refs:
                k = _key(ref)
                if k in scored:
                    continue
                score = len(chunk)
                if k not in scored or scored[k][1] < score:
                    scored[k] = (ref, score)

        # ---- 特异性检查 ----
        if len(scored) > GENERIC_WORD_THRESHOLD:
            # 命中章节太多 → 取 top 高分（≥6），如果也没有则降级取所有，不直接返回空
            high_score_hits = {k: v for k, v in scored.items() if v[1] >= 6}
            if high_score_hits:
                sorted_hits = sorted(high_score_hits.values(), key=lambda x: x[1], reverse=True)
                return [ref for ref, _score in sorted_hits[:MAX_SECTIONS_PER_QUERY]]
            # 都没有高分命中 → 保留所有，不做强召（泛词场景）
            return []

        # 按分数降序取 top MAX_SECTIONS_PER_QUERY
        sorted_hits = sorted(scored.values(), key=lambda x: x[1], reverse=True)
        return [ref for ref, _score in sorted_hits[:MAX_SECTIONS_PER_QUERY]]
