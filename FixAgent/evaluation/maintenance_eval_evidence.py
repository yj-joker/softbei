"""Deterministic adapters for evidence returned in maintenance ReAct traces."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence

from evaluation.maintenance_eval_schema import (
    AllowedSource,
    ClaimConstraint,
    ConflictAlternative,
    ConflictConstraint,
    MaintenanceEvalTurn,
)
from services.retrieval.graph_evidence import normalize_graph_response


SourceType = Literal["manual", "domain_rule", "graph"]
CoverageStatus = Literal["complete", "partial", "unsupported", "conflict"]


@dataclass(frozen=True)
class EvidenceEnvelope:
    evidence_id: str
    source_type: SourceType
    text: str
    qualification: str
    source: dict[str, Any]
    conflict_eligible: bool = False


@dataclass
class EvidenceTraceResult:
    envelopes: list[EvidenceEnvelope] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    trace_missing: bool = False


@dataclass
class TurnEvidenceScore:
    final_pass: bool
    coverage_status: CoverageStatus
    unsupported_completion_free: bool
    partial_answer_correct: bool
    conflict_handling_pass: bool
    source_style_mode_pass: bool
    diagnostics: list[str] = field(default_factory=list)
    evidence_nugget_coverage_rate: float = 1.0
    evidence_source_pass: bool = True
    answer_evidence_alignment_pass: bool = True
    scope_isolation_pass: bool = True
    refusal_integrity_pass: bool = True
    fixed_template_detected: bool = False
    style_proxy_pass: bool = True
    source_mode_pass: bool = True


def _string(value: Any) -> str:
    return str(value or "").strip()


def _string_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [_string(item) for item in value if _string(item)]
    text = _string(value)
    return [text] if text else []


def _first_value(data: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, "", []):
            return value
    return None


def _text_from_mapping(data: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in ("content", "text", "summary", "caption", "image_summary", "message"):
        value = _string(data.get(key))
        if value and value not in parts:
            parts.append(value)
    return "\n".join(parts)


def _manual_items(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, Mapping):
        items: list[Mapping[str, Any]] = []
        for key, default_qualification in (
            ("results", ""),
            ("qualified_evidence", "qualified"),
            ("reference_evidence", "reference_only"),
        ):
            value = payload.get(key)
            if not isinstance(value, list):
                continue
            for item in value:
                if not isinstance(item, Mapping):
                    continue
                if default_qualification and not (
                    item.get("qualification")
                    or (
                        isinstance(item.get("metadata"), Mapping)
                        and item["metadata"].get("qualification")
                    )
                ):
                    item = {**item, "qualification": default_qualification}
                items.append(item)
        if items:
            return items
        payload = payload.get("data") or []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, Mapping)]


def _manual_envelopes(payload: Any, diagnostics: list[str]) -> list[EvidenceEnvelope]:
    envelopes: list[EvidenceEnvelope] = []
    for item in _manual_items(payload):
        metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
        qualification = _string(metadata.get("qualification") or item.get("qualification"))
        if qualification not in {"qualified", "reference_only"}:
            continue
        document_id = _string(metadata.get("document_id") or item.get("document_id"))
        chunk_id = _string(
            metadata.get("chunk_id")
            or item.get("chunk_id")
            or item.get("id")
            or item.get("doc_id")
        )
        if not document_id or not chunk_id:
            diagnostics.append("manual_source_identity_missing")
            continue
        source: dict[str, Any] = {
            "document_id": document_id,
            "document_version": _string(metadata.get("document_version")),
            "page": metadata.get("page") if metadata.get("page") is not None else metadata.get("page_number"),
            "chunk_id": chunk_id,
        }
        stable_chunk_ids = list(dict.fromkeys(
            value
            for key in ("chunk_uid", "row_id", "table_id", "parent_chunk_id", "continuation_id")
            for value in _string_list(metadata.get(key))
        ))
        if stable_chunk_ids:
            source["chunk_ids"] = stable_chunk_ids
        envelopes.append(
            EvidenceEnvelope(
                evidence_id=f"manual:{document_id}:{chunk_id}",
                source_type="manual",
                text=_text_from_mapping(item),
                qualification=qualification,
                source=source,
                conflict_eligible=True,
            )
        )
    return envelopes


def _domain_rule_envelopes(payload: Any, diagnostics: list[str]) -> list[EvidenceEnvelope]:
    if not isinstance(payload, Mapping):
        return []
    rule = payload.get("rule") if isinstance(payload.get("rule"), Mapping) else {}
    rule_id = _string(rule.get("rule_id") or payload.get("rule_id"))
    status = _string(rule.get("status") or payload.get("status"))
    if not rule_id or status != "active":
        diagnostics.append("domain_rule_identity_or_status_invalid")
        return []
    text = _text_from_mapping(payload) or _text_from_mapping(rule)
    return [
        EvidenceEnvelope(
            evidence_id=f"domain_rule:{rule_id}",
            source_type="domain_rule",
            text=text,
            qualification="qualified",
            source={
                "rule_id": rule_id,
                "status": status,
                "evidence_sources": list(payload.get("evidence_sources") or []),
            },
            conflict_eligible=True,
        )
    ]


def _graph_record_text(record: Mapping[str, Any]) -> str:
    parts = [
        _string(record.get("content") or record.get("text") or record.get("summary")),
        _string(record.get("deviceName") or record.get("device")),
        _string(record.get("componentName") or record.get("component")),
        _string(record.get("faultName") or record.get("fault")),
        _string(record.get("solutionTitle") or record.get("solution")),
    ]
    solutions = record.get("solutions")
    if isinstance(solutions, list):
        for solution in solutions:
            if isinstance(solution, Mapping):
                parts.append(_string(solution.get("title") or solution.get("name")))
            else:
                parts.append(_string(solution))
    return " -> ".join(part for index, part in enumerate(parts) if part and part not in parts[:index])


def _graph_envelopes(payload: Any, diagnostics: list[str]) -> list[EvidenceEnvelope]:
    if not isinstance(payload, Mapping):
        return []
    if "evidence" in payload:
        records = payload.get("evidence")
        records = records if isinstance(records, list) else []
    else:
        raw_records = payload.get("raw_records") or payload.get("records") or []
        if not isinstance(raw_records, list):
            return []
        batch = normalize_graph_response({
            "status": payload.get("status") or "found",
            "records": raw_records,
        })
        records = [item.to_dict() for item in batch.evidence]
        if batch.diagnostics.get("rejected_count"):
            diagnostics.append("graph_raw_record_rejected")
    envelopes: list[EvidenceEnvelope] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        qualification = _string(record.get("qualification"))
        if qualification != "qualified":
            continue
        path_ids = _string_list(_first_value(record, "pathIds", "path_ids", "pathId", "path_id"))
        node_ids = _string_list(_first_value(record, "nodeIds", "node_ids", "nodeId", "node_id"))
        relationship_types = _string_list(
            _first_value(record, "relationshipTypes", "relationship_types", "relationshipType")
        )
        if not path_ids and not node_ids:
            diagnostics.append("graph_source_identity_missing")
            continue
        evidence_id = _string(record.get("evidence_id"))
        if not evidence_id:
            stable_id = path_ids[0] if path_ids else ":".join(node_ids)
            solution = record.get("solution") if isinstance(record.get("solution"), Mapping) else {}
            solution_id = _string(solution.get("id")) or "none"
            evidence_id = f"graph:{stable_id}:{solution_id}"
        if not evidence_id.startswith("graph:"):
            diagnostics.append("graph_evidence_id_invalid")
            continue
        record_source = record.get("source") if isinstance(record.get("source"), Mapping) else {}
        pages = _string_list(_first_value(record_source, "pages", "page"))
        chunk_ids = _string_list(
            _first_value(record_source, "source_chunk_uids", "chunk_ids", "chunk_uid", "chunk_id")
        )
        envelopes.append(
            EvidenceEnvelope(
                evidence_id=evidence_id,
                source_type="graph",
                text=_graph_record_text(record),
                qualification=qualification,
                source={
                    "document_id": _string(record_source.get("document_id")),
                    "document_version": _string(record_source.get("document_version")),
                    "pages": pages,
                    "chunk_ids": chunk_ids,
                    "node_ids": node_ids,
                    "relationship_types": relationship_types,
                    "path_ids": path_ids,
                },
                conflict_eligible=True,
            )
        )
    return envelopes


def _tool_payload(tool_call: Mapping[str, Any]) -> tuple[bool, Any]:
    for key in ("result_data", "data", "result"):
        if key in tool_call and tool_call.get(key) is not None:
            return True, tool_call.get(key)
    return False, None


def _evidence_preference_key(envelope: EvidenceEnvelope) -> tuple[int, int, int, int]:
    """Rank duplicate evidence without changing its provenance identity.

    A qualified envelope must win over a stale reference-only envelope for the
    same source identity.  When qualification is equal, prefer the envelope
    carrying more source metadata and text so later, richer tool results are
    not discarded by an earlier sparse result.
    """

    qualification_rank = 1 if envelope.qualification == "qualified" else 0
    source_completeness = sum(
        value not in (None, "", [], {}, ()) for value in envelope.source.values()
    )
    return (
        qualification_rank,
        source_completeness,
        1 if envelope.text else 0,
        len(envelope.text),
    )


def extract_evidence_envelopes(metadata: Mapping[str, Any] | None) -> EvidenceTraceResult:
    diagnostics: list[str] = []
    envelopes: list[EvidenceEnvelope] = []
    trace = (metadata or {}).get("react_trace") if isinstance(metadata, Mapping) else None
    if not isinstance(trace, list):
        return EvidenceTraceResult(diagnostics=["evidence_trace_missing"], trace_missing=True)

    adapters = {
        "knowledge_retrieval": _manual_envelopes,
        "domain_rule_engine": _domain_rule_envelopes,
        "java_graph_diagnosis_path": _graph_envelopes,
    }
    for step in trace:
        if not isinstance(step, Mapping):
            continue
        tool_calls = step.get("tool_calls") or []
        if not isinstance(tool_calls, list):
            continue
        for tool_call in tool_calls:
            if not isinstance(tool_call, Mapping):
                continue
            adapter = adapters.get(_string(tool_call.get("name")))
            if adapter is None:
                continue
            has_payload, payload = _tool_payload(tool_call)
            if not has_payload:
                continue
            envelopes.extend(adapter(payload, diagnostics))

    deduped: list[EvidenceEnvelope] = []
    seen: dict[str, int] = {}
    for envelope in envelopes:
        existing_index = seen.get(envelope.evidence_id)
        if existing_index is None:
            seen[envelope.evidence_id] = len(deduped)
            deduped.append(envelope)
            continue
        existing = deduped[existing_index]
        if _evidence_preference_key(envelope) > _evidence_preference_key(existing):
            deduped[existing_index] = envelope
    diagnostics = list(dict.fromkeys(diagnostics))
    trace_missing = not deduped
    if trace_missing and "evidence_trace_missing" not in diagnostics:
        diagnostics.append("evidence_trace_missing")
    return EvidenceTraceResult(
        envelopes=deduped,
        diagnostics=diagnostics,
        trace_missing=trace_missing,
    )


def decide_coverage_status(
    *,
    expected_scope: str,
    aspect_support: Sequence[bool],
    has_conflict: bool,
) -> CoverageStatus:
    if expected_scope == "out_of_scope" or not aspect_support:
        return "unsupported"
    if has_conflict:
        return "conflict"
    supported = sum(bool(value) for value in aspect_support)
    if supported == 0:
        return "unsupported"
    if supported == len(aspect_support):
        return "complete"
    return "partial"


_REFUSAL_HINTS = (
    "cannot determine",
    "can't determine",
    "cannot answer",
    "not enough information",
    "insufficient information",
    "insufficient evidence",
    "not available",
    "not provided",
    "not found",
    "outside the available material",
    "outside the knowledge base",
    "\u65e0\u6cd5\u786e\u5b9a",
    "\u65e0\u6cd5\u56de\u7b54",
    "\u4e0d\u80fd\u786e\u5b9a",
    "\u8d44\u6599\u4e0d\u8db3",
    "\u4f9d\u636e\u4e0d\u8db3",
    "\u672a\u627e\u5230",
    "\u6ca1\u6709\u627e\u5230",
    "\u672a\u63d0\u4f9b",
    "\u6ca1\u6709\u63d0\u4f9b",
    "\u672a\u8bf4\u660e",
    "\u6ca1\u6709\u660e\u786e\u8bf4\u660e",
    "\u4e0d\u5728\u5f53\u524d\u8d44\u6599",
    "\u4e0d\u5728\u5f53\u524d\u624b\u518c",
    "\u8d85\u51fa\u5f53\u524d\u77e5\u8bc6\u5e93",
)

_MANUAL_LEADS = (
    "according to the manual",
    "according to manual",
    "based on the manual",
    "the manual states",
    "the manual says",
    "the document states",
    "the document shows",
    "\u6839\u636e\u624b\u518c",
    "\u4f9d\u636e\u624b\u518c",
    "\u6309\u7167\u624b\u518c",
    "\u8d44\u6599\u663e\u793a",
    "\u6587\u6863\u6307\u51fa",
    "\u624b\u518c\u663e\u793a",
    "\u624b\u518c\u6307\u51fa",
    "\u6839\u636e\u8d44\u6599",
    "\u4f9d\u636e\u8d44\u6599",
)


@dataclass(frozen=True)
class _ClaimObservation:
    constraint: ClaimConstraint
    answer_asserted: bool
    supported: bool
    disclosure_present: bool
    forbidden_asserted: bool


@dataclass(frozen=True)
class _ConflictObservation:
    constraint: ConflictConstraint
    alternative_envelopes: tuple[tuple[EvidenceEnvelope, ...], ...]
    distinct_sources_present: bool


def _normalized(value: Any) -> str:
    text = unicodedata.normalize("NFKC", _string(value)).casefold()
    text = text.replace("跟换", "更换")
    return "".join(character for character in text if character.isalnum())


def _number_tokens(value: str) -> list[str]:
    text = unicodedata.normalize("NFKC", value).casefold()
    return [token.replace(",", ".") for token in re.findall(r"(?<!\d)\d+(?:[.,]\d+)?(?!\d)", text)]


def _pattern_matched(pattern: str, text: str) -> bool:
    pattern_norm = _normalized(pattern)
    text_norm = _normalized(text)
    if not pattern_norm:
        return True
    if pattern_norm not in text_norm:
        return False
    expected_numbers = _number_tokens(pattern)
    if expected_numbers:
        actual_numbers = _number_tokens(text)
        if not all(number in actual_numbers for number in expected_numbers):
            return False
    return True


def _matches_any(patterns: Sequence[str], text: str) -> bool:
    return bool(patterns) and any(_pattern_matched(pattern, text) for pattern in patterns)


def _clauses(answer: str) -> list[str]:
    return [
        clause.strip()
        for clause in re.split(r"[\r\n.!?;\u3002\uff01\uff1f\uff1b]+", answer or "")
        if clause.strip()
    ]


def _clause_is_negated(clause: str) -> bool:
    return any(_pattern_matched(hint, clause) for hint in _REFUSAL_HINTS)


def _pattern_asserted(pattern: str, answer: str) -> bool:
    matching_clauses = [clause for clause in _clauses(answer) if _pattern_matched(pattern, clause)]
    if not matching_clauses:
        return False
    return any(not _clause_is_negated(clause) for clause in matching_clauses)


def _any_pattern_asserted(patterns: Sequence[str], answer: str) -> bool:
    return any(_pattern_asserted(pattern, answer) for pattern in patterns)


def _contains_refusal(answer: str) -> bool:
    return any(_clause_is_negated(clause) for clause in _clauses(answer))


def _assertion_after_refusal(patterns: Sequence[str], answer: str) -> bool:
    clauses = _clauses(answer)
    refusal_indexes = [index for index, clause in enumerate(clauses) if _clause_is_negated(clause)]
    if not refusal_indexes:
        return False
    first_refusal = refusal_indexes[0]
    return any(
        _pattern_asserted(pattern, clause)
        for clause in clauses[first_refusal + 1 :]
        for pattern in patterns
    )


def _any_dimension_matches(expected: Sequence[Any], actual: Any) -> bool:
    if not expected:
        return True
    actual_values = actual if isinstance(actual, (list, tuple, set)) else [actual]
    return bool({_string(value) for value in expected} & {_string(value) for value in actual_values})


def _all_dimensions_match(expected: Sequence[Any], actual: Any) -> bool:
    if not expected:
        return True
    actual_values = actual if isinstance(actual, (list, tuple, set)) else [actual]
    expected_set = {_string(value) for value in expected if _string(value)}
    actual_set = {_string(value) for value in actual_values if _string(value)}
    return expected_set.issubset(actual_set)


def _path_dimension_matches(expected: Sequence[Any], actual: Any) -> bool:
    if not expected:
        return True
    actual_values = actual if isinstance(actual, (list, tuple, set)) else [actual]
    expected_set = {_string(value) for value in expected if _string(value)}
    actual_set = {_string(value) for value in actual_values if _string(value)}
    return expected_set == actual_set


def _page_dimension_matches(expected: Sequence[int], actual: Any) -> bool:
    if not expected:
        return True
    actual_values = actual if isinstance(actual, (list, tuple, set)) else [actual]
    try:
        expected_pages = {int(page) for page in expected}
        actual_pages = {int(page) for page in actual_values}
    except (TypeError, ValueError):
        return False
    if len(actual_pages) == 2:
        start, end = sorted(actual_pages)
        return any(start <= page <= end for page in expected_pages)
    return bool(expected_pages & actual_pages)


def _allowed_source_matches(envelope: EvidenceEnvelope, allowed: AllowedSource) -> bool:
    if envelope.source_type != allowed.source_type:
        return False
    source = envelope.source
    if envelope.source_type == "manual":
        chunk_ids = [source.get("chunk_id"), *(source.get("chunk_ids") or [])]
        return bool(
            (not allowed.document_id or source.get("document_id") == allowed.document_id)
            and (
                not allowed.document_version
                or source.get("document_version") == allowed.document_version
            )
            and _page_dimension_matches(allowed.pages, source.get("page"))
            and _any_dimension_matches(allowed.chunk_ids, chunk_ids)
        )
    if envelope.source_type == "domain_rule":
        return bool(
            (not allowed.rule_id or source.get("rule_id") == allowed.rule_id)
            and (not allowed.status or source.get("status") == allowed.status)
        )
    if envelope.source_type == "graph":
        return bool(
            (not allowed.document_id or source.get("document_id") == allowed.document_id)
            and (
                not allowed.document_version
                or source.get("document_version") == allowed.document_version
            )
            and _page_dimension_matches(allowed.pages, source.get("pages") or [])
            and _all_dimensions_match(allowed.chunk_ids, source.get("chunk_ids") or [])
            and _all_dimensions_match(allowed.node_ids, source.get("node_ids") or [])
            and _all_dimensions_match(
                allowed.relationship_types, source.get("relationship_types") or []
            )
            and _path_dimension_matches(allowed.path_ids, source.get("path_ids") or [])
        )
    return False


def _matches_allowed_sources(
    envelope: EvidenceEnvelope,
    allowed_sources: Sequence[AllowedSource],
) -> bool:
    return bool(allowed_sources) and any(
        _allowed_source_matches(envelope, allowed) for allowed in allowed_sources
    )


def _qualified_claim_envelopes(
    constraint: ClaimConstraint,
    envelopes: Sequence[EvidenceEnvelope],
) -> tuple[list[EvidenceEnvelope], list[EvidenceEnvelope]]:
    text_matches = [
        envelope
        for envelope in envelopes
        if envelope.qualification == "qualified"
        and _matches_any(constraint.evidence_patterns, envelope.text)
    ]
    allowed_matches = [
        envelope
        for envelope in text_matches
        if _matches_allowed_sources(envelope, constraint.allowed_sources)
    ]
    return text_matches, allowed_matches


def _observe_claims(
    constraints: Sequence[ClaimConstraint],
    answer: str,
    envelopes: Sequence[EvidenceEnvelope],
    *,
    force_unsupported: bool,
    require_graph_binding: bool,
    final_bound_graph_ids: set[str],
    diagnostics: list[str],
) -> list[_ClaimObservation]:
    observations: list[_ClaimObservation] = []
    for constraint in constraints:
        text_matches, allowed_matches = _qualified_claim_envelopes(constraint, envelopes)
        supported = bool(allowed_matches) and not force_unsupported
        graph_constraint = any(
            allowed.source_type == "graph" for allowed in constraint.allowed_sources
        )
        if supported and require_graph_binding and graph_constraint:
            matched_graph_ids = {
                envelope.evidence_id
                for envelope in allowed_matches
                if envelope.source_type == "graph"
            }
            if not matched_graph_ids.intersection(final_bound_graph_ids):
                supported = False
                diagnostics.append(f"claim:{constraint.claim_id}:final_binding_missing")
        answer_asserted = _any_pattern_asserted(constraint.answer_patterns, answer)
        disclosure_present = _matches_any(constraint.missing_disclosure_patterns, answer)
        forbidden_asserted = _any_pattern_asserted(
            constraint.forbidden_without_evidence_patterns, answer
        )

        prefix = f"claim:{constraint.claim_id}"
        if not constraint.evidence_patterns:
            diagnostics.append(f"{prefix}:evidence_patterns_missing")
        elif not text_matches:
            diagnostics.append(f"{prefix}:evidence_pattern_missing")
        elif not constraint.allowed_sources:
            diagnostics.append(f"{prefix}:allowed_sources_missing")
        elif not allowed_matches:
            diagnostics.append(f"{prefix}:allowed_source_missing")
        if supported and not answer_asserted:
            diagnostics.append(f"{prefix}:answer_pattern_missing")
        if not supported and (answer_asserted or forbidden_asserted):
            diagnostics.append(f"{prefix}:unsupported_completion")

        observations.append(
            _ClaimObservation(
                constraint=constraint,
                answer_asserted=answer_asserted,
                supported=supported,
                disclosure_present=disclosure_present,
                forbidden_asserted=forbidden_asserted,
            )
        )
    return observations


def _alternative_envelopes(
    alternative: ConflictAlternative,
    envelopes: Sequence[EvidenceEnvelope],
) -> tuple[EvidenceEnvelope, ...]:
    return tuple(
        envelope
        for envelope in envelopes
        if envelope.conflict_eligible
        and envelope.qualification in {"qualified", "reference_only"}
        and _matches_any(alternative.value_patterns, envelope.text)
        and _matches_any(alternative.unit_patterns, envelope.text)
        and _matches_allowed_sources(envelope, alternative.allowed_sources)
    )


def _has_distinct_source_assignment(
    alternatives: Sequence[Sequence[EvidenceEnvelope]],
) -> bool:
    candidates = [items for items in alternatives if items]
    if len(candidates) < 2:
        return False
    for left_index, left_items in enumerate(candidates):
        for right_items in candidates[left_index + 1 :]:
            if any(
                left.evidence_id != right.evidence_id
                for left in left_items
                for right in right_items
            ):
                return True
    return False


def _observe_conflicts(
    constraints: Sequence[ConflictConstraint],
    envelopes: Sequence[EvidenceEnvelope],
) -> list[_ConflictObservation]:
    observations: list[_ConflictObservation] = []
    for constraint in constraints:
        alternatives = tuple(
            _alternative_envelopes(alternative, envelopes)
            for alternative in constraint.alternatives
        )
        observations.append(
            _ConflictObservation(
                constraint=constraint,
                alternative_envelopes=alternatives,
                distinct_sources_present=_has_distinct_source_assignment(alternatives),
            )
        )
    return observations


def _conflict_answer_passes(observation: _ConflictObservation, answer: str) -> bool:
    if not observation.distinct_sources_present:
        return False
    disclosure_present = _matches_any(observation.constraint.disclosure_patterns, answer)
    alternatives_present = all(
        _matches_any(alternative.value_patterns, answer)
        and _matches_any(alternative.unit_patterns, answer)
        for alternative, envelopes in zip(
            observation.constraint.alternatives,
            observation.alternative_envelopes,
        )
        if envelopes
    )
    return disclosure_present and alternatives_present


def _first_content_line(answer: str) -> str:
    for line in (answer or "").splitlines():
        line = re.sub(r"^\s*(?:#{1,6}|>|[-*+] |\d+[.)]\s*)\s*", "", line).strip()
        if line:
            return line
    return ""


def _manual_lead_detected(answer: str) -> bool:
    first_line = _normalized(_first_content_line(answer))
    return any(first_line.startswith(_normalized(lead)) for lead in _MANUAL_LEADS)


def _repeated_line_detected(answer: str) -> bool:
    lines = [_normalized(line) for line in (answer or "").splitlines()]
    meaningful = [line for line in lines if line]
    return len(meaningful) != len(set(meaningful))


def _list_item_count(answer: str) -> int:
    marker = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)\u3001]\s*|[\u4e00-\u5341]+[\u3001.)]\s*)")
    return sum(bool(marker.match(line)) for line in (answer or "").splitlines())


def _quoted_spans(answer: str) -> list[str]:
    spans: list[str] = []
    for pattern in (r'"([^"\r\n]+)"', r"\u201c([^\u201d\r\n]+)\u201d", r"\u300c([^\u300d\r\n]+)\u300d"):
        spans.extend(match.group(1).strip() for match in re.finditer(pattern, answer or ""))
    return [span for span in spans if span]


def _quote_mode_passes(
    turn: MaintenanceEvalTurn,
    answer: str,
    envelopes: Sequence[EvidenceEnvelope],
) -> bool:
    spans = _quoted_spans(answer)
    marker_present = _matches_any(("verbatim", "original text", "\u539f\u6587"), answer)
    if not spans and not marker_present:
        return False
    qualified_texts = [
        envelope.text for envelope in envelopes if envelope.qualification == "qualified"
    ]
    expected_patterns = [
        pattern
        for constraint in turn.claim_constraints
        for pattern in constraint.evidence_patterns
    ]
    if spans:
        return any(
            any(_pattern_matched(pattern, span) for pattern in expected_patterns)
            and any(_pattern_matched(span, evidence) for evidence in qualified_texts)
            for span in spans
        ) if expected_patterns else any(
            any(_pattern_matched(span, evidence) for evidence in qualified_texts)
            for span in spans
        )
    return bool(expected_patterns) and all(
        _matches_any(expected_patterns, evidence) for evidence in qualified_texts
    )


def _page_mode_passes(answer: str, envelopes: Sequence[EvidenceEnvelope]) -> tuple[bool, bool]:
    text = unicodedata.normalize("NFKC", answer or "").casefold()
    mentioned = {
        int(value)
        for pattern in (
            r"\bpage\s*#?\s*(\d+)\b",
            r"\bp\.?\s*(\d+)\b",
            r"\u7b2c?\s*(\d+)\s*\u9875",
        )
        for value in re.findall(pattern, text)
    }
    if not mentioned:
        return False, False
    evidence_pages: set[int] = set()
    for envelope in envelopes:
        if envelope.qualification != "qualified" or envelope.source_type != "manual":
            continue
        try:
            evidence_pages.add(int(envelope.source.get("page")))
        except (TypeError, ValueError):
            continue
    return True, bool(mentioned & evidence_pages)


def _source_and_style_score(
    turn: MaintenanceEvalTurn,
    answer: str,
    envelopes: Sequence[EvidenceEnvelope],
    diagnostics: list[str],
) -> tuple[bool, bool, bool, bool]:
    fixed_template_detected = _manual_lead_detected(answer)
    repeated_line = _repeated_line_detected(answer)
    style_proxy_pass = not repeated_line
    if repeated_line:
        diagnostics.append(f"{turn.source_request_mode}_mode_repeated_line")

    expectation = turn.style_expectation
    if expectation is not None:
        if expectation.max_answer_chars is not None and len(answer) > expectation.max_answer_chars:
            style_proxy_pass = False
            diagnostics.append("style_max_answer_chars_exceeded")
        if expectation.max_list_items is not None and _list_item_count(answer) > expectation.max_list_items:
            style_proxy_pass = False
            diagnostics.append("style_max_list_items_exceeded")

    if turn.source_request_mode == "normal":
        manual_lead_allowed = bool(expectation and expectation.allow_manual_lead)
        source_mode_pass = manual_lead_allowed or not fixed_template_detected
        if not source_mode_pass:
            diagnostics.append("normal_mode_manual_lead")
    elif turn.source_request_mode == "quote":
        source_mode_pass = _quote_mode_passes(turn, answer, envelopes)
        if not source_mode_pass:
            diagnostics.append("quote_mode_quote_missing_or_unbound")
    elif turn.source_request_mode == "page":
        page_present, page_bound = _page_mode_passes(answer, envelopes)
        source_mode_pass = page_present and page_bound
        if not page_present:
            diagnostics.append("page_mode_page_missing")
        elif not page_bound:
            diagnostics.append("page_mode_page_source_mismatch")
    else:
        source_mode_pass = False
        diagnostics.append("source_request_mode_invalid")
    return (
        fixed_template_detected,
        style_proxy_pass,
        source_mode_pass,
        style_proxy_pass and source_mode_pass,
    )


def score_turn_output(
    turn: MaintenanceEvalTurn,
    answer: str,
    metadata: Mapping[str, Any] | None,
) -> TurnEvidenceScore:
    """Score one answer against its actual trace without model-based judging."""

    answer = answer or ""
    trace = extract_evidence_envelopes(metadata)
    diagnostics = list(trace.diagnostics)
    raw_used_ids = (metadata or {}).get("graph_evidence_used_ids") or []
    used_graph_ids = set(_string_list(raw_used_ids))
    raw_bindings = (metadata or {}).get("claim_evidence_bindings") or []
    if isinstance(raw_bindings, str):
        try:
            import json

            raw_bindings = json.loads(raw_bindings)
        except (TypeError, ValueError):
            raw_bindings = []
    if isinstance(raw_bindings, Mapping):
        raw_bindings = [raw_bindings]
    bound_graph_ids: set[str] = set()
    for binding in raw_bindings if isinstance(raw_bindings, Sequence) else []:
        if not isinstance(binding, Mapping) or binding.get("emitted") is False:
            continue
        bound_graph_ids.update(
            evidence_id
            for evidence_id in _string_list(binding.get("evidence_ids"))
            if evidence_id.startswith("graph:")
        )
    final_bound_graph_ids = used_graph_ids.intersection(bound_graph_ids)
    force_unsupported = turn.expected_scope == "out_of_scope"
    claims = _observe_claims(
        turn.claim_constraints,
        answer,
        trace.envelopes,
        force_unsupported=force_unsupported,
        require_graph_binding=turn.graph_dependency.strip().lower() == "required",
        final_bound_graph_ids=final_bound_graph_ids,
        diagnostics=diagnostics,
    )
    conflicts = _observe_conflicts(turn.conflict_constraints, trace.envelopes)
    has_conflict = any(observation.distinct_sources_present for observation in conflicts)

    aspect_support = [observation.supported for observation in claims]
    if not aspect_support and turn.conflict_constraints:
        aspect_support = [False]
    if not turn.claim_constraints and not turn.conflict_constraints:
        coverage_status: CoverageStatus = (
            "unsupported" if force_unsupported else "complete"
        )
    else:
        coverage_status = decide_coverage_status(
            expected_scope=turn.expected_scope,
            aspect_support=aspect_support,
            has_conflict=has_conflict,
        )

    expected_coverage_matches = bool(
        not turn.expected_coverage_status
        or turn.expected_coverage_status == coverage_status
    )
    if not expected_coverage_matches:
        diagnostics.append(
            f"coverage_status_mismatch:{turn.expected_coverage_status}:{coverage_status}"
        )

    unsupported_completion_free = all(
        observation.supported
        or not (observation.answer_asserted or observation.forbidden_asserted)
        for observation in claims
    )
    evidence_source_pass = all(
        observation.supported or not observation.answer_asserted for observation in claims
    )
    answer_evidence_alignment_pass = all(
        not observation.supported or observation.answer_asserted
        for observation in claims
    )

    supported_claims = [observation for observation in claims if observation.supported]
    unsupported_claims = [observation for observation in claims if not observation.supported]
    evidence_nugget_coverage_rate = (
        round(len(supported_claims) / len(claims), 6) if claims else 1.0
    )
    complete_answer_correct = all(
        observation.supported and observation.answer_asserted for observation in claims
    )

    partial_answer_correct = True
    if turn.expected_coverage_status == "partial" or coverage_status == "partial":
        partial_answer_correct = bool(supported_claims and unsupported_claims)
        partial_answer_correct = partial_answer_correct and all(
            observation.answer_asserted for observation in supported_claims
        )
        for observation in unsupported_claims:
            disclosure_ok = observation.disclosure_present
            if not observation.constraint.missing_disclosure_patterns:
                disclosure_ok = False
                diagnostics.append(
                    f"claim:{observation.constraint.claim_id}:missing_disclosure_patterns_missing"
                )
            elif not disclosure_ok:
                diagnostics.append(
                    f"claim:{observation.constraint.claim_id}:missing_disclosure_missing"
                )
            partial_answer_correct = bool(
                partial_answer_correct
                and disclosure_ok
                and not observation.answer_asserted
                and not observation.forbidden_asserted
            )

    conflict_handling_pass = True
    if turn.expected_coverage_status == "conflict" or coverage_status == "conflict":
        conflict_handling_pass = bool(conflicts) and all(
            _conflict_answer_passes(observation, answer) for observation in conflicts
        )
        for observation in conflicts:
            if not observation.distinct_sources_present:
                diagnostics.append(
                    f"conflict:{observation.constraint.subject}:distinct_sources_missing"
                )
            elif not _conflict_answer_passes(observation, answer):
                diagnostics.append(
                    f"conflict:{observation.constraint.subject}:answer_disclosure_incomplete"
                )

    forbidden_source_hit = any(
        _pattern_asserted(pattern, answer) for pattern in turn.forbidden_source_terms
    )
    if forbidden_source_hit:
        diagnostics.append("forbidden_source_term_hit")
    has_refusal = _contains_refusal(answer)
    requires_refusal = bool(
        turn.answerable is False
        or force_unsupported
        or turn.expected_coverage_status == "unsupported"
        or coverage_status == "unsupported"
    )
    claim_patterns = [
        pattern
        for constraint in turn.claim_constraints
        for pattern in (
            *constraint.answer_patterns,
            *constraint.forbidden_without_evidence_patterns,
        )
    ]
    refusal_followed_by_claim = _assertion_after_refusal(claim_patterns, answer)
    if force_unsupported and refusal_followed_by_claim:
        diagnostics.append("out_of_scope_refusal_followed_by_claim")
    refusal_integrity_pass = bool(
        not requires_refusal
        or (has_refusal and unsupported_completion_free and not refusal_followed_by_claim)
    )
    if requires_refusal and not has_refusal:
        diagnostics.append("required_refusal_missing")
    scope_isolation_pass = bool(
        not forbidden_source_hit
        and (
            not force_unsupported
            or (unsupported_completion_free and refusal_integrity_pass)
        )
    )

    (
        fixed_template_detected,
        style_proxy_pass,
        source_mode_pass,
        source_style_mode_pass,
    ) = _source_and_style_score(turn, answer, trace.envelopes, diagnostics)

    has_explicit_constraints = bool(
        turn.expected_scope
        or turn.expected_coverage_status
        or turn.claim_constraints
        or turn.conflict_constraints
        or turn.forbidden_source_terms
        or turn.source_request_mode != "normal"
        or turn.style_expectation is not None
    )
    if not has_explicit_constraints:
        final_pass = True
    elif coverage_status == "complete":
        final_pass = complete_answer_correct
    elif coverage_status == "partial":
        final_pass = partial_answer_correct
    elif coverage_status == "conflict":
        final_pass = conflict_handling_pass
    else:
        final_pass = refusal_integrity_pass and unsupported_completion_free
    final_pass = bool(
        final_pass
        and expected_coverage_matches
        and scope_isolation_pass
        and source_style_mode_pass
    )

    return TurnEvidenceScore(
        final_pass=final_pass,
        coverage_status=coverage_status,
        unsupported_completion_free=unsupported_completion_free,
        partial_answer_correct=partial_answer_correct,
        conflict_handling_pass=conflict_handling_pass,
        source_style_mode_pass=source_style_mode_pass,
        diagnostics=list(dict.fromkeys(diagnostics)),
        evidence_nugget_coverage_rate=evidence_nugget_coverage_rate,
        evidence_source_pass=evidence_source_pass,
        answer_evidence_alignment_pass=answer_evidence_alignment_pass,
        scope_isolation_pass=scope_isolation_pass,
        refusal_integrity_pass=refusal_integrity_pass,
        fixed_template_detected=fixed_template_detected,
        style_proxy_pass=style_proxy_pass,
        source_mode_pass=source_mode_pass,
    )
