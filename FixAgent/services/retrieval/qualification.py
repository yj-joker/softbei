"""Classify retrieved maintenance evidence before it reaches the answering model.

Ranking chooses the best available candidates. Qualification decides whether those
candidates are usable as evidence for the current device and task.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


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
) -> Dict[str, Any]:
    """Return a serializable evidence bundle without exposing rejected content.

    Explicit retrieval scope is a hard boundary. Without a scope, evidence may be
    useful as a reference but cannot be presented as the user's device manual.
    """
    qualified: List[Dict[str, Any]] = []
    references: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []

    for index, raw in enumerate(candidates or []):
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        metadata = dict(item.get("metadata") or {})
        item["metadata"] = metadata
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

    conflicts = _detect_conflicts(qualified)
    if conflicts:
        for item in qualified:
            metadata = item["metadata"]
            metadata["qualification"] = REFERENCE_ONLY
            metadata["qualification_reasons"] = list(metadata["qualification_reasons"]) + ["evidence_conflict"]
            metadata["direct_answer_eligible"] = False
            references.append(item)
        qualified = []

    status = QUALIFIED if qualified else REFERENCE_ONLY if references else "no_evidence"
    capabilities = {
        "may_cite_manual": bool(qualified),
        "may_emit_exact_parameter": bool(qualified) and not conflicts,
        "may_emit_device_specific_procedure": bool(qualified) and not conflicts,
        "may_offer_generic_guidance": True,
    }
    return {
        "evidence_bundle_version": 1,
        "overall_status": status,
        "qualified_evidence": qualified,
        "reference_evidence": references,
        "excluded_evidence": excluded,
        "conflicts": conflicts,
        "capabilities": capabilities,
        "summary": {
            "qualified_count": len(qualified),
            "reference_count": len(references),
            "excluded_count": len(excluded),
            "has_explicit_scope": bool(document_id or device_type),
        },
    }


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


def _detect_conflicts(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    values: Dict[tuple[str, str], set[str]] = {}
    for item in items:
        metadata = item.get("metadata") or {}
        names = metadata.get("parameter_names") or []
        numbers = metadata.get("numeric_values") or []
        units = metadata.get("units") or []
        if not isinstance(names, list) or not isinstance(numbers, list):
            continue
        unit = str(units[0]) if isinstance(units, list) and units else ""
        for name, number in zip(names, numbers):
            if name and number is not None:
                values.setdefault((str(name), unit), set()).add(str(number))
    return [
        {"field": name, "unit": unit, "values": sorted(numbers), "impact": "manual_claim_blocked"}
        for (name, unit), numbers in values.items()
        if len(numbers) > 1
    ]


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
