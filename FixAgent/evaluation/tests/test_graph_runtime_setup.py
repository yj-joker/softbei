from __future__ import annotations

from typing import Any

from embeddings.constants import EMBEDDING_DIMENSIONS
from evaluation import graph_runtime_setup


class _Result:
    def __init__(self, rows: list[dict[str, Any]] | None = None):
        self._rows = rows or []

    def __iter__(self):
        return iter(self._rows)

    def single(self):
        return self._rows[0] if self._rows else None

    def data(self):
        return list(self._rows)

    def consume(self):
        return None


class _Session:
    def __init__(self):
        self.vector_create_dimensions: list[int] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def run(self, query: str, **parameters: Any):
        if "size(n.embedding) AS dimensions" in query:
            return _Result([{"dimensions": 1536}])
        if query.startswith("SHOW INDEXES YIELD name, type, options"):
            return _Result([
                {
                    "name": name,
                    "type": "VECTOR",
                    "options": {"indexConfig": {"vector.dimensions": 1536}},
                }
                for name, *_rest in graph_runtime_setup.VECTOR_INDEX_DEFINITIONS
            ])
        if query.startswith("CREATE VECTOR INDEX"):
            self.vector_create_dimensions.append(int(parameters["dimensions"]))
        return _Result()


class _Driver:
    def __init__(self, session: _Session):
        self._session = session

    def session(self, **_kwargs):
        return self._session

    def close(self):
        return None


def test_target_dimension_is_shared_1024_contract() -> None:
    assert graph_runtime_setup.target_embedding_dimensions() == EMBEDDING_DIMENSIONS == 1024


def test_ensure_indexes_ignores_legacy_1536_node_dimensions(monkeypatch) -> None:
    session = _Session()
    monkeypatch.setattr(graph_runtime_setup, "_driver", lambda: _Driver(session))

    graph_runtime_setup.ensure_indexes()

    assert session.vector_create_dimensions == [1024, 1024, 1024, 1024]


def test_static_index_definitions_are_all_1024_dimensions() -> None:
    vector_statements = graph_runtime_setup.INDEX_STATEMENTS[:4]

    assert len(vector_statements) == 4
    assert all("`vector.dimensions`: 1024" in statement for statement in vector_statements)
