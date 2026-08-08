"""Deterministic response planning and final fact auditing."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping

from services.retrieval.aspects import QuestionAspect, canonical_aspect_text, split_question_aspects
from services.retrieval.evidence import EvidenceLedger, determine_coverage
from services.retrieval.evidence_fusion import fuse_evidence_support
from services.retrieval.provenance import dedupe_and_sort_manual_records
from services.pending_clarification import build_evidence_conflict_clarification


_MANUAL_LEADS = ("根据手册", "依据手册", "按照手册", "根据资料", "依据资料")
_UNSUPPORTED_GUESS_MARKERS = (
    "常见原因包括",
    "可能是",
    "可以检查",
    "建议检查",
    "拆卸",
    "更换",
    "紧固",
    "调到",
)
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?")
_MODEL_RE = re.compile(r"\b[A-Za-z]{1,8}[-_]?\d{2,}[A-Za-z0-9_-]*\b")
_MEASUREMENT_RE = re.compile(
    r"(?P<object>[\u4e00-\u9fffA-Za-z]{2,20}?)(?:标准)?(?:为|是|需(?:要)?|必须(?:调到)?)?\s*"
    r"(?P<value>\d+(?:\.\d+)?(?:\s*(?:到|至|[-~～])\s*\d+(?:\.\d+)?)?)\s*"
    r"(?P<unit>mm|cm|m|N·m|N\s*·\s*m|kPa|MPa|V|A|℃|°C)",
    flags=re.IGNORECASE,
)
_SAFETY_CONTRADICTIONS = (
    (re.compile(r"必须(?P<action>[\u4e00-\u9fff]{2,8})"), ("无需{action}", "不必{action}", "不用{action}", "可以直接操作")),
    (re.compile(r"不得(?P<action>[\u4e00-\u9fff]{2,8})"), ("可以{action}", "可直接{action}")),
)


@dataclass(frozen=True)
class ResponseAuditResult:
    answer: str
    passed: bool
    violations: tuple[str, ...]
    used_fallback: bool
    claim_evidence_bindings: tuple[dict[str, Any], ...] = ()
    graph_evidence_used_ids: tuple[str, ...] = ()

    def to_metadata(self) -> dict[str, Any]:
        return {
            "claim_evidence_bindings": [
                {
                    **dict(binding),
                    "evidence_ids": list(binding.get("evidence_ids") or []),
                    "source_types": list(binding.get("source_types") or []),
                }
                for binding in self.claim_evidence_bindings
            ],
            "graph_evidence_used_ids": list(self.graph_evidence_used_ids),
        }


@dataclass(frozen=True)
class ResponsePlan:
    plan_id: str
    coverage_status: str
    source_mode: str
    allowed_evidence: tuple[dict[str, Any], ...]
    missing_aspects: tuple[str, ...]
    conflicts: tuple[dict[str, Any], ...]
    ledger_digest: str
    authorized_claim_evidence_bindings: tuple[dict[str, Any], ...] = ()
    graph_evidence_bound_ids: tuple[str, ...] = ()
    pending_clarification: dict[str, Any] | None = None

    def deterministic_fallback(self) -> str:
        if self.coverage_status == "unsupported":
            return (
                "当前知识库没有找到足以回答该问题的可靠依据。"
                "请提供对应设备手册，或确认设备型号和文档版本后再查询。"
            )
        if self.coverage_status == "conflict":
            return self._conflict_fallback()

        graph_diagnosis = _graph_diagnostic_fallback(
            self.allowed_evidence,
            self._source_label(),
        )
        if graph_diagnosis:
            if self.coverage_status == "partial" and self.missing_aspects:
                missing = "、".join(f"“{item}”" for item in self.missing_aspects)
                graph_diagnosis += f"\n\n关于{missing}，当前资料没有明确说明。"
            return graph_diagnosis

        ordered_evidence = _order_manual_evidence(self.allowed_evidence)
        facts = [str(entry.get("text") or "").strip() for entry in ordered_evidence]
        facts = [text for text in facts if text]
        if not facts:
            return (
                "当前知识库没有找到足以回答该问题的可靠依据。"
                "请提供对应设备手册，或确认设备型号和文档版本后再查询。"
            )
        body = "\n".join(dict.fromkeys(facts))
        source = self._source_label()
        has_manual = any(entry.get("source_type") == "manual" for entry in self.allowed_evidence)
        if self.source_mode == "quote" and has_manual:
            answer = f"手册原文（{source.removeprefix('手册')}）：\n{body}"
        elif self.source_mode == "page" and has_manual:
            answer = f"手册{source.removeprefix('手册')}记录：{body}"
        else:
            answer = body
            if source:
                answer += f"\n\n（来源：{source}）"
        if self.coverage_status == "partial" and self.missing_aspects:
            missing = "、".join(f"“{item}”" for item in self.missing_aspects)
            answer += f"\n\n关于{missing}，当前资料没有明确说明。"
        return answer

    def generation_instructions(self) -> str:
        status_rules = {
            "complete": "只使用允许证据回答全部要点。",
            "partial": "只回答有证据的要点，并逐项说明缺失内容。",
            "unsupported": "仅说明知识库证据不足，不提供通用原因、参数或操作猜测。",
            "conflict": "并列披露冲突值及来源，不自行选择其中一个。",
        }
        source_rules = {
            "normal": "先直接给结论；来源放在事实之后，不以‘根据手册’开头。",
            "quote": "用户要求原文，保留原文并明确标注可核对页码。",
            "page": "用户要求页码，明确给出页码并简述对应内容。",
        }
        return f"{status_rules.get(self.coverage_status, status_rules['unsupported'])}{source_rules[self.source_mode]}"

    def to_metadata(self) -> dict[str, Any]:
        sources = [
            entry.get("source") if isinstance(entry.get("source"), Mapping) else {}
            for entry in self.allowed_evidence
        ]
        allowed_evidence_refs = list(dict.fromkeys(
            str(entry.get("evidence_id") or "")
            for entry in self.allowed_evidence
            if str(entry.get("evidence_id") or "").strip()
        ))
        allowed_source_chunk_ids = list(dict.fromkeys(
            str(source.get("chunk_id") or "")
            for source in sources
            if str(source.get("chunk_id") or "").strip()
        ))
        allowed_evidence_pages = list(dict.fromkeys(
            source.get("page")
            for source in sources
            if source.get("page") not in (None, "")
        ))
        allowed_document_ids = list(dict.fromkeys(
            str(source.get("document_id") or "")
            for source in sources
            if str(source.get("document_id") or "").strip()
        ))
        metadata = {
            "response_plan_id": self.plan_id,
            "coverage_status": self.coverage_status,
            "source_mode": self.source_mode,
            "missing_aspects": list(self.missing_aspects),
            "evidence_ledger_digest": self.ledger_digest,
            "allowed_evidence_refs": allowed_evidence_refs,
            "allowed_source_chunk_ids": allowed_source_chunk_ids,
            "allowed_evidence_pages": allowed_evidence_pages,
            "allowed_document_ids": allowed_document_ids,
            "authorized_claim_evidence_bindings": [
                {
                    **dict(binding),
                    "evidence_ids": list(binding.get("evidence_ids") or []),
                }
                for binding in self.authorized_claim_evidence_bindings
            ],
            "graph_evidence_bound_ids": list(self.graph_evidence_bound_ids),
            "claim_evidence_bindings": [],
            "graph_evidence_used_ids": [],
        }
        if self.pending_clarification:
            metadata["pending_clarification"] = dict(self.pending_clarification)
        return metadata

    def _source_label(self) -> str:
        source_types = {
            str(entry.get("source_type") or "")
            for entry in self.allowed_evidence
        }
        labels: list[str] = []
        pages = []
        for entry in self.allowed_evidence:
            source = entry.get("source") if isinstance(entry.get("source"), Mapping) else {}
            page = source.get("page")
            if page not in (None, "") and str(page) not in pages:
                pages.append(str(page))
        if "manual" in source_types:
            if not pages:
                labels.append("手册")
            elif len(pages) == 1:
                labels.append(f"手册第{pages[0]}页")
            else:
                labels.append(f"手册第{'、'.join(pages)}页")
        if "domain_rule" in source_types:
            labels.append("已审核规则")
        if "graph" in source_types:
            labels.append("知识图谱")
        return "、".join(labels) or "知识库"

    def _conflict_fallback(self) -> str:
        descriptions = []
        for conflict in self.conflicts:
            field = str(conflict.get("field") or "关键参数")
            unit = str(conflict.get("unit") or "")
            alternatives = conflict.get("alternatives") or [
                {"value": value, "candidate_ids": []}
                for value in conflict.get("values") or []
            ]
            rendered = []
            for alternative in alternatives:
                value = str(alternative.get("value") or "")
                candidate_ids = [str(item) for item in alternative.get("candidate_ids") or []]
                source = self._conflict_source_label(candidate_ids)
                value_text = f"{value}{(' ' + unit) if unit else ''}"
                rendered.append(f"{value_text}（{source}）" if source else value_text)
            descriptions.append(f"{field}存在冲突：{' 与 '.join(rendered)}")
        detail = "；".join(descriptions) or "关键资料存在冲突"
        return f"{detail}。当前不能据此选定一个值，请先确认设备型号或文档版本。"

    def _conflict_source_label(self, candidate_ids: list[str]) -> str:
        for entry in self.allowed_evidence:
            source = entry.get("source") if isinstance(entry.get("source"), Mapping) else {}
            chunk_id = str(source.get("chunk_id") or "")
            evidence_id = str(entry.get("evidence_id") or "")
            if not any(candidate == chunk_id or evidence_id.endswith(f":{candidate}") for candidate in candidate_ids):
                continue
            parts = []
            page = source.get("page")
            version = str(source.get("document_version") or "").strip()
            document_id = str(source.get("document_id") or "").strip()
            if page not in (None, ""):
                parts.append(f"手册第{page}页")
            elif document_id:
                parts.append(f"手册{document_id}")
            if version:
                parts.append(f"版本{version}")
            return "，".join(parts)
        return ""


def build_response_plan(
    query: str,
    evidence_bundle: Mapping[str, Any],
    ledger: EvidenceLedger,
) -> ResponsePlan:
    evidence_bundle = fuse_evidence_support(query, evidence_bundle, ledger)
    source_mode = _detect_source_mode(query)
    qualified = tuple(
        entry
        for entry in ledger.entries
        if entry.get("qualification") == "qualified"
    )
    capabilities = evidence_bundle.get("capabilities") if isinstance(evidence_bundle.get("capabilities"), Mapping) else {}
    if capabilities.get("may_cite_manual") is False:
        qualified = tuple(entry for entry in qualified if entry.get("source_type") != "manual")
    support_rows = [row for row in evidence_bundle.get("aspect_support") or [] if isinstance(row, Mapping)]
    authorized_evidence_ids = {
        str(evidence_id)
        for row in support_rows
        if row.get("supported")
        for evidence_id in row.get("evidence_ids") or []
        if str(evidence_id).strip()
    }
    qualified = tuple(
        entry for entry in qualified
        if str(entry.get("evidence_id") or "") in authorized_evidence_ids
    )
    aspects = [
        QuestionAspect(str(row.get("aspect_id")), str(row.get("aspect_text") or row.get("aspect_id")))
        for row in support_rows
        if row.get("aspect_id")
    ]
    coverage_input = dict(evidence_bundle)
    coverage_input["qualified_evidence"] = list(qualified)
    coverage = determine_coverage(coverage_input, aspects=aspects)
    coverage_status = coverage.status
    user_obligation_rows = [
        row
        for row in support_rows
        if row.get("user_obligation") is not False
        and str(row.get("aspect_origin") or "") != "retrieval_expansion"
    ]
    if coverage_status != "conflict" and user_obligation_rows and not any(
        row.get("supported") for row in user_obligation_rows
    ):
        coverage_status = "unsupported"
    missing_ids = {str(item) for item in evidence_bundle.get("missing_aspect_ids") or []}
    missing_labels = tuple(
        str(row.get("aspect_text") or row.get("aspect_id"))
        for row in evidence_bundle.get("aspect_support") or []
        if isinstance(row, Mapping) and str(row.get("aspect_id")) in missing_ids
    )
    if not missing_labels:
        missing_labels = tuple(sorted(missing_ids))
    non_user_obligation_ids = {
        str(row.get("aspect_id"))
        for row in support_rows
        if row.get("aspect_id")
        and (
            row.get("user_obligation") is False
            or str(row.get("aspect_origin") or "") == "retrieval_expansion"
        )
    }
    if (
        coverage_status == "partial"
        and qualified
        and missing_ids
        and missing_ids.issubset(non_user_obligation_ids)
    ):
        # Retrieval-only expansions may be hidden only when their provenance is
        # explicit. Lexical differences between the user query and an expanded
        # retrieval query are not sufficient proof that an aspect is optional.
        coverage_status = "complete"
        missing_labels = ()
    conflicts = tuple(
        dict(item)
        for item in evidence_bundle.get("conflict_eligible") or []
        if isinstance(item, Mapping)
    )
    allowed = qualified
    if coverage_status == "conflict":
        conflict_ids = {
            str(candidate_id)
            for conflict in conflicts
            for candidate_id in conflict.get("candidate_ids") or []
        }
        allowed = tuple(
            entry for entry in ledger.entries
            if str((entry.get("source") or {}).get("chunk_id") or "") in conflict_ids
            or any(str(entry.get("evidence_id") or "").endswith(f":{candidate_id}") for candidate_id in conflict_ids)
        )
    allowed_by_id = {
        str(entry.get("evidence_id") or ""): entry
        for entry in allowed
        if str(entry.get("evidence_id") or "").strip()
    }
    authorized_claim_evidence_bindings = tuple(
        {
            "claim_id": str(row.get("aspect_id") or ""),
            "claim_text": str(row.get("aspect_text") or row.get("aspect_id") or ""),
            "evidence_ids": list(dict.fromkeys(
                evidence_id
                for evidence_id in (
                    str(value).strip() for value in row.get("evidence_ids") or []
                )
                if evidence_id in allowed_by_id
            )),
        }
        for row in support_rows
        if row.get("supported")
        and row.get("user_obligation") is not False
        and str(row.get("aspect_origin") or "") != "retrieval_expansion"
        and any(
            str(value).strip() in allowed_by_id
            for value in row.get("evidence_ids") or []
        )
    )
    graph_evidence_bound_ids = tuple(dict.fromkeys(
        evidence_id
        for binding in authorized_claim_evidence_bindings
        for evidence_id in binding["evidence_ids"]
        if allowed_by_id[evidence_id].get("source_type") == "graph"
    ))
    identity = {
        "coverage_status": coverage_status,
        "source_mode": source_mode,
        "missing_aspects": missing_labels,
        "conflicts": conflicts,
        "ledger_digest": ledger.digest,
        "authorized_claim_evidence_bindings": authorized_claim_evidence_bindings,
        "graph_evidence_bound_ids": graph_evidence_bound_ids,
    }
    canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    plan_id = f"response-plan-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:16]}"
    pending_clarification = None
    if coverage_status == "conflict" and conflicts:
        pending_clarification = build_evidence_conflict_clarification(
            query,
            conflicts[0],
            list(allowed),
        )
    return ResponsePlan(
        plan_id=plan_id,
        coverage_status=coverage_status,
        source_mode=source_mode,
        allowed_evidence=allowed,
        missing_aspects=missing_labels,
        conflicts=conflicts,
        ledger_digest=ledger.digest,
        authorized_claim_evidence_bindings=authorized_claim_evidence_bindings,
        graph_evidence_bound_ids=graph_evidence_bound_ids,
        pending_clarification=pending_clarification,
    )


def _is_explicit_user_missing_aspect(query: str, label: str) -> bool:
    query_text = canonical_aspect_text(query)
    label_text = canonical_aspect_text(label)
    if not query_text or not label_text:
        return False
    if label_text in query_text:
        return True
    for prefix in ("建议", "具体", "标准", "对应", "相关"):
        if label_text.startswith(prefix):
            label_text = label_text[len(prefix):]
            break
    for suffix in ("数值", "信息", "要求"):
        if label_text.endswith(suffix):
            label_text = label_text[:-len(suffix)]
            break
    return len(label_text) >= 2 and label_text in query_text


def _has_explicit_multi_part_request(query: str) -> bool:
    text = unicodedata.normalize("NFKC", str(query or ""))
    return any(marker in text for marker in ("并", "以及", "同时", "分别", "和", "与", "及", "、", ";", "；"))


def _order_manual_evidence(entries: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    manual = [entry for entry in entries if entry.get("source_type") == "manual"]
    if not manual:
        return list(entries)
    ordered_manual = iter(dedupe_and_sort_manual_records(manual))
    output: list[dict[str, Any]] = []
    inserted = False
    for entry in entries:
        if entry.get("source_type") == "manual":
            if not inserted:
                output.extend(ordered_manual)
                inserted = True
            continue
        output.append(entry)
    return output


def _source_values(source: Mapping[str, Any], *keys: str) -> set[str]:
    values: set[str] = set()
    for key in keys:
        value = source.get(key)
        items = value if isinstance(value, (list, tuple, set)) else [value]
        values.update(str(item).strip() for item in items if str(item or "").strip())
    return values


def _page_range_matches(graph_pages: set[str], manual_pages: set[str]) -> bool:
    try:
        graph_numbers = sorted({int(page) for page in graph_pages})
        manual_numbers = {int(page) for page in manual_pages}
    except (TypeError, ValueError):
        return False
    if len(graph_numbers) == 2 and graph_numbers[0] <= graph_numbers[1]:
        return any(graph_numbers[0] <= page <= graph_numbers[1] for page in manual_numbers)
    return bool(set(graph_numbers).intersection(manual_numbers))


def _manual_solution_matches_graph_source(
    graph_entry: Mapping[str, Any],
    manual_entry: Mapping[str, Any],
) -> bool:
    graph_source = graph_entry.get("source")
    manual_source = manual_entry.get("source")
    if not isinstance(graph_source, Mapping) or not isinstance(manual_source, Mapping):
        return False

    graph_document = str(graph_source.get("document_id") or "").strip()
    graph_version = str(graph_source.get("document_version") or "").strip()
    graph_sections = _source_values(graph_source, "section_id")
    graph_chunks = _source_values(graph_source, "source_chunk_uids")
    graph_pages = _source_values(graph_source, "pages")
    if not all((graph_document, graph_version, graph_sections, graph_chunks, graph_pages)):
        return False

    manual_sections = _source_values(manual_source, "section_id", "parent_section_id")
    manual_chunks = _source_values(
        manual_source,
        "source_chunk_uids",
        "chunk_ids",
        "chunk_uid",
        "chunk_id",
        "parent_chunk_id",
        "table_id",
    )
    manual_pages = _source_values(manual_source, "pages", "page")
    if (
        str(manual_source.get("document_id") or "").strip() != graph_document
        or str(manual_source.get("document_version") or "").strip() != graph_version
        or not graph_sections.intersection(manual_sections)
        or not graph_chunks.intersection(manual_chunks)
        or not _page_range_matches(graph_pages, manual_pages)
    ):
        return False

    graph_device = str((graph_entry.get("device") or {}).get("name") or "").strip()
    graph_component = str((graph_entry.get("component") or {}).get("name") or "").strip()
    manual_device = str(manual_source.get("device_name") or "").strip()
    manual_component = str(manual_source.get("component_name") or "").strip()
    return not (
        (manual_device and manual_device != graph_device)
        or (manual_component and manual_component != graph_component)
    )


def _graph_diagnostic_fallback(
    entries: tuple[dict[str, Any], ...],
    source_label: str,
) -> str:
    graph_entry = next((
        entry
        for entry in entries
        if entry.get("source_type") == "graph"
        and {"OWNS", "CAUSES"}.issubset(set(entry.get("relationship_types") or []))
        and str((entry.get("component") or {}).get("name") or "").strip()
        and str((entry.get("fault") or {}).get("name") or "").strip()
    ), None)
    if graph_entry is None:
        return ""

    device_name = str((graph_entry.get("device") or {}).get("name") or "").strip()
    component_name = str((graph_entry.get("component") or {}).get("name") or "").strip()
    fault_name = str((graph_entry.get("fault") or {}).get("name") or "").strip()
    device_prefix = f"在{device_name}中，" if device_name else ""
    lines = [
        f"诊断结论：{device_prefix}知识图谱将“{fault_name}”定位为“{component_name}”部件的故障。"
    ]

    solution = graph_entry.get("solution") if isinstance(graph_entry.get("solution"), Mapping) else {}
    solution_text = ""
    if (
        "HAS_SOLUTION" in set(graph_entry.get("relationship_types") or [])
        and solution.get("verified") is True
        and str(solution.get("status") or "active") == "active"
    ):
        solution_text = str(solution.get("title") or "").strip()
    if not solution_text:
        matching_rows = [
            entry
            for entry in entries
            if entry.get("source_type") == "manual"
            and (entry.get("source") or {}).get("row_index") is not None
            and fault_name in str(entry.get("text") or "")
            and _manual_solution_matches_graph_source(graph_entry, entry)
        ]
        matching_rows.sort(key=lambda item: len(str(item.get("text") or "")))
        for row in matching_rows:
            for line in str(row.get("text") or "").splitlines():
                if fault_name not in line:
                    continue
                match = re.search(
                    r"(?:col_5|处理建议|解决方案|维修建议|指导)\s*[=:：]\s*([^；;\n|]+)",
                    line,
                    flags=re.IGNORECASE,
                )
                if match:
                    solution_text = match.group(1).strip(" 。；;")
                    break
            if solution_text:
                break

    if not solution_text:
        action_markers = ("更换", "跟换", "修复", "检查", "调整", "清洗", "紧固")
        for entry in entries:
            if entry.get("source_type") != "manual":
                continue
            if not _manual_solution_matches_graph_source(graph_entry, entry):
                continue
            for line in str(entry.get("text") or "").splitlines():
                if fault_name not in line or "|" not in line:
                    continue
                cells = [cell.strip() for cell in line.split("|")]
                fault_cell_index = next(
                    (index for index, cell in enumerate(cells) if fault_name in cell),
                    None,
                )
                if fault_cell_index is None:
                    continue
                solution_text = next((
                    cell.strip(" 。；;")
                    for cell in cells[fault_cell_index + 1:]
                    if any(marker in cell for marker in action_markers)
                ), "")
                if solution_text:
                    break
            if solution_text:
                break

    if not solution_text:
        solution_text = _manual_treatment_sentence(
            graph_entry,
            entries,
        )

    if solution_text:
        lines.append(f"处理建议：手册对应故障行记录“{solution_text}”。")
        canonical_treatment = _canonical_manual_treatment(graph_entry, solution_text)
        if canonical_treatment and _normalized(canonical_treatment) not in _normalized(solution_text):
            lines.append(f"处理结论：{canonical_treatment}。")
    else:
        lines.append("处理建议：当前合格证据未给出进一步处理方法。")
    if source_label:
        lines.extend(["", f"（来源：{source_label}）"])
    return "\n".join(lines)


_FAULT_TREATMENT_SUFFIX_RE = re.compile(
    r"(?:变形或开裂|损坏|磨损|故障|不灵活|发黑|变形|开裂|弯曲|卡住|失效)$"
)


def _manual_treatment_sentence(
    graph_entry: Mapping[str, Any],
    entries: tuple[dict[str, Any], ...],
) -> str:
    """Extract one action-bearing treatment sentence from the graph source chunk."""
    fault_name = str((graph_entry.get("fault") or {}).get("name") or "").strip()
    component_name = str((graph_entry.get("component") or {}).get("name") or "").strip()
    fault_target = _FAULT_TREATMENT_SUFFIX_RE.sub("", fault_name).strip(" -_/，、")
    targets = [
        _normalized(value)
        for value in (fault_target, component_name)
        if len(_normalized(value)) >= 2
    ]
    action_markers = ("更换", "跟换", "修复", "重新安装", "调整", "清洗", "紧固")
    candidates: list[tuple[int, int, str]] = []
    for entry in entries:
        if entry.get("source_type") != "manual":
            continue
        if not _manual_solution_matches_graph_source(graph_entry, entry):
            continue
        text = str(entry.get("text") or "").strip()
        if not text:
            continue
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for index, line in enumerate(lines):
            normalized_line = _normalized(line)
            if not any(marker in line for marker in action_markers):
                continue
            target_hits = sum(target in normalized_line for target in targets)
            if not target_hits:
                continue
            condition_hit = int(any(marker in line for marker in (
                "若", "如有", "否则", "不灵活", "损坏", "磨损", "故障", "发黑", "开裂", "变形",
            )))
            candidates.append((target_hits + condition_hit, -index, line.strip(" 。；;")))
    if not candidates:
        return ""
    return max(candidates, key=lambda item: (item[0], item[1], -len(item[2])))[2]


def _canonical_manual_treatment(
    graph_entry: Mapping[str, Any],
    solution_text: str,
) -> str:
    """Render an explicit replacement target only when the manual names it."""
    if "更换" not in solution_text and "跟换" not in solution_text:
        return ""
    fault_name = str((graph_entry.get("fault") or {}).get("name") or "").strip()
    fault_target = _FAULT_TREATMENT_SUFFIX_RE.sub("", fault_name).strip(" -_/，、")
    normalized_target = _normalized(fault_target)
    if len(normalized_target) < 2 or normalized_target not in _normalized(solution_text):
        return ""
    return f"更换{fault_target}"


_GRAPH_CLAIM_TYPES_BY_ASPECT = {
    "device": "device_identity",
    "device-identity": "device_identity",
    "component": "component_ownership",
    "component-ownership": "component_ownership",
    "ownership": "component_ownership",
    "fault": "fault_relation",
    "fault-cause": "fault_relation",
    "fault-relation": "fault_relation",
    "treatment": "verified_solution",
    "verified-solution": "verified_solution",
}


def _graph_claim_type(
    binding: Mapping[str, Any],
    entry: Mapping[str, Any],
) -> str:
    aspect_id = str(binding.get("claim_id") or "").strip().lower().replace("_", "-")
    explicit = _GRAPH_CLAIM_TYPES_BY_ASPECT.get(aspect_id)
    available = {str(value) for value in entry.get("claim_types") or []}
    if explicit in available:
        return explicit
    for candidate in (
        "verified_solution",
        "fault_relation",
        "component_ownership",
        "device_identity",
    ):
        if candidate in available:
            return candidate
    return ""


def _graph_claim_is_emitted(
    answer: str,
    entry: Mapping[str, Any],
    claim_type: str,
) -> bool:
    normalized_answer = _normalized(answer)

    def contains(container: Mapping[str, Any], key: str) -> bool:
        value = _normalized(str(container.get(key) or ""))
        return bool(value and value in normalized_answer)

    device = entry.get("device") if isinstance(entry.get("device"), Mapping) else {}
    component = entry.get("component") if isinstance(entry.get("component"), Mapping) else {}
    fault = entry.get("fault") if isinstance(entry.get("fault"), Mapping) else {}
    solution = entry.get("solution") if isinstance(entry.get("solution"), Mapping) else {}
    if claim_type == "device_identity":
        return contains(device, "name")
    if claim_type == "component_ownership":
        # The graph path is already scope-authorized. A concise answer may
        # name only the component (for example, "故障部件：火花塞") without
        # repeating the parent device or an ownership verb.
        return contains(component, "name")
    if claim_type == "fault_relation":
        return contains(component, "name") and contains(fault, "name")
    if claim_type == "verified_solution":
        return (
            solution.get("verified") is True
            and str(solution.get("status") or "active") == "active"
            and contains(solution, "title")
            and (contains(fault, "name") or not str(fault.get("name") or "").strip())
        )
    return False


def _bind_emitted_graph_claims(
    plan: ResponsePlan,
    answer: str,
) -> tuple[tuple[dict[str, Any], ...], tuple[str, ...]]:
    allowed_by_id = {
        str(entry.get("evidence_id") or ""): entry
        for entry in plan.allowed_evidence
        if str(entry.get("evidence_id") or "").strip()
    }
    emitted: list[dict[str, Any]] = []
    used_ids: list[str] = []
    for binding in plan.authorized_claim_evidence_bindings:
        graph_ids: list[str] = []
        claim_type = ""
        for evidence_id in binding.get("evidence_ids") or []:
            entry = allowed_by_id.get(str(evidence_id))
            if not entry or entry.get("source_type") != "graph":
                continue
            entry_claim_type = _graph_claim_type(binding, entry)
            if not entry_claim_type or not _graph_claim_is_emitted(
                answer,
                entry,
                entry_claim_type,
            ):
                continue
            claim_type = claim_type or entry_claim_type
            stable_id = str(evidence_id)
            if stable_id not in graph_ids:
                graph_ids.append(stable_id)
            if stable_id not in used_ids:
                used_ids.append(stable_id)
        if graph_ids:
            emitted.append({
                "claim_id": str(binding.get("claim_id") or ""),
                "claim_type": claim_type,
                "claim_text": str(binding.get("claim_text") or ""),
                "evidence_ids": graph_ids,
                "source_types": ["graph"],
                "emitted": True,
            })
    return tuple(emitted), tuple(used_ids)


def _response_audit_result(
    plan: ResponsePlan,
    answer: str,
    passed: bool,
    violations: tuple[str, ...],
    used_fallback: bool,
) -> ResponseAuditResult:
    bindings, graph_ids = _bind_emitted_graph_claims(plan, answer)
    used_graph_ids = set(graph_ids)
    unbound_graph_ids: list[str] = []
    for entry in plan.allowed_evidence:
        if entry.get("source_type") != "graph":
            continue
        evidence_id = str(entry.get("evidence_id") or "")
        if not evidence_id or evidence_id in used_graph_ids:
            continue
        if any(
            _graph_claim_is_emitted(answer, entry, claim_type)
            for claim_type in entry.get("claim_types") or []
        ):
            unbound_graph_ids.append(evidence_id)
    if unbound_graph_ids:
        passed = False
        used_fallback = True
        violations = tuple(dict.fromkeys((
            *violations,
            *(f"unbound_graph_claim:{evidence_id}" for evidence_id in unbound_graph_ids),
        )))
    return ResponseAuditResult(
        answer=answer,
        passed=passed,
        violations=violations,
        used_fallback=used_fallback,
        claim_evidence_bindings=bindings,
        graph_evidence_used_ids=graph_ids,
    )


def finalize_response(
    plan: ResponsePlan,
    draft: str,
    *,
    evidence_rendered: bool = False,
) -> ResponseAuditResult:
    answer = str(draft or "").strip()
    violations: list[str] = []
    allowed_text = _normalized("\n".join(
        f"{entry.get('text', '')} {json.dumps(entry.get('source') or {}, ensure_ascii=False)}"
        for entry in plan.allowed_evidence
    ))

    if not answer:
        violations.append("empty_answer")
    if not evidence_rendered:
        for fact in _bound_fact_tokens(answer):
            if _normalized(fact) not in allowed_text:
                violations.append(f"unbound_fact:{fact}")
        for measurement in _MEASUREMENT_RE.finditer(answer):
            object_text = measurement.group("object")
            unit = measurement.group("unit")
            if _normalized(object_text) not in allowed_text or _normalized(unit) not in allowed_text:
                violations.append(f"unbound_measurement:{object_text}:{measurement.group('value')}:{unit}")
    if _contradicts_safety_requirement(answer, allowed_text):
        violations.append("unbound_safety_requirement")
    if (
        not evidence_rendered
        and plan.source_mode == "normal"
        and answer.startswith(_MANUAL_LEADS)
    ):
        violations.append("unsolicited_manual_lead")
    if plan.coverage_status == "partial" and plan.missing_aspects:
        if not all(item in answer for item in plan.missing_aspects) or not any(
            marker in answer for marker in ("没有明确说明", "未找到", "依据不足", "资料不足")
        ):
            if evidence_rendered:
                missing = "、".join(f"“{item}”" for item in plan.missing_aspects)
                answer += f"\n\n关于{missing}，当前资料没有明确说明。"
            else:
                violations.append("partial_missing_disclosure")
    if plan.coverage_status == "unsupported":
        # A deterministic renderer may have recovered an authorized section
        # directly even when the semantic aspect classifier marked the
        # original retrieval as unsupported. In that case the answer is still
        # bound to the plan's qualified evidence and must not be overwritten
        # by the generic insufficient-evidence fallback.
        if (
            answer
            and answer != plan.deterministic_fallback()
            and not (evidence_rendered and plan.allowed_evidence)
        ):
            violations.append("unsupported_generic_completion")
    if plan.coverage_status == "conflict":
        for conflict in plan.conflicts:
            if not all(str(value) in answer for value in conflict.get("values") or []):
                violations.append("conflict_value_omitted")

    deduped = tuple(dict.fromkeys(violations))
    if deduped:
        fallback = plan.deterministic_fallback()
        return _response_audit_result(plan, fallback, False, deduped, True)
    if plan.graph_evidence_bound_ids:
        emitted_bindings, _ = _bind_emitted_graph_claims(plan, answer)
        if not emitted_bindings:
            fallback = plan.deterministic_fallback()
            fallback_bindings, _ = _bind_emitted_graph_claims(plan, fallback)
            if fallback_bindings:
                return _response_audit_result(plan, fallback, True, (), True)
    return _response_audit_result(plan, answer, True, (), False)


def _detect_source_mode(query: str) -> str:
    text = str(query or "")
    if any(term in text for term in ("原文", "引用", "逐字", "出处")):
        return "quote"
    if any(term in text for term in ("第几页", "哪一页", "页码", "多少页")):
        return "page"
    return "normal"


def _bound_fact_tokens(answer: str) -> list[str]:
    facts = list(_MODEL_RE.findall(answer))
    for match in _NUMBER_RE.finditer(answer):
        prefix = answer[max(0, match.start() - 3):match.start()]
        suffix = answer[match.end():match.end() + 2]
        if re.search(r"(?:^|\n)\s*$", prefix) and re.match(r"\s*[.、)）]", suffix):
            continue
        facts.append(match.group(0))
    return list(dict.fromkeys(facts))


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).casefold().replace(" ", "")


def _contradicts_safety_requirement(answer: str, allowed_text: str) -> bool:
    normalized_answer = _normalized(answer)
    normalized_evidence = _normalized(allowed_text)
    for required_pattern, contradictions in _SAFETY_CONTRADICTIONS:
        for match in required_pattern.finditer(normalized_evidence):
            action = match.group("action")
            if any(_normalized(pattern.format(action=action)) in normalized_answer for pattern in contradictions):
                return True
    return False
