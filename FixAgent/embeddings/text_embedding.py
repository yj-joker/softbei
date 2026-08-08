"""
文本向量化模块

使用阿里云百炼 qwen2.5-vl-embedding 将文本转为高维向量。
与 image_embedding.py 共用同一模型，确保文本和图片向量在同一语义空间。

【模型信息】
- 模型: qwen2.5-vl-embedding
- 固定维度: 1024
- 输入格式: [{"text": "..."}]

【并发与连接（2026-06 优化）】
- 改用 dashscope 异步 API(AioMultiModalEmbedding) + 注入复用的 aiohttp 连接池，
  连接 keep-alive 复用，避免高并发下「每请求各自新建 TLS 连接」触发的偶发
  连接重置(10054)/连接超时——这是此前导入既慢又失败的根因。
- 原生异步，去掉 asyncio.to_thread 线程池；redis 缓存改异步，不阻塞事件循环。
- 重试覆盖网络层异常(连接重置/超时)与 5xx/429，仅 4xx 参数错误不重试。
"""

import asyncio
import hashlib
import logging
import pickle
import random
from typing import Optional, List

import aiohttp
import dashscope
import redis.asyncio as aioredis

try:
    from dashscope.embeddings.multimodal_embedding import AioMultiModalEmbedding
except ImportError:
    # Older DashScope releases only expose the synchronous implementation.
    # Keep the application importable and run that implementation in a worker
    # thread so it does not block FastAPI's event loop.
    AioMultiModalEmbedding = None

from config.settings import get_settings
from embeddings.constants import EMBEDDING_DIMENSIONS, ensure_embedding_dimensions
from embeddings.rate_limiter import get_embedding_rate_limiter

logger = logging.getLogger(__name__)


class TextEmbedding:
    """文本向量化服务，使用 qwen2.5-vl-embedding 统一模型。"""

    _RETRY_DELAYS = (0.5, 1.0)
    _MAX_CONNECTIONS = 12  # aiohttp 连接池上限：覆盖 embedding 段并发并复用连接

    def __init__(self):
        self.settings = get_settings()
        self.model = "qwen2.5-vl-embedding"
        self.dimensions = EMBEDDING_DIMENSIONS
        dashscope.api_key = self.settings.dashscope_api_key
        self.redis = aioredis.Redis(
            host=self.settings.redis_host,
            port=self.settings.redis_port,
            password=self.settings.redis_password,
            db=self.settings.redis_db,
            decode_responses=False,
        )
        self.cache_ttl = self.settings.redis_ttl
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        """懒加载复用的 aiohttp 连接池 session（必须在事件循环内创建）。"""
        if self._session is not None and not self._session.closed:
            return self._session
        async with self._session_lock:
            if self._session is None or self._session.closed:
                connector = aiohttp.TCPConnector(
                    limit=self._MAX_CONNECTIONS,
                    limit_per_host=self._MAX_CONNECTIONS,
                    ttl_dns_cache=300,
                    enable_cleanup_closed=True,
                )
                timeout = aiohttp.ClientTimeout(total=60, connect=10)
                self._session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        return self._session

    def _get_cache_key(self, text: str) -> str:
        return f"cache:emb:text:v2:{hashlib.md5(text.encode()).hexdigest()}"

    async def _get_from_cache(self, text: str) -> Optional[List[float]]:
        cache_key = self._get_cache_key(text)
        data = await self.redis.get(cache_key)
        if data:
            embedding = pickle.loads(data)
            try:
                return ensure_embedding_dimensions(embedding, "文本缓存")
            except ValueError:
                await self.redis.delete(cache_key)
                logger.warning("已删除维度不匹配的文本向量缓存")
        return None

    async def _set_to_cache(self, text: str, embedding: List[float]) -> None:
        await self.redis.setex(self._get_cache_key(text), self.cache_ttl, pickle.dumps(embedding))

    async def _call_api_async(self, inputs: List[dict]) -> List[List[float]]:
        """异步调用 dashscope MultiModalEmbedding，复用注入的 aiohttp 连接池。"""
        await get_embedding_rate_limiter().acquire()  # 全局限速：平滑突发，避免瞬时超百炼限流(429)
        if AioMultiModalEmbedding is None:
            logger.warning(
                "Installed DashScope does not provide AioMultiModalEmbedding; "
                "falling back to the synchronous API in a worker thread"
            )
            resp = await asyncio.to_thread(
                dashscope.MultiModalEmbedding.call,
                model=self.model,
                input=inputs,
                api_key=self.settings.dashscope_api_key,
            )
        else:
            session = await self._get_session()
            resp = await AioMultiModalEmbedding.call(
                model=self.model,
                input=inputs,
                api_key=self.settings.dashscope_api_key,
                session=session,
            )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Embedding API 返回错误 code={resp.status_code} message={resp.message}"
            )

        if resp.output and "embeddings" in resp.output:
            embeddings = sorted(resp.output["embeddings"], key=lambda x: x.get("index", 0))
            result = [
                ensure_embedding_dimensions(e["embedding"], "文本模型返回")
                for e in embeddings
            ]
            if result:
                logger.debug(f"文本向量化完成 模型={self.model} 维度={len(result[0])}")
            return result

        raise ValueError(f"Embedding API 响应格式异常: {resp}")

    async def _call_api_with_retry(self, inputs: List[dict]) -> List[List[float]]:
        for attempt in range(len(self._RETRY_DELAYS) + 1):
            try:
                return await self._call_api_async(inputs)
            except Exception as exc:
                # 教训：原实现只 catch RuntimeError，漏掉了网络层异常(连接重置/超时)，
                # 导致瞬时抖动一次就让整段导入失败。这里改为捕获所有异常退避重试，
                # 仅对 4xx 参数类错误(非 429，如输入超限)立即抛出——重试也不会变好。
                message = str(exc)
                if isinstance(exc, RuntimeError) and "code=4" in message and "code=429" not in message:
                    raise
                if attempt >= len(self._RETRY_DELAYS):
                    raise
                delay = self._RETRY_DELAYS[attempt] + random.uniform(0, self._RETRY_DELAYS[attempt])
                logger.warning(
                    "Embedding API temporary failure, retrying in %.1fs (attempt %s/%s): %s",
                    delay,
                    attempt + 2,
                    len(self._RETRY_DELAYS) + 1,
                    exc,
                )
                await asyncio.sleep(delay)

    async def embed(self, text: str) -> List[float]:
        """单条文本向量化。"""
        cached = await self._get_from_cache(text)
        if cached is not None:
            return cached

        embeddings = await self._call_api_with_retry([{"text": text}])
        result = embeddings[0]
        await self._set_to_cache(text, result)
        return result

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """批量文本向量化。

        qwen2.5-vl-embedding 的 MultiModalEmbedding 调用中同一种输入类型
        不能重复出现，因此未命中缓存的文本需要逐条请求。
        """
        results: List[Optional[List[float]]] = []
        uncached_items: List[tuple[int, str]] = []

        for i, text in enumerate(texts):
            cached = await self._get_from_cache(text)
            if cached is not None:
                results.append(cached)
            else:
                results.append(None)
                uncached_items.append((i, text))

        for idx, text in uncached_items:
            new_embeddings = await self._call_api_with_retry([{"text": text}])
            emb = new_embeddings[0]
            results[idx] = emb
            await self._set_to_cache(text, emb)

        return results


_text_embedding: Optional[TextEmbedding] = None


def get_text_embedding() -> TextEmbedding:
    global _text_embedding
    if _text_embedding is None:
        _text_embedding = TextEmbedding()
    return _text_embedding
