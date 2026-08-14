import pytest

from services.knowledge.vector_service import (
    _build_keyword_query,
    _escape_redisearch_text_term,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("火花塞损坏", "@text:(火花塞损坏)"),
        ("O 型圈", "@text:(O 型圈)"),
        ("125×1.5", r"@text:(125 1\.5)"),
        ("N·m", "@text:(N m)"),
        ('型号(A:B)-C "检查"', "@text:(型号 A B C 检查)"),
    ],
)
def test_keyword_query_handles_chinese_units_and_redisearch_syntax(text: str, expected: str) -> None:
    assert _build_keyword_query(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "---", "()：\""])
def test_keyword_query_rejects_empty_lexical_input(text: str) -> None:
    assert _build_keyword_query(text) is None


def test_redisearch_text_term_escapes_reserved_characters() -> None:
    assert _escape_redisearch_text_term("1.5:规格") == r"1\.5\:规格"
