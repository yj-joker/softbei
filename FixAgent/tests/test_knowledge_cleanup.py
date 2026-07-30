import asyncio
import json
import unittest
from unittest.mock import patch

from mq import consumer
from services.knowledge.service import KnowledgeService


class _VectorService:
    def __init__(self, fail_delete=False):
        self.fail_delete = fail_delete
        self.calls = []

    def get_document_image_urls(self, document_id):
        self.calls.append(("get_urls", document_id))
        return ["http://minio/images/a.png"]

    def delete_by_document(self, document_id):
        self.calls.append(("delete_vectors", document_id))
        if self.fail_delete:
            raise RuntimeError("redis unavailable")
        return 7

    def delete_document_manifest(self, document_id):
        self.calls.append(("delete_manifest", document_id))
        return True


class _FileStorage:
    def __init__(self):
        self.calls = []

    def delete_images(self, urls):
        self.calls.append(("delete_images", list(urls)))
        return len(urls)


def _knowledge_service(vector_service=None, file_storage=None):
    service = object.__new__(KnowledgeService)
    service.vector_svc = vector_service or _VectorService()
    service.file_storage = file_storage or _FileStorage()
    return service


class _ProcessContext:
    def __init__(self, message, requeue):
        self.message = message
        self.requeue = requeue

    async def __aenter__(self):
        self.message.requeue = self.requeue
        return self.message

    async def __aexit__(self, exc_type, exc, traceback):
        self.message.exit_exception = exc
        return False


class _DeleteMessage:
    def __init__(self):
        self.body = json.dumps({
            "action": "delete",
            "documentId": "kdoc_1",
        }).encode()
        self.requeue = None
        self.exit_exception = None

    def process(self, requeue=False):
        return _ProcessContext(self, requeue)


class KnowledgeCleanupTest(unittest.TestCase):
    def test_delete_document_cleans_images_before_vectors_and_manifest(self):
        vectors = _VectorService()
        storage = _FileStorage()
        service = _knowledge_service(vectors, storage)

        result = service.delete_document("kdoc_1")

        self.assertEqual(result, {
            "vectors_deleted": 7,
            "images_deleted": 1,
            "manifest_deleted": True,
        })
        self.assertEqual(vectors.calls, [
            ("get_urls", "kdoc_1"),
            ("delete_vectors", "kdoc_1"),
            ("delete_manifest", "kdoc_1"),
        ])
        self.assertEqual(storage.calls, [
            ("delete_images", ["http://minio/images/a.png"]),
        ])

    def test_delete_document_propagates_redis_failure(self):
        service = _knowledge_service(_VectorService(fail_delete=True), _FileStorage())

        with self.assertRaisesRegex(RuntimeError, "redis unavailable"):
            service.delete_document("kdoc_1")

    def test_image_persistence_failure_rejects_import_success(self):
        with self.assertRaisesRegex(
            RuntimeError,
            r"1 of 58 image\(s\) failed",
        ):
            KnowledgeService._ensure_complete_image_persistence(
                failed_count=1,
                expected_count=58,
            )

    def test_delete_consumer_requeues_cleanup_failure(self):
        class _FailingKnowledgeService:
            def delete_document(self, document_id):
                raise RuntimeError("redis unavailable")

        message = _DeleteMessage()
        with patch(
            "services.knowledge.service.get_knowledge_service",
            return_value=_FailingKnowledgeService(),
        ):
            with self.assertRaisesRegex(RuntimeError, "redis unavailable"):
                asyncio.run(consumer.handle_knowledge_import(message, object()))

        self.assertTrue(message.requeue)
        self.assertIsInstance(message.exit_exception, RuntimeError)


if __name__ == "__main__":
    unittest.main()
