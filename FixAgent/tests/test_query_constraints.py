from services.retrieval.query_constraints import (
    candidate_constraint_conflicts,
    extract_query_constraints,
)


def _item(title: str, content: str = "") -> dict:
    return {"content": content, "metadata": {"section_title": title}}


def test_right_crankcase_cover_rejects_left_cover_section() -> None:
    constraints = extract_query_constraints("如何安装右曲轴箱盖")

    assert candidate_constraint_conflicts(
        constraints,
        _item("7.4 左曲轴箱盖", "安装左曲轴箱盖垫片"),
    ) == ["direction:右->左"]


def test_right_crankcase_cover_accepts_right_cover_section() -> None:
    constraints = extract_query_constraints("如何安装右曲轴箱盖")

    assert candidate_constraint_conflicts(
        constraints,
        _item("6.4 右曲轴箱盖与离合器/安装右盖", "检查曲轴油封后安装右盖"),
    ) == []


def test_install_query_rejects_disassembly_only_section() -> None:
    constraints = extract_query_constraints("如何安装右曲轴箱盖")

    assert "action:安装->拆卸" in candidate_constraint_conflicts(
        constraints,
        _item("6.3 拆卸右曲轴箱盖", "拆下右盖并取出离合器拉杆"),
    )


def test_generic_title_still_checks_body_for_direction_conflict() -> None:
    constraints = extract_query_constraints("如何安装右曲轴箱盖")

    assert candidate_constraint_conflicts(
        constraints,
        _item("6.4 曲轴箱盖", "安装左曲轴箱盖垫片"),
    ) == ["direction:右->左"]


def test_comparing_left_and_right_covers_does_not_forbid_either_side() -> None:
    constraints = extract_query_constraints("比较左曲轴箱盖和右曲轴箱盖的差异")

    assert constraints.forbidden_terms == ()
    assert candidate_constraint_conflicts(constraints, _item("7.4 左曲轴箱盖")) == []
    assert candidate_constraint_conflicts(constraints, _item("6.4 右曲轴箱盖")) == []


def test_direction_constraint_applies_to_unseen_component_names() -> None:
    constraints = extract_query_constraints("如何安装右侧液压护罩")

    assert candidate_constraint_conflicts(
        constraints,
        _item("左侧液压护罩", "安装左侧液压护罩密封条"),
    ) == ["direction:右->左"]
    assert candidate_constraint_conflicts(
        constraints,
        _item("右侧液压护罩", "安装右侧液压护罩密封条"),
    ) == []


def test_remove_and_reinstall_query_does_not_forbid_either_action() -> None:
    constraints = extract_query_constraints("拆卸并重新安装右曲轴箱盖")

    assert constraints.action == ""
    assert constraints.forbidden_actions == ()
    assert candidate_constraint_conflicts(constraints, _item("拆卸右曲轴箱盖")) == []
    assert candidate_constraint_conflicts(constraints, _item("安装右曲轴箱盖")) == []
