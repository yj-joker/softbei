"""File URL adapters used by the Python knowledge import pipeline."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import List, Optional

from config.settings import get_settings

logger = logging.getLogger(__name__)


class FileStorage:
    """Expose retrievable URLs for files already persisted by the pipeline."""

    def ensure_public_url(self, image: dict) -> str:
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

    def ensure_public_url(self, image: dict) -> str:
        existing = (image.get("image_url") or "").strip()
        if existing:
            return existing

        local_path = (image.get("local_path") or "").strip()
        if not local_path:
            return ""

        path = Path(local_path)
        if not path.exists():
            return ""
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
        self.public_base_url = public_base_url.rstrip("/")
        self.endpoint = endpoint
        self.access_key = access_key
        self.secret_key = secret_key
        self.document_bucket = document_bucket
        self.public_image_bucket = public_image_bucket
        self.secure = secure
        self._client = None

    def ensure_public_url(self, image: dict) -> str:
        existing = (image.get("image_url") or "").strip()
        if existing:
            return existing
        object_key = (image.get("object_key") or "").strip().lstrip("/")
        if object_key and self.public_base_url:
            return f"{self.public_base_url}/{object_key}"
        local_path = Path((image.get("local_path") or "").strip())
        if local_path.exists():
            uploaded_key = object_key or f"pdf-images/{local_path.parent.name}/{local_path.name}"
            if self._upload(local_path, uploaded_key, self.public_image_bucket):
                if self.public_base_url:
                    return f"{self.public_base_url}/{uploaded_key}"
                try:
                    return self._client.presigned_get_object(self.public_image_bucket, uploaded_key)
                except Exception:
                    return ""
        return ""

    def ensure_document_url(self, file_url: str) -> str:
        if file_url.startswith(("http://", "https://")):
            return file_url
        local_path = Path(file_url.strip().strip('"'))
        if not local_path.exists():
            return file_url
        object_key = f"pdf-documents/{local_path.name}"
        if not self._upload(local_path, object_key, self.document_bucket):
            return file_url
        try:
            return self._client.presigned_get_object(self.document_bucket, object_key)
        except Exception:
            return file_url

    def _ensure_client(self):
        """懒加载 MinIO 客户端。连接池上限调大(默认10→24,覆盖图片并发)，消除 "pool is full"。"""
        if self._client is None:
            if not (self.endpoint and self.access_key and self.secret_key):
                return None
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

    def _upload(self, path: Path, object_key: str, bucket: str) -> bool:
        client = self._ensure_client()
        if client is None:
            return False
        try:
            if not client.bucket_exists(bucket):
                client.make_bucket(bucket)
            client.fput_object(bucket, object_key, str(path))
            return True
        except Exception:
            return False

    def _object_key_from_url(self, url: str) -> str:
        """从图片 public URL 反解出 MinIO object_key。"""
        url = (url or "").strip()
        if not url:
            return ""
        base = self.public_base_url + "/"
        if url.startswith(base):
            return url[len(base):]
        # 兜底：解析 URL path，去掉可能的 bucket 段
        from urllib.parse import urlparse, unquote
        path = unquote(urlparse(url).path).lstrip("/")
        parts = path.split("/", 1)
        if len(parts) == 2 and parts[0] == self.public_image_bucket:
            return parts[1]
        return path

    def delete_images(self, image_urls: List[str]) -> int:
        """按图片 public URL 删除 MinIO 图片对象。单张失败仅记日志，不中断。"""
        if not image_urls:
            return 0
        client = self._ensure_client()
        if client is None:
            return 0
        deleted = 0
        for url in image_urls:
            key = self._object_key_from_url(url)
            if not key:
                continue
            try:
                client.remove_object(self.public_image_bucket, key)
                deleted += 1
            except Exception as exc:
                logger.warning("删除 MinIO 图片失败 key=%s: %s", key, exc)
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
