from __future__ import annotations

import asyncio

import pytest

from evaluation.migrate_graph_embeddings_1024 import (
    apply_migration_rows,
    build_node_text,
    generate_migration_rows,
    validate_migration_rows,
)


class _Embedder:
    def __init__(self, fill: float):
        self.fill = fill
        self.calls: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        return [self.fill] * 1024


class _WriteResult:
    def __init__(self, updated: int):
        self.updated = updated

    def single(self):
        return {"updated": self.updated}


class _Transaction:
    def __init__(self, updated: int):
        self.updated = updated
        self.parameters = None

    def run(self, _query: str, **parameters):
        self.parameters = parameters
        return _WriteResult(self.updated)


class _WriteSession:
    def __init__(self, updated: int):
        self.transaction = _Transaction(updated)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute_write(self, callback):
        return callback(self.transaction)


class _WriteDriver:
    def __init__(self, updated: int):
        self.session_instance = _WriteSession(updated)

    def session(self, **_kwargs):
        return self.session_instance


def test_build_node_text_matches_java_manual_graph_upsert_format() -> None:
    assert build_node_text({
        "label": "Component",
        "name": "火花塞",
        "specification": "标准件",
    }) == "部件名称：火花塞\n规格参数：标准件"
    assert build_node_text({
        "label": "Fault",
        "name": "火花塞损坏",
        "description": "中心电极变形",
    }) == "故障名称：火花塞损坏\n故障描述：中心电极变形"


def test_validate_rows_rejects_any_non_1024_vector() -> None:
    rows = [{
        "id": "fault-1",
        "embedding": [0.0] * 1024,
        "multimodal_embedding": [0.0] * 1536,
    }]

    with pytest.raises(ValueError, match="fault-1.*multimodal_embedding.*1536"):
        validate_migration_rows(rows)


def test_generate_rows_validates_both_embedding_spaces_before_write() -> None:
    nodes = [
        {
            "label": "Component",
            "id": "component-1",
            "name": "火花塞",
            "specification": "标准件",
        },
        {
            "label": "Fault",
            "id": "fault-1",
            "name": "火花塞损坏",
            "description": "中心电极变形",
        },
    ]
    text_embedder = _Embedder(0.25)
    multimodal_embedder = _Embedder(0.75)

    rows = asyncio.run(generate_migration_rows(
        nodes,
        text_embedder=text_embedder,
        multimodal_embedder=multimodal_embedder,
        concurrency=2,
    ))

    assert [row["id"] for row in rows] == ["component-1", "fault-1"]
    assert all(len(row["embedding"]) == 1024 for row in rows)
    assert all(len(row["multimodal_embedding"]) == 1024 for row in rows)
    assert text_embedder.calls == [
        "部件名称：火花塞\n规格参数：标准件",
        "故障名称：火花塞损坏\n故障描述：中心电极变形",
    ]
    assert multimodal_embedder.calls == text_embedder.calls


def test_validate_rows_rejects_duplicate_node_ids() -> None:
    valid = {
        "id": "component-1",
        "embedding": [0.0] * 1024,
        "multimodal_embedding": [0.0] * 1024,
    }

    with pytest.raises(ValueError, match="duplicate.*component-1"):
        validate_migration_rows([valid, dict(valid)])


def test_apply_rows_requires_every_expected_node_to_be_updated() -> None:
    rows = [{
        "id": "component-1",
        "embedding": [0.0] * 1024,
        "multimodal_embedding": [1.0] * 1024,
    }]
    driver = _WriteDriver(updated=0)

    with pytest.raises(RuntimeError, match="updated 0 nodes, expected 1"):
        apply_migration_rows(driver, rows, "document-1")


def test_apply_rows_uses_one_transaction_with_validated_1024_vectors() -> None:
    rows = [{
        "id": "component-1",
        "embedding": [0.0] * 1024,
        "multimodal_embedding": [1.0] * 1024,
    }]
    driver = _WriteDriver(updated=1)

    updated = apply_migration_rows(driver, rows, "document-1")

    assert updated == 1
    parameters = driver.session_instance.transaction.parameters
    assert parameters["document_id"] == "document-1"
    assert parameters["dimensions"] == 1024
    assert parameters["rows"] == rows
