"""Evidence Bundle v2 and deterministic coverage tests."""

from services.retrieval.aspects import QuestionAspect
from services.retrieval.evidence import EvidenceCoverage, determine_coverage
from services.retrieval.qualification import qualify_candidates


def _aspect(aspect_id: str, text: str) -> QuestionAspect:
    return QuestionAspect(aspect_id=aspect_id, text=text)


def _candidate(
    evidence_id: str,
    *,
    content: str,
    parameter_name: str | None = None,
    numeric_value: str | None = None,
) -> dict:
    metadata = {
        "device_type": "motorcycle-engine",
        "document_id": "manual-1",
        "document_version": "v1",
        "record_type": "manual",
        "chunk_id": evidence_id,
        "section_title": content,
        "local_rerank_features": {"query_coverage": 0.9, "title_coverage": 0.9},
    }
    if parameter_name is not None:
        metadata["parameter_names"] = [parameter_name]
    if numeric_value is not None:
        metadata["numeric_values"] = [numeric_value]
        metadata["units"] = ["mm"]
    return {"doc_id": evidence_id, "content": content, "metadata": metadata}


def _bundle(candidates: list[dict], aspects: list[QuestionAspect], **kwargs) -> dict:
    return qualify_candidates(
        "火花塞间隙标准和建议更换周期分别是多少",
        candidates,
        document_id="manual-1",
        device_type="motorcycle-engine",
        document_version="v1",
        requires_strict_evidence=True,
        aspects=aspects,
        **kwargs,
    )


def test_bundle_v2_retains_v1_keys_and_stable_source_identity() -> None:
    aspects = [_aspect("gap", "火花塞间隙标准")]
    bundle = _bundle([_candidate("chunk-7", content="火花塞间隙标准为 0.7 到 0.9 mm")], aspects)

    assert bundle["evidence_bundle_version"] == 2
    for key in (
        "overall_status",
        "qualified_evidence",
        "reference_evidence",
        "excluded_evidence",
        "conflicts",
        "capabilities",
        "summary",
    ):
        assert key in bundle
    assert bundle["evidence_identity"]["document_id"] == "manual-1"
    assert bundle["evidence_identity"]["device_type"] == "motorcycle-engine"
    assert bundle["evidence_identity"]["document_version"] == "v1"
    assert bundle["qualified_evidence"][0]["metadata"]["evidence_id"] == "chunk-7"


def test_coverage_is_complete_only_when_every_aspect_has_support() -> None:
    aspects = [
        _aspect("gap", "火花塞间隙标准"),
        _aspect("cycle", "建议更换周期"),
    ]
    partial = _bundle(
        [_candidate("gap-doc", content="火花塞间隙标准为 0.7 到 0.9 mm")],
        aspects,
    )
    complete = _bundle(
        [
            _candidate("gap-doc", content="火花塞间隙标准为 0.7 到 0.9 mm"),
            _candidate("cycle-doc", content="建议更换周期为 8000 km"),
        ],
        aspects,
    )

    assert partial["coverage_status"] == "partial"
    assert partial["missing_aspect_ids"] == ["cycle"]
    assert complete["coverage_status"] == "complete"
    assert complete["missing_aspect_ids"] == []


def test_zero_qualified_evidence_is_unsupported_and_disables_generic_guidance() -> None:
    aspects = [_aspect("gap", "火花塞间隙标准")]
    bundle = _bundle([], aspects)

    assert bundle["coverage_status"] == "unsupported"
    assert bundle["capabilities"]["may_offer_generic_guidance"] is False


def test_legacy_callers_automatically_receive_v2_coverage() -> None:
    bundle = qualify_candidates(
        "火花塞间隙标准是多少",
        [],
        document_id="manual-1",
        device_type="motorcycle-engine",
        requires_strict_evidence=True,
    )

    assert bundle["coverage_status"] == "unsupported"
    assert bundle["missing_aspect_ids"]
    assert bundle["capabilities"]["may_offer_generic_guidance"] is False


def test_interrogative_suffix_does_not_hide_supported_single_aspect() -> None:
    bundle = qualify_candidates(
        "火花塞间隙标准是多少？",
        [_candidate("gap-doc", content="火花塞间隙标准为 0.7 到 0.9 mm")],
        document_id="manual-1",
        device_type="motorcycle-engine",
        document_version="v1",
        requires_strict_evidence=True,
    )

    assert bundle["coverage_status"] == "complete"
    assert bundle["missing_aspect_ids"] == []


def test_spaced_semantic_aspect_is_supported_by_matching_manual_fact() -> None:
    aspects = [_aspect("starter-nut", "起动电机 装配 正极线 螺母 数量 扭矩")]
    bundle = _bundle(
        [_candidate(
            "starter-nut-row",
            content="零件名称=正极线螺母；数量=1；工具/扭矩要求=10#套筒 / 5±1.5 N·m",
        )],
        aspects,
    )

    assert bundle["coverage_status"] == "complete"
    assert bundle["supported_aspect_ids"] == ["starter-nut"]


def test_semantic_aspect_does_not_accept_unrelated_same_section_row() -> None:
    aspects = [_aspect("starter-nut", "起动电机 装配 正极线 螺母 数量 扭矩")]
    bundle = _bundle(
        [_candidate(
            "starter-motor-row",
            content="零件名称=起动电机；数量=1",
        )],
        aspects,
    )

    assert bundle["coverage_status"] == "unsupported"
    assert bundle["supported_aspect_ids"] == []


def test_direction_aspect_accepts_equivalent_manual_wording() -> None:
    aspects = [_aspect("spring-direction", "气门弹簧 安装方向 密距端 疏距端")]
    bundle = _bundle(
        [_candidate(
            "spring-direction-step",
            content="气门弹簧间距较密的一端必须朝下安装。",
        )],
        aspects,
    )

    assert bundle["coverage_status"] == "complete"
    assert bundle["supported_aspect_ids"] == ["spring-direction"]


def test_location_aspect_accepts_named_mounting_location() -> None:
    aspects = [_aspect(
        "coolant-location",
        "发动机 拆卸 冷却液 排放 位置 右水箱盖 打开时机",
    )]
    bundle = _bundle(
        [_candidate(
            "coolant-drain-step",
            content=(
                "排放冷却液：拆下水泵盖上的放水螺栓。"
                "当水流变小时，打开右水箱盖，使发动机内剩余冷却液完全排出。"
            ),
        )],
        aspects,
    )

    assert bundle["coverage_status"] == "complete"


def test_entity_qualified_marker_terms_remain_evidence_anchors() -> None:
    aspects = [_aspect("timing-marks", "曲柄C标记 平衡轴D标记 对齐 正时")]
    bundle = _bundle(
        [_candidate(
            "timing-mark-step",
            content="曲柄上的C标记应与平衡轴配重块上的D标记对齐。",
        )],
        aspects,
    )

    assert bundle["coverage_status"] == "complete"


def test_compact_semantic_aspect_matches_manual_fact_without_exact_suffix() -> None:
    aspects = [_aspect("cylinder-group", "气缸与活塞组别匹配要求")]
    bundle = _bundle(
        [_candidate(
            "cylinder-group-rule",
            content=(
                "气缸与活塞组别：活塞与气缸均分为A、B、C、D四组，"
                "组装时必须使用相同组别的活塞与气缸。"
            ),
        )],
        aspects,
    )

    assert bundle["coverage_status"] == "complete"


def test_conflict_retains_candidate_ids_and_values_after_demotion() -> None:
    aspects = [_aspect("gap", "火花塞间隙标准")]
    bundle = _bundle(
        [
            _candidate("gap-a", content="火花塞间隙标准为 0.7 mm", parameter_name="火花塞间隙", numeric_value="0.7"),
            _candidate("gap-b", content="火花塞间隙标准为 0.9 mm", parameter_name="火花塞间隙", numeric_value="0.9"),
        ],
        aspects,
    )

    assert bundle["coverage_status"] == "conflict"
    assert bundle["qualified_evidence"] == []
    assert bundle["conflict_eligible"][0]["candidate_ids"] == ["gap-a", "gap-b"]
    assert bundle["conflict_eligible"][0]["values"] == ["0.7", "0.9"]
    assert bundle["conflict_eligible"][0]["alternatives"] == [
        {"value": "0.7", "candidate_ids": ["gap-a"]},
        {"value": "0.9", "candidate_ids": ["gap-b"]},
    ]


def test_coverage_priority_is_scope_then_conflict_then_zero_then_aspects() -> None:
    aspects = [_aspect("gap", "火花塞间隙标准")]
    bundle = _bundle(
        [
            _candidate("gap-a", content="火花塞间隙标准为 0.7 mm", parameter_name="火花塞间隙", numeric_value="0.7"),
            _candidate("gap-b", content="火花塞间隙标准为 0.9 mm", parameter_name="火花塞间隙", numeric_value="0.9"),
        ],
        aspects,
        scope_status="out_of_scope",
    )

    coverage = determine_coverage(bundle, aspects=aspects, scope_status="out_of_scope")
    assert isinstance(coverage, EvidenceCoverage)
    assert coverage.status == "unsupported"
    assert coverage.reason == "out_of_scope"
