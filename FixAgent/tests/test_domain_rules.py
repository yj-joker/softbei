"""Unit tests for expert domain rule sync and direct diagnosis matching."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_MODULE_PATH = os.path.join(os.path.dirname(__file__), "..", "services", "domain_rules.py")
_SPEC = importlib.util.spec_from_file_location("domain_rules", _MODULE_PATH)
domain_rules = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
sys.modules["domain_rules"] = domain_rules
_SPEC.loader.exec_module(domain_rules)


class FakeEmbedding:
    async def embed(self, text: str):
        self.last_text = text
        return [0.1] * 1024


class FakeVectorService:
    def __init__(self):
        self.add_calls = []
        self.delete_calls = []
        self.search_results = []

    def add_vector(self, doc_id, text, vector, metadata=None, category=None, tags=None):
        self.add_calls.append(
            {
                "doc_id": doc_id,
                "text": text,
                "vector": vector,
                "metadata": metadata or {},
                "category": category,
                "tags": tags,
            }
        )
        return True

    def delete(self, doc_id):
        self.delete_calls.append(doc_id)
        return False

    async def search_by_text(self, text, top_k=5, include_metadata=True, filter=None):
        self.last_search = {
            "text": text,
            "top_k": top_k,
            "include_metadata": include_metadata,
            "filter": filter,
        }
        return list(self.search_results)


def _sample_payload():
    return {
        "rule_id": 7,
        "rule_code": "BLUE-SMOKE-001",
        "doc_id": "domain_rule:7",
        "status": "active",
        "title": "发动机冒蓝烟诊断",
        "device_type": "motorcycle_engine",
        "symptom_keys": ["冒蓝烟", "烧机油"],
        "condition_text": "冒蓝烟并伴随机油消耗增加，且冷却液未明显减少",
        "conclusion": "优先检查活塞环磨损和气门油封老化。",
        "question": "启动时蓝烟更明显，还是加速时更明显？",
        "options": ["A. 启动时明显", "B. 加速时明显", "C. 不确定"],
        "evidence_refs": [{"source": "expert_review", "section": "demo"}],
    }


def test_upsert_domain_rule_stores_active_rule_vector(monkeypatch):
    fake_vector = FakeVectorService()
    fake_embedding = FakeEmbedding()
    monkeypatch.setattr(domain_rules, "get_vector_service", lambda: fake_vector)
    monkeypatch.setattr(domain_rules, "get_text_embedding", lambda: fake_embedding)

    response = asyncio.run(domain_rules.upsert_domain_rule(_sample_payload()))

    assert response == {
        "success": True,
        "code": 200,
        "message": "ok",
        "doc_id": "domain_rule:7",
    }
    assert len(fake_vector.add_calls) == 1
    call = fake_vector.add_calls[0]
    assert call["doc_id"] == "domain_rule:7"
    assert "冒蓝烟" in call["text"]
    assert "活塞环磨损" in call["text"]
    assert call["metadata"]["record_type"] == "domain_rule"
    assert call["metadata"]["status"] == "active"
    assert call["metadata"]["symptom_keys"] == ["冒蓝烟", "烧机油"]
    assert call["tags"] == ["冒蓝烟", "烧机油"]


def test_delete_domain_rule_is_idempotent(monkeypatch):
    fake_vector = FakeVectorService()
    monkeypatch.setattr(domain_rules, "get_vector_service", lambda: fake_vector)

    response = asyncio.run(domain_rules.delete_domain_rule({"doc_id": "domain_rule:7"}))

    assert response["success"] is True
    assert response["code"] == 200
    assert response["doc_id"] == "domain_rule:7"
    assert fake_vector.delete_calls == ["domain_rule:7"]


def test_match_domain_rule_requires_symptom_hit_and_builds_follow_up(monkeypatch):
    fake_vector = FakeVectorService()
    fake_vector.search_results = [
        {
            "doc_id": "domain_rule:7",
            "relevance_score": 0.82,
            "metadata": {
                **_sample_payload(),
                "record_type": "domain_rule",
                "status": "active",
            },
        }
    ]
    monkeypatch.setattr(domain_rules, "get_vector_service", lambda: fake_vector)

    match = asyncio.run(
        domain_rules.match_domain_rule(
            "摩托车发动机冒蓝烟，而且最近有点烧机油",
            device_type="motorcycle_engine",
        )
    )

    assert match is not None
    assert match["rule"]["rule_id"] == 7
    assert match["confidence_source"] == "rule"
    assert match["matched_symptom_keys"] == ["冒蓝烟", "烧机油"]
    assert "优先检查活塞环磨损" in match["message"]
    assert "启动时蓝烟更明显" in match["message"]


def test_match_domain_rule_rejects_incompatible_or_keywordless_candidates(monkeypatch):
    fake_vector = FakeVectorService()
    fake_vector.search_results = [
        {
            "doc_id": "domain_rule:7",
            "relevance_score": 0.95,
            "metadata": {
                **_sample_payload(),
                "device_type": "diesel_generator",
                "record_type": "domain_rule",
                "status": "active",
            },
        },
        {
            "doc_id": "domain_rule:8",
            "relevance_score": 0.95,
            "metadata": {
                **_sample_payload(),
                "rule_id": 8,
                "doc_id": "domain_rule:8",
                "symptom_keys": ["异响"],
                "record_type": "domain_rule",
                "status": "active",
            },
        },
    ]
    monkeypatch.setattr(domain_rules, "get_vector_service", lambda: fake_vector)

    match = asyncio.run(
        domain_rules.match_domain_rule(
            "摩托车发动机冒蓝烟",
            device_type="motorcycle_engine",
        )
    )

    assert match is None
