from evaluation.maintenance_eval_evidence import (
    EvidenceEnvelope,
    decide_coverage_status,
    extract_evidence_envelopes,
    score_turn_output,
)
from evaluation.maintenance_eval_schema import (
    AllowedSource,
    ClaimConstraint,
    ConflictAlternative,
    ConflictConstraint,
    MaintenanceEvalTurn,
)


def test_manual_trace_uses_full_result_data_and_stable_source_identity() -> None:
    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_summary": "截断摘要中的错误扭矩 99 N·m",
                        "result_data": [
                            {
                                "content": "水泵锁紧扭矩为 20 N·m。",
                                "metadata": {
                                    "qualification": "qualified",
                                    "document_id": "manual-v1",
                                    "document_version": "1.0",
                                    "page": 25,
                                    "chunk_id": "chunk-25-torque",
                                },
                            }
                        ],
                    }
                ]
            }
        ]
    }

    result = extract_evidence_envelopes(metadata)

    assert result.trace_missing is False
    assert result.diagnostics == []
    assert result.envelopes == [
        EvidenceEnvelope(
            evidence_id="manual:manual-v1:chunk-25-torque",
            source_type="manual",
            text="水泵锁紧扭矩为 20 N·m。",
            qualification="qualified",
            source={
                "document_id": "manual-v1",
                "document_version": "1.0",
                "page": 25,
                "chunk_id": "chunk-25-torque",
            },
            conflict_eligible=True,
        )
    ]
    assert "99 N·m" not in result.envelopes[0].text


def test_summary_only_trace_is_diagnostic_and_cannot_be_evidence() -> None:
    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_summary": "水泵锁紧扭矩为 20 N·m。",
                    }
                ]
            }
        ]
    }

    result = extract_evidence_envelopes(metadata)

    assert result.envelopes == []
    assert result.trace_missing is True
    assert "evidence_trace_missing" in result.diagnostics


def test_manual_trace_without_stable_identity_is_ineligible() -> None:
    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": [
                            {
                                "content": "水泵锁紧扭矩为 20 N·m。",
                                "metadata": {"qualification": "qualified", "page": 25},
                            }
                        ],
                    }
                ]
            }
        ]
    }

    result = extract_evidence_envelopes(metadata)

    assert result.envelopes == []
    assert "manual_source_identity_missing" in result.diagnostics


def test_domain_rule_requires_rule_id_and_active_status() -> None:
    valid = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "domain_rule_engine",
                        "result_data": {
                            "message": "检查水泵密封圈。",
                            "rule": {"rule_id": "rule-pump", "status": "active"},
                            "evidence_sources": [{"doc_id": "rule-doc"}],
                        },
                    }
                ]
            }
        ]
    }
    invalid = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "domain_rule_engine",
                        "result_data": {
                            "message": "检查水泵密封圈。",
                            "rule": {"rule_id": "rule-pump"},
                        },
                    }
                ]
            }
        ]
    }

    valid_result = extract_evidence_envelopes(valid)
    invalid_result = extract_evidence_envelopes(invalid)

    assert valid_result.envelopes[0].source_type == "domain_rule"
    assert valid_result.envelopes[0].source["status"] == "active"
    assert invalid_result.envelopes == []
    assert "domain_rule_identity_or_status_invalid" in invalid_result.diagnostics


def test_graph_requires_stable_path_or_node_identity() -> None:
    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "java_graph_diagnosis_path",
                        "result_data": {
                            "raw_records": [
                                {
                                    "pathId": "path-1",
                                    "nodeIds": ["device-1", "fault-1"],
                                    "relationshipTypes": ["HAS_FAULT"],
                                    "deviceName": "摩托车发动机",
                                    "faultName": "水泵泄漏",
                                },
                                {"deviceName": "摩托车发动机", "faultName": "无 ID 摘要"},
                            ]
                        },
                    }
                ]
            }
        ]
    }

    result = extract_evidence_envelopes(metadata)

    assert [item.source_type for item in result.envelopes] == ["graph"]
    assert result.envelopes[0].source["path_ids"] == ["path-1"]
    assert "graph_source_identity_missing" in result.diagnostics


def test_coverage_status_has_one_deterministic_priority_order() -> None:
    assert decide_coverage_status(
        expected_scope="out_of_scope", aspect_support=[True], has_conflict=True
    ) == "unsupported"
    assert decide_coverage_status(
        expected_scope="in_scope", aspect_support=[False, False], has_conflict=True
    ) == "conflict"
    assert decide_coverage_status(
        expected_scope="in_scope", aspect_support=[False, False], has_conflict=False
    ) == "unsupported"
    assert decide_coverage_status(
        expected_scope="in_scope", aspect_support=[True, True], has_conflict=False
    ) == "complete"
    assert decide_coverage_status(
        expected_scope="in_scope", aspect_support=[True, False], has_conflict=False
    ) == "partial"


def _manual_metadata(*, qualification: str = "qualified", document_id: str = "manual-a") -> dict:
    return {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": {
                            "qualified_evidence": [
                                {
                                    "content": "Pump bolt torque is 20 Nm.",
                                    "metadata": {
                                        "qualification": qualification,
                                        "document_id": document_id,
                                        "chunk_id": "pump-torque",
                                        "page": 25,
                                    },
                                }
                            ],
                            "reference_evidence": [
                                {
                                    "content": "Reference says pump bolt torque is 25 Nm.",
                                    "metadata": {
                                        "qualification": "reference_only",
                                        "document_id": "reference-b",
                                        "chunk_id": "pump-ref",
                                        "page": 26,
                                    },
                                }
                            ],
                        },
                    }
                ]
            }
        ]
    }


def _torque_claim(document_id: str = "manual-a") -> ClaimConstraint:
    return ClaimConstraint(
        claim_id="pump_torque",
        answer_patterns=["20 Nm"],
        evidence_patterns=["20 Nm"],
        allowed_sources=[
            AllowedSource(
                source_type="manual", document_id=document_id, chunk_ids=["pump-torque"], pages=[25]
            )
        ],
    )


def test_manual_payload_keeps_qualified_and_reference_evidence_together() -> None:
    result = extract_evidence_envelopes(_manual_metadata())

    assert [(item.qualification, item.source["document_id"]) for item in result.envelopes] == [
        ("qualified", "manual-a"),
        ("reference_only", "reference-b"),
    ]


def test_manual_duplicate_prefers_later_qualified_evidence_over_reference_only() -> None:
    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": {
                            "results": [
                                {
                                    "content": "Stale reference copy.",
                                    "metadata": {
                                        "qualification": "reference_only",
                                        "document_id": "manual-a",
                                        "chunk_id": "pump-torque",
                                        "page": 25,
                                    },
                                }
                            ]
                        },
                    }
                ]
            },
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": {
                            "qualified_evidence": [
                                {
                                    "content": "Exact qualified copy.",
                                    "metadata": {
                                        "qualification": "qualified",
                                        "document_id": "manual-a",
                                        "chunk_id": "pump-torque",
                                        "page": 25,
                                        "document_version": "1.0",
                                    },
                                }
                            ]
                        },
                    }
                ]
            },
        ]
    }

    result = extract_evidence_envelopes(metadata)

    assert len(result.envelopes) == 1
    assert result.envelopes[0].qualification == "qualified"
    assert result.envelopes[0].text == "Exact qualified copy."
    assert result.envelopes[0].source["document_version"] == "1.0"


def test_score_requires_exact_allowed_document_even_when_evidence_text_matches() -> None:
    turn = MaintenanceEvalTurn(query="torque", claim_constraints=[_torque_claim("manual-a")])

    score = score_turn_output(turn, "Set it to 20 Nm.", _manual_metadata(document_id="manual-wrong"))

    assert score.final_pass is False
    assert score.unsupported_completion_free is False
    assert "claim:pump_torque:allowed_source_missing" in score.diagnostics


def test_alignment_requires_supported_claim_to_appear_in_answer() -> None:
    turn = MaintenanceEvalTurn(query="torque", claim_constraints=[_torque_claim()])

    score = score_turn_output(turn, "The relevant instruction was found.", _manual_metadata())

    assert score.evidence_source_pass is True
    assert score.answer_evidence_alignment_pass is False
    assert score.final_pass is False


def test_partial_score_requires_specific_missing_disclosure_and_no_unsupported_completion() -> None:
    turn = MaintenanceEvalTurn(
        query="pump service",
        expected_coverage_status="partial",
        claim_constraints=[
            _torque_claim(),
            ClaimConstraint(
                claim_id="seal",
                answer_patterns=["replace seal"],
                evidence_patterns=["replace seal"],
                missing_disclosure_patterns=["seal procedure is not available"],
                allowed_sources=[AllowedSource(source_type="manual", document_id="manual-a", chunk_ids=["seal"])],
            ),
        ],
    )

    score = score_turn_output(
        turn,
        "Set it to 20 Nm. The seal procedure is not available.",
        _manual_metadata(),
    )
    supplemented = score_turn_output(
        turn,
        "Set it to 20 Nm. Replace seal.",
        _manual_metadata(),
    )

    assert score.coverage_status == "partial"
    assert score.partial_answer_correct is True
    assert score.final_pass is True
    assert supplemented.partial_answer_correct is False
    assert supplemented.unsupported_completion_free is False


def test_conflict_requires_two_distinct_evidence_sources_and_disclosure() -> None:
    turn = MaintenanceEvalTurn(
        query="torque",
        expected_coverage_status="conflict",
        conflict_constraints=[
            ConflictConstraint(
                subject="pump torque",
                alternatives=[
                    ConflictAlternative(
                        value_patterns=["20"],
                        unit_patterns=["Nm"],
                        allowed_sources=[AllowedSource(source_type="manual", document_id="manual-a")],
                    ),
                    ConflictAlternative(
                        value_patterns=["25"],
                        unit_patterns=["Nm"],
                        allowed_sources=[AllowedSource(source_type="graph", path_ids=["pump-path"])],
                    ),
                ],
                disclosure_patterns=["sources conflict"],
            )
        ],
    )
    metadata = _manual_metadata()
    metadata["react_trace"].append(
        {
            "tool_calls": [
                {
                    "name": "java_graph_diagnosis_path",
                    "result_data": {
                        "raw_records": [
                            {"pathId": "pump-path", "content": "Pump bolt torque is 25 Nm."}
                        ]
                    },
                }
            ]
        }
    )

    score = score_turn_output(turn, "Sources conflict: 20 Nm versus 25 Nm.", metadata)
    undisclosed = score_turn_output(turn, "Use 20 Nm.", metadata)

    assert score.coverage_status == "conflict"
    assert score.conflict_handling_pass is True
    assert score.final_pass is True
    assert undisclosed.conflict_handling_pass is False


def test_out_of_scope_refusal_cannot_continue_with_repair_guess() -> None:
    turn = MaintenanceEvalTurn(
        query="unknown procedure",
        expected_scope="out_of_scope",
        claim_constraints=[_torque_claim()],
    )

    score = score_turn_output(
        turn,
        "Cannot determine from the available material. Set it to 20 Nm.",
        _manual_metadata(),
    )

    assert score.coverage_status == "unsupported"
    assert score.unsupported_completion_free is False
    assert score.final_pass is False
    assert "out_of_scope_refusal_followed_by_claim" in score.diagnostics


def test_source_modes_reject_manual_lead_and_repeated_lines_but_honor_quote_and_page_requests() -> None:
    normal = MaintenanceEvalTurn(query="torque", claim_constraints=[_torque_claim()])
    normal_answer = "According to the manual, set it to 20 Nm.\nAccording to the manual, set it to 20 Nm."
    quote = MaintenanceEvalTurn(
        query="quote", source_request_mode="quote", claim_constraints=[_torque_claim()]
    )
    page = MaintenanceEvalTurn(
        query="page", source_request_mode="page", claim_constraints=[_torque_claim()]
    )

    normal_score = score_turn_output(normal, normal_answer, _manual_metadata())
    quote_score = score_turn_output(quote, '"Pump bolt torque is 20 Nm."', _manual_metadata())
    page_score = score_turn_output(page, "Set it to 20 Nm (page 25).", _manual_metadata())

    assert normal_score.source_style_mode_pass is False
    assert "normal_mode_manual_lead" in normal_score.diagnostics
    assert "normal_mode_repeated_line" in normal_score.diagnostics
    assert quote_score.source_style_mode_pass is True
    assert page_score.source_style_mode_pass is True
