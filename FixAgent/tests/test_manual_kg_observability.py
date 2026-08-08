import asyncio
from types import SimpleNamespace

import httpx
import pytest

from api import main as api_main
from services.knowledge import manual_kg_extractor as kg


class _Response:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data if data is not None else {}

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


@pytest.mark.parametrize(
    "envelope, expected_code",
    [
        ({"code": "500", "message": "upsert failed", "data": None}, "code=500"),
        ({"code": "200", "success": False, "message": "business failed", "data": None}, "code=200"),
    ],
)
def test_call_java_rejects_http_200_business_failure_without_token(monkeypatch, envelope, expected_code):
    client = _PostClient(_Response(status_code=200, data=envelope))
    monkeypatch.setattr(kg.httpx, "AsyncClient", lambda timeout: client)
    extractor = _extractor_with_result({})
    del extractor._call_java

    with pytest.raises(kg.JavaApiError) as raised:
        asyncio.run(
            extractor._call_java(
                "/weixiu/kg/internal/upsert-device", {"name": "测试设备"}
            )
        )

    assert "/weixiu/kg/internal/upsert-device" in str(raised.value)
    assert expected_code in str(raised.value)
    assert "internal-token-B" not in str(raised.value)


def _failure_chunks(path):
    label = "troubleshooting" if path.endswith("upsert-fault-solution") else "text"
    return [
        {
            "metadata": {
                "section_title": "部件1",
                "chunk_label": label,
                "chunk_uid": "chunk-1",
                "raw_text": "气缸盖：故障排查内容" if label == "troubleshooting" else "步骤内容",
            },
            "text": "气缸盖：故障排查内容" if label == "troubleshooting" else "步骤内容",
        }
    ]


@pytest.mark.parametrize(
    "failure_path",
    [
        "/weixiu/kg/internal/upsert-device",
        "/weixiu/kg/internal/upsert-component",
        "/weixiu/kg/internal/upsert-fault-solution",
    ],
)
def test_each_java_callback_business_failure_enters_result_errors(monkeypatch, failure_path):
    extractor = object.__new__(kg.ManualKGExtractor)
    extractor.vector_svc = SimpleNamespace(
        list_document_chunks=lambda _document_id: _failure_chunks(failure_path),
        get_document_manifest=lambda _document_id: {},
    )
    extractor.settings = SimpleNamespace(intent_router_model="test-model")
    extractor.llm = SimpleNamespace()
    extractor._base_url = "http://java.test"
    extractor._token = "internal-token-B"
    extractor._identify_device = _identify_device
    extractor._extract_component = _extract_component
    extractor._extract_fault_solutions = _extract_fault_solution_item

    async def call_java(path, body):
        if path == failure_path:
            raise kg.JavaApiError(path, status_code=200, business_code="500")
        if path.endswith("upsert-device"):
            return {"deviceId": "device-1"}
        if path.endswith("upsert-component"):
            return {"componentId": "component-1"}
        return {"faultId": "fault-1", "solutionId": "solution-1"}

    extractor._call_java = call_java
    monkeypatch.setattr(kg, "assess_section_structure", lambda _chunks: {"ok": True, "reason": "", "stats": {}})

    result = asyncio.run(extractor.extract_document("doc-1", device_type_hint="测试设备"))

    assert result.errors
    assert any(failure_path in error and "code=500" in error for error in result.errors)


def test_step_chunks_do_not_create_manual_procedure_nodes(monkeypatch):
    calls = []
    extractor = object.__new__(kg.ManualKGExtractor)
    extractor.vector_svc = SimpleNamespace(
        list_document_chunks=lambda _document_id: [
            {
                "metadata": {
                    "section_title": "拆卸气缸盖",
                    "chunk_label": "step",
                    "chunk_uid": "step-1",
                    "raw_text": "1. 拆卸气缸盖螺栓",
                },
                "text": "1. 拆卸气缸盖螺栓",
            }
        ],
        get_document_manifest=lambda _document_id: {},
    )
    extractor.settings = SimpleNamespace(intent_router_model="test-model")
    extractor.llm = SimpleNamespace()
    extractor._base_url = "http://java.test"
    extractor._token = "internal-token-B"
    extractor._identify_device = _identify_device
    extractor._extract_component = _extract_component
    extractor._extract_fault_solutions = _extract_fault_solutions

    async def call_java(path, body):
        calls.append(path)
        if path.endswith("upsert-device"):
            return {"deviceId": "device-1"}
        if path.endswith("upsert-component"):
            return {"componentId": "component-1"}
        raise AssertionError(f"unexpected Java callback: {path}")

    extractor._call_java = call_java
    monkeypatch.setattr(kg, "assess_section_structure", lambda _chunks: {"ok": True, "reason": "", "stats": {}})

    result = asyncio.run(extractor.extract_document("doc-1", device_type_hint="测试设备"))

    assert not result.errors
    assert result.procedures_created == 0
    assert "/weixiu/kg/internal/upsert-procedure" not in calls
    assert calls == [
        "/weixiu/kg/internal/upsert-device",
        "/weixiu/kg/internal/upsert-component",
    ]


def test_section_callback_exception_enters_result_errors(monkeypatch):
    extractor = _extractor_for_section_error()
    monkeypatch.setattr(kg, "assess_section_structure", lambda _chunks: {"ok": True, "reason": "", "stats": {}})

    result = asyncio.run(extractor.extract_document("doc-1", device_type_hint="测试设备"))

    assert result.errors
    assert any("upsert-component" in error for error in result.errors)
    assert any("status=503" in error for error in result.errors)


async def _extract_fault_solution_item(*_args, **_kwargs):
    return [
        kg.ExtractedFaultSolution(
            fault_name="测试故障",
            fault_description="故障描述",
            solution_title="测试方案",
            solution_description="方案描述",
            solution_steps=["步骤一"],
            confidence=0.9,
            source_chunk_uid="chunk-1",
            source_subject="气缸盖",
            component_name="气缸盖",
        )
    ]


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


def test_extract_document_sends_document_scoped_provenance(monkeypatch):
    calls = []
    extractor = _extractor_with_result({})
    extractor.vector_svc = SimpleNamespace(
        list_document_chunks=lambda _document_id: [
            {
                "metadata": {
                    "section_title": "6.2 离合器装配",
                    "parent_section_id": "6.2",
                    "chunk_label": "text",
                    "chunk_uid": "chunk-23",
                    "page_number": 23,
                },
                "text": "内容",
            }
        ],
        get_document_manifest=lambda _document_id: {
            "document_version": "batch-7",
        },
    )

    async def call_java(path, body):
        calls.append((path, body))
        if path.endswith("upsert-device"):
            return {"deviceId": "device-1"}
        if path.endswith("upsert-component"):
            return {"componentId": "component-1"}
        return {"faultId": "fault-1", "solutionId": "solution-1"}

    extractor._call_java = call_java
    monkeypatch.setattr(
        kg,
        "assess_section_structure",
        lambda _chunks: {"ok": True, "reason": "", "stats": {}},
    )

    asyncio.run(extractor.extract_document("manual-1", device_type_hint="测试设备"))

    component_path, component_body = next(
        item for item in calls if item[0].endswith("upsert-component")
    )
    assert component_path.endswith("upsert-component")
    assert component_body["documentId"] == "manual-1"
    assert component_body["documentVersion"] == "batch-7"
    assert component_body["sectionId"] == "6.2"
    assert component_body["sourceChunkUid"] == "chunk-23"
    assert component_body["pageStart"] == 23
    assert component_body["pageEnd"] == 23
