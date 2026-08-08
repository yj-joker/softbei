"""Governance, leakage checks, freezing, and verification for private blind sets."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from evaluation.maintenance_eval_schema import read_jsonl_dataset


_GOLD_FIELDS = frozenset({
    "gold_answer", "golden_answer", "gold_evidence", "qrels", "required_nuggets",
    "optional_nuggets", "forbidden_claims", "expected_step_order", "expected_images",
    "expected_image_order", "step_image_mapping", "forbidden_images", "claim_constraints",
    "conflict_constraints", "answerable", "author_id", "reviewer_a_id", "reviewer_b_id",
    "review_status", "review_notes",
})

_BLIND_METADATA_FIELDS = frozenset({
    "schema_version", "split", "question_type", "graph_dependency", "difficulty",
    "question_origin", "target_section", "target_pages", "expected_scope",
    "expected_coverage_status", "source_request_mode", "source_document",
    "document_id", "document_version", "group", "task_type", "trap_type",
    "claim_constraint_count", "has_forbidden_without_evidence",
})


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            text = line.strip()
            if not text:
                continue
            value = json.loads(text)
            if not isinstance(value, Mapping):
                raise ValueError(f"{path}:{line_number} expected object")
            rows.append(dict(value))
    return rows


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def materialize_question_only(rows: Iterable[Mapping[str, Any]], output: Path) -> Path:
    questions = [
        {
            key: value
            for key, value in row.items()
            if key not in _GOLD_FIELDS and key not in _BLIND_METADATA_FIELDS
        }
        for row in rows
    ]
    return _write_jsonl(output, questions)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_review(rows: Sequence[Mapping[str, Any]]) -> None:
    case_ids: set[str] = set()
    for row in rows:
        case_id = str(row.get("case_id") or "").strip()
        if not case_id or case_id in case_ids:
            raise ValueError(f"missing or duplicate case_id: {case_id!r}")
        case_ids.add(case_id)
        identities = [
            str(row.get(key) or "").strip()
            for key in ("author_id", "reviewer_a_id", "reviewer_b_id")
        ]
        if not all(identities) or len(set(identities)) != 3:
            raise ValueError(f"case {case_id} requires three distinct review identities")
        if str(row.get("review_status") or "") != "resolved":
            raise ValueError(f"case {case_id} review_status must be resolved")


def freeze_dataset(
    root: Path,
    *,
    existing_queries: Sequence[str] = (),
    expected_case_count: int | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    source = root / "authoring" / "questions_resolved.jsonl"
    frozen = root / "frozen"
    if frozen.exists():
        raise ValueError(f"frozen directory already exists: {frozen}")
    if not source.exists():
        raise ValueError(f"resolved authoring file not found: {source}")
    rows = _read_jsonl(source)
    if expected_case_count is not None and len(rows) != expected_case_count:
        raise ValueError(
            f"expected {expected_case_count} cases, found {len(rows)}"
        )
    _validate_review(rows)
    leaks = leakage_check(rows, existing_queries)
    if leaks:
        raise ValueError(f"blind question leakage detected: {leaks[:5]}")

    # Reuse the production parser as the schema gate before any hashes exist.
    read_jsonl_dataset(source)
    frozen.mkdir(parents=True)
    questions_path = materialize_question_only(rows, frozen / "questions.jsonl")
    gold_path = _write_jsonl(
        frozen / "gold.jsonl",
        ({"case_id": row["case_id"], "gold_answer": row.get("gold_answer", "")} for row in rows),
    )
    qrels_path = _write_jsonl(
        frozen / "qrels.jsonl",
        ({"case_id": row["case_id"], "gold_evidence": row.get("gold_evidence", [])} for row in rows),
    )
    scoring_cases_path = _write_jsonl(frozen / "scoring_cases.jsonl", rows)
    review_path = _write_jsonl(
        frozen / "review_signoff.jsonl",
        ({
            "case_id": row["case_id"],
            "author_id": row["author_id"],
            "reviewer_a_id": row["reviewer_a_id"],
            "reviewer_b_id": row["reviewer_b_id"],
            "review_status": row["review_status"],
        } for row in rows),
    )
    files = {
        path.name: _sha256(path)
        for path in (questions_path, gold_path, qrels_path, scoring_cases_path, review_path)
    }
    manifest = {
        "manifest_version": "1.0",
        "schema_version": "3.0",
        "case_count": len(rows),
        "files": files,
    }
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    manifest["manifest_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    (frozen / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    return manifest


def verify_frozen_dataset(root: Path) -> dict[str, Any]:
    frozen = root.resolve() / "frozen"
    manifest_path = frozen / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_manifest_sha = str(manifest.pop("manifest_sha256", ""))
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    actual_manifest_sha = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if actual_manifest_sha != expected_manifest_sha:
        raise ValueError("manifest SHA-256 mismatch")
    for name, expected in manifest.get("files", {}).items():
        path = frozen / name
        if not path.exists() or _sha256(path) != expected:
            raise ValueError(f"file SHA-256 mismatch: {name}")
    return {"verified": True, "case_count": manifest.get("case_count", 0)}


def _normalized_question(text: str) -> str:
    return re.sub(r"\W+", "", str(text or "").lower())


def leakage_check(rows: Sequence[Mapping[str, Any]], existing_queries: Sequence[str]) -> list[dict[str, Any]]:
    leaks: list[dict[str, Any]] = []
    normalized_existing = [_normalized_question(value) for value in existing_queries]
    for row in rows:
        query = _normalized_question(str(row.get("query") or row.get("question") or ""))
        for candidate in normalized_existing:
            similarity = SequenceMatcher(None, query, candidate).ratio() if query and candidate else 0.0
            if query == candidate or similarity >= 0.92:
                leaks.append({"case_id": row.get("case_id"), "similarity": similarity})
                break
    return leaks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Govern a private frozen blind evaluation set")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "freeze", "verify"):
        child = sub.add_parser(command)
        child.add_argument("--root", required=True)
        child.add_argument("--existing-query-file", action="append", default=[])
        child.add_argument("--expected-case-count", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root)
    existing_queries: list[str] = []
    for query_file in args.existing_query_file:
        existing_queries.extend(
            str(row.get("query") or row.get("question") or "")
            for row in _read_jsonl(Path(query_file))
        )
    if args.command == "validate":
        source = root / "authoring" / "questions_resolved.jsonl"
        rows = _read_jsonl(source)
        if args.expected_case_count is not None and len(rows) != args.expected_case_count:
            raise ValueError(
                f"expected {args.expected_case_count} cases, found {len(rows)}"
            )
        _validate_review(rows)
        leaks = leakage_check(rows, existing_queries)
        if leaks:
            raise ValueError(f"blind question leakage detected: {leaks[:5]}")
        read_jsonl_dataset(source)
        result = {"valid": True, "case_count": len(rows)}
    elif args.command == "freeze":
        result = freeze_dataset(
            root,
            existing_queries=existing_queries,
            expected_case_count=args.expected_case_count,
        )
    else:
        result = verify_frozen_dataset(root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
