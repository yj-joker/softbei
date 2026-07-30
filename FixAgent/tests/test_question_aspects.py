"""Deterministic question-aspect decomposition tests."""

from services.retrieval.aspects import QuestionAspect, split_question_aspects


def test_compound_question_splits_into_stable_aspects() -> None:
    query = "火花塞间隙标准和建议更换周期分别是多少？"

    first = split_question_aspects(query)
    second = split_question_aspects(query)

    assert [aspect.text for aspect in first] == ["火花塞间隙标准", "建议更换周期"]
    assert [aspect.aspect_id for aspect in first] == [aspect.aspect_id for aspect in second]
    assert all(isinstance(aspect, QuestionAspect) for aspect in first)
    assert all(aspect.aspect_id.startswith("aspect-") for aspect in first)


def test_two_explicit_questions_keep_shared_subject_context() -> None:
    aspects = split_question_aspects("水泵装配里有水泵密封圈吗？叶轮轴向间隙是多少？")

    assert [aspect.text for aspect in aspects] == [
        "水泵装配里有水泵密封圈吗",
        "水泵装配里叶轮轴向间隙是多少",
    ]


def test_single_question_is_not_split_on_ordinary_conjunction() -> None:
    aspects = split_question_aspects("如何检查传动主轴与副轴转动是否灵活？")

    assert len(aspects) == 1
    assert aspects[0].text == "如何检查传动主轴与副轴转动是否灵活"


def test_aspect_id_depends_on_normalized_content_not_position() -> None:
    isolated = split_question_aspects("建议更换周期是多少？")[0]
    compound = split_question_aspects("火花塞间隙标准和建议更换周期分别是多少？")[1]

    assert isolated.aspect_id == compound.aspect_id
