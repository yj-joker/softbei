import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import embeddings.text_embedding as text_embedding_module
from embeddings.constants import EMBEDDING_DIMENSIONS


class TextEmbeddingCompatibilityTests(unittest.TestCase):
    def test_legacy_dashscope_uses_sync_api_in_worker_thread(self):
        embedding = text_embedding_module.TextEmbedding.__new__(
            text_embedding_module.TextEmbedding
        )
        embedding.model = "qwen2.5-vl-embedding"
        embedding.settings = SimpleNamespace(dashscope_api_key="test-key")

        response = SimpleNamespace(
            status_code=200,
            output={
                "embeddings": [
                    {"index": 0, "embedding": [0.1] * EMBEDDING_DIMENSIONS},
                ]
            },
            message="",
        )

        class FakeLimiter:
            async def acquire(self):
                return None

        async def run_test():
            with (
                patch.object(
                    text_embedding_module,
                    "AioMultiModalEmbedding",
                    None,
                ),
                patch.object(
                    text_embedding_module,
                    "get_embedding_rate_limiter",
                    return_value=FakeLimiter(),
                ),
                patch.object(
                    text_embedding_module.dashscope.MultiModalEmbedding,
                    "call",
                    return_value=response,
                ) as sync_call,
            ):
                result = await embedding._call_api_async([{"text": "test"}])

            self.assertEqual(EMBEDDING_DIMENSIONS, len(result[0]))
            sync_call.assert_called_once_with(
                model="qwen2.5-vl-embedding",
                input=[{"text": "test"}],
                api_key="test-key",
            )

        asyncio.run(run_test())

    def test_rejects_model_response_with_wrong_dimensions(self):
        embedding = text_embedding_module.TextEmbedding.__new__(
            text_embedding_module.TextEmbedding
        )
        embedding.model = "qwen2.5-vl-embedding"
        embedding.settings = SimpleNamespace(dashscope_api_key="test-key")

        response = SimpleNamespace(
            status_code=200,
            output={"embeddings": [{"index": 0, "embedding": [0.1]}]},
            message="",
        )

        class FakeLimiter:
            async def acquire(self):
                return None

        async def run_test():
            with (
                patch.object(text_embedding_module, "AioMultiModalEmbedding", None),
                patch.object(
                    text_embedding_module,
                    "get_embedding_rate_limiter",
                    return_value=FakeLimiter(),
                ),
                patch.object(
                    text_embedding_module.dashscope.MultiModalEmbedding,
                    "call",
                    return_value=response,
                ),
            ):
                with self.assertRaisesRegex(ValueError, "期望1024"):
                    await embedding._call_api_async([{"text": "test"}])

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
