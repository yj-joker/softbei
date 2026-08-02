import asyncio
from types import SimpleNamespace

import httpx
import pytest

from api import main as api_main
from services.knowledge import manual_kg_extractor as kg


def _extractor_with_result(result):
    extractor = object.__new__(kg.ManualKGExtractor)
    extractor.vector_svc = SimpleNamespace(
        list_document_chunks=lambda _document_id: [
            {"metadata": {"section_title": "气缸盖", "chunk_label": "text"}, "text": "内容"}
        ],
        get_document_manifest=lambda _document_id: {},
    )
    extractor.settings = SimpleNamespace(intent_router_model="test-model")
    extractor.llm = SimpleNamespace()
    extractor._base_url = "http://java.test"
    extractor._token = "internal-token-B"
    extractor._identify_device = _identify_device
    extractor._call_java = _call_java_factory(result)
    extractor._extract_component = _extract_component
    extractor._extract_fault_solutions = _extract_fault_solutions
    return extractor


async def _identify_device(*_args, **_kwargs):
    return kg.ExtractedDevice(name="测试设备")


async def _extract_component(*_args, **_kwargs):
    return kg.ExtractedComponent(name="气缸盖")


async def _extract_fault_solutions(*_args, **_kwargs):
    return []


def _call_java_factory(result):
    async def _call_java(path, body):
        if path.endswith("upsert-device"):
            return {"deviceId": "device-1"}
        if path.endswith("upsert-component"):
            return {"componentId": "component-1"}
        return result

    return _call_java


def _valid_chunks():
    return [
        {
            "metadata": {
                "section_title": f"部件{i}",
                "chunk_label": "text",
                "chunk_uid": f"chunk-{i}",
            },
            "text": "内容",
        }
        for i in range(1, 5)
    ]


@pytest.mark.parametrize(
    "status_code, expected_message",
    [(403, "status=403"), (500, "status=500")],
)
def test_call_java_http_error_keeps_path_and_status_without_token(monkeypatch, status_code, expected_message):
    class Response:
        def __init__(self):
            self.status_code = status_code

        def raise_for_status(self):
            request = SimpleNamespace(url="http://java.test/weixiu/kg/internal/upsert-device")
            response = SimpleNamespace(status_code=status_code, request=request)
            raise httpx.HTTPStatusError("forbidden", request=request, response=response)

        def json(self):
            return {}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, *args, **kwargs):
            assert kwargs["headers"] == {"X-Internal-Token": "internal-token-B"}
            return Response()

    monkeypatch.setattr(kg.httpx, "AsyncClient", lambda timeout: Client())
    extractor = _extractor_with_result({})
    del extractor._call_java
    with pytest.raises(kg.JavaApiError) as raised:
        asyncio.run(
            extractor._call_java(
                "/weixiu/kg/internal/upsert-device", {"name": "测试设备"}
            )
        )
    assert expected_message in str(raised.value)
    assert "/weixiu/kg/internal/upsert-device" in str(raised.value)
    assert "internal-token-B" not in str(raised.value)


def test_section_callback_exception_enters_result_errors(monkeypatch):
    extractor = _extractor_for_section_error()
    monkeypatch.setattr(kg, "assess_section_structure", lambda _chunks: {"ok": True, "reason": "", "stats": {}})

    result = asyncio.run(extractor.extract_document("doc-1", device_type_hint="测试设备"))

    assert result.errors
    assert any("upsert-component" in error for error in result.errors)
    assert any("status=503" in error for error in result.errors)


def test_manual_kg_api_returns_failure_when_extraction_has_errors(monkeypatch):
    class Request:
        async def json(self):
            return {"document_id": "doc-1"}

    result = kg.ExtractionResult(document_id="doc-1", errors=["path=/weixiu/kg status=503"])
    monkeypatch.setattr(
        "services.knowledge.manual_kg_extractor.get_manual_kg_extractor",
        lambda: SimpleNamespace(extract_document=lambda *args, **kwargs: _resolved(result)),
    )

    response = asyncio.run(api_main.manual_kg_extract(Request()))

    assert response["success"] is False
    assert response["message"] != "操作成功"
    assert response["data"]["errors"] == ["path=/weixiu/kg status=503"]


def test_manual_kg_api_returns_success_when_extraction_is_skipped_without_errors(monkeypatch):
    class Request:
        async def json(self):
            return {"document_id": "doc-1"}

    result = kg.ExtractionResult(document_id="doc-1", skipped=True, skip_reason="结构不完整")
    monkeypatch.setattr(
        "services.knowledge.manual_kg_extractor.get_manual_kg_extractor",
        lambda: SimpleNamespace(extract_document=lambda *args, **kwargs: _resolved(result)),
    )

    response = asyncio.run(api_main.manual_kg_extract(Request()))

    assert response["success"] is True
    assert response["message"] == "操作成功"


async def _resolved(value):
    return value


def _extractor_for_section_error():
    extractor = object.__new__(kg.ManualKGExtractor)
    extractor.vector_svc = SimpleNamespace(
        list_document_chunks=lambda _document_id: _valid_chunks(),
        get_document_manifest=lambda _document_id: {},
    )
    extractor.settings = SimpleNamespace(intent_router_model="test-model")
    extractor.llm = SimpleNamespace()
    extractor._base_url = "http://java.test"
    extractor._token = "internal-token-B"
    extractor._identify_device = _identify_device
    extractor._extract_component = _extract_component_error
    extractor._extract_fault_solutions = _extract_fault_solutions
    extractor._call_java = _call_java_for_section_error
    return extractor


async def _extract_component_error(*_args, **_kwargs):
    return kg.ExtractedComponent(name="气缸盖")


async def _call_java_for_section_error(path, body):
    if path.endswith("upsert-device"):
        return {"deviceId": "device-1"}
    raise kg.JavaApiError(f"Java API request failed: path={path} status=503")
