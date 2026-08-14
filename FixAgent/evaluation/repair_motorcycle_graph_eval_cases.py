"""Create an auditable primary-manual correction of GraphRAG evaluation cases."""

from __future__ import annotations

import argparse
import copy
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


_TEXT_REPAIRS: dict[str, tuple[tuple[str, str], ...]] = {
    "motorcycle_manual_v2_graph_005": (
        ("气缸与活塞", "活塞环"),
        ("活塞卡住", "活塞环卡住"),
    ),
    "motorcycle_manual_v2_graph_009": (
        ("机油泵卡死", "机油泵从动齿轮卡滞"),
    ),
    "motorcycle_manual_v2_graph_010": (
        ("机油泵变形或开裂", "油泵座垫变形"),
        ("机油泵更换", "油泵座垫更换"),
        ("更换机油泵", "更换油泵座垫"),
    ),
    "motorcycle_manual_v2_graph_011": (
        ("机油泵损坏", "O 型圈开裂"),
        ("机油泵更换", "O 型圈更换"),
        ("更换机油泵", "更换 O 型圈"),
    ),
}


def _replace_text(value: Any, repairs: Sequence[tuple[str, str]]) -> Any:
    if isinstance(value, str):
        for old, new in repairs:
            value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [_replace_text(item, repairs) for item in value]
    if isinstance(value, dict):
        return {key: _replace_text(item, repairs) for key, item in value.items()}
    return value


def _legacy_id(case: Mapping[str, Any]) -> str:
    return str(case.get("legacy_case_id") or case.get("case_id") or case.get("id") or "")


def _walk_sources(value: Any):
    if isinstance(value, dict):
        sources = value.get("allowed_sources")
        if isinstance(sources, list):
            for source in sources:
                if isinstance(source, dict):
                    yield value, source
        for key, child in value.items():
            if key != "allowed_sources":
                yield from _walk_sources(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_sources(child)


def _downgrade_unanchored_graph_claim(case: dict[str, Any]) -> None:
    case["graph_dependency"] = "none"
    case["question_origin"] = "primary_manual_only_after_graph_contract_audit"
    for parent, source in list(_walk_sources(case)):
        if source.get("source_type") != "graph":
            continue
        manual = {
            key: copy.deepcopy(source[key])
            for key in ("document_id", "document_version", "pages", "chunk_ids")
            if key in source
        }
        manual["source_type"] = "manual"
        ordered = {"source_type": manual.pop("source_type"), **manual}
        parent["claim_id"] = "manual_relation"
        parent["allowed_sources"] = [ordered]


def repair_cases(
    cases: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    repaired: list[dict[str, Any]] = []
    changed_ids: list[str] = []
    for source_case in cases:
        case = copy.deepcopy(dict(source_case))
        legacy_id = _legacy_id(case)
        repairs = _TEXT_REPAIRS.get(legacy_id)
        if repairs:
            case = _replace_text(case, repairs)
            changed_ids.append(str(case.get("case_id") or case.get("id") or legacy_id))
        if legacy_id == "motorcycle_manual_v2_graph_012":
            _downgrade_unanchored_graph_claim(case)
            changed_ids.append(str(case.get("case_id") or case.get("id") or legacy_id))
        if legacy_id == "motorcycle_manual_v2_graph_026":
            for _, graph_source in _walk_sources(case):
                if graph_source.get("source_type") == "graph":
                    graph_source["fault_name"] = "曲轴与平衡轴转动不灵活"
            changed_ids.append(str(case.get("case_id") or case.get("id") or legacy_id))
        repaired.append(case)
    unique_ids = list(dict.fromkeys(changed_ids))
    return repaired, {
        "case_count": len(repaired),
        "repaired_case_count": len(unique_ids),
        "repaired_case_ids": unique_ids,
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
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args(argv)
    repaired, report = repair_cases(_read_jsonl(Path(args.dataset)))
    _write_jsonl(Path(args.output), repaired)
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
