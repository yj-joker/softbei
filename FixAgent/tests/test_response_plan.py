"""ResponsePlan generation and deterministic answer-audit tests."""

from services.retrieval.evidence import EvidenceLedger
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


def test_normal_complete_plan_is_conclusion_first_without_manual_lead() -> None:
    plan = build_response_plan("火花塞间隙标准是多少？", _bundle("complete"), _ledger())

    assert isinstance(plan, ResponsePlan)
    assert plan.source_mode == "normal"
    answer = plan.deterministic_fallback()
    assert answer.startswith("火花塞间隙标准为 0.7 到 0.9 mm")
    assert not answer.startswith(("根据手册", "依据手册", "按照手册"))
    assert "来源：手册第3页" in answer


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

    answer = build_response_plan("火花塞间隙是多少？", bundle, ledger).deterministic_fallback()

    assert "0.7 mm（手册第3页，版本v1）" in answer
    assert "0.9 mm（手册第8页，版本v2）" in answer
    assert "gap-a" not in answer and "gap-b" not in answer
    assert "候选证据" not in answer
