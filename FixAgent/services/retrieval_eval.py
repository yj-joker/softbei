"""Small retrieval evaluation harness for project-specific RAG cases."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List


@dataclass
class RetrievalEvalCase:
    query: str
    expected_ids: List[str] = field(default_factory=list)
    expected_types: List[str] = field(default_factory=list)
    image_urls: List[str] = field(default_factory=list)
    filters: Dict[str, Any] = field(default_factory=dict)


class RetrievalEvaluator:
    """Evaluate recall and evidence-type hits for a retrieval tool."""

    def __init__(self, retrieval_tool):
        self.retrieval_tool = retrieval_tool

    async def evaluate(self, cases: Iterable[RetrievalEvalCase], top_k: int = 5) -> Dict[str, Any]:
        rows = []
        hit_count = 0
        type_hit_count = 0
        for case in cases:
            result = await self.retrieval_tool.run(
                query=case.query,
                top_k=top_k,
                image_urls=case.image_urls or None,
                **case.filters,
            )
            items = result.data if result.success else []
            returned_ids = [item.id for item in items]
            returned_types = {
                (item.metadata or {}).get("chunk_type")
                for item in items
                if (item.metadata or {}).get("chunk_type")
            }
            id_hit = not case.expected_ids or bool(set(case.expected_ids) & set(returned_ids))
            type_hit = not case.expected_types or bool(set(case.expected_types) & returned_types)
            hit_count += int(id_hit)
            type_hit_count += int(type_hit)
            rows.append({
                "query": case.query,
                "success": result.success,
                "returned_ids": returned_ids,
                "returned_types": sorted(returned_types),
                "id_hit": id_hit,
                "type_hit": type_hit,
            })
        total = len(rows)
        return {
            "case_count": total,
            "top_k": top_k,
            "topk_hit_rate": round(hit_count / total, 6) if total else 0.0,
            "type_hit_rate": round(type_hit_count / total, 6) if total else 0.0,
            "cases": rows,
        }
