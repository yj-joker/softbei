"""Dynamic document catalog and open-vocabulary device identity matching.

The catalog is built from imported document manifests.  It deliberately does
not contain a list of unsupported devices: a query identity is compared with
the identities of documents that actually exist in the knowledge base.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


MATCHED = "matched"
UNMATCHED = "unmatched"
UNCERTAIN = "uncertain"

_IDENTITY_FIELDS = (
    "device_category",
    "carrier_or_application",
    "manufacturer",
    "model",
)
_HARD_CONFLICT_FIELDS = (
    "carrier_or_application",
    "manufacturer",
    "model",
)
_COMPONENT_CATEGORY_SUFFIXES = ("部件", "组件", "零件", "总成")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalized(value: Any) -> str:
    text = unicodedata.normalize("NFKC", _text(value)).casefold()
    return re.sub(r"[\s_\-—–·,，。:：/\\()（）\[\]【】]+", "", text)


def _is_grounded_operation_target(raw_query: str, action: str, raw_span: str) -> bool:
    """Whether the extracted span is the grammatical target of an operation.

    This is an entity-role check, not a component-name list: a component-like
    category immediately following the grounded action is an operation target,
    while carrier/manufacturer/model-qualified spans remain device identities.
    """
    query = _normalized(raw_query)
    normalized_action = _normalized(action)
    span = _normalized(raw_span)
    if not query or not normalized_action or not span:
        return False
    start = query.find(normalized_action)
    while start >= 0:
        tail = query[start + len(normalized_action):]
        if tail.startswith(span):
            remainder = tail[len(span):]
            if not remainder or remainder[0] in "时的前后、？?；;":
                return True
        start = query.find(normalized_action, start + len(normalized_action))
    return False


@dataclass(frozen=True)
class QueryContract:
    raw_query: str
    intent: str = ""
    task_action: str = ""
    raw_device_span: str = ""
    device_name: str = ""
    device_category: str = ""
    carrier_or_application: str = ""
    manufacturer: str = ""
    model: str = ""
    component: str = ""
    action: str = ""
    orientation: str = ""
    risk_level: str = ""
    identity_resolution: str = ""

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any] | None,
        *,
        raw_query: str,
    ) -> "QueryContract":
        data = dict(payload or {})
        query = _text(raw_query)
        raw_span = _text(data.get("raw_device_span"))
        span_is_grounded = bool(raw_span and raw_span.casefold() in query.casefold())
        component = _text(data.get("component"))
        orientation = _text(data.get("orientation"))
        component_forms = {
            value
            for value in (
                _normalized(component),
                _normalized(orientation + component),
                _normalized(component + orientation),
            )
            if value
        }
        normalized_span = _normalized(raw_span)
        normalized_component = _normalized(component)
        normalized_category = _normalized(data.get("device_category"))
        category_is_component_like = any(
            normalized_category.endswith(_normalized(suffix))
            for suffix in _COMPONENT_CATEGORY_SUFFIXES
        )
        component_remainder_length = len(normalized_span) - len(normalized_component)
        component_with_short_modifier = bool(
            normalized_span
            and normalized_component
            and category_is_component_like
            and 0 < component_remainder_length <= 2
            and (
                normalized_span.startswith(normalized_component)
                or normalized_span.endswith(normalized_component)
            )
        )
        has_identity_qualifier = bool(
            _text(data.get("carrier_or_application"))
            or _text(data.get("manufacturer"))
            or _text(data.get("model"))
        )
        operation_target_is_component = bool(
            category_is_component_like
            and _is_grounded_operation_target(query, data.get("action"), raw_span)
        )
        if (
            span_is_grounded
            and (
                normalized_span in component_forms
                or component_with_short_modifier
                or operation_target_is_component
            )
            and not has_identity_qualifier
        ):
            span_is_grounded = False
        if not span_is_grounded:
            raw_span = ""
            for field in ("device_name", *_IDENTITY_FIELDS):
                data[field] = ""
        identity_resolution = _text(data.get("identity_resolution"))
        if identity_resolution != "confirmed_absent" or raw_span:
            identity_resolution = ""
        return cls(
            raw_query=query,
            intent=_text(data.get("intent")),
            task_action=_text(data.get("task_action")),
            raw_device_span=raw_span,
            device_name=raw_span or _text(data.get("device_name")),
            device_category=_text(data.get("device_category")),
            carrier_or_application=_text(data.get("carrier_or_application")),
            manufacturer=_text(data.get("manufacturer")),
            model=_text(data.get("model")),
            component=_text(data.get("component")),
            action=_text(data.get("action")),
            orientation=_text(data.get("orientation")),
            risk_level=_text(data.get("risk_level")),
            identity_resolution=identity_resolution,
        )

    @property
    def has_explicit_device(self) -> bool:
        return bool(self.raw_device_span)

    def to_dict(self) -> dict[str, str]:
        return {
            "raw_query": self.raw_query,
            "intent": self.intent,
            "task_action": self.task_action,
            "raw_device_span": self.raw_device_span,
            "device_name": self.device_name,
            "device_category": self.device_category,
            "carrier_or_application": self.carrier_or_application,
            "manufacturer": self.manufacturer,
            "model": self.model,
            "component": self.component,
            "action": self.action,
            "orientation": self.orientation,
            "risk_level": self.risk_level,
            "identity_resolution": self.identity_resolution,
        }


@dataclass(frozen=True)
class DocumentIdentity:
    document_id: str
    device_name: str
    device_type: str = ""
    device_category: str = ""
    carrier_or_application: str = ""
    manufacturer: str = ""
    model: str = ""
    manual_type: str = ""
    document_version: str = ""
    confidence: float = 0.0
    index_revision: int = 0
    aliases: tuple[str, ...] = ()

    @classmethod
    def from_manifest(cls, manifest: Mapping[str, Any]) -> "DocumentIdentity | None":
        document_id = _text(manifest.get("document_id"))
        identity = manifest.get("document_identity")
        data = dict(identity) if isinstance(identity, Mapping) else {}
        device_name = _text(data.get("device_name"))
        confidence = _float(data.get("confidence") or data.get("identity_confidence"))
        if not document_id or not device_name or confidence < 0.65:
            return None
        raw_aliases = data.get("aliases") if isinstance(data.get("aliases"), list) else []
        aliases = tuple(
            dict.fromkeys(
                value
                for value in (_text(item) for item in raw_aliases)
                if value and value != device_name
            )
        )
        return cls(
            document_id=document_id,
            device_name=device_name,
            device_type=_text(manifest.get("device_type") or data.get("device_type")),
            device_category=_text(data.get("device_category")),
            carrier_or_application=_text(data.get("carrier_or_application")),
            manufacturer=_text(data.get("manufacturer")),
            model=_text(data.get("model")),
            manual_type=_text(manifest.get("manual_type") or data.get("manual_type")),
            document_version=_text(manifest.get("document_version") or data.get("document_version")),
            confidence=confidence,
            index_revision=_int(manifest.get("index_revision")),
            aliases=aliases,
        )


@dataclass(frozen=True)
class IdentityComparison:
    relation: str
    document: DocumentIdentity
    conflicts: tuple[str, ...] = ()
    matched_fields: tuple[str, ...] = ()
    reason: str = ""


class DeviceCatalog:
    def __init__(self, documents: Iterable[DocumentIdentity]) -> None:
        self.documents = tuple(documents)
        self._by_id = {document.document_id: document for document in self.documents}

    @classmethod
    def from_manifests(cls, manifests: Iterable[Mapping[str, Any]]) -> "DeviceCatalog":
        documents: list[DocumentIdentity] = []
        for manifest in manifests:
            if _text(manifest.get("status")) != "ready":
                continue
            identity = DocumentIdentity.from_manifest(manifest)
            if identity is not None:
                documents.append(identity)
        return cls(documents)

    def document(self, document_id: Any) -> DocumentIdentity | None:
        return self._by_id.get(_text(document_id))

    def match(self, query: QueryContract) -> tuple[IdentityComparison, ...]:
        return tuple(compare_query_to_document(query, document) for document in self.documents)


def document_identity_heads(document: DocumentIdentity) -> tuple[str, ...]:
    """Return generic identity heads derived only from an imported manifest.

    For example, a compound manifest identity made from a carrier qualifier
    and a device category yields the category as its generic head.  No carrier
    names or aliases are registered here; the result is entirely data-driven
    by the imported document identity.
    """
    qualifiers = tuple(
        value
        for value in (
            _normalized(document.carrier_or_application),
            _normalized(document.manufacturer),
            _normalized(document.model),
        )
        if value
    )
    heads: list[str] = []
    for raw_name in (document.device_name, *document.aliases):
        name = _normalized(raw_name)
        if not name:
            continue
        head = name
        for qualifier in qualifiers:
            head = head.replace(qualifier, "")
        if len(head) >= 2 and head != name:
            heads.append(head)
    category = _normalized(document.device_category)
    if len(category) >= 2 and any(category in _normalized(name) for name in (document.device_name, *document.aliases)):
        heads.append(category)
    return tuple(dict.fromkeys(heads))


def query_mentions_unresolved_identity(
    query: QueryContract,
    document: DocumentIdentity,
) -> bool:
    """Conservatively detect an identity-bearing query missed by extraction.

    This signal can only *remove* retrieval authority.  It never turns an
    unknown query into a matched document, so a dynamic catalog term cannot
    become a reverse keyword whitelist.
    """
    raw_query = _normalized(query.raw_query)
    if not raw_query:
        return False
    heads = document_identity_heads(document)
    if not query.has_explicit_device:
        if query.identity_resolution == "confirmed_absent":
            return False
        return any(head in raw_query for head in heads)

    span = _normalized(query.raw_device_span)
    if not span or span not in heads:
        return False

    # A model can return only the generic head from a larger compound identity.
    # Treat that non-maximal span as unresolved.  The API layer may ask the
    # focused semantic extractor to confirm that the prefix is an operation;
    # without that confirmation, scope authorization remains conservative.
    start = raw_query.find(span)
    while start >= 0:
        prefix = raw_query[:start]
        if not prefix:
            return False
        start = raw_query.find(span, start + len(span))
    return True


def query_has_grounded_operation_target(
    query: QueryContract,
    document: DocumentIdentity,
) -> bool:
    """Whether focused semantics prove that a document head is an action target.

    The action and the document-derived generic head must both be literal spans
    in the current query, with the action immediately governing the head.  An
    empty LLM extraction therefore cannot authorize a document by itself.
    """
    if (
        query.task_action not in {"formal_procedure", "repair_guidance"}
        or query.has_explicit_device
        or not query.action
    ):
        return False
    return any(
        _is_grounded_operation_target(query.raw_query, query.action, head)
        for head in document_identity_heads(document)
    )


def query_is_unqualified_document_head(
    query: QueryContract,
    document: DocumentIdentity,
) -> bool:
    """Whether the explicit span is only the selected document's generic head."""
    span = _normalized(query.raw_device_span)
    if not span or span not in document_identity_heads(document):
        return False
    return not any(
        _normalized(getattr(query, field))
        for field in ("carrier_or_application", "manufacturer", "model")
    )


def _query_device_name_candidates(query: QueryContract) -> tuple[str, ...]:
    query_name = _normalized(query.device_name or query.raw_device_span)
    if not query_name:
        return ()
    candidates = [query_name]
    component_forms = {
        value
        for value in (
            _normalized(query.component),
            _normalized(query.orientation + query.component),
            _normalized(query.component + query.orientation),
        )
        if value
    }
    for component in component_forms:
        if query_name.endswith(component) and len(query_name) > len(component):
            candidates.append(query_name[:-len(component)])
        if query_name.startswith(component) and len(query_name) > len(component):
            candidates.append(query_name[len(component):])
    return tuple(dict.fromkeys(value for value in candidates if value))


def compare_query_to_document(
    query: QueryContract,
    document: DocumentIdentity,
) -> IdentityComparison:
    if not query.has_explicit_device:
        return IdentityComparison(UNCERTAIN, document, reason="query_device_not_explicit")

    # A selected document may scope its own generic identity head even when the
    # intent model describes the category differently from the import manifest
    # (for example, "机械装置" versus "内燃机").  Keep this comparison uncertain,
    # never matched: only an explicit request/session document can authorize it.
    if query_is_unqualified_document_head(query, document):
        return IdentityComparison(UNCERTAIN, document, reason="identity_not_distinguishing")

    query_names = _query_device_name_candidates(query)
    query_name = query_names[0] if query_names else ""
    document_names = tuple(
        value for value in (_normalized(document.device_name), *map(_normalized, document.aliases)) if value
    )
    # 设备名和导入时确认的完整别名只做规范化精确匹配；
    # 通用类别名称不得通过子串关系授权更具体的复合设备名称。
    name_compatible = bool(set(query_names).intersection(document_names))
    attribute_conflicts = tuple(
        field
        for field in _HARD_CONFLICT_FIELDS
        if _normalized(getattr(query, field))
        and _normalized(getattr(document, field))
        and _normalized(getattr(query, field)) != _normalized(getattr(document, field))
    )
    category_only = query_name == _normalized(query.device_category)
    name_conflicts = (
        ("device_name",)
        if query_name and document_names and not name_compatible and not category_only
        else ()
    )
    conflicts = (*name_conflicts, *attribute_conflicts)
    if conflicts:
        return IdentityComparison(
            UNMATCHED,
            document,
            conflicts=conflicts,
            reason="identity_attribute_conflict",
        )

    matched_fields = tuple(
        field
        for field in _IDENTITY_FIELDS
        if _normalized(getattr(query, field))
        and _normalized(getattr(query, field)) == _normalized(getattr(document, field))
    )
    name_matches = bool(
        name_compatible
        and any(name != _normalized(query.device_category) for name in query_names)
    )
    distinguishing_match = any(
        field in matched_fields for field in ("carrier_or_application", "manufacturer", "model")
    )
    if name_matches or ("device_category" in matched_fields and distinguishing_match):
        return IdentityComparison(
            MATCHED,
            document,
            matched_fields=matched_fields,
            reason="identity_confirmed",
        )
    return IdentityComparison(
        UNCERTAIN,
        document,
        matched_fields=matched_fields,
        reason="identity_not_distinguishing",
    )


async def load_dynamic_device_catalog(
    *,
    vector_service: Any = None,
    llm_service: Any = None,
) -> DeviceCatalog:
    """Load the read-only runtime catalog from identities persisted at import time."""
    if vector_service is None:
        from services.knowledge.vector_service import get_vector_service

        vector_service = get_vector_service()
    manifests = list(vector_service.list_all_manifests() or [])
    return DeviceCatalog.from_manifests(manifests)


async def extract_document_identity_payload(
    *,
    manifest: Mapping[str, Any],
    source_items: Iterable[Mapping[str, Any]],
    llm_service: Any,
) -> dict[str, Any] | None:
    sample = _document_identity_sample(source_items)
    if not sample:
        return None
    system = (
        "你是维修知识库的文档身份抽取器，只输出 JSON。"
        "仅根据文件元数据和文档原文识别该手册适用的设备，不要根据知识库中其他文档猜测。"
        "返回字段 device_name、device_category、carrier_or_application、manufacturer、model、confidence。"
        "device_category 是设备类别，carrier_or_application 是设备所装载的平台或应用范围；"
        "无法从资料确认的字段输出空字符串，无法确认设备名称时 confidence 必须低于 0.65。"
    )
    user = {
        "file_name": _text(manifest.get("file_name")),
        "device_type_hint": _text(manifest.get("device_type")),
        "manual_type": _text(manifest.get("manual_type")),
        "document_version": _text(manifest.get("document_version")),
        "document_excerpt": sample,
    }
    try:
        response = await llm_service.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            ],
            temperature=0,
            max_tokens=240,
            response_format={"type": "json_object"},
        )
        data = _parse_json_mapping(response.get("content") if isinstance(response, Mapping) else "")
    except Exception:
        return None
    payload = {
        "device_name": _text(data.get("device_name")),
        "device_type": _text(manifest.get("device_type")),
        "device_category": _text(data.get("device_category")),
        "carrier_or_application": _text(data.get("carrier_or_application")),
        "manufacturer": _text(data.get("manufacturer")),
        "model": _text(data.get("model")),
        "confidence": _float(data.get("confidence")),
        "identity_source": "document_content",
    }
    if not payload["device_name"] or payload["confidence"] < 0.65:
        return None
    return payload


def _document_identity_sample(source_items: Iterable[Mapping[str, Any]]) -> str:
    samples: list[str] = []
    for item in source_items:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
        text = _text(metadata.get("raw_text") or item.get("text") or item.get("content"))
        if text:
            samples.append(text)
        if sum(len(value) for value in samples) >= 2400:
            break
    return "\n".join(samples)[:2400]


def _parse_json_mapping(value: Any) -> dict[str, Any]:
    text = _text(value)
    try:
        data = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    return dict(data) if isinstance(data, Mapping) else {}


def _float(value: Any) -> float:
    try:
        number = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        return 0.0
    return number


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
