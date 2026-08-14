"""Prepare the local Neo4j runtime required by GraphRAG evaluation."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any, Sequence

from dotenv import load_dotenv
from neo4j import GraphDatabase

from embeddings.constants import EMBEDDING_DIMENSIONS


VECTOR_INDEX_DEFINITIONS = (
    ("fault_embedding_index", "Fault", "f", "embedding"),
    ("component_embedding_index", "Component", "c", "embedding"),
    ("fault_multimodal_index", "Fault", "f", "multimodal_embedding"),
    ("component_multimodal_index", "Component", "c", "multimodal_embedding"),
)

TEXT_INDEX_STATEMENTS = (
    "CREATE TEXT INDEX device_name_text_index IF NOT EXISTS FOR (d:Device) ON (d.name)",
    "CREATE TEXT INDEX component_name_text_index IF NOT EXISTS FOR (c:Component) ON (c.name)",
    "CREATE TEXT INDEX fault_name_text_index IF NOT EXISTS FOR (f:Fault) ON (f.name)",
    "CREATE TEXT INDEX solution_title_text_index IF NOT EXISTS FOR (s:Solution) ON (s.title)",
)

INDEX_STATEMENTS = (
    """CREATE VECTOR INDEX fault_embedding_index IF NOT EXISTS
       FOR (f:Fault) ON (f.embedding)
       OPTIONS {indexConfig: {`vector.dimensions`: 1024,
                              `vector.similarity_function`: 'cosine'}}""",
    """CREATE VECTOR INDEX component_embedding_index IF NOT EXISTS
       FOR (c:Component) ON (c.embedding)
       OPTIONS {indexConfig: {`vector.dimensions`: 1024,
                              `vector.similarity_function`: 'cosine'}}""",
    """CREATE VECTOR INDEX fault_multimodal_index IF NOT EXISTS
       FOR (f:Fault) ON (f.multimodal_embedding)
       OPTIONS {indexConfig: {`vector.dimensions`: 1024,
                              `vector.similarity_function`: 'cosine'}}""",
    """CREATE VECTOR INDEX component_multimodal_index IF NOT EXISTS
       FOR (c:Component) ON (c.multimodal_embedding)
       OPTIONS {indexConfig: {`vector.dimensions`: 1024,
                              `vector.similarity_function`: 'cosine'}}""",
    *TEXT_INDEX_STATEMENTS,
)


def _driver():
    return GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]),
    )


def target_embedding_dimensions() -> int:
    return EMBEDDING_DIMENSIONS


def ensure_indexes() -> list[dict[str, Any]]:
    driver = _driver()
    try:
        with driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as session:
            dimensions = target_embedding_dimensions()
            existing = {
                row["name"]: row
                for row in session.run(
                    "SHOW INDEXES YIELD name, type, options RETURN name, type, options"
                )
            }
            for name, label, variable, property_name in VECTOR_INDEX_DEFINITIONS:
                row = existing.get(name)
                configured = (
                    ((row or {}).get("options") or {})
                    .get("indexConfig", {})
                    .get("vector.dimensions")
                )
                if row and int(configured or 0) != dimensions:
                    session.run(f"DROP INDEX {name} IF EXISTS").consume()
                session.run(
                    f"CREATE VECTOR INDEX {name} IF NOT EXISTS "
                    f"FOR ({variable}:{label}) ON ({variable}.{property_name}) "
                    "OPTIONS {indexConfig: {`vector.dimensions`: $dimensions, "
                    "`vector.similarity_function`: 'cosine'}}",
                    dimensions=dimensions,
                ).consume()
            for statement in TEXT_INDEX_STATEMENTS:
                session.run(statement).consume()
            return session.run(
                "SHOW INDEXES YIELD name, type, state, options "
                "RETURN name, type, state, options ORDER BY name"
            ).data()
    finally:
        driver.close()


def graph_counts(document_id: str) -> dict[str, Any]:
    driver = _driver()
    try:
        with driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as session:
            labels = session.run(
                "MATCH (n) UNWIND labels(n) AS label "
                "RETURN label, count(*) AS count ORDER BY label"
            ).data()
            scoped = session.run(
                "MATCH (n) WHERE n.document_id = $document_id "
                "OR n.documentId = $document_id "
                "UNWIND labels(n) AS label "
                "RETURN label, count(*) AS count ORDER BY label",
                document_id=document_id,
            ).data()
            paths = session.run(
                "MATCH (d:Device)-[:OWNS]->(c:Component)-[:CAUSES]->(f:Fault) "
                "OPTIONAL MATCH (f)-[:HAS_SOLUTION]->(s:Solution) "
                "WHERE coalesce(d.document_id, d.documentId, c.document_id, "
                "c.documentId, f.document_id, f.documentId, s.document_id, "
                "s.documentId) = $document_id "
                "RETURN count(DISTINCT [d.id, c.id, f.id, s.id]) AS count",
                document_id=document_id,
            ).single()
            return {
                "labels": labels,
                "document_labels": scoped,
                "document_path_count": int(paths["count"] if paths else 0),
            }
    finally:
        driver.close()


async def extract_document(document_id: str, device_type: str) -> dict[str, Any]:
    from services.knowledge.manual_kg_extractor import ManualKGExtractor

    result = await ManualKGExtractor().extract_document(
        document_id=document_id,
        device_type_hint=device_type,
        manual_name="摩托车发动机维修手册",
    )
    if hasattr(result, "to_dict"):
        return result.to_dict()
    if hasattr(result, "__dict__"):
        return dict(result.__dict__)
    return {"result": str(result)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare local GraphRAG evaluation state.")
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--device-type", default="摩托车发动机")
    parser.add_argument("--extract", action="store_true")
    parser.add_argument("--report", default="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    report: dict[str, Any] = {
        "document_id": args.document_id,
        "indexes": ensure_indexes(),
        "before": graph_counts(args.document_id),
    }
    if args.extract:
        report["extraction"] = asyncio.run(
            extract_document(args.document_id, args.device_type)
        )
        report["indexes_after_extraction"] = ensure_indexes()
    report["after"] = graph_counts(args.document_id)
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
    "INDEX_STATEMENTS",
    "ensure_indexes",
    "extract_document",
    "graph_counts",
    "target_embedding_dimensions",
]
