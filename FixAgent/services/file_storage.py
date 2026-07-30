"""File URL adapters used by the Python knowledge import pipeline."""

from __future__ import annotations

import logging
import mimetypes
import shutil
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote, unquote, urlparse

from config.settings import get_settings

logger = logging.getLogger(__name__)


class FileStorageConfigurationError(RuntimeError):
    """Raised when the selected file-storage backend is not usable."""


class FileStorage:
    """Expose retrievable URLs for files already persisted by the pipeline."""

    def ensure_public_url(self, image: dict, force: bool = False) -> str:
        raise NotImplementedError

    def ensure_document_url(self, file_url: str) -> str:
        return file_url

    def delete_images(self, image_urls: List[str]) -> int:
        """按图片 public URL 删除已存储的图片对象，返回删除数量。

        基类默认空实现；持有图片对象的后端（MinIO）覆盖它。
        """
        return 0


class LocalFileStorage(FileStorage):
    """Map extracted local file paths to a backend-served URL prefix."""

    def __init__(self, public_base_url: str = "/files", storage_dir: str = "rag_files"):
        self.public_base_url = public_base_url.rstrip("/")
        self.storage_dir = Path(storage_dir)

    def ensure_public_url(self, image: dict, force: bool = False) -> str:
        existing = (image.get("image_url") or "").strip()
        if existing and not force:
            return existing

        local_path = (image.get("local_path") or "").strip()
        if not local_path:
            return ""

        path = Path(local_path)
        if not path.is_file():
            raise ValueError(f"image local_path is not a readable file: {local_path}")
        parent = path.parent.name or "images"
        target_dir = self.storage_dir / parent
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / path.name
        if path.resolve() != target.resolve():
            shutil.copy2(path, target)
        return f"{self.public_base_url}/{parent}/{path.name}"

    def ensure_document_url(self, file_url: str) -> str:
        if file_url.startswith(("http://", "https://")):
            return file_url
        path = Path(file_url.strip().strip('"'))
        if not path.exists():
            return file_url
        target_dir = self.storage_dir / "documents"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / path.name
        if path.resolve() != target.resolve():
            shutil.copy2(path, target)
        return f"{self.public_base_url}/documents/{path.name}"


class MinioStorage(FileStorage):
    """Resolve URLs already uploaded to MinIO by Java or a storage worker."""

    def __init__(
        self,
        public_base_url: str,
        endpoint: str = "",
        access_key: str = "",
        secret_key: str = "",
        document_bucket: str = "fixagent-rag",
        public_image_bucket: str = "fixagent-rag",
        secure: bool = False,
    ):
        missing = []
        if not endpoint:
            missing.append("MINIO_ENDPOINT")
        if not access_key:
            missing.append("MINIO_ACCESS_KEY")
        if not secret_key:
            missing.append("MINIO_SECRET_KEY")
        if not public_image_bucket:
            missing.append("MINIO_PUBLIC_IMAGE_BUCKET")
        if missing:
            raise FileStorageConfigurationError(
                "MinIO storage configuration is incomplete: " + ", ".join(missing)
            )

        self.endpoint = endpoint.strip().rstrip("/")
        self.access_key = access_key
        self.secret_key = secret_key
        self.document_bucket = document_bucket
        self.public_image_bucket = public_image_bucket
        self.secure = secure
        self.public_base_url = public_base_url.rstrip("/") or (
            f"{'https' if secure else 'http'}://{self.endpoint}/{public_image_bucket}"
        )
        self._client = None

    def ensure_public_url(self, image: dict, force: bool = False) -> str:
        existing = (image.get("image_url") or "").strip()
        if existing and not force:
            return existing

        object_key = (image.get("object_key") or "").strip().lstrip("/")
        if not object_key and existing:
            object_key = self._object_key_from_url(existing)

        local_path = Path((image.get("local_path") or "").strip())
        if not local_path.is_file():
            if object_key and not force:
                return self._public_url(object_key)
            raise ValueError("image has neither image_url nor a readable local_path")

        uploaded_key = object_key or f"pdf-images/{local_path.parent.name}/{local_path.name}"
        self._upload(local_path, uploaded_key, self.public_image_bucket)
        return self._public_url(uploaded_key)

    def ensure_document_url(self, file_url: str) -> str:
        if file_url.startswith(("http://", "https://")):
            return file_url
        local_path = Path(file_url.strip().strip('"'))
        if not local_path.exists():
            return file_url
        object_key = f"pdf-documents/{local_path.name}"
        self._upload(local_path, object_key, self.document_bucket)
        try:
            return self._client.presigned_get_object(self.document_bucket, object_key)
        except Exception as exc:
            raise RuntimeError(
                f"MinIO document URL generation failed for {object_key}: {exc}"
            ) from exc

    def _ensure_client(self):
        """懒加载 MinIO 客户端。连接池上限调大(默认10→24,覆盖图片并发)，消除 "pool is full"。"""
        if self._client is None:
            from minio import Minio
            import urllib3

            self._client = Minio(
                self.endpoint,
                access_key=self.access_key,
                secret_key=self.secret_key,
                secure=self.secure,
                http_client=urllib3.PoolManager(
                    num_pools=10,
                    maxsize=24,
                    timeout=urllib3.Timeout(connect=10, read=60),
                    retries=urllib3.Retry(
                        total=3, backoff_factor=0.2,
                        status_forcelist=[500, 502, 503, 504],
                    ),
                ),
            )
        return self._client

    def _upload(self, path: Path, object_key: str, bucket: str) -> None:
        client = self._ensure_client()
        try:
            if not client.bucket_exists(bucket):
                client.make_bucket(bucket)
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            client.fput_object(
                bucket,
                object_key,
                str(path),
                content_type=content_type,
            )
        except Exception as exc:
            raise RuntimeError(
                f"MinIO upload failed for bucket={bucket}, object={object_key}: {exc}"
            ) from exc

    def _public_url(self, object_key: str) -> str:
        encoded_key = quote(unquote(object_key).lstrip("/"), safe="/")
        return f"{self.public_base_url}/{encoded_key}"

    def _object_key_from_url(self, url: str) -> str:
        """从图片 public URL 反解出 MinIO object_key。"""
        url = (url or "").strip()
        if not url:
            return ""
        base = self.public_base_url + "/"
        if url.startswith(base):
            return unquote(url[len(base):])
        # 兜底：解析 URL path，去掉可能的 bucket 段
        path = unquote(urlparse(url).path).lstrip("/")
        parts = path.split("/", 1)
        if len(parts) == 2 and parts[0] == self.public_image_bucket:
            return parts[1]
        return path

    def delete_images(self, image_urls: List[str]) -> int:
        """按图片 public URL 删除 MinIO 图片对象；任一失败即报告删除失败。"""
        if not image_urls:
            return 0
        client = self._ensure_client()
        deleted = 0
        failures = []
        for url in image_urls:
            key = self._object_key_from_url(url)
            if not key:
                continue
            try:
                client.remove_object(self.public_image_bucket, key)
                deleted += 1
            except Exception as exc:
                logger.warning("删除 MinIO 图片失败 key=%s: %s", key, exc)
                failures.append((key, exc))
        if failures:
            key, exc = failures[0]
            raise RuntimeError(
                f"failed to delete {len(failures)} MinIO image(s); "
                f"first object={key}: {exc}"
            ) from exc
        return deleted


_file_storage: Optional[FileStorage] = None


def get_file_storage() -> FileStorage:
    global _file_storage
    if _file_storage is None:
        settings = get_settings()
        if settings.file_storage_backend.lower() == "minio":
            _file_storage = MinioStorage(
                settings.minio_public_base_url,
                settings.minio_endpoint,
                settings.minio_access_key,
                settings.minio_secret_key,
                settings.minio_document_bucket,
                settings.minio_public_image_bucket,
                settings.minio_secure,
            )
        else:
            _file_storage = LocalFileStorage(settings.file_public_base_url, settings.local_file_storage_dir)
    return _file_storage
