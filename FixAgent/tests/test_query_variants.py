from services.retrieval.aspects import QuestionAspect
from services.retrieval.query_variants import build_query_variants, build_variant_route_pairs
from tools.knowledge_retrieval_tool import KnowledgeRetrievalTool


def test_query_variants_use_contract_fields_without_domain_synonyms() -> None:
    variants = build_query_variants(
        "摩托车发动机的火花塞损坏时如何处理？",
        {
            "component": "火花塞",
            "fault": "火花塞损坏",
            "action": "处理",
            "requested_fields": ["手册依据"],
        },
    )

    assert [(item.source, item.text) for item in variants] == [
        ("original", "摩托车发动机的火花塞损坏时如何处理？"),
        ("component_fault", "火花塞 火花塞损坏"),
        ("component_action", "火花塞 处理"),
    ]


def test_query_variants_add_targets_and_aspects_with_stable_limit() -> None:
    variants = build_query_variants(
        "检查气门间隙并更换调整垫片，说明调整步骤",
        {
            "component": "气门",
            "action": "检查",
            "targets": [
                {"target_id": "gap", "component": "气门间隙", "action": "检查"},
                {"target_id": "adjust", "component": "调整垫片", "action": "更换"},
            ],
        },
        aspects=(QuestionAspect("aspect-1", "说明调整步骤"),),
        max_variants=4,
    )

    assert len(variants) == 4
    assert variants[0].source == "original"
    assert variants[2].target_id == "gap"
    assert variants[3].target_id == "adjust"


def test_query_variants_return_only_original_without_structured_fields() -> None:
    assert [item.text for item in build_query_variants("如何处理？", {})] == ["如何处理？"]


def test_query_variants_do_not_depend_on_evaluation_gold() -> None:
    variants = build_query_variants(
        "检查火花塞",
        {"component": "火花塞", "gold_evidence": ["禁止进入查询"]},
    )

    assert all("禁止进入查询" not in item.text for item in variants)


def test_query_variants_ignore_contract_fields_not_grounded_in_query() -> None:
    variants = build_query_variants(
        "发动机无法启动怎么办",
        {
            "component": "火花塞",
            "action": "更换",
            "requested_fields": ["手册依据"],
            "targets": [
                {"target_id": "invented", "component": "点火线圈", "action": "检查"},
            ],
        },
    )

    assert [(item.source, item.text) for item in variants] == [
        ("original", "发动机无法启动怎么办"),
    ]


def test_variant_route_pairs_expand_every_route_in_stable_order() -> None:
    variants = build_query_variants(
        "火花塞损坏如何处理",
        {"component": "火花塞", "fault": "火花塞损坏", "action": "处理"},
    )

    pairs = build_variant_route_pairs(("text", "keyword"), variants)

    assert [(route, variant.source) for route, variant in pairs] == [
        ("text", "original"),
        ("text", "component_fault"),
        ("text", "component_action"),
        ("keyword", "original"),
        ("keyword", "component_fault"),
        ("keyword", "component_action"),
    ]


def test_internal_query_contract_is_not_exposed_in_llm_tool_schema() -> None:
    assert "_query_contract" not in KnowledgeRetrievalTool().get_parameters_schema()["properties"]


def test_retrieval_stage_ids_prefer_stable_section_identity() -> None:
    ids = KnowledgeRetrievalTool._retrieval_stage_ids([
        {"doc_id": "row-1", "metadata": {"parent_section_id": "sec:a"}},
        {"doc_id": "row-2", "metadata": {"parent_section_id": "sec:a"}},
        {"doc_id": "row-3", "metadata": {"chunk_uid": "chunk:3"}},
    ])

    assert ids == ["sec:a", "chunk:3"]


def test_retrieval_stage_trace_uses_fused_order_for_candidate_cutoffs() -> None:
    trace = KnowledgeRetrievalTool._build_retrieval_stage_trace(
        fused=[
            {"doc_id": "fused-first", "metadata": {"parent_section_id": "sec:first"}},
            {"doc_id": "fused-second", "metadata": {"parent_section_id": "sec:second"}},
        ],
        filtered=[{"doc_id": "filtered", "metadata": {"parent_section_id": "sec:second"}}],
        reranked=[{"doc_id": "ranked", "metadata": {"parent_section_id": "sec:second"}}],
        selected=[{"doc_id": "selected", "metadata": {"parent_section_id": "sec:second"}}],
        expanded=[{"doc_id": "expanded", "metadata": {"parent_section_id": "sec:context"}}],
        visible=[],
    )

    assert trace["candidate_ids"] == ["sec:first", "sec:second"]
    assert trace["selected_ids"] == ["sec:second"]
    assert trace["expanded_ids"] == ["sec:context"]
    assert trace["visible_ids"] == []
