"""Embedding contracts shared by text, image, and vector storage paths."""

from typing import Sequence, TypeVar


EMBEDDING_DIMENSIONS = 1024

Vector = TypeVar("Vector", bound=Sequence[float])


def ensure_embedding_dimensions(vector: Vector, source: str) -> Vector:
    """Reject vectors that cannot be stored or queried by the 1024-d indexes."""
    try:
        actual = len(vector)
    except TypeError as exc:
        raise ValueError(f"{source}向量格式异常") from exc
    if actual != EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"{source}向量维度异常，期望{EMBEDDING_DIMENSIONS}，实际{actual}"
        )
    return vector
