"""Stable SSE image field and audit-summary regressions."""

from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api import main


def _stream_events(helper_name: str) -> list[dict]:
    output = main.AgentOutput(
        agent_name="fix_agent",
        message="测试回答",
        tools_used=[],
        metadata={"diagnostic_follow_up": {}},
        latency_ms=1,
    )

    async def collect() -> list[dict]:
        helper = getattr(main, helper_name)
        chunks = [chunk async for chunk in helper(output)]
        return [
            json.loads(line[6:])
            for chunk in chunks
            for line in chunk.splitlines()
            if line.startswith("data: ")
        ]

    return asyncio.run(collect())


def test_done_event_always_contains_an_image_array() -> None:
    event = {"event": "done", "data": {}}

    result = main._ensure_stream_done_image_field(event)

    assert result is event
    assert event["data"]["evidenceImages"] == []


def test_non_done_event_is_not_modified() -> None:
    event = {"event": "token", "data": {"content": "x"}}

    main._ensure_stream_done_image_field(event)

    assert "evidenceImages" not in event["data"]


def test_stream_done_metadata_contains_compact_image_summary_only() -> None:
    metadata = {
        "image_selection_status": "ok",
        "image_followup_inherited": True,
        "resolved_image_query": "如何安装起动电机；用户追问：图片呢",
        "image_selection_contract": {
            "mode": "evidence_pages",
            "decision_reason": "image_evidence_gate",
            "candidate_count": 3,
            "authorized_count": 2,
            "selected_count": 1,
            "reject_reason_counts": {"no_image_level_binding": 1},
            "selected_image_bindings": [
                {"source_chunk_id": "image-starter", "reason": "answer_evidence_binding"}
            ],
            "rejected_images": [{"image_url": "must-not-leak"}],
        },
    }
    event = {"event": "done", "data": {}}

    main._attach_stream_done_metadata(event, metadata)

    payload = event["data"]["metadata"]
    assert payload["image_selection_status"] == "ok"
    assert payload["image_followup_inherited"] is True
    assert payload["image_selection_summary"] == {
        "mode": "evidence_pages",
        "decision_reason": "image_evidence_gate",
        "candidate_count": 3,
        "authorized_count": 2,
        "selected_count": 1,
        "reject_reason_counts": {"no_image_level_binding": 1},
        "selected_source_chunk_ids": ["image-starter"],
    }
    assert "image_selection_contract" not in payload


@pytest.mark.parametrize(
    "helper_name",
    [
        "_stream_direct_agent_output",
        "_stream_scope_guard_output",
        "_stream_policy_direct_output",
        "_stream_causal_follow_up_output",
    ],
)
def test_all_early_stream_completions_include_empty_image_array(helper_name: str) -> None:
    events = _stream_events(helper_name)
    done = [event for event in events if event.get("event") == "done"]

    assert len(done) == 1
    assert done[0]["data"]["evidenceImages"] == []
