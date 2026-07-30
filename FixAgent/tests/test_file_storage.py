import tempfile
import unittest
from pathlib import Path

from services.file_storage import (
    FileStorageConfigurationError,
    MinioStorage,
)


class _FakeMinioClient:
    def __init__(self, upload_error: Exception | None = None):
        self.upload_error = upload_error
        self.uploads = []

    def bucket_exists(self, bucket):
        return True

    def fput_object(self, bucket, object_key, path, content_type=None):
        if self.upload_error is not None:
            raise self.upload_error
        self.uploads.append((bucket, object_key, path, content_type))


def _storage(public_base_url=""):
    return MinioStorage(
        public_base_url=public_base_url,
        endpoint="localhost:9000",
        access_key="access",
        secret_key="secret",
        document_bucket="documents",
        public_image_bucket="images",
        secure=False,
    )


class MinioStorageTest(unittest.TestCase):
    def test_rejects_incomplete_configuration(self):
        with self.assertRaisesRegex(
            FileStorageConfigurationError,
            "MINIO_ENDPOINT",
        ):
            MinioStorage(
                public_base_url="",
                endpoint="",
                access_key="access",
                secret_key="secret",
                document_bucket="documents",
                public_image_bucket="images",
            )

    def test_derives_public_url_and_uploads_image(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_dir = Path(temp_dir) / "文档图片"
            image_dir.mkdir()
            image_path = image_dir / "图 1.png"
            image_path.write_bytes(b"real-image-bytes")

            storage = _storage()
            client = _FakeMinioClient()
            storage._client = client

            image_url = storage.ensure_public_url({"local_path": str(image_path)})

            object_key = f"pdf-images/{image_dir.name}/{image_path.name}"
            self.assertEqual(
                client.uploads,
                [("images", object_key, str(image_path), "image/png")],
            )
            self.assertEqual(
                image_url,
                "http://localhost:9000/images/"
                "pdf-images/%E6%96%87%E6%A1%A3%E5%9B%BE%E7%89%87/"
                "%E5%9B%BE%201.png",
            )

    def test_surfaces_upload_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "image.png"
            image_path.write_bytes(b"real-image-bytes")

            storage = _storage("http://cdn.example/images")
            storage._client = _FakeMinioClient(OSError("connection refused"))

            with self.assertRaisesRegex(RuntimeError, "MinIO upload failed"):
                storage.ensure_public_url({"local_path": str(image_path)})

    def test_force_upload_preserves_encoded_object_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "图 1.png"
            image_path.write_bytes(b"real-image-bytes")

            storage = _storage()
            client = _FakeMinioClient()
            storage._client = client

            image_url = storage.ensure_public_url(
                {
                    "image_url": (
                        "http://localhost:9000/images/"
                        "pdf-images/folder/%E5%9B%BE%201.png"
                    ),
                    "local_path": str(image_path),
                },
                force=True,
            )

            self.assertEqual(
                client.uploads,
                [
                    (
                        "images",
                        "pdf-images/folder/图 1.png",
                        str(image_path),
                        "image/png",
                    )
                ],
            )
            self.assertEqual(
                image_url,
                "http://localhost:9000/images/"
                "pdf-images/folder/%E5%9B%BE%201.png",
            )

    def test_rejects_missing_image_source(self):
        storage = _storage()

        with self.assertRaisesRegex(ValueError, "neither image_url"):
            storage.ensure_public_url({})


if __name__ == "__main__":
    unittest.main()
