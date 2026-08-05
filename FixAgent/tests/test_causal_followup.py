from services.causal_followup import build_follow_up, resolve_follow_up, build_evidence_follow_up


def test_evidence_follow_up_requires_two_real_candidates_and_does_not_use_demo_scenario():
    assert build_evidence_follow_up("发动机冒蓝烟", {}) is None
    result = build_evidence_follow_up(
        "发动机异响",
        {
            "diagnostic_candidates": [
                {"id": "c1", "faultPart": "曲轴", "rootCause": "轴承磨损", "distinguishingFeature": "随转速变化"},
                {"id": "c2", "faultPart": "气门", "rootCause": "间隙异常", "distinguishingFeature": "冷机明显"},
            ]
        },
    )
    assert result is not None
    assert {item["id"] for item in result["alternatives"]} == {"A", "B"}


def test_build_follow_up_uses_dynamic_diagnosis_candidates_without_scenario_table():
    follow_up = build_follow_up(
        "一种未登记设备出现未知现象，怎么回事？",
        diagnosis_items=[
            {
                "id": "cause-a",
                "faultPart": "甲部件",
                "rootCause": "甲原因",
                "confidence": 0.62,
                "distinguishingFeature": "现象发生在阶段甲",
                "suggestedCheck": "检查甲部件",
            },
            {
                "id": "cause-b",
                "faultPart": "乙部件",
                "rootCause": "乙原因",
                "confidence": 0.59,
                "distinguishingFeature": "现象发生在阶段乙",
                "suggestedCheck": "检查乙部件",
            },
        ],
    )

    assert follow_up is not None
    assert follow_up["status"] == "awaiting_answer"
    assert "现场情况" in follow_up["question"]
    assert len(follow_up["hypotheses"]) >= 2
    assert [item["rootCause"] for item in follow_up["hypotheses"][:2]] == ["甲原因", "乙原因"]
    assert follow_up["options"][0]["id"] == "A"
    assert follow_up["clarification_id"].startswith("clarification-")
    assert follow_up["kind"] == "diagnostic_cause"
    assert follow_up["alternatives"][0]["id"] == "A"


def test_resolve_follow_up_reranks_by_selected_option():
    follow_up = build_follow_up(
        "未知故障",
        diagnosis_items=[
            {"id": "a", "faultPart": "甲", "rootCause": "原因甲", "distinguishingFeature": "特征甲"},
            {"id": "b", "faultPart": "乙", "rootCause": "原因乙", "distinguishingFeature": "特征乙"},
        ],
    )

    resolved = resolve_follow_up(
        {"diagnostic_follow_up": follow_up, "selected_option_id": "B"},
        "B. 特征乙",
    )

    assert resolved is not None
    assert resolved["status"] == "resolved"
    assert resolved["selectedOption"]["id"] == "B"
    assert resolved["hypotheses"][0]["rootCause"] == "原因乙"
    assert resolved["diagnosisItems"][0]["rootCause"] == "原因乙"


def test_resolve_follow_up_accepts_common_pending_clarification_context():
    follow_up = build_follow_up(
        "未知故障",
        diagnosis_items=[
            {"id": "a", "faultPart": "甲", "rootCause": "原因甲", "distinguishingFeature": "特征甲"},
            {"id": "b", "faultPart": "乙", "rootCause": "原因乙", "distinguishingFeature": "特征乙"},
        ],
    )

    resolved = resolve_follow_up(
        {"pending_clarification": follow_up, "selected_clarification_option_id": "B"},
        "B. 特征乙",
    )

    assert resolved is not None
    assert resolved["selectedOption"]["id"] == "B"


def test_build_follow_up_ignores_unrelated_query():
    assert build_follow_up("帮我查询维修手册里有哪些章节") is None


def test_graph_trace_candidates_choose_dynamic_part_discriminator():
    result = build_evidence_follow_up(
        "未知设备出现异常",
        {
            "react_trace": [{
                "tool_calls": [{
                    "name": "java_graph_diagnosis_path",
                    "result_data": {
                        "raw_records": [
                            {"faultId": "f1", "componentName": "甲单元", "faultName": "故障甲", "matchScore": 3},
                            {"faultId": "f2", "componentName": "乙单元", "faultName": "故障乙", "matchScore": 3},
                        ]
                    },
                }]
            }]
        },
    )

    assert result is not None
    assert result["question_dimension"] == "faultPart"
    assert {item["text"] for item in result["alternatives"]} == {"甲单元", "乙单元"}
