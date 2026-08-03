"""Deterministic device and document scope decisions for knowledge answers."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from services.retrieval.device_identity import (
    MATCHED,
    UNCERTAIN,
    UNMATCHED,
    DeviceCatalog,
    DocumentIdentity,
    QueryContract,
    compare_query_to_document,
    query_is_unqualified_document_head,
    query_mentions_unresolved_identity,
)


IN_SCOPE = "in_scope"
OUT_OF_SCOPE = "out_of_scope"
UNKNOWN_SCOPE = "unknown"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _compact(value: Any) -> str:
    return re.sub(r"[\s_\-]+", "", _clean(value).lower())


@dataclass(frozen=True)
class DeviceProfile:
    device_type: str
    display_name: str
    aliases: tuple[str, ...]
    supported: bool


@dataclass(frozen=True)
class DocumentProfile:
    document_id: str
    device_type: str
    display_name: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class ScopeDecision:
    status: str
    source: str
    reason: str
    document_id: str = ""
    device_type: str = ""
    display_name: str = ""
    detected_device_type: str = ""
    requested_document_id: str = ""
    requested_device_type: str = ""
    identity_relation: str = ""
    identity_conflicts: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def retrieval_filter(self) -> dict[str, str]:
        if self.status != IN_SCOPE:
            return {}
        return {
            "document_id": self.document_id,
            "device_type": "" if self.document_id else self.device_type,
        }


class ScopeRegistry:
    def __init__(
        self,
        documents: list[DocumentProfile],
        devices: list[DeviceProfile],
    ) -> None:
        self.documents = {profile.document_id: profile for profile in documents}
        self.devices = {profile.device_type: profile for profile in devices}
        self.documents_by_device: dict[str, list[DocumentProfile]] = {}
        for profile in documents:
            self.documents_by_device.setdefault(profile.device_type, []).append(profile)
        aliases: list[tuple[str, str]] = []
        for profile in devices:
            values = (profile.device_type, profile.display_name, *profile.aliases)
            aliases.extend((_compact(alias), profile.device_type) for alias in values if _compact(alias))
        self.aliases = sorted(set(aliases), key=lambda pair: len(pair[0]), reverse=True)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ScopeRegistry":
        documents: list[DocumentProfile] = []
        devices_by_type: dict[str, DeviceProfile] = {}
        for raw in payload.get("documents") or []:
            document_id = _clean(raw.get("document_id"))
            device_type = _clean(raw.get("device_type"))
            if not document_id or not device_type:
                continue
            aliases = tuple(_clean(value) for value in raw.get("aliases") or [] if _clean(value))
            display_name = _clean(raw.get("display_name")) or device_type
            documents.append(DocumentProfile(document_id, device_type, display_name, aliases))
            devices_by_type[device_type] = DeviceProfile(device_type, display_name, aliases, True)
        for raw in payload.get("external_devices") or []:
            device_type = _clean(raw.get("device_type"))
            if not device_type or device_type in devices_by_type:
                continue
            aliases = tuple(_clean(value) for value in raw.get("aliases") or [] if _clean(value))
            display_name = _clean(raw.get("display_name")) or device_type
            devices_by_type[device_type] = DeviceProfile(device_type, display_name, aliases, False)
        return cls(documents, list(devices_by_type.values()))

    def document(self, document_id: Any) -> DocumentProfile | None:
        return self.documents.get(_clean(document_id))

    def resolve_device(self, value: Any) -> DeviceProfile | None:
        compact = _compact(value)
        if not compact:
            return None
        for alias, device_type in self.aliases:
            if compact == alias:
                return self.devices[device_type]
        return None

    def detect_device(self, query: str) -> DeviceProfile | None:
        compact_query = _compact(query)
        if not compact_query:
            return None
        for alias, device_type in self.aliases:
            if alias in compact_query:
                return self.devices[device_type]
        return None

    def first_document(self, device_type: str) -> DocumentProfile | None:
        profiles = self.documents_by_device.get(device_type) or []
        return profiles[0] if profiles else None


def _decision(
    status: str,
    source: str,
    reason: str,
    *,
    profile: DocumentProfile | None = None,
    detected: DeviceProfile | None = None,
    request_document_id: Any = None,
    request_device_type: Any = None,
) -> ScopeDecision:
    return ScopeDecision(
        status=status,
        source=source,
        reason=reason,
        document_id=profile.document_id if profile else _clean(request_document_id),
        device_type=profile.device_type if profile else "",
        display_name=profile.display_name if profile else (detected.display_name if detected else ""),
        detected_device_type=detected.device_type if detected else "",
        requested_document_id=_clean(request_document_id),
        requested_device_type=_clean(request_device_type),
    )


def decide_scope(
    query: str,
    *,
    request_document_id: Any = None,
    request_device_type: Any = None,
    session_document_id: Any = None,
    session_device_type: Any = None,
    registry: ScopeRegistry | None = None,
    query_contract: QueryContract | Mapping[str, Any] | None = None,
    catalog: DeviceCatalog | None = None,
) -> ScopeDecision:
    if catalog is not None:
        contract = (
            query_contract
            if isinstance(query_contract, QueryContract)
            else QueryContract.from_mapping(query_contract, raw_query=query)
        )
        return _decide_dynamic_scope(
            contract,
            catalog=catalog,
            request_document_id=request_document_id,
            request_device_type=request_device_type,
            session_document_id=session_document_id,
            session_device_type=session_device_type,
        )

    registry = registry or get_scope_registry()
    detected = registry.detect_device(query)

    if _clean(request_document_id):
        profile = registry.document(request_document_id)
        if profile is None:
            return _decision(
                OUT_OF_SCOPE,
                "request_document",
                "unknown_document",
                detected=detected,
                request_document_id=request_document_id,
                request_device_type=request_device_type,
            )
        requested_device = registry.resolve_device(request_device_type)
        if _clean(request_device_type) and (
            requested_device is None or requested_device.device_type != profile.device_type
        ):
            return _decision(
                OUT_OF_SCOPE,
                "request_document",
                "device_document_conflict",
                profile=profile,
                detected=detected or requested_device,
                request_document_id=request_document_id,
                request_device_type=request_device_type,
            )
        if detected and detected.device_type != profile.device_type:
            return _decision(
                OUT_OF_SCOPE,
                "request_document",
                "device_document_conflict",
                profile=profile,
                detected=detected,
                request_document_id=request_document_id,
                request_device_type=request_device_type,
            )
        return _decision(
            IN_SCOPE,
            "request_document",
            "document_confirmed",
            profile=profile,
            detected=detected,
            request_document_id=request_document_id,
            request_device_type=request_device_type,
        )

    if _clean(request_device_type):
        requested_device = registry.resolve_device(request_device_type)
        profile = registry.first_document(requested_device.device_type) if requested_device else None
        if requested_device is None or not requested_device.supported or profile is None:
            return _decision(
                OUT_OF_SCOPE,
                "request_device",
                "unsupported_device",
                detected=detected or requested_device,
                request_device_type=request_device_type,
            )
        if detected and detected.device_type != requested_device.device_type:
            return _decision(
                OUT_OF_SCOPE,
                "request_device",
                "explicit_device_conflict",
                profile=profile,
                detected=detected,
                request_device_type=request_device_type,
            )
        return _decision(
            IN_SCOPE,
            "request_device",
            "device_confirmed",
            profile=profile,
            detected=detected,
            request_device_type=request_device_type,
        )

    if _clean(session_document_id):
        profile = registry.document(session_document_id)
        if profile:
            if detected and detected.device_type != profile.device_type:
                return _decision(
                    OUT_OF_SCOPE,
                    "session_document",
                    "explicit_device_switch",
                    profile=profile,
                    detected=detected,
                )
            return _decision(IN_SCOPE, "session_document", "session_confirmed", profile=profile, detected=detected)

    if _clean(session_device_type):
        session_device = registry.resolve_device(session_device_type)
        profile = registry.first_document(session_device.device_type) if session_device else None
        if session_device and session_device.supported and profile:
            if detected and detected.device_type != session_device.device_type:
                return _decision(
                    OUT_OF_SCOPE,
                    "session_device",
                    "explicit_device_switch",
                    profile=profile,
                    detected=detected,
                )
            return _decision(IN_SCOPE, "session_device", "session_confirmed", profile=profile, detected=detected)

    if detected:
        profile = registry.first_document(detected.device_type)
        if detected.supported and profile:
            return _decision(IN_SCOPE, "audited_alias", "alias_confirmed", profile=profile, detected=detected)
        return _decision(OUT_OF_SCOPE, "audited_alias", "unsupported_device", detected=detected)

    return _decision(UNKNOWN_SCOPE, "unknown", "no_confirmed_scope")


def _decide_dynamic_scope(
    query: QueryContract,
    *,
    catalog: DeviceCatalog,
    request_document_id: Any = None,
    request_device_type: Any = None,
    session_document_id: Any = None,
    session_device_type: Any = None,
) -> ScopeDecision:
    requested_document_id = _clean(request_document_id)
    requested_device_type = _clean(request_device_type)

    if requested_document_id:
        document = catalog.document(requested_document_id)
        if document is None:
            return _dynamic_decision(
                OUT_OF_SCOPE,
                "request_document",
                "unknown_document",
                query=query,
                request_document_id=requested_document_id,
                request_device_type=requested_device_type,
            )
        # An explicit request device and an explicit document form a binding
        # contract independent of the intent model.  Reject their mismatch
        # before consulting the query-extraction fallback; otherwise a router
        # miss can downgrade a known mismatch to ``unknown`` and accidentally
        # leave the selected manual available for retrieval.
        if requested_device_type and not _request_device_matches(requested_device_type, document):
            return _dynamic_decision(
                OUT_OF_SCOPE,
                "request_document",
                "device_document_conflict",
                query=query,
                document=document,
                relation=UNMATCHED,
                request_document_id=requested_document_id,
                request_device_type=requested_device_type,
            )
        if query_mentions_unresolved_identity(query, document):
            return _dynamic_decision(
                UNKNOWN_SCOPE,
                "request_document",
                "unresolved_device_identity",
                query=query,
                document=document,
                relation=UNCERTAIN,
                request_document_id=requested_document_id,
                request_device_type=requested_device_type,
            )
        comparison = compare_query_to_document(query, document)
        if query.has_explicit_device and comparison.relation == UNMATCHED:
            return _dynamic_decision(
                OUT_OF_SCOPE,
                "request_document",
                "device_document_conflict",
                query=query,
                document=document,
                comparison=comparison,
                request_document_id=requested_document_id,
                request_device_type=requested_device_type,
            )
        if (
            query.has_explicit_device
            and comparison.relation == UNCERTAIN
            and not query_is_unqualified_document_head(query, document)
        ):
            return _dynamic_decision(
                UNKNOWN_SCOPE,
                "request_document",
                comparison.reason or "identity_not_distinguishing",
                query=query,
                document=document,
                comparison=comparison,
                request_document_id=requested_document_id,
                request_device_type=requested_device_type,
            )
        return _dynamic_decision(
            IN_SCOPE,
            "request_document",
            "document_confirmed",
            query=query,
            document=document,
            comparison=comparison,
            request_document_id=requested_document_id,
            request_device_type=requested_device_type,
        )

    if requested_device_type:
        candidates = [
            document
            for document in catalog.documents
            if _request_device_matches(requested_device_type, document)
        ]
        if len(candidates) != 1:
            return _dynamic_decision(
                OUT_OF_SCOPE if not candidates else UNKNOWN_SCOPE,
                "request_device",
                "unsupported_device" if not candidates else "ambiguous_device",
                query=query,
                request_device_type=requested_device_type,
            )
        document = candidates[0]
        if query_mentions_unresolved_identity(query, document):
            return _dynamic_decision(
                UNKNOWN_SCOPE,
                "request_device",
                "unresolved_device_identity",
                query=query,
                document=document,
                relation=UNCERTAIN,
                request_device_type=requested_device_type,
            )
        comparison = compare_query_to_document(query, document)
        if query.has_explicit_device and comparison.relation == UNMATCHED:
            return _dynamic_decision(
                OUT_OF_SCOPE,
                "request_device",
                "explicit_device_conflict",
                query=query,
                document=document,
                comparison=comparison,
                request_device_type=requested_device_type,
            )
        if (
            query.has_explicit_device
            and comparison.relation == UNCERTAIN
            and not query_is_unqualified_document_head(query, document)
        ):
            return _dynamic_decision(
                UNKNOWN_SCOPE,
                "request_device",
                comparison.reason or "identity_not_distinguishing",
                query=query,
                document=document,
                comparison=comparison,
                request_device_type=requested_device_type,
            )
        return _dynamic_decision(
            IN_SCOPE,
            "request_device",
            "device_confirmed",
            query=query,
            document=document,
            comparison=comparison,
            request_device_type=requested_device_type,
        )

    session_document = catalog.document(session_document_id) if _clean(session_document_id) else None
    if session_document is not None:
        if query_mentions_unresolved_identity(query, session_document):
            return _dynamic_decision(
                UNKNOWN_SCOPE,
                "session_document",
                "unresolved_device_identity",
                query=query,
                document=session_document,
                relation=UNCERTAIN,
            )
        comparison = compare_query_to_document(query, session_document)
        if query.has_explicit_device and comparison.relation == UNMATCHED:
            return _dynamic_decision(
                OUT_OF_SCOPE,
                "session_document",
                "explicit_device_switch",
                query=query,
                document=session_document,
                comparison=comparison,
            )
        if (
            query.has_explicit_device
            and comparison.relation == UNCERTAIN
            and not query_is_unqualified_document_head(query, session_document)
        ):
            return _dynamic_decision(
                UNKNOWN_SCOPE,
                "session_document",
                comparison.reason or "identity_not_distinguishing",
                query=query,
                document=session_document,
                comparison=comparison,
            )
        return _dynamic_decision(
            IN_SCOPE,
            "session_document",
            "session_confirmed",
            query=query,
            document=session_document,
            comparison=comparison,
        )

    if _clean(session_device_type):
        session_matches = [
            document
            for document in catalog.documents
            if _request_device_matches(session_device_type, document)
        ]
        if len(session_matches) == 1:
            document = session_matches[0]
            if query_mentions_unresolved_identity(query, document):
                return _dynamic_decision(
                    UNKNOWN_SCOPE,
                    "session_device",
                    "unresolved_device_identity",
                    query=query,
                    document=document,
                    relation=UNCERTAIN,
                )
            comparison = compare_query_to_document(query, document)
            if query.has_explicit_device and comparison.relation == UNMATCHED:
                return _dynamic_decision(
                    OUT_OF_SCOPE,
                    "session_device",
                    "explicit_device_switch",
                    query=query,
                    document=document,
                    comparison=comparison,
                )
            if (
                query.has_explicit_device
                and comparison.relation == UNCERTAIN
                and not query_is_unqualified_document_head(query, document)
            ):
                return _dynamic_decision(
                    UNKNOWN_SCOPE,
                    "session_device",
                    comparison.reason or "identity_not_distinguishing",
                    query=query,
                    document=document,
                    comparison=comparison,
                )
            return _dynamic_decision(
                IN_SCOPE,
                "session_device",
                "session_confirmed",
                query=query,
                document=document,
                comparison=comparison,
            )

    comparisons = catalog.match(query)
    matched = [item for item in comparisons if item.relation == MATCHED]
    if len(matched) == 1:
        return _dynamic_decision(
            IN_SCOPE,
            "query_identity",
            "identity_confirmed",
            query=query,
            document=matched[0].document,
            comparison=matched[0],
        )
    if len(matched) > 1:
        return _dynamic_decision(
            UNKNOWN_SCOPE,
            "query_identity",
            "ambiguous_device",
            query=query,
            relation=UNCERTAIN,
        )
    if query.has_explicit_device and comparisons and all(
        item.relation == UNMATCHED for item in comparisons
    ):
        conflicts = tuple(dict.fromkeys(
            conflict for item in comparisons for conflict in item.conflicts
        ))
        return _dynamic_decision(
            OUT_OF_SCOPE,
            "query_identity",
            "identity_attribute_conflict",
            query=query,
            relation=UNMATCHED,
            conflicts=conflicts,
        )
    reason = next(
        (item.reason for item in comparisons if item.relation == UNCERTAIN),
        "no_confirmed_scope",
    )
    return _dynamic_decision(
        UNKNOWN_SCOPE,
        "query_identity" if query.has_explicit_device else "unknown",
        reason,
        query=query,
        relation=UNCERTAIN,
    )


def _dynamic_decision(
    status: str,
    source: str,
    reason: str,
    *,
    query: QueryContract,
    document: DocumentIdentity | None = None,
    comparison: Any = None,
    relation: str = "",
    conflicts: tuple[str, ...] = (),
    request_document_id: str = "",
    request_device_type: str = "",
) -> ScopeDecision:
    resolved_relation = relation or getattr(comparison, "relation", "")
    resolved_conflicts = conflicts or tuple(getattr(comparison, "conflicts", ()) or ())
    return ScopeDecision(
        status=status,
        source=source,
        reason=reason,
        document_id=document.document_id if document else request_document_id,
        device_type=(document.device_type or document.device_category) if document else "",
        display_name=document.device_name if document else "",
        detected_device_type=query.raw_device_span,
        requested_document_id=request_document_id,
        requested_device_type=request_device_type,
        identity_relation=resolved_relation,
        identity_conflicts=resolved_conflicts,
    )


def _request_device_matches(value: Any, document: DocumentIdentity) -> bool:
    requested = _compact(value)
    if not requested:
        return False
    return requested in {
        _compact(document.device_type),
        _compact(document.device_name),
        _compact(document.model),
    }


def format_scope_guard_message(decision: ScopeDecision | Mapping[str, Any]) -> str:
    data = decision.to_dict() if isinstance(decision, ScopeDecision) else dict(decision)
    if data.get("reason") == "unknown_document":
        return "当前知识库没有找到所选手册，无法据此回答。请确认手册选择，或上传对应设备资料。"
    detected = _clean(data.get("detected_device_type"))
    current = _clean(data.get("display_name"))
    if detected and current:
        return f"当前知识库绑定的是{current}资料，与本次问题指定的设备不匹配，无法据此回答。请切换或上传对应设备手册。"
    return "当前知识库没有与该设备匹配的资料，无法据此回答。请提供或选择对应设备手册。"


@lru_cache(maxsize=1)
def get_scope_registry() -> ScopeRegistry:
    configured = _clean(os.environ.get("RAG_SCOPE_REGISTRY_PATH"))
    path = Path(configured) if configured else Path(__file__).resolve().parents[2] / "config" / "scope_registry.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return ScopeRegistry.from_dict(payload)
