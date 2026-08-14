"""Migrate existing Neo4j graph embeddings to the shared 1024-d contract."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

import httpx
from dotenv import load_dotenv
from neo4j import GraphDatabase

from embeddings.constants import EMBEDDING_DIMENSIONS
from embeddings.text_embedding import get_text_embedding
from evaluation.graph_runtime_setup import (
    VECTOR_INDEX_DEFINITIONS,
    ensure_indexes,
    graph_counts,
)


class AsyncEmbedder(Protocol):
    async def embed(self, text: str) -> list[float]: ...


class TextEmbeddingV4Client:
    """Match the Java ``EmbeddingUtils`` text vector contract exactly."""

    def __init__(self, api_key: str, *, timeout: float = 60.0) -> None:
        if not api_key.strip():
            raise ValueError("DASHSCOPE_API_KEY is required")
        self.api_key = api_key.strip()
        self.client = httpx.AsyncClient(
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            timeout=timeout,
        )

    async def embed(self, text: str) -> list[float]:
        response = await self.client.post(
            "/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": "text-embedding-v4",
                "input": text,
                "dimensions": EMBEDDING_DIMENSIONS,
                "encoding_format": "float",
            },
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, Mapping) else None
        if not isinstance(data, list) or not data:
            raise ValueError("text-embedding-v4 response has no data")
        embedding = data[0].get("embedding") if isinstance(data[0], Mapping) else None
        if not isinstance(embedding, list):
            raise ValueError("text-embedding-v4 response has no embedding")
        vector = [float(value) for value in embedding]
        _validate_vector("text-embedding-v4", "embedding", vector)
        return vector

    async def aclose(self) -> None:
        await self.client.aclose()


def _text(value: Any) -> str:
    return str(value or "").strip()


def build_node_text(node: Mapping[str, Any]) -> str:
    label = _text(node.get("label"))
    name = _text(node.get("name"))
    if label == "Component":
        return f"部件名称：{name}\n规格参数：{_text(node.get('specification'))}"
    if label == "Fault":
        return f"故障名称：{name}\n故障描述：{_text(node.get('description'))}"
    raise ValueError(f"unsupported graph label: {label or '<empty>'}")


def _validate_vector(node_id: str, field: str, vector: Any) -> None:
    actual = len(vector) if isinstance(vector, Sequence) else 0
    if actual != EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"{node_id} {field} dimensions expected {EMBEDDING_DIMENSIONS}, actual {actual}"
        )


def validate_migration_rows(rows: Sequence[Mapping[str, Any]]) -> None:
    seen: set[str] = set()
    for row in rows:
        node_id = _text(row.get("id"))
        if not node_id:
            raise ValueError("migration row has empty node id")
        if node_id in seen:
            raise ValueError(f"duplicate node id: {node_id}")
        seen.add(node_id)
        _validate_vector(node_id, "embedding", row.get("embedding"))
        _validate_vector(
            node_id,
            "multimodal_embedding",
            row.get("multimodal_embedding"),
        )


async def generate_migration_rows(
    nodes: Sequence[Mapping[str, Any]],
    *,
    text_embedder: AsyncEmbedder,
    multimodal_embedder: AsyncEmbedder,
    concurrency: int = 6,
) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(max(1, int(concurrency)))

    async def generate(node: Mapping[str, Any]) -> dict[str, Any]:
        async with semaphore:
            embedding_text = build_node_text(node)
            text_vector, multimodal_vector = await asyncio.gather(
                text_embedder.embed(embedding_text),
                multimodal_embedder.embed(embedding_text),
            )
            return {
                "id": _text(node.get("id")),
                "embedding": list(text_vector),
                "multimodal_embedding": list(multimodal_vector),
            }

    rows = list(await asyncio.gather(*(generate(node) for node in nodes)))
    validate_migration_rows(rows)
    return rows


def _driver():
    return GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]),
    )


def fetch_nodes(driver: Any, document_id: str) -> list[dict[str, Any]]:
    query = """
        MATCH (n)
        WHERE (n:Component OR n:Fault)
          AND ($document_id = '' OR n.document_id = $document_id)
        RETURN CASE WHEN n:Component THEN 'Component' ELSE 'Fault' END AS label,
               n.id AS id,
               n.name AS name,
               n.specification AS specification,
               n.description AS description
        ORDER BY label, id
    """
    with driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as session:
        return session.run(query, document_id=document_id).data()


def audit_dimensions(driver: Any, document_id: str) -> list[dict[str, Any]]:
    query = """
        MATCH (n)
        WHERE (n:Component OR n:Fault)
          AND ($document_id = '' OR n.document_id = $document_id)
        RETURN CASE WHEN n:Component THEN 'Component' ELSE 'Fault' END AS label,
               size(n.embedding) AS embedding_dimensions,
               size(n.multimodal_embedding) AS multimodal_dimensions,
               count(*) AS count
        ORDER BY label, embedding_dimensions, multimodal_dimensions
    """
    with driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as session:
        return session.run(query, document_id=document_id).data()


def apply_migration_rows(
    driver: Any,
    rows: Sequence[Mapping[str, Any]],
    document_id: str,
) -> int:
    validate_migration_rows(rows)
    query = """
        UNWIND $rows AS row
        MATCH (n {id: row.id})
        WHERE (n:Component OR n:Fault)
          AND ($document_id = '' OR n.document_id = $document_id)
        SET n.embedding = row.embedding,
            n.multimodal_embedding = row.multimodal_embedding,
            n.embedding_dimensions = $dimensions
        RETURN count(n) AS updated
    """

    def update(tx: Any) -> int:
        result = tx.run(
            query,
            rows=[dict(row) for row in rows],
            document_id=document_id,
            dimensions=EMBEDDING_DIMENSIONS,
        ).single()
        updated = int(result["updated"] if result else 0)
        if updated != len(rows):
            raise RuntimeError(
                f"embedding migration updated {updated} nodes, expected {len(rows)}"
            )
        return updated

    with driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as session:
        return int(session.execute_write(update))


def wait_for_indexes_online(driver: Any, *, timeout_seconds: float = 120.0) -> list[dict[str, Any]]:
    names = [definition[0] for definition in VECTOR_INDEX_DEFINITIONS]
    deadline = time.monotonic() + max(1.0, timeout_seconds)
    latest: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        with driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as session:
            latest = session.run(
                "SHOW INDEXES YIELD name, type, state, options "
                "WHERE name IN $names RETURN name, type, state, options ORDER BY name",
                names=names,
            ).data()
        by_name = {str(row.get("name")): row for row in latest}
        if len(by_name) == len(names):
            invalid_dimensions = [
                name
                for name, row in by_name.items()
                if int(
                    ((row.get("options") or {}).get("indexConfig") or {}).get(
                        "vector.dimensions", 0
                    )
                )
                != EMBEDDING_DIMENSIONS
            ]
            failed = [name for name, row in by_name.items() if row.get("state") == "FAILED"]
            if failed:
                raise RuntimeError(f"vector indexes failed: {failed}")
            if not invalid_dimensions and all(
                row.get("state") == "ONLINE" for row in by_name.values()
            ):
                return latest
        time.sleep(0.5)
    raise TimeoutError(f"vector indexes did not become ONLINE: {latest}")


async def _close_multimodal_embedder(embedder: Any) -> None:
    session = getattr(embedder, "_session", None)
    if session is not None and not getattr(session, "closed", True):
        await session.close()
    redis = getattr(embedder, "redis", None)
    if redis is not None and hasattr(redis, "aclose"):
        await redis.aclose()


async def migrate(document_id: str, *, concurrency: int) -> dict[str, Any]:
    driver = _driver()
    text_embedder = TextEmbeddingV4Client(os.environ.get("DASHSCOPE_API_KEY", ""))
    multimodal_embedder = get_text_embedding()
    try:
        nodes = fetch_nodes(driver, document_id)
        if not nodes:
            raise RuntimeError(f"no Component or Fault nodes found for {document_id}")
        rows = await generate_migration_rows(
            nodes,
            text_embedder=text_embedder,
            multimodal_embedder=multimodal_embedder,
            concurrency=concurrency,
        )
        updated = apply_migration_rows(driver, rows, document_id)
        ensure_indexes()
        indexes = wait_for_indexes_online(driver)
        return {
            "generated_vector_count": len(rows) * 2,
            "updated_node_count": updated,
            "indexes": indexes,
        }
    finally:
        await text_embedder.aclose()
        await _close_multimodal_embedder(multimodal_embedder)
        driver.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--report", default="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    driver = _driver()
    try:
        before = audit_dimensions(driver, args.document_id)
        counts_before = graph_counts(args.document_id)
    finally:
        driver.close()

    report: dict[str, Any] = {
        "document_id": args.document_id,
        "target_dimensions": EMBEDDING_DIMENSIONS,
        "applied": bool(args.apply),
        "before": before,
        "counts_before": counts_before,
    }
    if args.apply:
        report["migration"] = asyncio.run(
            migrate(args.document_id, concurrency=args.concurrency)
        )
        driver = _driver()
        try:
            report["after"] = audit_dimensions(driver, args.document_id)
            report["counts_after"] = graph_counts(args.document_id)
        finally:
            driver.close()

    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "TextEmbeddingV4Client",
    "apply_migration_rows",
    "audit_dimensions",
    "build_node_text",
    "fetch_nodes",
    "generate_migration_rows",
    "migrate",
    "validate_migration_rows",
    "wait_for_indexes_online",
]
