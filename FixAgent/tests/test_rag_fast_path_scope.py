"""Fast-path scope regressions."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from agents.base_agent import AgentInput
from api import main
from schemas.request import AgentMode, ChatRequest


class _FakeRetrievalTool:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(success=False, data=[], error=None)


def test_rag_fast_path_uses_final_hard_scope(monkeypatch) -> None:
    tool = _FakeRetrievalTool()
    monkeypatch.setattr(main, "get_knowledge_retrieval_tool", lambda: tool)
    request = ChatRequest(
        session_id="scope-fast-path",
        message="根据手册说明火花塞",
        mode=AgentMode.RETRIEVAL,
    )
    input_data = AgentInput(
        user_message=request.message,
        session_id=request.session_id,
        context={
            "retrieval_scope": {
                "document_id": "manual-motorcycle",
                "device_type": "motorcycle-engine",
            }
        },
    )

    result = asyncio.run(main._run_rag_fast_path(request, input_data))

    assert result is None
    assert tool.calls == [
        {
            "query": request.message,
            "top_k": 5,
            "document_id": "manual-motorcycle",
            "device_type": "motorcycle-engine",
        }
    ]


def test_rag_fast_path_propagates_complete_authoritative_scope(monkeypatch) -> None:
    tool = _FakeRetrievalTool()
    monkeypatch.setattr(main, "get_knowledge_retrieval_tool", lambda: tool)
    request = ChatRequest(
        session_id="scope-fast-path-complete",
        message="保险熔断时如何处理",
        mode=AgentMode.RETRIEVAL,
    )
    input_data = AgentInput(
        user_message=request.message,
        session_id=request.session_id,
        context={
            "retrieval_scope": {
                "server_authoritative": True,
                "scope_fingerprint": "manual-scope:test",
                "document_id": "manual-1",
                "document_version": "v1",
                "device_type": "",
                "parent_section_id": "section-1",
                "allowed_section_ids": ["section-1"],
                "allowed_evidence_refs": ["row-1"],
                "allowed_source_chunk_uids": ["chunk-uid-1"],
                "pages": [12],
            }
        },
    )

    result = asyncio.run(main._run_rag_fast_path(request, input_data))

    assert result is None
    assert tool.calls == [{
        "query": request.message,
        "top_k": 5,
        "document_id": "manual-1",
        "document_version": "v1",
        "parent_section_id": "section-1",
        "allowed_section_ids": ["section-1"],
        "allowed_evidence_refs": ["row-1"],
        "allowed_source_chunk_uids": ["chunk-uid-1"],
        "pages": [12],
    }]

