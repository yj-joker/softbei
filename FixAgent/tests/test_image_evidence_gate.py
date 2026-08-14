from __future__ import annotations

import pytest

from schemas.response import EvidenceImage


def _gate_api():
    try:
        from services.retrieval.image_evidence_gate import (
            ImageEvidenceContext,
            authorize_image,
            normalize_binding_role,
        )
    except ModuleNotFoundError:
        pytest.fail("image evidence gate is not implemented")
    return ImageEvidenceContext, authorize_image, normalize_binding_role


def _candidate(**updates) -> EvidenceImage:
    base = EvidenceImage(
        image_url="/candidate.png",
        page=5,
        document_id="manual-1",
        source_chunk_id="image-1",
        role="same_page_step",
        binding_confidence=1.0,
        step_ids=["step-1"],
    )
    return base.model_copy(update=updates)


def _context(**updates):
    ImageEvidenceContext, _, _ = _gate_api()
    values = {
        "target_non_image_source_ids": frozenset({"step-1"}),
        "needs_images": True,
    }
    values.update(updates)
    return ImageEvidenceContext(**values)


def test_legacy_same_page_role_is_not_promoted_to_positioned_binding() -> None:
    _, _, normalize_binding_role = _gate_api()
    image = _candidate()

    assert normalize_binding_role(image.role) == "legacy_same_page_step"
    assert image.role == "same_page_step"


def test_legacy_same_page_step_is_rejected_even_when_id_intersects() -> None:
    _, authorize_image, _ = _gate_api()

    rejected = authorize_image(_candidate(), _context())

    assert rejected.allowed is False
    assert rejected.reason == "legacy_image_binding"


def test_v2_target_binding_authorizes_only_its_bound_answer_step() -> None:
    _, authorize_image, _ = _gate_api()
    image = _candidate(
        role="positioned_step",
        step_ids=[],
        binding_schema_version=2,
        bindings=[{
            "target_id": "step-1",
            "target_type": "step",
            "relation": "layout_anchor",
            "confidence": 0.95,
        }],
    )

    allowed = authorize_image(image, _context())
    rejected = authorize_image(
        image,
        _context(target_non_image_source_ids=frozenset({"step-other"})),
    )

    assert allowed.reason == "answer_evidence_binding"
    assert rejected.allowed is False


def test_v2_answer_binding_with_local_semantics_must_match_query_target() -> None:
    _, authorize_image, _ = _gate_api()
    image = _candidate(
        role="positioned_step",
        step_ids=[],
        binding_schema_version=2,
        image_title="局部连接示意图",
        bindings=[{
            "target_id": "step-1",
            "target_type": "step",
            "relation": "layout_anchor",
            "confidence": 0.95,
        }],
    )

    rejected = authorize_image(
        image,
        _context(require_local_semantic_match=True),
        local_semantic_match=False,
    )
    allowed = authorize_image(image, _context(), local_semantic_match=True)

    assert rejected.allowed is False
    assert rejected.reason == "image_query_target_mismatch"
    assert allowed.reason == "answer_evidence_binding"


def test_procedure_scope_never_authorizes_without_target_binding() -> None:
    _, authorize_image, _ = _gate_api()
    image = _candidate(
        role="positioned_step",
        step_ids=[],
        procedure_scope_ids=["scope-1"],
    )

    decision = authorize_image(
        image,
        _context(
            target_non_image_source_ids=frozenset(),
            target_procedure_scope_ids=frozenset({"scope-1"}),
        ),
    )

    assert decision.allowed is False
    assert decision.reason == "no_image_level_binding"


def test_page_membership_alone_never_authorizes_image() -> None:
    _, authorize_image, _ = _gate_api()
    image = _candidate(role="", step_ids=[], text_ids=[], procedure_scope_ids=[])

    assert authorize_image(image, _context()).allowed is False


def test_allowed_source_id_alone_cannot_self_authorize_image() -> None:
    _, authorize_image, _ = _gate_api()
    image = _candidate(source_chunk_id="image-1", step_ids=[])
    decision = authorize_image(
        image,
        _context(
            target_non_image_source_ids=frozenset(),
            direct_image_source_ids=frozenset(),
        ),
    )

    assert decision.allowed is False


def test_claim_bound_direct_image_can_be_authorized() -> None:
    _, authorize_image, _ = _gate_api()
    image = _candidate(source_chunk_id="image-1", step_ids=[])
    decision = authorize_image(
        image,
        _context(
            target_non_image_source_ids=frozenset(),
            direct_image_source_ids=frozenset({"image-1"}),
        ),
    )

    assert decision.reason == "direct_image_evidence"


def test_exact_target_section_image_can_be_authorized() -> None:
    _, authorize_image, _ = _gate_api()
    image = _candidate(source_chunk_id="image-1", step_ids=[])
    decision = authorize_image(
        image,
        _context(
            target_non_image_source_ids=frozenset(),
            exact_section_image_source_ids=frozenset({"image-1"}),
        ),
    )

    assert decision.reason == "exact_target_section_binding"


def test_page_render_cannot_use_exact_target_section_authorization() -> None:
    _, authorize_image, _ = _gate_api()
    image = _candidate(
        source_chunk_id="rendered-page:manual-1:5",
        context_role="page_render",
        role="page_fallback",
        step_ids=[],
    )
    decision = authorize_image(
        image,
        _context(
            exact_section_image_source_ids=frozenset({"rendered-page:manual-1:5"}),
        ),
    )

    assert decision.allowed is False
    assert decision.reason == "page_render_not_requested"


def test_page_fallback_cannot_use_procedure_scope() -> None:
    _, authorize_image, _ = _gate_api()
    image = _candidate(
        role="page_fallback",
        step_ids=[],
        procedure_scope_ids=["scope-1"],
    )
    decision = authorize_image(
        image,
        _context(
            target_non_image_source_ids=frozenset(),
            target_procedure_scope_ids=frozenset({"scope-1"}),
        ),
    )

    assert decision.allowed is False


def test_negative_image_request_is_terminal() -> None:
    _, authorize_image, _ = _gate_api()

    decision = authorize_image(
        _candidate(),
        _context(negative_image_request=True),
    )

    assert decision.reason == "negative_image_request"


def test_local_semantics_are_only_used_for_explicit_visual_query() -> None:
    _, authorize_image, _ = _gate_api()
    image = _candidate(
        role="page_fallback",
        step_ids=[],
        image_title="起动电机安装图",
    )

    implicit = authorize_image(image, _context(), local_semantic_match=True)
    explicit = authorize_image(
        image,
        _context(explicit_visual_request=True),
        local_semantic_match=True,
    )

    assert implicit.allowed is False
    assert explicit.reason == "image_local_query_match"


def test_explicit_page_render_is_the_only_page_render_authorization() -> None:
    _, authorize_image, _ = _gate_api()
    image = _candidate(context_role="page_render", role="page_fallback", step_ids=[])

    assert authorize_image(image, _context()).allowed is False
    explicit = authorize_image(image, _context(explicit_page_render=True))
    assert explicit.reason == "explicit_page_render"
