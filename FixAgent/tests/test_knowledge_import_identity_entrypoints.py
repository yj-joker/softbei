from __future__ import annotations

import asyncio
import json

import mq.consumer as consumer
import services.knowledge.service as knowledge_service_module
from api import main
from schemas.request import KnowledgeImportRequest


class _ImportService:
    def __init__(self):
        self.kwargs = None

    async def import_document(self, **kwargs):
        self.kwargs = kwargs
        return {
            "file_name": "manual.pdf",
            "total_pages": 1,
            "text_count": 1,
            "image_count": 0,
            "table_count": 0,
            "sections": [],
            "extraction_summary": {},
            "process_time_ms": 1,
        }


class _MessageProcess:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Message:
    def __init__(self, body):
        self.body = json.dumps(body, ensure_ascii=False).encode("utf-8")

    def process(self, **kwargs):
        return _MessageProcess()


def _identity():
    return {
        "device_name": "设备甲",
        "aliases": ["设备乙"],
        "identity_confidence": 1.0,
    }


def test_rest_import_forwards_structured_document_identity(monkeypatch) -> None:
    service = _ImportService()
    monkeypatch.setattr(knowledge_service_module, "get_knowledge_service", lambda: service)
    request = KnowledgeImportRequest(
        file_url="https://example.invalid/manual.pdf",
        document_identity=_identity(),
    )

    asyncio.run(main.knowledge_import(request))

    assert service.kwargs["document_identity"] == _identity()


def test_mq_import_forwards_structured_document_identity(monkeypatch) -> None:
    service = _ImportService()
    published = []

    async def fake_publish(*args, **kwargs):
        published.append((args, kwargs))

    monkeypatch.setattr(knowledge_service_module, "get_knowledge_service", lambda: service)
    monkeypatch.setattr(consumer, "publish_result", fake_publish)
    message = _Message({
        "action": "import",
        "documentId": "manual-1",
        "fileUrl": "https://example.invalid/manual.pdf",
        "documentIdentity": _identity(),
    })

    asyncio.run(consumer.handle_knowledge_import(message, object()))

    assert service.kwargs["document_identity"] == _identity()
    assert published
