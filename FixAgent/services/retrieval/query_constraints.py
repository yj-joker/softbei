"""Query entity constraints that must not be reversed by semantic retrieval."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class QueryConstraints:
    required_terms: tuple[str, ...] = ()
    forbidden_terms: tuple[str, ...] = ()
    action: str = ""
    forbidden_actions: tuple[str, ...] = ()


_DIRECTION_MARKER_PAIRS = (
    ("右", "左"),
    ("左", "右"),
)

_ACTION_PAIRS = (
    (("安装", "装上", "装入", "装配"), ("拆卸", "拆下", "取下", "取出")),
    (("拆卸", "拆下", "取下", "取出"), ("安装", "装上", "装入", "装配")),
    (("连接", "接通"), ("断开", "断电")),
    (("断开", "断电"), ("连接", "接通")),
)


def extract_query_constraints(query: str) -> QueryConstraints:
    compact = _compact(query)
    required: list[str] = []
    forbidden: list[str] = []
    for expected, opposite in _DIRECTION_MARKER_PAIRS:
        expected_present = _compact(expected) in compact
        opposite_present = _compact(opposite) in compact
        if expected_present and opposite_present:
            break
        if expected_present:
            required.append(expected)
            forbidden.append(opposite)
            break
    action = ""
    forbidden_actions: tuple[str, ...] = ()
    for expected_terms, opposite_terms in _ACTION_PAIRS:
        expected_present = any(_compact(term) in compact for term in expected_terms)
        opposite_present = any(_compact(term) in compact for term in opposite_terms)
        if expected_present and opposite_present:
            break
        if expected_present:
            action = expected_terms[0]
            forbidden_actions = tuple(opposite_terms)
            break
    return QueryConstraints(tuple(required), tuple(forbidden), action, forbidden_actions)


def candidate_constraint_conflicts(
    constraints: QueryConstraints,
    item: Mapping[str, Any],
) -> list[str]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
    title = _compact(" ".join(str(metadata.get(key) or "") for key in ("section_title", "chunk_label")))
    body = _compact(" ".join(str(item.get(key) or "") for key in ("content", "text")))
    searchable = f"{title}{body}"
    conflicts: list[str] = []
    for expected, opposite in zip(constraints.required_terms, constraints.forbidden_terms):
        expected_text = _compact(expected)
        opposite_text = _compact(opposite)
        if opposite_text in searchable and expected_text not in searchable:
            conflicts.append(f"direction:{expected}->{opposite}")
    if constraints.action:
        desired_action = _compact(constraints.action)
        action_text = searchable
        if desired_action not in action_text:
            for opposite in constraints.forbidden_actions:
                if _compact(opposite) in action_text:
                    conflicts.append(f"action:{constraints.action}->{opposite}")
                    break
    return conflicts


def _compact(value: Any) -> str:
    return re.sub(r"[^0-9a-z一-鿿]+", "", str(value or "").casefold())
