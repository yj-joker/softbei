"""Serializable clarification state shared by conflicts and diagnostic follow-ups."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping, Sequence


_PENDING_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_DEFAULT_PENDING_TTL_SECONDS = 15 * 60


@dataclass(frozen=True)
class PendingClarification:
    clarification_id: str
    kind: str
    subject: str
    alternatives: tuple[dict[str, Any], ...]
    evidence_refs: tuple[str, ...]
    missing_identity_fields: tuple[str, ...]
    question: str
    status: str
    original_query: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["alternatives"] = [dict(item) for item in self.alternatives]
        value["evidence_refs"] = list(self.evidence_refs)
        value["missing_identity_fields"] = list(self.missing_identity_fields)
        return value


def _stable_id(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"clarification-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:16]}"


def _evidence_identity(entry: Mapping[str, Any]) -> tuple[str, str]:
    source = entry.get("source") if isinstance(entry.get("source"), Mapping) else {}
    return str(entry.get("evidence_id") or ""), str(source.get("chunk_id") or "")


def _source_label(entry: Mapping[str, Any]) -> str:
    source = entry.get("source") if isinstance(entry.get("source"), Mapping) else {}
    parts: list[str] = []
    page = source.get("page")
    version = str(source.get("document_version") or "").strip()
    document_id = str(source.get("document_id") or "").strip()
    device_type = str(source.get("device_type") or source.get("device_model") or "").strip()
    if page not in (None, ""):
        parts.append(f"手册第{page}页")
    elif document_id:
        parts.append(f"手册{document_id}")
    if version:
        parts.append(f"版本{version}")
    if device_type:
        parts.append(f"设备{device_type}")
    return "，".join(parts) or "知识库证据"


def _source_identity(entry: Mapping[str, Any]) -> dict[str, Any]:
    source = entry.get("source") if isinstance(entry.get("source"), Mapping) else {}
    return {
        "page": source.get("page"),
        "document_id": str(source.get("document_id") or "").strip(),
        "document_version": str(source.get("document_version") or "").strip(),
        "device_type": str(source.get("device_type") or source.get("device_model") or "").strip(),
    }


def _matching_evidence(
    candidate_ids: Iterable[Any],
    evidence: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    wanted = {str(value) for value in candidate_ids if value not in (None, "")}
    matched: list[Mapping[str, Any]] = []
    for entry in evidence:
        evidence_id, chunk_id = _evidence_identity(entry)
        if chunk_id in wanted or evidence_id in wanted or any(evidence_id.endswith(f":{value}") for value in wanted):
            matched.append(entry)
    return matched


def build_evidence_conflict_clarification(
    query: str,
    conflict: Mapping[str, Any],
    evidence: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    semantic_fields = {
        str(value).strip()
        for value in conflict.get("semantic_fields") or []
        if str(value).strip()
    }
    if len(semantic_fields) > 1 or str(conflict.get("kind") or "") == "surface_conflict":
        return None

    raw_alternatives = [item for item in conflict.get("alternatives") or [] if isinstance(item, Mapping)]
    if len(raw_alternatives) < 2:
        raw_alternatives = [
            {"value": value, "candidate_ids": []}
            for value in conflict.get("values") or []
        ]
    if len(raw_alternatives) < 2:
        return None

    unit = str(conflict.get("unit") or "").strip()
    alternatives: list[dict[str, Any]] = []
    evidence_refs: list[str] = []
    for index, raw in enumerate(raw_alternatives):
        matched = _matching_evidence(raw.get("candidate_ids") or [], evidence)
        refs = [str(item.get("evidence_id") or "") for item in matched if item.get("evidence_id")]
        for ref in refs:
            if ref not in evidence_refs:
                evidence_refs.append(ref)
        value = str(raw.get("value") or "").strip()
        source_labels = [_source_label(item) for item in matched]
        source_identities = [_source_identity(item) for item in matched]
        primary_identity = source_identities[0] if source_identities else {}
        alternatives.append({
            "id": chr(ord("A") + index),
            "value": value,
            "unit": unit,
            "label": f"{value}{(' ' + unit) if unit else ''}",
            "candidate_ids": [str(item) for item in raw.get("candidate_ids") or []],
            "evidence_refs": refs,
            "source_labels": list(dict.fromkeys(source_labels)),
            "source_identities": source_identities,
            "page": primary_identity.get("page"),
            "document_id": primary_identity.get("document_id", ""),
            "document_version": primary_identity.get("document_version", ""),
            "device_type": primary_identity.get("device_type", ""),
        })

    subject = str(conflict.get("field") or "关键参数").strip()
    missing_fields = tuple(str(item) for item in conflict.get("missing_identity_fields") or ("设备型号", "文档版本"))
    identity = {
        "kind": "evidence_conflict",
        "subject": subject,
        "alternatives": alternatives,
        "query": str(query or ""),
    }
    pending = PendingClarification(
        clarification_id=_stable_id(identity),
        kind="evidence_conflict",
        subject=subject,
        alternatives=tuple(alternatives),
        evidence_refs=tuple(evidence_refs),
        missing_identity_fields=missing_fields,
        question=f"请确认适用的{'或'.join(missing_fields)}，也可以直接选择 A/B。",
        status="awaiting_answer",
        original_query=str(query or ""),
    )
    return pending.to_dict()


def _pending_cache_key(session_id: str) -> str:
    digest = hashlib.sha256(str(session_id or "").encode("utf-8")).hexdigest()
    return f"fixagent:pending_clarification:{digest}"


def remember_pending_clarification(
    session_id: str,
    pending: Mapping[str, Any] | None,
    *,
    redis_client: Any = None,
    ttl_seconds: int = _DEFAULT_PENDING_TTL_SECONDS,
) -> None:
    if not session_id or not isinstance(pending, Mapping):
        return
    if pending.get("kind") != "evidence_conflict" or pending.get("status") != "awaiting_answer":
        return
    payload = dict(pending)
    key = _pending_cache_key(session_id)
    ttl = max(int(ttl_seconds), 1)
    _PENDING_CACHE[key] = (time.time() + ttl, payload)
    if redis_client is not None:
        try:
            redis_client.setex(
                key,
                ttl,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            )
        except Exception:
            pass


def load_pending_clarification(
    session_id: str,
    *,
    client_pending: Mapping[str, Any] | None = None,
    redis_client: Any = None,
) -> dict[str, Any] | None:
    """Load server-owned state; client_pending is never used as evidence data."""
    if not session_id:
        return None
    key = _pending_cache_key(session_id)
    trusted: dict[str, Any] | None = None
    if redis_client is not None:
        try:
            raw = redis_client.get(key)
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            if raw:
                loaded = json.loads(raw)
                if isinstance(loaded, dict):
                    trusted = loaded
        except Exception:
            trusted = None
    if trusted is None:
        cached = _PENDING_CACHE.get(key)
        if cached:
            expires_at, payload = cached
            if expires_at > time.time():
                trusted = dict(payload)
            else:
                _PENDING_CACHE.pop(key, None)
    if not isinstance(trusted, dict):
        return None
    if trusted.get("kind") != "evidence_conflict" or trusted.get("status") != "awaiting_answer":
        return None
    return trusted


def clear_pending_clarification(session_id: str, *, redis_client: Any = None) -> None:
    if not session_id:
        return
    key = _pending_cache_key(session_id)
    _PENDING_CACHE.pop(key, None)
    if redis_client is not None:
        try:
            redis_client.delete(key)
        except Exception:
            pass


def build_diagnostic_clarification(
    *,
    scenario_id: str,
    query: str,
    subject: str,
    question: str,
    alternatives: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    public_alternatives = [dict(item) for item in alternatives]
    return PendingClarification(
        clarification_id=_stable_id({
            "kind": "diagnostic_cause",
            "scenario_id": scenario_id,
            "query": query,
            "alternatives": public_alternatives,
        }),
        kind="diagnostic_cause",
        subject=subject,
        alternatives=tuple(public_alternatives),
        evidence_refs=(),
        missing_identity_fields=(),
        question=question,
        status="awaiting_answer",
        original_query=query,
    ).to_dict()


def resolve_pending_clarification(
    context: Mapping[str, Any] | None,
    answer_text: str,
) -> dict[str, Any] | None:
    if not isinstance(context, Mapping):
        return None
    pending = context.get("pending_clarification")
    if not isinstance(pending, Mapping):
        return None
    if pending.get("kind") != "evidence_conflict" or pending.get("status") != "awaiting_answer":
        return None

    alternatives = [
        alternative
        for alternative in pending.get("alternatives") or []
        if isinstance(alternative, Mapping)
    ]
    selected = _select_alternative(
        alternatives,
        str(answer_text or ""),
        str(context.get("selected_clarification_option_id") or ""),
    )
    if selected is None:
        return None

    resolved = dict(pending)
    resolved.update({
        "status": "resolved",
        "selected_option_id": str(selected.get("id") or ""),
        "selected_value": str(selected.get("value") or ""),
        "selected_unit": str(selected.get("unit") or ""),
        "selected_evidence_refs": [str(item) for item in selected.get("evidence_refs") or []],
        "selected_source_labels": [str(item) for item in selected.get("source_labels") or []],
    })
    return resolved


def _select_alternative(
    alternatives: Sequence[Mapping[str, Any]],
    answer_text: str,
    selected_option_id: str,
) -> Mapping[str, Any] | None:
    by_id = {
        str(item.get("id") or "").strip().upper(): item
        for item in alternatives
        if str(item.get("id") or "").strip()
    }
    explicit_id = str(selected_option_id or "").strip().upper()
    if explicit_id in by_id:
        return by_id[explicit_id]

    answer = str(answer_text or "").strip()
    for option_id, alternative in by_id.items():
        if re.match(
            rf"^(?:(?:选择|选|采用|使用|按)\s*)?{re.escape(option_id)}(?:[.、，,。：:；;\s]|$)",
            answer,
            flags=re.IGNORECASE,
        ):
            return alternative

    source_scores = [
        (_source_identity_match_score(alternative, answer), alternative)
        for alternative in alternatives
    ]
    best_source_score = max((score for score, _ in source_scores), default=0)
    source_matches = [
        alternative for score, alternative in source_scores
        if score == best_source_score and score > 0
    ]
    if len(source_matches) == 1:
        return source_matches[0]

    measurement_matches = [
        alternative
        for alternative in alternatives
        if _matches_complete_measurement(alternative, answer)
    ]
    if len(measurement_matches) == 1:
        return measurement_matches[0]
    return None


def _compact_match_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _source_identity_match_score(alternative: Mapping[str, Any], answer_text: str) -> int:
    answer = _compact_match_text(answer_text)
    score = 0
    identities = [
        item for item in alternative.get("source_identities") or []
        if isinstance(item, Mapping)
    ]
    if not identities:
        identities = [{
            "page": alternative.get("page"),
            "document_id": alternative.get("document_id"),
            "document_version": alternative.get("document_version"),
            "device_type": alternative.get("device_type"),
        }]
    for identity in identities:
        page = identity.get("page")
        if page not in (None, "") and re.search(rf"第\s*{re.escape(str(page))}\s*页", answer_text):
            score += 1
        for key in ("document_id", "device_type"):
            value = _compact_match_text(identity.get(key))
            if value and value in answer:
                score += 2
        version = str(identity.get("document_version") or "").strip()
        if version and re.search(
            rf"(?<![A-Za-z0-9._-]){re.escape(version)}(?![A-Za-z0-9._-])",
            answer_text,
            flags=re.IGNORECASE,
        ):
            score += 2
    for raw_label in alternative.get("source_labels") or []:
        label = str(raw_label or "").strip()
        if not label:
            continue
        if _compact_match_text(label) in answer:
            score += 4
    return score


def _matches_complete_measurement(alternative: Mapping[str, Any], answer_text: str) -> bool:
    value = _compact_match_text(alternative.get("value"))
    unit = _compact_match_text(alternative.get("unit"))
    if not value or not unit:
        return False
    answer = _compact_match_text(answer_text)
    if unit not in answer:
        return False
    return re.search(rf"(?<![\d.]){re.escape(value)}(?![\d.])", answer) is not None


def format_pending_resolution(resolved: Mapping[str, Any]) -> str:
    query = str(resolved.get("original_query") or "").strip().rstrip("？?")
    subject = str(resolved.get("subject") or "关键参数").strip()
    value = str(resolved.get("selected_value") or "").strip()
    unit = str(resolved.get("selected_unit") or "").strip()
    sources = "、".join(str(item) for item in resolved.get("selected_source_labels") or [] if item)
    query_prefix = f"关于“{query}”：" if query else ""
    source_suffix = f"（来源：{sources}）" if sources else ""
    return f"{query_prefix}{subject}为 {value}{(' ' + unit) if unit else ''}{source_suffix}。"
