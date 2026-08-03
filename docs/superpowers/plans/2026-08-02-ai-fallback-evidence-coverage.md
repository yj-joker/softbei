# AI Fallback And Evidence Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow safe AI general guidance when the bound manual belongs to another device, while preventing retrieval query expansions from producing contradictory missing-evidence text.

**Architecture:** Keep the existing response modes. Route medium/low-risk device-document conflicts into `MAINTENANCE_AI_FALLBACK`, retain `INSUFFICIENT_EVIDENCE` for exact parameters and high-risk instructions, and continue disabling citations/images from the mismatched document. Normalize evidence coverage against immutable aspects extracted from the original user query before the final response audit.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, pytest

---

### Task 1: Route document conflicts to the existing safe AI fallback

**Files:**
- Modify: `FixAgent/services/response_policy.py:97-103`
- Test: `FixAgent/tests/test_response_policy.py`

- [ ] **Step 1: Write failing policy tests**

```python
def test_device_document_conflict_uses_safe_ai_fallback() -> None:
    policy = derive_response_policy(
        fault_decision(),
        {"status": "out_of_scope", "reason": "device_document_conflict"},
        {},
        query="飞机发动机有异响通常是什么原因",
    )
    assert policy.mode == MAINTENANCE_AI_FALLBACK
    assert policy.allow_ai_fallback is True
    assert policy.allow_knowledge_retrieval is False
    assert policy.manual_citation_allowed is False
    assert policy.images_allowed is False


def test_high_risk_device_document_conflict_remains_insufficient_evidence() -> None:
    policy = derive_response_policy(
        parameter_decision(),
        {"status": "out_of_scope", "reason": "device_document_conflict"},
        {},
        query="飞机发动机磁电机点火提前角是多少",
    )
    assert policy.mode == INSUFFICIENT_EVIDENCE
    assert policy.allow_ai_fallback is False
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `pytest -q tests/test_response_policy.py`

Expected: the medium-risk conflict still returns `BLOCKED_SCOPE`.

- [ ] **Step 3: Implement the policy matrix**

Map explicit conflicts to the existing `MAINTENANCE_AI_FALLBACK` for low/medium risk and `INSUFFICIENT_EVIDENCE` for high risk. In both cases keep retrieval, manual citations, and images disabled.

- [ ] **Step 4: Run the policy tests**

Run: `pytest -q tests/test_response_policy.py tests/test_scope_gate.py`

Expected: all tests pass and unknown explicit document IDs remain blocked.

### Task 2: Keep retrieval expansions out of visible missing aspects

**Files:**
- Modify: `FixAgent/services/retrieval/response_plan.py:220-288`
- Test: `FixAgent/tests/test_response_plan.py`

- [ ] **Step 1: Write failing evidence-aspect tests**

```python
def test_retrieval_expansion_does_not_become_visible_missing_aspect() -> None:
    bundle = _bundle(
        "partial",
        aspect_support=[{
            "aspect_id": "expanded",
            "aspect_text": "右曲轴箱盖 安装步骤 摩托车 发动机 手册原文",
            "supported": False,
            "evidence_ids": [],
        }],
        missing_aspect_ids=["expanded"],
    )
    plan = build_response_plan("如何安装右曲轴箱盖", bundle, _ledger(text="安装右曲轴箱盖。"))
    assert plan.missing_aspects == ()
    assert plan.coverage_status == "complete"


def test_explicit_second_user_aspect_is_still_reported_missing() -> None:
    plan = build_response_plan("间隙和更换周期分别是多少", partial_bundle, _ledger())
    assert plan.coverage_status == "partial"
    assert plan.missing_aspects == ("更换周期",)
```

- [ ] **Step 2: Run the response-plan tests and verify the expansion test fails**

Run: `pytest -q tests/test_response_plan.py`

Expected: the expansion text is currently copied into `missing_aspects` and appended to the answer.

- [ ] **Step 3: Normalize coverage to original-query aspects**

Build the visible missing-aspect list only from `split_question_aspects(query)`. Treat retrieval aspect rows as support signals, never as new user obligations. For a single original aspect with qualified evidence and no conflict, normalize coverage to complete. Preserve explicit compound-question gaps.

- [ ] **Step 4: Run response-plan and evidence tests**

Run: `pytest -q tests/test_response_plan.py tests/test_evidence_qualification.py`

Expected: all tests pass; true partial coverage still names the explicit missing user aspect.

### Task 3: Verify API behavior and regressions

**Files:**
- Test: `FixAgent/tests/test_response_plan.py`
- Test: `FixAgent/tests/test_response_policy.py`
- Test: `FixAgent/tests/test_scope_gate.py`

- [ ] **Step 1: Run focused regression tests**

Run: `pytest -q tests/test_response_policy.py tests/test_response_plan.py tests/test_scope_gate.py tests/test_image_page_selector.py tests/test_response_image_contract.py`

Expected: zero failures, with image binding and step-order behavior unchanged.

- [ ] **Step 2: Restart Python, Java, and frontend from the latest worktree**

Use ports `8000`, `8080`, and `3000`, preserving local `.env` and `application-dev.yml` configuration.

- [ ] **Step 3: Run both real API checks**

For `飞机发动机有异响通常是什么原因`, expect AI-source disclosure, useful low-risk causes, no manual citation, and zero images. For `如何安装右曲轴箱盖`, expect section `6.4`, pages `26-27`, four bound images, and no `当前资料没有明确说明` suffix.

- [ ] **Step 4: Report exact responses and remaining gaps**

Include response mode, scope reason, evidence pages, image count, and whether the contradictory suffix is present.
