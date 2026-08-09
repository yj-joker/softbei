import unittest

from embeddings.constants import EMBEDDING_DIMENSIONS, ensure_embedding_dimensions
from services.knowledge.vector_service import VectorService


class EmbeddingDimensionContractTests(unittest.TestCase):
    def test_allows_1024_dimension_vectors(self):
        vector = [0.0] * EMBEDDING_DIMENSIONS

        self.assertIs(vector, ensure_embedding_dimensions(vector, "测试"))
        service = VectorService.__new__(VectorService)
        self.assertEqual(EMBEDDING_DIMENSIONS * 4, len(service._to_bytes(vector)))

    def test_rejects_non_1024_dimension_vectors(self):
        service = VectorService.__new__(VectorService)

        with self.assertRaisesRegex(ValueError, "期望1024"):
            service._to_bytes([0.0] * (EMBEDDING_DIMENSIONS + 1))


if __name__ == "__main__":
    unittest.main()
