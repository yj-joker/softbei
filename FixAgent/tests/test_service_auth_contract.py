import asyncio
from types import SimpleNamespace

import httpx
import pytest

from config import settings as settings_module
from guardrails import review_agent
from guardrails.review_agent import _GraphCheck
from services.knowledge import manual_kg_extractor as kg


class _Response:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data if data is not None else {"data": True}

    def json(self):
        return self._data


class _PostClient:
    def __init__(self, response):
        self.response = response
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def post(self, url, *, json, headers):
        self.requests.append((url, json, headers))
        return self.response


class _GraphClient:
    def __init__(self, response_factory):
        self.response_factory = response_factory
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def get(self, url, *, params, headers):
        self.requests.append((url, params, headers))
        return self.response_factory(url)


def _extractor_for_java_call():
    extractor = object.__new__(kg.ManualKGExtractor)
    extractor._base_url = "http://java.test"
    extractor._token = "internal-token-B"
    return extractor


def test_call_java_sends_only_internal_header_and_preserves_403_context(monkeypatch):
    client = _PostClient(_Response(status_code=403, data={"detail": "forbidden"}))
    monkeypatch.setattr(kg.httpx, "AsyncClient", lambda timeout: client)

    with pytest.raises(kg.JavaApiError) as raised:
        asyncio.run(
            _extractor_for_java_call()._call_java(
                "/weixiu/kg/internal/upsert-device", {"name": "测试设备"}
            )
        )

    assert client.requests[0][2] == {"X-Internal-Token": "internal-token-B"}
    assert "path=/weixiu/kg/internal/upsert-device" in str(raised.value)
    assert "status=403" in str(raised.value)
    assert "internal-token-B" not in str(raised.value)


def test_graph_check_sends_internal_header_to_both_queries(monkeypatch):
    client = _GraphClient(lambda _url: _Response(status_code=200, data={"data": True}))
    monkeypatch.setattr(review_agent, "httpx", httpx, raising=False)
    monkeypatch.setattr(review_agent.httpx, "AsyncClient", lambda timeout: client)
    monkeypatch.setattr(
        settings_module,
        "get_settings",
        lambda: SimpleNamespace(
            java_service_url="http://java.test", internal_token="internal-token-B"
        ),
    )

    result = asyncio.run(
        _GraphCheck.run("1. 燃油系统故障：建议更换燃油滤芯并检查油路", react_trace=[])
    )

    assert result["verified_count"] == 1
    assert [request[0] for request in client.requests] == [
        "http://java.test/weixiu/path/fault-exists",
        "http://java.test/weixiu/path/solution-exists",
    ]
    assert all(
        request[2] == {"X-Internal-Token": "internal-token-B"}
        for request in client.requests
    )


def test_graph_check_does_not_treat_non_200_as_missing_node(monkeypatch):
    client = _GraphClient(lambda _url: _Response(status_code=503, data={"data": False}))
    monkeypatch.setattr(review_agent, "httpx", httpx, raising=False)
    monkeypatch.setattr(review_agent.httpx, "AsyncClient", lambda timeout: client)
    monkeypatch.setattr(
        settings_module,
        "get_settings",
        lambda: SimpleNamespace(
            java_service_url="http://java.test", internal_token="internal-token-B"
        ),
    )

    result = asyncio.run(
        _GraphCheck.run("1. 燃油系统故障：建议更换燃油滤芯并检查油路", react_trace=[])
    )

    assert result["verified_count"] == 0
    assert result["unverified_count"] == 1
    reason = result["unverified_paths"][0]["reason"]
    assert "查询失败" in reason
    assert "不在图谱中" not in reason
    assert "HTTP 503" in reason
