from services.retrieval.evidence import EvidenceLedger
from services.retrieval.evidence_fusion import fuse_evidence_support


def _graph_entry(*, solution: bool = False) -> dict:
    suffix = "solution-1" if solution else "none"
    return {
        "evidence_id": f"graph:kgpath:device-1:component-1:fault-1:{suffix}",
        "source_type": "graph",
        "qualification": "qualified",
        "text": "一号发动机 -> OWNS -> 张紧轮 -> CAUSES -> 轴承磨损",
        "path_id": "kgpath:device-1:component-1:fault-1",
        "relationship_types": ["OWNS", "CAUSES"] + (["HAS_SOLUTION"] if solution else []),
        "device": {"id": "device-1", "name": "一号发动机"},
        "component": {"id": "component-1", "name": "张紧轮"},
        "fault": {"id": "fault-1", "name": "轴承磨损"},
        "solution": (
            {"id": "solution-1", "title": "更换张紧轮轴承", "verified": True, "status": "active"}
            if solution else {}
        ),
        "claim_types": (["verified_solution"] if solution else [
            "device_identity", "component_ownership", "fault_relation"
        ]),
        "supports_aspect_ids": (["treatment"] if solution else [
            "device", "component", "fault-cause"
        ]),
        "source": {"document_id": "manual-1", "section_id": "sec-1"},
    }


def _manual_entry() -> dict:
    return {
        "evidence_id": "manual:manual-1:chunk-1",
        "source_type": "manual",
        "qualification": "qualified",
        "text": "用听诊器检查张紧轮轴承，确认异响位置。",
        "source": {"document_id": "manual-1", "chunk_id": "chunk-1", "page": 12},
    }


def _bundle(aspect_id: str, aspect_text: str, *, manual_supported: bool = False) -> dict:
    return {
        "aspect_support": [{
            "aspect_id": aspect_id,
            "aspect_text": aspect_text,
            "supported": manual_supported,
            "evidence_ids": ["chunk-1"] if manual_supported else [],
        }],
        "missing_aspect_ids": [] if manual_supported else [aspect_id],
        "conflict_eligible": [],
        "capabilities": {},
    }


def test_graph_evidence_supports_possible_fault_with_stable_evidence_id() -> None:
    fused = fuse_evidence_support(
        "张紧轮异响是什么原因",
        _bundle("fault-cause", "张紧轮异响原因"),
        EvidenceLedger([_graph_entry()]),
    )

    row = fused["aspect_support"][0]
    assert row["supported"] is True
    assert row["evidence_ids"] == ["graph:kgpath:device-1:component-1:fault-1:none"]
    assert row["supporting_source_types"] == ["graph"]
    assert fused["missing_aspect_ids"] == []


def test_graph_evidence_cannot_support_parameter_or_inspection_aspects() -> None:
    ledger = EvidenceLedger([_graph_entry(), _graph_entry(solution=True)])

    parameter = fuse_evidence_support(
        "张紧轮间隙是多少",
        _bundle("gap", "张紧轮间隙参数"),
        ledger,
    )
    inspection = fuse_evidence_support(
        "如何检查张紧轮",
        _bundle("inspection", "张紧轮检查方法"),
        ledger,
    )

    assert parameter["aspect_support"][0]["supported"] is False
    assert inspection["aspect_support"][0]["supported"] is False


def test_graph_evidence_cannot_support_procedure_safety_or_image_aspects() -> None:
    ledger = EvidenceLedger([_graph_entry(), _graph_entry(solution=True)])
    for aspect_id in ("procedure", "safety", "image"):
        fused = fuse_evidence_support(
            "诊断问题",
            _bundle(aspect_id, aspect_id),
            ledger,
        )
        assert fused["aspect_support"][0]["supported"] is False


def test_prebound_graph_ids_cannot_bypass_aspect_authorization() -> None:
    path = _graph_entry()
    solution = _graph_entry(solution=True)
    bundle = _bundle("gap", "张紧轮间隙参数")
    bundle["aspect_support"][0].update({
        "supported": True,
        "evidence_ids": [path["evidence_id"], solution["evidence_id"]],
    })

    fused = fuse_evidence_support(
        "张紧轮间隙是多少",
        bundle,
        EvidenceLedger([path, solution]),
    )

    assert fused["aspect_support"][0]["supported"] is False
    assert fused["aspect_support"][0]["evidence_ids"] == []


def test_verified_active_graph_solution_supports_treatment_direction() -> None:
    fused = fuse_evidence_support(
        "这个故障应该怎么处理",
        _bundle("treatment", "故障处理方向"),
        EvidenceLedger([_graph_entry(), _graph_entry(solution=True)]),
    )

    assert fused["aspect_support"][0]["evidence_ids"] == [
        "graph:kgpath:device-1:component-1:fault-1:solution-1"
    ]


def test_manual_and_graph_support_are_merged_without_manual_short_circuit() -> None:
    fused = fuse_evidence_support(
        "张紧轮异响原因",
        _bundle("fault-cause", "张紧轮异响原因", manual_supported=True),
        EvidenceLedger([_manual_entry(), _graph_entry()]),
    )

    row = fused["aspect_support"][0]
    assert row["evidence_ids"] == [
        "manual:manual-1:chunk-1",
        "graph:kgpath:device-1:component-1:fault-1:none",
    ]
    assert row["supporting_source_types"] == ["manual", "graph"]


def test_graph_capability_rows_are_added_for_opaque_manual_aspects() -> None:
    bundle = _bundle(
        "aspect-opaque-hash",
        "张紧轮异响原因和处理方法",
        manual_supported=True,
    )

    fused = fuse_evidence_support(
        "张紧轮异响是什么原因",
        bundle,
        EvidenceLedger([_manual_entry(), _graph_entry()]),
    )

    graph_rows = [
        row
        for row in fused["aspect_support"]
        if row.get("aspect_origin") == "graph_capability"
    ]
    assert {row["aspect_id"] for row in graph_rows} == {
        "device",
        "component",
        "fault-cause",
    }
    assert all(row["supported"] is True for row in graph_rows)
    obligations = {row["aspect_id"]: row["user_obligation"] for row in graph_rows}
    assert obligations == {
        "device": False,
        "component": False,
        "fault-cause": True,
    }
    assert all(row["evidence_ids"] == [
        "graph:kgpath:device-1:component-1:fault-1:none"
    ] for row in graph_rows)


def test_hashed_fault_aspect_authorizes_graph_path_on_the_user_obligation() -> None:
    bundle = _bundle(
        "aspect-24295e83cdbce92f",
        "助力油泵噪声大故障原因",
    )

    fused = fuse_evidence_support(
        "助力油泵噪声大，请诊断可能原因",
        bundle,
        EvidenceLedger([_graph_entry()]),
    )

    user_row = fused["aspect_support"][0]
    assert user_row["aspect_id"] == "aspect-24295e83cdbce92f"
    assert user_row["supported"] is True
    assert user_row["evidence_ids"] == [
        "graph:kgpath:device-1:component-1:fault-1:none"
    ]
    assert user_row["supporting_source_types"] == ["graph"]


def test_fault_names_containing_safety_or_temperature_are_not_manual_only() -> None:
    ledger = EvidenceLedger([_graph_entry()])
    for aspect_text in ("安全阀堵死故障原因", "温度高保护故障原因"):
        fused = fuse_evidence_support(
            f"请诊断{aspect_text}",
            _bundle("aspect-fault-name", aspect_text),
            ledger,
        )
        assert fused["aspect_support"][0]["evidence_ids"] == [
            "graph:kgpath:device-1:component-1:fault-1:none"
        ]


def test_composite_fault_and_inspection_keeps_graph_on_fault_claim_only() -> None:
    fused = fuse_evidence_support(
        "请说明故障原因和检查方法",
        _bundle("aspect-composite", "故障原因和检查方法", manual_supported=True),
        EvidenceLedger([_manual_entry(), _graph_entry()]),
    )

    composite = fused["aspect_support"][0]
    graph_fault = next(
        row for row in fused["aspect_support"]
        if row.get("aspect_origin") == "graph_capability"
        and row.get("aspect_id") == "fault-cause"
    )
    assert composite["evidence_ids"] == ["manual:manual-1:chunk-1"]
    assert graph_fault["user_obligation"] is True
    assert graph_fault["evidence_ids"] == [
        "graph:kgpath:device-1:component-1:fault-1:none"
    ]


def test_graph_path_binds_same_section_manual_treatment_after_query_expansion() -> None:
    graph = {
        **_graph_entry(),
        "source": {
            "document_id": "manual-1",
            "document_version": "v1",
            "section_id": "sec-1",
            "source_chunk_uids": ["chunk-1"],
            "pages": [12],
        },
    }
    manual = {
        **_manual_entry(),
        "text": "检查张紧轮轴承，若损坏则更换张紧轮轴承。",
        "source": {
            "document_id": "manual-1",
            "document_version": "v1",
            "parent_section_id": "sec-1",
            "chunk_uid": "chunk-1",
            "page": 12,
        },
    }
    bundle = _bundle(
        "aspect-expanded",
        "张紧轮轴承磨损故障处理步骤扭矩参数",
    )

    fused = fuse_evidence_support(
        "张紧轮轴承磨损时如何处理？",
        bundle,
        EvidenceLedger([graph, manual]),
    )

    expanded = fused["aspect_support"][0]
    assert expanded["aspect_origin"] == "retrieval_expansion"
    assert expanded["user_obligation"] is False
    treatment = next(row for row in fused["aspect_support"] if row["aspect_id"] == "manual-treatment")
    assert treatment["supported"] is True
    assert treatment["evidence_ids"] == [manual["evidence_id"]]
