from services.clarification.state import ClarificationStateStore, ClarificationStatus, ResolvedScope, topic_signature_for_contract


def _pending(kind="document_selection"):
    return {
        "kind": kind,
        "topic_signature": "topic:assembly",
        "original_query": "如何安装盖板",
        "candidates": [
            {"id": "A", "label": "文档甲", "constraints": {"document_id": "doc-a"}},
            {"id": "B", "label": "文档乙", "constraints": {"document_id": "doc-b"}},
        ],
    }


def test_new_state_contains_round_version_and_route_snapshot():
    store = ClarificationStateStore()
    state = store.create("s1", _pending(), route_snapshot={"action": "clarify"}, max_rounds=2)

    assert state.status is ClarificationStatus.AWAITING
    assert state.round_count == 1
    assert state.max_rounds == 2
    assert state.version == 1
    assert state.route_snapshot["action"] == "clarify"
    assert state.topic_signature == "topic:assembly"


def test_server_candidates_are_authoritative_and_answer_is_idempotent():
    store = ClarificationStateStore()
    state = store.create("s2", _pending())
    resolved = store.resolve("s2", answer="B", expected_version=state.version)

    assert resolved.status is ClarificationStatus.RESOLVED
    assert resolved.selected_option_id == "B"
    assert resolved.selected_constraints == {"document_id": "doc-b"}
    assert store.resolve("s2", answer="B", expected_version=state.version).to_dict() == resolved.to_dict()


def test_numeric_answer_resolves_candidates_by_display_order():
    store = ClarificationStateStore()
    state = store.create("numeric-selection", _pending())

    resolved = store.resolve("numeric-selection", answer="2", expected_version=state.version)

    assert resolved is not None
    assert resolved.status is ClarificationStatus.RESOLVED
    assert resolved.selected_option_id == "B"
    assert resolved.selected_constraints == {"document_id": "doc-b"}


def test_wrong_version_is_rejected_without_mutating_state():
    store = ClarificationStateStore()
    state = store.create("s3", _pending())

    assert store.resolve("s3", answer="A", expected_version=state.version + 1) is None
    assert store.load("s3").status is ClarificationStatus.AWAITING


def test_unresolved_answer_reasks_then_exhausts_after_max_rounds():
    store = ClarificationStateStore()
    state = store.create("s4", _pending(), max_rounds=2)
    reasked = store.reask("s4", expected_version=state.version)
    assert reasked.status is ClarificationStatus.REASKED
    assert reasked.round_count == 2
    exhausted = store.reask("s4", expected_version=reasked.version)
    assert exhausted.status is ClarificationStatus.EXHAUSTED


def test_new_topic_cancels_old_pending_state():
    store = ClarificationStateStore()
    store.create("s5", _pending())

    cancelled = store.cancel_for_topic("s5", "topic:new")

    assert cancelled.status is ClarificationStatus.CANCELLED
    assert store.load("s5").status is ClarificationStatus.CANCELLED


def test_topic_signature_uses_structured_entities_not_raw_word_lists():
    first = topic_signature_for_contract({"component": "离合器", "action": "安装"})
    same = topic_signature_for_contract({"component": "离合器", "action": "拆卸"})
    changed = topic_signature_for_contract({"component": "机油泵", "action": "安装"})

    assert first == same
    assert first != changed
    assert topic_signature_for_contract({"action": "安装"}) == ""


def test_resolved_scope_rejects_missing_document_and_normalizes_exact_limits():
    scope = ResolvedScope.from_constraints({
        "document_id": "manual-a",
        "allowed_section_ids": ["section-b", "section-a", "section-a"],
        "allowed_evidence_refs": ["chunk-2", "chunk-1", "chunk-1"],
        "pages": [18, "17", 18],
    })

    assert scope is not None
    assert scope.document_id == "manual-a"
    assert scope.allowed_section_ids == ("section-b", "section-a")
    assert scope.allowed_evidence_refs == ("chunk-2", "chunk-1")
    assert scope.pages == (18, 17)
    assert ResolvedScope.from_constraints({"allowed_section_ids": ["section-a"]}) is None


def test_resolved_scope_can_only_be_narrowed():
    original = ResolvedScope.from_constraints({
        "document_id": "manual-a",
        "allowed_section_ids": ["section-a", "section-b"],
        "allowed_evidence_refs": ["chunk-a", "chunk-b"],
        "pages": [10, 11],
    })
    assert original is not None

    narrowed = original.narrow({
        "document_id": "manual-a",
        "allowed_section_ids": ["section-b", "section-foreign"],
        "allowed_evidence_refs": ["chunk-b", "chunk-foreign"],
        "pages": [11, 99],
    })

    assert narrowed.allowed_section_ids == ("section-b",)
    assert narrowed.allowed_evidence_refs == ("chunk-b",)
    assert narrowed.pages == (11,)


def test_resolved_scope_projects_separate_retrieval_and_graph_scopes():
    scope = ResolvedScope.from_constraints({
        "document_id": "manual-a",
        "allowed_section_ids": ["section-a"],
        "allowed_evidence_refs": ["chunk-a", "page:17"],
        "allowed_source_chunk_uids": ["chunk-a"],
        "allowed_device_ids": ["device-a"],
        "allowed_component_ids": ["component-a"],
        "allowed_path_ids": ["path-a"],
    })

    assert scope is not None
    assert scope.allowed_source_chunk_uids == ("chunk-a",)
    assert scope.to_retrieval_scope() == {
        "document_id": "manual-a",
        "allowed_section_ids": ["section-a"],
        "allowed_evidence_refs": ["chunk-a", "page:17"],
        "allowed_source_chunk_uids": ["chunk-a"],
        "pages": [],
    }
    assert scope.to_graph_scope() == {
        "allowed_document_ids": ["manual-a"],
        "allowed_section_ids": ["section-a"],
        "allowed_source_chunk_uids": ["chunk-a"],
        "allowed_device_ids": ["device-a"],
        "allowed_component_ids": ["component-a"],
        "allowed_path_ids": ["path-a"],
    }
