"""Dry-run-first migration for pre-identity manual graph artifacts."""

from __future__ import annotations

import argparse
import json
import os

from dotenv import load_dotenv
from neo4j import GraphDatabase


COUNT_QUERY = """
MATCH (d:Device)
WHERE coalesce(d.identity_key, '') = ''
  AND (d.source = 'manual' OR size(coalesce(d.manual_ids, [])) > 0)
  AND NOT EXISTS { MATCH (d)-[:OWNS]->(:Component)-[:CAUSES]->(:Fault) }
OPTIONAL MATCH (d)-[:OWNS]->(c:Component)
WHERE NOT (c)-[:CAUSES]->(:Fault)
  AND c.source = 'manual'
  AND coalesce(c.verified, false) = false
  AND c.source_task_id IS NULL
WITH collect(DISTINCT d) AS devices, collect(DISTINCT c) AS components
OPTIONAL MATCH (s:Solution)
WHERE NOT (s)<-[:HAS_SOLUTION]-(:Fault)
  AND s.source = 'manual'
  AND coalesce(s.verified, false) = false
  AND s.source_task_id IS NULL
RETURN size(devices) AS legacy_devices,
       size([c IN components WHERE c IS NOT NULL]) AS legacy_components,
       count(DISTINCT s) AS orphan_manual_solutions
"""

COPY_EMBEDDINGS_QUERY = """
MATCH (oldD:Device)-[:OWNS]->(oldC:Component)
WHERE coalesce(oldD.identity_key, '') = ''
  AND (oldD.source = 'manual' OR size(coalesce(oldD.manual_ids, [])) > 0)
  AND NOT EXISTS { MATCH (oldD)-[:OWNS]->(:Component)-[:CAUSES]->(:Fault) }
  AND NOT (oldC)-[:CAUSES]->(:Fault)
  AND oldC.source = 'manual'
  AND coalesce(oldC.verified, false) = false
  AND oldC.source_task_id IS NULL
MATCH (newD:Device)-[:OWNS]->(newC:Component)
WHERE coalesce(newD.identity_key, '') <> ''
  AND newD.document_id = oldD.document_id
  AND newC.name = oldC.name
SET newC.embedding = coalesce(newC.embedding, oldC.embedding),
    newC.multimodal_embedding = coalesce(newC.multimodal_embedding, oldC.multimodal_embedding)
RETURN count(DISTINCT newC) AS embeddings_migrated
"""

DELETE_ORPHANS_QUERY = """
MATCH (s:Solution)
WHERE NOT (s)<-[:HAS_SOLUTION]-(:Fault)
  AND s.source = 'manual'
  AND coalesce(s.verified, false) = false
  AND s.source_task_id IS NULL
WITH collect(s) AS nodes, count(s) AS deleted
FOREACH (node IN nodes | DETACH DELETE node)
RETURN deleted
"""

DELETE_LEGACY_QUERY = """
MATCH (d:Device)
WHERE coalesce(d.identity_key, '') = ''
  AND (d.source = 'manual' OR size(coalesce(d.manual_ids, [])) > 0)
  AND NOT EXISTS { MATCH (d)-[:OWNS]->(:Component)-[:CAUSES]->(:Fault) }
OPTIONAL MATCH (d)-[:OWNS]->(c:Component)
WITH d, [c IN collect(DISTINCT c) WHERE c IS NOT NULL] AS components
WHERE all(c IN components WHERE
  NOT (c)-[:CAUSES]->(:Fault)
  AND c.source = 'manual'
  AND coalesce(c.verified, false) = false
  AND c.source_task_id IS NULL
)
WITH collect(DISTINCT d) AS devices, reduce(all_components = [], item IN collect(components) | all_components + item) AS components
FOREACH (node IN components | DETACH DELETE node)
FOREACH (node IN devices | DETACH DELETE node)
RETURN size(devices) AS devices_deleted, size(components) AS components_deleted
"""


def run(*, apply: bool) -> dict:
    load_dotenv()
    driver = GraphDatabase.driver(
        os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        auth=(os.getenv("NEO4J_USERNAME", "neo4j"), os.getenv("NEO4J_PASSWORD", "")),
    )
    database = os.getenv("NEO4J_DATABASE", "neo4j")
    try:
        with driver.session(database=database) as session:
            before = dict(session.run(COUNT_QUERY).single())
            devices = session.run(
                "MATCH (d:Device) OPTIONAL MATCH (d)-[:OWNS]->(c:Component) "
                "RETURN d.id AS id,d.name AS name,d.identity_key AS identity_key,"
                "d.manual_ids AS manual_ids,count(c) AS component_count"
            ).data()
            if not apply:
                return {"applied": False, "before": before, "devices": devices}
            migrated = dict(session.run(COPY_EMBEDDINGS_QUERY).single())
            orphan_result = dict(session.run(DELETE_ORPHANS_QUERY).single())
            legacy_result = dict(session.run(DELETE_LEGACY_QUERY).single())
            after = dict(session.run(COUNT_QUERY).single())
            return {
                "applied": True,
                "before": before,
                **migrated,
                **orphan_result,
                **legacy_result,
                "after": after,
            }
    finally:
        driver.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(apply=args.apply), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
