"""Expert domain rule sync and deterministic diagnosis matching."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping


RULE_RECORD_TYPE = "domain_rule"
ACTIVE_STATUS = "active"
DOC_ID_PREFIX = "domain_rule:"
MIN_RELEVANCE_FOR_DIRECT_HIT = 0.5
MIN_KEYWORD_RATIO_FOR_DIRECT_HIT = 0.5
DOMAIN_RULE_TOOL_NAME = "domain_rule_engine"


class DomainRuleServiceError(RuntimeError):
    """Raised when rule sync or matching cannot be completed safely."""


@dataclass(frozen=True)
class DomainRule:
    rule_id: Any
    rule_code: str
    doc_id: str
    status: str
    title: str
    device_type: str
    symptom_keys: list[str]
    condition_text: str
    conclusion: str
    question: str
    options: list[str]
    evidence_refs: list[dict[str, Any]]


def get_vector_service():
    from services.knowledge.vector_service import get_vector_service as _get_vector_service

    return _get_vector_service()


def get_text_embedding():
    from embeddings.text_embedding import get_text_embedding as _get_text_embedding

    return _get_text_embedding()


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", _clean_text(value)).lower()


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen = set()
    for item in value:
        text = _clean_text(item)
        if text and text not in seen:
            result.append(text)
            seen.add(text)
    return result


def _evidence_refs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    refs: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, Mapping):
            refs.append({str(k): v for k, v in item.items() if k is not None})
        elif item is not None:
            refs.append({"text": str(item)})
    return refs


def _doc_id_from_payload(payload: Mapping[str, Any]) -> str:
    doc_id = _clean_text(payload.get("doc_id"))
    if not doc_id and payload.get("rule_id") is not None:
        doc_id = f"{DOC_ID_PREFIX}{payload.get('rule_id')}"
    if not doc_id:
        raise ValueError("doc_id is required")
    if not doc_id.startswith(DOC_ID_PREFIX):
        raise ValueError("doc_id must start with domain_rule:")
    return doc_id


def _normalize_rule(payload: Mapping[str, Any], *, strict: bool = True) -> DomainRule:
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be an object")

    doc_id = _doc_id_from_payload(payload)
    title = _clean_text(payload.get("title"))
    conclusion = _clean_text(payload.get("conclusion"))
    condition_text = _clean_text(payload.get("condition_text"))
    symptom_keys = _string_list(payload.get("symptom_keys"))
    status = _clean_text(payload.get("status")) or ACTIVE_STATUS

    if strict and status != ACTIVE_STATUS:
        raise ValueError("only active domain rules can be synced")
    if not title:
        raise ValueError("title is required")
    if not conclusion:
        raise ValueError("conclusion is required")
    if strict and not condition_text:
        raise ValueError("condition_text is required")
    if not symptom_keys:
        raise ValueError("symptom_keys must contain at least one item")

    return DomainRule(
        rule_id=payload.get("rule_id"),
        rule_code=_clean_text(payload.get("rule_code")),
        doc_id=doc_id,
        status=status,
        title=title,
        device_type=_clean_text(payload.get("device_type")),
        symptom_keys=symptom_keys,
        condition_text=condition_text,
        conclusion=conclusion,
        question=_clean_text(payload.get("question")),
        options=_string_list(payload.get("options")),
        evidence_refs=_evidence_refs(payload.get("evidence_refs")),
    )


def _rule_metadata(rule: DomainRule) -> dict[str, Any]:
    return {
        "record_type": RULE_RECORD_TYPE,
        "status": ACTIVE_STATUS,
        "document_id": rule.doc_id,
        "chunk_type": RULE_RECORD_TYPE,
        "rule_id": rule.rule_id,
        "rule_code": rule.rule_code,
        "doc_id": rule.doc_id,
        "title": rule.title,
        "device_type": rule.device_type,
        "symptom_keys": rule.symptom_keys,
        "condition_text": rule.condition_text,
        "conclusion": rule.conclusion,
        "question": rule.question,
        "options": rule.options,
        "evidence_refs": rule.evidence_refs,
        "confidence_source": "rule",
    }


def _search_text(rule: DomainRule) -> str:
    parts = [
        f"规则标题: {rule.title}",
        f"设备类型: {rule.device_type}",
        f"症状关键词: {'、'.join(rule.symptom_keys)}",
        f"命中条件: {rule.condition_text}",
        f"诊断结论: {rule.conclusion}",
        f"追问问题: {rule.question}",
        f"追问选项: {'、'.join(rule.options)}",
    ]
    if rule.evidence_refs:
        parts.append(
            "证据来源: "
            + json.dumps(rule.evidence_refs, ensure_ascii=False, separators=(",", ":"))
        )
    return "\n".join(part for part in parts if part and not part.endswith(": "))


def _sync_response(doc_id: str, message: str = "ok") -> dict[str, Any]:
    return {"success": True, "code": 200, "message": message, "doc_id": doc_id}


async def upsert_domain_rule(payload: Mapping[str, Any]) -> dict[str, Any]:
    rule = _normalize_rule(payload, strict=True)
    text = _search_text(rule)
    try:
        vector = await get_text_embedding().embed(text)
        stored = get_vector_service().add_vector(
            doc_id=rule.doc_id,
            text=text,
            vector=vector,
            metadata=_rule_metadata(rule),
            category=RULE_RECORD_TYPE,
            tags=rule.symptom_keys,
        )
    except Exception as exc:
        raise DomainRuleServiceError(f"failed to upsert domain rule: {exc}") from exc
    if not stored:
        raise DomainRuleServiceError("vector store rejected domain rule")
    return _sync_response(rule.doc_id)


async def delete_domain_rule(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be an object")
    doc_id = _doc_id_from_payload(payload)
    try:
        get_vector_service().delete(doc_id)
    except Exception as exc:
        raise DomainRuleServiceError(f"failed to delete domain rule: {exc}") from exc
    return _sync_response(doc_id)


def _domain_rule_filter() -> str:
    return "(@record_type:{domain_rule}) (@status:{active})"


def _metadata_to_rule(result: Mapping[str, Any]) -> DomainRule | None:
    metadata = result.get("metadata") if isinstance(result.get("metadata"), Mapping) else {}
    payload = dict(metadata)
    payload.setdefault("doc_id", result.get("doc_id"))
    try:
        return _normalize_rule(payload, strict=False)
    except ValueError:
        return None


def _compatible_device(rule_device: str, requested_device: str | None) -> bool:
    requested = _compact(requested_device)
    rule_value = _compact(rule_device)
    return not requested or not rule_value or requested == rule_value


def _relevance_score(result: Mapping[str, Any]) -> float:
    if result.get("relevance_score") is not None:
        try:
            return max(0.0, min(1.0, float(result.get("relevance_score"))))
        except (TypeError, ValueError):
            return 0.0
    try:
        score = float(result.get("score", 0))
    except (TypeError, ValueError):
        return 0.0
    if result.get("raw_score_type") == "cosine_distance" or 0 <= score <= 1:
        return max(0.0, min(1.0, 1.0 - score))
    return 0.0


def _matched_symptoms(query: str, symptom_keys: list[str]) -> list[str]:
    compact_query = _compact(query)
    return [key for key in symptom_keys if _compact(key) and _compact(key) in compact_query]


def _public_rule(rule: DomainRule) -> dict[str, Any]:
    return {
        "rule_id": rule.rule_id,
        "rule_code": rule.rule_code,
        "doc_id": rule.doc_id,
        "title": rule.title,
        "device_type": rule.device_type,
        "symptom_keys": rule.symptom_keys,
        "condition_text": rule.condition_text,
        "conclusion": rule.conclusion,
        "question": rule.question,
        "options": rule.options,
        "evidence_refs": rule.evidence_refs,
    }


def _evidence_sources(
    rule: DomainRule,
    result: Mapping[str, Any],
    matched_symptom_keys: list[str],
    relevance_score: float,
) -> list[dict[str, Any]]:
    sources = [
        {
            "type": RULE_RECORD_TYPE,
            "source": "expert_rule",
            "doc_id": rule.doc_id,
            "rule_id": rule.rule_id,
            "rule_code": rule.rule_code,
            "title": rule.title,
            "matched_symptom_keys": matched_symptom_keys,
            "relevance_score": round(relevance_score, 6),
            "raw_score": result.get("raw_score", result.get("score")),
        }
    ]
    for ref in rule.evidence_refs[:5]:
        sources.append({"type": "evidence_ref", **ref})
    return sources


def build_domain_rule_message(rule: DomainRule, matched_symptom_keys: list[str]) -> str:
    lines = [
        f"确定性规则命中：{rule.title}",
        f"结论：{rule.conclusion}",
    ]
    if rule.condition_text:
        lines.append(f"命中条件：{rule.condition_text}")
    if matched_symptom_keys:
        lines.append(f"匹配症状：{'、'.join(matched_symptom_keys)}")
    if rule.evidence_refs:
        lines.append("依据来源：专家审核规则与已绑定证据。")
    if rule.question:
        lines.append(f"为了进一步确认根因，请补充：{rule.question}")
        if rule.options:
            for idx, option in enumerate(rule.options[:6]):
                if re.match(r"^[A-Z]\s*[.、]", option, flags=re.IGNORECASE):
                    lines.append(option)
                else:
                    lines.append(f"{chr(65 + idx)}. {option}")
    return "\n".join(lines)


async def match_domain_rule(
    query: str,
    *,
    device_type: str | None = None,
    top_k: int = 5,
) -> dict[str, Any] | None:
    query_text = _clean_text(query)
    if not query_text:
        return None

    try:
        results = await get_vector_service().search_by_text(
            query_text,
            top_k=top_k,
            include_metadata=True,
            filter=_domain_rule_filter(),
        )
    except Exception as exc:
        raise DomainRuleServiceError(f"failed to match domain rule: {exc}") from exc

    best: dict[str, Any] | None = None
    best_score = -1.0
    for result in results or []:
        if not isinstance(result, Mapping):
            continue
        rule = _metadata_to_rule(result)
        if rule is None:
            continue
        if not _compatible_device(rule.device_type, device_type):
            continue
        matched = _matched_symptoms(query_text, rule.symptom_keys)
        if not matched:
            continue
        relevance = _relevance_score(result)
        keyword_ratio = len(matched) / max(len(rule.symptom_keys), 1)
        if relevance < MIN_RELEVANCE_FOR_DIRECT_HIT and keyword_ratio < MIN_KEYWORD_RATIO_FOR_DIRECT_HIT:
            continue
        combined_score = round(relevance * 0.7 + keyword_ratio * 0.3, 6)
        if combined_score <= best_score:
            continue
        best_score = combined_score
        best = {
            "matched": True,
            "confidence_source": "rule",
            "confidence_label": "确定",
            "score": combined_score,
            "vector_relevance_score": round(relevance, 6),
            "keyword_match_ratio": round(keyword_ratio, 6),
            "matched_symptom_keys": matched,
            "rule": _public_rule(rule),
            "evidence_sources": _evidence_sources(rule, result, matched, relevance),
            "message": build_domain_rule_message(rule, matched),
        }

    return best
