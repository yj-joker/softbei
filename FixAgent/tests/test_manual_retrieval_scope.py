from services.retrieval.manual_scope import (
    apply_authoritative_manual_scope,
    build_additive_manual_scopes,
    build_graph_manual_fallback_kwargs,
    build_manual_retrieval_kwargs,
    build_manual_retrieval_scope,
    manual_scope_tool_kwargs,
)


def test_additive_scopes_keep_graph_locators_out_of_baseline() -> None:
    scopes = build_additive_manual_scopes(
        selected_document_id="manual-1",
        resolved_scope={"document_id": "manual-1"},
        graph_scope={
            "document_id": "manual-1",
            "document_version": "v3",
            "allowed_source_chunk_uids": ["chunk-graph"],
            "pages": [12],
        },
    )

    assert scopes.baseline["document_id"] == "manual-1"
    assert scopes.baseline["server_authoritative"] is True
    assert "allowed_source_chunk_uids" not in scopes.baseline
    assert "pages" not in scopes.baseline
    assert scopes.graph_seed["allowed_source_chunk_uids"] == ["chunk-graph"]
    assert scopes.graph_seed["pages"] == [12]


def test_graph_seed_inherits_resolved_constraints_across_locator_types() -> None:
    scopes = build_additive_manual_scopes(
        selected_document_id="manual-1",
        resolved_scope={
            "document_id": "manual-1",
            "allowed_section_ids": ["section-1"],
            "pages": [12],
        },
        graph_scope={
            "document_id": "manual-1",
            "allowed_source_chunk_uids": ["chunk-graph"],
        },
    )

    assert scopes.graph_seed["allowed_section_ids"] == ["section-1"]
    assert scopes.graph_seed["allowed_source_chunk_uids"] == ["chunk-graph"]
    assert scopes.graph_seed["pages"] == [12]


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


def test_manual_retrieval_kwargs_carry_server_query_contract_as_internal_parameter() -> None:
    contract = {"component": "火花塞", "fault": "火花塞损坏"}

    kwargs = build_manual_retrieval_kwargs(
        "火花塞损坏如何处理",
        {"document_id": "manual-1"},
        top_k=5,
        query_contract=contract,
    )

    assert kwargs["_query_contract"] == contract


def test_graph_seed_scope_can_fallback_inside_same_document_and_section() -> None:
    scope = build_manual_retrieval_scope(
        selected_document_id="manual-1",
        selected_section_id="section-1",
        graph_scope={
            "document_id": "manual-1",
            "document_version": "v1",
            "allowed_section_ids": ["section-1"],
            "allowed_source_chunk_uids": ["chunk-1"],
            "allowed_evidence_refs": ["evidence-1"],
            "pages": [12],
        },
    )

    assert scope["graph_fallback_allowed"] is True
    assert build_graph_manual_fallback_kwargs("bearing noise", scope, top_k=7) == {
        "query": "bearing noise",
        "top_k": 7,
        "document_id": "manual-1",
        "document_version": "v1",
        "parent_section_id": "section-1",
        "allowed_section_ids": ["section-1"],
    }


def test_resolved_scope_never_allows_graph_seed_fallback() -> None:
    scope = build_manual_retrieval_scope(
        selected_document_id="manual-1",
        resolved_scope={
            "document_id": "manual-1",
            "allowed_section_ids": ["section-1"],
            "allowed_evidence_refs": ["evidence-1"],
        },
        graph_scope={
            "document_id": "manual-1",
            "allowed_section_ids": ["section-1", "section-2"],
            "allowed_source_chunk_uids": ["chunk-1"],
            "pages": [12],
        },
    )

    assert scope.get("graph_fallback_allowed") is not True
    assert build_graph_manual_fallback_kwargs("bearing noise", scope) is None


def test_graph_seed_inherits_resolved_section_when_graph_only_supplies_chunk() -> None:
    scopes = build_additive_manual_scopes(
        selected_document_id="manual-1",
        resolved_scope={
            "document_id": "manual-1",
            "allowed_section_ids": ["section-1"],
        },
        graph_scope={
            "document_id": "manual-1",
            "allowed_source_chunk_uids": ["chunk-graph"],
        },
    )

    assert scopes.graph_seed["allowed_section_ids"] == ["section-1"]
    assert scopes.graph_seed["allowed_source_chunk_uids"] == ["chunk-graph"]
