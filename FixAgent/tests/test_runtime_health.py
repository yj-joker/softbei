from __future__ import annotations

import asyncio

from api import main


class _VectorService:
    def list_all_manifests(self):
        return [
            {
                "document_id": "kdoc_b",
                "status": "ready",
                "index_revision": 4,
            },
            {
                "document_id": "kdoc_a",
                "status": "ready",
                "index_revision": 7,
            },
            {
                "document_id": "kdoc_pending",
                "status": "processing",
                "index_revision": 9,
            },
        ]


def test_health_exposes_running_commit_worktree_and_ready_document_revisions(
    monkeypatch,
) -> None:
    monkeypatch.setenv("FIXAGENT_GIT_COMMIT", "abc123")
    monkeypatch.setenv("FIXAGENT_GIT_DIRTY", "true")
    monkeypatch.setenv("FIXAGENT_WORKTREE", "C:/workspace/rag-quality-v2")
    monkeypatch.setattr(main, "get_vector_service", lambda: _VectorService())

    public_payload = asyncio.run(main.health())
    payload = asyncio.run(main.runtime_info())

    assert public_payload == {"status": "ok", "build_id": "abc123-dirty"}
    assert "runtime" not in public_payload
    assert payload["status"] == "ok"
    assert payload["runtime"]["git_commit"] == "abc123"
    assert payload["runtime"]["dirty"] is True
    assert payload["runtime"]["worktree"] == "C:/workspace/rag-quality-v2"
    assert payload["runtime"]["documents"] == [
        {"document_id": "kdoc_a", "index_revision": 7, "status": "ready"},
        {"document_id": "kdoc_b", "index_revision": 4, "status": "ready"},
    ]
    assert any(route.path == "/health" for route in main.app.routes)
    assert any(route.path == "/ai/runtime" for route in main.app.routes)


def test_health_degrades_without_vector_catalog_but_keeps_version_identity(
    monkeypatch,
) -> None:
    monkeypatch.setenv("FIXAGENT_GIT_COMMIT", "abc123")
    monkeypatch.setenv("FIXAGENT_GIT_DIRTY", "false")
    monkeypatch.setenv("FIXAGENT_WORKTREE", "C:/workspace/rag-quality-v2")

    def unavailable_vector_service():
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(main, "get_vector_service", unavailable_vector_service)

    public_payload = asyncio.run(main.health())
    payload = asyncio.run(main.runtime_info())

    assert public_payload == {"status": "degraded", "build_id": "abc123"}
    assert payload["status"] == "degraded"
    assert payload["runtime"]["git_commit"] == "abc123"
    assert payload["runtime"]["documents"] == []
    assert payload["runtime"]["catalog_available"] is False
