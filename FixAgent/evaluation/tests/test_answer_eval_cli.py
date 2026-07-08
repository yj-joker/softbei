from evaluation.answer_eval_cli import (
    _fact_matched,
    build_rag_answer_messages,
    build_answer_eval_row,
    summarize_answer_eval_rows,
)
from evaluation.rag_eval_cli import EvalCase, RetrievedItem


def test_build_answer_eval_row_scores_exact_numeric_answer_with_retrieval_hit():
    case = EvalCase(
        case_id="case_001",
        question="spark plug gap?",
        golden_answer="The spark plug gap is 0.7 to 0.9 mm.",
        golden_chunk_ids=["chunk_a"],
        answerable=True,
        question_type="spec",
    )
    retrieved = [
        RetrievedItem(chunk_id="chunk_z", content="other"),
        RetrievedItem(chunk_id="chunk_a", content="gap 0.7-0.9 mm"),
    ]

    row = build_answer_eval_row(
        case=case,
        generated_answer="The standard gap is 0.7-0.9 mm.",
        retrieved_items=retrieved,
        latency_ms=123,
    )

    assert row["retrieval_hit_top5"] is True
    assert row["retrieval_rank"] == 2
    assert row["answer_score"] == 1.0
    assert row["answer_pass"] is True
    assert row["grounded_pass"] is True
    assert row["hallucination"] is False
    assert row["failure_type"] == "pass"


def test_build_answer_eval_row_scores_partial_text_overlap():
    case = EvalCase(
        case_id="case_002",
        question="tightening order?",
        golden_answer="Tighten the bolts evenly in diagonal order.",
        golden_chunk_ids=["chunk_b"],
        answerable=True,
        question_type="procedure",
    )

    row = build_answer_eval_row(
        case=case,
        generated_answer="Tighten the bolts evenly.",
        retrieved_items=[RetrievedItem(chunk_id="chunk_b", content="diagonal order")],
    )

    assert row["answer_score"] == 0.5
    assert row["answer_pass"] is False
    assert row["failure_type"] == "partial_answer"


def test_build_answer_eval_row_uses_required_facts_instead_of_full_golden_answer():
    case = EvalCase(
        case_id="case_024",
        question="how many tightening passes?",
        golden_answer="Tighten three times: 25 N.m, 45 N.m, then 60 N.m.",
        golden_chunk_ids=["chunk_m10"],
        answerable=True,
        question_type="torque",
    )
    case.required_facts = ["three times"]
    case.optional_facts = ["25 N.m", "45 N.m", "60 N.m"]

    row = build_answer_eval_row(
        case=case,
        generated_answer="Three times.",
        retrieved_items=[RetrievedItem(chunk_id="chunk_m10", content="tighten three times")],
    )

    assert row["answer_score"] == 1.0
    assert row["answer_pass"] is True
    assert row["failure_type"] == "pass"


def test_build_answer_eval_row_includes_image_grounding_metrics():
    case = EvalCase(
        case_id="case_img",
        question="show piston ring installation",
        golden_answer="Install piston rings with the matching figures.",
        golden_chunk_ids=["step_chunk"],
        answerable=True,
        question_type="procedure",
        expected_image_pages=[21, 22],
        expected_image_count_min=2,
        expected_image_count_max=2,
    )

    row = build_answer_eval_row(
        case=case,
        generated_answer="Install the piston rings according to the manual.",
        retrieved_items=[
            RetrievedItem(chunk_id="step_chunk", content="Install the piston rings according to the manual."),
            RetrievedItem(chunk_id="img_21", metadata={"chunk_type": "image", "page": 21}),
            RetrievedItem(chunk_id="img_22", metadata={"chunk_type": "image", "page": 22}),
        ],
    )

    assert row["retrieved_image_pages"] == "21;22"
    assert row["image_recall"] == 1.0
    assert row["image_precision"] == 1.0
    assert row["image_count_pass"] is True
    assert row["image_pass"] is True


def test_build_answer_eval_row_uses_stable_evidence_key_for_grounding():
    case = EvalCase(
        case_id="case_stable",
        question="spark plug gap?",
        golden_answer="The spark plug gap is 0.7 to 0.9 mm.",
        golden_chunk_ids=["old-doc:02:txt:0001"],
        golden_evidence_keys=["anchor:sec:0002|text|step|3|abc123"],
        answerable=True,
        question_type="spec",
    )

    row = build_answer_eval_row(
        case=case,
        generated_answer="The standard gap is 0.7-0.9 mm.",
        retrieved_items=[
            RetrievedItem(
                chunk_id="new-doc:02:txt:0001",
                content="gap 0.7-0.9 mm",
                metadata={"source_anchor": "sec:0002|text|step|3|abc123"},
            )
        ],
    )

    assert row["retrieval_hit_top5"] is True
    assert row["retrieval_rank"] == 1
    assert row["matched_evidence_key"] == "anchor:sec:0002|text|step|3|abc123"
    assert row["grounded_pass"] is True


def test_build_answer_eval_row_prefers_final_evidence_images_when_provided():
    case = EvalCase(
        case_id="case_api_img",
        question="show transmission list",
        golden_answer="Show the transmission list.",
        answerable=True,
        question_type="inventory",
        expected_image_pages=[35],
        expected_image_count_min=1,
        expected_image_count_max=1,
    )

    row = build_answer_eval_row(
        case=case,
        generated_answer="Show the transmission list.",
        retrieved_items=[],
        evidence_images=[
            {
                "imageUrl": "https://example.test/page35.png",
                "page": 35,
                "sourceChunkId": "manual-doc:35:img:0000",
            }
        ],
    )

    assert row["retrieved_image_ids"] == "manual-doc:35:img:0000"
    assert row["retrieved_image_pages"] == "35"
    assert row["image_pass"] is True


def test_fact_matching_accepts_condition_word_variants():
    assert _fact_matched(
        "\u5982\u6709\u5f2f\u66f2\u3001\u635f\u574f\u6216\u88c2\u7eb9",
        "\u5f02\u5e38\u8868\u73b0\uff1a\u5f2f\u66f2\u3001\u635f\u574f\u6216\u88c2\u7eb9",
    )


def test_fact_matching_accepts_pressure_increase_synonym():
    assert _fact_matched(
        "\u538b\u529b\u5347\u9ad8",
        "\u82e5\u538b\u529b\u6bd4\u52a0\u673a\u6cb9\u524d\u9ad8\uff0c\u5219\u662f\u6d3b\u585e\u73af\u78e8\u635f\u6216\u635f\u574f\u3002",
    )


def test_build_answer_eval_row_accepts_refusal_for_no_answer_case():
    case = EvalCase(
        case_id="case_003",
        question="recommended oil brand?",
        golden_answer="The manual does not provide this information.",
        answerable=False,
        question_type="no_answer",
    )

    row = build_answer_eval_row(
        case=case,
        generated_answer="No relevant information was found in the manual.",
        retrieved_items=[],
    )

    assert row["answer_score"] == 1.0
    assert row["answer_pass"] is True
    assert row["grounded_pass"] is True
    assert row["hallucination"] is False
    assert row["failure_type"] == "pass"


def test_build_answer_eval_row_accepts_chinese_manual_refusal_for_no_answer_case():
    case = EvalCase(
        case_id="case_003",
        question="recommended oil brand?",
        golden_answer="The manual does not provide this information.",
        answerable=False,
        question_type="no_answer",
    )

    row = build_answer_eval_row(
        case=case,
        generated_answer="\u8d44\u6599\u4e2d\u672a\u627e\u5230\u660e\u786e\u4f9d\u636e\u3002",
        retrieved_items=[],
    )

    assert row["answer_score"] == 1.0
    assert row["answer_pass"] is True
    assert row["failure_type"] == "pass"


def test_build_answer_eval_row_marks_no_answer_false_positive_as_hallucination():
    case = EvalCase(
        case_id="case_004",
        question="recommended oil brand?",
        golden_answer="The manual does not provide this information.",
        answerable=False,
        question_type="no_answer",
    )

    row = build_answer_eval_row(
        case=case,
        generated_answer="Use Brand X 10W-40 engine oil.",
        retrieved_items=[],
    )

    assert row["answer_score"] == 0.0
    assert row["answer_pass"] is False
    assert row["grounded_pass"] is False
    assert row["hallucination"] is True
    assert row["failure_type"] == "no_answer_false_positive"


def test_summarize_answer_eval_rows_calculates_core_metrics():
    rows = [
        {"answerable": True, "answer_score": 1.0, "answer_pass": True, "grounded_pass": True, "hallucination": False},
        {"answerable": True, "answer_score": 0.5, "answer_pass": False, "grounded_pass": True, "hallucination": False},
        {"answerable": True, "answer_score": 0.0, "answer_pass": False, "grounded_pass": False, "hallucination": True},
        {"answerable": False, "answer_score": 1.0, "answer_pass": True, "grounded_pass": True, "hallucination": False},
    ]

    summary = summarize_answer_eval_rows(rows)

    assert summary["case_count"] == 4
    assert summary["answerable_case_count"] == 3
    assert summary["answer_accuracy"] == 0.5
    assert summary["answerable_accuracy"] == 0.333333
    assert summary["avg_score"] == 0.625
    assert summary["grounded_rate"] == 0.75
    assert summary["hallucination_rate"] == 0.25
    assert summary["no_answer_correct_rate"] == 1.0


def test_summarize_answer_eval_rows_includes_image_metrics_when_present():
    rows = [
        {
            "answerable": True,
            "answer_score": 1.0,
            "answer_pass": True,
            "grounded_pass": True,
            "hallucination": False,
            "image_eval_required": True,
            "image_pass": True,
            "image_recall": 1.0,
            "image_precision": 1.0,
        },
        {
            "answerable": True,
            "answer_score": 1.0,
            "answer_pass": True,
            "grounded_pass": True,
            "hallucination": False,
            "image_eval_required": True,
            "image_pass": False,
            "image_recall": 0.5,
            "image_precision": 1.0,
        },
    ]

    summary = summarize_answer_eval_rows(rows)

    assert summary["image_case_count"] == 2
    assert summary["image_pass_rate"] == 0.5
    assert summary["avg_image_recall"] == 0.75
    assert summary["avg_image_precision"] == 1.0


def test_build_rag_answer_messages_adds_inspection_answer_structure():
    case = EvalCase(
        case_id="case_009",
        question="what if the shaft does not rotate smoothly?",
        golden_answer="Replace the starter motor if the shaft does not rotate smoothly.",
        answerable=True,
        question_type="inspection",
    )

    messages = build_rag_answer_messages(
        case,
        [
            RetrievedItem(
                chunk_id="chunk_a",
                content="If the starter motor shaft does not rotate smoothly, replace the starter motor.",
                metadata={"chunk_type": "text"},
            )
        ],
    )

    joined = "\n".join(message["content"] for message in messages)
    assert "检查对象" in joined
    assert "正常标准" in joined
    assert "异常表现" in joined
    assert "处理方式" in joined
    assert "核心命中证据" in joined
