"""
向量化模块

文本、图片、图文混合三种向量化服务，统一使用 qwen2.5-vl-embedding，输出 1024 维向量。

三个服务分工：
- TextEmbedding      → 文本 → qwen2.5-vl-embedding
- ImageEmbedding     → 图片 → qwen2.5-vl-embedding
- MultimodalEmbedding → 图文混合 → 门面模式聚合上述两者
"""

from .text_embedding import TextEmbedding, get_text_embedding
from .image_embedding import ImageEmbedding, get_image_embedding
from .multimodal_embedding import MultimodalEmbedding, get_multimodal_embedding

__all__ = [
    "TextEmbedding",
    "get_text_embedding",
    "ImageEmbedding",
    "get_image_embedding",
    "MultimodalEmbedding",
    "get_multimodal_embedding",
]
