"""Build conservative retrieval queries from query-grounded contract fields."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from services.retrieval.aspects import QuestionAspect


@dataclass(frozen=True)
class QueryVariant:
    text: str
    source: str
    target_id: str = ""


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or ""))).strip()


def _original_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _texts(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, Sequence):
        return []
    return [text for item in value if (text := _text(item))]


def _joined(*values: Any) -> str:
    return " ".join(text for value in values if (text := _text(value)))


def _grounded(query: str, value: Any) -> str:
    text = _text(value)
    return text if text and text.casefold() in _text(query).casefold() else ""


def _grounded_texts(query: str, value: Any) -> list[str]:
    return [text for item in _texts(value) if (text := _grounded(query, item))]


def build_query_variants(
    query: str,
    query_contract: Mapping[str, Any] | None,
    aspects: Sequence[QuestionAspect] = (),
    max_variants: int = 4,
) -> tuple[QueryVariant, ...]:
    """Return stable query views using only the request and its validated contract."""

    if max_variants <= 0:
        return ()
    original = _original_text(query)
    if not original:
        return ()
    contract = dict(query_contract or {})
    candidates: list[QueryVariant] = [QueryVariant(original, "original")]
    component = _grounded(
        original,
        contract.get("raw_component_span") or contract.get("component"),
    )
    fault = _grounded(original, contract.get("raw_fault_span") or contract.get("fault"))
    action = _grounded(original, contract.get("action") or contract.get("task_action"))
    requested_fields = _grounded_texts(original, contract.get("requested_fields"))

    if component and fault:
        candidates.append(QueryVariant(_joined(component, fault), "component_fault"))
    if component and (action or requested_fields):
        candidates.append(
            QueryVariant(
                _joined(component, action, *requested_fields),
                "component_action",
            )
        )

    for index, target in enumerate(contract.get("targets") or (), start=1):
        if not isinstance(target, Mapping):
            continue
        target_component = _grounded(
            original,
            target.get("raw_component_span") or target.get("component"),
        )
        if not target_component:
            continue
        target_text = _joined(
            target_component,
            _grounded(original, target.get("part_spec")),
            _grounded(original, target.get("assembly_context")),
            _grounded(original, target.get("action")),
            *_grounded_texts(original, target.get("requested_fields")),
        )
        if target_text:
            candidates.append(
                QueryVariant(
                    target_text,
                    "target",
                    _text(target.get("target_id")) or f"target-{index}",
                )
            )
    for aspect in aspects:
        aspect_text = _grounded(original, getattr(aspect, "text", ""))
        if aspect_text:
            candidates.append(
                QueryVariant(aspect_text, "aspect", _text(getattr(aspect, "aspect_id", "")))
            )

    result: list[QueryVariant] = []
    seen: set[str] = set()
    for candidate in candidates:
        canonical = candidate.text.casefold()
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        result.append(candidate)
        if len(result) >= max_variants:
            break
    return tuple(result)


def build_variant_route_pairs(
    routes: Sequence[str],
    variants: Sequence[QueryVariant],
) -> tuple[tuple[str, QueryVariant], ...]:
    return tuple((str(route), variant) for route in routes for variant in variants)


__all__ = ["QueryVariant", "build_query_variants", "build_variant_route_pairs"]
