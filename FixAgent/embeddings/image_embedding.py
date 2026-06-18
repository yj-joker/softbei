"""
图像向量化模块

使用阿里云百炼 qwen2.5-vl-embedding 将图片转为向量。
与 text_embedding.py 共用同一模型，确保文本和图片向量在同一语义空间。

【模型信息】
- 模型: qwen2.5-vl-embedding
- 默认维度: 1024
- 图片限制: 5MB
- 输入格式: [{"image": "url_or_base64"}]
"""

import asyncio
import hashlib
import logging
import redis
from typing import Optional, List

import dashscope
from config.settings import get_settings

logger = logging.getLogger(__name__)


class ImageEmbedding:
    """图像向量化服务，使用 qwen2.5-vl-embedding 统一模型。"""

    def __init__(self):
        self.settings = get_settings()
        self.model = "qwen2.5-vl-embedding"
        dashscope.api_key = self.settings.dashscope_api_key
        self.redis = redis.Redis(
            host=self.settings.redis_host,
            port=self.settings.redis_port,
            password=self.settings.redis_password,
            db=self.settings.redis_db,
            decode_responses=False
        )
        self.cache_ttl = self.settings.redis_ttl

    def _get_cache_key(self, image_url: str) -> str:
        return f"cache:emb:image:v2:{hashlib.md5(image_url.encode()).hexdigest()}"

    def _get_from_cache(self, image_url: str) -> Optional[List[float]]:
        data = self.redis.get(self._get_cache_key(image_url))
        if data:
            import pickle
            return pickle.loads(data)
        return None

    def _set_to_cache(self, image_url: str, embedding: List[float]) -> None:
        import pickle
        self.redis.setex(self._get_cache_key(image_url), self.cache_ttl, pickle.dumps(embedding))

    def _call_api_sync(self, inputs: List[dict]) -> List[List[float]]:
        """同步调用 dashscope MultiModalEmbedding API。"""
        resp = dashscope.MultiModalEmbedding.call(
            model=self.model,
            input=inputs
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Embedding API 返回错误 code={resp.status_code} message={resp.message}"
            )

        if resp.output and "embeddings" in resp.output:
            embeddings = sorted(resp.output["embeddings"], key=lambda x: x.get("index", 0))
            result = [e["embedding"] for e in embeddings]
            if result:
                logger.debug(f"图片向量化完成 模型={self.model} 维度={len(result[0])}")
            return result

        raise ValueError(f"Embedding API 响应格式异常: {resp}")

    async def embed(self, image_url: str) -> List[float]:
        """单张图片向量化。"""
        cached = self._get_from_cache(image_url)
        if cached is not None:
            return cached

        embeddings = await asyncio.to_thread(
            self._call_api_sync, [{"image": image_url}]
        )
        result = embeddings[0]
        self._set_to_cache(image_url, result)
        return result

    async def embed_text_as_multimodal(self, text: str) -> List[float]:
        """
        将纯文本通过多模态模型映射到 1024 维空间。
        用于没有图片的实体生成多模态向量，确保它们也能被图片搜索命中。
        输入格式: [{"text": "..."}]
        """
        cache_key = f"txt_mm_emb:v1:{hashlib.md5(text.encode()).hexdigest()}"
        data = self.redis.get(cache_key)
        if data:
            import pickle
            return pickle.loads(data)

        embeddings = await asyncio.to_thread(
            self._call_api_sync, [{"text": text}]
        )
        result = embeddings[0]

        import pickle
        self.redis.setex(cache_key, self.cache_ttl, pickle.dumps(result))
        return result

    async def embed_batch(self, image_urls: List[str]) -> List[List[float]]:
        """批量图片向量化，单次 API 调用。"""
        results: List[Optional[List[float]]] = []
        uncached_indices: List[int] = []
        uncached_inputs: List[dict] = []

        for i, url in enumerate(image_urls):
            cached = self._get_from_cache(url)
            if cached is not None:
                results.append(cached)
            else:
                results.append(None)
                uncached_indices.append(i)
                uncached_inputs.append({"image": url})

        if uncached_inputs:
            new_embeddings = await asyncio.to_thread(
                self._call_api_sync, uncached_inputs
            )
            for idx, emb in zip(uncached_indices, new_embeddings):
                results[idx] = emb
                self._set_to_cache(image_urls[idx], emb)

        return results


_image_embedding: Optional[ImageEmbedding] = None


def get_image_embedding() -> ImageEmbedding:
    global _image_embedding
    if _image_embedding is None:
        _image_embedding = ImageEmbedding()
    return _image_embedding
