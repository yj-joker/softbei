"""End-to-end graph quality evaluation against real Neo4j, Java and Redis.

The script creates isolated runtime fixtures with real model embeddings.  It
never injects similarity scores into application code.  Quality tiers are
selected from scores returned by the live Java/Neo4j retrieval path and all
created records are removed in ``finally`` unless ``--keep-data`` is used.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from neo4j import GraphDatabase


ROOT = Path(__file__).resolve().parents[2]
FIX_AGENT_ROOT = ROOT / "FixAgent"
if str(FIX_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(FIX_AGENT_ROOT))
load_dotenv(ROOT / ".env")

from services.clarification.graph_candidates import unresolved_graph_dimensions  # noqa: E402
from services.clarification.models import RiskLevel  # noqa: E402
from services.clarification.policy import ClarificationDecisionEngine  # noqa: E402
from services.knowledge.vector_service import build_redis_filter, get_vector_service  # noqa: E402
from services.retrieval.device_identity import QueryContract  # noqa: E402
from services.retrieval.evidence import EvidenceLedger  # noqa: E402
from services.retrieval.graph_quality import evaluate_graph_path_quality  # noqa: E402
from services.routing.graph_candidate_provider import JavaGraphCandidateProvider  # noqa: E402
from tools.knowledge_retrieval_tool import get_knowledge_retrieval_tool  # noqa: E402


def _generated_texts(run_tag: str) -> tuple[str, list[str]]:
    rng = random.SystemRandom()
    subject = rng.choice(("液压执行单元", "旋转驱动单元", "热交换控制单元"))
    context = rng.choice(("负载切换后", "连续运行阶段", "低速升载过程中"))
    observations = [
        "输出压力周期性下降并伴随壳体振动",
        "输出压力出现周期波动",
        "负载变化时压力响应迟缓",
        "运行阶段出现间歇性振动",
        "控制反馈发生短时漂移",
        "温升后输出响应不稳定",
        "执行动作结束后存在残余压力",
        "冷却回路流量缓慢下降",
        "通信接口偶发数据帧丢失",
        "照明回路在待机时亮度变化",
        "仓储输送带标签识别延迟",
        "办公终端打印队列无法清空",
        "环境监测探头湿度读数偏移",
        "包装设备计数器复位失败",
        "门禁读卡器夜间提示音降低",
        "视频编码模块色彩饱和度异常",
    ]
    query = f"{run_tag} {subject}{context}{observations[0]}"
    variants = [query]
    for index, observation in enumerate(observations[1:], start=1):
        if index <= 8:
            variants.append(
                f"{run_tag} {subject}{rng.choice(('运行时', '升载时', '稳定工况下'))}{observation}"
            )
        else:
            variants.append(observation)
    return query, variants


def _graph_embeddings(texts: list[str]) -> list[list[float]]:
    api_key = str(os.getenv("DASHSCOPE_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is required")
    vectors: list[list[float]] = []
    with httpx.Client(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=90.0,
    ) as client:
        for text in texts:
            response = client.post(
                "/embeddings",
                json={
                    "model": "text-embedding-v4",
                    "input": text,
                    "dimensions": 1024,
                    "encoding_format": "float",
                },
            )
            response.raise_for_status()
            vectors.append(list(map(float, response.json()["data"][0]["embedding"])))
    if len(vectors) != len(texts) or any(len(vector) != 1024 for vector in vectors):
        raise RuntimeError("graph embedding response did not contain 1024-dimensional vectors")
    return vectors


def _cosine(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    denominator = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return numerator / denominator if denominator else 0.0


def _insert_graph_data(
    session,
    *,
    run_id: str,
    device_id: str,
    device_name: str,
    variants: list[str],
    vectors: list[list[float]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    session.run(
        "CREATE (d:Device {id:$id, name:$name, code:$code, test_run_id:$runId})",
        id=device_id,
        name=device_name,
        code=run_id,
        runId=run_id,
    ).consume()
    for index, (text, vector) in enumerate(zip(variants, vectors)):
        component_id = f"gq-{run_id}-c-{index}"
        fault_id = f"gq-{run_id}-f-{index}"
        document_id = f"gq-{run_id}-doc-{index}"
        section_id = f"gq-{run_id}-sec-{index}"
        chunk_uid = f"gq-{run_id}-chunk-{index}"
        graph_revision = f"gq-{run_id}-rev"
        page = 100 + index
        session.run(
            """
            MATCH (d:Device {id:$deviceId})
            CREATE (c:Component {
                id:$componentId, name:$componentName, embedding:$componentEmbedding,
                document_id:$documentId, document_version:'e2e-v1', section_id:$sectionId,
                source_chunk_uids:[$chunkUid], page_start:$page, page_end:$page,
                graph_revision:$graphRevision, test_run_id:$runId
            })
            CREATE (f:Fault {
                id:$faultId, name:$faultName, description:$faultName, embedding:$faultEmbedding,
                status:'active', document_id:$documentId, document_version:'e2e-v1',
                section_id:$sectionId, source_chunk_uids:[$chunkUid],
                page_start:$page, page_end:$page, graph_revision:$graphRevision,
                distinguishing_features:[$observation], test_run_id:$runId
            })
            CREATE (d)-[:OWNS]->(c)
            CREATE (c)-[:CAUSES]->(f)
            """,
            deviceId=device_id,
            componentId=component_id,
            componentName=f"runtime-component-{index}-{run_id}",
            componentEmbedding=vector,
            faultId=fault_id,
            faultName=text,
            faultEmbedding=vector,
            documentId=document_id,
            sectionId=section_id,
            chunkUid=chunk_uid,
            page=page,
            graphRevision=graph_revision,
            observation=f"现场观测序列 {index + 1}: {text}",
            runId=run_id,
        ).consume()
        rows.append({
            "component_id": component_id,
            "fault_id": fault_id,
            "document_id": document_id,
            "section_id": section_id,
            "chunk_uid": chunk_uid,
            "path_id": f"kgpath:{device_id}:{component_id}:{fault_id}",
            "text": text,
            "page": str(page),
        })
    return rows


def _raw_candidates(
    client: httpx.Client,
    *,
    query: str,
    device_name: str,
    device_id: str,
    path_ids: list[str],
) -> list[dict[str, Any]]:
    response = client.post(
        "/weixiu/path/candidates",
        json={
            "queryContract": {
                "rawQuery": query,
                "intent": "fault_diagnosis",
                "taskAction": "find_cause",
                "deviceIdentity": device_name,
            },
            "allowedDeviceIds": [device_id],
            "allowedPathIds": path_ids,
            "limit": 50,
            "minScore": 0.0,
        },
    )
    response.raise_for_status()
    data = response.json().get("data") or {}
    return [dict(item) for item in data.get("records") or []]


def _select_tiers(records: list[dict[str, Any]]) -> tuple[dict, list[dict], dict]:
    graded = [(record, evaluate_graph_path_quality(record, trusted_query_structure=True)) for record in records]
    highs = [record for record, decision in graded if decision.tier.value == "high"]
    mediums = [record for record, decision in graded if decision.tier.value == "medium"]
    lows = [record for record, decision in graded if decision.tier.value == "low"]
    if not highs or len(mediums) < 2 or not lows:
        scores = sorted(
            (float(record.get("graphScore") or 0.0), record.get("pathId"))
            for record in records
        )
        raise RuntimeError(
            "generated embeddings did not cover high/medium/low tiers; "
            f"observed scores={scores}"
        )
    return highs[0], mediums[:2], lows[0]


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    run_id = uuid.uuid4().hex[:12]
    run_tag = f"e2e-{run_id}"
    query, variants = _generated_texts(run_tag)
    vectors = await asyncio.to_thread(_graph_embeddings, variants)
    local_scores = [_cosine(vectors[0], vector) for vector in vectors]
    device_id = f"gq-{run_id}-device"
    device_name = f"runtime-device-{run_id}"
    redis_keys: list[str] = []
    embedding_cache_keys: list[str] = []
    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]),
    )
    vector_service = None
    cleaned = False
    try:
        with driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as session:
            fixture_rows = _insert_graph_data(
                session,
                run_id=run_id,
                device_id=device_id,
                device_name=device_name,
                variants=variants,
                vectors=vectors,
            )

        headers = {"X-Internal-Token": os.environ["INTERNAL_TOKEN"]}
        with httpx.Client(base_url=args.java_url, headers=headers, timeout=90.0) as client:
            raw_records = _raw_candidates(
                client,
                query=query,
                device_name=device_name,
                device_id=device_id,
                path_ids=[row["path_id"] for row in fixture_rows],
            )
        high_record, medium_records, low_record = _select_tiers(raw_records)

        provider = JavaGraphCandidateProvider(base_url=args.java_url)
        contract = QueryContract(
            raw_query=query,
            intent="fault_diagnosis",
            task_action="find_cause",
            raw_device_span=device_name,
            device_name=device_name,
        )

        async def gated(path_ids: list[str]):
            return await provider.fetch_candidates(
                contract,
                allowed_device_ids=(device_id,),
                allowed_path_ids=tuple(path_ids),
                min_score=0.0,
                limit=50,
            )

        high_candidates = await gated([str(high_record["pathId"])])
        medium_candidates = await gated([str(item["pathId"]) for item in medium_records])
        low_candidates = await gated([str(low_record["pathId"])])
        low_gate_diagnostics = dict(provider.retrieval_status)

        clarification = ClarificationDecisionEngine().decide(
            medium_candidates,
            risk_level=RiskLevel.HIGH,
            unresolved_dimensions=unresolved_graph_dimensions(medium_candidates),
        )

        high_batch = await provider.retrieve_path_evidence(
            fault_description=query,
            limit=50,
            min_score=0.0,
            allowed_path_ids=[str(high_record["pathId"])],
            allowed_device_ids=[device_id],
        )
        medium_batch = await provider.retrieve_path_evidence(
            fault_description=query,
            limit=50,
            min_score=0.0,
            allowed_path_ids=[str(medium_records[0]["pathId"])],
            allowed_device_ids=[device_id],
        )

        medium_path = next(
            row for row in fixture_rows if row["path_id"] == str(medium_records[0]["pathId"])
        )
        rag_text = f"{query}。手册记录的核验内容与该现场观测一致。"
        from embeddings.text_embedding import get_text_embedding

        text_embedder = get_text_embedding()
        rag_vector = await text_embedder.embed(rag_text)
        embedding_cache_keys.extend((
            text_embedder._get_cache_key(rag_text),
            text_embedder._get_cache_key(query),
        ))
        vector_service = get_vector_service()
        rag_doc_id = medium_path["chunk_uid"]
        redis_keys.append(f"{vector_service.VECTOR_KEY_PREFIX}{rag_doc_id}")
        added = vector_service.add_vector(
            rag_doc_id,
            rag_text,
            rag_vector,
            metadata={
                "document_id": medium_path["document_id"],
                "document_version": "e2e-v1",
                "chunk_id": rag_doc_id,
                "chunk_uid": rag_doc_id,
                "source_chunk_uid": rag_doc_id,
                "source_chunk_uids": [rag_doc_id],
                "parent_section_id": medium_path["section_id"],
                "section_title": f"runtime-section-{run_id}",
                "page": int(medium_path["page"]),
                "chunk_type": "text",
                "record_type": "manual",
                "status": "ready",
                "test_run_id": run_id,
            },
        )
        if not added:
            raise RuntimeError("failed to add runtime RAG vector")
        await asyncio.sleep(1.0)
        direct_rag_probe = vector_service.search(
            rag_vector,
            top_k=5,
            filter=build_redis_filter(document_id=medium_path["document_id"]),
        )
        section_probe = vector_service.get_section_records(
            medium_path["document_id"],
            medium_path["section_id"],
            limit=10,
        )

        rag_result = await get_knowledge_retrieval_tool().run(
            query=query,
            top_k=5,
            document_id=medium_path["document_id"],
            document_version="e2e-v1",
            allowed_section_ids=[medium_path["section_id"]],
            allowed_source_chunk_uids=[rag_doc_id],
        )
        if not rag_result.success:
            raise RuntimeError(f"RAG retrieval failed: {rag_result.error}")
        serialized_rag = [
            item.model_dump() if hasattr(item, "model_dump") else item
            for item in (rag_result.data or [])
        ]

        high_ledger = EvidenceLedger.from_react_trace({
            "react_trace": [{"tool_calls": [{
                "name": "java_graph_diagnosis_path",
                "result_data": high_batch.to_dict(),
            }]}],
        })
        medium_ledger = EvidenceLedger.from_react_trace({
            "react_trace": [{"tool_calls": [
                {
                    "name": "java_graph_diagnosis_path",
                    "result_data": medium_batch.to_dict(),
                },
                {"name": "knowledge_retrieval", "result_data": serialized_rag},
            ]}],
        })

        high_graph_entries = [entry for entry in high_ledger.entries if entry["source_type"] == "graph"]
        medium_graph_entries = [entry for entry in medium_ledger.entries if entry["source_type"] == "graph"]
        corroborated_manual = [
            entry for entry in medium_ledger.entries
            if entry["source_type"] == "manual" and entry.get("graph_cross_validation")
        ]
        if not high_candidates or high_candidates[0].quality_tier != "high":
            raise AssertionError("high candidate did not pass the provider gate")
        if len(medium_candidates) < 2 or any(item.quality_tier != "medium" for item in medium_candidates):
            raise AssertionError("medium candidates did not remain available for clarification")
        if low_candidates:
            raise AssertionError("low candidate passed the provider gate")
        if not high_graph_entries:
            raise AssertionError("high graph evidence did not enter EvidenceLedger")
        if medium_graph_entries:
            raise AssertionError("medium graph evidence entered EvidenceLedger")
        if not corroborated_manual:
            raise AssertionError(
                "medium graph path was not cross-validated by real RAG evidence: "
                + json.dumps({
                    "medium_batch": medium_batch.to_dict(),
                    "rag": serialized_rag,
                    "direct_rag_probe": direct_rag_probe,
                    "section_probe": section_probe,
                    "ledger": medium_ledger.entries,
                }, ensure_ascii=False, default=str)
            )

        return {
            "run_id": run_id,
            "embedding_dimensions": len(vectors[0]),
            "generated_variant_count": len(variants),
            "local_cosine_range": [round(min(local_scores), 6), round(max(local_scores), 6)],
            "raw_java_recall": {
                "record_count": len(raw_records),
                "high": {"path_id": high_record["pathId"], "score": high_record["graphScore"]},
                "medium": [
                    {"path_id": item["pathId"], "score": item["graphScore"]}
                    for item in medium_records
                ],
                "low": {"path_id": low_record["pathId"], "score": low_record["graphScore"]},
            },
            "quality_gate": {
                "high_candidate_tier": high_candidates[0].quality_tier,
                "medium_candidate_tiers": [item.quality_tier for item in medium_candidates],
                "low_candidate_count": len(low_candidates),
                "low_gate_status": low_gate_diagnostics,
            },
            "clarification": {
                "should_clarify": clarification.should_clarify,
                "dimension": clarification.question.dimension if clarification.question else "",
                "option_count": len(clarification.question.options) if clarification.question else 0,
            },
            "evidence": {
                "high_batch": high_batch.to_dict(),
                "high_graph_ledger_ids": [entry["evidence_id"] for entry in high_graph_entries],
                "medium_batch": medium_batch.to_dict(),
                "medium_graph_ledger_count": len(medium_graph_entries),
                "rag_result_ids": [
                    str((item.get("metadata") or {}).get("chunk_uid") or item.get("id") or "")
                    for item in serialized_rag
                ],
                "medium_cross_validation": [
                    entry["graph_cross_validation"] for entry in corroborated_manual
                ],
            },
        }
    finally:
        if not args.keep_data:
            if vector_service is not None and (redis_keys or embedding_cache_keys):
                vector_service.redis.delete(*redis_keys, *embedding_cache_keys)
            with driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as session:
                session.run(
                    "MATCH (n {test_run_id:$runId}) DETACH DELETE n",
                    runId=run_id,
                ).consume()
            cleaned = True
        driver.close()
        if cleaned:
            print(json.dumps({"cleanup": "complete", "run_id": run_id}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--java-url", default=os.getenv("JAVA_SERVICE_URL", "http://localhost:8080"))
    parser.add_argument("--keep-data", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(_run(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
