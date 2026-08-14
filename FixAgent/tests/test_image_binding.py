from __future__ import annotations

import pytest


def _binding_api():
    try:
        from services.knowledge.image_binding import (
            IMAGE_BINDING_SCHEMA_VERSION,
            build_layout_image_bindings,
        )
    except ModuleNotFoundError:
        pytest.fail("image binding v2 is not implemented")
    return IMAGE_BINDING_SCHEMA_VERSION, build_layout_image_bindings


def _step(step_id: str, bbox: list[float], scope_id: str) -> dict:
    return {
        "id": step_id,
        "page": 12,
        "chunk_type": "text",
        "chunk_label": "step",
        "metadata": {
            "bbox": bbox,
            "procedure_scope_id": scope_id,
        },
    }


def _image(name: str, bbox: list[float] | None) -> dict:
    return {
        "image_name": name,
        "page": 12,
        "bbox": bbox,
    }


def test_one_step_can_bind_multiple_distinct_images() -> None:
    version, build_bindings = _binding_api()
    bundles = build_bindings(
        [_step("step-install", [40, 100, 500, 150], "scope-install")],
        [
            _image("overview.png", [40, 170, 250, 330]),
            _image("detail.png", [270, 170, 500, 330]),
        ],
    )

    assert version == 2
    assert [bundle["related_step_chunk_ids"] for bundle in bundles] == [
        ["step-install"],
        ["step-install"],
    ]
    assert all(bundle["image_binding_schema_version"] == 2 for bundle in bundles)


def test_one_image_can_bind_multiple_steps_in_the_same_layout_scope() -> None:
    _, build_bindings = _binding_api()
    bundles = build_bindings(
        [
            _step("step-align", [40, 520, 500, 560], "scope-install"),
            _step("step-order", [40, 600, 500, 640], "scope-install"),
            _step("step-distant", [40, 760, 500, 800], "scope-install"),
        ],
        [_image("camshaft-install.png", [40, 180, 500, 490])],
    )

    assert bundles[0]["related_step_chunk_ids"] == ["step-align", "step-order"]
    assert {
        binding["target_id"] for binding in bundles[0]["image_bindings"]
    } == {"step-align", "step-order"}


def test_embedded_image_does_not_expand_to_every_step_in_scope() -> None:
    _, build_bindings = _binding_api()
    bundles = build_bindings(
        [
            _step("step-before", [40, 80, 500, 130], "scope-install"),
            _step("step-with-figure", [40, 150, 500, 460], "scope-install"),
            _step("step-after", [40, 520, 500, 570], "scope-install"),
        ],
        [_image("local-detail.png", [120, 190, 480, 420])],
    )

    assert bundles[0]["related_step_chunk_ids"] == ["step-with-figure"]
    assert bundles[0]["image_bindings"] == [{
        "target_id": "step-with-figure",
        "target_type": "step",
        "relation": "layout_anchor",
        "confidence": 0.95,
    }]


def test_trailing_figure_does_not_bind_the_next_procedure_step() -> None:
    _, build_bindings = _binding_api()
    bundles = build_bindings(
        [
            _step("step-illustrated", [40, 100, 500, 150], "scope-remove"),
            _step("step-next", [40, 510, 500, 550], "scope-remove"),
        ],
        [_image("trailing-figure.png", [40, 153, 500, 501])],
    )

    assert bundles[0]["related_step_chunk_ids"] == ["step-illustrated"]


def test_same_page_different_procedure_scopes_do_not_cross_bind() -> None:
    _, build_bindings = _binding_api()
    bundles = build_bindings(
        [
            _step("step-remove", [40, 80, 500, 120], "scope-remove"),
            _step("step-install", [40, 500, 500, 540], "scope-install"),
        ],
        [
            _image("remove.png", [40, 140, 500, 300]),
            _image("install.png", [40, 560, 500, 720]),
        ],
    )

    assert bundles[0]["related_step_chunk_ids"] == ["step-remove"]
    assert bundles[1]["related_step_chunk_ids"] == ["step-install"]
    assert bundles[0]["procedure_scope_ids"] == ["scope-remove"]
    assert bundles[1]["procedure_scope_ids"] == ["scope-install"]


def test_image_without_coordinates_never_receives_strong_bindings() -> None:
    _, build_bindings = _binding_api()
    bundles = build_bindings(
        [_step("step-install", [40, 100, 500, 150], "scope-install")],
        [_image("unpositioned.png", None)],
    )

    assert bundles == [{
        "image_binding_schema_version": 2,
        "image_bindings": [],
        "related_step_chunk_ids": [],
        "related_text_chunk_ids": [],
        "procedure_scope_ids": [],
        "binding_role": "page_fallback",
        "binding_confidence": 0.0,
    }]
