"""Read-only Neo4j readiness audit for GraphRAG evaluation gates."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Mapping

from dotenv import load_dotenv
from neo4j import GraphDatabase


COUNT_CODES = {
    "device_count": "device_nodes_missing",
    "component_count": "component_nodes_missing",
    "fault_count": "fault_nodes_missing",
    "owns_count": "owns_edges_missing",
    "causes_count": "causes_edges_missing",
    "complete_path_count": "complete_paths_missing",
}


def evaluate_readiness(
    metrics: Mapping[str, Any], policy: Mapping[str, Any]
) -> dict[str, Any]:
    normalized = dict(metrics)
    violations: list[dict[str, Any]] = []
    for metric, raw_threshold in dict(policy.get("minimum_counts") or {}).items():
        threshold = int(raw_threshold)
        actual = int(normalized.get(metric) or 0)
        if actual < threshold:
            violations.append(
                {
                    "code": COUNT_CODES.get(metric, f"{metric}_low"),
                    "metric": metric,
                    "actual": actual,
                    "required": threshold,
                }
            )
    for metric, raw_threshold in dict(policy.get("minimum_coverage") or {}).items():
        threshold = float(raw_threshold)
        actual = float(normalized.get(metric) or 0.0)
        if actual < threshold:
            violations.append(
                {
                    "code": f"{metric}_low",
                    "metric": metric,
                    "actual": actual,
                    "required": threshold,
                }
            )
    for metric, raw_threshold in dict(policy.get("maximum_counts") or {}).items():
        threshold = int(raw_threshold)
        actual = int(normalized.get(metric) or 0)
        if actual > threshold:
            violations.append(
                {
                    "code": f"{metric}_high",
                    "metric": metric,
                    "actual": actual,
                    "required": threshold,
                }
            )
    return {
        "ready": not violations,
        "metrics": normalized,
        "violations": violations,
    }


def collect_graph_metrics(driver, *, database: str, known_chunk_uids: set[str]) -> dict[str, Any]:
    scalar_queries = {
        "device_count": "MATCH (n:Device) RETURN count(n) AS value",
        "component_count": "MATCH (n:Component) RETURN count(n) AS value",
        "fault_count": "MATCH (n:Fault) RETURN count(n) AS value",
        "solution_count": "MATCH (n:Solution) RETURN count(n) AS value",
        "owns_count": "MATCH ()-[r:OWNS]->() RETURN count(r) AS value",
        "causes_count": "MATCH ()-[r:CAUSES]->() RETURN count(r) AS value",
        "has_solution_count": "MATCH ()-[r:HAS_SOLUTION]->() RETURN count(r) AS value",
        "complete_path_count": (
            "MATCH (:Device)-[:OWNS]->(:Component)-[:CAUSES]->(:Fault) "
            "RETURN count(*) AS value"
        ),
        "component_embedding_count": (
            "MATCH (n:Component) WHERE n.embedding IS NOT NULL "
            "AND size(n.embedding) > 0 RETURN count(n) AS value"
        ),
        "fault_embedding_count": (
            "MATCH (n:Fault) WHERE n.embedding IS NOT NULL "
            "AND size(n.embedding) > 0 RETURN count(n) AS value"
        ),
        "orphan_fault_count": (
            "MATCH (n:Fault) WHERE NOT (:Component)-[:CAUSES]->(n) "
            "RETURN count(n) AS value"
        ),
        "orphan_solution_count": (
            "MATCH (n:Solution) WHERE NOT (:Fault)-[:HAS_SOLUTION]->(n) "
            "RETURN count(n) AS value"
        ),
    }
    metrics: dict[str, Any] = {}
    with driver.session(database=database) as session:
        for name, query in scalar_queries.items():
            record = session.run(query).single()
            metrics[name] = int(record["value"] if record else 0)
        path_rows = list(
            session.run(
                """
                MATCH (d:Device)-[:OWNS]->(c:Component)-[:CAUSES]->(f:Fault)
                RETURN d.id AS deviceId, c.id AS componentId, f.id AS faultId,
                       f.document_id AS documentId, f.document_version AS documentVersion,
                       f.section_id AS sectionId,
                       coalesce(f.source_chunk_uids,
                         CASE WHEN f.source_chunk_uid IS NULL THEN [] ELSE [f.source_chunk_uid] END
                       ) AS sourceChunkUids
                """
            )
        )

    metrics["component_embedding_coverage"] = _coverage(
        metrics["component_embedding_count"], metrics["component_count"]
    )
    metrics["fault_embedding_coverage"] = _coverage(
        metrics["fault_embedding_count"], metrics["fault_count"]
    )
    identity_count = 0
    provenance_count = 0
    graph_chunk_uids: set[str] = set()
    for row in path_rows:
        if all(_text(row.get(key)) for key in ("deviceId", "componentId", "faultId")):
            identity_count += 1
        chunks = {_text(value) for value in row.get("sourceChunkUids", []) if _text(value)}
        graph_chunk_uids.update(chunks)
        if (
            _text(row.get("documentId"))
            and _text(row.get("documentVersion"))
            and _text(row.get("sectionId"))
            and chunks
        ):
            provenance_count += 1
    path_count = len(path_rows)
    metrics["path_identity_coverage"] = _coverage(identity_count, path_count)
    metrics["path_provenance_coverage"] = _coverage(provenance_count, path_count)
    metrics["graph_source_chunk_count"] = len(graph_chunk_uids)
    metrics["resolved_source_chunk_count"] = len(graph_chunk_uids & known_chunk_uids)
    metrics["chunk_round_trip_coverage"] = _coverage(
        metrics["resolved_source_chunk_count"], metrics["graph_source_chunk_count"]
    )
    return metrics


def load_known_chunk_uids(document_ids: set[str]) -> set[str]:
    if not document_ids:
        return set()
    from services.knowledge.vector_service import get_vector_service

    vector_service = get_vector_service()
    result: set[str] = set()
    for document_id in sorted(document_ids):
        for chunk in vector_service.list_document_chunks(document_id):
            metadata = chunk.get("metadata") or {}
            uid = _text(metadata.get("chunk_uid") or metadata.get("id"))
            if uid:
                result.add(uid)
    return result


def graph_document_ids(driver, *, database: str) -> set[str]:
    with driver.session(database=database) as session:
        records = session.run(
            "MATCH (n) WHERE n.document_id IS NOT NULL "
            "RETURN DISTINCT n.document_id AS documentId"
        )
        return {_text(record["documentId"]) for record in records if _text(record["documentId"])}


def load_policy(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit GraphRAG data readiness without writes.")
    parser.add_argument(
        "--policy",
        type=Path,
        default=Path("evaluation/datasets/graph_readiness_policy_v1.json"),
    )
    parser.add_argument("--out", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    load_dotenv()
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    username = os.getenv("NEO4J_USERNAME", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "")
    database = os.getenv("NEO4J_DATABASE", "neo4j")
    if not password:
        raise SystemExit("NEO4J_PASSWORD is required for readiness audit")
    driver = GraphDatabase.driver(uri, auth=(username, password))
    try:
        document_ids = graph_document_ids(driver, database=database)
        known_chunk_uids = load_known_chunk_uids(document_ids)
        metrics = collect_graph_metrics(
            driver,
            database=database,
            known_chunk_uids=known_chunk_uids,
        )
    finally:
        driver.close()
    report = evaluate_readiness(metrics, load_policy(args.policy))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ready"] else 2


def _coverage(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 6)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


if __name__ == "__main__":
    raise SystemExit(main())
