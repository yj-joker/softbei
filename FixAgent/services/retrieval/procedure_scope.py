"""Structured procedure-subflow identity shared by import and retrieval."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping


_ACTION_ALIASES = {
    "安装": ("安装", "装配"),
    "拆卸": ("拆卸", "拆除"),
    "检查": ("检查", "检验", "测量"),
    "调整": ("调整", "调节", "校正"),
    "更换": ("更换", "替换"),
}


def _compact(value: Any) -> str:
    return re.sub(r"[\s：:；;，,。．、（）()【】\[\]]+", "", str(value or "")).casefold()


def _strip_heading_number(value: str) -> str:
    return re.sub(r"^\s*(?:第)?\d+(?:\.\d+)*(?:节)?\s*", "", str(value or "")).strip()


def _canonical_action_prefix(heading: str) -> tuple[str, str]:
    compact_heading = _compact(_strip_heading_number(heading))
    for canonical, aliases in _ACTION_ALIASES.items():
        for alias in sorted(aliases, key=len, reverse=True):
            compact_alias = _compact(alias)
            if compact_heading.startswith(compact_alias):
                return canonical, compact_heading[len(compact_alias):]
    return "", ""


@dataclass(frozen=True)
class ProcedureScope:
    scope_id: str
    heading: str
    action: str
    target: str

    def to_metadata(self) -> dict[str, str]:
        return {
            "procedure_scope_id": self.scope_id,
            "procedure_heading": self.heading,
            "procedure_action": self.action,
            "procedure_target": self.target,
        }


def procedure_scope_from_heading(heading: str) -> ProcedureScope | None:
    clean_heading = _strip_heading_number(heading)
    if "\n" in clean_heading or re.search(r"[，,。；;：:！？!?]", clean_heading):
        return None
    action, target = _canonical_action_prefix(clean_heading)
    if not action or not target:
        return None
    digest = hashlib.sha1(f"{action}|{target}".encode("utf-8")).hexdigest()[:12]
    return ProcedureScope(
        scope_id=f"proc:{digest}",
        heading=clean_heading,
        action=action,
        target=target,
    )


def procedure_scope_from_toc_path(toc_path: str) -> ProcedureScope | None:
    parts = [part.strip() for part in re.split(r"\s*[>＞]\s*", str(toc_path or "")) if part.strip()]
    for part in reversed(parts):
        scope = procedure_scope_from_heading(part)
        if scope is not None:
            return scope
    return None


def procedure_scope_from_metadata(metadata: Mapping[str, Any] | None) -> ProcedureScope | None:
    values = metadata or {}
    scope_id = str(values.get("procedure_scope_id") or "").strip()
    heading = str(values.get("procedure_heading") or "").strip()
    action = str(values.get("procedure_action") or "").strip()
    target = _compact(values.get("procedure_target"))
    if heading and action and target:
        if not scope_id:
            digest = hashlib.sha1(f"{action}|{target}".encode("utf-8")).hexdigest()[:12]
            scope_id = f"proc:{digest}"
        return ProcedureScope(scope_id=scope_id, heading=heading, action=action, target=target)
    return procedure_scope_from_toc_path(str(values.get("toc_path") or ""))


def normalize_procedure_target(value: Any) -> str:
    return _compact(value)


def _is_subsequence(shorter: str, longer: str) -> bool:
    if not shorter or not longer:
        return False
    iterator = iter(longer)
    return all(any(char == candidate for candidate in iterator) for char in shorter)


def procedure_target_similarity(query_target: str, candidate_target: str) -> int:
    query = normalize_procedure_target(query_target)
    candidate = normalize_procedure_target(candidate_target)
    if not query or not candidate:
        return 0
    if query == candidate:
        return 1000
    if query in candidate or candidate in query:
        return 850 + min(len(query), len(candidate)) * 10
    shorter, longer = sorted((query, candidate), key=len)
    if len(shorter) >= 2 and _is_subsequence(shorter, longer):
        return 450 + int(250 * len(shorter) / len(longer))
    return 0
