"""Short-lived evidence boundaries for deictic image follow-up turns."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from threading import RLock


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GroundedTurnContext:
    base_query: str
    document_id: str
    resolved_query: str = ""
    device_type: str = ""
    section_id: str = ""
    section_title: str = ""
    evidence_pages: tuple[int, ...] = ()
    source_chunk_ids: tuple[str, ...] = ()
    procedure_scope_ids: tuple[str, ...] = ()
    topic_signature: str = ""
    created_at_ms: int = 0

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["evidence_pages"] = list(self.evidence_pages)
        value["source_chunk_ids"] = list(self.source_chunk_ids)
        value["procedure_scope_ids"] = list(self.procedure_scope_ids)
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "GroundedTurnContext":
        return cls(
            base_query=str(value.get("base_query") or value.get("query") or "").strip(),
            document_id=str(value.get("document_id") or "").strip(),
            resolved_query=str(
                value.get("resolved_query")
                or value.get("base_query")
                or value.get("query")
                or ""
            ).strip(),
            device_type=str(value.get("device_type") or "").strip(),
            section_id=str(value.get("section_id") or "").strip(),
            section_title=str(value.get("section_title") or "").strip(),
            evidence_pages=tuple(
                int(page)
                for page in value.get("evidence_pages") or ()
                if str(page).isdigit()
            ),
            source_chunk_ids=tuple(
                str(item).strip()
                for item in value.get("source_chunk_ids") or ()
                if str(item).strip()
            ),
            procedure_scope_ids=tuple(
                str(item).strip()
                for item in value.get("procedure_scope_ids") or ()
                if str(item).strip()
            ),
            topic_signature=str(value.get("topic_signature") or "").strip(),
            created_at_ms=int(value.get("created_at_ms") or 0),
        )


class GroundedTurnContextStore:
    _PREFIX = "fixagent:grounded-turn:"

    def __init__(
        self,
        redis_client=None,
        *,
        ttl_seconds: int = 900,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._redis = redis_client
        self._ttl_seconds = ttl_seconds
        self._now = now
        self._memory: dict[str, tuple[float, str]] = {}
        self._lock = RLock()

    def _key(self, session_id: str) -> str:
        return f"{self._PREFIX}{session_id.strip()}"

    def remember(self, session_id: str, context: GroundedTurnContext) -> None:
        if not str(session_id or "").strip():
            return
        payload = json.dumps(
            context.to_dict(), ensure_ascii=False, separators=(",", ":")
        )
        key = self._key(session_id)
        expires_at = self._now() + self._ttl_seconds
        with self._lock:
            self._memory[key] = (expires_at, payload)
        if self._redis is not None:
            try:
                self._redis.setex(key, self._ttl_seconds, payload)
            except Exception:
                logger.warning(
                    "[grounded_turn] redis remember failed",
                    exc_info=True,
                )

    def load(self, session_id: str) -> GroundedTurnContext | None:
        if not str(session_id or "").strip():
            return None
        key = self._key(session_id)
        payload = None
        if self._redis is not None:
            try:
                payload = self._redis.get(key)
            except Exception:
                logger.warning(
                    "[grounded_turn] redis load failed",
                    exc_info=True,
                )
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        if not payload:
            with self._lock:
                cached = self._memory.get(key)
                if cached and cached[0] > self._now():
                    payload = cached[1]
                elif cached:
                    self._memory.pop(key, None)
        if not payload:
            return None
        try:
            context = GroundedTurnContext.from_dict(json.loads(payload))
        except (TypeError, ValueError, json.JSONDecodeError):
            self.clear(session_id)
            return None
        if not context.base_query or not context.document_id:
            self.clear(session_id)
            return None
        return context

    def clear(self, session_id: str) -> None:
        if not str(session_id or "").strip():
            return
        key = self._key(session_id)
        with self._lock:
            self._memory.pop(key, None)
        if self._redis is not None:
            try:
                self._redis.delete(key)
            except Exception:
                logger.warning(
                    "[grounded_turn] redis clear failed",
                    exc_info=True,
                )


def context_from_successful_answer(
    query: str,
    metadata: Mapping[str, object],
    *,
    device_type: str = "",
) -> GroundedTurnContext | None:
    route_plan = metadata.get("route_plan")
    route_action = (
        str(route_plan.get("action") or "")
        if isinstance(route_plan, Mapping)
        else ""
    )
    response_audit = metadata.get("response_audit")
    response_audit = response_audit if isinstance(response_audit, Mapping) else {}
    coverage_status = str(metadata.get("coverage_status") or "")
    if (
        route_action != "grounded_retrieval"
        or response_audit.get("passed") is not True
        or coverage_status not in {"complete", "partial"}
        or metadata.get("blocked_for_insufficient_evidence") is True
        or metadata.get("evidence_status") == "no_evidence"
        or metadata.get("execution_mode")
        in {
            "maintenance_ai_fallback_after_retrieval",
            "generic_guidance",
            "causal_follow_up_question",
        }
    ):
        return None

    selection_contract = metadata.get("image_selection_contract")
    selection_contract = (
        selection_contract if isinstance(selection_contract, Mapping) else {}
    )
    document_ids = {
        str(value).strip()
        for value in selection_contract.get("target_document_ids") or ()
        if str(value).strip()
    }
    if len(document_ids) != 1:
        return None

    source_ids = tuple(
        dict.fromkeys(
            str(value).strip()
            for value in selection_contract.get("target_non_image_source_ids") or ()
            if str(value).strip()
        )
    )
    scope_ids = tuple(
        dict.fromkeys(
            str(value).strip()
            for value in selection_contract.get("target_procedure_scope_ids") or ()
            if str(value or "").strip()
        )
    )
    pages = tuple(
        dict.fromkeys(
            int(page)
            for page in selection_contract.get("target_pages") or ()
            if str(page).isdigit()
        )
    )
    if not source_ids and not scope_ids:
        return None

    section_ids = [
        str(value).strip()
        for value in metadata.get("_deterministic_answer_section_ids") or ()
        if str(value).strip()
    ]
    if not section_ids and isinstance(route_plan, Mapping):
        selected_section_id = str(route_plan.get("selected_section_id") or "").strip()
        if selected_section_id:
            section_ids = [selected_section_id]
    return GroundedTurnContext(
        base_query=str(metadata.get("image_followup_base_query") or query or "").strip(),
        document_id=next(iter(document_ids)),
        resolved_query=str(metadata.get("resolved_image_query") or query or "").strip(),
        device_type=str(device_type or metadata.get("device_type") or "").strip(),
        section_id=section_ids[0] if len(section_ids) == 1 else "",
        section_title=str(
            metadata.get("_deterministic_answer_section_title") or ""
        ).strip(),
        evidence_pages=pages,
        source_chunk_ids=source_ids,
        procedure_scope_ids=scope_ids,
        topic_signature=str(metadata.get("topic_signature") or "").strip(),
        created_at_ms=int(time.time() * 1000),
    )
