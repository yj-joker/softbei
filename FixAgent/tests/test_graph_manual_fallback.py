from __future__ import annotations

import asyncio
from types import SimpleNamespace

from tools.knowledge_retrieval_tool import KnowledgeRetrievalTool


def _item(item_id: str, qualification: str):
    return SimpleNamespace(
        id=item_id,
        score=0.9,
        content=item_id,
        metadata={"qualification": qualification},
    )


def test_graph_scoped_empty_result_retries_with_controlled_manual_scope(monkeypatch) -> None:
    tool = KnowledgeRetrievalTool()
    calls = []

    async def execute(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return []
        return [_item("manual-qualified", "qualified")]

    monkeypatch.setattr(tool, "_execute", execute)

    result = asyncio.run(tool.run(
        query="bearing noise",
        top_k=5,
        document_id="manual-1",
        document_version="v1",
        parent_section_id="section-1",
        allowed_section_ids=["section-1"],
        allowed_source_chunk_uids=["chunk-1"],
        pages=[12],
        _allow_graph_scope_fallback=True,
        _graph_failure_reason="empty_graph_evidence",
    ))

    assert result.success is True
    assert [item.id for item in result.data] == ["manual-qualified"]
    assert len(calls) == 2
    assert calls[0]["allowed_source_chunk_uids"] == ["chunk-1"]
    assert "allowed_source_chunk_uids" not in calls[1]
    assert "pages" not in calls[1]
    assert calls[1]["document_id"] == "manual-1"
    assert "parent_section_id" not in calls[1]
    assert "allowed_section_ids" not in calls[1]
    assert result.data[0].metadata["manual_fallback_reason"] == "graph_failed_manual_fallback"
    assert result.data[0].metadata["graph_failure_reason"] == "empty_graph_evidence"


def test_graph_scoped_reference_only_result_retries_manual_rag(monkeypatch) -> None:
    tool = KnowledgeRetrievalTool()
    calls = []

    async def execute(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return [_item("graph-seed-reference", "reference")]
        return [_item("manual-qualified", "qualified")]

    monkeypatch.setattr(tool, "_execute", execute)

    result = asyncio.run(tool.run(
        query="bearing noise",
        document_id="manual-1",
        allowed_source_chunk_uids=["chunk-1"],
        _allow_graph_scope_fallback=True,
    ))

    assert len(calls) == 2
    assert [item.id for item in result.data] == ["manual-qualified"]


def test_graph_scoped_qualified_seed_still_supplements_manual_solution(monkeypatch) -> None:
    tool = KnowledgeRetrievalTool()
    calls = []

    async def execute(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return [_item("graph-seed-qualified", "qualified")]
        return [_item("manual-solution-qualified", "qualified")]

    monkeypatch.setattr(tool, "_execute", execute)

    result = asyncio.run(tool.run(
        query="starter motor stuck repair",
        document_id="manual-1",
        allowed_section_ids=["wrong-section"],
        allowed_source_chunk_uids=["wrong-chunk"],
        _allow_graph_scope_fallback=True,
    ))

    assert len(calls) == 2
    assert "allowed_section_ids" not in calls[1]
    assert "allowed_source_chunk_uids" not in calls[1]
    assert [item.id for item in result.data] == ["manual-solution-qualified"]


def test_failed_controlled_fallback_preserves_first_result(monkeypatch) -> None:
    tool = KnowledgeRetrievalTool()
    first = [_item("graph-seed-reference", "reference")]
    calls = []

    async def execute(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return first
        raise RuntimeError("manual fallback unavailable")

    monkeypatch.setattr(tool, "_execute", execute)

    result = asyncio.run(tool.run(
        query="bearing noise",
        document_id="manual-1",
        allowed_source_chunk_uids=["chunk-1"],
        _allow_graph_scope_fallback=True,
    ))

    assert result.success is True
    assert result.data == first
    assert len(calls) == 2


def test_plain_rag_never_runs_graph_scope_fallback(monkeypatch) -> None:
    tool = KnowledgeRetrievalTool()
    calls = []

    async def execute(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(tool, "_execute", execute)

    result = asyncio.run(tool.run(query="bearing noise", document_id="manual-1"))

    assert result.success is True
    assert result.data == []
    assert len(calls) == 1
