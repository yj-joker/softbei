from services.retrieval.manual_scope import (
    apply_authoritative_manual_scope,
    build_manual_retrieval_kwargs,
    build_manual_retrieval_scope,
    manual_scope_tool_kwargs,
)


def test_graph_scope_fields_enter_authoritative_manual_scope() -> None:
    scope = build_manual_retrieval_scope(
        selected_document_id="manual-1",
        selected_section_id="section-1",
        graph_scope={
            "document_id": "manual-1",
            "document_version": "v1",
            "allowed_section_ids": ["section-1"],
            "allowed_evidence_refs": ["row-1"],
            "allowed_source_chunk_uids": ["chunk-uid-1"],
            "pages": [12, 13],
        },
    )

    assert scope["server_authoritative"] is True
    assert scope["document_id"] == "manual-1"
    assert scope["document_version"] == "v1"
    assert scope["parent_section_id"] == "section-1"
    assert scope["allowed_section_ids"] == ["section-1"]
    assert "allowed_evidence_refs" not in scope
    assert scope["allowed_source_chunk_uids"] == ["chunk-uid-1"]
    assert scope["pages"] == [12, 13]
    assert scope["device_type"] == ""
    assert scope["scope_fingerprint"].startswith("manual-scope:")


def test_resolved_scope_has_priority_over_broader_graph_scope() -> None:
    scope = build_manual_retrieval_scope(
        selected_document_id="manual-1",
        selected_section_id="section-1",
        resolved_scope={
            "document_id": "manual-1",
            "allowed_section_ids": ["section-1"],
            "allowed_evidence_refs": ["row-1"],
            "allowed_source_chunk_uids": ["chunk-uid-1"],
            "pages": [12],
        },
        graph_scope={
            "document_id": "manual-1",
            "document_version": "v1",
            "allowed_section_ids": ["section-1", "section-2"],
            "allowed_evidence_refs": ["row-1", "row-2"],
            "allowed_source_chunk_uids": ["chunk-uid-1", "chunk-uid-2"],
            "pages": [12, 13],
        },
    )

    assert scope["allowed_section_ids"] == ["section-1"]
    assert scope["allowed_evidence_refs"] == ["row-1"]
    assert scope["allowed_source_chunk_uids"] == ["chunk-uid-1"]
    assert scope["pages"] == [12]
    assert scope["document_version"] == "v1"


def test_foreign_graph_scope_cannot_broaden_selected_document() -> None:
    scope = build_manual_retrieval_scope(
        selected_document_id="manual-1",
        graph_scope={
            "document_id": "foreign-manual",
            "document_version": "v9",
            "allowed_section_ids": ["foreign-section"],
            "pages": [99],
        },
    )

    assert scope["document_id"] == "manual-1"
    assert "document_version" not in scope
    assert "allowed_section_ids" not in scope
    assert "pages" not in scope


def test_scope_application_clears_model_owned_filters_before_injection() -> None:
    scope = build_manual_retrieval_scope(
        selected_document_id="manual-1",
        graph_scope={
            "document_id": "manual-1",
            "document_version": "v1",
            "allowed_section_ids": ["section-1"],
            "allowed_source_chunk_uids": ["chunk-uid-1"],
            "pages": [12],
        },
    )
    effective = apply_authoritative_manual_scope(
        {
            "query": "故障原因",
            "document_id": "foreign-manual",
            "document_version": "foreign-version",
            "device_type": "纯电动客车",
            "parent_section_id": "foreign-section",
            "allowed_section_ids": ["foreign-section"],
            "allowed_evidence_refs": ["foreign-row"],
            "allowed_source_chunk_uids": ["foreign-chunk"],
            "pages": [99],
        },
        scope,
    )

    assert effective == {
        "query": "故障原因",
        **manual_scope_tool_kwargs(scope),
    }
    assert "device_type" not in effective


def test_manual_retrieval_kwargs_exclude_internal_scope_metadata() -> None:
    scope = build_manual_retrieval_scope(
        selected_document_id="manual-1",
        graph_scope={
            "document_id": "manual-1",
            "document_version": "v1",
            "allowed_source_chunk_uids": ["chunk-uid-1"],
            "pages": [12],
        },
    )

    assert build_manual_retrieval_kwargs("保险熔断", scope, top_k=8) == {
        "query": "保险熔断",
        "top_k": 8,
        "document_id": "manual-1",
        "document_version": "v1",
        "allowed_source_chunk_uids": ["chunk-uid-1"],
        "pages": [12],
    }
