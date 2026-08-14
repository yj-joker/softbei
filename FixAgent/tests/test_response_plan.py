"""ResponsePlan generation and deterministic answer-audit tests."""

from services.retrieval.evidence import EvidenceLedger
from services.retrieval import response_plan as response_plan_module
from services.retrieval.response_plan import ResponsePlan, build_response_plan, finalize_response


def _ledger(*, text: str = "火花塞间隙标准为 0.7 到 0.9 mm。", page: int = 3) -> EvidenceLedger:
    return EvidenceLedger([{
        "evidence_id": "manual:manual-1:chunk-1",
        "source_type": "manual",
        "text": text,
        "qualification": "qualified",
        "source": {
            "document_id": "manual-1",
            "document_version": "v1",
            "chunk_id": "chunk-1",
            "page": page,
        },
    }])


def _bundle(status: str, **extra) -> dict:
    base = {
        "coverage_status": status,
        "coverage_reason": "test",
        "aspect_support": [{
            "aspect_id": "gap",
            "aspect_text": "火花塞间隙标准",
            "supported": status in {"complete", "partial"},
            "evidence_ids": ["chunk-1"],
        }],
        "missing_aspect_ids": [],
        "conflict_eligible": [],
        "capabilities": {"may_offer_generic_guidance": status not in {"unsupported", "conflict"}},
    }
    base.update(extra)
    return base


def _additive_ledger() -> EvidenceLedger:
    return EvidenceLedger([
        {
            "evidence_id": "manual:manual-1:spark-treatment",
            "source_type": "manual",
            "qualification": "qualified",
            "text": "若火花塞损坏或变形，应更换火花塞。",
            "source": {
                "document_id": "manual-1",
                "document_version": "v1",
                "chunk_id": "spark-treatment",
                "page": 3,
            },
        },
        {
            "evidence_id": "graph:kgpath:engine:spark-plug:damaged:none",
            "source_type": "graph",
            "qualification": "qualified",
            "text": "摩托车发动机 -> OWNS -> 火花塞 -> CAUSES -> 火花塞损坏",
            "relationship_types": ["OWNS", "CAUSES"],
            "device": {"id": "engine", "name": "摩托车发动机"},
            "component": {"id": "spark-plug", "name": "火花塞"},
            "fault": {"id": "damaged", "name": "火花塞损坏"},
            "solution": {"title": "不得由图谱授权的拆装步骤", "verified": True},
            "claim_types": [
                "device_identity",
                "component_ownership",
                "fault_relation",
                "verified_solution",
                "procedure",
                "safety",
                "image",
            ],
            "authorized_claim_types": [
                "device_identity",
                "component_ownership",
                "fault_relation",
                "verified_solution",
                "procedure",
                "safety",
                "image",
            ],
            "supports_aspect_ids": ["device", "component", "fault-cause"],
            "source": {
                "document_id": "manual-1",
                "document_version": "v1",
                "section_id": "spark-plug",
                "pages": [3],
            },
        },
    ])


def _additive_bundle() -> dict:
    return {
        "coverage_status": "complete",
        "aspect_support": [
            {
                "aspect_id": "manual-treatment",
                "aspect_text": "更换火花塞",
                "supported": True,
                "evidence_ids": ["manual:manual-1:spark-treatment"],
                "supporting_source_types": ["manual"],
                "user_obligation": True,
            },
            {
                "aspect_id": "fault-cause",
                "aspect_text": "火花塞损坏的故障关系",
                "supported": True,
                "evidence_ids": ["graph:kgpath:engine:spark-plug:damaged:none"],
                "supporting_source_types": ["graph"],
                "aspect_origin": "graph_capability",
                "user_obligation": True,
            },
        ],
        "missing_aspect_ids": [],
        "conflict_eligible": [],
        "capabilities": {"may_cite_manual": True},
    }


def test_response_plan_separates_manual_baseline_from_graph_additions() -> None:
    plan = build_response_plan(
        "火花塞损坏时如何处理，故障属于什么关系？",
        _additive_bundle(),
        _additive_ledger(),
    )

    assert plan.base_manual_claims == ("manual-treatment",)
    assert plan.baseline_evidence_refs == ("manual:manual-1:spark-treatment",)
    assert plan.graph_additions == (
        "device_identity",
        "component_ownership",
        "fault_relation",
    )
    assert "verified_solution" not in plan.graph_additions
    assert "procedure" not in plan.graph_additions
    assert "safety" not in plan.graph_additions
    assert "image" not in plan.graph_additions


def test_no_qualified_graph_evidence_keeps_the_baseline_answer_byte_for_byte() -> None:
    plan = build_response_plan("火花塞损坏时如何处理？", _bundle("complete"), _ledger())
    baseline = "火花塞间隙标准为 0.7 到 0.9 mm。"

    audited = finalize_response(plan, baseline)

    assert audited.answer == baseline
    assert audited.used_fallback is False


def test_graph_relations_are_appended_without_rewriting_manual_baseline() -> None:
    plan = build_response_plan(
        "火花塞损坏时如何处理，故障属于什么关系？",
        _additive_bundle(),
        _additive_ledger(),
    )
    baseline = "若火花塞损坏或变形，应更换火花塞。"

    audited = finalize_response(plan, baseline)

    assert audited.passed is True
    assert audited.answer.startswith(baseline)
    assert audited.answer != baseline
    assert "故障关系" in audited.answer
    assert "摩托车发动机" in audited.answer
    assert "火花塞损坏" in audited.answer
    assert "不得由图谱授权的拆装步骤" not in audited.answer
    assert audited.graph_evidence_used_ids == (
        "graph:kgpath:engine:spark-plug:damaged:none",
    )


def test_additive_graph_finalization_is_idempotent() -> None:
    plan = build_response_plan(
        "火花塞损坏时如何处理，故障属于什么关系？",
        _additive_bundle(),
        _additive_ledger(),
    )
    baseline = "若火花塞损坏或变形，应更换火花塞。"

    first = finalize_response(plan, baseline)
    second = finalize_response(plan, first.answer)

    assert first.passed is True
    assert second.passed is True
    assert second.answer == first.answer
    assert second.answer.count("故障关系：知识图谱确认") == 1


def test_internal_aggregate_manual_claim_does_not_block_graph_addition() -> None:
    bundle = _additive_bundle()
    bundle["aspect_support"].insert(0, {
        "aspect_id": "direct-manual-answer",
        "aspect_text": "本次直取手册答案",
        "supported": True,
        "evidence_ids": ["manual:manual-1:spark-treatment"],
        "supporting_source_types": ["manual"],
        "user_obligation": True,
    })
    plan = build_response_plan(
        "火花塞损坏时如何处理，故障属于什么关系？",
        bundle,
        _additive_ledger(),
    )
    baseline = "若火花塞损坏或变形，应更换火花塞。"

    audited = finalize_response(plan, baseline)

    assert plan.base_manual_claims == ("manual-treatment",)
    assert audited.passed is True
    assert audited.used_fallback is False
    assert audited.answer.startswith(baseline)
    assert audited.graph_evidence_used_ids == (
        "graph:kgpath:engine:spark-plug:damaged:none",
    )


def test_internal_knowledge_answer_claim_is_not_a_manual_monotonicity_obligation() -> None:
    bundle = _additive_bundle()
    bundle["aspect_support"].insert(0, {
        "aspect_id": "knowledge-answer",
        "aspect_text": "当前问题",
        "supported": True,
        "evidence_ids": ["manual:manual-1:spark-treatment"],
        "supporting_source_types": ["manual"],
        "user_obligation": True,
    })
    plan = build_response_plan(
        "火花塞损坏时如何处理，故障属于什么关系？",
        bundle,
        _additive_ledger(),
    )

    assert "knowledge-answer" not in plan.base_manual_claims


def test_additive_monotonicity_audit_rejects_dropped_manual_baseline() -> None:
    plan = build_response_plan(
        "火花塞损坏时如何处理，故障属于什么关系？",
        _additive_bundle(),
        _additive_ledger(),
    )
    baseline = "若火花塞损坏或变形，应更换火花塞。"

    violations = response_plan_module._audit_additive_monotonicity(
        plan,
        baseline,
        "故障关系：摩托车发动机的火花塞发生火花塞损坏。",
    )

    assert "baseline_answer_not_preserved" in violations


def test_normal_complete_plan_is_conclusion_first_without_manual_lead() -> None:
    plan = build_response_plan("火花塞间隙标准是多少？", _bundle("complete"), _ledger())

    assert isinstance(plan, ResponsePlan)
    assert plan.source_mode == "normal"
    answer = plan.deterministic_fallback()
    assert answer.startswith("火花塞间隙标准为 0.7 到 0.9 mm")
    assert not answer.startswith(("根据手册", "依据手册", "按照手册"))
    assert "来源：手册第3页" in answer


def test_response_plan_metadata_exposes_only_final_allowed_evidence_bindings() -> None:
    plan = build_response_plan("火花塞间隙标准是多少？", _bundle("complete"), _ledger(page=3))

    metadata = plan.to_metadata()

    assert metadata["allowed_evidence_refs"] == ["manual:manual-1:chunk-1"]
    assert metadata["allowed_source_chunk_ids"] == ["chunk-1"]
    assert metadata["allowed_evidence_pages"] == [3]
    assert metadata["allowed_document_ids"] == ["manual-1"]
    assert metadata["authorized_claim_evidence_bindings"] == [{
        "claim_id": "gap",
        "claim_text": "火花塞间隙标准",
        "evidence_ids": ["manual:manual-1:chunk-1"],
    }]
    assert metadata["claim_evidence_bindings"] == []
    assert metadata["graph_evidence_bound_ids"] == []
    assert metadata["graph_evidence_used_ids"] == []


def test_explicit_quote_and_page_requests_keep_auditable_citations() -> None:
    quote = build_response_plan("请给我原文和页码", _bundle("complete"), _ledger())
    page = build_response_plan("这个在第几页？", _bundle("complete"), _ledger())

    assert quote.source_mode == "quote"
    assert quote.deterministic_fallback().startswith("手册原文（第3页）")
    assert page.source_mode == "page"
    assert page.deterministic_fallback().startswith("手册第3页记录：")


def test_partial_plan_answers_supported_fact_and_names_missing_aspect() -> None:
    bundle = _bundle(
        "partial",
        aspect_support=[
            {"aspect_id": "gap", "aspect_text": "火花塞间隙标准", "supported": True, "evidence_ids": ["chunk-1"]},
            {"aspect_id": "cycle", "aspect_text": "建议更换周期", "supported": False, "evidence_ids": []},
        ],
        missing_aspect_ids=["cycle"],
    )
    plan = build_response_plan("间隙和更换周期分别是多少？", bundle, _ledger())

    answer = plan.deterministic_fallback()
    assert "0.7 到 0.9 mm" in answer
    assert "建议更换周期" in answer
    assert "当前资料没有明确说明" in answer


def test_retrieval_expansion_does_not_become_a_visible_missing_user_aspect() -> None:
    bundle = _bundle(
        "partial",
        aspect_support=[
            {
                "aspect_id": "install-cover",
                "aspect_text": "安装右曲轴箱盖",
                "supported": True,
                "evidence_ids": ["chunk-1"],
                "aspect_origin": "user_query",
                "user_obligation": True,
            },
            {
                "aspect_id": "expanded-query",
                "aspect_text": "右曲轴箱盖 安装步骤 摩托车 发动机 手册原文",
                "supported": False,
                "evidence_ids": [],
                "aspect_origin": "retrieval_expansion",
                "user_obligation": False,
            },
        ],
        missing_aspect_ids=["expanded-query"],
    )
    ledger = _ledger(
        text=(
            "装上定位销和全新的右曲轴箱盖垫片，盖上右曲轴箱盖，"
            "放入拉索支架和螺栓并对角均匀预紧。"
        ),
        page=26,
    )

    plan = build_response_plan("如何安装右曲轴箱盖", bundle, ledger)

    assert plan.coverage_status == "complete"
    assert plan.missing_aspects == ()
    answer = plan.deterministic_fallback()
    assert "当前资料没有明确说明" not in answer
    assert "摩托车 发动机 手册原文" not in answer


def test_explicit_missing_parameter_in_a_single_sentence_remains_partial() -> None:
    bundle = _bundle(
        "partial",
        aspect_support=[
            {
                "aspect_id": "install",
                "aspect_text": "右曲轴箱盖安装步骤",
                "supported": True,
                "evidence_ids": ["chunk-1"],
            },
            {
                "aspect_id": "torque",
                "aspect_text": "螺栓扭矩",
                "supported": False,
                "evidence_ids": [],
            },
        ],
        missing_aspect_ids=["torque"],
    )
    ledger = _ledger(text="装上右曲轴箱盖并对角均匀预紧螺栓。", page=26)

    plan = build_response_plan("如何安装右曲轴箱盖，螺栓扭矩是多少？", bundle, ledger)

    assert plan.coverage_status == "partial"
    assert plan.missing_aspects == ("螺栓扭矩",)
    assert "关于“螺栓扭矩”，当前资料没有明确说明" in plan.deterministic_fallback()


def test_zero_supported_user_aspects_are_unsupported_even_with_qualified_records() -> None:
    bundle = _bundle(
        "partial",
        aspect_support=[{
            "aspect_id": "fault-cause",
            "aspect_text": "摩托车发动机异响原因",
            "supported": False,
            "evidence_ids": [],
        }],
        missing_aspect_ids=["fault-cause"],
    )
    ledger = _ledger(text="拆卸发动机前先排放机油。", page=6)

    plan = build_response_plan("摩托车发动机异响是什么原因", bundle, ledger)

    assert plan.coverage_status == "unsupported"
    assert plan.missing_aspects == ("摩托车发动机异响原因",)
    assert "当前知识库没有找到足以回答该问题的可靠依据" in plan.deterministic_fallback()


def test_graph_path_evidence_completes_fault_cause_response_plan() -> None:
    graph_id = "graph:kgpath:device-1:component-1:fault-1:none"
    ledger = EvidenceLedger([{
        "evidence_id": graph_id,
        "source_type": "graph",
        "qualification": "qualified",
        "text": "一号发动机 -> OWNS -> 张紧轮 -> CAUSES -> 轴承磨损",
        "path_id": "kgpath:device-1:component-1:fault-1",
        "relationship_types": ["OWNS", "CAUSES"],
        "device": {"id": "device-1", "name": "一号发动机"},
        "component": {"id": "component-1", "name": "张紧轮"},
        "fault": {"id": "fault-1", "name": "轴承磨损"},
        "solution": {},
        "claim_types": ["device_identity", "component_ownership", "fault_relation"],
        "supports_aspect_ids": ["device", "component", "fault-cause"],
        "source": {"document_id": "manual-1", "section_id": "sec-bearing"},
    }])
    bundle = _bundle(
        "unsupported",
        aspect_support=[{
            "aspect_id": "fault-cause",
            "aspect_text": "张紧轮异响原因",
            "supported": False,
            "evidence_ids": [],
        }],
        missing_aspect_ids=["fault-cause"],
    )

    plan = build_response_plan("张紧轮异响是什么原因", bundle, ledger)

    assert plan.coverage_status == "complete"
    assert [entry["evidence_id"] for entry in plan.allowed_evidence] == [graph_id]
    assert plan.to_metadata()["authorized_claim_evidence_bindings"] == [{
        "claim_id": "fault-cause",
        "claim_text": "张紧轮异响原因",
        "evidence_ids": [graph_id],
    }]
    assert plan.to_metadata()["graph_evidence_bound_ids"] == [graph_id]
    assert plan.to_metadata()["claim_evidence_bindings"] == []
    assert plan.to_metadata()["graph_evidence_used_ids"] == []

    audited = finalize_response(plan, plan.deterministic_fallback())

    assert audited.claim_evidence_bindings == ({
        "claim_id": "fault-cause",
        "claim_type": "fault_relation",
        "claim_text": "张紧轮异响原因",
        "evidence_ids": [graph_id],
        "source_types": ["graph"],
        "emitted": True,
    },)
    assert audited.graph_evidence_used_ids == (graph_id,)


def test_fault_relation_binding_accepts_natural_language_fault_expression() -> None:
    graph_id = "graph:kgpath:device-1:component-1:fault-1:none"
    ledger = EvidenceLedger([{
        "evidence_id": graph_id,
        "source_type": "graph",
        "qualification": "qualified",
        "text": "摩托车发动机 -> OWNS -> 火花塞 -> CAUSES -> 火花塞损坏",
        "path_id": "kgpath:device-1:component-1:fault-1",
        "relationship_types": ["OWNS", "CAUSES"],
        "device": {"id": "device-1", "name": "摩托车发动机"},
        "component": {"id": "component-1", "name": "火花塞"},
        "fault": {"id": "fault-1", "name": "火花塞损坏"},
        "solution": {},
        "claim_types": ["device_identity", "component_ownership", "fault_relation"],
        "supports_aspect_ids": ["device", "component", "fault-cause"],
        "source": {"document_id": "manual-1", "section_id": "sec-spark"},
    }])
    bundle = {
        "coverage_status": "unsupported",
        "aspect_support": [{
            "aspect_id": "fault-cause",
            "aspect_text": "故障关系",
            "supported": False,
            "evidence_ids": [],
        }],
        "missing_aspect_ids": ["fault-cause"],
        "conflict_eligible": [],
    }

    plan = build_response_plan(
        "火花塞出现损坏时应如何处理？请说明故障所属部件和手册依据。",
        bundle,
        ledger,
    )
    audited = finalize_response(plan, "火花塞出现损坏。")

    assert audited.passed is True
    assert audited.used_fallback is False
    assert audited.answer == "火花塞出现损坏。"
    assert audited.graph_evidence_used_ids == (graph_id,)


def test_bound_graph_claim_omission_uses_deterministic_graph_fallback() -> None:
    graph_id = "graph:kgpath:engine-1:cylinder-1:fault-1:none"
    graph_entry = {
        "evidence_id": graph_id,
        "source_type": "graph",
        "qualification": "qualified",
        "text": "摩托车发动机 -> OWNS -> 气缸与活塞 -> CAUSES -> 气缸内壁损伤",
        "path_id": "kgpath:engine-1:cylinder-1:fault-1",
        "relationship_types": ["OWNS", "CAUSES"],
        "device": {"id": "engine-1", "name": "摩托车发动机"},
        "component": {"id": "cylinder-1", "name": "气缸与活塞"},
        "fault": {"id": "fault-1", "name": "气缸内壁损伤"},
        "solution": {},
        "source": {
            "document_id": "manual-1",
            "document_version": "v1",
            "section_id": "section-1",
            "pages": [18],
        },
        "claim_types": ["device_identity", "component_ownership", "fault_relation"],
    }
    plan = ResponsePlan(
        plan_id="response-plan-graph-omission",
        coverage_status="complete",
        source_mode="normal",
        allowed_evidence=(graph_entry,),
        missing_aspects=(),
        conflicts=(),
        ledger_digest="digest",
        authorized_claim_evidence_bindings=({
            "claim_id": "component",
            "claim_text": "故障所属部件",
            "evidence_ids": [graph_id],
        },),
        graph_evidence_bound_ids=(graph_id,),
    )

    audited = finalize_response(plan, "当前知识库暂无该故障的可靠依据。")

    assert audited.passed is True
    assert audited.used_fallback is True
    assert "气缸与活塞" in audited.answer
    assert "气缸内壁损伤" in audited.answer
    assert audited.graph_evidence_used_ids == (graph_id,)
    assert audited.claim_evidence_bindings


def test_user_requested_graph_capability_can_bind_without_manual_aspect() -> None:
    graph_id = "graph:kgpath:device-1:component-1:fault-1:none"
    ledger = EvidenceLedger([{
        "evidence_id": graph_id,
        "source_type": "graph",
        "qualification": "qualified",
        "text": "客车 -> OWNS -> 助力油泵 -> CAUSES -> 保险熔断",
        "relationship_types": ["OWNS", "CAUSES"],
        "device": {"id": "device-1", "name": "客车"},
        "component": {"id": "component-1", "name": "助力油泵"},
        "fault": {"id": "fault-1", "name": "保险熔断"},
        "solution": {},
        "claim_types": ["device_identity", "component_ownership", "fault_relation"],
        "supports_aspect_ids": ["device", "component", "fault-cause"],
        "source": {"document_id": "manual-1", "section_id": "sec-pump"},
    }])
    bundle = {
        "coverage_status": "complete",
        "aspect_support": [{
            "aspect_id": "fault-cause",
            "aspect_text": "故障关系",
            "supported": True,
            "evidence_ids": [graph_id],
            "supporting_source_types": ["graph"],
            "aspect_origin": "graph_capability",
            "user_obligation": True,
        }],
        "missing_aspect_ids": [],
        "conflict_eligible": [],
        "capabilities": {},
    }

    plan = build_response_plan("助力油泵保险熔断是什么关系？", bundle, ledger)
    audited = finalize_response(plan, "助力油泵的故障是保险熔断。")

    assert plan.graph_evidence_bound_ids == (graph_id,)
    assert audited.passed is True
    assert audited.graph_evidence_used_ids == (graph_id,)


def test_emitted_graph_relation_without_authorized_binding_fails_audit() -> None:
    graph_id = "graph:kgpath:device-1:component-1:fault-1:none"
    graph_entry = {
        "evidence_id": graph_id,
        "source_type": "graph",
        "qualification": "qualified",
        "text": "客车 -> OWNS -> 助力油泵 -> CAUSES -> 保险熔断",
        "relationship_types": ["OWNS", "CAUSES"],
        "device": {"id": "device-1", "name": "客车"},
        "component": {"id": "component-1", "name": "助力油泵"},
        "fault": {"id": "fault-1", "name": "保险熔断"},
        "solution": {},
        "claim_types": ["fault_relation"],
        "source": {"document_id": "manual-1"},
    }
    plan = ResponsePlan(
        plan_id="unbound-graph",
        coverage_status="complete",
        source_mode="normal",
        allowed_evidence=(graph_entry,),
        missing_aspects=(),
        conflicts=(),
        ledger_digest="digest",
        authorized_claim_evidence_bindings=(),
        graph_evidence_bound_ids=(),
    )

    audited = finalize_response(plan, "助力油泵的故障是保险熔断。")

    assert audited.passed is False
    assert f"unbound_graph_claim:{graph_id}" in audited.violations


def test_graph_usage_is_derived_from_final_answer_not_all_authorized_paths() -> None:
    first_id = "graph:kgpath:device-1:component-1:fault-1:none"
    second_id = "graph:kgpath:device-2:component-2:fault-2:none"
    entries = [
        {
            "evidence_id": first_id,
            "source_type": "graph",
            "qualification": "qualified",
            "text": "一号发动机 -> OWNS -> 张紧轮 -> CAUSES -> 轴承磨损",
            "path_id": "kgpath:device-1:component-1:fault-1",
            "relationship_types": ["OWNS", "CAUSES"],
            "device": {"id": "device-1", "name": "一号发动机"},
            "component": {"id": "component-1", "name": "张紧轮"},
            "fault": {"id": "fault-1", "name": "轴承磨损"},
            "solution": {},
            "claim_types": ["device_identity", "component_ownership", "fault_relation"],
            "supports_aspect_ids": ["device", "component", "fault-cause"],
            "source": {"document_id": "manual-1", "section_id": "sec-bearing"},
        },
        {
            "evidence_id": second_id,
            "source_type": "graph",
            "qualification": "qualified",
            "text": "二号发动机 -> OWNS -> 水泵 -> CAUSES -> 密封失效",
            "path_id": "kgpath:device-2:component-2:fault-2",
            "relationship_types": ["OWNS", "CAUSES"],
            "device": {"id": "device-2", "name": "二号发动机"},
            "component": {"id": "component-2", "name": "水泵"},
            "fault": {"id": "fault-2", "name": "密封失效"},
            "solution": {},
            "claim_types": ["device_identity", "component_ownership", "fault_relation"],
            "supports_aspect_ids": ["device", "component", "fault-cause"],
            "source": {"document_id": "manual-2", "section_id": "sec-seal"},
        },
    ]
    bundle = _bundle(
        "unsupported",
        aspect_support=[{
            "aspect_id": "fault-cause",
            "aspect_text": "故障原因",
            "supported": False,
            "evidence_ids": [],
        }],
        missing_aspect_ids=["fault-cause"],
    )
    plan = build_response_plan("故障是什么原因", bundle, EvidenceLedger(entries))

    audited = finalize_response(plan, "一号发动机的张紧轮发生轴承磨损。")

    assert plan.to_metadata()["graph_evidence_bound_ids"] == [first_id, second_id]
    assert audited.graph_evidence_used_ids == (first_id,)
    assert audited.claim_evidence_bindings[0]["evidence_ids"] == [first_id]


def test_graph_path_evidence_does_not_complete_parameter_response_plan() -> None:
    ledger = EvidenceLedger([{
        "evidence_id": "graph:kgpath:device-1:component-1:fault-1:none",
        "source_type": "graph",
        "qualification": "qualified",
        "text": "一号发动机 -> OWNS -> 张紧轮 -> CAUSES -> 轴承磨损",
        "solution": {},
        "source": {"document_id": "manual-1", "section_id": "sec-bearing"},
    }])
    bundle = _bundle(
        "unsupported",
        aspect_support=[{
            "aspect_id": "gap",
            "aspect_text": "张紧轮间隙参数",
            "supported": False,
            "evidence_ids": [],
        }],
        missing_aspect_ids=["gap"],
    )

    plan = build_response_plan("张紧轮间隙是多少", bundle, ledger)

    assert plan.coverage_status == "unsupported"
    assert plan.allowed_evidence == ()


def test_graph_diagnostic_fallback_uses_path_and_matching_manual_row_only() -> None:
    graph_id = "graph:kgpath:device-1:component-1:fault-1:none"
    entries = [
        {
            "evidence_id": graph_id,
            "source_type": "graph",
            "qualification": "qualified",
            "text": "纯电动客车 -> OWNS -> 助力油泵 -> CAUSES -> 保险熔断",
            "path_id": "kgpath:device-1:component-1:fault-1",
            "relationship_types": ["OWNS", "CAUSES"],
            "device": {"id": "device-1", "name": "纯电动客车"},
            "component": {"id": "component-1", "name": "助力油泵"},
            "fault": {"id": "fault-1", "name": "保险熔断"},
            "solution": {},
            "claim_types": ["device_identity", "component_ownership", "fault_relation"],
            "supports_aspect_ids": ["device", "component", "fault-cause"],
            "source": {
                "document_id": "manual-1",
                "document_version": "v1",
                "section_id": "sec-pump",
                "source_chunk_uids": ["table-pump"],
                "pages": [60],
            },
        },
        {
            "evidence_id": "manual:manual-1:table-parent",
            "source_type": "manual",
            "qualification": "qualified",
            "text": (
                "保险熔断 | 跟换同种规格保险\n"
                "继电器无法吸合 | 跟换同种继电器\n"
                "电动泵没有工作 | 更换电动泵"
            ),
            "source": {
                "document_id": "manual-1",
                "document_version": "v1",
                "chunk_id": "table-parent",
                "chunk_type": "table",
                "parent_section_id": "sec-pump",
                "table_id": "table-pump",
                "page": 60,
                "row_index": None,
            },
        },
        {
            "evidence_id": "manual:manual-1:fault-row",
            "source_type": "manual",
            "qualification": "qualified",
            "text": "检查内容=检查保险32A；col_4=保险熔断；col_5=跟换同种规格保险",
            "source": {
                "document_id": "manual-1",
                "document_version": "v1",
                "chunk_id": "fault-row",
                "chunk_type": "table",
                "parent_section_id": "sec-pump",
                "table_id": "table-pump",
                "page": 60,
                "row_index": 3,
            },
        },
    ]
    bundle = {
        "aspect_support": [{
            "aspect_id": "fault-cause",
            "aspect_text": "助力油泵保险熔断故障",
            "supported": True,
            "evidence_ids": [graph_id, "table-parent", "fault-row"],
        }],
        "missing_aspect_ids": [],
        "conflict_eligible": [],
        "capabilities": {"may_cite_manual": True},
    }

    plan = build_response_plan(
        "助力油泵保险熔断，请说明故障部件和处理建议",
        bundle,
        EvidenceLedger(entries),
    )
    audited = finalize_response(plan, "电压约为380V。")

    assert audited.used_fallback is True
    assert "助力油泵" in audited.answer
    assert "保险熔断" in audited.answer
    assert "跟换同种规格保险" in audited.answer
    assert "继电器无法吸合" not in audited.answer
    assert "更换电动泵" not in audited.answer
    assert "380V" not in audited.answer


def test_graph_diagnostic_fallback_extracts_matching_parent_table_line() -> None:
    graph = {
        "evidence_id": "graph:path-1",
        "source_type": "graph",
        "qualification": "qualified",
        "text": "纯电动客车 -> OWNS -> 助力油泵 -> CAUSES -> 保险熔断",
        "relationship_types": ["OWNS", "CAUSES"],
        "device": {"name": "纯电动客车"},
        "component": {"name": "助力油泵"},
        "fault": {"name": "保险熔断"},
        "solution": {},
        "source": {
            "document_id": "manual-1",
            "document_version": "v1",
            "section_id": "sec-pump",
            "source_chunk_uids": ["table-pump"],
            "pages": [60],
        },
    }
    parent_table = {
        "evidence_id": "manual:manual-1:table-parent",
        "source_type": "manual",
        "qualification": "qualified",
        "text": (
            "检查保险32A | 进行第二步 | 保险熔断 | 跟换同种规格保险\n"
            "检查继电器K6 | 继电器无法吸合 | 跟换同种继电器"
        ),
        "source": {
            "document_id": "manual-1",
            "document_version": "v1",
            "chunk_id": "table-parent",
            "parent_section_id": "sec-pump",
            "table_id": "table-pump",
            "page": 60,
        },
    }
    plan = ResponsePlan(
        plan_id="response-plan-test",
        coverage_status="complete",
        source_mode="normal",
        allowed_evidence=(graph, parent_table),
        missing_aspects=(),
        conflicts=(),
        ledger_digest="digest",
    )

    answer = plan.deterministic_fallback()

    assert "跟换同种规格保险" in answer
    assert "继电器无法吸合" not in answer
    assert "跟换同种继电器" not in answer


def test_graph_diagnostic_fallback_rejects_manual_solution_from_mismatched_source() -> None:
    graph = {
        "evidence_id": "graph:kgpath:device-a:component-a:fault-a:none",
        "source_type": "graph",
        "qualification": "qualified",
        "text": "设备A -> OWNS -> 泵A -> CAUSES -> 保险熔断",
        "relationship_types": ["OWNS", "CAUSES"],
        "device": {"id": "device-a", "name": "设备A"},
        "component": {"id": "component-a", "name": "泵A"},
        "fault": {"id": "fault-a", "name": "保险熔断"},
        "solution": {},
        "source": {
            "document_id": "doc-a",
            "document_version": "v1",
            "section_id": "section-a",
            "source_chunk_uids": ["table-a"],
            "pages": [1],
        },
    }
    matching_source = {
        "document_id": "doc-a",
        "document_version": "v1",
        "parent_section_id": "section-a",
        "table_id": "table-a",
        "page": 1,
        "device_name": "设备A",
        "component_name": "泵A",
        "row_index": 1,
    }
    mismatches = (
        {"document_id": "doc-b"},
        {"document_version": "v2"},
        {"page": 99},
        {"table_id": "table-b"},
        {"component_name": "泵B"},
    )

    for mismatch in mismatches:
        manual_source = {**matching_source, **mismatch}
        manual = {
            "evidence_id": "manual:wrong:fault-row",
            "source_type": "manual",
            "qualification": "qualified",
            "text": "col_4=保险熔断；col_5=更换错误规格保险",
            "source": manual_source,
        }
        plan = ResponsePlan(
            plan_id="response-plan-source-mismatch",
            coverage_status="complete",
            source_mode="normal",
            allowed_evidence=(graph, manual),
            missing_aspects=(),
            conflicts=(),
            ledger_digest="digest",
        )

        answer = plan.deterministic_fallback()

        assert "更换错误规格保险" not in answer
        assert "当前合格证据未给出进一步处理方法" in answer


def test_graph_diagnostic_fallback_accepts_manual_page_inside_graph_page_range() -> None:
    graph = {
        "evidence_id": "graph:kgpath:device-a:component-a:fault-a:none",
        "source_type": "graph",
        "qualification": "qualified",
        "relationship_types": ["OWNS", "CAUSES"],
        "device": {"name": "设备A"},
        "component": {"name": "泵A"},
        "fault": {"name": "保险熔断"},
        "solution": {},
        "source": {
            "document_id": "doc-a",
            "document_version": "v1",
            "section_id": "section-a",
            "source_chunk_uids": ["table-a"],
            "pages": [58, 61],
        },
    }
    manual = {
        "evidence_id": "manual:doc-a:table-a",
        "source_type": "manual",
        "qualification": "qualified",
        "text": "col_4=保险熔断；col_5=更换同种规格保险",
        "source": {
            "document_id": "doc-a",
            "document_version": "v1",
            "parent_section_id": "section-a",
            "table_id": "table-a",
            "page": 60,
            "row_index": 1,
        },
    }

    plan = ResponsePlan(
        plan_id="response-plan-range",
        coverage_status="complete",
        source_mode="normal",
        allowed_evidence=(graph, manual),
        missing_aspects=(),
        conflicts=(),
        ledger_digest="digest",
    )

    answer = plan.deterministic_fallback()

    assert "更换同种规格保险" in answer


def test_graph_diagnostic_fallback_accepts_same_section_when_optional_locators_are_missing() -> None:
    graph = {
        "evidence_id": "graph:kgpath:engine:pump:gasket:none",
        "source_type": "graph",
        "qualification": "qualified",
        "relationship_types": ["OWNS", "CAUSES"],
        "device": {"name": "摩托车发动机"},
        "component": {"name": "机油泵"},
        "fault": {"name": "油泵座垫变形"},
        "solution": {},
        "source": {
            "document_id": "manual-engine",
            "document_version": "v1",
            "section_id": "sec-oil-pump",
            "source_chunk_uids": ["sec-oil-pump:text:0000"],
            "pages": [18],
        },
    }
    manual = {
        "evidence_id": "manual:manual-engine:oil-pump-treatment",
        "source_type": "manual",
        "qualification": "qualified",
        "text": "若油泵座垫变形或开裂，应更换油泵座垫。",
        "source": {
            "document_id": "manual-engine",
            "parent_section_id": "sec-oil-pump",
        },
    }
    plan = ResponsePlan(
        plan_id="response-plan-missing-optional-locators",
        coverage_status="complete",
        source_mode="normal",
        allowed_evidence=(graph, manual),
        missing_aspects=(),
        conflicts=(),
        ledger_digest="digest",
    )

    answer = plan.deterministic_fallback()

    assert "更换油泵座垫" in answer
    assert "当前合格证据未给出进一步处理方法" not in answer


def test_graph_diagnostic_fallback_binds_solution_from_same_table_line() -> None:
    graph = {
        "evidence_id": "graph:kgpath:device-a:component-a:fault-a:none",
        "source_type": "graph",
        "qualification": "qualified",
        "relationship_types": ["OWNS", "CAUSES"],
        "device": {"name": "设备A"},
        "component": {"name": "泵A"},
        "fault": {"name": "保险熔断"},
        "solution": {},
        "source": {
            "document_id": "doc-a",
            "document_version": "v1",
            "section_id": "section-a",
            "source_chunk_uids": ["table-a"],
            "pages": [60],
        },
    }
    manual = {
        "evidence_id": "manual:doc-a:table-a-row",
        "source_type": "manual",
        "qualification": "qualified",
        "text": (
            "col_4=继电器无法吸合；col_5=更换继电器\n"
            "col_4=保险熔断；col_5=更换同种规格保险"
        ),
        "source": {
            "document_id": "doc-a",
            "document_version": "v1",
            "parent_section_id": "section-a",
            "table_id": "table-a",
            "page": 60,
            "row_index": 2,
        },
    }
    plan = ResponsePlan(
        plan_id="response-plan-same-row",
        coverage_status="complete",
        source_mode="normal",
        allowed_evidence=(graph, manual),
        missing_aspects=(),
        conflicts=(),
        ledger_digest="digest",
    )

    answer = plan.deterministic_fallback()

    assert "更换同种规格保险" in answer
    assert "更换继电器" not in answer


def test_graph_diagnostic_fallback_binds_treatment_from_same_manual_chunk() -> None:
    graph = {
        "evidence_id": "graph:kgpath:engine:spark-plug:fault:none",
        "source_type": "graph",
        "qualification": "qualified",
        "relationship_types": ["OWNS", "CAUSES"],
        "device": {"name": "摩托车发动机"},
        "component": {"name": "火花塞"},
        "fault": {"name": "火花塞损坏"},
        "solution": {},
        "source": {
            "document_id": "manual-engine",
            "document_version": "v1",
            "section_id": "sec-spark",
            "source_chunk_uids": ["sec-spark:text:0000"],
            "pages": [3],
        },
    }
    manual = {
        "evidence_id": "manual:manual-engine:spark-step",
        "source_type": "manual",
        "qualification": "qualified",
        "text": "检查火花塞螺纹以及中心电极处，若有损坏或变形，则应更换火花塞。",
        "source": {
            "document_id": "manual-engine",
            "document_version": "v1",
            "parent_section_id": "sec-spark",
            "chunk_uid": "sec-spark:text:0000",
            "page": 3,
        },
    }
    plan = ResponsePlan(
        plan_id="response-plan-manual-treatment",
        coverage_status="complete",
        source_mode="normal",
        allowed_evidence=(graph, manual),
        missing_aspects=(),
        conflicts=(),
        ledger_digest="digest",
    )

    answer = plan.deterministic_fallback()

    assert "火花塞损坏" in answer
    assert "更换火花塞" in answer
    assert "处理动作：火花塞更换" in answer
    assert "当前合格证据未给出进一步处理方法" not in answer


def test_graph_diagnostic_fallback_emits_canonical_replacement_target() -> None:
    graph = {
        "evidence_id": "graph:camshaft",
        "source_type": "graph",
        "qualification": "qualified",
        "relationship_types": ["OWNS", "CAUSES"],
        "device": {"name": "摩托车发动机"},
        "component": {"name": "凸轮轴"},
        "fault": {"name": "凸轮轴磨损"},
        "solution": {},
        "source": {
            "document_id": "manual-engine",
            "document_version": "v1",
            "section_id": "sec-camshaft",
            "source_chunk_uids": ["sec-camshaft:text:0000"],
            "pages": [12],
        },
    }
    manual = {
        "evidence_id": "manual:camshaft",
        "source_type": "manual",
        "qualification": "qualified",
        "text": "若存在磨损、腐蚀或划伤，应更换相应凸轮轴。",
        "source": {
            "document_id": "manual-engine",
            "document_version": "v1",
            "parent_section_id": "sec-camshaft",
            "chunk_uid": "sec-camshaft:text:0000",
            "page": 12,
        },
    }
    plan = ResponsePlan(
        plan_id="response-plan-camshaft",
        coverage_status="complete",
        source_mode="normal",
        allowed_evidence=(graph, manual),
        missing_aspects=(),
        conflicts=(),
        ledger_digest="digest",
    )

    answer = plan.deterministic_fallback()

    assert "更换相应凸轮轴" in answer
    assert "处理结论：更换凸轮轴" in answer
    assert "处理动作：凸轮轴更换" in answer


def test_unsupported_plan_never_offers_generic_causes_or_operations() -> None:
    plan = build_response_plan(
        "飞机发动机坏了有哪些常见原因？",
        _bundle("unsupported", missing_aspect_ids=["aircraft-causes"]),
        EvidenceLedger(),
    )

    answer = plan.deterministic_fallback()
    assert "没有找到足以回答该问题的可靠依据" in answer
    assert all(term not in answer for term in ("常见原因包括", "检查火花塞", "拆卸", "更换"))


def test_conflict_plan_lists_values_and_does_not_choose_one() -> None:
    bundle = _bundle(
        "conflict",
        conflict_eligible=[{
            "field": "火花塞间隙",
            "unit": "mm",
            "values": ["0.7", "0.9"],
            "candidate_ids": ["gap-a", "gap-b"],
            "aspect_ids": ["gap"],
        }],
    )
    plan = build_response_plan("火花塞间隙是多少？", bundle, _ledger())

    answer = plan.deterministic_fallback()
    assert "0.7 mm" in answer and "0.9 mm" in answer
    assert "存在冲突" in answer
    assert "确认设备型号或文档版本" in answer


def test_unbound_number_or_model_uses_deterministic_fallback() -> None:
    plan = build_response_plan("火花塞间隙标准是多少？", _bundle("complete"), _ledger())

    audited = finalize_response(plan, "结论是 AB120 型火花塞，间隙必须调到 1.2 mm。")

    assert audited.passed is False
    assert audited.used_fallback is True
    assert any(item.startswith("unbound_fact:") for item in audited.violations)
    assert "AB120" not in audited.answer
    assert "1.2" not in audited.answer
    assert "0.7 到 0.9 mm" in audited.answer


def test_bound_facts_pass_without_second_generation() -> None:
    plan = build_response_plan("火花塞间隙标准是多少？", _bundle("complete"), _ledger())
    draft = "火花塞间隙标准为 0.7 到 0.9 mm。"

    audited = finalize_response(plan, draft)

    assert audited.passed is True
    assert audited.used_fallback is False
    assert audited.answer == draft


def test_complete_plan_falls_back_when_one_supported_manual_obligation_is_omitted() -> None:
    entries = [
        {
            "evidence_id": "manual:manual-engine:pump-check",
            "source_type": "manual",
            "qualification": "qualified",
            "text": "检查机油泵转子是否转动灵活。",
            "source": {"document_id": "manual-engine", "chunk_id": "pump-check", "page": 18},
        },
        {
            "evidence_id": "manual:manual-engine:pump-repair",
            "source_type": "manual",
            "qualification": "qualified",
            "text": "若油泵座垫变形或开裂，应更换油泵座垫。",
            "source": {"document_id": "manual-engine", "chunk_id": "pump-repair", "page": 18},
        },
    ]
    bundle = {
        "aspect_support": [
            {
                "aspect_id": "inspection",
                "aspect_text": "机油泵检查方法",
                "supported": True,
                "evidence_ids": ["manual:manual-engine:pump-check"],
                "user_obligation": True,
            },
            {
                "aspect_id": "treatment",
                "aspect_text": "油泵座垫维修方法",
                "supported": True,
                "evidence_ids": ["manual:manual-engine:pump-repair"],
                "user_obligation": True,
            },
        ],
        "missing_aspect_ids": [],
        "conflict_eligible": [],
    }
    plan = build_response_plan(
        "机油泵怎么检查，油泵座垫损坏后怎么维修",
        bundle,
        EvidenceLedger(entries),
    )

    audited = finalize_response(plan, "检查机油泵转子是否转动灵活。")

    assert audited.passed is False
    assert audited.used_fallback is True
    assert "检查机油泵转子" in audited.answer
    assert "更换油泵座垫" in audited.answer
    assert "omitted_supported_claim:treatment" in audited.violations


def test_evidence_rendered_table_is_not_discarded_for_manual_lead_style() -> None:
    ledger = _ledger(text="1. 气缸体分部件；数量：1", page=17)
    plan = build_response_plan(
        "帮我查询气缸活塞装配部件清单",
        _bundle("complete"),
        ledger,
    )
    draft = "根据手册第17-18页“5.1 气缸活塞装配部件清单”，所用部件如下：\n1. 气缸体分部件；数量：1"

    audited = finalize_response(plan, draft, evidence_rendered=True)

    assert audited.passed is True
    assert audited.used_fallback is False
    assert audited.answer == draft
    assert "unsolicited_manual_lead" not in audited.violations


def test_manual_fallback_deduplicates_and_restores_source_order() -> None:
    entries = []
    for step in (4, 2, 1, 3):
        entries.append({
            "evidence_id": f"manual:manual-1:source-{step}",
            "source_type": "manual",
            "text": f"{step}. 执行第{step}步。",
            "qualification": "qualified",
            "source": {
                "document_id": "manual-1",
                "chunk_id": f"source-{step}",
                "source_chunk_id": f"source-{step}",
                "chunk_type": "step_raw",
                "parent_section_id": "sec-tensioner",
                "section_index": 4,
                "page": 13,
                "source_index": step,
                "child_index": step - 1,
            },
        })
    plan = build_response_plan("如何安装涨紧器？", _bundle("complete"), EvidenceLedger(entries))

    answer = plan.deterministic_fallback()

    assert answer.index("1. 执行第1步") < answer.index("2. 执行第2步")
    assert answer.index("2. 执行第2步") < answer.index("3. 执行第3步")
    assert answer.index("3. 执行第3步") < answer.index("4. 执行第4步")
    assert all(answer.count(f"执行第{step}步") == 1 for step in range(1, 5))


def test_plan_derives_coverage_instead_of_trusting_claimed_status() -> None:
    bundle = _bundle(
        "conflict",
        aspect_support=[{
            "aspect_id": "gap",
            "aspect_text": "火花塞间隙标准",
            "supported": True,
            "evidence_ids": ["chunk-1"],
        }],
        conflict_eligible=[],
        capabilities={"may_cite_manual": True},
    )

    plan = build_response_plan("火花塞间隙标准是多少？", bundle, _ledger())

    assert plan.coverage_status == "complete"


def test_missing_bundle_never_defaults_to_complete_even_with_ledger() -> None:
    plan = build_response_plan("火花塞间隙标准是多少？", {}, _ledger())

    assert plan.coverage_status == "unsupported"


def test_measurement_audit_binds_value_unit_and_object() -> None:
    plan = build_response_plan("火花塞间隙标准是多少？", _bundle("complete"), _ledger())

    audited = finalize_response(plan, "气门间隙为 0.7 到 0.9 cm。")

    assert audited.passed is False
    assert audited.used_fallback is True
    assert "气门间隙" not in audited.answer
    assert "cm" not in audited.answer
    assert any(item.startswith("unbound_measurement:") for item in audited.violations)


def test_safety_requirement_change_uses_deterministic_fallback() -> None:
    ledger = _ledger(text="拆卸火花塞前必须断电并等待发动机冷却。")
    plan = build_response_plan("拆卸火花塞前要注意什么？", _bundle("complete"), ledger)

    audited = finalize_response(plan, "拆卸火花塞前无需断电，可以直接操作。")

    assert audited.passed is False
    assert audited.used_fallback is True
    assert "无需断电" not in audited.answer
    assert "unbound_safety_requirement" in audited.violations


def test_unsupported_declarative_cause_cannot_bypass_plan() -> None:
    plan = build_response_plan(
        "发动机无法启动的原因是什么？",
        _bundle("unsupported"),
        EvidenceLedger(),
    )

    audited = finalize_response(plan, "点火线圈损坏会导致发动机无法启动。")

    assert audited.passed is False
    assert audited.used_fallback is True
    assert "点火线圈损坏" not in audited.answer


def test_conflict_fallback_maps_each_value_to_public_source() -> None:
    ledger = EvidenceLedger([
        {
            "evidence_id": "manual:manual-a:gap-a",
            "source_type": "manual",
            "text": "火花塞间隙为 0.7 mm",
            "qualification": "reference_only",
            "source": {"document_id": "manual-a", "document_version": "v1", "chunk_id": "gap-a", "page": 3},
        },
        {
            "evidence_id": "manual:manual-b:gap-b",
            "source_type": "manual",
            "text": "火花塞间隙为 0.9 mm",
            "qualification": "reference_only",
            "source": {"document_id": "manual-b", "document_version": "v2", "chunk_id": "gap-b", "page": 8},
        },
    ])
    bundle = _bundle(
        "complete",
        conflict_eligible=[{
            "field": "火花塞间隙",
            "unit": "mm",
            "values": ["0.7", "0.9"],
            "candidate_ids": ["gap-a", "gap-b"],
            "alternatives": [
                {"value": "0.7", "candidate_ids": ["gap-a"]},
                {"value": "0.9", "candidate_ids": ["gap-b"]},
            ],
            "aspect_ids": ["gap"],
        }],
    )

    plan = build_response_plan("火花塞间隙是多少？", bundle, ledger)
    answer = plan.deterministic_fallback()

    assert plan.coverage_status == "conflict"
    assert [item["evidence_id"] for item in plan.allowed_evidence] == [
        "manual:manual-a:gap-a",
        "manual:manual-b:gap-b",
    ]
    assert plan.pending_clarification is not None
    assert "0.7 mm（手册第3页，版本v1）" in answer
    assert "0.9 mm（手册第8页，版本v2）" in answer
    assert "gap-a" not in answer and "gap-b" not in answer
    assert "候选证据" not in answer
    assert plan.pending_clarification is not None
    assert plan.to_metadata()["pending_clarification"]["clarification_id"].startswith("clarification-")
