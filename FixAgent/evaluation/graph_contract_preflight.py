"""Preflight stable GraphRAG evidence contracts against a graph snapshot."""

from __future__ import annotations

import argparse
import json
import os
import unicodedata
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from evaluation.maintenance_eval_schema import (
    AllowedSource,
    MaintenanceEvalCase,
    read_jsonl_datasets,
)


GRAPH_SNAPSHOT_QUERY = """
MATCH (d:Device)-[:OWNS]->(c:Component)-[causes:CAUSES]->(f:Fault)
WHERE coalesce(c.document_id, d.document_id, f.document_id) = $document_id
RETURN d.id AS deviceId,
       d.stable_id AS deviceStableId,
       d.name AS deviceName,
       c.id AS componentId,
       c.stable_id AS componentStableId,
       c.name AS componentName,
       f.id AS faultId,
       f.stable_id AS faultStableId,
       f.name AS faultName,
       f.document_id AS documentId,
       f.document_version AS documentVersion,
       f.section_id AS sectionId,
       coalesce(f.source_chunk_uids,
                CASE WHEN f.source_chunk_uid IS NULL THEN [] ELSE [f.source_chunk_uid] END) AS faultChunks,
       f.page_start AS pageStart,
       f.page_end AS pageEnd,
       f.graph_revision AS graphRevision,
       causes.path_stable_id AS pathStableId
ORDER BY deviceName, componentName, faultName
"""


_CANONICAL_FAULT_ALIAS_GROUPS = (
    frozenset((
        "机油泵卡死",
        "机油泵卡滞",
        "机油泵齿轮卡滞",
        "机油泵从动齿轮卡滞",
    )),
    frozenset(("减速齿轮磨损", "减速齿轮齿损伤")),
    frozenset(("起动电机不灵活", "起动电机轴不灵活")),
    frozenset((
        "传动装置不灵活",
        "传动轴转动不灵活",
        "传动主轴转动不灵活",
    )),
    frozenset(("传动装置不顺畅", "换档不顺畅")),
    frozenset(("轴承磨损", "轴承内圈磨损")),
)


def _open_neo4j_driver():
    from dotenv import load_dotenv
    from neo4j import GraphDatabase

    load_dotenv()
    return GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]),
    )


def load_neo4j_graph_snapshot(document_id: str) -> dict[str, Any]:
    document = _text(document_id)
    if not document:
        raise ValueError("document_id is required")
    driver = _open_neo4j_driver()
    try:
        with driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as session:
            rows = session.run(GRAPH_SNAPSHOT_QUERY, document_id=document).data()
    finally:
        driver.close()

    records: list[dict[str, Any]] = []
    for row in rows:
        node_ids = _texts([
            row.get("deviceStableId"),
            row.get("componentStableId"),
            row.get("faultStableId"),
        ])
        chunks = _texts(row.get("faultChunks"))
        pages = _pages([row.get("pageStart"), row.get("pageEnd")])
        path_ids = _texts(row.get("pathStableId"))
        records.append({
            "document_id": _text(row.get("documentId")),
            "document_version": _text(row.get("documentVersion")),
            "device_name": _text(row.get("deviceName")),
            "component_name": _text(row.get("componentName")),
            "fault_name": _text(row.get("faultName")),
            "relationship_types": ["OWNS", "CAUSES"],
            "source_chunk_uids": chunks,
            "pages": pages,
            "graph_revision": _text(row.get("graphRevision")),
            "node_ids": node_ids,
            "path_ids": path_ids,
            "section_id": _text(row.get("sectionId")),
        })
    return {
        "document_id": document,
        "record_count": len(records),
        "records": records,
    }


def _text(value: Any) -> str:
    return str(value or "").strip()


def _texts(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_text(item) for item in value if _text(item)]
    text = _text(value)
    return [text] if text else []


def _pages(value: Any) -> list[int]:
    pages: list[int] = []
    for item in value if isinstance(value, (list, tuple, set)) else [value]:
        try:
            page = int(item)
        except (TypeError, ValueError):
            continue
        if page > 0 and page not in pages:
            pages.append(page)
    return pages


def _normalized(value: Any) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKC", _text(value)).casefold()
        if character.isalnum()
    )


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", [], {}, ()):
            return value
    return None


def _snapshot_records(snapshot: Any) -> list[dict[str, Any]]:
    if isinstance(snapshot, list):
        raw_records = snapshot
    elif isinstance(snapshot, Mapping):
        raw_records = []
        for key in ("records", "evidence", "raw_records", "graph_records"):
            value = snapshot.get(key)
            if isinstance(value, list):
                raw_records = value
                break
    else:
        raw_records = []

    records: list[dict[str, Any]] = []
    for raw in raw_records:
        if not isinstance(raw, Mapping):
            continue
        source = raw.get("source") if isinstance(raw.get("source"), Mapping) else {}
        records.append({
            "document_id": _text(_first(raw, "document_id", "documentId") or _first(source, "document_id", "documentId")),
            "document_version": _text(_first(raw, "document_version", "documentVersion") or _first(source, "document_version", "documentVersion")),
            "device_name": _text(_first(raw, "device_name", "deviceName")),
            "component_name": _text(_first(raw, "component_name", "componentName")),
            "fault_name": _text(_first(raw, "fault_name", "faultName")),
            "relationship_types": _texts(_first(raw, "relationship_types", "relationshipTypes")),
            "source_chunk_uids": _texts(
                _first(raw, "source_chunk_uids", "sourceChunkUids", "chunk_ids")
                or _first(source, "source_chunk_uids", "sourceChunkUids", "chunk_ids")
            ),
            "pages": _pages(_first(raw, "pages", "page") or _first(source, "pages", "page")),
            "graph_revision": _text(_first(raw, "graph_revision", "graphRevision")),
            "node_ids": _texts(_first(raw, "node_ids", "nodeIds")),
            "path_ids": _texts(_first(raw, "path_ids", "pathIds", "path_id", "pathId")),
        })
    return records


def _graph_sources(case: MaintenanceEvalCase) -> Iterable[tuple[str, AllowedSource]]:
    for constraint in case.claim_constraints:
        for source in constraint.allowed_sources:
            if source.source_type == "graph":
                yield constraint.claim_id, source
    for conflict in case.conflict_constraints:
        for index, alternative in enumerate(conflict.alternatives, start=1):
            for source in alternative.allowed_sources:
                if source.source_type == "graph":
                    yield f"conflict:{conflict.subject}:{index}", source
    for turn_index, turn in enumerate(case.turns, start=1):
        for constraint in turn.claim_constraints:
            for source in constraint.allowed_sources:
                if source.source_type == "graph":
                    yield f"turn:{turn_index}:{constraint.claim_id}", source


def _contains_all(expected: Sequence[str], actual: Sequence[str]) -> bool:
    return set(expected).issubset(set(actual))


def _fault_name_matches(expected: Any, actual: Any) -> bool:
    expected_name = _normalized(expected)
    actual_name = _normalized(actual)
    if expected_name == actual_name:
        return True
    return any(
        expected_name in {_normalized(alias) for alias in group}
        and actual_name in {_normalized(alias) for alias in group}
        for group in _CANONICAL_FAULT_ALIAS_GROUPS
    )


def _page_matches(expected: Sequence[int], actual: Sequence[int]) -> bool:
    if not expected:
        return True
    if len(actual) == 2:
        start, end = sorted(actual)
        return any(start <= page <= end for page in expected)
    return bool(set(expected) & set(actual))


def _record_matches(source: AllowedSource, record: Mapping[str, Any]) -> bool:
    return bool(
        (not source.document_id or record.get("document_id") == source.document_id)
        and (
            not source.document_version
            or record.get("document_version") == source.document_version
        )
        and _normalized(source.device_name) == _normalized(record.get("device_name"))
        and _normalized(source.component_name) == _normalized(record.get("component_name"))
        and _fault_name_matches(source.fault_name, record.get("fault_name"))
        and (
            not source.graph_revision
            or _normalized(source.graph_revision) == _normalized(record.get("graph_revision"))
        )
        and _contains_all(source.relationship_types, record.get("relationship_types") or [])
        and _contains_all(source.chunk_ids, record.get("source_chunk_uids") or [])
        and _page_matches(source.pages, record.get("pages") or [])
    )


def _stable_path_matches(source: AllowedSource, record: Mapping[str, Any]) -> bool:
    """A graph gold source is valid only when its stable path also matches.

    Human-readable names are useful diagnostics, but they cannot replace a
    stable path identity after a graph rebuild.
    """
    return not source.path_ids or _contains_all(
        source.path_ids,
        record.get("path_ids") or [],
    )


def _issue(case_id: str, claim_id: str, code: str, **details: Any) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "claim_id": claim_id,
        "code": code,
        **details,
    }


def preflight_graph_contract(
    cases: Sequence[MaintenanceEvalCase],
    snapshot: Any,
) -> dict[str, Any]:
    records = _snapshot_records(snapshot)
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    graph_source_count = 0
    matched_graph_sources = 0

    for index, record in enumerate(records):
        if not record["source_chunk_uids"] or not record["pages"]:
            warnings.append(_issue("", "", "snapshot_source_location_missing", record_index=index))
        if not record["graph_revision"]:
            warnings.append(_issue("", "", "snapshot_graph_revision_missing", record_index=index))

    for case in cases:
        case_sources = list(_graph_sources(case))
        if case.graph_dependency == "required" and not case_sources:
            errors.append(_issue(case.case_id, "", "graph_source_contract_missing"))
            continue
        for claim_id, source in case_sources:
            graph_source_count += 1
            if not all((source.device_name, source.component_name, source.fault_name)):
                errors.append(_issue(
                    case.case_id,
                    claim_id,
                    "stable_graph_identity_missing",
                    source=asdict(source),
                ))
                continue
            semantic_matches = [
                record for record in records if _record_matches(source, record)
            ]
            if any(_stable_path_matches(source, record) for record in semantic_matches):
                matched_graph_sources += 1
                continue
            if semantic_matches and source.path_ids:
                errors.append(_issue(
                    case.case_id,
                    claim_id,
                    "graph_stable_path_not_found",
                    expected_path_ids=list(source.path_ids),
                    actual_path_ids=[
                        path_id
                        for record in semantic_matches
                        for path_id in record.get("path_ids") or []
                    ],
                ))
                continue
            errors.append(_issue(
                case.case_id,
                claim_id,
                "graph_source_not_found",
                source=asdict(source),
            ))

    return {
        "passed": not errors,
        "case_count": len(cases),
        "snapshot_record_count": len(records),
        "expected_source_count": graph_source_count,
        "matched_source_count": matched_graph_sources,
        "graph_source_count": graph_source_count,
        "matched_graph_sources": matched_graph_sources,
        "errors": errors,
        "warnings": warnings,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preflight GraphRAG evaluation evidence contracts.")
    parser.add_argument("--dataset", action="append", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--snapshot")
    source.add_argument("--neo4j-document-id")
    parser.add_argument("--snapshot-report", default="")
    parser.add_argument("--report", default="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cases = read_jsonl_datasets([Path(path) for path in args.dataset])
    if args.neo4j_document_id:
        snapshot = load_neo4j_graph_snapshot(args.neo4j_document_id)
    else:
        snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    if args.snapshot_report:
        snapshot_output = Path(args.snapshot_report)
        snapshot_output.parent.mkdir(parents=True, exist_ok=True)
        snapshot_output.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    report = preflight_graph_contract(cases, snapshot)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        output = Path(args.report)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    print(payload)
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["load_neo4j_graph_snapshot", "preflight_graph_contract"]
