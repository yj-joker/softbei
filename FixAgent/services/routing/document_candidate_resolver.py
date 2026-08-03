"""Resolve zero, one, or multiple document candidates without static aliases."""

from __future__ import annotations

from typing import Iterable

from services.retrieval.device_identity import (
    MATCHED,
    DeviceCatalog,
    QueryContract,
    query_mentions_unresolved_identity,
)
from services.retrieval.section_index import SectionRef
from services.routing.models import DocumentCandidateResolution, RouteAction


class DocumentCandidateResolver:
    def resolve(
        self,
        contract: QueryContract,
        catalog: DeviceCatalog,
        section_refs: Iterable[SectionRef],
        *,
        request_document_id: str = "",
        session_document_id: str = "",
    ) -> DocumentCandidateResolution:
        requested = str(request_document_id or "").strip()
        session = str(session_document_id or "").strip()

        if requested:
            document = catalog.document(requested)
            if document is None:
                return self._fallback("requested_document_not_in_catalog")
            if query_mentions_unresolved_identity(contract, document):
                return self._fallback("unresolved_device_identity")
            if contract.has_explicit_device:
                comparison = next(
                    (item for item in catalog.match(contract) if item.document.document_id == requested),
                    None,
                )
                if comparison is None or comparison.relation != MATCHED:
                    return self._fallback("explicit_identity_conflicts_with_requested_document")
            return self._grounded((requested,), "request_document")

        if contract.has_explicit_device:
            matched = tuple(dict.fromkeys(
                item.document.document_id
                for item in catalog.match(contract)
                if item.relation == MATCHED
            ))
            if len(matched) == 1:
                return self._grounded(matched, "unique_identity_match")
            if len(matched) > 1:
                return self._clarify(matched, "multiple_identity_matches")
            return self._fallback("no_matching_device_document")

        section_documents = tuple(dict.fromkeys(
            section.document_id
            for section in section_refs
            if section.document_id and catalog.document(section.document_id) is not None
        ))
        if len(section_documents) == 1:
            return self._grounded(section_documents, "unique_section_match")
        if len(section_documents) > 1:
            return self._clarify(section_documents, "multiple_section_matches")

        if session and catalog.document(session) is not None:
            return self._grounded((session,), "session_document")
        return self._fallback("no_matching_document")

    @staticmethod
    def _grounded(document_ids: tuple[str, ...], reason: str) -> DocumentCandidateResolution:
        return DocumentCandidateResolution(
            action=RouteAction.GROUNDED_RETRIEVAL,
            candidate_document_ids=document_ids,
            selected_document_id=document_ids[0],
            reason=reason,
        )

    @staticmethod
    def _clarify(document_ids: tuple[str, ...], reason: str) -> DocumentCandidateResolution:
        return DocumentCandidateResolution(
            action=RouteAction.CLARIFY_DOCUMENT,
            candidate_document_ids=document_ids,
            selected_document_id="",
            reason=reason,
        )

    @staticmethod
    def _fallback(reason: str) -> DocumentCandidateResolution:
        return DocumentCandidateResolution(
            action=RouteAction.AI_FALLBACK,
            candidate_document_ids=(),
            selected_document_id="",
            reason=reason,
        )
