"""Refresh graph evidence in an evaluation dataset from a validated graph snapshot.

The source dataset is never overwritten.  ``--apply`` writes a separate JSONL
candidate only when every graph source has one complete, unique snapshot match.
"""

from __future__ import annotations

import argparse
import copy
import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from evaluation.graph_contract_preflight import (
    _fault_name_matches,
    _normalized,
    _snapshot_records,
    _text,
)


def _walk_allowed_sources(value: Any) -> Iterable[tuple[dict[str, Any], dict[str, Any]]]:
    if isinstance(value, dict):
        sources = value.get("allowed_sources")
        if isinstance(sources, list):
            for source in sources:
                if isinstance(source, dict):
                    yield value, source
        for key, child in value.items():
            if key != "allowed_sources":
                yield from _walk_allowed_sources(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_allowed_sources(child)


def _matches(source: Mapping[str, Any], record: Mapping[str, Any]) -> bool:
    return bool(
        (not _text(source.get("document_id"))
         or _text(source.get("document_id")) == _text(record.get("document_id")))
        and (not _text(source.get("document_version"))
             or _text(source.get("document_version")) == _text(record.get("document_version")))
        and (not _text(source.get("device_name"))
             or _normalized(source.get("device_name")) == _normalized(record.get("device_name")))
        and _normalized(source.get("component_name")) == _normalized(record.get("component_name"))
        and _fault_name_matches(source.get("fault_name"), record.get("fault_name"))
    )


def _complete(record: Mapping[str, Any]) -> bool:
    return bool(
        _text(record.get("document_id"))
        and _text(record.get("document_version"))
        and _text(record.get("device_name"))
        and _text(record.get("component_name"))
        and _text(record.get("fault_name"))
        and record.get("path_ids")
        and record.get("node_ids")
        and record.get("source_chunk_uids")
        and record.get("pages")
        and _text(record.get("graph_revision"))
    )


def refresh_cases_graph_sources(
    cases: Sequence[Mapping[str, Any]], snapshot: Any
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return a refreshed copy and an audit report without writing either input."""
    refreshed = copy.deepcopy(list(cases))
    records = _snapshot_records(snapshot)
    errors: list[dict[str, Any]] = []
    updated = 0

    for case in refreshed:
        case_id = _text(case.get("id") or case.get("case_id"))
        for _, source in _walk_allowed_sources(case):
            if _text(source.get("source_type")) != "graph":
                continue
            raw_matches = [record for record in records if _matches(source, record)]
            exact_fault_matches = [
                record for record in raw_matches
                if _normalized(source.get("fault_name"))
                == _normalized(record.get("fault_name"))
            ]
            if exact_fault_matches:
                raw_matches = exact_fault_matches
            matches_by_path: dict[tuple[str, ...], Mapping[str, Any]] = {}
            for record in raw_matches:
                path_key = tuple(str(path) for path in record.get("path_ids") or [])
                matches_by_path.setdefault(path_key, record)
            matches = list(matches_by_path.values())
            if len(matches) != 1:
                errors.append({
                    "case_id": case_id,
                    "code": "ambiguous_graph_source_match" if len(matches) > 1 else "graph_source_not_found",
                    "match_count": len(matches),
                    "component_name": source.get("component_name", ""),
                    "fault_name": source.get("fault_name", ""),
                })
                continue
            record = matches[0]
            if not _complete(record):
                errors.append({
                    "case_id": case_id,
                    "code": "graph_snapshot_provenance_incomplete",
                    "component_name": source.get("component_name", ""),
                    "fault_name": source.get("fault_name", ""),
                })
                continue
            source.update({
                "document_id": record["document_id"],
                "document_version": record["document_version"],
                "device_name": record["device_name"],
                "component_name": record["component_name"],
                "fault_name": record["fault_name"],
                "relationship_types": list(record["relationship_types"]),
                "chunk_ids": list(record["source_chunk_uids"]),
                "pages": list(record["pages"]),
                "node_ids": list(record["node_ids"]),
                "path_ids": list(record["path_ids"]),
                "graph_revision": record["graph_revision"],
            })
            updated += 1

    return refreshed, {
        "case_count": len(refreshed),
        "snapshot_record_count": len(records),
        "updated_source_count": updated if not errors else 0,
        "errors": errors,
        "passed": not errors,
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(path: Path, cases: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n" for case in cases),
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    cases = _read_jsonl(Path(args.dataset))
    snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    refreshed, report = refresh_cases_graph_sources(cases, snapshot)
    report.update({"applied": False, "output": str(Path(args.output))})
    if args.apply and report["passed"]:
        _write_jsonl(Path(args.output), refreshed)
        report["applied"] = True
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
