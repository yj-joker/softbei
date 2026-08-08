from pathlib import Path


def test_manual_fault_solution_upsert_persists_provenance_and_embedding_status():
    root = Path(__file__).resolve().parents[2]
    source = (
        root
        / "weixiu/src/main/java/ai/weixiu/controller/ManualKGInternalController.java"
    ).read_text(encoding="utf-8")

    for token in (
        "documentVersion",
        "sectionId",
        "sourceChunkUids",
        "pageStart",
        "pageEnd",
        "document_version",
        "section_id",
        "source_chunk_uids",
        "page_start",
        "page_end",
        "embeddingStatus",
    ):
        assert token in source
    assert "coalesce(f.source_chunk_uids, [])" in source
    assert "coalesce(s.source_chunk_uids, [])" in source
    assert "SET f.embedding" in source


def test_manual_fault_solution_upsert_uses_stable_source_identity_keys():
    root = Path(__file__).resolve().parents[2]
    source = (
        root
        / "weixiu/src/main/java/ai/weixiu/controller/ManualKGInternalController.java"
    ).read_text(encoding="utf-8")

    for token in (
        "faultIdentityKey",
        "solutionIdentityKey",
        "identity_key: $faultIdentityKey",
        "identity_key: $solutionIdentityKey",
    ):
        assert token in source
    assert "MERGE (f:Fault {name: $faultName})" not in source
    assert "MERGE (s:Solution {title: $solutionTitle})" not in source
    assert "f.name              = $faultName" in source
    assert "s.title             = $solutionTitle" in source


def test_delete_by_manual_only_deletes_nodes_owned_before_the_call():
    root = Path(__file__).resolve().parents[2]
    source = (
        root
        / "weixiu/src/main/java/ai/weixiu/controller/ManualKGInternalController.java"
    ).read_text(encoding="utf-8")
    method = source.split("deleteByManual", 1)[1].split("private static String asText", 1)[0]

    assert "WITH collect(n) AS candidateNodes" in method
    assert "UNWIND candidateNodes AS n" in method
    assert method.count("                    MATCH (n)\n") == 1
