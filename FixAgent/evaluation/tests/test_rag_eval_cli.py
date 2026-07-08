from pathlib import Path

from evaluation.maintenance_eval_cli import read_jsonl_dataset
from evaluation.rag_eval_cli import EvalCase, RetrievedItem, evaluate_evidence_images, evaluate_retrieval_rows, read_dataset


EVALUATION_DIR = Path(__file__).resolve().parents[1]


def test_evaluate_retrieval_rows_calculates_recall_and_mrr():
    cases = [
        EvalCase(
            case_id="case_001",
            question="火花塞间隙标准是多少？",
            golden_chunk_ids=["chunk_a"],
            answerable=True,
        ),
        EvalCase(
            case_id="case_002",
            question="压缩压力低于最小值时怎么判断？",
            golden_chunk_ids=["chunk_b", "chunk_c"],
            answerable=True,
        ),
        EvalCase(
            case_id="case_003",
            question="推荐什么品牌机油？",
            golden_chunk_ids=[],
            answerable=False,
        ),
    ]
    retrieved = {
        "case_001": [
            RetrievedItem(chunk_id="chunk_a", score=0.9, content="火花塞间隙标准值是 0.7-0.9 mm。"),
            RetrievedItem(chunk_id="chunk_z", score=0.4, content="其他内容"),
        ],
        "case_002": [
            RetrievedItem(chunk_id="chunk_x", score=0.8, content="其他内容"),
            RetrievedItem(chunk_id="chunk_y", score=0.7, content="其他内容"),
            RetrievedItem(chunk_id="chunk_c", score=0.6, content="压缩压力判断内容"),
        ],
        "case_003": [
            RetrievedItem(chunk_id="chunk_a", score=0.2, content="火花塞内容"),
        ],
    }

    rows, summary = evaluate_retrieval_rows(cases, retrieved, top_k=5)

    assert rows[0]["hit_top1"] is True
    assert rows[1]["hit_top1"] is False
    assert rows[1]["hit_top3"] is True
    assert rows[1]["rank"] == 3
    assert rows[2]["skipped_for_recall"] is True
    assert summary["answerable_case_count"] == 2
    assert summary["recall_at_1"] == 0.5
    assert summary["recall_at_3"] == 1.0
    assert summary["recall_at_5"] == 1.0
    assert summary["mrr"] == 0.666667


def test_evaluate_retrieval_rows_limits_mrr_to_top_k():
    cases = [
        EvalCase(
            case_id="case_001",
            question="火花塞间隙标准是多少？",
            golden_chunk_ids=["chunk_hit"],
            answerable=True,
        ),
    ]
    retrieved = {
        "case_001": [
            RetrievedItem(chunk_id="chunk_1"),
            RetrievedItem(chunk_id="chunk_2"),
            RetrievedItem(chunk_id="chunk_3"),
            RetrievedItem(chunk_id="chunk_4"),
            RetrievedItem(chunk_id="chunk_5"),
            RetrievedItem(chunk_id="chunk_hit"),
        ],
    }

    rows, summary = evaluate_retrieval_rows(cases, retrieved, top_k=5)

    assert rows[0]["rank"] == 6
    assert rows[0]["hit_top5"] is False
    assert summary["recall_at_5"] == 0.0
    assert summary["mrr"] == 0.0


def test_read_dataset_loads_image_expectations(tmp_path):
    dataset = tmp_path / "image_cases.csv"
    dataset.write_text(
        "\n".join(
            [
                "id,question,golden_chunk_ids,answerable,expected_image_ids,expected_image_pages,forbidden_image_pages,expected_image_count_min,expected_image_count_max",
                "case_img,how to install piston rings,txt_step,true,img_21;img_22,21;22,30,2,2",
            ]
        ),
        encoding="utf-8",
    )

    cases = read_dataset(dataset)

    assert cases[0].expected_image_ids == ["img_21", "img_22"]
    assert cases[0].expected_image_pages == [21, 22]
    assert cases[0].forbidden_image_pages == [30]
    assert cases[0].expected_image_count_min == 2
    assert cases[0].expected_image_count_max == 2


def test_read_dataset_loads_stable_golden_evidence_keys(tmp_path):
    dataset = tmp_path / "stable_cases.csv"
    dataset.write_text(
        "\n".join(
            [
                "id,question,golden_chunk_ids,golden_evidence_keys,answerable",
                "case_stable,spark plug gap,old-doc:02:txt:0001,anchor:sec:0002|text|step|3|abc123,true",
            ]
        ),
        encoding="utf-8",
    )

    cases = read_dataset(dataset)

    assert cases[0].golden_chunk_ids == ["old-doc:02:txt:0001"]
    assert cases[0].golden_evidence_keys == ["anchor:sec:0002|text|step|3|abc123"]


def test_evaluate_retrieval_rows_scores_expected_images():
    cases = [
        EvalCase(
            case_id="case_img",
            question="how to install piston rings",
            golden_chunk_ids=["txt_step"],
            answerable=True,
            expected_image_ids=["img_21", "img_22"],
            expected_image_pages=[21, 22],
            forbidden_image_pages=[30],
            expected_image_count_min=2,
            expected_image_count_max=2,
        )
    ]
    retrieved = {
        "case_img": [
            RetrievedItem(chunk_id="txt_step", content="step evidence"),
            RetrievedItem(
                chunk_id="img_21",
                content="correct image",
                metadata={"chunk_type": "image", "page": 21},
            ),
            RetrievedItem(
                chunk_id="img_30",
                content="wrong image",
                metadata={"chunk_type": "image", "page": 30},
            ),
        ]
    }

    rows, summary = evaluate_retrieval_rows(cases, retrieved, top_k=5)

    assert rows[0]["expected_image_ids"] == "img_21;img_22"
    assert rows[0]["retrieved_image_ids"] == "img_21;img_30"
    assert rows[0]["retrieved_image_pages"] == "21;30"
    assert rows[0]["image_recall"] == 0.5
    assert rows[0]["image_precision"] == 0.5
    assert rows[0]["image_count_pass"] is True
    assert rows[0]["forbidden_image_pass"] is False
    assert rows[0]["image_pass"] is False
    assert rows[0]["missing_expected_image_ids"] == "img_22"
    assert rows[0]["unexpected_image_ids"] == "img_30"
    assert summary["image_case_count"] == 1
    assert summary["image_pass_rate"] == 0.0
    assert summary["avg_image_recall"] == 0.5
    assert summary["avg_image_precision"] == 0.5


def test_evaluate_retrieval_rows_matches_stable_source_anchor_when_chunk_id_drifted():
    cases = [
        EvalCase(
            case_id="case_stable",
            question="spark plug gap?",
            golden_chunk_ids=["old-doc:02:txt:0001"],
            golden_evidence_keys=["anchor:sec:0002|text|step|3|abc123"],
            answerable=True,
        )
    ]
    retrieved = {
        "case_stable": [
            RetrievedItem(
                chunk_id="new-doc:02:txt:0001",
                content="spark plug gap 0.7-0.9mm",
                metadata={"source_anchor": "sec:0002|text|step|3|abc123"},
            )
        ]
    }

    rows, summary = evaluate_retrieval_rows(cases, retrieved, top_k=5)

    assert rows[0]["rank"] == 1
    assert rows[0]["hit_top1"] is True
    assert rows[0]["matched_evidence_key"] == "anchor:sec:0002|text|step|3|abc123"
    assert summary["recall_at_1"] == 1.0
    assert summary["mrr"] == 1.0


def test_evaluate_retrieval_rows_matches_chunk_id_tail_when_document_prefix_drifted():
    cases = [
        EvalCase(
            case_id="case_tail",
            question="spark plug gap?",
            golden_chunk_ids=["old-doc:02:txt:0001"],
            answerable=True,
        )
    ]
    retrieved = {
        "case_tail": [
            RetrievedItem(chunk_id="new-doc:02:txt:0001", content="spark plug gap 0.7-0.9mm")
        ]
    }

    rows, summary = evaluate_retrieval_rows(cases, retrieved, top_k=5)

    assert rows[0]["rank"] == 1
    assert rows[0]["matched_evidence_key"] == "id_tail:02:txt:0001"
    assert summary["recall_at_1"] == 1.0


def test_evaluate_retrieval_rows_matches_required_facts_when_ids_and_anchors_drifted():
    cases = [
        EvalCase(
            case_id="case_fact",
            question="spark plug gap?",
            golden_chunk_ids=["old-doc:99:txt:9999"],
            required_facts=["0.7 到 0.9 mm"],
            answerable=True,
        )
    ]
    retrieved = {
        "case_fact": [
            RetrievedItem(
                chunk_id="new-doc:02:txt:0001",
                content="间隙标准值 ： 0.7 ～ 0.9 mm",
            )
        ]
    }

    rows, summary = evaluate_retrieval_rows(cases, retrieved, top_k=5)

    assert rows[0]["rank"] == 1
    assert rows[0]["matched_evidence_key"] == "fact:0.7 到 0.9 mm"
    assert summary["recall_at_1"] == 1.0


def test_evaluate_retrieval_rows_dedupes_image_summary_for_page_only_expectations():
    cases = [
        EvalCase(
            case_id="case_page_img",
            question="show water pump list",
            answerable=True,
            expected_image_pages=[25],
            expected_image_count_min=1,
            expected_image_count_max=1,
        )
    ]
    retrieved = {
        "case_page_img": [
            RetrievedItem(
                chunk_id="manual:img:0000",
                metadata={"chunk_type": "image", "page": 25},
            ),
            RetrievedItem(
                chunk_id="manual:ims:0000",
                metadata={"chunk_type": "image_summary", "page": 25},
            ),
        ]
    }

    rows, summary = evaluate_retrieval_rows(cases, retrieved, top_k=5)

    assert rows[0]["retrieved_image_pages"] == "25"
    assert rows[0]["image_count_pass"] is True
    assert rows[0]["image_pass"] is True
    assert summary["image_pass_rate"] == 1.0


def test_evaluate_evidence_images_accepts_final_api_shape():
    case = EvalCase(
        case_id="case_api_images",
        question="show transmission list",
        expected_image_pages=[35],
        expected_image_count_min=1,
        expected_image_count_max=1,
    )

    metrics = evaluate_evidence_images(
        case,
        [
            {
                "imageUrl": "https://example.test/page35.png",
                "caption": "transmission list",
                "page": 35,
                "sectionTitle": "8.2 传动主副轴装配部件清单",
                "documentId": "manual-doc",
                "sourceChunkId": "manual-doc:35:img:0000",
                "contextRole": "direct_lookup",
            }
        ],
    )

    assert metrics["retrieved_image_ids"] == "manual-doc:35:img:0000"
    assert metrics["retrieved_image_pages"] == "35"
    assert metrics["image_pass"] is True


def test_maintenance_eval_dataset_contains_real_regression_cases():
    cases = read_jsonl_dataset(EVALUATION_DIR / "maintenance_eval_dataset_v1.jsonl")

    by_id = {case.case_id: case for case in cases}

    assert len(cases) == 40

    assert by_id["manual_e2e_018"].query == "如何拆卸气门？"
    assert by_id["manual_e2e_018"].expected_image_order == [16]
    assert by_id["manual_e2e_018"].forbidden_images[0]["page"] == 17
    assert "冷却30分钟以上" in by_id["manual_e2e_018"].forbidden_claims

    assert by_id["manual_e2e_022"].query == "如何安装气缸与活塞？"
    assert by_id["manual_e2e_022"].expected_image_order == [19, 20, 21]
    assert by_id["manual_e2e_022"].expected_step_order == [
        "安装全新的箱体缸体垫片",
        "将活塞头部插入气缸裙部",
        "安装活塞销",
        "安装活塞销挡圈",
    ]

    assert by_id["manual_e2e_034"].query == "传动主副轴装配部件清单完整列一下"
    assert len(by_id["manual_e2e_034"].required_nuggets) == 11

    assert by_id["manual_e2e_039"].answerable is False
    assert by_id["manual_e2e_040"].answerable is False
