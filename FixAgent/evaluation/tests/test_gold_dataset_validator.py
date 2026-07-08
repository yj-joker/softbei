from evaluation.gold_dataset_validator import (
    validate_raw_cases_against_pages,
)


def test_gold_validator_accepts_case_with_traceable_pdf_evidence():
    pages = {
        3: "1.2 检查火花塞\n间隙标准值：0.7～0.9 mm\n1.3 安装火花塞",
    }
    cases = [
        {
            "case_id": "manual_e2e_001",
            "query": "火花塞间隙标准是多少？",
            "target_section": "1.2 检查火花塞",
            "target_pages": [3],
            "answerable": True,
            "required_nuggets": ["火花塞间隙标准值为0.7～0.9 mm"],
            "gold_evidence": [
                {
                    "page": 3,
                    "text": "间隙标准值：0.7～0.9 mm",
                    "supports": ["火花塞间隙标准值为0.7～0.9 mm"],
                }
            ],
            "expected_images": [{"page": 3, "role": "火花塞检查与安装图"}],
        }
    ]

    issues = validate_raw_cases_against_pages(cases, pages)

    assert issues == []


def test_gold_validator_requires_gold_evidence_for_answerable_cases():
    pages = {3: "1.2 检查火花塞\n间隙标准值：0.7～0.9 mm"}
    cases = [
        {
            "case_id": "manual_e2e_001",
            "query": "火花塞间隙标准是多少？",
            "target_pages": [3],
            "answerable": True,
            "required_nuggets": ["火花塞间隙标准值为0.7～0.9 mm"],
        }
    ]

    issues = validate_raw_cases_against_pages(cases, pages)

    assert [issue.code for issue in issues] == ["missing_gold_evidence"]


def test_gold_validator_rejects_evidence_text_that_is_not_on_declared_pdf_page():
    pages = {3: "1.2 检查火花塞\n间隙标准值：0.7～0.9 mm"}
    cases = [
        {
            "case_id": "manual_e2e_001",
            "query": "火花塞间隙标准是多少？",
            "target_pages": [3],
            "answerable": True,
            "required_nuggets": ["火花塞间隙标准值为0.7～0.9 mm"],
            "gold_evidence": [
                {
                    "page": 3,
                    "text": "间隙标准值：0.6～0.8 mm",
                    "supports": ["火花塞间隙标准值为0.7～0.9 mm"],
                }
            ],
        }
    ]

    issues = validate_raw_cases_against_pages(cases, pages)

    assert any(issue.code == "evidence_text_not_found" for issue in issues)


def test_gold_validator_checks_expected_image_pages_are_valid():
    pages = {3: "1.2 检查火花塞\n间隙标准值：0.7～0.9 mm"}
    cases = [
        {
            "case_id": "manual_e2e_001",
            "query": "火花塞间隙标准是多少？",
            "target_pages": [3],
            "answerable": True,
            "required_nuggets": ["火花塞间隙标准值为0.7～0.9 mm"],
            "gold_evidence": [
                {
                    "page": 3,
                    "text": "间隙标准值：0.7～0.9 mm",
                    "supports": ["火花塞间隙标准值为0.7～0.9 mm"],
                }
            ],
            "expected_images": [{"page": 99}],
        }
    ]

    issues = validate_raw_cases_against_pages(cases, pages)

    assert any(issue.code == "expected_image_page_out_of_range" for issue in issues)
