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


def test_diagnostic_fact_identity_is_stable_across_component_uuid_changes():
    source = "若起动电机轴不灵活，则更换起动电机。"

    first = kg._diagnostic_fact_identity(
        document_id="kdoc-1",
        component_identity="起动电机",
        chunk_uid="chunk-1",
        source_fact=source,
        action_text="更换起动电机",
    )
    second = kg._diagnostic_fact_identity(
        document_id="kdoc-1",
        component_identity="起动电机",
        chunk_uid="chunk-1",
        source_fact=source,
        action_text="更换起动电机",
    )

    assert first == second
    assert first[0].startswith("kgfault:kdoc-1:起动电机:chunk-1:")
    assert first[1].startswith(f"kgsolution:{first[0]}:")


def test_diagnostic_fact_identity_distinguishes_multiple_explicit_items():
    common = {
        "document_id": "kdoc-1",
        "component_identity": "轴承",
        "chunk_uid": "chunk-1",
        "source_fact": "检查轴承：卡滞或磨损，需更换缺陷轴承。",
        "action_text": "更换缺陷轴承",
    }

    jammed = kg._diagnostic_fact_identity(
        **common,
        item_disambiguator="轴承|轴承卡滞",
    )
    worn = kg._diagnostic_fact_identity(
        **common,
        item_disambiguator="轴承|轴承磨损",
    )
    jammed_again = kg._diagnostic_fact_identity(
        **common,
        item_disambiguator="轴承|轴承卡滞",
    )

    assert jammed != worn
    assert jammed == jammed_again


@pytest.mark.parametrize(
    ("fault_name", "source_subject", "expected"),
    [
        ("磨损", "凸轮轴", "凸轮轴磨损"),
        ("磨损", "轴承", "轴承磨损"),
        ("轴不灵活", "起动电机轴", "起动电机轴不灵活"),
        ("气缸内壁损伤", "气缸内壁与活塞裙部", "气缸内壁损伤"),
        ("磨损", "各部件", "磨损"),
    ],
)
def test_canonical_fault_name_uses_only_explicit_source_subject(
    fault_name,
    source_subject,
    expected,
):
    assert kg._canonical_fault_name(fault_name, source_subject) == expected


def test_split_diagnostic_facts_assigns_distinct_stable_keys_to_multiple_facts():
    text = (
        "若起动电机轴不灵活，则更换起动电机。\n"
        "如机油泵从动齿轮不能自由转动，应更换机油泵。"
    )

    facts = kg._split_diagnostic_facts(text)
    identities = {
        kg._diagnostic_fact_identity(
            document_id="kdoc-1",
            component_identity="轴承",
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


def test_split_diagnostic_facts_preserves_heading_without_colon():
    facts = kg._split_diagnostic_facts(
        "（5）检查轴承\n"
        "卡滞或磨损 → 更换缺陷轴承"
    )

    assert facts == ["检查轴承：卡滞或磨损 → 更换缺陷轴承"]
    assert kg._extract_source_subject(facts[0]) == "轴承"


def test_split_diagnostic_facts_does_not_emit_abnormal_word_in_heading_as_fact():
    facts = kg._split_diagnostic_facts(
        "检查离合器各部件磨损情况：\n"
        "磨损严重者需更换缺陷部件；"
    )

    assert facts == [
        "检查离合器各部件磨损情况：磨损严重者需更换缺陷部件；"
    ]


def test_split_diagnostic_facts_reuses_heading_for_each_following_condition():
    facts = kg._split_diagnostic_facts(
        "检查轴承：\n"
        "若卡滞则更换。\n"
        "若磨损则更换。"
    )

    assert facts == [
        "检查轴承：若卡滞则更换。",
        "检查轴承：若磨损则更换。",
    ]


def test_fault_extraction_prompt_requires_exhaustive_explicit_alternatives():
    prompt = kg._FAULT_SOLUTION_SYSTEM

    assert "每个明确对象与异常状态组合分别输出一个条目" in prompt
    assert "卡滞或磨损" in prompt
    assert "不能只保留其中一个" in prompt


def test_fault_extraction_is_deterministic():
    calls = []
    extractor = object.__new__(kg.ManualKGExtractor)
    extractor.settings = SimpleNamespace(intent_router_model="test-model")

    async def chat(**kwargs):
        calls.append(kwargs)
        return {"content": '{"items": []}'}

    extractor.llm = SimpleNamespace(chat=chat)

    result = asyncio.run(extractor._extract_fault_solutions(
        "检查轴承：若磨损则更换。",
        device_name="摩托车发动机",
        component_name="传动装置",
        chunk_uid="chunk-1",
    ))

    assert result == []
    assert calls[0]["temperature"] == 0


def test_subjectless_condition_does_not_inherit_section_component():
    assert kg._extract_source_subject("若变形或开裂，则更换。") == ""


def test_explicit_subcomponent_can_anchor_to_its_section_assembly():
    candidates = kg._resolve_component_anchor_candidates(
        source_fact="检查拨叉：若损坏则更换拨叉。",
        requested_name="拨叉",
        section_component_name="传动装置",
        current_component_id="transmission-component",
        component_ids_by_name={"传动装置": ["transmission-component"]},
    )

    assert candidates == ["transmission-component"]


def test_exact_component_anchor_wins_over_section_assembly():
    candidates = kg._resolve_component_anchor_candidates(
        source_fact="检查拨叉：若损坏则更换拨叉。",
        requested_name="拨叉",
        section_component_name="传动装置",
        current_component_id="transmission-component",
        component_ids_by_name={
            "拨叉": ["fork-component"],
            "传动装置": ["transmission-component"],
        },
    )

    assert candidates == ["fork-component"]


def test_subjectless_condition_cannot_anchor_to_section_assembly():
    candidates = kg._resolve_component_anchor_candidates(
        source_fact="若损坏则更换。",
        requested_name="",
        section_component_name="传动装置",
        current_component_id="transmission-component",
        component_ids_by_name={"传动装置": ["transmission-component"]},
    )

    assert candidates == []


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
