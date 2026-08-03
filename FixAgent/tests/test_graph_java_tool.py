"""Java 图谱工具对后端空结果契约的回归测试。"""

from __future__ import annotations

import asyncio

from tools import graph_java_tool


def test_null_java_data_is_treated_as_an_empty_graph_result(monkeypatch) -> None:
    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"code": 200, "message": "success", "data": None}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, *args, **kwargs):
            return _Response()

    monkeypatch.setattr(
        graph_java_tool.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _Client(),
    )

    result = asyncio.run(
        graph_java_tool.JavaGraphDiagnosisPathTool()._execute(
            fault_description="发动机异响",
        )
    )

    assert result["paths_found"] == 0
    assert result["cases_found"] == 0
    assert result["evidence_status"] == "empty"


def test_null_java_data_is_treated_as_an_empty_device_search(monkeypatch) -> None:
    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"code": 200, "message": "success", "data": None}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, *args, **kwargs):
            return _Response()

    monkeypatch.setattr(
        graph_java_tool.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _Client(),
    )

    result = asyncio.run(
        graph_java_tool.JavaGraphDeviceSearchTool()._execute(
            keyword="发动机",
        )
    )

    assert result == {"count": 0, "devices": []}
