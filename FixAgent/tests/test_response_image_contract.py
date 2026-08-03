from api import main
from schemas.response import EvidenceImage


def _image(
    page: int,
    name: str,
    *,
    step_ids: list[str] | None = None,
    section_title: str = "",
    context_role: str = "",
) -> EvidenceImage:
    ids = step_ids or []
    return EvidenceImage(
        image_url=f"/{name}.png",
        page=page,
        document_id="manual-1",
        source_chunk_id=f"image-{name}",
        step_id=ids[0] if ids else "",
        step_ids=ids,
        caption=name,
        section_title=section_title,
        context_role=context_role,
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


def test_single_target_keeps_only_image_bound_to_final_step() -> None:
    metadata = {
        "original_user_message": "只要安装定位销这一步对应的图",
        "query_understanding_selection_mode": "single_target",
        "allowed_evidence_pages": [11],
        "allowed_source_chunk_ids": ["step-install-pin"],
        "allowed_document_ids": ["manual-1"],
    }

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
        "allowed_document_ids": ["manual-1"],
    }

    selected = main._select_evidence_images_for_response(
        [_image(21, "p21"), _image(19, "p19"), _image(20, "p20")],
        metadata,
    )

    assert [item.page for item in selected] == [19, 20, 21]
    assert metadata["image_selection_contract"]["mode"] == "evidence_pages"
    assert metadata["image_selection_contract"]["target_pages"] == [19, 20, 21]
    assert metadata["image_selection_contract"]["selected_pages"] == [19, 20, 21]


def test_evidence_pages_step_binding_does_not_drop_an_answer_page_without_binding_metadata() -> None:
    metadata = {
        "original_user_message": "如何安装气缸与活塞？",
        "query_understanding_selection_mode": "evidence_pages",
        "_deterministic_answer_evidence_pages": [19, 20, 21],
        "allowed_source_chunk_ids": ["step-19", "step-20"],
        "allowed_document_ids": ["manual-1"],
    }

    selected = main._select_evidence_images_for_response(
        [
            _image(19, "p19", step_ids=["step-19"]),
            _image(20, "p20", step_ids=["step-20"]),
            _image(21, "p21"),
        ],
        metadata,
    )

    assert [item.page for item in selected] == [19, 20, 21]
    assert metadata["image_selection_contract"]["selected_pages"] == [19, 20, 21]


def test_evidence_pages_accepts_image_explicitly_selected_by_its_own_source_id() -> None:
    metadata = {
        "original_user_message": "如何安装气缸与活塞？",
        "query_understanding_selection_mode": "evidence_pages",
        "_deterministic_answer_evidence_pages": [19, 20, 21],
        "allowed_source_chunk_ids": ["step-19", "step-20", "image-p21"],
        "allowed_document_ids": ["manual-1"],
    }

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

    assert [item.page for item in selected] == [16]
    assert metadata["image_selection_contract"]["target_pages"] == [16]


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

    assert [item.page for item in selected] == [6]
    assert metadata["image_selection_contract"]["target_pages"] == [6, 7]
    assert metadata["image_selection_contract"]["selected_pages"] == [6]


def test_single_target_contract_scores_all_allowed_pages_before_choosing_one() -> None:
    metadata = {
        "original_user_message": "只看安装活塞销挡圈这一步的图片",
        "query_understanding_selection_mode": "single_target",
        "allowed_evidence_pages": [20, 21],
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

    selected = main._select_evidence_images_for_response(
        [_image(20, "piston-group"), _image(21, "piston-pin-circlip")],
        metadata,
    )

    assert [item.page for item in selected] == [21]


def test_single_target_scores_action_pages_instead_of_taking_first_target_page() -> None:
    metadata = {
        "original_user_message": "检查凸轮轴时要检查哪些部位？只返回检查对应图",
        "query_understanding_selection_mode": "single_target",
        "_deterministic_answer_evidence_pages": [11, 12],
        "allowed_evidence_pages": [12, 11],
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

    selected = main._select_evidence_images_for_response(
        [_image(11, "camshaft-removal"), _image(12, "camshaft-inspection")],
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

    assert [item.page for item in selected] == [17]


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
        [_image(19, "p19"), _image(20, "p20"), _image(21, "p21")],
        metadata,
    )

    assert [item.page for item in selected] == [19]
    assert metadata["image_selection_contract"]["mode"] == "single_target"


def test_contract_drops_same_page_image_from_neighbor_section() -> None:
    metadata = {
        "original_user_message": "帮我查一下气缸活塞装配部件清单",
        "query_understanding_selection_mode": "evidence_pages",
        "allowed_evidence_pages": [17],
        "allowed_document_ids": ["manual-1"],
        "_deterministic_answer_section_title": "5.1 气缸活塞装配部件清单",
    }

    selected = main._select_evidence_images_for_response(
        [
            _image(17, "valve", section_title="4.8 气门"),
            _image(17, "cylinder-piston", section_title="5.1 气缸活塞装配部件清单"),
        ],
        metadata,
    )

    assert [item.image_url for item in selected] == ["/cylinder-piston.png"]


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


def test_visual_location_query_keeps_rendered_page_fallback() -> None:
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

    assert [item.page for item in selected] == [7]


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
    assert [item.page for item in final_images] == [16]
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
