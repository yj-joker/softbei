"""Resolve open-vocabulary entity roles from runtime catalogs.

No device or component names are registered here.  A model-proposed device
span is demoted only when the imported section directory proves that it is a
document-internal object and the imported document identity directory does not
prove that it is a device identity.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable, Mapping

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


def _graph_dimension(candidate: Any, *names: str) -> str:
    if isinstance(candidate, Mapping):
        dimensions = candidate.get("dimensions")
        dimensions = dimensions if isinstance(dimensions, Mapping) else {}
        for name in names:
            value = candidate.get(name)
            if value in (None, ""):
                value = dimensions.get(name)
            if value not in (None, ""):
                return str(value).strip()
        return ""
    dimensions = getattr(candidate, "dimensions", {}) or {}
    for name in names:
        value = getattr(candidate, name, None)
        if value in (None, "") and isinstance(dimensions, Mapping):
            value = dimensions.get(name)
        if value not in (None, ""):
            return str(value).strip()
    return ""


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


def _span_is_composed_from_structured_part_fields(contract: QueryContract) -> bool:
    """Prove that a proposed device span is actually a structured part span.

    This exact structural check has no knowledge of device, component, or
    specification vocabulary. The normalized structured fields must jointly
    cover the complete proposed identity span without a gap.
    """
    span = _normalized(contract.raw_device_span)
    anchors = tuple(dict.fromkeys(
        value
        for value in (
            _normalized(contract.part_spec),
            _normalized(contract.raw_component_span),
            _normalized(contract.component),
        )
        if len(value) >= 2
    ))
    if len(anchors) < 2 or not span or not all(anchor in span for anchor in anchors):
        return False

    intervals: list[tuple[int, int]] = []
    for anchor in anchors:
        start = span.find(anchor)
        while start >= 0:
            intervals.append((start, start + len(anchor)))
            start = span.find(anchor, start + 1)
    intervals.sort()
    covered_until = 0
    for start, end in intervals:
        if start > covered_until:
            break
        covered_until = max(covered_until, end)
        if covered_until == len(span):
            return True
    return False


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


def _resolve_section_suffix_dynamic_identity(
    contract: QueryContract,
    catalog: DeviceCatalog,
    sections: tuple[SectionRef, ...],
) -> tuple[QueryContract, tuple[str, ...]] | None:
    """Recover identity/component boundaries from imported document and section names."""
    raw_span = str(contract.raw_device_span or "").strip()
    if not raw_span or contract.component:
        return None
    resolved: list[tuple[QueryContract, str]] = []
    for document in catalog.documents:
        document_sections = tuple(section for section in sections if section.document_id == document.document_id)
        if not document_sections:
            continue
        for raw_name in (document.device_name, *document.aliases):
            name = str(raw_name or "").strip()
            if not name or not raw_span.casefold().startswith(name.casefold()):
                continue
            remainder = raw_span[len(name):].strip()
            if not remainder or not any(_section_matches_span(remainder, section) for section in document_sections):
                continue
            payload = contract.to_dict()
            payload.update({
                "raw_device_span": document.device_name,
                "device_name": document.device_name,
                "component": remainder,
                "raw_component_span": remainder,
            })
            canonical = QueryContract.from_mapping(payload, raw_query=contract.raw_query)
            comparison = next(
                (item for item in catalog.match(canonical) if item.document.document_id == document.document_id),
                None,
            )
            if comparison is not None and comparison.relation == MATCHED:
                resolved.append((canonical, document.document_id))
            break
    document_ids = tuple(dict.fromkeys(document_id for _, document_id in resolved))
    contracts = {item.raw_device_span: item for item, _ in resolved}
    if len(document_ids) != 1 or len(contracts) != 1:
        return None
    return next(iter(contracts.values())), document_ids


class EntityResolver:
    """Validate whether the extracted span represents a device or a section object."""

    def resolve(
        self,
        contract: QueryContract,
        catalog: DeviceCatalog,
        section_refs: Iterable[SectionRef],
        *,
        graph_candidates: Iterable[Any] = (),
    ) -> EntityResolution:
        sections = tuple(section_refs)
        graph_values = tuple(graph_candidates or ())
        section_document_ids = tuple(dict.fromkeys(
            section.document_id
            for section in sections
            if section.document_id and catalog.document(section.document_id) is not None
        ))
        span = _normalized(contract.raw_device_span or contract.component)
        graph_component_document_ids = tuple(dict.fromkeys(
            document_id
            for candidate in graph_values
            if (
                (component_name := _normalized(_graph_dimension(
                    candidate, "componentName", "component_name", "component"
                )))
                and span
                and (span == component_name or span in component_name or component_name in span)
                and (device_name := _normalized(_graph_dimension(
                    candidate, "deviceName", "device_name", "device_identity"
                )))
                and device_name != span
                and (document_id := _graph_dimension(
                    candidate, "documentId", "document_id"
                ))
                and catalog.document(document_id) is not None
            )
        ))
        if not contract.has_explicit_device:
            return EntityResolution(
                contract=contract,
                entity_role="document_component" if section_document_ids or graph_component_document_ids else "unspecified",
                reason=(
                    "graph_parent_device_proves_component"
                    if graph_component_document_ids
                    else "matched_dynamic_section"
                    if section_document_ids
                    else "no_explicit_identity"
                ),
                matched_section_document_ids=graph_component_document_ids or section_document_ids,
            )

        comparisons = catalog.match(contract)
        if any(item.relation == MATCHED for item in comparisons):
            return EntityResolution(
                contract=contract,
                entity_role="device_identity",
                reason="matched_dynamic_document_identity",
                matched_section_document_ids=section_document_ids,
            )

        if graph_component_document_ids:
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
            return EntityResolution(
                contract=demoted,
                entity_role="document_component",
                reason="graph_parent_device_proves_component",
                matched_section_document_ids=graph_component_document_ids,
            )

        section_suffix_identity = _resolve_section_suffix_dynamic_identity(contract, catalog, sections)
        if section_suffix_identity is not None:
            canonical_contract, matched_document_ids = section_suffix_identity
            return EntityResolution(
                contract=canonical_contract,
                entity_role="device_identity",
                reason="resolved_section_suffix_dynamic_identity",
                matched_section_document_ids=matched_document_ids,
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

        matching_sections = tuple(
            section for section in sections if _section_matches_span(contract.raw_device_span, section)
        )
        operation_target = _is_operation_target(contract)
        structured_part_role = _span_is_composed_from_structured_part_fields(contract)
        component_span_overlap = bool(
            _span_matches_extracted_component(contract)
            or _span_contains_multiple_component_terms(contract)
        )
        normalized_model = _normalized(contract.model)
        normalized_part_spec = _normalized(contract.part_spec)
        model_is_part_field = bool(
            normalized_model
            and (
                component_span_overlap
                or (
                    normalized_part_spec
                    and normalized_model == normalized_part_spec
                )
            )
        )
        has_identity_qualifier = bool(
            contract.carrier_or_application
            or contract.manufacturer
            or (contract.model and not model_is_part_field)
        )
        component_role = bool(
            section_document_ids
            and (
                component_span_overlap
                or structured_part_role
            )
        )
        if (
            matching_sections
            or (operation_target and not has_identity_qualifier)
            or (component_role and (not has_identity_qualifier or structured_part_role))
        ):
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
