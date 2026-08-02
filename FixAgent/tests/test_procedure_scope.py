"""Structured procedure-scope parsing regressions."""

from services.retrieval.procedure_scope import (
    procedure_scope_from_heading,
    procedure_scope_from_toc_path,
)


def test_colon_labels_inside_a_procedure_are_not_new_subflow_headings() -> None:
    assert procedure_scope_from_heading("测量步骤：") is None
    assert procedure_scope_from_heading("检查结果：") is None


def test_nested_toc_inherits_nearest_procedure_ancestor() -> None:
    scope = procedure_scope_from_toc_path(
        "摩托车发动机维修手册 > 五、气缸与活塞 > 5.4 安装气缸与活塞 > "
        "（2）将活塞头部插入气缸裙部 > 注意事项 > 活塞环开口位置与角度"
    )

    assert scope is not None
    assert scope.action == "安装"
    assert scope.target == "气缸与活塞"
