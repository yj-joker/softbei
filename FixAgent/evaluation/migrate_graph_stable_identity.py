"""Backfill deterministic identities for a manual graph without re-extracting it."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from neo4j import GraphDatabase


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", _text(value))).strip()


def _canonical(*values: Any) -> str:
    return "|".join(_normalize(value) for value in values).lower()


def stable_node_id(document_id: str, document_version: str, node_type: str, identity_key: str) -> str:
    normalized_type = _normalize(node_type).lower()
    raw = "\x1f".join((
        _normalize(document_id),
        _normalize(document_version).lower(),
        normalized_type,
        _normalize(identity_key),
    ))
    return f"kg:{normalized_type}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def stable_path_id(device_stable_id: str, component_stable_id: str, fault_stable_id: str) -> str:
    raw = "\x1f".join((_normalize(device_stable_id), _normalize(component_stable_id), _normalize(fault_stable_id)))
    return f"kgpath:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def build_stable_identity_rows(
    records: Sequence[Mapping[str, Any]],
    *,
    document_id: str,
    document_version: str,
) -> list[dict[str, str]]:
    revision = f"manual:{_normalize(document_id)}:{_normalize(document_version)}:stable-v1"
    rows: list[dict[str, str]] = []
    for record in records:
        device_stable_id = stable_node_id(
            document_id, document_version, "device",
            _canonical(record.get("device_name"), record.get("device_model"), record.get("device_manufacturer")),
        )
        component_stable_id = stable_node_id(
            document_id, document_version, "component",
            _canonical(record.get("component_name"), record.get("component_type"), record.get("component_specification")),
        )
        fault_stable_id = stable_node_id(
            document_id, document_version, "fault",
            _canonical(component_stable_id, record.get("fault_name"), record.get("fault_description")),
        )
        solution_stable_id = stable_node_id(
            document_id, document_version, "solution",
            _canonical(fault_stable_id, record.get("solution_title"), record.get("solution_description")),
        )
        rows.append({
            "device_id": _text(record.get("device_id")),
            "component_id": _text(record.get("component_id")),
            "fault_id": _text(record.get("fault_id")),
            "solution_id": _text(record.get("solution_id")),
            "device_stable_id": device_stable_id,
            "component_stable_id": component_stable_id,
            "fault_stable_id": fault_stable_id,
            "solution_stable_id": solution_stable_id,
            "path_stable_id": stable_path_id(device_stable_id, component_stable_id, fault_stable_id),
            "graph_revision": revision,
        })
    return rows


FETCH_QUERY = """
MATCH (d:Device)-[:OWNS]->(c:Component)-[:CAUSES]->(f:Fault)
WHERE coalesce(c.document_id, d.document_id, f.document_id) = $document_id
OPTIONAL MATCH (f)-[:HAS_SOLUTION]->(s:Solution)
RETURN d.id AS device_id, d.name AS device_name, d.model AS device_model,
       d.manufacturer AS device_manufacturer, c.id AS component_id,
       c.name AS component_name, c.component_type AS component_type,
       c.specification AS component_specification, f.id AS fault_id,
       f.name AS fault_name, f.description AS fault_description,
       s.id AS solution_id, s.title AS solution_title,
       s.description AS solution_description
ORDER BY device_id, component_id, fault_id, solution_id
"""


APPLY_PATHS_QUERY = """
UNWIND $rows AS row
MATCH (d:Device {id: row.device_id})-[:OWNS]->(c:Component {id: row.component_id})
MATCH (c)-[causes:CAUSES]->(f:Fault {id: row.fault_id})
SET d.stable_id = row.device_stable_id,
    c.stable_id = row.component_stable_id,
    f.stable_id = row.fault_stable_id,
    d.graph_revision = row.graph_revision,
    c.graph_revision = row.graph_revision,
    f.graph_revision = row.graph_revision,
    causes.path_stable_id = row.path_stable_id,
    causes.graph_revision = row.graph_revision
RETURN count(DISTINCT causes) AS paths
"""


APPLY_SOLUTIONS_QUERY = """
UNWIND $rows AS row
WITH row
WHERE row.solution_id <> ''
MATCH (s:Solution {id: row.solution_id})
SET s.stable_id = row.solution_stable_id,
    s.graph_revision = row.graph_revision
RETURN count(DISTINCT s) AS solutions
"""


def _driver() -> Any:
    return GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]),
    )


def migrate(document_id: str, document_version: str, *, apply: bool) -> dict[str, Any]:
    driver = _driver()
    try:
        with driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as session:
            source_rows = session.run(FETCH_QUERY, document_id=document_id).data()
            rows = build_stable_identity_rows(
                source_rows, document_id=document_id, document_version=document_version
            )
            updated_paths = 0
            updated_solutions = 0
            if apply and rows:
                updated_paths = int(
                    session.run(APPLY_PATHS_QUERY, rows=rows).single()["paths"]
                )
                updated_solutions = int(
                    session.run(APPLY_SOLUTIONS_QUERY, rows=rows).single()["solutions"]
                )
            return {
                "document_id": document_id,
                "document_version": document_version,
                "graph_revision": f"manual:{document_id}:{document_version}:stable-v1",
                "applied": apply,
                "source_path_count": len(rows),
                "updated_path_count": updated_paths,
                "updated_solution_count": updated_solutions,
            }
    finally:
        driver.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--document-version", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", default="")
    args = parser.parse_args(argv)
    load_dotenv()
    report = migrate(args.document_id, args.document_version, apply=args.apply)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        output = Path(args.report)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
