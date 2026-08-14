from evaluation.migrate_graph_stable_identity import (
    APPLY_PATHS_QUERY,
    APPLY_SOLUTIONS_QUERY,
    build_stable_identity_rows,
)


def test_stable_identity_rows_ignore_legacy_neo4j_uuids() -> None:
    first = {
        "device_id": "uuid-device-a",
        "device_name": "motorcycle engine",
        "device_model": "",
        "device_manufacturer": "",
        "component_id": "uuid-component-a",
        "component_name": "oil pump",
        "component_type": "",
        "component_specification": "",
        "fault_id": "uuid-fault-a",
        "fault_name": "oil pump damaged",
        "fault_description": "replace the damaged pump",
        "solution_id": "uuid-solution-a",
        "solution_title": "replace oil pump",
        "solution_description": "replace the damaged pump",
    }
    second = {**first, "device_id": "uuid-device-b", "component_id": "uuid-component-b",
              "fault_id": "uuid-fault-b", "solution_id": "uuid-solution-b"}

    first_row = build_stable_identity_rows([first], document_id="manual-1", document_version="v1")[0]
    second_row = build_stable_identity_rows([second], document_id="manual-1", document_version="v1")[0]

    assert first_row["path_stable_id"] == second_row["path_stable_id"]
    assert first_row["device_stable_id"] == second_row["device_stable_id"]
    assert first_row["component_stable_id"] == second_row["component_stable_id"]
    assert first_row["fault_stable_id"] == second_row["fault_stable_id"]
    assert first_row["graph_revision"] == "manual:manual-1:v1:stable-v1"


def test_migration_updates_paths_and_solutions_in_separate_cypher_statements() -> None:
    assert "FOREACH" not in APPLY_PATHS_QUERY
    assert "MATCH (s:Solution" not in APPLY_PATHS_QUERY
    assert "MATCH (s:Solution" in APPLY_SOLUTIONS_QUERY
    assert "WHERE row.solution_id <> ''" in APPLY_SOLUTIONS_QUERY
