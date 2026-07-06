from services.causal_followup import build_follow_up, resolve_follow_up


def test_build_follow_up_for_blue_smoke_candidates():
    follow_up = build_follow_up("发动机冒蓝烟还烧机油，怎么回事？")

    assert follow_up is not None
    assert follow_up["status"] == "awaiting_answer"
    assert "蓝烟" in follow_up["question"]
    assert len(follow_up["hypotheses"]) >= 2
    assert [item["rootCause"] for item in follow_up["hypotheses"][:2]] == [
        "气门油封老化",
        "活塞环磨损",
    ]
    assert follow_up["options"][0]["id"] == "A"


def test_resolve_follow_up_reranks_by_selected_option():
    follow_up = build_follow_up("发动机冒蓝烟还烧机油，怎么回事？")

    resolved = resolve_follow_up(
        {"diagnostic_follow_up": follow_up, "selected_option_id": "B"},
        "B. 加速或负载时更明显",
    )

    assert resolved is not None
    assert resolved["status"] == "resolved"
    assert resolved["selectedOption"]["id"] == "B"
    assert resolved["hypotheses"][0]["rootCause"] == "活塞环磨损"
    assert resolved["diagnosisItems"][0]["rootCause"] == "活塞环磨损"


def test_build_follow_up_ignores_unrelated_query():
    assert build_follow_up("帮我查询维修手册里有哪些章节") is None
