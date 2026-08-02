"""Document identity extraction and manifest backfill."""

from __future__ import annotations

import asyncio
import json

from services.knowledge.service import KnowledgeService
from services.retrieval.device_identity import (
    DeviceCatalog,
    load_dynamic_device_catalog,
)


class _IdentityLLM:
    def __init__(self, *, confidence: float = 0.94):
        self.confidence = confidence
        self.calls = 0
        self.messages = []

    async def chat(self, messages, **kwargs):
        self.calls += 1
        self.messages.append(messages)
        return {
            "content": json.dumps(
                {
                    "device_name": "摩托车发动机",
                    "device_category": "发动机",
                    "carrier_or_application": "摩托车",
                    "manufacturer": "",
                    "model": "",
                    "confidence": self.confidence,
                },
                ensure_ascii=False,
            )
        }


class _VectorStore:
    def __init__(self, manifest):
        self.manifest = dict(manifest)
        self.saved = []
        self.metadata_updates = []

    def list_all_manifests(self):
        return [dict(self.manifest)]

    def list_document_chunks(self, document_id):
        assert document_id == self.manifest["document_id"]
        return [
            {
                "text": "气缸与活塞、离合器、磁电机转子、左右曲轴箱盖的拆装步骤",
                "metadata": {"page": 1, "section_title": "目录"},
            }
        ]

    def put_document_manifest(self, document_id, manifest):
        self.saved.append((document_id, dict(manifest)))
        self.manifest = dict(manifest)
        return True

    def update_document_metadata(self, document_id, updates):
        self.metadata_updates.append((document_id, dict(updates)))
        return 4


class _ImportParser:
    async def _execute(self, file_url, file_type):
        return {
            "file_name": "uploaded-object.pdf",
            "total_pages": 1,
            "sections": [
                {
                    "section_title": "设备维修手册",
                    "page_range": "1",
                    "text_chunks": [
                        {
                            "text": "本手册说明设备总成的检查、拆卸和安装要求。",
                            "page": 1,
                            "chunk_label": "general",
                        }
                    ],
                    "tables": [
                        {
                            "page": 1,
                            "caption": "装配部件清单",
                            "rows": [["序号", "零件名称"], ["1", "总成"]],
                        }
                    ],
                    "images": [
                        {
                            "page": 1,
                            "image_name": "assembly.png",
                            "caption": "设备总成结构图",
                        }
                    ],
                }
            ],
            "extraction_summary": {"text_chunks_total": 1},
        }


class _TextEmbedding:
    async def embed_batch(self, texts):
        return [[0.1, 0.2] for _ in texts]

    async def embed(self, text):
        return [0.1, 0.2]


class _ImageEmbedding:
    async def embed_batch(self, images):
        return [[0.3, 0.4] for _ in images]

    async def embed(self, image):
        return [0.3, 0.4]


class _FileStorage:
    def ensure_document_url(self, file_url):
        return file_url

    def ensure_public_url(self, image):
        return "https://example.invalid/assembly.png"


class _ImageSummary:
    async def summarize(self, **kwargs):
        return {
            "image_title": "设备总成结构图",
            "image_summary": "图片展示设备总成结构。",
            "summary_source": "caption",
        }


class _ImportVectorStore:
    def __init__(self, manifest=None, manifests=None, *, manifest_write_success=True):
        self.manifest = dict(manifest or {})
        self.manifests = {
            str(item["document_id"]): dict(item)
            for item in (manifests or [])
        }
        if self.manifest.get("document_id"):
            self.manifests[str(self.manifest["document_id"])] = dict(self.manifest)
        self.manifest_write_success = manifest_write_success
        self.saved_manifests = []
        self.batch_docs = []
        self.single_docs = []

    def get_document_manifest(self, document_id):
        return dict(self.manifests.get(str(document_id), {}))

    def put_document_manifest(self, document_id, manifest):
        if not self.manifest_write_success:
            return False
        saved = dict(manifest)
        self.manifest = saved
        self.manifests[str(document_id)] = saved
        self.saved_manifests.append(saved)
        return True

    def add_vector_batch(self, docs):
        self.batch_docs.extend(docs)
        return len(docs)

    def add_vector(self, **doc):
        self.single_docs.append(doc)
        return True


def _build_import_service(
    *,
    llm,
    manifest=None,
    manifests=None,
    manifest_write_success=True,
):
    service = KnowledgeService.__new__(KnowledgeService)
    service.parser = _ImportParser()
    service.text_emb = _TextEmbedding()
    service.image_emb = _ImageEmbedding()
    service.file_storage = _FileStorage()
    service.image_summary_svc = _ImageSummary()
    service.vector_svc = _ImportVectorStore(
        manifest,
        manifests,
        manifest_write_success=manifest_write_success,
    )
    service.llm_svc = llm
    return service


def _indexed_metadata(service):
    docs = [*service.vector_svc.batch_docs, *service.vector_svc.single_docs]
    return [doc["metadata"] for doc in docs]


def test_runtime_catalog_does_not_extract_or_mutate_missing_document_identity() -> None:
    vector = _VectorStore(
        {
            "document_id": "manual-1",
            "status": "ready",
            "file_name": "uploaded-object.pdf",
            "document_version": "v1",
        }
    )
    llm = _IdentityLLM()

    catalog = asyncio.run(
        load_dynamic_device_catalog(vector_service=vector, llm_service=llm)
    )

    assert llm.calls == 0
    assert catalog.documents == ()
    assert vector.saved == []
    assert vector.metadata_updates == []


def test_existing_high_confidence_identity_is_reused_without_llm_call() -> None:
    vector = _VectorStore(
        {
            "document_id": "manual-1",
            "status": "ready",
            "index_revision": 4,
            "identity_metadata_revision": 4,
            "document_identity": {
                "device_name": "摩托车发动机",
                "device_category": "发动机",
                "carrier_or_application": "摩托车",
                "confidence": 0.96,
            },
        }
    )
    llm = _IdentityLLM()

    catalog = asyncio.run(
        load_dynamic_device_catalog(vector_service=vector, llm_service=llm)
    )

    assert llm.calls == 0
    assert vector.saved == []
    assert catalog.documents[0].index_revision == 4


def test_runtime_catalog_authorizes_existing_identity_without_mutating_index() -> None:
    vector = _VectorStore(
        {
            "document_id": "manual-1",
            "status": "ready",
            "index_revision": 4,
            "document_identity": {
                "device_name": "摩托车发动机",
                "device_category": "发动机",
                "carrier_or_application": "摩托车",
                "confidence": 0.96,
            },
        }
    )

    catalog = asyncio.run(
        load_dynamic_device_catalog(vector_service=vector, llm_service=_IdentityLLM())
    )

    assert len(catalog.documents) == 1
    assert vector.metadata_updates == []
    assert vector.saved == []


def test_low_confidence_extraction_does_not_authorize_document_retrieval() -> None:
    vector = _VectorStore(
        {
            "document_id": "manual-1",
            "status": "ready",
            "file_name": "unknown.pdf",
        }
    )
    llm = _IdentityLLM(confidence=0.42)

    catalog = asyncio.run(
        load_dynamic_device_catalog(vector_service=vector, llm_service=llm)
    )

    assert catalog.documents == ()
    assert vector.saved == []


def test_failed_or_non_ready_manifest_is_not_backfilled() -> None:
    vector = _VectorStore(
        {
            "document_id": "manual-1",
            "status": "failed",
            "file_name": "unknown.pdf",
        }
    )
    llm = _IdentityLLM()

    catalog = asyncio.run(
        load_dynamic_device_catalog(vector_service=vector, llm_service=llm)
    )

    assert catalog.documents == ()
    assert llm.calls == 0
    assert vector.saved == []


def test_manifest_without_status_is_not_backfilled() -> None:
    vector = _VectorStore(
        {
            "document_id": "manual-1",
            "file_name": "unknown.pdf",
        }
    )
    llm = _IdentityLLM()

    catalog = asyncio.run(
        load_dynamic_device_catalog(vector_service=vector, llm_service=llm)
    )

    assert catalog.documents == ()
    assert llm.calls == 0
    assert vector.saved == []
    assert vector.metadata_updates == []


def test_import_prefers_explicit_identity_and_propagates_it_to_every_index_record() -> None:
    llm = _IdentityLLM()
    service = _build_import_service(
        llm=llm,
        manifest={"document_id": "manual-1", "index_revision": 4, "status": "ready"},
    )

    asyncio.run(
        service.import_document(
            file_url="https://example.invalid/manual.pdf",
            document_id="manual-1",
            device_type="设备甲总成",
            document_identity={
                "device_name": "设备甲总成",
                "device_category": "动力设备",
                "carrier_or_application": "平台甲",
                "manufacturer": "制造商甲",
                "model": "MODEL-A",
                "identity_confidence": 0.98,
            },
        )
    )

    assert llm.calls == 0
    manifest = service.vector_svc.manifest
    assert manifest["index_revision"] == 5
    assert manifest["document_identity"] == {
        "device_name": "设备甲总成",
        "device_type": "设备甲总成",
        "device_category": "动力设备",
        "carrier_or_application": "平台甲",
        "manufacturer": "制造商甲",
        "model": "MODEL-A",
        "confidence": 0.98,
        "identity_source": "user_metadata",
    }

    metadata_items = _indexed_metadata(service)
    assert {item["chunk_type"] for item in metadata_items} >= {
        "text",
        "table",
        "image",
        "image_summary",
    }
    for metadata in metadata_items:
        assert metadata["device_name"] == "设备甲总成"
        assert metadata["device_category"] == "动力设备"
        assert metadata["carrier_or_application"] == "平台甲"
        assert metadata["manufacturer"] == "制造商甲"
        assert metadata["model"] == "MODEL-A"
        assert metadata["identity_confidence"] == 0.98
        assert metadata["index_revision"] == 5


def test_import_extracts_identity_from_manual_title_and_opening_text() -> None:
    llm = _IdentityLLM()
    service = _build_import_service(llm=llm)

    asyncio.run(
        service.import_document(
            file_url="https://example.invalid/manual.pdf",
            document_id="manual-2",
        )
    )

    assert llm.calls == 1
    extraction_input = json.loads(llm.messages[0][1]["content"])
    assert extraction_input["file_name"] == "uploaded-object.pdf"
    assert "设备维修手册" in extraction_input["document_excerpt"]
    assert "本手册说明设备总成" in extraction_input["document_excerpt"]
    assert service.vector_svc.manifest["document_identity"]["identity_source"] == "document_content"
    assert service.vector_svc.manifest["index_revision"] == 1


def test_low_confidence_import_identity_is_recorded_as_untrusted_and_cannot_authorize() -> None:
    service = _build_import_service(llm=_IdentityLLM(confidence=0.42))

    asyncio.run(
        service.import_document(
            file_url="https://example.invalid/manual.pdf",
            document_id="manual-3",
        )
    )

    manifest = service.vector_svc.manifest
    assert "document_identity" not in manifest
    assert manifest["identity_confidence"] == 0.0
    assert DeviceCatalog.from_manifests([manifest]).documents == ()
    assert all(item["identity_confidence"] == 0.0 for item in _indexed_metadata(service))


def test_cross_document_reimport_inherits_previous_index_revision() -> None:
    service = _build_import_service(
        llm=_IdentityLLM(),
        manifests=[{
            "document_id": "manual-old",
            "index_revision": 4,
            "status": "ready",
        }],
    )

    result = asyncio.run(
        service.import_document(
            file_url="https://example.invalid/manual-v2.pdf",
            document_id="manual-new",
            old_document_id="manual-old",
            replace_existing=True,
        )
    )

    assert result["index_revision"] == 5
    assert service.vector_svc.manifest["index_revision"] == 5


def test_generic_device_type_is_only_a_hint_not_a_trusted_document_name() -> None:
    llm = _IdentityLLM()
    service = _build_import_service(llm=llm)

    asyncio.run(
        service.import_document(
            file_url="https://example.invalid/manual.pdf",
            document_id="manual-generic",
            device_type="发动机",
        )
    )

    assert llm.calls == 1
    assert service.vector_svc.manifest["document_identity"]["identity_source"] == "document_content"
    assert service.vector_svc.manifest["document_identity"]["device_name"] == "摩托车发动机"


def test_manifest_write_failure_fails_the_import() -> None:
    service = _build_import_service(
        llm=_IdentityLLM(),
        manifest_write_success=False,
    )

    try:
        asyncio.run(
            service.import_document(
                file_url="https://example.invalid/manual.pdf",
                document_id="manual-write-failure",
            )
        )
    except RuntimeError as exc:
        assert "manifest" in str(exc).lower()
    else:
        raise AssertionError("manifest write failure must abort the import")
