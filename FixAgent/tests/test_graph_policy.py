import pytest

from services.routing.graph_policy import decide_graph_use


def _diagnostic_contract():
    return {
        "intent": "fault_diagnosis",
        "task_action": "find_cause",
        "device_name": "一号发动机",
        "component": "张紧轮",
        "symptoms": ["异常振动"],
    }


def test_variant_capabilities_are_strictly_separated():
    contract = _diagnostic_contract()
    no_graph = decide_graph_use("no_graph", contract)
    shadow = decide_graph_use("graph_shadow", contract)
    full = decide_graph_use("graph_full", contract)

    assert no_graph.candidate_enabled is False
    assert shadow.candidate_enabled is True
    assert shadow.may_influence_route is False
    assert shadow.may_enter_evidence is False
    assert full.may_influence_route is True
    assert full.may_enter_evidence is True


@pytest.mark.parametrize(
    "contract",
    [
        {"intent": "parameter_query", "task_action": "parameter_lookup"},
        {"intent": "maintenance_guidance", "task_action": "procedure_lookup"},
        {"intent": "image_query", "task_action": "show_image"},
        {"intent": "safety_query", "task_action": "safety_lookup"},
    ],
)
def test_manual_only_requests_never_enable_graph(contract):
    decision = decide_graph_use("graph_full", contract)
    assert decision.candidate_enabled is False
    assert decision.pre_retrieval_enabled is False
    assert decision.allowed_claim_types == ()


def test_historical_graph_alias_is_graph_full():
    assert decide_graph_use("graph", _diagnostic_contract()) == decide_graph_use(
        "graph_full", _diagnostic_contract()
    )


def test_diagnostic_intent_is_not_blocked_by_parameter_lookup_action():
    decision = decide_graph_use(
        "graph_full",
        {
            "intent": "fault_diagnosis",
            "task_action": "parameter_lookup",
            "requested_fields": ["故障原因", "判断依据"],
            "symptoms": ["压缩压力低于最小值"],
        },
    )

    assert decision.candidate_enabled is True
    assert decision.pre_retrieval_enabled is True
    assert decision.reason == "diagnostic_graph_enabled"


def test_pure_parameter_lookup_remains_manual_only():
    decision = decide_graph_use(
        "graph_full",
        {"intent": "parameter_query", "task_action": "parameter_lookup"},
    )

    assert decision.candidate_enabled is False
    assert decision.pre_retrieval_enabled is False
    assert decision.reason == "manual_only_request"


def test_symptom_driven_repair_guidance_enables_diagnostic_graph():
    decision = decide_graph_use(
        "graph_full",
        {
            "intent": "maintenance_guidance",
            "task_action": "repair_guidance",
            "component": "火花塞",
            "symptoms": ["火花塞损坏"],
            "requested_fields": ["故障所属部件", "手册依据"],
        },
    )

    assert decision.candidate_enabled is True
    assert decision.pre_retrieval_enabled is True
    assert decision.may_enter_evidence is True
    assert decision.reason == "diagnostic_graph_enabled"
