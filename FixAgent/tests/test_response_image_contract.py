import asyncio

import re

import pytest

from api import main
from schemas.response import EvidenceImage


def _image(
    page: int,
    name: str,
    *,
    step_ids: list[str] | None = None,
    text_ids: list[str] | None = None,
    procedure_scope_ids: list[str] | None = None,
    section_title: str = "",
    context_role: str = "",
    binding_confidence: float = 0.95,
    bound: bool = True,
) -> EvidenceImage:
    ids = step_ids or []
    return EvidenceImage(
        image_url=f"/{name}.png",
        page=page,
        document_id="manual-1",
        source_chunk_id=f"image-{name}",
        step_id=ids[0] if ids else "",
        step_ids=ids,
        text_ids=text_ids or [],
        procedure_scope_ids=procedure_scope_ids or [],
        caption=name,
        section_title=section_title,
        context_role=context_role,
        binding_confidence=binding_confidence,
        role=("positioned_step" if ids else "positioned_text") if bound else "",
    )


def test_explicit_no_image_contract_returns_no_images() -> None:
    metadata = {
        "original_user_message": "只告诉我扭矩，不要图片",
        "response_policy": {"images_allowed": True},
        "allowed_evidence_pages": [8],
    }

    selected = main._select_evidence_images_for_response([_image(8, "torque")], metadata)

    assert selected == []
    assert metadata["image_selection_contract"]["mode"] == "none"


def test_query_understanding_none_is_terminal_for_fact_query() -> None:
    metadata = {
        "original_user_message": "气门弹簧自由长度是多少？",
        "query_understanding_selection_mode": "none",
        "response_policy": {"images_allowed": True},
        "_deterministic_answer_evidence_pages": [17],
        "allowed_document_ids": ["manual-1"],
    }

    selected = main._select_evidence_images_for_response(
        [_image(17, "same-page-figure", section_title="4.8 气门")],
        metadata,
    )

    assert selected == []
    assert metadata["image_selection_contract"]["mode"] == "none"


def _claim_scope_metadata(*, audit_passed: bool = True) -> dict:
    return {
        "original_user_message": "如何安装起动电机",
        "response_audit": {"passed": audit_passed},
        "allowed_source_chunk_ids": ["step-final", "step-distractor"],
        "allowed_evidence_pages": [5, 17],
        "_deterministic_answer_evidence_pages": [5, 17],
        "_deterministic_answer_document_ids": ["manual-1", "manual-2"],
        "_deterministic_answer_section_ids": ["section-final", "section-distractor"],
        "authorized_claim_evidence_bindings": [{
            "claim_id": "install",
            "evidence_ids": ["manual:manual-1:step-final"],
        }],
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": [
                    {
                        "id": "step-final",
                        "content": "安装起动电机并紧固螺栓。",
                        "metadata": {
                            "document_id": "manual-1",
                            "page": 5,
                            "chunk_type": "text",
                            "chunk_label": "step",
                            "answer_role": "procedure_step",
                            "parent_section_id": "section-final",
                            "procedure_scope_ids": ["scope-final"],
                        },
                    },
                    {
                        "id": "step-distractor",
                        "content": "拆卸其他总成。",
                        "metadata": {
                            "document_id": "manual-2",
                            "page": 17,
                            "chunk_type": "text",
                            "chunk_label": "step",
                            "answer_role": "procedure_step",
                            "parent_section_id": "section-distractor",
                            "procedure_scope_ids": ["scope-distractor"],
                        },
                    },
                ],
            }],
        }],
    }


def _claim_bound_test_metadata(metadata: dict) -> dict:
    """Upgrade legacy selector fixtures to the final-claim evidence contract."""
    pages = [
        int(value)
        for value in (
            metadata.get("_deterministic_answer_evidence_pages")
            or metadata.get("allowed_evidence_pages")
            or []
        )
        if str(value).isdigit()
    ]
    document_ids = [
        str(value)
        for value in (
            metadata.get("_deterministic_answer_document_ids")
            or metadata.get("allowed_document_ids")
            or ["manual-1"]
        )
        if str(value).strip()
    ]
    source_ids = [
        str(value)
        for value in metadata.get("allowed_source_chunk_ids") or []
        if str(value).strip()
    ]
    scope_id = str(metadata.get("_deterministic_answer_procedure_scope_id") or "").strip()
    if not source_ids and scope_id:
        source_ids = ["test-final-scope-evidence"]
    section_ids = [
        str(value)
        for value in metadata.get("_deterministic_answer_section_ids") or []
        if str(value).strip()
    ]
    trace = list(metadata.get("react_trace") or [])
    existing_ids = {
        str(item.get("id") or item.get("doc_id") or "")
        for step in trace
        if isinstance(step, dict)
        for call in step.get("tool_calls") or []
        if isinstance(call, dict)
        for item in call.get("result_data") or []
        if isinstance(item, dict)
    }
    synthetic = []
    for index, source_id in enumerate(source_ids):
        if source_id in existing_ids:
            continue
        numeric_parts = [int(value) for value in re.findall(r"\d+", source_id)]
        page = next((value for value in numeric_parts if value in pages), None)
        if page is None and pages:
            page = pages[-1]
        synthetic.append({
            "id": source_id,
            "content": f"final evidence for {source_id}",
            "metadata": {
                "document_id": document_ids[0] if document_ids else "manual-1",
                "page": page,
                "chunk_type": "text",
                "chunk_label": "step",
                "answer_role": "procedure_step",
                "parent_section_id": section_ids[0] if section_ids else "",
                "section_title": str(metadata.get("_deterministic_answer_section_title") or ""),
                "procedure_scope_ids": [scope_id] if scope_id else [],
            },
        })
    if synthetic:
        trace.append({"tool_calls": [{"name": "knowledge_retrieval", "result_data": synthetic}]})
    metadata["react_trace"] = trace
    existing_bindings = list(metadata.get("authorized_claim_evidence_bindings") or [])
    existing_bound_ids = {
        str(evidence_id)
        for binding in existing_bindings
        if isinstance(binding, dict)
        for evidence_id in binding.get("evidence_ids") or []
    }
    document_id = document_ids[0] if document_ids else "manual-1"
    evidence_ids = [
        f"manual:{document_id}:{source_id}"
        for source_id in source_ids
        if f"manual:{document_id}:{source_id}" not in existing_bound_ids
    ]
    if evidence_ids:
        existing_bindings.append({"claim_id": "test-final-claim", "evidence_ids": evidence_ids})
    metadata["authorized_claim_evidence_bindings"] = existing_bindings
    metadata.setdefault("response_audit", {"passed": True})
    return metadata


def test_final_answer_scope_uses_only_claim_bound_manual_evidence() -> None:
    metadata = _claim_scope_metadata()

    assert main._final_answer_non_image_source_ids(metadata) == {"step-final"}
    assert main._final_answer_document_ids(metadata) == ["manual-1"]
    assert main._final_answer_evidence_pages(metadata) == [5]
    assert main._final_answer_section_ids(metadata) == ["section-final"]
    assert main._deterministic_document_ids(metadata) == ["manual-1"]
    assert main._text_evidence_pages(metadata) == [5]


def test_unbound_allowed_source_ids_do_not_authorize_non_image_scope() -> None:
    metadata = _claim_scope_metadata()
    metadata["authorized_claim_evidence_bindings"] = []

    assert main._final_answer_non_image_source_ids(metadata) == set()
    assert main._final_answer_document_ids(metadata) == []
    assert main._final_answer_evidence_pages(metadata) == []
    assert main._final_answer_section_ids(metadata) == []


def test_page_lookup_uses_only_the_final_claim_document_and_page() -> None:
    calls: list[tuple[str, int]] = []

    class FakeVectorService:
        def get_page_records(self, document_id, page, chunk_type=None, limit=20):
            calls.append((document_id, page))
            return []

    metadata = _claim_scope_metadata()
    main._collect_direct_evidence_page_images(
        metadata,
        vector_service=FakeVectorService(),
    )

    assert calls == [("manual-1", 5)]


def test_procedure_auto_images_require_a_passed_response_audit() -> None:
    failed_metadata = _claim_scope_metadata(audit_passed=False)
    passed_metadata = _claim_scope_metadata(audit_passed=True)

    assert main._response_needs_images("如何安装起动电机", failed_metadata) is False
    assert main._response_needs_images("如何安装起动电机", passed_metadata) is True


def test_single_target_keeps_all_distinct_images_bound_to_the_same_step() -> None:
    metadata = _claim_scope_metadata()
    metadata["query_understanding_selection_mode"] = "single_target"
    bindings = [{
        "target_id": "step-final",
        "target_type": "step",
        "relation": "layout_anchor",
        "confidence": 0.95,
    }]
    images = [
        _image(5, "starter-overview", step_ids=["step-final"]).model_copy(update={
            "binding_schema_version": 2,
            "bindings": bindings,
        }),
        _image(5, "starter-terminal-detail", step_ids=["step-final"]).model_copy(update={
            "binding_schema_version": 2,
            "bindings": bindings,
        }),
    ]

    selected = main._select_evidence_images_for_response(images, metadata)

    assert [image.source_chunk_id for image in selected] == [
        "image-starter-overview",
        "image-starter-terminal-detail",
    ]


def test_cross_page_strong_bindings_survive_page_narrowing() -> None:
    metadata = {
        "original_user_message": "如何安装摩托车发动机气缸头盖",
        "query_understanding_selection_mode": "evidence_pages",
        "_deterministic_answer_evidence_pages": [13, 14],
        "allowed_evidence_pages": [13, 14],
        "allowed_source_chunk_ids": ["step-seal-13", "step-install-14"],
        "allowed_document_ids": ["manual-1"],
        "_deterministic_answer_section_title": "4.5 气缸头盖",
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": [
                    {
                        "id": "step-seal-13",
                        "content": "在气缸头密封帽周围涂抹密封硅胶。",
                        "metadata": {
                            "document_id": "manual-1",
                            "page": 13,
                            "chunk_type": "text",
                            "chunk_label": "step",
                            "answer_role": "procedure_step",
                            "section_title": "4.5 气缸头盖",
                        },
                    },
                    {
                        "id": "step-install-14",
                        "content": "安装气缸头盖并对角均匀拧紧。",
                        "metadata": {
                            "document_id": "manual-1",
                            "page": 14,
                            "chunk_type": "text",
                            "chunk_label": "step",
                            "answer_role": "procedure_step",
                            "section_title": "4.5 气缸头盖",
                        },
                    },
                ],
            }],
        }],
    }
    metadata = _claim_bound_test_metadata(metadata)

    def bound_image(page: int, name: str, step_id: str, title: str) -> EvidenceImage:
        return _image(
            page,
            name,
            step_ids=[step_id],
            section_title="4.5 气缸头盖",
        ).model_copy(update={
            "image_title": title,
            "image_summary": title,
            "binding_schema_version": 2,
            "bindings": [{
                "target_id": step_id,
                "target_type": "step",
                "relation": "layout_anchor",
                "confidence": 0.95,
            }],
        })

    selected = main._select_evidence_images_for_response(
        [
            bound_image(13, "head-cover-seal", "step-seal-13", "气缸头盖密封处理安装示意图"),
            bound_image(14, "head-cover-install", "step-install-14", "气缸头盖安装示意图"),
            bound_image(14, "head-cover-tighten", "step-install-14", "气缸头盖对角拧紧示意图"),
            bound_image(14, "valve-clearance", "step-next-section", "拆下气缸头盖检查气门间隙"),
        ],
        metadata,
    )

    assert [image.source_chunk_id for image in selected] == [
        "image-head-cover-seal",
        "image-head-cover-install",
        "image-head-cover-tighten",
    ], metadata.get("image_selection_contract")


def test_identical_image_url_is_still_deduplicated() -> None:
    first = _image(5, "starter-overview", step_ids=["step-final"])
    duplicate = first.model_copy(update={"source_chunk_id": "image-summary-starter"})

    assert main._sort_unique_evidence_images([first, duplicate]) == [first]


def test_same_page_images_require_final_answer_step_binding() -> None:
    metadata = {
        "original_user_message": "如何检查气缸内壁？请返回对应图片。",
        "query_understanding_selection_mode": "evidence_pages",
        "_deterministic_answer_evidence_pages": [18],
        "allowed_source_chunk_ids": ["step-check-cylinder-wall"],
        "allowed_document_ids": ["manual-1"],
    }

    metadata = _claim_bound_test_metadata(metadata)
    selected = main._select_evidence_images_for_response(
        [
            _image(18, "cylinder-wall", step_ids=["step-check-cylinder-wall"]),
            _image(18, "piston-skirt", step_ids=["step-check-piston-skirt"]),
        ],
        metadata,
    )

    assert [item.image_url for item in selected] == ["/cylinder-wall.png"]


def test_legacy_same_page_step_is_rejected_even_by_final_answer_step() -> None:
    metadata = {
        "original_user_message": "如何安装起动电机",
        "query_understanding_selection_mode": "evidence_pages",
        "_deterministic_answer_evidence_pages": [5],
        "allowed_source_chunk_ids": ["step-install-starter"],
        "allowed_document_ids": ["manual-1"],
        "authorized_claim_evidence_bindings": [{
            "claim_id": "install",
            "evidence_ids": ["manual:manual-1:step-install-starter"],
        }],
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": [{
                    "id": "step-install-starter",
                    "content": "安装起动电机并按规定紧固。",
                    "metadata": {
                        "document_id": "manual-1",
                        "page": 5,
                        "chunk_type": "text",
                        "chunk_label": "step",
                        "answer_role": "procedure_step",
                    },
                }],
            }],
        }],
    }
    image = _image(
        5,
        "starter-install",
        step_ids=["step-install-starter"],
        binding_confidence=1.0,
    ).model_copy(update={
        "role": "same_page_step",
        "caption": "",
        "image_title": "",
        "image_summary": "",
    })

    metadata = _claim_bound_test_metadata(metadata)
    selected = main._select_evidence_images_for_response([image], metadata)

    assert selected == []
    assert metadata["image_selection_contract"]["rejected_images"] == [{
        "source_chunk_id": "image-starter-install",
        "page": "5",
        "role": "same_page_step",
        "normalized_role": "legacy_same_page_step",
        "reason": "legacy_image_binding",
    }]


def test_allowed_image_source_id_without_claim_binding_cannot_self_authorize() -> None:
    metadata = {
        "allowed_source_chunk_ids": ["image-1"],
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": [{
                    "id": "image-1",
                    "content": "",
                    "metadata": {
                        "document_id": "manual-1",
                        "chunk_type": "image",
                        "image_url": "/image-1.png",
                    },
                }],
            }],
        }],
    }

    assert hasattr(main, "_final_answer_direct_image_source_ids")
    assert main._final_answer_direct_image_source_ids(metadata) == set()


def test_claim_bound_image_source_id_can_be_direct_evidence() -> None:
    metadata = {
        "allowed_source_chunk_ids": ["image-1"],
        "authorized_claim_evidence_bindings": [{
            "claim_id": "visual",
            "evidence_ids": ["manual:manual-1:image-1"],
        }],
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": [{
                    "id": "image-1",
                    "content": "",
                    "metadata": {
                        "document_id": "manual-1",
                        "chunk_type": "image",
                        "image_url": "/image-1.png",
                    },
                }],
            }],
        }],
    }

    assert hasattr(main, "_final_answer_direct_image_source_ids")
    assert main._final_answer_direct_image_source_ids(metadata) == {"image-1"}


def test_same_page_unbound_image_is_not_used_when_answer_has_step_bindings() -> None:
    metadata = {
        "original_user_message": "如何检查气缸内壁？请返回对应图片。",
        "query_understanding_selection_mode": "evidence_pages",
        "_deterministic_answer_evidence_pages": [18],
        "allowed_source_chunk_ids": ["step-check-cylinder-wall"],
        "allowed_document_ids": ["manual-1"],
    }

    selected = main._select_evidence_images_for_response(
        [_image(18, "same-page-unbound", bound=False)],
        metadata,
    )

    assert selected == []


def test_same_page_image_can_be_authorized_by_final_text_binding() -> None:
    metadata = {
        "original_user_message": "请返回气缸内壁对应图片。",
        "query_understanding_selection_mode": "single_target",
        "_deterministic_answer_evidence_pages": [18],
        "allowed_source_chunk_ids": ["text-cylinder-wall"],
        "allowed_document_ids": ["manual-1"],
    }

    metadata = _claim_bound_test_metadata(metadata)
    selected = main._select_evidence_images_for_response(
        [
            _image(18, "cylinder-wall", text_ids=["text-cylinder-wall"]),
            _image(18, "piston-skirt", text_ids=["text-piston-skirt"]),
        ],
        metadata,
    )

    assert [item.image_url for item in selected] == ["/cylinder-wall.png"]


def test_procedure_scope_without_target_edge_authorizes_no_image() -> None:
    metadata = {
        "original_user_message": "请返回检查气缸内壁的步骤图片。",
        "query_understanding_selection_mode": "evidence_pages",
        "_deterministic_answer_evidence_pages": [18],
        "_deterministic_answer_procedure_scope_id": "scope-cylinder-wall",
        "allowed_document_ids": ["manual-1"],
    }

    metadata = _claim_bound_test_metadata(metadata)
    selected = main._select_evidence_images_for_response(
        [
            _image(
                18,
                "cylinder-wall",
                procedure_scope_ids=["scope-cylinder-wall"],
                binding_confidence=0.95,
            ).model_copy(update={"role": "positioned_step"}),
            _image(
                18,
                "piston-skirt",
                procedure_scope_ids=["scope-piston-skirt"],
                binding_confidence=0.95,
            ).model_copy(update={"role": "positioned_step"}),
        ],
        metadata,
    )

    assert selected == []


def test_page_fallback_binding_cannot_be_authorized_by_shared_procedure_scope() -> None:
    metadata = {
        "original_user_message": "请返回检查气缸内壁的步骤图片。",
        "query_understanding_selection_mode": "evidence_pages",
        "_deterministic_answer_evidence_pages": [18],
        "_deterministic_answer_procedure_scope_id": "scope-cylinder",
        "allowed_document_ids": ["manual-1"],
    }

    selected = main._select_evidence_images_for_response(
        [
            _image(
                18,
                "unpositioned-figure",
                procedure_scope_ids=["scope-cylinder"],
                context_role="page_lookup",
                binding_confidence=0.35,
            ).model_copy(update={"role": "page_fallback"}),
        ],
        metadata,
    )

    assert selected == []


def test_page_fallback_binding_cannot_be_authorized_by_shared_answer_step() -> None:
    metadata = {
        "original_user_message": "请返回气缸内壁对应图片。",
        "query_understanding_selection_mode": "evidence_pages",
        "_deterministic_answer_evidence_pages": [18],
        "allowed_source_chunk_ids": ["step-cylinder-wall"],
        "allowed_document_ids": ["manual-1"],
    }
    weak = _image(
        18,
        "unpositioned-figure",
        step_ids=["step-cylinder-wall"],
        context_role="page_lookup",
        binding_confidence=0.35,
    ).model_copy(update={"role": "page_fallback"})

    assert main._select_evidence_images_for_response([weak], metadata) == []


def test_image_trace_scope_cannot_authorize_the_same_candidate() -> None:
    metadata = {
        "original_user_message": "请返回气缸内壁对应图片。",
        "query_understanding_selection_mode": "evidence_pages",
        "_deterministic_answer_evidence_pages": [18],
        "allowed_document_ids": ["manual-1"],
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": [{
                    "content": "unrelated figure",
                    "metadata": {
                        "chunk_type": "image",
                        "procedure_scope_id": "scope-from-image",
                    },
                }],
            }],
        }],
    }
    candidate = _image(
        18,
        "unrelated-figure",
        procedure_scope_ids=["scope-from-image"],
        binding_confidence=0.95,
    ).model_copy(update={"role": "positioned_step"})

    selected = main._select_evidence_images_for_response([candidate], metadata)

    assert selected == []
    contract = metadata["image_selection_contract"]
    assert contract["target_procedure_scope_ids"] == []
    assert contract["selected_image_bindings"] == []


def test_fact_query_without_final_answer_ids_rejects_bound_same_page_images() -> None:
    metadata = {
        "original_user_message": "气缸内壁有什么检查要求？",
        "query_understanding_selection_mode": "evidence_pages",
        "_deterministic_answer_evidence_pages": [18],
        "allowed_document_ids": ["manual-1"],
    }

    selected = main._select_evidence_images_for_response(
        [
            _image(18, "气缸内壁检查图", step_ids=["step-cylinder-wall"]).model_copy(
                update={"image_title": "气缸内壁检查图"}
            ),
            _image(18, "活塞裙部检查图", step_ids=["step-piston-skirt"]).model_copy(
                update={"image_title": "活塞裙部检查图"}
            ),
        ],
        metadata,
    )

    assert selected == []


def test_visual_query_without_final_answer_ids_rejects_local_matching_image() -> None:
    metadata = {
        "original_user_message": "请显示气缸内壁的检查图片。",
        "query_understanding_selection_mode": "single_target",
        "_deterministic_answer_evidence_pages": [18],
        "allowed_document_ids": ["manual-1"],
    }

    selected = main._select_evidence_images_for_response(
        [
            _image(18, "气缸内壁检查图", step_ids=["step-cylinder-wall"]).model_copy(
                update={"image_title": "气缸内壁检查图"}
            ),
            _image(18, "活塞裙部检查图", step_ids=["step-piston-skirt"]).model_copy(
                update={"image_title": "活塞裙部检查图"}
            ),
        ],
        metadata,
    )

    assert selected == []
    assert metadata["image_selection_contract"]["selected_image_bindings"] == []


def test_single_target_uses_image_local_semantics_when_images_share_answer_step() -> None:
    metadata = {
        "original_user_message": "如何检查活塞裙部？请返回对应图片。",
        "query_understanding_selection_mode": "single_target",
        "_deterministic_answer_evidence_pages": [18],
        "allowed_source_chunk_ids": ["step-check-cylinder-and-piston"],
        "allowed_document_ids": ["manual-1"],
    }

    metadata = _claim_bound_test_metadata(metadata)
    selected = main._select_evidence_images_for_response(
        [
            _image(
                18,
                "cylinder-wall",
                step_ids=["step-check-cylinder-and-piston"],
            ).model_copy(update={"image_title": "气缸内壁结构示意图"}),
            _image(
                18,
                "piston-skirt",
                step_ids=["step-check-cylinder-and-piston"],
            ).model_copy(update={"image_title": "活塞及其裙部结构示意图"}),
        ],
        metadata,
    )

    assert [item.image_url for item in selected] == ["/piston-skirt.png"]


def test_selector_derives_single_target_mode_from_corresponding_image_query() -> None:
    metadata = {
        "original_user_message": "如何检查活塞裙部？请返回对应图片。",
        "_deterministic_answer_evidence_pages": [18],
        "allowed_source_chunk_ids": ["step-check-cylinder-and-piston"],
        "allowed_document_ids": ["manual-1"],
    }

    metadata = _claim_bound_test_metadata(metadata)
    selected = main._select_evidence_images_for_response(
        [
            _image(
                18,
                "cylinder-wall",
                step_ids=["step-check-cylinder-and-piston"],
            ).model_copy(update={"image_title": "气缸内壁结构示意图"}),
            _image(
                18,
                "piston-skirt",
                step_ids=["step-check-cylinder-and-piston"],
            ).model_copy(update={"image_title": "活塞及其裙部结构示意图"}),
        ],
        metadata,
    )

    assert [item.image_url for item in selected] == ["/piston-skirt.png"]
    assert metadata["image_selection_contract"]["mode"] == "single_target"


def test_original_single_target_query_overrides_trace_section_overview_mode() -> None:
    metadata = {
        "original_user_message": "如何检查活塞裙部？请返回对应图片。",
        "_deterministic_answer_evidence_pages": [18],
        "allowed_source_chunk_ids": ["step-check-cylinder-and-piston"],
        "allowed_document_ids": ["manual-1"],
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": [{
                    "content": "活塞裙部检查图",
                    "metadata": {
                        "query_understanding_selection_mode": "section_overview",
                    },
                }],
            }],
        }],
    }

    metadata = _claim_bound_test_metadata(metadata)
    selected = main._select_evidence_images_for_response(
        [
            _image(
                18,
                "cylinder-wall",
                step_ids=["step-check-cylinder-and-piston"],
            ).model_copy(update={"image_title": "气缸内壁结构示意图"}),
            _image(
                18,
                "piston-skirt",
                step_ids=["step-check-cylinder-and-piston"],
            ).model_copy(update={"image_title": "活塞及其裙部结构示意图"}),
        ],
        metadata,
    )

    assert [item.image_url for item in selected] == ["/piston-skirt.png"]
    assert metadata["image_selection_contract"]["mode"] == "single_target"


def test_unbound_original_retrieval_image_is_not_automatically_authorized() -> None:
    metadata = {
        "original_user_message": "如何检查气缸内壁？",
        "query_understanding_selection_mode": "evidence_pages",
        "_deterministic_answer_evidence_pages": [18],
        "allowed_document_ids": ["manual-1"],
    }

    assert main._select_evidence_images_for_response(
        [_image(18, "generic-page-figure", bound=False)],
        metadata,
    ) == []


def test_single_target_keeps_only_image_bound_to_final_step() -> None:
    metadata = {
        "original_user_message": "只要安装定位销这一步对应的图",
        "query_understanding_selection_mode": "single_target",
        "allowed_evidence_pages": [11],
        "allowed_source_chunk_ids": ["step-install-pin"],
        "allowed_document_ids": ["manual-1"],
    }

    metadata = _claim_bound_test_metadata(metadata)
    selected = main._select_evidence_images_for_response(
        [
            _image(11, "wrong-step", step_ids=["step-install-gasket"]),
            _image(11, "right-step", step_ids=["step-install-pin"]),
        ],
        metadata,
    )

    assert [item.image_url for item in selected] == ["/right-step.png"]
    assert selected[0].step_ids == ["step-install-pin"]


def test_evidence_pages_contract_keeps_all_final_answer_pages_in_order() -> None:
    metadata = {
        "original_user_message": "如何安装气缸与活塞？给我完整步骤的相关图片",
        "query_understanding_selection_mode": "evidence_pages",
        "allowed_evidence_pages": [19, 20, 21],
        "allowed_source_chunk_ids": ["step-19", "step-20", "step-21"],
        "allowed_document_ids": ["manual-1"],
    }

    metadata = _claim_bound_test_metadata(metadata)
    metadata = _claim_bound_test_metadata(metadata)
    selected = main._select_evidence_images_for_response(
        [
            _image(21, "p21", step_ids=["step-21"]),
            _image(19, "p19", step_ids=["step-19"]),
            _image(20, "p20", step_ids=["step-20"]),
        ],
        metadata,
    )

    assert [item.page for item in selected] == [19, 20, 21]
    assert metadata["image_selection_contract"]["mode"] == "evidence_pages"
    assert metadata["image_selection_contract"]["target_pages"] == [19, 20, 21]
    assert metadata["image_selection_contract"]["selected_pages"] == [19, 20, 21]


def test_complete_procedure_request_keeps_all_scoped_pages_when_one_action_scores_higher() -> None:
    metadata = {
        "original_user_message": "如何完整安装气缸与活塞？给我完整步骤的相关图片",
        "query_understanding_selection_mode": "evidence_pages",
        "_deterministic_answer_evidence_pages": [19, 20, 21],
        "allowed_evidence_pages": [19, 20, 21],
        "allowed_source_chunk_ids": ["step-19", "step-20", "step-21"],
        "allowed_document_ids": ["manual-1"],
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": [
                    {"content": "安装气缸。", "metadata": {"chunk_type": "step_raw", "page": 19}},
                    {"content": "安装活塞销。", "metadata": {"chunk_type": "step_raw", "page": 20}},
                    {"content": "安装活塞销挡圈。", "metadata": {"chunk_type": "step_raw", "page": 21}},
                ],
            }],
        }],
    }

    metadata = _claim_bound_test_metadata(metadata)
    metadata = _claim_bound_test_metadata(metadata)
    selected = main._select_evidence_images_for_response(
        [
            _image(19, "cylinder", step_ids=["step-19"]),
            _image(20, "piston-pin", step_ids=["step-20"]),
            _image(21, "circlip", step_ids=["step-21"]),
        ],
        metadata,
    )

    assert [item.page for item in selected] == [19, 20, 21]


def test_evidence_pages_step_binding_rejects_answer_page_without_image_binding_metadata() -> None:
    metadata = {
        "original_user_message": "如何安装气缸与活塞？",
        "query_understanding_selection_mode": "evidence_pages",
        "_deterministic_answer_evidence_pages": [19, 20, 21],
        "allowed_source_chunk_ids": ["step-19", "step-20"],
        "allowed_document_ids": ["manual-1"],
    }

    metadata = _claim_bound_test_metadata(metadata)
    selected = main._select_evidence_images_for_response(
        [
            _image(19, "p19", step_ids=["step-19"]),
            _image(20, "p20", step_ids=["step-20"]),
            _image(21, "p21"),
        ],
        metadata,
    )

    assert [item.page for item in selected] == [19, 20]
    assert metadata["image_selection_contract"]["selected_pages"] == [19, 20]


def test_evidence_pages_accepts_image_explicitly_selected_by_its_own_source_id() -> None:
    metadata = {
        "original_user_message": "如何安装气缸与活塞？",
        "query_understanding_selection_mode": "evidence_pages",
        "_deterministic_answer_evidence_pages": [19, 20, 21],
        "allowed_source_chunk_ids": ["step-19", "step-20", "image-p21"],
        "allowed_document_ids": ["manual-1"],
        "authorized_claim_evidence_bindings": [{
            "claim_id": "visual-step",
            "evidence_ids": ["manual:manual-1:image-p21"],
        }],
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": [{
                    "id": "image-p21",
                    "content": "",
                    "metadata": {
                        "document_id": "manual-1",
                        "chunk_type": "image",
                        "image_url": "/p21.png",
                        "page": 21,
                    },
                }],
            }],
        }],
    }

    metadata = _claim_bound_test_metadata(metadata)
    selected = main._select_evidence_images_for_response(
        [
            _image(19, "p19", step_ids=["step-19"]),
            _image(20, "p20", step_ids=["step-20"]),
            _image(21, "p21", step_ids=["next-section-step"]),
        ],
        metadata,
    )

    assert [item.page for item in selected] == [19, 20, 21]


def test_contract_prefers_final_answer_pages_over_broader_allowed_pages() -> None:
    metadata = {
        "original_user_message": "如何拆卸气门？",
        "query_understanding_selection_mode": "evidence_pages",
        "_deterministic_answer_evidence_pages": [16],
        "allowed_evidence_pages": [16, 17],
        "allowed_document_ids": ["manual-1"],
    }

    selected = main._select_evidence_images_for_response(
        [_image(16, "valve-removal"), _image(17, "valve-installation")],
        metadata,
    )

    assert selected == []
    assert metadata["image_selection_contract"]["target_pages"] == []


def test_evidence_pages_contract_narrows_adjacent_allowed_pages_to_query_anchor() -> None:
    metadata = {
        "original_user_message": "拆卸发动机时排放机油要拆哪两个放油螺栓？",
        "query_understanding_selection_mode": "evidence_pages",
        "allowed_evidence_pages": [6, 7],
        "allowed_document_ids": ["manual-1"],
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": [
                    {
                        "content": "排放机油，拆下发动机左曲轴箱和车架上的放油螺栓。",
                        "metadata": {"chunk_type": "step_raw", "page": 6},
                    },
                    {
                        "content": "排放冷却液，拆下水泵盖上的放水螺栓。",
                        "metadata": {"chunk_type": "step_raw", "page": 7},
                    },
                ],
            }],
        }],
    }

    selected = main._select_evidence_images_for_response(
        [_image(6, "drain-oil"), _image(7, "drain-coolant")],
        metadata,
    )

    assert selected == []
    assert metadata["image_selection_contract"]["target_pages"] == []
    assert metadata["image_selection_contract"]["selected_pages"] == []


def test_single_target_contract_scores_all_allowed_pages_before_choosing_one() -> None:
    metadata = {
        "original_user_message": "只看安装活塞销挡圈这一步的图片",
        "query_understanding_selection_mode": "single_target",
        "allowed_evidence_pages": [20, 21],
        "allowed_source_chunk_ids": ["step-circlip"],
        "allowed_document_ids": ["manual-1"],
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": [
                    {
                        "content": "活塞与气缸必须使用相同组别。",
                        "metadata": {"chunk_type": "text", "page": 20},
                    },
                    {
                        "content": "安装活塞销，然后安装活塞销挡圈。",
                        "metadata": {"chunk_type": "step_raw", "page": 21},
                    },
                ],
            }],
        }],
    }

    metadata = _claim_bound_test_metadata(metadata)
    selected = main._select_evidence_images_for_response(
        [
            _image(20, "piston-group", step_ids=["step-group"]),
            _image(21, "piston-pin-circlip", step_ids=["step-circlip"]),
        ],
        metadata,
    )

    assert [item.page for item in selected] == [21]


def test_single_target_scores_action_pages_instead_of_taking_first_target_page() -> None:
    metadata = {
        "original_user_message": "检查凸轮轴时要检查哪些部位？只返回检查对应图",
        "query_understanding_selection_mode": "single_target",
        "_deterministic_answer_evidence_pages": [11, 12],
        "allowed_evidence_pages": [12, 11],
        "allowed_source_chunk_ids": ["step-inspection"],
        "allowed_document_ids": ["manual-1"],
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": [
                    {
                        "content": "拆卸凸轮轴座盖，取下进气和排气凸轮轴。",
                        "metadata": {"chunk_type": "step_raw", "page": 11},
                    },
                    {
                        "content": "检查凸轮轴轴颈和凸轮基圆表面是否磨损、腐蚀或划伤。",
                        "metadata": {"chunk_type": "step_raw", "page": 12},
                    },
                ],
            }],
        }],
    }

    metadata = _claim_bound_test_metadata(metadata)
    selected = main._select_evidence_images_for_response(
        [
            _image(11, "camshaft-removal", step_ids=["step-removal"]),
            _image(12, "camshaft-inspection", step_ids=["step-inspection"]),
        ],
        metadata,
    )

    assert [item.page for item in selected] == [12]


def test_directional_query_narrows_multi_page_evidence_to_visual_target_page() -> None:
    metadata = {
        "original_user_message": "安装气门时气门弹簧间距较密的一端朝哪边？",
        "query_understanding_selection_mode": "evidence_pages",
        "_deterministic_answer_evidence_pages": [16, 17],
        "allowed_evidence_pages": [16, 17],
        "allowed_document_ids": ["manual-1"],
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": [
                    {
                        "content": "拆卸气门，压缩气门弹簧并依次取下零件。",
                        "metadata": {"chunk_type": "step_raw", "page": 16},
                    },
                    {
                        "content": "安装时，气门弹簧间距较密的一端必须朝下。",
                        "metadata": {"chunk_type": "step_raw", "page": 17},
                    },
                ],
            }],
        }],
    }

    selected = main._select_evidence_images_for_response(
        [_image(16, "valve-removal"), _image(17, "valve-spring-direction")],
        metadata,
    )

    assert selected == []


def test_structured_orientation_contract_forces_single_visual_target() -> None:
    metadata = {
        "original_user_message": "安装气缸与活塞时IN标记应该朝哪里？",
        "_deterministic_answer_evidence_pages": [19, 20, 21],
        "allowed_evidence_pages": [19, 20, 21],
        "allowed_document_ids": ["manual-1"],
        "route_plan": {
            "query_contract": {
                "component": "气缸",
                "action": "安装",
                "orientation": "IN标记朝向",
            }
        },
    }

    selected = main._select_evidence_images_for_response(
        [
            _image(19, "p19").model_copy(update={"image_title": "IN标记朝向图"}),
            _image(20, "p20").model_copy(update={"image_title": "活塞组别图"}),
            _image(21, "p21").model_copy(update={"image_title": "活塞销挡圈图"}),
        ],
        metadata,
    )

    assert selected == []
    assert metadata["image_selection_contract"]["mode"] == "single_target"


def test_complete_inventory_returns_only_exact_section_real_image() -> None:
    metadata = {
        "original_user_message": "帮我查一下气缸活塞装配部件清单",
        "query_understanding_selection_mode": "evidence_pages",
        "allowed_evidence_pages": [17],
        "allowed_document_ids": ["manual-1"],
        "allowed_source_chunk_ids": ["table-17"],
        "_deterministic_answer_section_title": "5.1 气缸活塞装配部件清单",
    }
    metadata = _claim_bound_test_metadata(metadata)

    selected = main._select_evidence_images_for_response(
        [
            _image(17, "valve", section_title="4.8 气门"),
            _image(17, "cylinder-piston", section_title="5.1 气缸活塞装配部件清单"),
            _image(
                17,
                "rendered-page",
                section_title="5.1 气缸活塞装配部件清单",
                context_role="page_render",
            ),
        ],
        metadata,
    )

    assert [image.source_chunk_id for image in selected] == ["image-cylinder-piston"]
    assert metadata["image_selection_contract"]["mode"] == "section_overview"
    assert metadata["image_selection_contract"]["selected_image_bindings"] == [
        {
            "source_chunk_id": "image-cylinder-piston",
            "page": 17,
            "reason": "exact_target_section_binding",
        }
    ]


def test_parameter_query_drops_rendered_page_fallback_without_visual_need() -> None:
    metadata = {
        "original_user_message": "安装水泵时水封动环和水泵组件扭力要求是什么？",
        "allowed_evidence_pages": [29],
        "allowed_document_ids": ["manual-1"],
    }

    selected = main._select_evidence_images_for_response(
        [_image(29, "rendered-water-pump", context_role="page_render")],
        metadata,
    )

    assert selected == []


def test_fact_query_does_not_fallback_to_adjacent_indexed_image_page() -> None:
    metadata = {
        "original_user_message": "安装左曲轴箱盖前导出线束橡胶周围要涂什么？",
        "_deterministic_answer_evidence_pages": [32],
        "allowed_evidence_pages": [32, 33],
        "allowed_document_ids": ["manual-1"],
        "_deterministic_answer_section_title": "7.4 左曲轴箱盖",
    }

    selected = main._select_evidence_images_for_response(
        [_image(33, "left-cover", section_title="7.4 左曲轴箱盖")],
        metadata,
    )

    assert selected == []


def test_fact_query_does_not_expand_final_page_from_cross_page_trace() -> None:
    metadata = {
        "original_user_message": "安装左曲轴箱盖前导出线束橡胶周围要涂什么？",
        "_deterministic_answer_evidence_pages": [32],
        "allowed_evidence_pages": [32, 33],
        "allowed_document_ids": ["manual-1"],
        "_deterministic_answer_section_title": "7.4 左曲轴箱盖",
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": [
                    {
                        "content": "装上左曲轴箱盖前，在导出线束橡胶周围涂抹密封硅胶。",
                        "metadata": {
                            "chunk_type": "text",
                            "page": 32,
                            "page_range": "32-33",
                            "section_title": "7.4 左曲轴箱盖",
                        },
                    },
                    {
                        "content": "7.4 左曲轴箱盖 第33页插图",
                        "metadata": {
                            "chunk_type": "image",
                            "page": 33,
                            "section_title": "7.4 左曲轴箱盖",
                        },
                    },
                ],
            }],
        }],
    }

    selected = main._select_evidence_images_for_response(
        [_image(33, "left-cover", section_title="7.4 左曲轴箱盖")],
        metadata,
    )

    assert selected == []


def test_visual_location_query_does_not_imply_rendered_page_fallback() -> None:
    metadata = {
        "original_user_message": "排放冷却液应该拆哪里、什么时候打开右水箱盖？",
        "_deterministic_answer_evidence_pages": [7],
        "allowed_evidence_pages": [6, 7, 8],
        "allowed_document_ids": ["manual-1"],
    }

    selected = main._select_evidence_images_for_response(
        [_image(7, "rendered-coolant-drain", context_role="page_render")],
        metadata,
    )

    assert selected == []


def test_explicit_page_view_query_keeps_rendered_page_fallback() -> None:
    metadata = {
        "original_user_message": "请显示手册第7页的整页截图。",
        "_deterministic_answer_evidence_pages": [7],
        "allowed_document_ids": ["manual-1"],
        "route_plan": {
            "action": "grounded_retrieval",
            "selected_document_id": "manual-1",
        },
    }

    selected = main._select_evidence_images_for_response(
        [_image(7, "rendered-page-7", context_role="page_render")],
        metadata,
    )

    assert [item.page for item in selected] == [7]


def test_explicit_page_view_uses_route_document_without_final_claims(monkeypatch) -> None:
    metadata = {
        "original_user_message": "请显示手册第 7 页整页截图",
        "execution_mode": "maintenance_ai_fallback_after_retrieval",
        "response_policy": {"images_allowed": False},
        "route_plan": {
            "action": "grounded_retrieval",
            "entity_role": "unspecified",
            "selected_document_id": "manual-1",
            "answer_source": "selected_document",
        },
    }
    rendered = _image(7, "rendered-page-7", context_role="page_render")

    selected = main._select_evidence_images_for_response([rendered], metadata)
    message, final_images = main._apply_final_image_contract(
        "正文检索没有覆盖该页。",
        selected,
        metadata,
    )

    assert message == "正文检索没有覆盖该页。"
    assert [item.source_chunk_id for item in final_images] == ["image-rendered-page-7"]
    assert metadata["image_selection_contract"]["target_document_ids"] == ["manual-1"]
    assert metadata["image_selection_contract"]["target_pages"] == [7]
    assert metadata["image_selection_contract"]["selected_image_bindings"] == [{
        "source_chunk_id": "image-rendered-page-7",
        "page": 7,
        "reason": "explicit_page_render",
    }]


def test_explicit_page_lookup_renders_one_page_without_final_claims(monkeypatch) -> None:
    metadata = {
        "original_user_message": "请显示手册第 7 页整页截图",
        "route_plan": {
            "action": "grounded_retrieval",
            "selected_document_id": "manual-1",
            "answer_source": "selected_document",
        },
    }
    rendered = _image(7, "rendered-page-7", context_role="page_render")
    calls: list[tuple[str, int]] = []

    def render_page(metadata_value, document_id, page):
        calls.append((document_id, page))
        return rendered

    class VectorService:
        def get_page_records(self, *args, **kwargs):
            raise AssertionError("explicit page rendering must not return indexed page images")

    monkeypatch.setattr(main, "_render_evidence_pdf_page_image", render_page)

    images = main._collect_direct_evidence_page_images(metadata, VectorService())

    assert calls == [("manual-1", 7)]
    assert images == [rendered]


def test_document_source_hints_use_route_document_manifest_without_trace(monkeypatch) -> None:
    metadata = {
        "original_user_message": "请显示手册第 7 页整页截图",
        "route_plan": {
            "action": "grounded_retrieval",
            "selected_document_id": "manual-1",
        },
    }

    class VectorService:
        def get_document_manifest(self, document_id):
            assert document_id == "manual-1"
            return {
                "file_name": "manual.pdf",
                "source_file_url": "http://files.example/manual.pdf",
            }

    monkeypatch.setattr(
        main,
        "_initialized_or_injected_vector_service",
        lambda: VectorService(),
    )

    assert main._document_source_hints(metadata) == {
        "manual-1": {
            "file_name": "manual.pdf",
            "source_file_url": "http://files.example/manual.pdf",
        }
    }


def test_resolved_document_component_route_keeps_scoped_images_when_text_is_unsupported() -> None:
    metadata = {
        "original_user_message": "拆卸气门时依次拆下哪些件？",
        "response_policy": {"images_allowed": False},
        "route_plan": {
            "action": "grounded_retrieval",
            "entity_role": "document_component",
            "selected_document_id": "manual-1",
        },
        "allowed_evidence_pages": [16, 17],
        "allowed_document_ids": ["manual-1"],
        "react_trace": [{
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "result_data": [
                    {
                        "content": "拆卸气门并依次取下气门弹簧等部件。",
                        "metadata": {"chunk_type": "step_raw", "page": 16},
                    },
                    {
                        "content": "安装气门弹簧，间距较密的一端朝下。",
                        "metadata": {"chunk_type": "step_raw", "page": 17},
                    },
                ],
            }],
        }],
    }

    selected = main._select_evidence_images_for_response(
        [_image(16, "valve-removal"), _image(17, "valve-installation")],
        metadata,
    )
    message, final_images = main._apply_final_image_contract("正文证据不足。", selected, metadata)

    assert message == "正文证据不足。"
    assert final_images == []
    assert metadata["route_scoped_visual_evidence_allowed"] is True


def test_route_scoped_visual_evidence_rejects_foreign_document_images() -> None:
    metadata = {
        "original_user_message": "拆卸气门时依次拆下哪些件？",
        "response_policy": {"images_allowed": False},
        "route_plan": {
            "action": "grounded_retrieval",
            "entity_role": "document_component",
            "selected_document_id": "manual-1",
        },
        "allowed_evidence_pages": [16],
    }
    foreign = _image(16, "foreign")
    foreign = foreign.model_copy(update={"document_id": "manual-2"})

    selected = main._select_evidence_images_for_response([foreign], metadata)

    assert selected == []
    assert metadata["route_scoped_visual_evidence_allowed"] is False


def test_insufficient_evidence_final_state_never_returns_images() -> None:
    metadata = {
        "original_user_message": "查询虚构总成的校准值",
        "route_plan": {
            "action": "insufficient_evidence",
            "entity_role": "document_component",
            "selected_document_id": "manual-1",
        },
        "blocked_for_insufficient_evidence": True,
        "allowed_document_ids": ["manual-1"],
        "allowed_evidence_pages": [9],
    }

    message, images = main._apply_final_image_contract(
        "当前资料不足，无法可靠确认。",
        [_image(9, "unsupported-figure")],
        metadata,
    )

    assert message == "当前资料不足，无法可靠确认。"
    assert images == []


def test_ai_fallback_after_retrieval_never_returns_manual_images() -> None:
    metadata = {
        "original_user_message": "远航飞行器涡轮装置出现周期性抖动是什么原因",
        "execution_mode": "maintenance_ai_fallback_after_retrieval",
        "route_plan": {
            "action": "grounded_retrieval",
            "entity_role": "document_component",
            "selected_document_id": "manual-1",
        },
        "allowed_document_ids": ["manual-1"],
    }

    message, images = main._apply_final_image_contract(
        "以下内容由 AI 基于通用知识生成，仅供参考。",
        [_image(38, "unrelated-manual-figure")],
        metadata,
    )

    assert message == "以下内容由 AI 基于通用知识生成，仅供参考。"
    assert images == []


def test_no_evidence_execution_state_never_returns_images() -> None:
    metadata = {
        "execution_mode": "generic_guidance",
        "evidence_status": "no_evidence",
        "insufficient_evidence_reason": "empty_retrieval",
    }

    message, images = main._apply_final_image_contract(
        "当前知识库没有找到足以回答该问题的可靠依据。",
        [_image(33, "unsupported-section-figure")],
        metadata,
    )

    assert message == "当前知识库没有找到足以回答该问题的可靠依据。"
    assert images == []


@pytest.mark.parametrize(
    "failed_stage",
    [
        "extract_trace_images",
        "section_image_lookup",
        "merge_section_images",
        "page_image_lookup",
        "merge_page_images",
        "image_evidence_gate",
        "final_image_contract",
    ],
)
def test_safe_image_pipeline_failures_keep_answer_and_return_no_images(
    monkeypatch,
    failed_stage: str,
) -> None:
    image = _image(5, "starter-install", step_ids=["step-install-starter"])

    def fail():
        raise RuntimeError("image stage unavailable")

    monkeypatch.setattr(main, "_apply_inherited_image_evidence", lambda *args: None)
    monkeypatch.setattr(main, "_extract_evidence_images", lambda metadata: [])

    async def empty_section(metadata):
        return []

    monkeypatch.setattr(main, "_collect_direct_section_images", empty_section)
    monkeypatch.setattr(main, "_collect_direct_evidence_page_images", lambda metadata: [])
    monkeypatch.setattr(main, "_merge_evidence_images", lambda left, right: [*left, *right])
    monkeypatch.setattr(main, "_select_evidence_images_for_response", lambda images, metadata: images)
    monkeypatch.setattr(main, "_apply_final_image_contract", lambda message, images, metadata: (message, images))

    if failed_stage == "extract_trace_images":
        monkeypatch.setattr(main, "_extract_evidence_images", lambda metadata: fail())
    elif failed_stage == "section_image_lookup":
        async def failed_section(metadata):
            fail()
        monkeypatch.setattr(main, "_collect_direct_section_images", failed_section)
    elif failed_stage == "merge_section_images":
        async def one_section(metadata):
            return [image]
        monkeypatch.setattr(main, "_collect_direct_section_images", one_section)
        monkeypatch.setattr(main, "_merge_evidence_images", lambda left, right: fail())
    elif failed_stage == "page_image_lookup":
        monkeypatch.setattr(main, "_collect_direct_evidence_page_images", lambda metadata: fail())
    elif failed_stage == "merge_page_images":
        calls = [0]
        def fail_second_merge(left, right):
            calls[0] += 1
            if calls[0] == 2:
                fail()
            return [*left, *right]
        monkeypatch.setattr(main, "_collect_direct_evidence_page_images", lambda metadata: [image])
        monkeypatch.setattr(main, "_merge_evidence_images", fail_second_merge)
    elif failed_stage == "image_evidence_gate":
        monkeypatch.setattr(main, "_select_evidence_images_for_response", lambda images, metadata: fail())
    elif failed_stage == "final_image_contract":
        monkeypatch.setattr(main, "_apply_final_image_contract", lambda message, images, metadata: fail())

    metadata = {}
    message, images = asyncio.run(main._safe_build_response_images(
        "按步骤安装起动电机，如图所示。",
        metadata,
        input_context={},
        session_id="image-stage-failure",
    ))

    assert message == "按步骤安装起动电机。"
    assert images == []
    assert metadata["image_selection_status"] == "failed"
    assert metadata["image_selection_failed_stage"] == failed_stage
    assert metadata["image_selection_error_type"] == "RuntimeError"
    assert metadata["image_selection_contract"]["selected_count"] == 0
