from evaluation import repair_graph_legacy_artifacts as migration


def test_legacy_device_queries_target_only_manual_devices_without_fault_paths():
    expected_identity_markers = (
        (migration.COUNT_QUERY, "d.source = 'manual' OR size(coalesce(d.manual_ids, [])) > 0"),
        (
            migration.COPY_EMBEDDINGS_QUERY,
            "oldD.source = 'manual' OR size(coalesce(oldD.manual_ids, [])) > 0",
        ),
        (
            migration.DELETE_LEGACY_QUERY,
            "d.source = 'manual' OR size(coalesce(d.manual_ids, [])) > 0",
        ),
    )
    for query, identity_marker in expected_identity_markers:
        assert identity_marker in query
        assert "size(coalesce(d.manual_ids, [])) = 0" not in query
        assert "size(coalesce(oldD.manual_ids, [])) = 0" not in query
        assert "[:CAUSES]->(:Fault)" in query


def test_legacy_deletion_protects_fault_verified_and_task_components():
    query = migration.DELETE_LEGACY_QUERY

    assert "NOT (c)-[:CAUSES]->(:Fault)" in query
    assert "c.source = 'manual'" in query
    assert "coalesce(c.verified, false) = false" in query
    assert "c.source_task_id IS NULL" in query
