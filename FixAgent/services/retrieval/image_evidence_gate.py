"""Pure image-level authorization for response evidence."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import Callable, Iterable

from schemas.response import EvidenceImage


ROLE_ALIASES = {
    "same_page_step": "legacy_same_page_step",
    "same_page_text": "legacy_same_page_text",
    "positioned_step": "positioned_step",
    "positioned_text": "positioned_text",
    "page_fallback": "page_fallback",
    "section_fallback": "section_fallback",
}
STRONG_BINDING_ROLES = frozenset({"positioned_step", "positioned_text"})
LEGACY_BINDING_ROLES = frozenset({"legacy_same_page_step", "legacy_same_page_text"})


@dataclass(frozen=True)
class ImageEvidenceContext:
    target_non_image_source_ids: frozenset[str] = frozenset()
    direct_image_source_ids: frozenset[str] = frozenset()
    exact_section_image_source_ids: frozenset[str] = frozenset()
    target_procedure_scope_ids: frozenset[str] = frozenset()
    needs_images: bool = False
    explicit_visual_request: bool = False
    negative_image_request: bool = False
    explicit_page_render: bool = False
    require_local_semantic_match: bool = False
    minimum_binding_confidence: float = 0.8


@dataclass(frozen=True)
class ImageGateDecision:
    allowed: bool
    reason: str
    normalized_role: str


def normalize_binding_role(role: str) -> str:
    normalized = str(role or "").strip().lower()
    return ROLE_ALIASES.get(normalized, normalized)


def _local_semantics_available(image: EvidenceImage) -> bool:
    confident_caption = bool(image.caption) and image.caption_confidence >= 0.8
    return bool(image.image_title or image.image_summary or confident_caption)


def _strong_target_binding_ids(
    image: EvidenceImage,
    *,
    minimum_confidence: float,
) -> set[str]:
    binding_ids: set[str] = set()
    for raw_binding in image.bindings:
        if isinstance(raw_binding, Mapping):
            target_id = str(raw_binding.get("target_id") or "").strip()
            target_type = str(raw_binding.get("target_type") or "").strip()
            confidence = float(raw_binding.get("confidence") or 0.0)
        else:
            target_id = str(raw_binding.target_id or "").strip()
            target_type = str(raw_binding.target_type or "").strip()
            confidence = float(raw_binding.confidence or 0.0)
        if (
            target_id
            and target_type in {"step", "text", "table"}
            and confidence >= minimum_confidence
        ):
            binding_ids.add(target_id)
    if binding_ids or image.binding_schema_version >= 2:
        return binding_ids
    return {
        str(value).strip()
        for value in (image.step_id, *image.step_ids, *image.text_ids)
        if str(value).strip()
    }


def has_strong_answer_binding(
    image: EvidenceImage,
    target_non_image_source_ids: frozenset[str] | set[str],
    *,
    minimum_confidence: float = 0.8,
) -> bool:
    """Return whether Schema v2 evidence binds the image to a final answer item."""
    role = normalize_binding_role(image.role)
    if role not in STRONG_BINDING_ROLES or image.binding_confidence < minimum_confidence:
        return False
    binding_ids = _strong_target_binding_ids(
        image,
        minimum_confidence=minimum_confidence,
    )
    return bool(binding_ids.intersection(target_non_image_source_ids))


def authorize_image(
    image: EvidenceImage,
    context: ImageEvidenceContext,
    *,
    local_semantic_match: bool = False,
) -> ImageGateDecision:
    role = normalize_binding_role(image.role)

    def reject(reason: str) -> ImageGateDecision:
        return ImageGateDecision(False, reason, role)

    def allow(reason: str) -> ImageGateDecision:
        return ImageGateDecision(True, reason, role)

    if context.negative_image_request:
        return reject("negative_image_request")
    if not context.needs_images:
        return reject("query_does_not_require_images")
    if (image.context_role or image.role) == "page_render":
        if context.explicit_page_render:
            return allow("explicit_page_render")
        return reject("page_render_not_requested")

    source_id = str(image.source_chunk_id or "").strip()
    if source_id and source_id in context.direct_image_source_ids:
        return allow("direct_image_evidence")
    if source_id and source_id in context.exact_section_image_source_ids:
        return allow("exact_target_section_binding")

    if role in LEGACY_BINDING_ROLES:
        return reject("legacy_image_binding")

    if has_strong_answer_binding(
        image,
        context.target_non_image_source_ids,
        minimum_confidence=context.minimum_binding_confidence,
    ):
        if (
            context.require_local_semantic_match
            and _local_semantics_available(image)
            and not local_semantic_match
        ):
            return reject("image_query_target_mismatch")
        return allow("answer_evidence_binding")

    if (
        context.explicit_visual_request
        and local_semantic_match
        and _local_semantics_available(image)
    ):
        return allow("image_local_query_match")
    return reject("no_image_level_binding")


def authorize_images(
    images: Iterable[EvidenceImage],
    context: ImageEvidenceContext,
    *,
    semantic_matcher: Callable[[EvidenceImage], bool],
) -> tuple[list[tuple[EvidenceImage, ImageGateDecision]], list[dict[str, str]]]:
    accepted: list[tuple[EvidenceImage, ImageGateDecision]] = []
    rejected: list[dict[str, str]] = []
    for image in images:
        local_match = semantic_matcher(image) if _local_semantics_available(image) else False
        decision = authorize_image(
            image,
            context,
            local_semantic_match=local_match,
        )
        if decision.allowed:
            accepted.append((image, decision))
            continue
        rejected.append({
            "source_chunk_id": image.source_chunk_id,
            "page": str(image.page or ""),
            "role": image.role,
            "normalized_role": decision.normalized_role,
            "reason": decision.reason,
        })
    return accepted, rejected
