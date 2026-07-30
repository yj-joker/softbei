import json
from pathlib import Path

from evaluation.maintenance_eval_cli import (
    MaintenanceEvalCase,
    MaintenanceEvalTurn,
    aggregate_case_rows,
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
    assert row["final_pass"] is True


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
