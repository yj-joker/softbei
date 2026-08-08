from pathlib import Path


JAVA_ROOT = Path(__file__).resolve().parents[2] / "weixiu" / "src" / "main" / "java" / "ai" / "weixiu"


def test_java_search_query_declares_graph_allow_lists() -> None:
    source = (JAVA_ROOT / "pojo" / "query" / "DiagnosisSearchQuery.java").read_text(encoding="utf-8")

    for field in (
        "allowedPathIds",
        "allowedDeviceIds",
        "allowedComponentIds",
        "allowedFaultIds",
    ):
        assert f"List<String> {field}" in source


def test_java_path_response_declares_stable_identity_and_provenance() -> None:
    source = (JAVA_ROOT / "pojo" / "vo" / "DiagnosisPathVO.java").read_text(encoding="utf-8")

    expected_fields = {
        "String": ("pathId", "documentId", "documentVersion", "sectionId", "graphRevision", "provenanceStatus"),
        "List<String>": ("nodeIds", "relationshipTypes", "sourceChunkUids"),
        "List<Integer>": ("pages",),
    }
    for java_type, fields in expected_fields.items():
        for field in fields:
            assert f"{java_type} {field}" in source


def test_java_graph_query_enforces_scope_and_maps_stable_path_evidence() -> None:
    source = (JAVA_ROOT / "service" / "impl" / "GraphQueryServiceImpl.java").read_text(encoding="utf-8")

    for parameter in (
        "allowedPathIds",
        "allowedDeviceIds",
        "allowedComponentIds",
        "allowedFaultIds",
    ):
        assert f'params.put("{parameter}"' in source
    assert '"kgpath:" + vo.getDeviceId()' in source
    assert "vo.setNodeIds(" in source
    assert "vo.setRelationshipTypes(" in source
    assert "vo.setSourceChunkUids(" in source
    assert "vo.setProvenanceStatus(" in source


def test_java_graph_query_prefers_fault_row_provenance_for_diagnostic_paths() -> None:
    source = (JAVA_ROOT / "service" / "impl" / "GraphQueryServiceImpl.java").read_text(encoding="utf-8")

    assert "selectPathSourceChunkUids(\n                !candidate.getFaultId().isBlank()," in source
    assert "selectPathSourceChunkUids(\n                hasText(vo.getFaultId())," in source
    assert "coalesce(f.page_start, c.page_start, d.page_start) AS pageStart" in source
    assert "coalesce(f.page_end, c.page_end, d.page_end) AS pageEnd" in source


def test_java_graph_query_derives_revision_from_manual_document_version() -> None:
    source = (JAVA_ROOT / "service" / "impl" / "GraphQueryServiceImpl.java").read_text(encoding="utf-8")

    assert "CASE WHEN coalesce(c.document_id, d.document_id, f.document_id) IS NULL" in source
    assert "'manual:' + coalesce(c.document_id, d.document_id, f.document_id)" in source
    assert "coalesce(c.document_version, d.document_version, f.document_version)" in source
    assert "END) AS graphRevision" in source


def test_java_candidate_dependency_failures_are_not_collapsed_to_empty() -> None:
    source = (JAVA_ROOT / "service" / "impl" / "GraphQueryServiceImpl.java").read_text(encoding="utf-8")

    assert 'throw new IllegalStateException("graph_candidate_recall_unavailable"' in source
    assert 'throw new IllegalStateException("graph_candidate_query_unavailable"' in source


def test_java_candidate_filters_rows_after_optional_fault_match() -> None:
    source = (JAVA_ROOT / "service" / "impl" / "GraphQueryServiceImpl.java").read_text(encoding="utf-8")

    assert "OPTIONAL MATCH (c)-[:CAUSES]->(f:Fault)\n                WITH d, c, f\n                WHERE" in source


def test_java_embedding_client_uses_system_dns_resolver() -> None:
    source = (JAVA_ROOT / "utils" / "EmbeddingUtils.java").read_text(encoding="utf-8")

    assert "DefaultAddressResolverGroup.INSTANCE" in source
    assert ".resolver(" in source
