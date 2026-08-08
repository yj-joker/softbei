import csv
import json
import threading
import time
from pathlib import Path

import evaluation.maintenance_eval_cli as eval_cli
from evaluation.maintenance_eval_cli import (
    MaintenanceEvalCase,
    MaintenanceEvalTurn,
    aggregate_case_rows,
    build_run_manifest,
    evaluate_case_output,
    main,
    read_jsonl_dataset,
    run_cases,
    summarize_results,
    summarize_rows,
)
from evaluation.maintenance_eval_schema import AllowedSource, ClaimConstraint


def test_read_jsonl_dataset_loads_structured_maintenance_case(tmp_path: Path):
    dataset = tmp_path / "maintenance_eval.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "case_id": "manual_e2e_001",
                "query": "如何拆卸气门",
                "task_type": "procedure",
                "intent_action": "拆卸",
                "target_section": "4.8 气门",
                "target_pages": [16],
                "answerable": True,
                "required_nuggets": ["取下滑动挺柱", "使用气门拆装器压缩气门弹簧"],
                "forbidden_claims": ["冷却30分钟以上"],
                "expected_step_order": ["取下滑动挺柱", "压缩气门弹簧", "拆下气门锁夹"],
                "expected_images": [{"page": 16, "role": "拆卸气门图"}],
                "expected_image_order": [16],
                "step_image_mapping": [{"step": "压缩气门弹簧", "page": 16}],
                "forbidden_images": [{"page": 17, "reason": "安装气门图"}],
                "difficulty": "hard",
                "trap_type": ["opposite_action", "adjacent_page"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    cases = read_jsonl_dataset(dataset)

    assert len(cases) == 1
    assert cases[0].case_id == "manual_e2e_001"
    assert cases[0].required_nuggets == ["取下滑动挺柱", "使用气门拆装器压缩气门弹簧"]
    assert cases[0].expected_images[0]["page"] == 16
    assert cases[0].trap_type == ["opposite_action", "adjacent_page"]


def test_run_cases_executes_independent_cases_in_parallel_and_returns_input_order(monkeypatch) -> None:
    active = 0
    max_active = 0
    lock = threading.Lock()

    def fake_request(*args, **kwargs):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        return eval_cli.CaseRunResult(answer="ok", metadata={})

    monkeypatch.setattr(eval_cli, "_chat_api_request", fake_request)
    cases = [
        MaintenanceEvalCase(case_id=f"parallel-{index}", query="q")
        for index in range(6)
    ]
    rows = run_cases(
        cases,
        mode="api",
        endpoint="http://test/ai/chat",
        timeout=5,
        run_id="parallel-run",
        concurrency=3,
    )

    assert [row["case_id"] for row in rows] == [f"parallel-{index}" for index in range(6)]
    assert max_active >= 2


def test_request_context_exposes_only_internal_paired_route_contract() -> None:
    contract = {
        "intent_decision": {"intent": "knowledge_query", "task_action": "find_cause"},
        "query_contract": {"raw_query": "pump fault", "component": "pump"},
    }
    case = MaintenanceEvalCase(
        case_id="paired-contract",
        query="pump fault",
        candidate_metadata={
            "_paired_route_contract": contract,
            "unrelated_candidate_metadata": "must-not-leak",
        },
    )

    assert eval_cli._request_context_for_case(case) == {
        "_evaluation_route_contract": contract,
    }


def test_evaluate_case_output_scores_nuggets_order_forbidden_and_images():
    case = MaintenanceEvalCase(
        case_id="manual_e2e_002",
        query="如何安装气缸与活塞",
        task_type="procedure",
        intent_action="安装",
        target_section="5.4 安装气缸与活塞",
        target_pages=[19, 20, 21],
        required_nuggets=[
            "安装全新的箱体缸体垫片",
            "将活塞头部插入气缸裙部",
            "安装活塞销",
            "安装活塞销挡圈",
        ],
        expected_step_order=[
            "安装全新的箱体缸体垫片",
            "将活塞头部插入气缸裙部",
            "安装活塞销",
            "安装活塞销挡圈",
        ],
        forbidden_claims=["先安装活塞销挡圈再安装活塞销"],
        expected_images=[{"page": 19, "role": "垫片和插入气缸"}, {"page": 21, "role": "活塞销和挡圈"}],
        expected_image_order=[19, 21],
        step_image_mapping=[
            {"step": "安装全新的箱体缸体垫片", "page": 19},
            {"step": "安装活塞销", "page": 21},
        ],
        forbidden_images=[{"page": 18, "reason": "拆卸气缸与活塞"}],
    )

    answer = (
        "1. 安装全新的箱体缸体垫片。\n"
        "2. 将活塞头部插入气缸裙部。\n"
        "3. 安装活塞销。\n"
        "4. 安装活塞销挡圈。"
    )
    row = evaluate_case_output(
        case,
        answer,
        evidence_images=[
            {"page": 19, "caption": "安装垫片"},
            {"page": 21, "caption": "安装活塞销"},
        ],
    )

    assert row["required_nugget_recall"] == 1.0
    assert row["procedure_order_pass"] is True
    assert row["forbidden_claim_pass"] is True
    assert row["image_pass"] is True
    assert row["image_order_pass"] is True
    assert row["step_image_binding_pass"] is True
    assert row["answer_correct_pass"] is True
    assert row["evidence_pass"] is True
    assert row["delivery_pass"] is True
    assert row["mechanism_pass"] is True
    assert row["final_pass"] is True


def test_evaluate_case_output_exports_rag_variant_audit_fields():
    case = MaintenanceEvalCase(
        case_id="kg_ablation_audit",
        query="气门间隙是多少？",
        required_nuggets=["进气门0.13～0.20 mm"],
    )
    metadata = {
        "rag_variant": "no_graph",
        "graph_candidate_query_count": 0,
        "graph_candidate_count": 0,
        "graph_tool_call_count": 0,
        "graph_tools_used": [],
        "graph_review_enabled": False,
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        "cost": 0.0123,
    }

    row = evaluate_case_output(
        case,
        "进气门间隙为0.13～0.20 mm。",
        metadata=metadata,
    )

    assert row["rag_variant"] == "no_graph"
    assert row["graph_candidate_query_count"] == 0
    assert row["graph_candidate_count"] == 0
    assert row["graph_tool_call_count"] == 0
    assert row["graph_tools_used"] == ""
    assert row["graph_review_enabled"] is False
    assert row["input_tokens"] == 10
    assert row["output_tokens"] == 20
    assert row["total_tokens"] == 30
    assert row["cost"] == 0.0123


def test_evaluate_case_output_exports_complete_graph_mechanism_fields(tmp_path: Path):
    case = MaintenanceEvalCase(case_id="graph-mechanism-audit", query="Why did it fail?")
    metadata = {
        "graph_candidate_status": "found",
        "graph_candidate_reason": "candidate_match",
        "graph_retrieval_status": "partial",
        "graph_retrieval_reason": "one_path_rejected",
        "graph_qualified_count": 4,
        "graph_routing_only_count": 2,
        "graph_rejected_count": 1,
        "graph_evidence_ids": ["graph:z", "", "graph:a", "graph:z"],
        "claim_evidence_bindings": [
            {
                "claim_text": "cause claim",
                "evidence_ids": ["graph:z"],
                "claim_id": "claim-1",
            }
        ],
        "graph_evidence_used_ids": ["graph:z", "", "graph:z", "graph:a"],
        "graph_relationship_types": ["CAUSES", "", "OWNS", "CAUSES"],
        "graph_provenance_statuses": ["manual_backed", "", "verified", "manual_backed"],
        "graph_retrieval_latency_ms": "27",
    }

    row = evaluate_case_output(case, "The graph-backed cause is shown.", metadata=metadata)

    assert row["graph_candidate_status"] == "found"
    assert row["graph_candidate_reason"] == "candidate_match"
    assert row["graph_retrieval_status"] == "partial"
    assert row["graph_retrieval_reason"] == "one_path_rejected"
    assert row["graph_qualified_count"] == 4
    assert row["graph_routing_only_count"] == 2
    assert row["graph_rejected_count"] == 1
    assert row["graph_evidence_ids"] == "graph:z;graph:a"
    assert row["claim_evidence_bindings"] == (
        '[{"claim_id":"claim-1","claim_text":"cause claim",'
        '"evidence_ids":["graph:z"]}]'
    )
    assert row["graph_evidence_used_ids"] == "graph:z;graph:a"
    assert row["graph_evidence_used_count"] == 2
    assert row["graph_relationship_types"] == "CAUSES;OWNS"
    assert row["graph_provenance_statuses"] == "manual_backed;verified"
    assert row["graph_retrieval_latency_ms"] == 27

    csv_path = tmp_path / "graph-mechanism.csv"
    eval_cli.write_rows(csv_path, [row])
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        written = next(reader)

    expected_fields = {
        "graph_candidate_status",
        "graph_candidate_reason",
        "graph_retrieval_status",
        "graph_retrieval_reason",
        "graph_qualified_count",
        "graph_routing_only_count",
        "graph_rejected_count",
        "graph_evidence_ids",
        "claim_evidence_bindings",
        "graph_evidence_used_ids",
        "graph_evidence_used_count",
        "graph_relationship_types",
        "graph_provenance_statuses",
        "graph_retrieval_latency_ms",
    }
    assert expected_fields <= set(reader.fieldnames or [])
    assert written["graph_evidence_used_ids"] == "graph:z;graph:a"
    assert written["graph_evidence_used_count"] == "2"


def test_graph_required_case_cannot_pass_without_complete_used_binding_chain() -> None:
    case = MaintenanceEvalCase(
        case_id="graph-required-unbound",
        query="Which graph relation explains the fault?",
        graph_dependency="required",
        required_nuggets=["fault relation"],
    )
    metadata = {
        "rag_variant": "graph_full",
        "graph_candidate_count": 1,
        "graph_qualified_count": 1,
        "graph_evidence_ids": ["graph:path-1:none"],
        "graph_evidence_used_ids": [],
        "claim_evidence_bindings": [],
    }

    row = evaluate_case_output(case, "fault relation", metadata=metadata)

    assert row["grounding_pass"] is True
    assert row["graph_required_mechanism_pass"] is False
    assert row["final_pass"] is False
    assert "graph_evidence_used_count" in row["graph_required_mechanism_failures"]
    assert "graph_claim_binding_count" in row["graph_required_mechanism_failures"]


def test_graph_required_case_passes_mechanism_gate_with_bound_graph_evidence() -> None:
    case = MaintenanceEvalCase(
        case_id="graph-required-bound",
        query="Which graph relation explains the fault?",
        graph_dependency="required",
        required_nuggets=["fault relation"],
    )
    metadata = {
        "rag_variant": "graph_full",
        "graph_candidate_count": 1,
        "graph_qualified_count": 1,
        "graph_evidence_ids": ["graph:path-1:none"],
        "graph_evidence_used_ids": ["graph:path-1:none"],
        "claim_evidence_bindings": [
            {
                "claim_id": "aspect-fault",
                "claim_type": "fault_relation",
                "emitted": True,
                "evidence_ids": ["graph:path-1:none"],
            }
        ],
    }

    row = evaluate_case_output(case, "fault relation", metadata=metadata)

    assert row["graph_required_mechanism_pass"] is True
    assert row["graph_required_mechanism_failures"] == ""
    assert row["final_pass"] is True


def test_summarize_rows_reports_p50_and_p95_latency():
    rows = [
        {"answerable": True, "latency_ms": 100},
        {"answerable": True, "latency_ms": 200},
    ]

    summary = summarize_rows(rows)

    assert summary["avg_latency_ms"] == 150.0
    assert summary["p50_latency_ms"] == 150.0
    assert summary["p95_latency_ms"] == 195.0


def test_evaluate_case_output_exports_v2_grouping_fields():
    case = MaintenanceEvalCase(
        case_id="blind_group_001",
        schema_version="2.0",
        split="blind_test",
        question_type="relation_disambiguation",
        graph_dependency="required",
        question_origin="human_authored",
        query="两个相似故障应如何区分？",
    )

    row = evaluate_case_output(case, "需要依据证据区分。")

    assert row["schema_version"] == "2.0"
    assert row["split"] == "blind_test"
    assert row["question_type"] == "relation_disambiguation"
    assert row["graph_dependency"] == "required"
    assert row["question_origin"] == "human_authored"


def test_run_manifest_records_server_reported_rag_variants(tmp_path: Path):
    dataset = tmp_path / "blind.jsonl"
    dataset.write_text('{"case_id":"case-1","query":"测试"}\n', encoding="utf-8")
    case = MaintenanceEvalCase(case_id="case-1", query="测试", dataset_source=dataset.name)

    manifest = build_run_manifest(
        run_id="run-1",
        started_at="2026-08-05T00:00:00+00:00",
        dataset_paths=[dataset],
        cases=[case],
        turn_rows=[{"rag_variant": "no_graph"}],
        mode="api",
        endpoint="http://127.0.0.1:8001/ai/chat",
        timeout=120,
        default_device_type="",
        default_document_id="",
    )

    assert manifest["rag_variants"] == ["no_graph"]


def test_evaluate_case_output_catches_good_page_bad_order_and_unsupported_claims():
    case = MaintenanceEvalCase(
        case_id="manual_e2e_003",
        query="如何安装气缸与活塞",
        task_type="procedure",
        intent_action="安装",
        target_section="5.4 安装气缸与活塞",
        target_pages=[19, 20, 21],
        required_nuggets=["安装活塞销", "安装活塞销挡圈"],
        expected_step_order=["安装活塞销", "安装活塞销挡圈"],
        forbidden_claims=["冷却30分钟以上"],
        expected_images=[{"page": 19}, {"page": 21}],
        expected_image_order=[19, 21],
    )

    row = evaluate_case_output(
        case,
        "先安装活塞销挡圈，再安装活塞销。操作前冷却30分钟以上。",
        evidence_images=[{"page": 21}, {"page": 19}],
    )

    assert row["required_nugget_recall"] == 1.0
    assert row["procedure_order_pass"] is False
    assert row["forbidden_claim_pass"] is False
    assert row["image_recall"] == 1.0
    assert row["image_precision"] == 1.0
    assert row["image_order_pass"] is False
    assert row["final_pass"] is False


def test_evaluate_case_output_allows_minor_insertions_inside_order_snippet():
    case = MaintenanceEvalCase(
        case_id="manual_e2e_spark_install",
        query="安装火花塞时应该怎么预紧和拧紧？",
        task_type="procedure",
        expected_step_order=[
            "将火花塞放入气缸头",
            "顺时针转动3圈预紧",
            "再转动1/4圈",
            "将高压帽套进火花塞并压紧",
        ],
        required_nuggets=["顺时针转动3圈预紧"],
    )

    row = evaluate_case_output(
        case,
        (
            "1. 将火花塞放入气缸头，套上火花塞专用套筒，"
            "顺时针转动 3 圈预紧，然后再转动 1/4 圈。"
            "2. 用尖嘴钳将高压帽套进火花塞并用力往下压紧。"
        ),
    )

    assert row["procedure_order_pass"] is True


def test_evaluate_case_output_matches_table_separators_inside_required_nuggets():
    case = MaintenanceEvalCase(
        case_id="manual_e2e_valve_clearance",
        query="进气门和排气门间隙标准分别是多少？",
        required_nuggets=["进气门0.13～0.20 mm", "排气门0.20～0.30 mm"],
    )

    row = evaluate_case_output(
        case,
        (
            "气门类型 | 标准间隙范围\n"
            "进气门 | 0.13～0.20 mm\n"
            "排气门=0.20～0.30 mm"
        ),
    )

    assert row["required_nugget_recall"] == 1.0


def test_evaluate_case_output_matches_include_word_with_parenthetical_fact():
    case = MaintenanceEvalCase(
        case_id="manual_e2e_valve_count",
        query="如何拆卸气门？",
        required_nuggets=["气门包括进气门×2、排气门×2"],
    )

    row = evaluate_case_output(
        case,
        "依次拆下气门（进气门 ×2，排气门 ×2）。",
    )

    assert row["required_nugget_recall"] == 1.0


def test_evaluate_case_output_matches_x_then_y_when_answer_orders_both_parts():
    case = MaintenanceEvalCase(
        case_id="manual_e2e_then_order",
        query="如何安装气缸与活塞？",
        required_nuggets=["先安装活塞销，再安装活塞销挡圈"],
    )

    row = evaluate_case_output(
        case,
        "（3）安装活塞销\n将活塞销插入活塞销孔与连杆小端孔。\n（4）安装活塞销挡圈",
    )

    assert row["required_nugget_recall"] == 1.0


def test_evaluate_case_output_matches_ordered_put_items_under_shared_put_heading():
    case = MaintenanceEvalCase(
        case_id="manual_e2e_oil_pump_install",
        query="如何安装机油泵？",
        task_type="procedure",
        expected_step_order=[
            "放入两个φ8定位销",
            "放入机油泵座垫",
            "放入机油泵",
            "放入3个φ10定位销",
            "放入3个O型圈",
            "放入3颗螺栓",
        ],
        required_nuggets=["锁紧螺栓并用定扭扳手校验扭力"],
    )

    row = evaluate_case_output(
        case,
        (
            "1. 依次放入：\n"
            "两个 φ8 定位销\n"
            "机油泵座垫\n"
            "机油泵\n"
            "φ\n"
            "3个 10 定位销\n"
            "3个 O型圈\n"
            "3颗螺栓（其中 M6×30 螺栓需安装铜垫）\n"
            "2. 锁紧螺栓并用定扭扳手校验扭力。"
        ),
    )

    assert row["procedure_order_pass"] is True


def test_summarize_rows_reports_layered_rates_in_chinese_friendly_keys():
    rows = [
        {
            "answerable": True,
            "final_pass": True,
            "grounding_pass": True,
            "expected_step_order": "步骤一；步骤二",
            "procedure_order_pass": True,
            "image_pass": True,
            "image_eval_required": True,
            "required_nugget_recall": 1.0,
            "forbidden_claim_pass": True,
            "refusal_pass": True,
        },
        {
            "answerable": True,
            "final_pass": False,
            "grounding_pass": False,
            "expected_step_order": "步骤一；步骤二",
            "procedure_order_pass": False,
            "image_pass": False,
            "image_eval_required": True,
            "required_nugget_recall": 0.5,
            "forbidden_claim_pass": False,
            "refusal_pass": True,
        },
    ]

    summary = summarize_rows(rows)

    assert summary["case_count"] == 2
    assert summary["final_pass_rate"] == 0.5
    assert summary["avg_required_nugget_recall"] == 0.75
    assert summary["grounding_pass_rate"] == 0.5
    assert summary["procedure_order_pass_rate"] == 0.5
    assert summary["image_pass_rate"] == 0.5
    assert summary["unsupported_claim_free_rate"] == 0.5


def test_forbidden_claim_matching_does_not_penalize_negated_or_generic_mentions():
    case = MaintenanceEvalCase(
        case_id="manual_e2e_no_answer",
        query="活塞环安装专用扩张器型号是什么？",
        answerable=False,
        forbidden_claims=["型号", "必须使用活塞环扩张器"],
    )

    row = evaluate_case_output(
        case,
        "手册未提及活塞环安装专用扩张器的具体型号，也未说明必须使用活塞环扩张器。",
    )

    assert row["forbidden_claim_pass"] is True
    assert row["refusal_pass"] is True
    assert row["grounding_pass"] is True


def test_forbidden_claim_matching_still_catches_asserted_bad_claim():
    case = MaintenanceEvalCase(
        case_id="manual_e2e_bad_claim",
        query="如何拆卸气门？",
        required_nuggets=["使用气门拆装器压缩气门弹簧"],
        forbidden_claims=["冷却30分钟以上"],
    )

    row = evaluate_case_output(
        case,
        "使用气门拆装器压缩气门弹簧。操作前必须冷却30分钟以上。",
    )

    assert row["forbidden_claim_pass"] is False
    assert row["grounding_pass"] is False


def test_summarize_rows_counts_only_cases_with_expected_step_order_as_procedure_cases():
    rows = [
        {
            "answerable": True,
            "final_pass": True,
            "grounding_pass": True,
            "expected_step_order": "先拆A；再拆B",
            "procedure_order_pass": True,
            "image_pass": True,
            "image_eval_required": False,
            "required_nugget_recall": 1.0,
            "forbidden_claim_pass": True,
            "refusal_pass": True,
        },
        {
            "answerable": True,
            "final_pass": True,
            "grounding_pass": True,
            "expected_step_order": "",
            "procedure_order_pass": False,
            "image_pass": True,
            "image_eval_required": False,
            "required_nugget_recall": 1.0,
            "forbidden_claim_pass": True,
            "refusal_pass": True,
        },
    ]

    summary = summarize_rows(rows)

    assert summary["procedure_case_count"] == 1
    assert summary["procedure_order_pass_rate"] == 1.0


def test_order_matching_accepts_marker_pairs_with_original_text_between_markers():
    case = MaintenanceEvalCase(
        case_id="marker_order",
        query="如何安装曲轴与平衡轴并对正标记？",
        required_nuggets=["平衡轴齿轮上的B标记与曲轴齿轮上的A标记必须对正角相"],
        expected_step_order=[
            "将左曲轴箱体水平放置",
            "喷涂适量机油",
            "B标记与A标记必须对正角相",
            "将曲轴旋转至上止点位置",
            "C标记应与D标记对齐",
            "转动曲轴检查",
        ],
    )

    answer = (
        "1. 将左曲轴箱体水平放置（合箱面朝上）。\n"
        "2. 在曲轴轴承与平衡轴轴承上喷涂适量机油。\n"
        "3. 装配对正要求：平衡轴齿轮上的标记（图示“B”）与曲轴齿轮上的标记（图示“A”）必须对正角相。\n"
        "4. 安装完成后，将曲轴旋转至上止点位置。\n"
        "曲柄上的标记（图示“C”）应与平衡轴配重块上的标记（图示“D”）对齐。\n"
        "5. 转动曲轴，检查曲轴转动是否灵活。"
    )

    row = evaluate_case_output(case, answer)

    assert row["procedure_order_pass"] is True


def test_evaluate_case_output_skips_evidence_score_without_constraints_or_metadata():
    case = MaintenanceEvalCase(
        case_id="manual_e2e_no_constraints",
        query="如何拆卸气门？",
        required_nuggets=["取下滑动挺柱"],
    )

    row = evaluate_case_output(case, "取下滑动挺柱。", metadata={"react_trace": []})

    assert row["evidence_score_available"] is False
    assert row["evidence_final_pass"] == ""
    assert row["final_pass"] is True


def test_evaluate_case_output_attaches_evidence_score_when_case_has_claim_constraints():
    case = MaintenanceEvalCase(
        case_id="manual_e2e_torque_claim",
        query="水泵锁紧扭矩是多少？",
        claim_constraints=[
            ClaimConstraint(
                claim_id="pump_torque",
                answer_patterns=["20 Nm"],
                evidence_patterns=["20 Nm"],
                allowed_sources=[
                    AllowedSource(source_type="manual", document_id="manual-a", chunk_ids=["pump-torque"])
                ],
            )
        ],
    )
    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": [
                            {
                                "content": "Pump bolt torque is 20 Nm.",
                                "metadata": {
                                    "qualification": "qualified",
                                    "document_id": "manual-a",
                                    "chunk_id": "pump-torque",
                                },
                            }
                        ],
                    }
                ]
            }
        ]
    }

    row = evaluate_case_output(case, "Set it to 20 Nm.", metadata=metadata)

    assert row["evidence_score_available"] is True
    assert row["evidence_coverage_status"] == "complete"
    assert row["evidence_final_pass"] is True
    assert row["evidence_answer_alignment_pass"] is True


def test_evaluate_case_output_requires_explicit_evidence_constraints_to_pass():
    case = MaintenanceEvalCase(
        case_id="manual_e2e_missing_trace",
        query="水泵锁紧扭矩是多少？",
        claim_constraints=[
            ClaimConstraint(
                claim_id="pump_torque",
                answer_patterns=["20 Nm"],
                evidence_patterns=["20 Nm"],
                allowed_sources=[
                    AllowedSource(source_type="manual", document_id="manual-a")
                ],
            )
        ],
    )

    row = evaluate_case_output(
        case,
        "Set it to 20 Nm.",
        metadata={"react_trace": []},
    )

    assert row["grounding_pass"] is True
    assert row["evidence_score_available"] is True
    assert row["evidence_final_pass"] is False
    assert row["final_pass"] is False


def test_run_cases_multi_turn_fixture_produces_turn_rows():
    case = MaintenanceEvalCase(
        case_id="mt_001",
        device_type="motorcycle-engine-v1",
        document_id="manual-v1",
        turns=[
            MaintenanceEvalTurn(
                query="凸轮轴拆卸时先取哪根？",
                required_nuggets=["进气凸轮轴"],
                candidate_answer="先取下进气凸轮轴。",
            ),
            MaintenanceEvalTurn(
                query="那安装时呢？",
                required_nuggets=["排气凸轮轴"],
                forbidden_claims=["先安装进气凸轮轴"],
                candidate_answer="安装顺序相反：先安装排气凸轮轴。",
            ),
        ],
    )

    rows = run_cases([case], mode="fixture", endpoint="", timeout=10)

    assert len(rows) == 2
    assert rows[0]["id"] == "mt_001:t1"
    assert rows[1]["id"] == "mt_001:t2"
    assert rows[0]["final_pass"] is True
    assert rows[1]["final_pass"] is True


def test_run_cases_single_turn_case_id_unchanged():
    case = MaintenanceEvalCase(
        case_id="st_001",
        query="火花塞间隙是多少？",
        required_nuggets=["0.7～0.9 mm"],
        candidate_answer="火花塞间隙为0.7～0.9 mm。",
    )

    rows = run_cases([case], mode="fixture", endpoint="", timeout=10)

    assert len(rows) == 1
    assert rows[0]["id"] == "st_001"


def test_run_cases_multi_turn_api_passes_accumulated_history(monkeypatch):
    captured_payloads: list[dict] = []

    class _FakeResponse:
        def __init__(self, body: bytes):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        captured_payloads.append(payload)
        response_body = json.dumps({"message": f"answer-{len(captured_payloads)}"}).encode("utf-8")
        return _FakeResponse(response_body)

    import evaluation.maintenance_eval_cli as cli_module

    monkeypatch.setattr(cli_module.urllib.request, "urlopen", fake_urlopen)

    case = MaintenanceEvalCase(
        case_id="mt_api_001",
        device_type="motorcycle-engine-v1",
        document_id="manual-v1",
        turns=[
            MaintenanceEvalTurn(query="第一问"),
            MaintenanceEvalTurn(query="第二问"),
        ],
    )

    run_cases([case], mode="api", endpoint="http://test/ai/chat", timeout=5)

    assert len(captured_payloads) == 2
    assert captured_payloads[0]["conversation_history"] == []
    assert captured_payloads[0]["device_type"] == "motorcycle-engine-v1"
    assert captured_payloads[0]["document_id"] == "manual-v1"

    history_in_turn2 = captured_payloads[1]["conversation_history"]
    assert len(history_in_turn2) == 2
    assert history_in_turn2[0] == {"role": "user", "content": "第一问"}
    assert history_in_turn2[1] == {"role": "assistant", "content": "answer-1"}


def test_run_cases_api_sends_configured_token_header(monkeypatch) -> None:
    captured_headers: list[dict[str, str]] = []

    class _FakeResponse:
        def read(self):
            return json.dumps({"message": "answer"}).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(request, timeout):
        captured_headers.append(dict(request.header_items()))
        return _FakeResponse()

    import evaluation.maintenance_eval_cli as cli_module

    monkeypatch.setattr(cli_module.urllib.request, "urlopen", fake_urlopen)
    case = MaintenanceEvalCase(case_id="auth_case", query="question")

    run_cases(
        [case],
        mode="api",
        endpoint="http://test/ai/chat",
        timeout=5,
        api_token="eval-secret",
    )

    assert len(captured_headers) == 1
    assert captured_headers[0]["X-api-token"] == "eval-secret"


def test_runner_aggregates_case_and_turn_denominators() -> None:
    cases = [
        MaintenanceEvalCase(
            case_id="single_001",
            query="单轮问题",
            required_nuggets=["正确"],
            candidate_answer="正确",
            dataset_source="legacy.jsonl",
        ),
        MaintenanceEvalCase(
            case_id="multi_001",
            dataset_source="special.jsonl",
            turns=[
                MaintenanceEvalTurn(
                    query="第一轮",
                    required_nuggets=["第一轮正确"],
                    candidate_answer="第一轮正确",
                ),
                MaintenanceEvalTurn(
                    query="第二轮",
                    required_nuggets=["第二轮正确"],
                    candidate_answer="缺少答案",
                ),
            ],
        ),
    ]

    turn_rows = run_cases(cases, mode="fixture", endpoint="", timeout=5, run_id="run-a")
    case_rows = aggregate_case_rows(cases, turn_rows)
    summary = summarize_results(case_rows, turn_rows)

    assert len(turn_rows) == 3
    assert [row["id"] for row in case_rows] == ["single_001", "multi_001"]
    assert case_rows[0]["query"] == "单轮问题"
    assert case_rows[0]["generated_answer"] == "正确"
    assert case_rows[1]["turn_count"] == 2
    assert case_rows[1]["final_pass"] is False
    assert {row["dataset_source"] for row in turn_rows} == {"legacy.jsonl", "special.jsonl"}
    assert summary["case_count"] == 2
    assert summary["turn_count"] == 3
    assert summary["request_count"] == 3
    assert summary["metric_counts"]["final_pass_rate"] == {
        "numerator": 1,
        "denominator": 2,
        "rate": 0.5,
    }


def test_runner_uses_shared_session_per_case_and_unique_session_per_run(monkeypatch) -> None:
    captured_payloads: list[dict] = []

    class _FakeResponse:
        def read(self):
            return json.dumps({"message": "answer"}).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(request, timeout):
        captured_payloads.append(json.loads(request.data.decode("utf-8")))
        return _FakeResponse()

    import evaluation.maintenance_eval_cli as cli_module

    monkeypatch.setattr(cli_module.urllib.request, "urlopen", fake_urlopen)
    case = MaintenanceEvalCase(
        case_id="session_case",
        turns=[MaintenanceEvalTurn(query="一"), MaintenanceEvalTurn(query="二")],
    )

    run_cases(
        [case],
        mode="api",
        endpoint="http://test/ai/chat",
        timeout=5,
        run_id="run-one",
        default_device_type="motorcycle-engine",
        default_document_id="manual-doc",
    )
    run_cases(
        [case],
        mode="api",
        endpoint="http://test/ai/chat",
        timeout=5,
        run_id="run-two",
        default_device_type="motorcycle-engine",
        default_document_id="manual-doc",
    )

    assert len(captured_payloads) == 4
    assert captured_payloads[0]["session_id"] == captured_payloads[1]["session_id"]
    assert captured_payloads[2]["session_id"] == captured_payloads[3]["session_id"]
    assert captured_payloads[0]["session_id"] != captured_payloads[2]["session_id"]
    assert captured_payloads[0]["device_type"] == "motorcycle-engine"
    assert captured_payloads[0]["document_id"] == "manual-doc"


def test_main_reads_api_token_from_env_without_persisting_it(monkeypatch, tmp_path: Path) -> None:
    dataset = tmp_path / "auth.jsonl"
    dataset.write_text(
        json.dumps({"case_id": "auth_main", "query": "question"}) + "\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "results"
    captured_headers: list[dict[str, str]] = []

    class _FakeResponse:
        def read(self):
            return json.dumps({"message": "answer"}).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(request, timeout):
        captured_headers.append(dict(request.header_items()))
        return _FakeResponse()

    import evaluation.maintenance_eval_cli as cli_module

    monkeypatch.setattr(cli_module.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("MAINTENANCE_EVAL_API_TOKEN", "env-secret")

    exit_code = main(
        [
            "--dataset",
            str(dataset),
            "--mode",
            "api",
            "--endpoint",
            "http://test/ai/chat",
            "--out-dir",
            str(out_dir),
            "--result-name",
            "auth",
        ]
    )

    assert exit_code == 0
    assert captured_headers[0]["X-api-token"] == "env-secret"
    assert "env-secret" not in (out_dir / "auth_run.json").read_text(encoding="utf-8")


def test_main_writes_five_auditable_artifacts(tmp_path: Path) -> None:
    dataset = tmp_path / "special.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "case_id": "artifact_case",
                "query": "问题",
                "candidate_answer": "回答",
                "candidate_metadata": {"react_trace": []},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "results"

    exit_code = main(
        [
            "--dataset",
            str(dataset),
            "--mode",
            "fixture",
            "--out-dir",
            str(out_dir),
            "--result-name",
            "baseline",
            "--default-device-type",
            "motorcycle-engine",
            "--default-document-id",
            "manual-doc",
        ]
    )

    assert exit_code == 0
    assert (out_dir / "baseline.csv").is_file()
    assert (out_dir / "baseline_turns.csv").is_file()
    assert (out_dir / "baseline_trace.jsonl").is_file()
    assert (out_dir / "baseline_summary.json").is_file()
    assert (out_dir / "baseline_run.json").is_file()
    summary = json.loads((out_dir / "baseline_summary.json").read_text(encoding="utf-8"))
    run_manifest = json.loads((out_dir / "baseline_run.json").read_text(encoding="utf-8"))
    assert summary["case_count"] == 1
    assert summary["turn_count"] == 1
    assert run_manifest["dataset_files"][0]["sha256"]
    assert run_manifest["default_document_id"] == "manual-doc"
