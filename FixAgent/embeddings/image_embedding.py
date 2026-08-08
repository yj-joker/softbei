"""
图像向量化模块

使用阿里云百炼 qwen2.5-vl-embedding 将图片转为向量。
与 text_embedding.py 共用同一模型，确保文本和图片向量在同一语义空间。

【模型信息】
- 模型: qwen2.5-vl-embedding
- 默认维度: 1024
- 图片限制: 5MB
- 输入格式: [{"image": "url_or_base64"}]

【并发与连接（2026-06 优化）】
- 图片走本地路径时 dashscope 需先把图片同步上传到 OSS(OssUtils.upload 用同步 requests，
  且在异步调用里被同步执行)，会阻塞事件循环、把并发压成串行——这是图片向量化最慢的根因。
- 因此图片侧改用「同步 SDK + asyncio.to_thread」：把"上传 OSS + 向量化"整体扔进线程池，
  多张图在多个线程里真正并行，互不阻塞事件循环。
- redis 缓存异步化；调用前过全局限速器；新增重试退避兜底网络抖动。
"""

import asyncio
import hashlib
import logging
import pickle
import random
from typing import Optional, List

import dashscope
import redis.asyncio as aioredis

from config.settings import get_settings
from embeddings.constants import ensure_embedding_dimensions
from embeddings.rate_limiter import get_embedding_rate_limiter

logger = logging.getLogger(__name__)


class ImageEmbedding:
    """图像向量化服务，使用 qwen2.5-vl-embedding 统一模型。"""

    _RETRY_DELAYS = (0.5, 1.0)

    def __init__(self):
        self.settings = get_settings()
        self.model = "qwen2.5-vl-embedding"
        dashscope.api_key = self.settings.dashscope_api_key
        self.redis = aioredis.Redis(
            host=self.settings.redis_host,
            port=self.settings.redis_port,
            password=self.settings.redis_password,
            db=self.settings.redis_db,
            decode_responses=False,
        )
        self.cache_ttl = self.settings.redis_ttl

    def _get_cache_key(self, image_url: str) -> str:
        return f"cache:emb:image:v2:{hashlib.md5(image_url.encode()).hexdigest()}"

    async def _get_from_cache(self, image_url: str) -> Optional[List[float]]:
        cache_key = self._get_cache_key(image_url)
        data = await self.redis.get(cache_key)
        if data:
            embedding = pickle.loads(data)
            try:
                return ensure_embedding_dimensions(embedding, "图片缓存")
            except ValueError:
                await self.redis.delete(cache_key)
                logger.warning("已删除维度不匹配的图片向量缓存")
        return None

    async def _set_to_cache(self, image_url: str, embedding: List[float]) -> None:
        await self.redis.setex(self._get_cache_key(image_url), self.cache_ttl, pickle.dumps(embedding))

    def _call_api_sync(self, inputs: List[dict]) -> List[List[float]]:
        """同步调用 dashscope MultiModalEmbedding（含可能的同步 OSS 上传）。

        由调用方放进 asyncio.to_thread 执行——同步 OSS 上传因此落在线程池里，
        多张图可真正并行，不阻塞主事件循环。
        """
        resp = dashscope.MultiModalEmbedding.call(
            model=self.model,
            input=inputs,
            api_key=self.settings.dashscope_api_key,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Embedding API 返回错误 code={resp.status_code} message={resp.message}"
            )

        if resp.output and "embeddings" in resp.output:
            embeddings = sorted(resp.output["embeddings"], key=lambda x: x.get("index", 0))
            result = [
                ensure_embedding_dimensions(e["embedding"], "图片模型返回")
                for e in embeddings
            ]
            if result:
                logger.debug(f"图片向量化完成 模型={self.model} 维度={len(result[0])}")
            return result

        raise ValueError(f"Embedding API 响应格式异常: {resp}")

    async def _call_api_with_retry(self, inputs: List[dict]) -> List[List[float]]:
        for attempt in range(len(self._RETRY_DELAYS) + 1):
            try:
                await get_embedding_rate_limiter().acquire()  # 每次请求前全局限速
                # to_thread：同步上传 OSS + 向量化放线程池，多张图真正并行、不堵事件循环
                return await asyncio.to_thread(self._call_api_sync, inputs)
            except Exception as exc:
                # 捕获所有异常退避重试（含网络层连接重置/超时），仅 4xx 参数错误立即抛出。
                message = str(exc)
                if isinstance(exc, RuntimeError) and "code=4" in message and "code=429" not in message:
                    raise
                if attempt >= len(self._RETRY_DELAYS):
                    raise
                delay = self._RETRY_DELAYS[attempt] + random.uniform(0, self._RETRY_DELAYS[attempt])
                logger.warning(
                    "Image embedding API temporary failure, retrying in %.1fs (attempt %s/%s): %s",
                    delay,
                    attempt + 2,
                    len(self._RETRY_DELAYS) + 1,
                    exc,
                )
                await asyncio.sleep(delay)

    async def embed(self, image_url: str) -> List[float]:
        """单张图片向量化。"""
        cached = await self._get_from_cache(image_url)
        if cached is not None:
            return cached

        embeddings = await self._call_api_with_retry([{"image": image_url}])
        result = embeddings[0]
        await self._set_to_cache(image_url, result)
        return result

    async def embed_text_as_multimodal(self, text: str) -> List[float]:
        """
        将纯文本通过多模态模型映射到 1024 维空间。
        用于没有图片的实体生成多模态向量，确保它们也能被图片搜索命中。
        输入格式: [{"text": "..."}]
        """
        cache_key = f"txt_mm_emb:v1:{hashlib.md5(text.encode()).hexdigest()}"
        data = await self.redis.get(cache_key)
        if data:
            embedding = pickle.loads(data)
            try:
                return ensure_embedding_dimensions(embedding, "多模态文本缓存")
            except ValueError:
                await self.redis.delete(cache_key)
                logger.warning("已删除维度不匹配的多模态文本向量缓存")

        embeddings = await self._call_api_with_retry([{"text": text}])
        result = embeddings[0]
        await self.redis.setex(cache_key, self.cache_ttl, pickle.dumps(result))
        return result

    async def embed_batch(self, image_urls: List[str]) -> List[List[float]]:
        """批量图片向量化。

        qwen2.5-vl-embedding 不允许单次请求出现多个 image（会报
        400 Duplicate input type, Each type can appear at most once），
        因此未命中缓存的图片逐张请求。
        """
        results: List[Optional[List[float]]] = []
        uncached_items: List[tuple[int, str]] = []

        for i, url in enumerate(image_urls):
            cached = await self._get_from_cache(url)
            if cached is not None:
                results.append(cached)
            else:
                results.append(None)
                uncached_items.append((i, url))

        for idx, url in uncached_items:
            new_embeddings = await self._call_api_with_retry([{"image": url}])
            emb = new_embeddings[0]
            results[idx] = emb
            await self._set_to_cache(url, emb)

        return results


_image_embedding: Optional[ImageEmbedding] = None


def get_image_embedding() -> ImageEmbedding:
    global _image_embedding
    if _image_embedding is None:
        _image_embedding = ImageEmbedding()
    return _image_embedding
