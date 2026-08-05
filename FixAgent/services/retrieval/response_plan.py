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


@dataclass(frozen=True)
class ResponsePlan:
    plan_id: str
    coverage_status: str
    source_mode: str
    allowed_evidence: tuple[dict[str, Any], ...]
    missing_aspects: tuple[str, ...]
    conflicts: tuple[dict[str, Any], ...]
    ledger_digest: str
    pending_clarification: dict[str, Any] | None = None

    def deterministic_fallback(self) -> str:
        if self.coverage_status == "unsupported":
            return (
                "当前知识库没有找到足以回答该问题的可靠依据。"
                "请提供对应设备手册，或确认设备型号和文档版本后再查询。"
            )
        if self.coverage_status == "conflict":
            return self._conflict_fallback()

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
    aspects = [
        QuestionAspect(str(row.get("aspect_id")), str(row.get("aspect_text") or row.get("aspect_id")))
        for row in support_rows
        if row.get("aspect_id")
    ]
    coverage_input = dict(evidence_bundle)
    coverage_input["qualified_evidence"] = list(qualified)
    coverage = determine_coverage(coverage_input, aspects=aspects)
    coverage_status = coverage.status
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
    identity = {
        "coverage_status": coverage_status,
        "source_mode": source_mode,
        "missing_aspects": missing_labels,
        "conflicts": conflicts,
        "ledger_digest": ledger.digest,
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
        return ResponseAuditResult(plan.deterministic_fallback(), False, deduped, True)
    return ResponseAuditResult(answer, True, (), False)


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
