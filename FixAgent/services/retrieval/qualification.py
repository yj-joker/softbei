"""Classify retrieved maintenance evidence before it reaches the answering model.

Ranking chooses the best available candidates. Qualification decides whether those
candidates are usable as evidence for the current device and task.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional, Sequence

from services.retrieval.aspects import (
    QuestionAspect,
    canonical_aspect_text,
    split_question_aspects,
)
from services.retrieval.evidence import determine_coverage
from services.retrieval.query_constraints import (
    candidate_constraint_conflicts,
    extract_query_constraints,
)


QUALIFIED = "qualified"
REFERENCE_ONLY = "reference_only"
EXCLUDED = "excluded"


def qualify_candidates(
    query: str,
    candidates: Iterable[Dict[str, Any]],
    *,
    document_id: Optional[str] = None,
    device_type: Optional[str] = None,
    document_version: Optional[str] = None,
    manual_type: Optional[str] = None,
    requires_strict_evidence: bool = False,
    aspects: Optional[Sequence[QuestionAspect]] = None,
    scope_status: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a serializable evidence bundle without exposing rejected content.

    Explicit retrieval scope is a hard boundary. Without a scope, evidence may be
    useful as a reference but cannot be presented as the user's device manual.
    """
    qualified: List[Dict[str, Any]] = []
    references: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []
    effective_aspects = list(aspects) if aspects is not None else split_question_aspects(query)
    query_constraints = extract_query_constraints(query)

    for index, raw in enumerate(candidates or []):
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        metadata = dict(item.get("metadata") or {})
        item["metadata"] = metadata
        metadata["evidence_id"] = _evidence_id(item, index)
        status, reasons, matches = _qualify_candidate(
            query,
            item,
            document_id=document_id,
            device_type=device_type,
            document_version=document_version,
            manual_type=manual_type,
            requires_strict_evidence=requires_strict_evidence,
        )
        metadata.update(matches)
        constraint_conflicts = candidate_constraint_conflicts(query_constraints, item)
        if constraint_conflicts:
            status = EXCLUDED
            reasons = list(reasons) + ["entity_constraint_conflict", *constraint_conflicts]
        metadata["qualification"] = status
        metadata["qualification_reasons"] = reasons
        metadata["direct_answer_eligible"] = status == QUALIFIED

        if status == QUALIFIED:
            qualified.append(item)
        elif status == REFERENCE_ONLY:
            references.append(item)
        else:
            excluded.append({
                "evidence_id": _evidence_id(item, index),
                "reasons": reasons,
                "device_type": metadata.get("device_type"),
                "document_id": metadata.get("document_id"),
                "section_title": metadata.get("section_title"),
            })

    aspect_support = _map_aspect_support(effective_aspects, qualified)
    conflicts = _detect_conflicts(qualified, aspects=effective_aspects)
    conflict_eligible = [dict(conflict) for conflict in conflicts]
    if conflicts:
        for item in qualified:
            metadata = item["metadata"]
            metadata["qualification"] = REFERENCE_ONLY
            metadata["qualification_reasons"] = list(metadata["qualification_reasons"]) + ["evidence_conflict"]
            metadata["direct_answer_eligible"] = False
            references.append(item)
        qualified = []

    status = QUALIFIED if qualified else REFERENCE_ONLY if references else "no_evidence"
    provisional = {
        "qualified_evidence": qualified,
        "conflicts": conflicts,
        "conflict_eligible": conflict_eligible,
        "aspect_support": aspect_support,
    }
    coverage = determine_coverage(
        provisional,
        aspects=effective_aspects,
        scope_status=scope_status,
    )
    capabilities = {
        "may_cite_manual": bool(qualified),
        "may_emit_exact_parameter": bool(qualified) and not conflicts,
        "may_emit_device_specific_procedure": bool(qualified) and not conflicts,
        "may_offer_generic_guidance": coverage.status not in {"unsupported", "conflict"},
    }
    result = {
        "evidence_bundle_version": 2,
        "overall_status": status,
        "qualified_evidence": qualified,
        "reference_evidence": references,
        "excluded_evidence": excluded,
        "conflicts": conflicts,
        "conflict_eligible": conflict_eligible,
        "aspect_support": aspect_support,
        "evidence_identity": {
            "document_id": document_id or "",
            "device_type": device_type or "",
            "document_version": document_version or "",
            "manual_type": manual_type or "",
        },
        "capabilities": capabilities,
        "summary": {
            "qualified_count": len(qualified),
            "reference_count": len(references),
            "excluded_count": len(excluded),
            "has_explicit_scope": bool(document_id or device_type),
        },
    }
    result["coverage_status"] = coverage.status
    result["coverage_reason"] = coverage.reason
    result["supported_aspect_ids"] = list(coverage.supported_aspect_ids)
    result["missing_aspect_ids"] = list(coverage.missing_aspect_ids)
    result["conflict_aspect_ids"] = list(coverage.conflict_aspect_ids)
    return result


def _qualify_candidate(
    query: str,
    item: Dict[str, Any],
    *,
    document_id: Optional[str],
    device_type: Optional[str],
    document_version: Optional[str],
    manual_type: Optional[str],
    requires_strict_evidence: bool,
) -> tuple[str, List[str], Dict[str, str]]:
    metadata = item.get("metadata") or {}
    reasons: List[str] = []
    matches = {
        "device_match": _match_scope(device_type, metadata.get("device_type")),
        "document_match": _match_scope(document_id, metadata.get("document_id")),
        "version_match": _match_scope(document_version, metadata.get("document_version")),
        "manual_match": _match_scope(manual_type, metadata.get("manual_type")),
        "topic_match": _topic_match(query, item),
    }

    for name in ("device", "document", "version", "manual"):
        if matches[f"{name}_match"] == "mismatch":
            reasons.append(f"{name}_mismatch")
    if matches["topic_match"] == "conflict":
        reasons.append("topic_conflict")
    if reasons:
        return EXCLUDED, reasons, matches

    scoped_keys = {
        "device_match": device_type,
        "document_match": document_id,
        "version_match": document_version,
        "manual_match": manual_type,
    }
    unknown_identity = any(
        matches[key] == "unknown"
        for key, expected in scoped_keys.items()
        if _normalize(expected)
    )
    has_identity_scope = any(_normalize(expected) for expected in scoped_keys.values())
    if matches["topic_match"] == "weak":
        reasons.append("weak_topic_match")
    if unknown_identity:
        reasons.append("identity_not_confirmed")

    if requires_strict_evidence and (not has_identity_scope or unknown_identity or matches["topic_match"] != "matched"):
        return REFERENCE_ONLY, reasons or ["strict_evidence_not_confirmed"], matches
    if not has_identity_scope or unknown_identity or matches["topic_match"] != "matched":
        return REFERENCE_ONLY, reasons or ["reference_only"], matches
    return QUALIFIED, reasons, matches


def _match_scope(expected: Optional[str], actual: Any) -> str:
    expected_text = _normalize(expected)
    actual_text = _normalize(actual)
    if not expected_text:
        return "unknown"
    if not actual_text:
        return "unknown"
    return "matched" if expected_text == actual_text else "mismatch"


def _topic_match(query: str, item: Dict[str, Any]) -> str:
    metadata = item.get("metadata") or {}
    features = metadata.get("local_rerank_features") or {}
    coverage = _to_float(features.get("query_coverage"))
    title_coverage = _to_float(features.get("title_coverage"), features.get("title_hit_ratio"))
    content = " ".join(
        str(value or "") for value in (
            metadata.get("section_title"), metadata.get("chunk_label"), item.get("text"), item.get("content"),
        )
    ).lower()
    terms = [term for term in _tokenize(query) if len(term) >= 2]
    hits = sum(1 for term in terms if term in content)
    if terms and hits == 0 and coverage < 0.15 and title_coverage < 0.15:
        return "conflict"
    if coverage >= 0.35 or title_coverage >= 0.35 or (terms and hits / len(terms) >= 0.5):
        return "matched"
    return "weak"


def _detect_conflicts(
    items: Iterable[Dict[str, Any]],
    *,
    aspects: Sequence[QuestionAspect] = (),
) -> List[Dict[str, Any]]:
    groups: Dict[tuple[str, str, tuple[str, ...]], Dict[str, Any]] = {}
    for item in items:
        metadata = item.get("metadata") or {}
        evidence_id = str(metadata.get("evidence_id") or item.get("doc_id") or item.get("id") or "")
        semantic_scope = _conflict_semantic_scope(metadata)
        explicit_values = metadata.get("parameter_values") or []
        measurements: List[tuple[str, str, str]] = []
        if isinstance(explicit_values, list):
            for measurement in explicit_values:
                if not isinstance(measurement, dict):
                    continue
                field = str(measurement.get("field") or "").strip()
                value = str(measurement.get("value") or "").strip()
                unit = str(measurement.get("unit") or "").strip()
                if field and value and unit:
                    measurements.append((field, value, unit))

        if not measurements:
            names = metadata.get("parameter_names") or []
            numbers = metadata.get("numeric_values") or []
            parameter_type = str(metadata.get("parameter_type") or "").strip()
            part_name = str(metadata.get("part_name") or "").strip()
            if not part_name and isinstance(names, list) and names:
                part_name = str(names[0] or "").strip()
            field = (
                f"{part_name}:{parameter_type}"
                if part_name and parameter_type
                else part_name or parameter_type
            )
            if field and isinstance(numbers, list):
                for number in numbers:
                    if not isinstance(number, dict):
                        if len(numbers) == 1:
                            units = metadata.get("units") or []
                            unit = str(units[0]) if isinstance(units, list) and units else ""
                            value = str(number or "").strip()
                            if value and unit:
                                measurements.append((field, value, unit))
                        continue
                    value = str(number.get("raw") or "").strip()
                    unit = str(number.get("unit") or "").strip()
                    if value and unit:
                        measurements.append((field, value, unit))

        for field, value, unit in measurements:
            normalized_field = _normalize_conflict_field(field)
            normalized_unit, display_unit = _normalize_conflict_unit(unit)
            normalized_value = _normalize_conflict_value(value)
            if not normalized_field or not normalized_unit or not normalized_value:
                continue
            group = groups.setdefault(
                (normalized_field, normalized_unit, semantic_scope),
                {"field": field, "unit": display_unit, "values": {}},
            )
            value_entry = group["values"].setdefault(
                normalized_value,
                {"value": value, "candidate_ids": set()},
            )
            if evidence_id:
                value_entry["candidate_ids"].add(evidence_id)

    conflicts: List[Dict[str, Any]] = []
    for (_, _, semantic_scope), group in groups.items():
        number_map = group["values"]
        candidate_ids = {
            candidate_id
            for entry in number_map.values()
            for candidate_id in entry["candidate_ids"]
            if candidate_id
        }
        if len(number_map) <= 1 or len(candidate_ids) <= 1:
            continue
        ordered = [entry for _, entry in sorted(number_map.items())]
        field = str(group["field"])
        conflicts.append({
            "field": field,
            "semantic_fields": [field],
            "unit": str(group["unit"]),
            "values": [str(entry["value"]) for entry in ordered],
            "alternatives": [
                {
                    "value": str(entry["value"]),
                    "candidate_ids": sorted(entry["candidate_ids"]),
                }
                for entry in ordered
            ],
            "candidate_ids": sorted(candidate_ids),
            "aspect_ids": [aspect.aspect_id for aspect in aspects if _aspect_matches(aspect, field)],
            "semantic_scope": list(semantic_scope),
            "impact": "manual_claim_blocked",
        })
    return conflicts


def _normalize_conflict_field(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = re.sub(r"\s+", "", text)
    for synonym in ("拧紧力矩", "紧固力矩", "拧紧扭矩", "紧固扭矩", "扭力"):
        text = text.replace(synonym, "扭矩")
    return text.strip(":：|/")


def _normalize_conflict_unit(value: Any) -> tuple[str, str]:
    text = unicodedata.normalize("NFKC", str(value or "")).replace(" ", "")
    compact = text.casefold().replace("*", "·").replace("⋅", "·").replace(".", "·")
    aliases = {
        "n·m": ("n·m", "N·m"),
        "nm": ("n·m", "N·m"),
        "mm": ("mm", "mm"),
        "cm": ("cm", "cm"),
        "mpa": ("mpa", "MPa"),
        "kpa": ("kpa", "kPa"),
    }
    return aliases.get(compact, (compact, text))


def _normalize_conflict_value(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"\s+", "", text).replace("～", "-").replace("~", "-")

    def normalize_number(match: re.Match[str]) -> str:
        try:
            number = Decimal(match.group(0))
        except InvalidOperation:
            return match.group(0)
        normalized = format(number.normalize(), "f")
        return normalized.rstrip("0").rstrip(".") if "." in normalized else normalized

    return re.sub(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?", normalize_number, text).casefold()


def _conflict_semantic_scope(metadata: Dict[str, Any]) -> tuple[str, ...]:
    values = (
        metadata.get("action") or metadata.get("operation_action"),
        metadata.get("orientation") or metadata.get("direction"),
        metadata.get("device_type") or metadata.get("device_model"),
        metadata.get("applicable_scope") or metadata.get("model_scope"),
    )
    return tuple(_normalize(value) for value in values)


def _map_aspect_support(
    aspects: Sequence[QuestionAspect],
    items: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for aspect in aspects:
        evidence_ids = [
            str((item.get("metadata") or {}).get("evidence_id") or "")
            for item in items
            if _aspect_matches(aspect, _candidate_text(item))
        ]
        rows.append({
            "aspect_id": aspect.aspect_id,
            "aspect_text": aspect.text,
            "supported": bool(evidence_ids),
            "evidence_ids": [value for value in evidence_ids if value],
        })
    return rows


def _aspect_matches(aspect: QuestionAspect, text: str) -> bool:
    aspect_text = canonical_aspect_text(aspect.text)
    candidate_text = _normalize_compact(text)
    if not aspect_text or not candidate_text:
        return False
    if aspect_text in candidate_text or candidate_text in aspect_text:
        return True

    raw_terms = [
        _normalize_compact(term)
        for term in re.split(r"[\s,，、/|;；]+", str(aspect.text or ""))
        if _normalize_compact(term)
    ]
    if len(raw_terms) <= 1:
        aspect_bigrams = {
            aspect_text[index:index + 2]
            for index in range(len(aspect_text) - 1)
        }
        matched_bigrams = sum(
            1 for gram in aspect_bigrams if gram in candidate_text
        )
        return bool(
            len(aspect_bigrams) >= 4
            and matched_bigrams >= 4
            and matched_bigrams / len(aspect_bigrams) >= 0.6
        )

    required_slots = {
        slot
        for slot, markers in _ASPECT_SLOT_QUERY_MARKERS.items()
        if any(marker in aspect_text for marker in markers)
    }
    if any(not _candidate_supports_slot(slot, candidate_text, text) for slot in required_slots):
        return False

    entity_terms = [
        term for term in raw_terms
        if not _is_aspect_operator_term(term)
    ]
    if not entity_terms:
        return False
    matched_terms = [
        term for term in entity_terms
        if _semantic_term_matches_candidate(term, candidate_text)
    ]
    required_matches = 1 if len(entity_terms) == 1 else 2
    return len(matched_terms) >= required_matches


_ASPECT_SLOT_QUERY_MARKERS = {
    "quantity": ("数量", "几件", "几个", "几只", "几颗"),
    "torque": ("扭矩", "力矩", "扭力", "校正力", "预紧力"),
    "orientation": (
        "方向", "朝向", "朝哪", "朝上", "朝下", "朝内", "朝外",
        "密距端", "疏距端", "顺时针", "逆时针", "对齐", "对正", "标记",
    ),
    "location": ("位置", "哪里", "何处", "哪边", "插入位置"),
}

_ASPECT_OPERATOR_TERMS = {
    "装配", "安装", "拆卸", "拆下", "检查", "测量", "调整", "更换",
    "零件清单", "部件清单", "装配清单", "操作方法", "操作步骤", "步骤",
    "数量", "扭矩", "力矩", "扭力", "校正力", "预紧力", "标准值", "要求",
    "安装方向", "方向", "朝向", "密距端", "疏距端", "位置", "插入位置",
    "顺时针", "逆时针", "对齐", "对正", "标记",
}


def _is_aspect_operator_term(term: str) -> bool:
    return term in _ASPECT_OPERATOR_TERMS


def _candidate_supports_slot(slot: str, candidate_text: str, raw_text: str) -> bool:
    if slot == "quantity":
        return bool(re.search(r"(?:数量|共|合计)[=:：]?\d+", candidate_text))
    if slot == "torque":
        return bool(
            any(marker in candidate_text for marker in ("扭矩", "力矩", "扭力", "校正力", "预紧力"))
            or re.search(r"\d(?:\.\d+)?(?:±\d(?:\.\d+)?)?n[·.]?m", candidate_text, flags=re.IGNORECASE)
        )
    if slot == "orientation":
        return any(
            marker in candidate_text
            for marker in (
                "朝上", "朝下", "朝内", "朝外", "朝前", "朝后", "方向",
                "顺时针", "逆时针", "对齐", "对正", "平齐", "标记",
            )
        )
    if slot == "location":
        return any(
            marker in candidate_text
            for marker in ("插入", "位于", "之间", "位置", "孔", "槽", "处", "上", "下", "侧", "端")
        )
    return False


def _semantic_term_matches_candidate(term: str, candidate_text: str) -> bool:
    if term in candidate_text:
        return True
    if len(term) < 4:
        return False
    term_bigrams = {term[index:index + 2] for index in range(len(term) - 1)}
    if not term_bigrams:
        return False
    matched = sum(1 for gram in term_bigrams if gram in candidate_text)
    return matched / len(term_bigrams) >= 0.6


def _candidate_text(item: Dict[str, Any]) -> str:
    metadata = item.get("metadata") or {}
    return " ".join(
        str(value or "")
        for value in (item.get("content"), item.get("text"), metadata.get("section_title"))
    )


def _normalize_compact(value: Any) -> str:
    return "".join(character for character in str(value or "").casefold() if character.isalnum())


def _tokenize(text: str) -> List[str]:
    return [piece.strip().lower() for piece in str(text or "").replace("/", " ").split() if piece.strip()]


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower()


def _to_float(*values: Any) -> float:
    for value in values:
        try:
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _evidence_id(item: Dict[str, Any], index: int) -> str:
    metadata = item.get("metadata") or {}
    return str(item.get("doc_id") or item.get("id") or metadata.get("chunk_id") or index)
