"""Command line retrieval evaluator for the project RAG pipeline.

The evaluator reads a manually curated CSV dataset, calls the internal
KnowledgeRetrievalTool, and writes per-case retrieval results plus aggregate
Recall@K/MRR metrics.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple


DEFAULT_TOP_K = 5


@dataclass
class EvalCase:
    case_id: str
    question: str
    golden_chunk_ids: List[str] = field(default_factory=list)
    golden_answer: str = ""
    required_facts: List[str] = field(default_factory=list)
    optional_facts: List[str] = field(default_factory=list)
    answerable: bool = True
    chapter: str = ""
    question_type: str = ""
    difficulty: str = ""
    filters: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievedItem:
    chunk_id: str
    score: float | None = None
    content: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


def split_ids(value: str | None) -> List[str]:
    if not value:
        return []
    normalized = value.replace("；", ";").replace("，", ",").replace("|", ";")
    parts: List[str] = []
    for segment in normalized.replace(",", ";").split(";"):
        item = segment.strip()
        if item:
            parts.append(item)
    return parts


def parse_bool(value: str | bool | None, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "是", "可回答"}


def read_dataset(path: Path) -> List[EvalCase]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        cases = []
        for index, row in enumerate(reader, start=1):
            case_id = (row.get("id") or row.get("case_id") or f"case_{index:03d}").strip()
            question = (row.get("question") or "").strip()
            if not question:
                raise ValueError(f"第 {index} 行缺少 question")
            filters = {
                name: row[name].strip()
                for name in (
                    "category",
                    "document_id",
                    "chunk_type",
                    "device_type",
                    "document_version",
                    "manual_type",
                )
                if row.get(name) and row[name].strip()
            }
            if row.get("tags") and row["tags"].strip():
                filters["tags"] = split_ids(row["tags"])
            cases.append(
                EvalCase(
                    case_id=case_id,
                    question=question,
                    golden_answer=(row.get("golden_answer") or "").strip(),
                    golden_chunk_ids=split_ids(row.get("golden_chunk_ids")),
                    required_facts=split_ids(row.get("required_facts")),
                    optional_facts=split_ids(row.get("optional_facts")),
                    answerable=parse_bool(row.get("answerable"), default=True),
                    chapter=(row.get("chapter") or "").strip(),
                    question_type=(row.get("question_type") or "").strip(),
                    difficulty=(row.get("difficulty") or "").strip(),
                    filters=filters,
                )
            )
    return cases


def first_hit_rank(golden_ids: Sequence[str], returned_ids: Sequence[str]) -> int | None:
    golden = set(golden_ids)
    if not golden:
        return None
    for index, chunk_id in enumerate(returned_ids, start=1):
        if chunk_id in golden:
            return index
    return None


def evaluate_retrieval_rows(
    cases: Sequence[EvalCase],
    retrieved: Mapping[str, Sequence[RetrievedItem]],
    top_k: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    answerable_rows: List[Dict[str, Any]] = []

    for case in cases:
        items = list(retrieved.get(case.case_id, []))
        returned_ids = [item.chunk_id for item in items]
        rank = first_hit_rank(case.golden_chunk_ids, returned_ids)
        skipped = not case.answerable
        row = {
            "id": case.case_id,
            "question": case.question,
            "golden_chunk_ids": ";".join(case.golden_chunk_ids),
            "retrieved_chunk_ids": ";".join(returned_ids),
            "hit_top1": bool(rank and rank <= 1),
            "hit_top3": bool(rank and rank <= 3),
            "hit_top5": bool(rank and rank <= 5),
            "rank": rank or "",
            "skipped_for_recall": skipped,
            "top_k": top_k,
            "chapter": case.chapter,
            "question_type": case.question_type,
            "difficulty": case.difficulty,
            "answerable": case.answerable,
            "golden_answer": case.golden_answer,
            "top1_content": items[0].content if items else "",
            "retrieved_scores": ";".join("" if item.score is None else f"{item.score:.6f}" for item in items),
        }
        rows.append(row)
        if not skipped:
            answerable_rows.append(row)

    total = len(answerable_rows)

    def rate(field: str) -> float:
        if not total:
            return 0.0
        return round(sum(1 for row in answerable_rows if row[field]) / total, 6)

    reciprocal_ranks = [
        1 / int(row["rank"])
        if row["rank"] not in ("", None) and int(row["rank"]) <= top_k
        else 0.0
        for row in answerable_rows
    ]
    summary = {
        "case_count": len(rows),
        "answerable_case_count": total,
        "top_k": top_k,
        "recall_at_1": rate("hit_top1"),
        "recall_at_3": rate("hit_top3"),
        "recall_at_5": rate("hit_top5"),
        "mrr": round(sum(reciprocal_ranks) / total, 6) if total else 0.0,
    }
    return rows, summary


async def run_internal_retrieval(cases: Sequence[EvalCase], top_k: int) -> Dict[str, List[RetrievedItem]]:
    from tools.knowledge_retrieval_tool import get_knowledge_retrieval_tool

    tool = get_knowledge_retrieval_tool()
    retrieved: Dict[str, List[RetrievedItem]] = {}
    for case in cases:
        result = await tool.run(query=case.question, top_k=top_k, **case.filters)
        if not result.success:
            message = result.error.message if result.error else "unknown retrieval error"
            raise RuntimeError(f"{case.case_id} 检索失败: {message}")
        items = []
        for item in result.data or []:
            items.append(
                RetrievedItem(
                    chunk_id=getattr(item, "id", ""),
                    score=getattr(item, "score", None),
                    content=getattr(item, "content", "") or "",
                    metadata=dict(getattr(item, "metadata", None) or {}),
                )
            )
        retrieved[case.case_id] = items
    return retrieved


def write_rows(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "id",
        "question",
        "golden_chunk_ids",
        "retrieved_chunk_ids",
        "hit_top1",
        "hit_top3",
        "hit_top5",
        "rank",
        "skipped_for_recall",
        "top_k",
        "chapter",
        "question_type",
        "difficulty",
        "answerable",
        "golden_answer",
        "top1_content",
        "retrieved_scores",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_summary(path: Path, summary: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate RAG retrieval Recall@K against a CSV dataset.")
    parser.add_argument("--dataset", required=True, help="CSV file with id, question, golden_chunk_ids, answerable columns.")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K, help="Number of chunks to request from retrieval.")
    parser.add_argument("--out-dir", default="evaluation/results", help="Directory for result CSV/JSON files.")
    parser.add_argument("--result-name", default="retrieval_result", help="Base name for output files.")
    return parser


async def async_main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    dataset_path = Path(args.dataset)
    out_dir = Path(args.out_dir)
    cases = read_dataset(dataset_path)
    retrieved = await run_internal_retrieval(cases, top_k=args.top_k)
    rows, summary = evaluate_retrieval_rows(cases, retrieved, top_k=args.top_k)
    write_rows(out_dir / f"{args.result_name}.csv", rows)
    write_summary(out_dir / f"{args.result_name}_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
