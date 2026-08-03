"""Resolve open-vocabulary entity roles from runtime catalogs.

No device or component names are registered here.  A model-proposed device
span is demoted only when the imported section directory proves that it is a
document-internal object and the imported document identity directory does not
prove that it is a device identity.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable

from services.retrieval.device_identity import MATCHED, DeviceCatalog, QueryContract
from services.retrieval.section_index import SectionRef
from services.routing.models import EntityResolution


def _normalized(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[^0-9a-z一-鿿]+", "", text)


def _section_matches_span(raw_span: str, section: SectionRef) -> bool:
    span = _normalized(raw_span)
    title = _normalized(section.core_title or section.full_title)
    if len(span) < 2 or len(title) < 2:
        return False
    if span in title or (len(title) >= 4 and title in span):
        return True
    previous = [0] * (len(title) + 1)
    for left in span:
        current = [0]
        for index, right in enumerate(title, start=1):
            current.append(previous[index - 1] + 1 if left == right else max(previous[index], current[-1]))
        previous = current
    shared = previous[-1]
    return shared >= 4 and shared / min(len(span), len(title)) >= 0.60


_OPERATION_TARGET_RE = re.compile(
    r"(?:如何|怎么|怎样|请|需要|要|进行|开始|继续|先)?"
    r"(?:安装|装配|拆卸|拆除|拆下|取下|检查|检修|测量|检测|更换|调整|校正)"
    r"(?:一下|这个|该)?$"
)


def _is_operation_target(contract: QueryContract) -> bool:
    if contract.task_action not in {"formal_procedure", "repair_guidance"}:
        return False
    query = str(contract.raw_query or "")
    span = str(contract.raw_device_span or "")
    start = query.find(span)
    if start < 0:
        return False
    prefix = query[max(0, start - 12):start]
    return bool(_OPERATION_TARGET_RE.search(prefix))


def _span_matches_extracted_component(contract: QueryContract) -> bool:
    """Whether two model fields describe the same open-vocabulary object span."""
    span = _normalized(contract.raw_device_span)
    component = _normalized(contract.component)
    if len(span) < 4 or len(component) < 4 or abs(len(span) - len(component)) > 2:
        return False
    shared = 0
    previous = [0] * (len(component) + 1)
    for left in span:
        current = [0]
        for index, right in enumerate(component, start=1):
            current.append(previous[index - 1] + 1 if left == right else max(previous[index], current[-1]))
        previous = current
    shared = previous[-1]
    return shared / min(len(span), len(component)) >= 0.85


def _span_contains_multiple_component_terms(contract: QueryContract) -> bool:
    """Recognize a model-labelled compound object without naming its domain terms."""
    span = _normalized(contract.raw_device_span)
    terms = tuple(dict.fromkeys(
        normalized
        for raw_term in re.split(r"[\s,，、;；/|]+", str(contract.component or ""))
        if (normalized := _normalized(raw_term)) and len(normalized) >= 2
    ))
    if len(terms) < 2 or not span or not all(term in span for term in terms):
        return False
    return sum(len(term) for term in terms) / len(span) >= 0.35


def _resolve_compound_dynamic_identity(
    contract: QueryContract,
    catalog: DeviceCatalog,
    sections: tuple[SectionRef, ...],
) -> tuple[QueryContract, tuple[str, ...]] | None:
    """Recover an imported identity when the extracted span also contains a section object.

    Authorization requires two independent runtime facts from the same document:
    the span starts with its imported identity, and the extracted component matches
    one of its imported section titles.  The optional bridge is structural (at
    most one character), so no device, component, alias, or question vocabulary
    is registered here.
    """
    span = _normalized(contract.raw_device_span)
    component = _normalized(contract.component)
    if not span or not component:
        return None
    section_document_ids = {
        section.document_id
        for section in sections
        if section.document_id and _section_matches_span(contract.component, section)
    }
    if not section_document_ids:
        return None

    resolved: list[tuple[QueryContract, str]] = []
    for document in catalog.documents:
        if document.document_id not in section_document_ids:
            continue
        names = (document.device_name, *document.aliases)
        if not any(
            (name := _normalized(raw_name))
            and span.startswith(name)
            and (remainder := span[len(name):]).endswith(component)
            and len(remainder[:-len(component)]) <= 1
            for raw_name in names
        ):
            continue
        payload = contract.to_dict()
        payload["raw_device_span"] = document.device_name
        payload["device_name"] = document.device_name
        canonical = QueryContract.from_mapping(payload, raw_query=contract.raw_query)
        comparison = next(
            (item for item in catalog.match(canonical) if item.document.document_id == document.document_id),
            None,
        )
        if comparison is not None and comparison.relation == MATCHED:
            resolved.append((canonical, document.document_id))

    document_ids = tuple(dict.fromkeys(document_id for _, document_id in resolved))
    if not resolved:
        return None
    canonical_contracts = {
        item.raw_device_span: item
        for item, _ in resolved
    }
    if len(canonical_contracts) != 1:
        return None
    return next(iter(canonical_contracts.values())), document_ids


class EntityResolver:
    """Validate whether the extracted span represents a device or a section object."""

    def resolve(
        self,
        contract: QueryContract,
        catalog: DeviceCatalog,
        section_refs: Iterable[SectionRef],
    ) -> EntityResolution:
        sections = tuple(section_refs)
        section_document_ids = tuple(dict.fromkeys(
            section.document_id
            for section in sections
            if section.document_id and catalog.document(section.document_id) is not None
        ))
        if not contract.has_explicit_device:
            return EntityResolution(
                contract=contract,
                entity_role="document_component" if section_document_ids else "unspecified",
                reason="matched_dynamic_section" if section_document_ids else "no_explicit_identity",
                matched_section_document_ids=section_document_ids,
            )

        comparisons = catalog.match(contract)
        if any(item.relation == MATCHED for item in comparisons):
            return EntityResolution(
                contract=contract,
                entity_role="device_identity",
                reason="matched_dynamic_document_identity",
                matched_section_document_ids=section_document_ids,
            )

        compound_identity = _resolve_compound_dynamic_identity(contract, catalog, sections)
        if compound_identity is not None:
            canonical_contract, matched_document_ids = compound_identity
            return EntityResolution(
                contract=canonical_contract,
                entity_role="device_identity",
                reason="resolved_compound_dynamic_identity",
                matched_section_document_ids=matched_document_ids,
            )

        has_identity_qualifier = bool(
            contract.carrier_or_application or contract.manufacturer or contract.model
        )
        matching_sections = tuple(
            section for section in sections if _section_matches_span(contract.raw_device_span, section)
        )
        operation_target = _is_operation_target(contract)
        component_role = bool(
            section_document_ids
            and (
                _span_matches_extracted_component(contract)
                or _span_contains_multiple_component_terms(contract)
            )
        )
        if matching_sections or ((operation_target or component_role) and not has_identity_qualifier):
            payload = contract.to_dict()
            for field in (
                "raw_device_span",
                "device_name",
                "device_category",
                "carrier_or_application",
                "manufacturer",
                "model",
                "identity_resolution",
            ):
                payload[field] = ""
            payload["identity_resolution"] = "confirmed_absent"
            demoted = QueryContract.from_mapping(payload, raw_query=contract.raw_query)
            matched_ids = tuple(dict.fromkeys(
                section.document_id
                for section in (matching_sections or sections)
                if section.document_id and catalog.document(section.document_id) is not None
            ))
            return EntityResolution(
                contract=demoted,
                entity_role="document_component",
                reason=(
                    "matched_dynamic_section"
                    if matching_sections
                    else "component_span_confirmed_by_dynamic_section"
                    if component_role
                    else "grounded_operation_target"
                ),
                matched_section_document_ids=matched_ids,
            )

        return EntityResolution(
            contract=contract,
            entity_role="device_identity",
            reason="explicit_identity_not_in_catalog",
            matched_section_document_ids=section_document_ids,
        )
