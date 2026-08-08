import asyncio
from types import SimpleNamespace

import pytest

from services.knowledge.diagnostic_chunk_selector import classify_diagnostic_chunk
from services.knowledge import manual_kg_extractor as kg


def test_unlabelled_fault_content_is_selected_but_parameter_content_is_not():
    selected = classify_diagnostic_chunk(
        {
            "metadata": {"chunk_label": "text", "chunk_uid": "c-1"},
            "text": "发动机出现异常振动，可能原因为轴承磨损，处理时检查张紧轮。",
        }
    )
    rejected = classify_diagnostic_chunk(
        {
            "metadata": {"chunk_label": "text", "chunk_uid": "c-2"},
            "text": "螺栓拧紧力矩为 35 N.m。",
        }
    )

    assert selected.selected is True
    assert selected.reason == "diagnostic_signal_pair"
    assert rejected.selected is False
    assert rejected.reason == "no_diagnostic_signal_pair"


@pytest.mark.parametrize("label", ["troubleshooting", "fault_diagnosis", "error_code"])
def test_explicit_diagnostic_labels_are_selected(label):
    result = classify_diagnostic_chunk(
        {"metadata": {"chunk_label": label, "chunk_uid": "c-explicit"}, "text": "E101"}
    )
    assert result.selected is True
    assert result.reason == "explicit_diagnostic_label"


def test_diagnostic_full_table_is_selected_but_parameter_table_is_not():
    diagnostic = classify_diagnostic_chunk({
        "metadata": {"chunk_label": "table", "chunk_type": "table_full", "chunk_uid": "table-1"},
        "text": "Er-08 | 车外温度传感器故障 | 请检查传感器回路短路或开路，排除故障后重新启动。",
    })
    parameter = classify_diagnostic_chunk({
        "metadata": {"chunk_label": "table", "chunk_type": "table_full", "chunk_uid": "table-2"},
        "text": "额定电压 | 540VDC\n额定功率 | 3kW",
    })
    assert diagnostic.selected is True
    assert diagnostic.reason == "diagnostic_signal_pair"
    assert parameter.selected is False


@pytest.mark.parametrize(
    ("label", "text"),
    [
        (
            "step",
            "检查起动电机轴是否转动灵活。若不灵活，则更换起动电机。",
        ),
        (
            "safety",
            "将拨叉轴放在平坦表面滚动；如弯曲则更换拨叉轴，不要尝试校直。",
        ),
    ],
)
def test_condition_action_diagnostics_inside_procedure_chunks_are_selected(label, text):
    result = classify_diagnostic_chunk(
        {"metadata": {"chunk_label": label, "chunk_uid": "c-condition"}, "text": text}
    )

    assert result.selected is True
    assert result.reason == "conditional_diagnostic_signal_pair"


def test_non_fault_conditions_inside_steps_remain_excluded():
    result = classify_diagnostic_chunk(
        {
            "metadata": {"chunk_label": "step", "chunk_uid": "c-oil"},
            "text": "加入1600 mL机油；若已更换机油精滤芯，则加入1700 mL机油。",
        }
    )

    assert result.selected is False
    assert result.reason == "excluded_chunk_label"


@pytest.mark.parametrize(
    ("label", "text", "reason"),
    [
        ("step", "1. 拆下护罩。2. 松开螺栓。", "excluded_chunk_label"),
        ("toc", "第一章 概述 第二章 维修", "excluded_chunk_label"),
        ("image", "图 3-2 发动机结构", "excluded_chunk_label"),
        ("table_row", "故障出现后检查线路。", "excluded_chunk_label"),
        ("text", "出现异常振动和噪声。", "no_diagnostic_signal_pair"),
        ("text", "检查皮带并更换张紧轮。", "no_diagnostic_signal_pair"),
    ],
)
def test_non_diagnostic_content_is_rejected_with_stable_reason(label, text, reason):
    result = classify_diagnostic_chunk(
        {"metadata": {"chunk_label": label, "chunk_uid": "c-rejected"}, "text": text}
    )
    assert result.selected is False
    assert result.reason == reason


def test_unlabelled_diagnostic_chunk_is_projected_with_complete_provenance(monkeypatch):
    calls = []
    manifest_updates = []
    source_text = "发动机出现异常振动，可能原因为轴承磨损，处理时检查张紧轮。"
    extractor = object.__new__(kg.ManualKGExtractor)
    extractor.vector_svc = SimpleNamespace(
        list_document_chunks=lambda _document_id: [
            {
                "metadata": {
                    "section_title": "张紧轮",
                    "section_id": "6.2",
                    "chunk_label": "text",
                    "chunk_uid": "chunk-23",
                    "page_number": 23,
                    "raw_text": source_text,
                },
                "text": source_text,
            }
        ],
        get_document_manifest=lambda _document_id: {"document_version": "batch-7"},
        put_document_manifest=lambda document_id, manifest: manifest_updates.append(
            (document_id, dict(manifest))
        ) or True,
    )
    extractor.settings = SimpleNamespace(intent_router_model="test-model")
    extractor.llm = SimpleNamespace()
    extractor._base_url = "http://java.test"
    extractor._token = "token"

    async def identify_device(*_args, **_kwargs):
        return kg.ExtractedDevice(name="一号发动机")

    async def extract_component(*_args, **_kwargs):
        return kg.ExtractedComponent(name="张紧轮")

    async def extract_fault_solutions(*_args, **_kwargs):
        return [
            kg.ExtractedFaultSolution(
                fault_name="异常振动",
                fault_description="轴承断裂导致异常振动，这是手册未说明的推断。",
                solution_title="检查张紧轮",
                solution_description="拆下张紧轮后更换轴承，这是手册未给出的步骤。",
                solution_steps=["手册未给出的拆卸步骤"],
                confidence=0.91,
                source_chunk_uid="chunk-23",
                component_name="张紧轮",
            )
        ]

    async def call_java(path, body):
        calls.append((path, body))
        if path.endswith("upsert-device"):
            return {"deviceId": "device-1"}
        if path.endswith("upsert-component"):
            return {"componentId": "component-1"}
        return {"faultId": "fault-1", "solutionId": "solution-1", "embeddingStatus": "ok"}

    extractor._identify_device = identify_device
    extractor._extract_component = extract_component
    extractor._extract_fault_solutions = extract_fault_solutions
    extractor._call_java = call_java
    monkeypatch.setattr(
        kg,
        "assess_section_structure",
        lambda _chunks: {"ok": True, "reason": "", "stats": {}},
    )

    result = asyncio.run(extractor.extract_document("manual-1", device_type_hint="测试设备"))

    _, body = next(item for item in calls if item[0].endswith("upsert-fault-solution"))
    assert body["componentId"] == "component-1"
    assert body["documentId"] == "manual-1"
    assert body["documentVersion"] == "batch-7"
    assert body["sectionId"] == "6.2"
    assert body["sourceChunkUid"] == "chunk-23"
    assert body["sourceChunkUids"] == ["chunk-23"]
    assert body["pageStart"] == 23
    assert body["pageEnd"] == 23
    assert body["faultDescription"] == source_text
    assert body["solutionDescription"] == source_text
    assert body["solutionSteps"] == []
    assert result.diagnostic_chunks_scanned == 1
    assert result.diagnostic_chunks_selected == 1
    assert result.fault_items_extracted == 1
    assert result.fault_items_unanchored == 0
    assert result.fault_upserts_succeeded == 1
    assert result.fault_upserts_failed == 0
    assert result.unique_fault_paths == 1
    assert manifest_updates[-1][0] == "manual-1"
    assert manifest_updates[-1][1]["kg_status"] == "ready"
    assert manifest_updates[-1][1]["kg_fault_paths"] == 1
    assert manifest_updates[-1][1]["kg_fault_upserts"] == 1
    assert manifest_updates[-1][1]["kg_error_count"] == 0


def test_reextract_all_forwards_manifest_manual_identity():
    extractor = object.__new__(kg.ManualKGExtractor)
    extractor.vector_svc = SimpleNamespace(
        list_all_manifests=lambda: [
            {
                "status": "ready",
                "record_type": "manual",
                "document_id": "kdoc-1",
                "device_type": "测试设备",
                "manual_id": 2084935338534219778,
                "manual_name": "测试设备维修手册",
            }
        ]
    )
    calls = []

    async def extract_document(document_id, **kwargs):
        calls.append((document_id, kwargs))
        return kg.ExtractionResult(document_id=document_id)

    extractor.extract_document = extract_document

    result = asyncio.run(extractor.reextract_all())

    assert result["total_documents"] == 1
    assert calls == [
        (
            "kdoc-1",
            {
                "device_type_hint": "测试设备",
                "manual_id": 2084935338534219778,
                "manual_name": "测试设备维修手册",
            },
        )
    ]


def test_diagnostic_fact_identity_is_stable_when_llm_names_change():
    source = "若起动电机轴不灵活，则更换起动电机。"

    first = kg._diagnostic_fact_identity(
        document_id="kdoc-1",
        component_id="component-1",
        chunk_uid="chunk-1",
        source_fact=source,
        action_text="更换起动电机",
    )
    second = kg._diagnostic_fact_identity(
        document_id="kdoc-1",
        component_id="component-1",
        chunk_uid="chunk-1",
        source_fact=source,
        action_text="更换起动电机",
    )

    assert first == second
    assert first[0].startswith("kgfault:kdoc-1:component-1:chunk-1:")
    assert first[1].startswith(f"kgsolution:{first[0]}:")


def test_split_diagnostic_facts_assigns_distinct_stable_keys_to_multiple_facts():
    text = (
        "若起动电机轴不灵活，则更换起动电机。\n"
        "如机油泵从动齿轮不能自由转动，应更换机油泵。"
    )

    facts = kg._split_diagnostic_facts(text)
    identities = {
        kg._diagnostic_fact_identity(
            document_id="kdoc-1",
            component_id="component-1",
            chunk_uid="chunk-1",
            source_fact=fact,
            action_text=fact,
        )[0]
        for fact in facts
    }

    assert facts == [
        "若起动电机轴不灵活，则更换起动电机。",
        "如机油泵从动齿轮不能自由转动，应更换机油泵。",
    ]
    assert len(identities) == 2


def test_split_diagnostic_facts_preserves_subject_heading_for_following_condition():
    facts = kg._split_diagnostic_facts("检查油泵座垫：\n若变形或开裂，则更换。")

    assert facts == ["检查油泵座垫：若变形或开裂，则更换。"]
    assert kg._extract_source_subject(facts[0]) == "油泵座垫"


def test_subjectless_condition_does_not_inherit_section_component():
    assert kg._extract_source_subject("若变形或开裂，则更换。") == ""


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (kg.ExtractionResult(document_id="d", skipped=True), "skipped"),
        (kg.ExtractionResult(document_id="d", errors=["boom"]), "failed"),
        (
            kg.ExtractionResult(
                document_id="d", fault_upserts_succeeded=1, errors=["one failed"]
            ),
            "partial",
        ),
        (
            kg.ExtractionResult(
                document_id="d", device_id="device-1", fault_upserts_succeeded=2
            ),
            "ready",
        ),
    ],
)
def test_kg_projection_status_covers_terminal_outcomes(result, expected):
    assert kg._kg_projection_status(result) == expected
