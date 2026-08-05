"""统一反问状态机。

状态由服务端保存，客户端只允许提交选项标识或自然语言答案；候选及约束永远从服务端状态读取。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping


class ClarificationStatus(str, Enum):
    AWAITING = "awaiting"
    RESOLVED = "resolved"
    REASKED = "reasked"
    CANCELLED = "cancelled"
    EXHAUSTED = "exhausted"
    EXPIRED = "expired"


def _unique_text(values: Any) -> tuple[str, ...]:
    source = values if isinstance(values, (list, tuple, set)) else ()
    return tuple(dict.fromkeys(
        str(value).strip() for value in source if str(value).strip()
    ))


def _unique_pages(values: Any) -> tuple[int, ...]:
    source = values if isinstance(values, (list, tuple, set)) else ()
    pages: list[int] = []
    for value in source:
        try:
            page = int(value)
        except (TypeError, ValueError):
            continue
        if page > 0 and page not in pages:
            pages.append(page)
    return tuple(pages)


@dataclass(frozen=True)
class ResolvedScope:
    """Server-authoritative evidence boundary produced by clarification."""

    document_id: str
    allowed_section_ids: tuple[str, ...] = ()
    allowed_evidence_refs: tuple[str, ...] = ()
    allowed_source_chunk_uids: tuple[str, ...] = ()
    pages: tuple[int, ...] = ()
    allowed_device_ids: tuple[str, ...] = ()
    allowed_component_ids: tuple[str, ...] = ()
    allowed_fault_ids: tuple[str, ...] = ()
    allowed_path_ids: tuple[str, ...] = ()
    allowed_graph_node_ids: tuple[str, ...] = ()

    @classmethod
    def from_constraints(cls, constraints: Mapping[str, Any]) -> "ResolvedScope | None":
        document_id = str(constraints.get("document_id") or "").strip()
        if not document_id:
            return None
        section_ids = _unique_text(
            constraints.get("allowed_section_ids")
            or ([constraints.get("section_id")] if constraints.get("section_id") else ())
        )
        return cls(
            document_id=document_id,
            allowed_section_ids=section_ids,
            allowed_evidence_refs=_unique_text(constraints.get("allowed_evidence_refs")),
            allowed_source_chunk_uids=_unique_text(
                constraints.get("allowed_source_chunk_uids")
                or constraints.get("source_chunk_uids")
            ),
            pages=_unique_pages(constraints.get("pages")),
            allowed_device_ids=_unique_text(constraints.get("allowed_device_ids")),
            allowed_component_ids=_unique_text(constraints.get("allowed_component_ids")),
            allowed_fault_ids=_unique_text(constraints.get("allowed_fault_ids")),
            allowed_path_ids=_unique_text(constraints.get("allowed_path_ids")),
            allowed_graph_node_ids=_unique_text(constraints.get("allowed_graph_node_ids")),
        )

    def narrow(self, constraints: Mapping[str, Any]) -> "ResolvedScope":
        requested = self.from_constraints(constraints)
        if requested is None or requested.document_id != self.document_id:
            return self

        def intersection(current: tuple[Any, ...], proposed: tuple[Any, ...]) -> tuple[Any, ...]:
            if not proposed:
                return current
            allowed = set(current)
            return tuple(value for value in proposed if value in allowed)

        return ResolvedScope(
            document_id=self.document_id,
            allowed_section_ids=intersection(self.allowed_section_ids, requested.allowed_section_ids),
            allowed_evidence_refs=intersection(self.allowed_evidence_refs, requested.allowed_evidence_refs),
            allowed_source_chunk_uids=intersection(
                self.allowed_source_chunk_uids,
                requested.allowed_source_chunk_uids,
            ),
            pages=intersection(self.pages, requested.pages),
            allowed_device_ids=intersection(self.allowed_device_ids, requested.allowed_device_ids),
            allowed_component_ids=intersection(self.allowed_component_ids, requested.allowed_component_ids),
            allowed_fault_ids=intersection(self.allowed_fault_ids, requested.allowed_fault_ids),
            allowed_path_ids=intersection(self.allowed_path_ids, requested.allowed_path_ids),
            allowed_graph_node_ids=intersection(self.allowed_graph_node_ids, requested.allowed_graph_node_ids),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "allowed_section_ids": list(self.allowed_section_ids),
            "allowed_evidence_refs": list(self.allowed_evidence_refs),
            "allowed_source_chunk_uids": list(self.allowed_source_chunk_uids),
            "pages": list(self.pages),
            "allowed_device_ids": list(self.allowed_device_ids),
            "allowed_component_ids": list(self.allowed_component_ids),
            "allowed_fault_ids": list(self.allowed_fault_ids),
            "allowed_path_ids": list(self.allowed_path_ids),
            "allowed_graph_node_ids": list(self.allowed_graph_node_ids),
        }

    def to_retrieval_scope(self) -> dict[str, Any]:
        """Return the exact evidence boundary consumed by document retrieval."""
        return {
            "document_id": self.document_id,
            "allowed_section_ids": list(self.allowed_section_ids),
            "allowed_evidence_refs": list(self.allowed_evidence_refs),
            "allowed_source_chunk_uids": list(self.allowed_source_chunk_uids),
            "pages": list(self.pages),
        }

    def to_graph_scope(self) -> dict[str, Any]:
        """Return the exact identifiers consumed by graph candidate retrieval."""
        scope: dict[str, Any] = {
            "allowed_document_ids": [self.document_id] if self.document_id else [],
            "allowed_section_ids": list(self.allowed_section_ids),
            "allowed_source_chunk_uids": list(self.allowed_source_chunk_uids),
        }
        for name in (
            "allowed_device_ids",
            "allowed_component_ids",
            "allowed_fault_ids",
            "allowed_path_ids",
            "allowed_graph_node_ids",
        ):
            values = getattr(self, name)
            if values:
                scope[name] = list(values)
        return scope


@dataclass(frozen=True)
class ClarificationState:
    clarification_id: str
    kind: str
    topic_signature: str
    original_query: str
    candidates: tuple[dict[str, Any], ...]
    route_snapshot: Mapping[str, Any] = field(default_factory=dict)
    round_count: int = 1
    max_rounds: int = 2
    version: int = 1
    status: ClarificationStatus = ClarificationStatus.AWAITING
    selected_option_id: str = ""
    selected_constraints: Mapping[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["candidates"] = [dict(item) for item in self.candidates]
        value["route_snapshot"] = dict(self.route_snapshot)
        value["selected_constraints"] = dict(self.selected_constraints)
        value["status"] = self.status.value
        return value


class ClarificationStateStore:
    """Redis-compatible store with an in-process fallback for local/test deployments."""

    def __init__(self, *, redis_client: Any = None, ttl_seconds: int = 900) -> None:
        self.redis_client = redis_client
        self.ttl_seconds = max(int(ttl_seconds), 1)
        self._cache: dict[str, tuple[float, ClarificationState]] = {}

    @staticmethod
    def _key(session_id: str) -> str:
        digest = hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()
        return f"fixagent:clarification_state:{digest}"

    def create(
        self,
        session_id: str,
        payload: Mapping[str, Any],
        *,
        route_snapshot: Mapping[str, Any] | None = None,
        max_rounds: int = 2,
    ) -> ClarificationState:
        now = time.time()
        candidates = tuple(
            dict(item) for item in payload.get("candidates") or payload.get("alternatives") or ()
            if isinstance(item, Mapping)
        )
        identity = {
            "kind": str(payload.get("kind") or "slot_disambiguation"),
            "topic_signature": str(payload.get("topic_signature") or ""),
            "query": str(payload.get("original_query") or ""),
            "candidate_ids": [str(item.get("id") or item.get("candidate_id") or "") for item in candidates],
        }
        state = ClarificationState(
            clarification_id=f"clarification-{hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]}",
            kind=identity["kind"],
            topic_signature=identity["topic_signature"],
            original_query=identity["query"],
            candidates=candidates,
            route_snapshot=dict(route_snapshot or payload.get("route_snapshot") or {}),
            max_rounds=max(1, int(max_rounds)),
            created_at=now,
            updated_at=now,
        )
        self._save(session_id, state)
        return state

    def load(self, session_id: str) -> ClarificationState | None:
        key = self._key(session_id)
        if self.redis_client is not None:
            try:
                raw = self.redis_client.get(key)
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                if raw:
                    return self._from_dict(json.loads(raw))
            except Exception:
                pass
        cached = self._cache.get(key)
        if not cached:
            return None
        expires_at, state = cached
        if expires_at <= time.time():
            expired = ClarificationState(**{**state.__dict__, "status": ClarificationStatus.EXPIRED, "version": state.version + 1})
            self._cache[key] = (time.time() + self.ttl_seconds, expired)
            return expired
        return state

    def resolve(self, session_id: str, *, answer: str, expected_version: int | None = None) -> ClarificationState | None:
        state = self.load(session_id)
        if state is None or state.status not in {ClarificationStatus.AWAITING, ClarificationStatus.REASKED}:
            return state if state and state.status is ClarificationStatus.RESOLVED else None
        if expected_version is not None and int(expected_version) != state.version:
            return None
        answer_key = str(answer or "").strip().casefold()
        selected = next((item for item in state.candidates if str(item.get("id") or item.get("candidate_id") or "").strip().casefold() == answer_key), None)
        if selected is None:
            selected = next((item for item in state.candidates if answer_key and answer_key in str(item.get("label") or item.get("value") or "").casefold()), None)
        if selected is None:
            ordinal = re.fullmatch(r"(?:第\s*)?(\d+)(?:\s*(?:项|个|号))?", answer_key)
            if ordinal:
                index = int(ordinal.group(1)) - 1
                if 0 <= index < len(state.candidates):
                    selected = state.candidates[index]
        if selected is None:
            return None
        option_id = str(selected.get("id") or selected.get("candidate_id") or "")
        constraints = selected.get("constraints") if isinstance(selected.get("constraints"), Mapping) else {}
        resolved = ClarificationState(**{**state.__dict__, "status": ClarificationStatus.RESOLVED, "version": state.version + 1, "updated_at": time.time(), "selected_option_id": option_id, "selected_constraints": dict(constraints)})
        self._save(session_id, resolved)
        return resolved

    def reask(self, session_id: str, *, expected_version: int | None = None) -> ClarificationState | None:
        state = self.load(session_id)
        if state is None or state.status not in {ClarificationStatus.AWAITING, ClarificationStatus.REASKED}:
            return state
        if expected_version is not None and int(expected_version) != state.version:
            return None
        status = ClarificationStatus.REASKED if state.round_count < state.max_rounds else ClarificationStatus.EXHAUSTED
        updated = ClarificationState(**{**state.__dict__, "status": status, "round_count": min(state.round_count + 1, state.max_rounds), "version": state.version + 1, "updated_at": time.time()})
        self._save(session_id, updated)
        return updated

    def cancel_for_topic(self, session_id: str, topic_signature: str) -> ClarificationState | None:
        state = self.load(session_id)
        if state is None or state.status not in {ClarificationStatus.AWAITING, ClarificationStatus.REASKED}:
            return state
        if str(topic_signature or "") == state.topic_signature:
            return state
        cancelled = ClarificationState(**{**state.__dict__, "status": ClarificationStatus.CANCELLED, "version": state.version + 1, "updated_at": time.time()})
        self._save(session_id, cancelled)
        return cancelled

    def _save(self, session_id: str, state: ClarificationState) -> None:
        key = self._key(session_id)
        self._cache[key] = (time.time() + self.ttl_seconds, state)
        if self.redis_client is not None:
            try:
                self.redis_client.setex(key, self.ttl_seconds, json.dumps(state.to_dict(), ensure_ascii=False))
            except Exception:
                pass

    @staticmethod
    def _from_dict(payload: Mapping[str, Any]) -> ClarificationState:
        data = dict(payload)
        data["candidates"] = tuple(dict(item) for item in data.get("candidates") or () if isinstance(item, Mapping))
        data["route_snapshot"] = dict(data.get("route_snapshot") or {})
        data["selected_constraints"] = dict(data.get("selected_constraints") or {})
        data["status"] = ClarificationStatus(str(data.get("status") or ClarificationStatus.AWAITING.value))
        return ClarificationState(**data)


def topic_signature_for_contract(contract: Any) -> str:
    """从结构化语义契约生成话题签名，不依赖业务关键词。"""
    data = contract.to_dict() if hasattr(contract, "to_dict") else dict(contract or {})
    targets = data.get("targets") if isinstance(data.get("targets"), (list, tuple)) else ()
    anchor = {
        "device": [
            str(data.get(key) or "").strip().casefold()
            for key in ("device_name", "device_category", "carrier_or_application", "manufacturer", "model")
            if str(data.get(key) or "").strip()
        ],
        "components": [
            str(value).strip().casefold()
            for value in (
                [data.get("component")]
                + [item.get("component") for item in targets if isinstance(item, Mapping)]
            )
            if str(value or "").strip()
        ],
        "symptoms": [str(value).strip().casefold() for value in data.get("symptoms") or () if str(value).strip()],
    }
    if not any(anchor.values()):
        return ""
    canonical = json.dumps(anchor, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"topic-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:16]}"


__all__ = ["ClarificationState", "ClarificationStateStore", "ClarificationStatus", "ResolvedScope", "topic_signature_for_contract"]
