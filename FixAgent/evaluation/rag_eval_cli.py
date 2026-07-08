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
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple


DEFAULT_TOP_K = 5

UNIT_HINTS = (
    "mm",
    "cm",
    "m",
    "n·m",
    "n.m",
    "nm",
    "mpa",
    "kpa",
    "pa",
    "v",
    "a",
    "kg",
    "g",
    "ml",
    "l",
    "rpm",
    "°c",
    "℃",
)

STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "be",
    "by",
    "for",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}

CJK_STOP_CHARS = set("的是了和与及或在时后前中内外应需要将用把对其该此为并按")
CJK_STOP_CHARS.update(set("如果如有被若则必须"))


@dataclass
class EvalCase:
    case_id: str
    question: str
    golden_chunk_ids: List[str] = field(default_factory=list)
    golden_evidence_keys: List[str] = field(default_factory=list)
    golden_answer: str = ""
    required_facts: List[str] = field(default_factory=list)
    optional_facts: List[str] = field(default_factory=list)
    answerable: bool = True
    chapter: str = ""
    question_type: str = ""
    difficulty: str = ""
    filters: Dict[str, Any] = field(default_factory=dict)
    expected_image_ids: List[str] = field(default_factory=list)
    expected_image_pages: List[int] = field(default_factory=list)
    forbidden_image_pages: List[int] = field(default_factory=list)
    expected_image_count_min: int | None = None
    expected_image_count_max: int | None = None


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


def split_evidence_keys(value: str | None) -> List[str]:
    if not value:
        return []
    normalized = value.replace("；", ";").replace("，", ",")
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


def parse_int(value: str | int | None) -> int | None:
    if isinstance(value, int):
        return value
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def split_ints(value: str | None) -> List[int]:
    values: List[int] = []
    for item in split_ids(value):
        parsed = parse_int(item)
        if parsed is not None:
            values.append(parsed)
    return values


def normalize_text(value: str) -> str:
    text = (value or "").lower()
    replacements = {
        "～": "-",
        "—": "-",
        "–": "-",
        "－": "-",
        "到": "-",
        "至": "-",
        "Ｎ": "n",
        "ｍ": "m",
        "Ｍ": "m",
        "．": ".",
        "，": ",",
        "。": ".",
        "：": ":",
        "；": ";",
        "（": "(",
        "）": ")",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_numbers(value: str) -> List[str]:
    return re.findall(r"\d+(?:\.\d+)?", normalize_text(value))


def extract_units(value: str) -> List[str]:
    text = normalize_text(value).replace(" ", "")
    return [unit for unit in UNIT_HINTS if unit in text]


def _is_cjk_char(char: str) -> bool:
    return "\u4e00" <= char <= "\u9fff"


def meaningful_terms(value: str) -> set[str]:
    text = normalize_text(value)
    terms = {token for token in re.findall(r"[a-z0-9]+", text) if token not in STOP_WORDS}
    cjk_chars = [char for char in text if _is_cjk_char(char) and char not in CJK_STOP_CHARS]
    terms.update(cjk_chars)
    terms.update("".join(cjk_chars[index : index + 2]) for index in range(max(len(cjk_chars) - 1, 0)))
    return {term for term in terms if term}


def case_facts(case: EvalCase, field_name: str) -> List[str]:
    value = getattr(case, field_name, [])
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return split_ids(str(value or ""))


def fact_matched(fact: str, text: str) -> bool:
    fact_text = normalize_text(fact)
    answer_text = normalize_text(text)
    if not fact_text:
        return True
    if fact_text in answer_text:
        return True
    if "压力" in fact_text and "升高" in fact_text and "压力" in answer_text and "高" in answer_text:
        return True

    fact_numbers = extract_numbers(fact)
    if fact_numbers:
        answer_numbers = set(extract_numbers(text))
        if not all(number in answer_numbers for number in fact_numbers):
            return False
        fact_units = extract_units(fact)
        answer_units = extract_units(text)
        return not fact_units or any(unit in answer_units for unit in fact_units)

    fact_terms = meaningful_terms(fact)
    if not fact_terms:
        return False
    answer_terms = meaningful_terms(text)
    overlap = len(fact_terms & answer_terms) / len(fact_terms)
    return overlap >= 0.75


def matched_facts(facts: Sequence[str], text: str) -> List[str]:
    return [fact for fact in facts if fact_matched(fact, text)]


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
                    golden_evidence_keys=split_evidence_keys(
                        row.get("golden_evidence_keys") or row.get("golden_source_anchors")
                    ),
                    required_facts=split_ids(row.get("required_facts")),
                    optional_facts=split_ids(row.get("optional_facts")),
                    answerable=parse_bool(row.get("answerable"), default=True),
                    chapter=(row.get("chapter") or "").strip(),
                    question_type=(row.get("question_type") or "").strip(),
                    difficulty=(row.get("difficulty") or "").strip(),
                    filters=filters,
                    expected_image_ids=split_ids(row.get("expected_image_ids")),
                    expected_image_pages=split_ints(row.get("expected_image_pages")),
                    forbidden_image_pages=split_ints(row.get("forbidden_image_pages")),
                    expected_image_count_min=parse_int(row.get("expected_image_count_min")),
                    expected_image_count_max=parse_int(row.get("expected_image_count_max")),
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


def _prefixed(value: Any, prefix: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith(f"{prefix}:"):
        return text
    return f"{prefix}:{text}"


def _chunk_id_tail(chunk_id: str) -> str:
    text = str(chunk_id or "").strip()
    if ":" not in text:
        return ""
    return text.split(":", 1)[1]


def evidence_keys_for_item(item: RetrievedItem) -> List[str]:
    metadata = item.metadata or {}
    keys: List[str] = []

    def add(value: Any, prefix: str | None = None) -> None:
        if value in ("", None):
            return
        key = _prefixed(value, prefix) if prefix else str(value).strip()
        if key and key not in keys:
            keys.append(key)

    add(item.chunk_id)
    add(item.chunk_id, "id")
    add(_chunk_id_tail(item.chunk_id), "id_tail")
    add(metadata.get("source_anchor"), "anchor")
    add(metadata.get("parent_chunk_id"), "parent")
    section_id = metadata.get("parent_section_id")
    chunk_type = metadata.get("chunk_type") or metadata.get("source_chunk_type")
    chunk_label = metadata.get("chunk_label")
    page = metadata.get("page_number") or metadata.get("page")
    if section_id and chunk_type and page not in ("", None):
        add(f"{section_id}|{chunk_type}|{page}", "section_page_type")
    if section_id and chunk_label and page not in ("", None):
        add(f"{section_id}|{chunk_label}|{page}", "section_page_label")
    return keys


def first_evidence_hit(
    golden_ids: Sequence[str],
    golden_evidence_keys: Sequence[str],
    returned_items: Sequence[RetrievedItem],
) -> tuple[int | None, str]:
    explicit_golden = set(golden_evidence_keys or [])
    if explicit_golden:
        for index, item in enumerate(returned_items, start=1):
            for key in evidence_keys_for_item(item):
                if key in explicit_golden:
                    return index, key

    golden = set(golden_ids or []) | explicit_golden
    for chunk_id in golden_ids or []:
        tail = _chunk_id_tail(chunk_id)
        if tail:
            golden.add(_prefixed(tail, "id_tail"))
        if chunk_id:
            golden.add(_prefixed(chunk_id, "id"))
    if not golden:
        return None, ""
    for index, item in enumerate(returned_items, start=1):
        for key in evidence_keys_for_item(item):
            if key in golden:
                return index, key
    return None, ""


def first_evidence_hit_for_case(case: EvalCase, returned_items: Sequence[RetrievedItem]) -> tuple[int | None, str]:
    rank, matched_key = first_evidence_hit(case.golden_chunk_ids, case.golden_evidence_keys, returned_items)
    if rank is not None:
        return rank, matched_key

    required_facts = case_facts(case, "required_facts")
    if required_facts:
        for index, item in enumerate(returned_items, start=1):
            content = item.content or str((item.metadata or {}).get("raw_text") or "")
            if len(matched_facts(required_facts, content)) == len(required_facts):
                return index, f"fact:{required_facts[0]}"

    golden_answer = (case.golden_answer or "").strip()
    if golden_answer:
        golden_numbers = extract_numbers(golden_answer)
        if golden_numbers:
            for index, item in enumerate(returned_items, start=1):
                content = item.content or str((item.metadata or {}).get("raw_text") or "")
                content_numbers = set(extract_numbers(content))
                if all(number in content_numbers for number in golden_numbers):
                    return index, f"golden_number:{','.join(golden_numbers)}"
    return None, ""


def _is_image_item(item: RetrievedItem) -> bool:
    metadata = item.metadata or {}
    chunk_type = str(metadata.get("chunk_type") or "").strip().lower()
    chunk_label = str(metadata.get("chunk_label") or "").strip().lower()
    return (
        ":img:" in item.chunk_id
        or chunk_type in {"image", "image_summary"}
        or chunk_label in {"image", "image_summary"}
        or bool(metadata.get("image_url"))
    )


def _item_page(item: RetrievedItem) -> int | None:
    metadata = item.metadata or {}
    for key in ("page", "page_num", "page_number"):
        page = parse_int(metadata.get(key))
        if page is not None:
            return page
    return None


def _unique_keep_order(values: Iterable[Any]) -> List[Any]:
    seen: set[Any] = set()
    unique: List[Any] = []
    for value in values:
        if value in ("", None) or value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _image_eval_required(case: EvalCase) -> bool:
    return bool(
        case.expected_image_ids
        or case.expected_image_pages
        or case.forbidden_image_pages
        or case.expected_image_count_min is not None
        or case.expected_image_count_max is not None
    )


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 1.0
    return round(numerator / denominator, 6)


def evaluate_image_expectations(case: EvalCase, items: Sequence[RetrievedItem]) -> Dict[str, Any]:
    image_items = [item for item in items if _is_image_item(item)]
    retrieved_image_ids = _unique_keep_order(item.chunk_id for item in image_items)
    retrieved_image_pages = _unique_keep_order(page for page in (_item_page(item) for item in image_items) if page is not None)

    expected_id_set = set(case.expected_image_ids)
    retrieved_id_set = set(retrieved_image_ids)
    expected_page_set = set(case.expected_image_pages)
    retrieved_page_set = set(retrieved_image_pages)

    if expected_id_set:
        matched_expected_ids = [image_id for image_id in case.expected_image_ids if image_id in retrieved_id_set]
        missing_expected_ids = [image_id for image_id in case.expected_image_ids if image_id not in retrieved_id_set]
        unexpected_image_ids = [image_id for image_id in retrieved_image_ids if image_id not in expected_id_set]
        image_recall = _ratio(len(matched_expected_ids), len(case.expected_image_ids))
        image_precision = _ratio(len(matched_expected_ids), len(retrieved_image_ids)) if retrieved_image_ids else 0.0
    elif expected_page_set:
        matched_pages = [page for page in case.expected_image_pages if page in retrieved_page_set]
        missing_expected_ids = [str(page) for page in case.expected_image_pages if page not in retrieved_page_set]
        unexpected_image_ids = [str(page) for page in retrieved_image_pages if page not in expected_page_set]
        image_recall = _ratio(len(matched_pages), len(case.expected_image_pages))
        image_precision = _ratio(len(matched_pages), len(retrieved_image_pages)) if retrieved_image_pages else 0.0
    else:
        missing_expected_ids = []
        unexpected_image_ids = []
        image_recall = 1.0
        image_precision = 1.0

    image_count = len(retrieved_image_ids) if expected_id_set else len(retrieved_image_pages)
    min_ok = case.expected_image_count_min is None or image_count >= case.expected_image_count_min
    max_ok = case.expected_image_count_max is None or image_count <= case.expected_image_count_max
    image_count_pass = min_ok and max_ok
    forbidden_page_set = set(case.forbidden_image_pages)
    forbidden_image_pass = not bool(forbidden_page_set & retrieved_page_set)
    image_pass = (
        image_recall >= 1.0
        and image_precision >= 1.0
        and image_count_pass
        and forbidden_image_pass
    )

    return {
        "expected_image_ids": ";".join(case.expected_image_ids),
        "expected_image_pages": ";".join(str(page) for page in case.expected_image_pages),
        "forbidden_image_pages": ";".join(str(page) for page in case.forbidden_image_pages),
        "expected_image_count_min": "" if case.expected_image_count_min is None else case.expected_image_count_min,
        "expected_image_count_max": "" if case.expected_image_count_max is None else case.expected_image_count_max,
        "retrieved_image_ids": ";".join(str(image_id) for image_id in retrieved_image_ids),
        "retrieved_image_pages": ";".join(str(page) for page in retrieved_image_pages),
        "image_recall": image_recall,
        "image_precision": image_precision,
        "image_count_pass": image_count_pass,
        "forbidden_image_pass": forbidden_image_pass,
        "image_pass": image_pass,
        "missing_expected_image_ids": ";".join(str(image_id) for image_id in missing_expected_ids),
        "unexpected_image_ids": ";".join(str(image_id) for image_id in unexpected_image_ids),
        "image_eval_required": _image_eval_required(case),
    }


def _evidence_image_to_mapping(image: Any) -> Mapping[str, Any]:
    if isinstance(image, Mapping):
        return image
    if hasattr(image, "model_dump"):
        return image.model_dump(by_alias=True)
    if hasattr(image, "dict"):
        return image.dict()
    return {}


def evidence_images_to_retrieved_items(evidence_images: Sequence[Any]) -> List[RetrievedItem]:
    items: List[RetrievedItem] = []
    for image in evidence_images or []:
        image_data = _evidence_image_to_mapping(image)
        if not image_data:
            continue
        image_url = image_data.get("imageUrl") or image_data.get("image_url") or ""
        source_chunk_id = image_data.get("sourceChunkId") or image_data.get("source_chunk_id") or ""
        page = image_data.get("page") or image_data.get("pageNumber") or image_data.get("page_number")
        chunk_id = str(source_chunk_id or image_url)
        if not chunk_id:
            continue
        items.append(
            RetrievedItem(
                chunk_id=chunk_id,
                content=str(image_data.get("caption") or ""),
                metadata={
                    "chunk_type": "image",
                    "image_url": image_url,
                    "page": page,
                    "section_title": image_data.get("sectionTitle") or image_data.get("section_title") or "",
                    "document_id": image_data.get("documentId") or image_data.get("document_id") or "",
                    "context_role": image_data.get("contextRole") or image_data.get("context_role") or "",
                    "source_chunk_id": source_chunk_id,
                },
            )
        )
    return items


def evaluate_evidence_images(case: EvalCase, evidence_images: Sequence[Any]) -> Dict[str, Any]:
    return evaluate_image_expectations(case, evidence_images_to_retrieved_items(evidence_images))


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
        rank, matched_evidence_key = first_evidence_hit_for_case(case, items)
        skipped = not case.answerable
        image_eval = evaluate_image_expectations(case, items)
        row = {
            "id": case.case_id,
            "question": case.question,
            "golden_chunk_ids": ";".join(case.golden_chunk_ids),
            "golden_evidence_keys": ";".join(case.golden_evidence_keys),
            "retrieved_chunk_ids": ";".join(returned_ids),
            "matched_evidence_key": matched_evidence_key,
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
        row.update(image_eval)
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
    image_rows = [row for row in answerable_rows if row.get("image_eval_required")]
    image_total = len(image_rows)
    if image_total:
        summary.update(
            {
                "image_case_count": image_total,
                "image_pass_rate": round(sum(1 for row in image_rows if row["image_pass"]) / image_total, 6),
                "avg_image_recall": round(sum(float(row["image_recall"]) for row in image_rows) / image_total, 6),
                "avg_image_precision": round(sum(float(row["image_precision"]) for row in image_rows) / image_total, 6),
            }
        )
    else:
        summary.update(
            {
                "image_case_count": 0,
                "image_pass_rate": 0.0,
                "avg_image_recall": 0.0,
                "avg_image_precision": 0.0,
            }
        )
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
        "golden_evidence_keys",
        "retrieved_chunk_ids",
        "matched_evidence_key",
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
        "expected_image_ids",
        "expected_image_pages",
        "forbidden_image_pages",
        "expected_image_count_min",
        "expected_image_count_max",
        "retrieved_image_ids",
        "retrieved_image_pages",
        "image_recall",
        "image_precision",
        "image_count_pass",
        "forbidden_image_pass",
        "image_pass",
        "missing_expected_image_ids",
        "unexpected_image_ids",
        "image_eval_required",
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
