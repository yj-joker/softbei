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
