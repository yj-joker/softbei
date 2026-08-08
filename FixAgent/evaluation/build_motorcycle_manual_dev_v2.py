"""Build the primary motorcycle-engine GraphRAG development set."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from neo4j import GraphDatabase


DOCUMENT_ID = "kdoc_2084935345958137858"
DOCUMENT_VERSION = "v1"
SOURCE_PDF = "摩托车发动机维修手册.pdf"
ROOT = Path(__file__).resolve().parent
SOURCE_DATASET = ROOT / "maintenance_eval_dataset_v1.jsonl"
OUTPUT_DATASET = ROOT / "datasets" / "motorcycle_engine_manual_graphrag_dev_v2.jsonl"
_ACTION_SUFFIXES = ("更换", "修复", "调整", "清洗", "紧固", "检查")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _question_type(task_type: str) -> str:
    return {
        "procedure": "procedure",
        "diagnosis": "multi_hop",
        "no_answer": "fact",
    }.get(task_type, "fact")


def _solution_answer_patterns(solution: str) -> list[str]:
    patterns = [solution]
    for action in _ACTION_SUFFIXES:
        if not solution.endswith(action):
            continue
        subject = solution[: -len(action)].strip()
        if len(subject) >= 2 and not subject.endswith(("或", "和", "及", "并")):
            patterns.append(f"{action}{subject}")
        break
    return list(dict.fromkeys(patterns))


def _manual_rows() -> list[dict[str, Any]]:
    output = []
    for index, source in enumerate(_read_jsonl(SOURCE_DATASET), start=1):
        row = dict(source)
        row.update(
            {
                "schema_version": "2.0",
                "case_id": f"motorcycle_manual_v2_vector_{index:03d}",
                "legacy_case_id": source["case_id"],
                "split": "dev",
                "question_type": _question_type(str(source.get("task_type") or "")),
                "graph_dependency": "none",
                "input_modality": "text",
                "image_inputs": [],
                "question_origin": "pdf_validated_primary_manual_v1",
                "document_id": DOCUMENT_ID,
                "document_version": DOCUMENT_VERSION,
                "device_type": "摩托车发动机",
                "source_pdf": SOURCE_PDF,
                "expected_scope": "in_scope",
                "expected_coverage_status": (
                    "complete" if source.get("answerable", True) else "unsupported"
                ),
                "group": "motorcycle_manual_vector",
            }
        )
        output.append(row)
    return output


def _graph_paths() -> list[dict[str, Any]]:
    load_dotenv()
    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]),
    )
    query = """
        MATCH (d:Device)-[:OWNS]->(c:Component)-[:CAUSES]->(f:Fault)
              -[:HAS_SOLUTION]->(s:Solution)
        WHERE f.document_id = $documentId
        RETURN d.id AS deviceId, d.name AS device,
               c.id AS componentId, c.name AS component,
               f.id AS faultId, f.name AS fault, f.description AS sourceFact,
               s.id AS solutionId, s.title AS solution,
               f.document_version AS documentVersion,
               f.page_start AS pageStart, f.page_end AS pageEnd,
               coalesce(f.source_chunk_uids,
                   CASE WHEN f.source_chunk_uid IS NULL THEN [] ELSE [f.source_chunk_uid] END
               ) AS chunkUids
        ORDER BY pageStart, component, fault
    """
    try:
        with driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as session:
            return [dict(record) for record in session.run(query, documentId=DOCUMENT_ID)]
    finally:
        driver.close()


def _graph_rows() -> list[dict[str, Any]]:
    rows = []
    for index, path in enumerate(_graph_paths(), start=1):
        component = str(path["component"])
        fault = str(path["fault"])
        solution = str(path["solution"])
        pages = list(
            range(
                int(path["pageStart"]),
                int(path.get("pageEnd") or path["pageStart"]) + 1,
            )
        )
        chunk_uids = list(path.get("chunkUids") or [])
        path_id = f"kgpath:{path['deviceId']}:{path['componentId']}:{path['faultId']}"
        graph_source = {
            "source_type": "graph",
            "document_id": DOCUMENT_ID,
            "document_version": path.get("documentVersion") or DOCUMENT_VERSION,
            "pages": pages,
            "chunk_ids": chunk_uids,
            "node_ids": [path["deviceId"], path["componentId"], path["faultId"]],
            "relationship_types": ["OWNS", "CAUSES"],
            "path_ids": [path_id],
        }
        manual_source = {
            "source_type": "manual",
            "document_id": DOCUMENT_ID,
            "document_version": path.get("documentVersion") or DOCUMENT_VERSION,
            "pages": pages,
            "chunk_ids": chunk_uids,
        }
        rows.append(
            {
                "schema_version": "2.0",
                "case_id": f"motorcycle_manual_v2_graph_{index:03d}",
                "split": "dev",
                "query": f"摩托车发动机的{component}出现“{fault}”时应如何处理？请说明故障所属部件和手册依据。",
                "question_type": "multi_hop" if index % 3 == 0 else "relation_disambiguation",
                "graph_dependency": "required",
                "input_modality": "text",
                "image_inputs": [],
                "question_origin": "stable_graph_path_and_primary_manual",
                "task_type": "diagnosis",
                "intent_action": "诊断",
                "answerable": True,
                "required_nuggets": [component, fault, solution],
                "forbidden_claims": [],
                "difficulty": "hard" if index % 3 == 0 else "medium",
                "group": component,
                "document_id": DOCUMENT_ID,
                "document_version": path.get("documentVersion") or DOCUMENT_VERSION,
                "device_type": "摩托车发动机",
                "source_pdf": SOURCE_PDF,
                "expected_scope": "in_scope",
                "expected_coverage_status": "complete",
                "claim_constraints": [
                    {
                        "claim_id": "graph_relation",
                        "answer_patterns": [component, fault],
                        "evidence_patterns": [component, fault],
                        "allowed_sources": [graph_source],
                    },
                    {
                        "claim_id": "manual_solution",
                        "answer_patterns": _solution_answer_patterns(solution),
                        "evidence_patterns": [solution, str(path.get("sourceFact") or "")],
                        "allowed_sources": [manual_source],
                    },
                ],
                "gold_evidence": [
                    {
                        "page": int(path["pageStart"]),
                        "text": str(path.get("sourceFact") or ""),
                        "relevance_grade": 3,
                        "supports": [component, fault, solution],
                    }
                ],
            }
        )
    return rows


def build() -> list[dict[str, Any]]:
    rows = _graph_rows() + _manual_rows()
    OUTPUT_DATASET.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )
    return rows


if __name__ == "__main__":
    built = build()
    print(json.dumps({"path": str(OUTPUT_DATASET), "case_count": len(built)}, ensure_ascii=False))
