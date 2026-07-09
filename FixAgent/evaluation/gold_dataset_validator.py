"""Validate the maintenance evaluation dataset as a PDF-backed gold set.

This module checks the dataset itself, not the RAG system output.  A case is
considered auditable only when its expected answer/image constraints can be
traced back to the source PDF pages declared by the dataset.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from evaluation.maintenance_eval_cli import normalize_text, phrase_matched


@dataclass(frozen=True)
class GoldValidationIssue:
    code: str
    case_id: str
    field: str
    message: str
    page: int | None = None
    value: str = ""


def load_raw_jsonl(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError(f"{path}:{line_no} must contain a JSON object")
            cases.append(data)
    return cases


def load_pdf_pages(pdf_path: Path) -> dict[int, str]:
    import fitz

    doc = fitz.open(str(pdf_path))
    return {index + 1: doc.load_page(index).get_text("text") for index in range(doc.page_count)}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _as_int(value: Any) -> int | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _page_from_mapping(value: Any) -> int | None:
    if not isinstance(value, Mapping):
        return None
    for key in ("page", "pageNumber", "page_number"):
        page = _as_int(value.get(key))
        if page is not None:
            return page
    return None


def _text_found_on_page(snippet: str, page_text: str) -> bool:
    snippet_norm = normalize_text(snippet)
    page_norm = normalize_text(page_text)
    return bool(snippet_norm) and snippet_norm in page_norm


def _nearby_page_text(page: int, pages: Mapping[int, str]) -> str:
    return "\n".join(pages[p] for p in (page - 1, page, page + 1) if p in pages)


def _add_page_range_issues(
    *,
    issues: list[GoldValidationIssue],
    case_id: str,
    field: str,
    values: Sequence[Any],
    page_count: int,
) -> None:
    for raw_value in values:
        page = _as_int(raw_value)
        if page is None or page < 1 or page > page_count:
            issues.append(
                GoldValidationIssue(
                    code=f"{field}_out_of_range",
                    case_id=case_id,
                    field=field,
                    message=f"{field} 页码不在 PDF 范围内。",
                    page=page,
                    value=str(raw_value),
                )
            )


def _validate_image_page_fields(
    *,
    issues: list[GoldValidationIssue],
    case_id: str,
    case: Mapping[str, Any],
    page_count: int,
) -> None:
    for field in ("expected_images", "forbidden_images", "step_image_mapping"):
        for item in _as_list(case.get(field)):
            page = _page_from_mapping(item)
            if page is None:
                continue
            if page < 1 or page > page_count:
                issues.append(
                    GoldValidationIssue(
                        code=f"{field[:-1] if field.endswith('s') else field}_page_out_of_range",
                        case_id=case_id,
                        field=field,
                        message=f"{field} 中的图片页码不在 PDF 范围内。",
                        page=page,
                        value=json.dumps(item, ensure_ascii=False),
                    )
                )
    _add_page_range_issues(
        issues=issues,
        case_id=case_id,
        field="expected_image_order",
        values=_as_list(case.get("expected_image_order")),
        page_count=page_count,
    )


def validate_raw_cases_against_pages(
    cases: Sequence[Mapping[str, Any]],
    pages: Mapping[int, str],
) -> list[GoldValidationIssue]:
    issues: list[GoldValidationIssue] = []
    page_count = max(pages.keys(), default=0)
    seen_ids: set[str] = set()

    for index, case in enumerate(cases, start=1):
        case_id = str(case.get("case_id") or case.get("id") or f"line_{index}").strip()
        if not case_id:
            case_id = f"line_{index}"
        if case_id in seen_ids:
            issues.append(
                GoldValidationIssue(
                    code="duplicate_case_id",
                    case_id=case_id,
                    field="case_id",
                    message="case_id 重复。",
                )
            )
        seen_ids.add(case_id)

        target_pages = [_as_int(value) for value in _as_list(case.get("target_pages"))]
        target_pages = [page for page in target_pages if page is not None]
        _add_page_range_issues(
            issues=issues,
            case_id=case_id,
            field="target_pages",
            values=target_pages,
            page_count=page_count,
        )

        target_section = str(case.get("target_section") or "").strip()
        if target_section and target_pages:
            section_found = any(phrase_matched(target_section, _nearby_page_text(page, pages)) for page in target_pages)
            if not section_found:
                issues.append(
                    GoldValidationIssue(
                        code="target_section_not_found",
                        case_id=case_id,
                        field="target_section",
                        message="target_section 未能在目标页或相邻页文本中自动命中。",
                        value=target_section,
                    )
                )

        answerable = case.get("answerable", True) is not False
        required_nuggets = [str(item).strip() for item in _as_list(case.get("required_nuggets")) if str(item).strip()]
        gold_evidence = [item for item in _as_list(case.get("gold_evidence")) if isinstance(item, Mapping)]

        if answerable and not required_nuggets:
            issues.append(
                GoldValidationIssue(
                    code="missing_required_nuggets",
                    case_id=case_id,
                    field="required_nuggets",
                    message="可回答用例必须包含 required_nuggets。",
                )
            )

        if not gold_evidence:
            issues.append(
                GoldValidationIssue(
                    code="missing_gold_evidence",
                    case_id=case_id,
                    field="gold_evidence",
                    message="用例必须包含可回查 PDF 的 gold_evidence。",
                )
            )

        supported_nuggets: set[str] = set()
        for evidence in gold_evidence:
            page = _page_from_mapping(evidence)
            text = str(evidence.get("text") or "").strip()
            supports = [str(item).strip() for item in _as_list(evidence.get("supports")) if str(item).strip()]
            if page is None or page < 1 or page > page_count:
                issues.append(
                    GoldValidationIssue(
                        code="gold_evidence_page_out_of_range",
                        case_id=case_id,
                        field="gold_evidence",
                        message="gold_evidence 页码不在 PDF 范围内。",
                        page=page,
                        value=json.dumps(evidence, ensure_ascii=False),
                    )
                )
                continue
            if not text:
                issues.append(
                    GoldValidationIssue(
                        code="empty_gold_evidence_text",
                        case_id=case_id,
                        field="gold_evidence",
                        message="gold_evidence.text 不能为空。",
                        page=page,
                    )
                )
                continue
            if not _text_found_on_page(text, pages.get(page, "")):
                issues.append(
                    GoldValidationIssue(
                        code="evidence_text_not_found",
                        case_id=case_id,
                        field="gold_evidence",
                        message="gold_evidence.text 未能在声明的 PDF 页文本中找到。",
                        page=page,
                        value=text,
                    )
                )
            for nugget in required_nuggets:
                if nugget in supports or phrase_matched(nugget, text):
                    supported_nuggets.add(nugget)

        if answerable and gold_evidence:
            for nugget in required_nuggets:
                if nugget not in supported_nuggets:
                    issues.append(
                        GoldValidationIssue(
                            code="required_nugget_not_supported",
                            case_id=case_id,
                            field="required_nuggets",
                            message="required_nugget 未被 gold_evidence 明确支撑。",
                            value=nugget,
                        )
                    )

        _validate_image_page_fields(issues=issues, case_id=case_id, case=case, page_count=page_count)

        forbidden_claims = [str(item).strip() for item in _as_list(case.get("forbidden_claims")) if str(item).strip()]
        forbidden_evidence = [item for item in _as_list(case.get("forbidden_evidence")) if isinstance(item, Mapping)]
        if forbidden_claims and not forbidden_evidence:
            issues.append(
                GoldValidationIssue(
                    code="missing_forbidden_evidence",
                    case_id=case_id,
                    field="forbidden_evidence",
                    message="存在 forbidden_claims 时，必须说明每个禁答项的原因。",
                )
            )

    return issues


def summarize_issues(cases: Sequence[Mapping[str, Any]], issues: Sequence[GoldValidationIssue]) -> dict[str, Any]:
    by_code: dict[str, int] = {}
    by_case: dict[str, int] = {}
    for issue in issues:
        by_code[issue.code] = by_code.get(issue.code, 0) + 1
        by_case[issue.case_id] = by_case.get(issue.case_id, 0) + 1
    return {
        "case_count": len(cases),
        "issue_count": len(issues),
        "valid_case_count": len(cases) - len(by_case),
        "invalid_case_count": len(by_case),
        "issues_by_code": dict(sorted(by_code.items())),
        "issues_by_case": dict(sorted(by_case.items())),
    }


def write_report(path: Path, cases: Sequence[Mapping[str, Any]], issues: Sequence[GoldValidationIssue]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": summarize_issues(cases, issues),
        "issues": [asdict(issue) for issue in issues],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate maintenance gold evaluation dataset against a source PDF.")
    parser.add_argument("--dataset", required=True, help="JSONL dataset path.")
    parser.add_argument("--pdf", required=True, help="Source PDF path.")
    parser.add_argument("--report", default="evaluation/results/maintenance_gold_validation_report.json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cases = load_raw_jsonl(Path(args.dataset))
    pages = load_pdf_pages(Path(args.pdf))
    issues = validate_raw_cases_against_pages(cases, pages)
    summary = summarize_issues(cases, issues)
    write_report(Path(args.report), cases, issues)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
