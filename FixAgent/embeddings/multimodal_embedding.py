"""
多模态统一向量化模块

聚合文本向量化和图像向量化，对外提供统一入口。
底层统一使用 qwen2.5-vl-embedding，文本和图片向量在同一语义空间。

【与架构文档的对应关系】
- 位置：embeddings/multimodal_embedding.py
- 依赖：embeddings/text_embedding.py + embeddings/image_embedding.py
- 下游：tools/knowledge_retrieval_tool.py（多模态检索时使用）

【设计思路】
- 门面模式（Facade）：对外一个 embed()，内部分发到 TextEmbedding / ImageEmbedding
- 共用 qwen2.5-vl-embedding 模型，文本和图片向量维度统一（1024）
- 各自维护自己的 Redis 缓存，不重复造轮子

【使用示例】
```python
mm = get_multimodal_embedding()

# 纯文本
result = await mm.embed(text="轴承过热原因")

# 纯图片
result = await mm.embed(image_urls=["http://xxx/photo.jpg"])

# 图文混合
result = await mm.embed(
    text="轴承过热原因",
    image_urls=["http://xxx/photo.jpg", "http://xxx/diagram.png"]
)
# → {"text_vector": [...], "image_vectors": [[...], [...]], "dimensions": {...}}
```
"""

from typing import List, Optional, Dict, Any

from embeddings.text_embedding import get_text_embedding
from embeddings.image_embedding import get_image_embedding


class MultimodalEmbedding:
    """
    多模态统一向量化服务

    组合 TextEmbedding 和 ImageEmbedding，根据输入类型自动分发。
    纯文本走 text-embedding-v4，图片走 multimodal-embedding-v1。
    """

    def __init__(self):
        self.text_embedding = get_text_embedding()
        self.image_embedding = get_image_embedding()

    async def embed(
        self,
        text: Optional[str] = None,
        image_urls: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        统一向量化入口（单条文本 + 多张图片）

        Args:
            text: 文本内容，可选
            image_urls: 图片URL列表，可选

        Returns:
            {
                "text_vector": [0.12, ...] or None,
                "image_vectors": [[0.08, ...], ...] or [],
                "dimensions": {
                    "text": 1024 or None,
                    "image": 1024 or None
                }
            }
        """
        result: Dict[str, Any] = {
            "text_vector": None,
            "image_vectors": [],
            "dimensions": {"text": None, "image": None}
        }

        if text:
            vec = await self.text_embedding.embed(text)
            result["text_vector"] = vec
            result["dimensions"]["text"] = len(vec)

        if image_urls:
            vecs = await self.image_embedding.embed_batch(image_urls)
            result["image_vectors"] = vecs
            if vecs:
                result["dimensions"]["image"] = len(vecs[0])

        return result

    async def embed_batch(
        self,
        texts: Optional[List[str]] = None,
        image_urls: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        批量统一向量化

        Args:
            texts: 文本列表，可选
            image_urls: 图片URL列表，可选

        Returns:
            {
                "text_vectors": [[0.12, ...], ...] or [],
                "image_vectors": [[0.08, ...], ...] or [],
                "dimensions": {"text": 1024, "image": 1024}
            }
        """
        result: Dict[str, Any] = {
            "text_vectors": [],
            "image_vectors": [],
            "dimensions": {"text": None, "image": None}
        }

        if texts:
            vecs = await self.text_embedding.embed_batch(texts)
            result["text_vectors"] = vecs
            if vecs:
                result["dimensions"]["text"] = len(vecs[0])

        if image_urls:
            vecs = await self.image_embedding.embed_batch(image_urls)
            result["image_vectors"] = vecs
            if vecs:
                result["dimensions"]["image"] = len(vecs[0])

        return result


# 单例
_multimodal_embedding: Optional[MultimodalEmbedding] = None


def get_multimodal_embedding() -> MultimodalEmbedding:
    """获取多模态统一向量化服务单例"""
    global _multimodal_embedding
    if _multimodal_embedding is None:
        _multimodal_embedding = MultimodalEmbedding()
    return _multimodal_embedding
