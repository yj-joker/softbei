from evaluation.rag_eval_cli import EvalCase, RetrievedItem, evaluate_retrieval_rows


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
