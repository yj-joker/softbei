import json
import hashlib
import logging
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Mapping
from functools import partial
from pathlib import Path
from typing import List
from fastapi import FastAPI, HTTPException, Request
from typing import Any, List
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# 全局替换：所有 json.dumps 默认保留中文原文，避免 \uXXXX 乱码
# 使用方法：文件内所有 json.dumps 调用都用 json_dumps 替代
json_dumps = partial(json.dumps, ensure_ascii=False)
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
from schemas.request import (
    ChatRequest,
    KnowledgeImportRequest,
    KnowledgeSearchRequest,
    MemoryConsolidateRequest,
    TemporaryPlanGenerateRequest,
    CaseDraftRequest,
    CaseComplianceRequest,
    CaseExtractRequest,
    ValidateRequest,
)
from schemas.voice_task import VoiceTaskDecision, VoiceTaskRequest
from schemas.response import (
    BaseResponse,
    ChatResponse,
    EvidenceImage,
    KnowledgeCacheClearResponse,
    KnowledgeImportResponse,
    KnowledgeSearchResponse,
    KnowledgeStorageStatsResponse,
    MemoryConsolidateResponse,
    TemporaryPlanDraftResponse,
    CaseDraftResponse,
    CaseComplianceResponse,
    CaseExtractResponse,
)
from services.case.case_agent import draft_case, check_compliance, extract_material, validate_task_text, validate_graph_entities
from agents.fix_agent import get_fix_agent
from agents.voice_task_agent import get_voice_task_agent
from guardrails import get_review_agent
from agents.memory_agent import get_memory_agent
from agents.base_agent import AgentInput, AgentOutput
from services.knowledge.vector_service import build_redis_filter, get_vector_service
from services.domain_rules import (
    DOMAIN_RULE_TOOL_NAME,
    DomainRuleServiceError,
    delete_domain_rule,
    match_domain_rule,
    upsert_domain_rule,
)
from services.causal_followup import (
    FOLLOW_UP_TOOL_NAME,
    build_follow_up,
    format_follow_up_message,
    format_resolution_message,
    resolve_follow_up,
)
from services.llm.service import get_llm_service
from services.llm.output_style import (
    USER_VISIBLE_PLAIN_TEXT_RULES,
    regenerate_user_visible_text,
    strip_user_visible_emojis,
)
from services.knowledge.image_summary_service import get_image_summary_service
from services.intent_router import IntentDecision, get_intent_router
from services.response_policy import derive_response_policy
from services.response_style import select_style
from services.preference_capture import schedule_capture
from services.retrieval.scope import (
    OUT_OF_SCOPE,
    decide_scope,
    format_scope_guard_message,
)
from services.retrieval.device_identity import (
    DeviceCatalog,
    QueryContract,
    compare_query_to_document,
    document_identity_heads,
    load_dynamic_device_catalog,
    query_has_grounded_operation_target,
    query_mentions_unresolved_identity,
)
from services.retrieval.evidence import EvidenceLedger
from services.retrieval.provenance import canonical_manual_chunk_id, dedupe_and_sort_manual_records
from services.retrieval.query_constraints import (
    candidate_constraint_conflicts,
    extract_query_constraints,
)
from services.retrieval.procedure_scope import (
    normalize_procedure_target,
    procedure_scope_from_heading,
    procedure_scope_from_metadata,
    procedure_target_similarity,
)
from services.retrieval.response_plan import build_response_plan, finalize_response
from services.retrieval.section_index import SectionTitleIndex
from services.routing.executor import RouteExecutor
from services.routing.evidence_gate import EvidenceDocumentGate
from services.routing.models import RouteAction, RoutePlan
from services.routing.orchestrator import SemanticRoutingOrchestrator
from services.routing.document_selection import (
    clear_pending_document_selection,
    load_pending_document_selection,
    remember_pending_document_selection,
    resolve_pending_document_selection,
)
from services.pending_clarification import (
    clear_pending_clarification,
    format_pending_resolution,
    load_pending_clarification,
    remember_pending_clarification,
    resolve_pending_clarification,
)
from tools.knowledge_retrieval_tool import get_knowledge_retrieval_tool
from tools.knowledge_inventory_tool import get_knowledge_inventory_tool
from services.temporary_plan_service import get_temporary_plan_service
from config.settings import get_settings
from schemas.models import AgentMode

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)


def _runtime_git_commit(repository_root: Path) -> str:
    configured = str(os.environ.get("FIXAGENT_GIT_COMMIT") or "").strip()
    if configured:
        return configured
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return completed.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _runtime_git_dirty(repository_root: Path) -> bool:
    configured = str(os.environ.get("FIXAGENT_GIT_DIRTY") or "").strip().casefold()
    if configured:
        return configured in {"1", "true", "yes", "dirty"}
    try:
        completed = subprocess.run(
            [
                "git",
                "status",
                "--porcelain",
                "--untracked-files=normal",
                "--",
                "FixAgent/api",
                "FixAgent/agents",
                "FixAgent/config",
                "FixAgent/guardrails",
                "FixAgent/mq",
                "FixAgent/schemas",
                "FixAgent/services",
                "FixAgent/tools",
                "weixiu/src/main",
                "fix-/src",
            ],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return bool(completed.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return True


def _runtime_snapshot() -> dict[str, Any]:
    repository_root = Path(__file__).resolve().parents[2]
    worktree = str(os.environ.get("FIXAGENT_WORKTREE") or "").strip() or str(repository_root)
    documents: list[dict[str, Any]] = []
    catalog_available = True
    try:
        manifests = get_vector_service().list_all_manifests() or []
        for manifest in manifests:
            if not isinstance(manifest, Mapping) or str(manifest.get("status") or "") != "ready":
                continue
            document_id = str(manifest.get("document_id") or "").strip()
            if not document_id:
                continue
            try:
                revision = max(0, int(manifest.get("index_revision") or 0))
            except (TypeError, ValueError):
                revision = 0
            documents.append(
                {
                    "document_id": document_id,
                    "index_revision": revision,
                    "status": "ready",
                }
            )
        documents.sort(key=lambda item: item["document_id"])
    except Exception as exc:
        catalog_available = False
        logger.warning("Runtime document catalog unavailable: %s", exc)

    git_commit = _runtime_git_commit(repository_root)
    dirty = _runtime_git_dirty(repository_root)
    build_id = (git_commit[:12] if git_commit else "unknown") + ("-dirty" if dirty else "")
    return {
        "git_commit": git_commit,
        "dirty": dirty,
        "build_id": build_id,
        "worktree": worktree,
        "catalog_available": catalog_available,
        "documents": documents,
    }


from contextlib import asynccontextmanager


def _normalize_diagnosis_item(item: dict) -> dict:
    return {
        "priority": item.get("priority", ""),
        "fault_part": item.get("faultPart", item.get("fault_part", "")),
        "root_cause": item.get("rootCause", item.get("root_cause", "")),
        "knowledge_basis": item.get("knowledgeBasis", item.get("knowledge_basis", "")),
    }


def _serialize_diagnosis_items(items: list[dict]) -> list[dict]:
    return [
        {
            "priority": item.get("priority", ""),
            "faultPart": item.get("fault_part", item.get("faultPart", "")),
            "rootCause": item.get("root_cause", item.get("rootCause", "")),
            "knowledgeBasis": item.get("knowledge_basis", item.get("knowledgeBasis", "")),
        }
        for item in items
    ]


def _parse_chat_payload_json(text: str) -> dict | None:
    """解析聊天结构化 payload。

    先尝试整段 JSON；失败则从文本中扫描提取「含 message 键」的 JSON 对象——
    覆盖模型不老实、在 JSON 前加前言/代码块包裹（文字+JSON 混排）的情况。
    用 raw_decode 从每个 '{' 处尝试，能正确处理 JSON 字符串内的花括号。
    """
    # 1. 整段就是 JSON 对象
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    # 2. 混排：从每个 '{' 处尝试解析，取第一个含 "message" 键的对象
    decoder = json.JSONDecoder()
    idx = text.find('{')
    while idx != -1:
        try:
            obj, _ = decoder.raw_decode(text[idx:])
            if isinstance(obj, dict) and "message" in obj:
                return obj
        except json.JSONDecodeError:
            pass
        idx = text.find('{', idx + 1)
    return None


def _extract_structured_chat_payload(message: str) -> tuple[str, list[dict] | None]:
    """
    从模型最终文本中提取结构化诊断结果。

    兼容三种形式：
    1. 纯 JSON：{"message":"...","diagnosisItems":[...]}
    2. 文字+JSON 混排（模型在 JSON 前加了前言或 ```json``` 包裹）：提取其中含 message 的 JSON
    3. 普通文本：原样返回，不填 diagnosisItems
    """
    text = (message or "").strip()
    if not text:
        return message, None

    payload = _parse_chat_payload_json(text)
    # 必须是含 message 键的对象才视为结构化 payload；否则当普通文本原样返回
    # （避免把正文里偶然出现的无关 {..} 误当 payload 而丢失正文）
    if not isinstance(payload, dict) or "message" not in payload:
        return message, None

    raw_items = payload.get("diagnosisItems") or payload.get("diagnosis_items")
    if not isinstance(raw_items, list):
        return payload.get("message", message), None

    diagnosis_items = [
        _normalize_diagnosis_item(item)
        for item in raw_items
        if isinstance(item, dict)
    ]

    return payload.get("message", message), diagnosis_items or None

@asynccontextmanager
async def lifespan(application: FastAPI):
    runtime = _runtime_snapshot()
    logger.info(
        "[runtime] build_id=%s git_commit=%s dirty=%s worktree=%s documents=%s",
        runtime["build_id"],
        runtime["git_commit"],
        runtime["dirty"],
        runtime["worktree"],
        runtime["documents"],
    )
    # 启动：开启 MQ 消费者
    close_connection = None
    try:
        from mq.consumer import start_consumers
        from mq.connection import close_connection
        await start_consumers()
        logger.info("[启动] RabbitMQ 消费者已启动")
    except Exception as e:
        logger.warning("[启动] RabbitMQ 消费者启动失败（MQ不可用时降级为HTTP模式）: %s", e)
    yield
    # 关闭：断开 MQ 连接
    if close_connection is not None:
        await close_connection()

app = FastAPI(
    title="FixAgent AI Module",
    version="2.0.0",
    description="AI推理引擎：FixAgent 统一诊断 + 3层确定性校验",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, Any]:
    runtime = _runtime_snapshot()
    return {
        "status": "ok" if runtime["catalog_available"] else "degraded",
        "build_id": runtime["build_id"],
    }


@app.get("/ai/runtime")
async def runtime_info() -> dict[str, Any]:
    runtime = _runtime_snapshot()
    return {
        "status": "ok" if runtime["catalog_available"] else "degraded",
        "runtime": runtime,
    }

_settings = get_settings()
os.makedirs(_settings.local_file_storage_dir, exist_ok=True)
app.mount(_settings.file_public_base_url, StaticFiles(directory=_settings.local_file_storage_dir), name="rag_files")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def verify_api_token_middleware(request: Request, call_next):
    """全站鉴权中间件：所有 /ai/* 接口均需携带 X-Api-Token。
    放行：FastAPI 文档页、静态文件目录、CORS 预检（OPTIONS）。
    未配置 API_TOKEN 时服务处于锁闭状态，拒绝所有请求。
    """
    path = request.url.path
    if (
        request.method == "OPTIONS"
        or path in ("/health", "/docs", "/redoc", "/openapi.json")
        or path.startswith(_settings.file_public_base_url + "/")
    ):
        return await call_next(request)

    token = request.headers.get("x-api-token", "")
    if not _settings.api_token or token != _settings.api_token:
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

    return await call_next(request)


@app.post("/ai/domain-rules/upsert")
async def domain_rule_upsert(request: dict[str, Any]):
    try:
        return await upsert_domain_rule(request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except DomainRuleServiceError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ai/domain-rules/delete")
async def domain_rule_delete(request: dict[str, Any]):
    try:
        return await delete_domain_rule(request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except DomainRuleServiceError as e:
        raise HTTPException(status_code=500, detail=str(e))


_DOMAIN_RULE_INTENTS = {
    "fault_diagnosis",
    "maintenance_guidance",
    "procedure_planning",
}


def _execution_mode(metadata: dict | None) -> str:
    return (metadata or {}).get("execution_mode") or ""


def _is_deterministic_direct_output(output: AgentOutput) -> bool:
    """检查是否为确定性直接输出，应跳过 review。"""
    if output.metadata.get("deterministic_direct"):
        return True
    return _execution_mode(output.metadata) in {
        "knowledge_inventory_direct",
        "domain_rule_direct",
        "causal_follow_up_resolved",
        "insufficient_evidence_guard",
        "scope_guard",
    }


_KNOWLEDGE_EVIDENCE_TOOLS = {
    "knowledge_retrieval",
    DOMAIN_RULE_TOOL_NAME,
    "java_graph_diagnosis_path",
}


def _trace_tool_names(metadata: dict | None) -> set[str]:
    names: set[str] = set()
    for step in (metadata or {}).get("react_trace") or []:
        if not isinstance(step, dict):
            continue
        for call in step.get("tool_calls") or []:
            if isinstance(call, dict) and call.get("name"):
                names.add(str(call["name"]))
    return names


def _is_knowledge_output(output: AgentOutput) -> bool:
    names = set(output.tools_used or []) | _trace_tool_names(output.metadata)
    return bool(names & _KNOWLEDGE_EVIDENCE_TOOLS)


def _manual_bundle_from_trace(metadata: dict | None) -> dict[str, Any]:
    for step in reversed((metadata or {}).get("react_trace") or []):
        if not isinstance(step, dict):
            continue
        for call in reversed(step.get("tool_calls") or []):
            if not isinstance(call, dict) or call.get("name") != "knowledge_retrieval":
                continue
            payload = next(
                (call.get(key) for key in ("result_data", "data", "result") if call.get(key) is not None),
                None,
            )
            if isinstance(payload, dict):
                nested = payload.get("data")
                if isinstance(nested, (dict, list)):
                    payload = nested
            if isinstance(payload, dict) and any(
                key in payload for key in ("aspect_support", "coverage_status", "conflict_eligible")
            ):
                return dict(payload)
            if isinstance(payload, list):
                for item in payload:
                    if not isinstance(item, dict):
                        continue
                    bundle = (item.get("metadata") or {}).get("evidence_bundle")
                    if isinstance(bundle, dict):
                        return dict(bundle)
    return {}


def _bundle_for_knowledge_output(output: AgentOutput, ledger: EvidenceLedger) -> dict[str, Any]:
    bundle = _manual_bundle_from_trace(output.metadata)
    direct_bundle = _direct_answer_evidence_bundle(output.metadata, bundle)
    if direct_bundle is not None:
        return direct_bundle
    if bundle:
        return bundle
    qualified_ids = [
        str(entry.get("evidence_id"))
        for entry in ledger.entries
        if entry.get("qualification") == "qualified"
        and not (
            entry.get("source_type") == "manual"
            and str((entry.get("source") or {}).get("chunk_type") or "")
            in {"image", "image_summary"}
        )
    ]
    return {
        "aspect_support": [{
            "aspect_id": "knowledge-answer",
            "aspect_text": "当前问题",
            "supported": bool(qualified_ids),
            "evidence_ids": qualified_ids,
        }],
        "missing_aspect_ids": [] if qualified_ids else ["knowledge-answer"],
        "conflict_eligible": [],
        "capabilities": {
            "may_cite_manual": True,
            "may_offer_generic_guidance": False,
        },
    }


def _direct_answer_evidence_bundle(metadata: dict, original: dict) -> dict[str, Any] | None:
    if metadata.get("scope_decision", {}).get("status") == "out_of_scope":
        return None
    capabilities = original.get("capabilities") if isinstance(original.get("capabilities"), dict) else {}

    records: list[dict[str, Any]] = []
    for step in metadata.get("react_trace") or []:
        if not isinstance(step, dict):
            continue
        for call in step.get("tool_calls") or []:
            if not isinstance(call, dict) or call.get("name") != "knowledge_retrieval":
                continue
            arguments = call.get("arguments") or {}
            if arguments.get("source") not in {"section_text_lookup", "section_table_lookup"}:
                continue
            payload = call.get("result_data")
            if isinstance(payload, list):
                records.extend(item for item in payload if isinstance(item, dict))
    direct_ids = {canonical_manual_chunk_id(record) for record in records}
    direct_ids.discard("")
    if not direct_ids:
        return None
    has_exact_section_match = any(
        bool((record.get("metadata") or {}).get("original_title_match"))
        for record in records
    )
    selected_document_id = str(
        ((metadata.get("route_plan") or {}).get("selected_document_id") or "")
    ).strip()
    has_route_scoped_structural_lookup = bool(
        _route_plan_authorizes_structural_lookup(metadata)
        and records
        and all(
            str((record.get("metadata") or {}).get("document_id") or "").strip()
            == selected_document_id
            and (record.get("metadata") or {}).get(_STRUCTURAL_RECOVERY_LOOKUP_SOURCE)
            == "section_text_lookup"
            for record in records
        )
    )
    has_trusted_section_lookup = (
        has_exact_section_match or has_route_scoped_structural_lookup
    )
    has_complete_exact_table = (
        metadata.get("deterministic_table_answer") is True
        and metadata.get("_deterministic_answer_table_complete") is True
        and has_exact_section_match
    )
    direct_text = "\n".join(
        "\n".join(filter(None, (
            str((record.get("metadata") or {}).get("section_title") or ""),
            str(record.get("content") or ""),
        )))
        for record in records
    )
    original_query = str(
        metadata.get("original_user_message")
        or metadata.get("user_message")
        or ""
    )
    query_supported_by_direct_records = _direct_manual_text_supports_query(
        direct_text,
        original_query,
    )
    query_supported_by_trusted_section = (
        has_trusted_section_lookup and query_supported_by_direct_records
    )
    direct_records_authorized = (
        query_supported_by_direct_records
        and (
            capabilities.get("may_cite_manual") is not False
            or has_trusted_section_lookup
        )
    )
    supported_aspects = [
        row
        for row in original.get("aspect_support") or []
        if isinstance(row, dict) and row.get("supported")
    ]
    original_coverage = str(original.get("coverage_status") or "")
    if (
        original_coverage in {"partial", "unsupported"}
        and not supported_aspects
        and not has_complete_exact_table
        and not direct_records_authorized
    ):
        return None
    if (
        capabilities.get("may_cite_manual") is False
        and not has_complete_exact_table
        and not query_supported_by_trusted_section
    ):
        return None
    relevant_conflicts = []
    for conflict in original.get("conflict_eligible") or []:
        if not isinstance(conflict, dict):
            continue
        candidate_ids = {str(item) for item in conflict.get("candidate_ids") or []}
        for alternative in conflict.get("alternatives") or []:
            if isinstance(alternative, dict):
                candidate_ids.update(str(item) for item in alternative.get("candidate_ids") or [])
        if candidate_ids & direct_ids:
            relevant_conflicts.append(conflict)

    aspect_by_id = {
        str(aspect.get("aspect_id") or ""): aspect
        for aspect in original.get("aspect_support") or []
        if isinstance(aspect, dict) and aspect.get("aspect_id")
    }
    unresolved_missing_ids = [
        str(aspect_id)
        for aspect_id in original.get("missing_aspect_ids") or []
        if not _direct_manual_text_supports_aspect(
            direct_text,
            str((aspect_by_id.get(str(aspect_id)) or {}).get("aspect_text") or ""),
        )
        and not query_supported_by_trusted_section
    ]
    partial = (
        original_coverage == "partial"
        and bool(unresolved_missing_ids)
        and not has_complete_exact_table
    )
    return {
        "coverage_status": "conflict" if relevant_conflicts else ("partial" if partial else "complete"),
        "aspect_support": list(original.get("aspect_support") or []) if partial else [{
            "aspect_id": "direct-manual-answer",
            "aspect_text": "本次直取手册答案",
            "supported": True,
            "evidence_ids": sorted(direct_ids),
        }],
        "missing_aspect_ids": unresolved_missing_ids if partial else [],
        "conflict_eligible": relevant_conflicts,
        "capabilities": {
            **capabilities,
            "may_cite_manual": True,
            "may_offer_generic_guidance": False,
        },
    }


def _direct_manual_text_supports_aspect(direct_text: str, aspect_text: str) -> bool:
    evidence = _compact_inventory_text(direct_text)
    aspect = _compact_inventory_text(aspect_text)
    if not evidence or not aspect:
        return False
    if aspect in evidence:
        return True
    action = _manual_query_action(aspect_text)
    target = _manual_action_target(aspect_text, action)
    compact_target = _compact_inventory_text(target)
    if not action or len(compact_target) < 2 or compact_target not in evidence:
        return False
    return any(word in evidence for word in _MANUAL_ACTION_SYNONYMS.get(action, ()))


def _manual_anchor_supported_by_text(anchor: str, evidence: str) -> bool:
    """Match a query anchor without requiring OCR/source modifiers to be absent."""
    if anchor in evidence:
        return True
    if len(anchor) < 4:
        return False
    bigrams = {anchor[index:index + 2] for index in range(len(anchor) - 1)}
    if not bigrams:
        return False
    matched = sum(1 for gram in bigrams if gram in evidence)
    return matched >= 3 and matched / len(bigrams) >= 0.6


def _direct_manual_text_supports_query(direct_text: str, query: str) -> bool:
    evidence = _compact_inventory_text(direct_text)
    if not evidence or not _compact_inventory_text(query):
        return False
    if _manual_answer_should_refuse_detail_query(query, [{"content": direct_text}]):
        return False

    anchors = _manual_query_anchor_terms(query)
    minimal_anchors = [
        anchor
        for anchor in anchors
        if not any(
            other != anchor and len(other) < len(anchor) and other in anchor
            for other in anchors
        )
    ]
    if not minimal_anchors or not all(
        _manual_anchor_supported_by_text(anchor, evidence)
        for anchor in minimal_anchors
    ):
        return False

    action = _manual_query_action(query)
    if action and not any(
        word in evidence for word in _MANUAL_ACTION_SYNONYMS.get(action, ())
    ):
        return False
    return True


def _finalize_knowledge_output(
    query: str,
    output: AgentOutput,
    *,
    candidate_message: str | None = None,
) -> AgentOutput:
    """Run the last visible knowledge answer through one evidence-plan audit."""
    if candidate_message is not None:
        output.message = candidate_message
    if not _is_knowledge_output(output):
        return output

    ledger = EvidenceLedger.from_react_trace(output.metadata)
    evidence_bundle = _bundle_for_knowledge_output(output, ledger)
    plan = build_response_plan(query, evidence_bundle, ledger)
    has_qualified_source_evidence = any(
        entry.get("qualification") == "qualified"
        and entry.get("source_type") == "manual"
        and str((entry.get("source") or {}).get("chunk_type") or "")
        not in {"image", "image_summary"}
        for entry in ledger.entries
    )
    evidence_rendered = (
        output.metadata.get("_deterministic_answer_mode") == "evidence_rendered"
        and (
            _has_registered_answer_evidence(output.metadata)
            or has_qualified_source_evidence
        )
    )
    audited = finalize_response(plan, output.message, evidence_rendered=evidence_rendered)
    output.message = audited.answer
    output.metadata.update(plan.to_metadata())
    output.metadata.setdefault("scope_decision", {"status": "unknown"})
    intent_data = output.metadata.get("intent_decision")
    if isinstance(intent_data, dict) and intent_data.get("intent"):
        final_evidence = dict(evidence_bundle)
        final_evidence["coverage_status"] = plan.coverage_status
        output.metadata["response_policy"] = derive_response_policy(
            IntentDecision(**intent_data),
            output.metadata.get("scope_decision") or {},
            final_evidence,
            query=query,
        ).to_dict()
    output.metadata["response_audit"] = {
        "passed": audited.passed,
        "violations": list(audited.violations),
        "used_fallback": audited.used_fallback,
        "mode": "evidence_rendered" if evidence_rendered else "generated",
    }
    return output


def _attach_stream_done_metadata(event: dict[str, Any], metadata: dict | None) -> None:
    diagnostics = {
        key: (metadata or {}).get(key)
        for key in (
            "scope_decision",
            "coverage_status",
            "response_plan_id",
            "evidence_ledger_digest",
            "pending_clarification",
            "_deterministic_answer_evidence_pages",
            "_deterministic_answer_document_ids",
            "_deterministic_answer_section_title",
            "_deterministic_answer_section_ids",
            "_deterministic_answer_table_complete",
        )
        if key in (metadata or {})
    }
    if diagnostics:
        event.setdefault("data", {}).setdefault("metadata", {}).update(diagnostics)


def _initialized_or_injected_vector_service(*, initialize: bool = False):
    try:
        from services.knowledge import vector_service as vector_service_module

        service = getattr(vector_service_module, "_vector_service", None)
        if service is not None:
            return service
        getter = getattr(vector_service_module, "get_vector_service", None)
        if callable(getter) and (
            initialize or getattr(getter, "__name__", "") != "get_vector_service"
        ):
            return getter()
    except Exception:
        pass
    return None


def _pending_clarification_redis_client():
    service = _initialized_or_injected_vector_service()
    return getattr(service, "redis", None) if service is not None else None


def _restore_trusted_pending_context(session_id: str, context: dict) -> dict:
    client_pending = context.get("pending_clarification")
    trusted = load_pending_clarification(
        session_id,
        client_pending=client_pending if isinstance(client_pending, Mapping) else None,
        redis_client=_pending_clarification_redis_client(),
    )
    if trusted:
        context["pending_clarification"] = trusted
    elif isinstance(client_pending, Mapping) and client_pending.get("kind") == "evidence_conflict":
        context.pop("pending_clarification", None)
    return context


def _sync_pending_clarification_state(session_id: str, metadata: Mapping[str, Any] | None) -> None:
    pending = (metadata or {}).get("pending_clarification")
    redis_client = _pending_clarification_redis_client()
    if isinstance(pending, Mapping) and pending.get("kind") == "evidence_conflict":
        if pending.get("status") == "awaiting_answer":
            remember_pending_clarification(session_id, pending, redis_client=redis_client)
        elif pending.get("status") == "resolved":
            clear_pending_clarification(session_id, redis_client=redis_client)


def _register_direct_manual_evidence(
    metadata: dict,
    records: list[Any],
    source_name: str,
) -> None:
    normalized: list[dict[str, Any]] = []
    for raw in records or []:
        if hasattr(raw, "model_dump"):
            record = raw.model_dump()
        elif isinstance(raw, dict):
            record = dict(raw)
        else:
            continue
        item_metadata = dict(record.get("metadata") or {})
        document_id = str(item_metadata.get("document_id") or record.get("document_id") or "").strip()
        chunk_id = str(
            item_metadata.get("chunk_id")
            or record.get("chunk_id")
            or record.get("id")
            or record.get("doc_id")
            or ""
        ).strip()
        if not document_id or not chunk_id:
            continue
        item_metadata.update({
            "document_id": document_id,
            "chunk_id": chunk_id,
            "qualification": "qualified",
        })
        content = (
            record.get("content")
            or record.get("text")
            or item_metadata.get("caption")
            or item_metadata.get("image_summary")
            or ""
        )
        normalized.append({
            **record,
            "id": chunk_id,
            "content": str(content),
            "metadata": item_metadata,
        })
    if not normalized:
        return
    trace = metadata.setdefault("react_trace", [])
    trace.append({
        "iteration": len(trace) + 1,
        "action": "direct_evidence_lookup",
        "tool_calls": [{
            "name": "knowledge_retrieval",
            "arguments": {"source": source_name},
            "result_data": normalized,
            "result_summary": f"{source_name}:{len(normalized)}",
        }],
    })


def _has_registered_answer_evidence(metadata: dict) -> bool:
    answer_sources = {"section_text_lookup", "section_table_lookup"}
    for step in metadata.get("react_trace") or []:
        if not isinstance(step, dict):
            continue
        for call in step.get("tool_calls") or []:
            if not isinstance(call, dict) or call.get("name") != "knowledge_retrieval":
                continue
            arguments = call.get("arguments") or {}
            if arguments.get("source") not in answer_sources:
                continue
            if call.get("result_data"):
                return True
    return False


def _should_try_domain_rule(request: ChatRequest, input_data: AgentInput) -> bool:
    if request.images:
        return False
    context = input_data.context or {}
    if context.get("disable_domain_rule_engine") or context.get("force_react"):
        return False
    intent_decision = context.get("intent_decision") if isinstance(context.get("intent_decision"), dict) else {}
    intent = intent_decision.get("intent")
    mode = getattr(request.mode, "value", request.mode)
    return intent in _DOMAIN_RULE_INTENTS or mode in {"diagnosis", "guidance", "full"}


def _domain_rule_trace(match: dict[str, Any]) -> list[dict[str, Any]]:
    rule = match.get("rule") or {}
    return [
        {
            "iteration": 0,
            "thought": "domain rule direct hit",
            "tool_calls": [
                {
                    "name": DOMAIN_RULE_TOOL_NAME,
                    "args": {
                        "rule_id": rule.get("rule_id"),
                        "rule_code": rule.get("rule_code"),
                    },
                    "result_data": {
                        "message": match.get("message", ""),
                        "rule": rule,
                        "status": match.get("status"),
                        "matched_symptom_keys": match.get("matched_symptom_keys", []),
                        "evidence_sources": match.get("evidence_sources", []),
                        "score": match.get("score"),
                    },
                }
            ],
        }
    ]


def _domain_rule_tool_items(match: dict[str, Any]) -> list[dict[str, Any]]:
    rule = match.get("rule") or {}
    return [
        {
            "title": rule.get("title") or "专家规则",
            "content": rule.get("conclusion") or match.get("message", ""),
            "type": "rule",
            "score": match.get("score"),
            "metadata": {
                "doc_id": rule.get("doc_id"),
                "rule_id": rule.get("rule_id"),
                "rule_code": rule.get("rule_code"),
                "matched_symptom_keys": match.get("matched_symptom_keys", []),
            },
        }
    ]


async def _try_domain_rule_direct(request: ChatRequest, input_data: AgentInput) -> AgentOutput | None:
    if not _should_try_domain_rule(request, input_data):
        return None
    started = time.time()
    try:
        context = input_data.context or {}
        scope = context.get("retrieval_scope") or {}
        decision = context.get("scope_decision") or {}
        match = await match_domain_rule(
            input_data.user_message,
            device_type=decision.get("device_type") or scope.get("device_type"),
            document_id=scope.get("document_id"),
        )
    except DomainRuleServiceError as e:
        logger.warning("[domain_rule] direct match skipped: %s", e)
        return None
    if not match:
        return None

    latency_ms = int((time.time() - started) * 1000)
    metadata = {
        "execution_mode": "domain_rule_direct",
        "confidence_source": "rule",
        "confidence_label": "确定",
        "domain_rule": match.get("rule"),
        "domain_rule_match": match,
        "evidence_sources": match.get("evidence_sources", []),
        "scope_decision": (input_data.context or {}).get("scope_decision") or {},
        "react_trace": _domain_rule_trace(match),
        "verification": {
            "grounding": {"unverified_count": 0},
            "graph": {"unverified_count": 0},
            "safety": {"missing_count": 0},
        },
    }
    output = AgentOutput(
        agent_name="fix_agent",
        message=match.get("message", ""),
        intention="fault_diagnosis",
        tools_used=[DOMAIN_RULE_TOOL_NAME],
        metadata=metadata,
        latency_ms=latency_ms,
        raw_response=match,
    )
    return _finalize_knowledge_output(input_data.user_message, output)


async def _stream_direct_agent_output(output: AgentOutput):
    import asyncio as _asyncio

    match = output.metadata.get("domain_rule_match") or {}
    visible_message = strip_user_visible_emojis(output.message)
    yield f"data: {json_dumps({'event': 'status', 'data': {'stage': '规则引擎命中，正在生成确定性诊断', 'mode': 'domain_rule'}})}\n\n"
    yield f"data: {json_dumps({'event': 'tool', 'data': {'tool': DOMAIN_RULE_TOOL_NAME}})}\n\n"
    yield f"data: {json_dumps({'event': 'tool_result', 'data': {'tool': DOMAIN_RULE_TOOL_NAME, 'text': visible_message, 'items': _domain_rule_tool_items(match)}})}\n\n"

    for i, char in enumerate(visible_message):
        yield f"data: {json_dumps({'event': 'token', 'data': {'content': char}})}\n\n"
        if i % 15 == 0:
            await _asyncio.sleep(0)

    yield f"data: {json_dumps({'event': 'verification', 'data': {'has_issues': False, 'summary': {'grounding_unverified': 0, 'graph_unverified': 0, 'safety_missing': 0}}})}\n\n"
    yield f"data: {json_dumps({'event': 'done', 'data': {'tools_used': output.tools_used, 'latency_ms': output.latency_ms, 'domainRule': output.metadata.get('domain_rule'), 'confidenceSource': output.metadata.get('confidence_source'), 'evidenceSources': output.metadata.get('evidence_sources', []), 'metadata': output.metadata}})}\n\n"


def _scope_gate_required(request: ChatRequest, input_data: AgentInput) -> bool:
    intent = (input_data.context or {}).get("intent_decision") or {}
    policy = intent.get("policy") if isinstance(intent.get("policy"), dict) else {}
    return bool(
        intent.get("requires_knowledge_retrieval")
        or policy.get("requires_knowledge_retrieval")
        or request.mode in {AgentMode.RETRIEVAL, AgentMode.DIAGNOSIS, AgentMode.GUIDANCE, AgentMode.FULL}
        or _is_high_risk_rag_question(request.message or "")
    )


def _try_scope_guard(request: ChatRequest, input_data: AgentInput) -> AgentOutput | None:
    decision = (input_data.context or {}).get("scope_decision") or {}
    policy = (input_data.context or {}).get("response_policy") or {}
    if policy and policy.get("mode") != "BLOCKED_SCOPE":
        return None
    if decision.get("status") != OUT_OF_SCOPE or not _scope_gate_required(request, input_data):
        return None
    message = format_scope_guard_message(decision)
    trace = [{
        "iteration": 0,
        "action": "scope_decision",
        "tool_calls": [{
            "name": "scope_gate",
            "arguments": {
                "document_id": decision.get("requested_document_id"),
                "device_type": decision.get("requested_device_type"),
            },
            "result_data": decision,
            "result_summary": decision.get("reason"),
        }],
    }]
    return AgentOutput(
        agent_name="fix_agent",
        message=message,
        intention="scope_guard",
        tools_used=["scope_gate"],
        metadata={
            "execution_mode": "scope_guard",
            "deterministic_direct": True,
            "scope_decision": decision,
            "coverage_status": "unsupported",
            "blocked_for_insufficient_evidence": True,
            "insufficient_evidence_reason": decision.get("reason"),
            "react_trace": trace,
        },
        raw_response={"scope_decision": decision},
    )


async def _try_response_policy_direct(request: ChatRequest, input_data: AgentInput) -> AgentOutput | None:
    """Handle non-retrieval answer modes before ReAct can call knowledge tools."""
    policy = (input_data.context or {}).get("response_policy") or {}
    mode = policy.get("mode")
    if mode not in {"GENERAL_AI", "MAINTENANCE_AI_FALLBACK", "INSUFFICIENT_EVIDENCE"}:
        return None
    if mode == "INSUFFICIENT_EVIDENCE":
        return AgentOutput(
            agent_name="fix_agent",
            message="当前资料未说明所问的具体参数或操作步骤，因此无法可靠确认。请补充对应手册或其他可验证资料后再核对。",
            intention=(input_data.context or {}).get("intention"),
            metadata={"execution_mode": "insufficient_evidence_direct", "deterministic_direct": True, "response_policy": policy, "scope_decision": (input_data.context or {}).get("scope_decision") or {}},
            raw_response={"mode": mode},
        )
    intent = (input_data.context or {}).get("intent_decision") or {}
    if mode == "GENERAL_AI" and intent.get("chat_subtype") == "model_information":
        model_name = get_settings().llm_model
        return AgentOutput(
            agent_name="fix_agent",
            message=f"当前检修 AI 助手配置使用的对话模型是 {model_name}。具体模型可能随部署配置调整。",
            intention=intent.get("intent"),
            metadata={"execution_mode": "model_information_direct", "deterministic_direct": True, "response_policy": policy, "scope_decision": (input_data.context or {}).get("scope_decision") or {}, "source_type": "runtime_config"},
            raw_response={"model": model_name},
        )
    if mode == "GENERAL_AI":
        system = (
            "你是检修 AI 助手的通用对话模块。当前问题不是设备手册检索问题，"
            "请直接使用通用知识自然回答，不要提到知识库、手册缺失或设备范围。不要输出 JSON。"
            + USER_VISIBLE_PLAIN_TEXT_RULES
        )
        temperature = select_style(
            policy.get("style_profile", "general_ai"), request.session_id, str((input_data.context or {}).get("turn_ts", ""))
        ).temperature
    else:
        system = (
            "你是检修 AI 助手。当前知识库没有找到与用户指定设备对应的文档。"
            "请说明知识库没有该设备对应文档、以下内容来自 AI、仅供参考，然后给出低风险通用分析。"
            "不要伪装成手册结论，不要编造精确参数或高风险步骤，不要引用法规编号、手册页码或未经当前知识库核验的标准编号。"
            + USER_VISIBLE_PLAIN_TEXT_RULES
        )
        temperature = select_style(
            policy.get("style_profile", "maintenance_ai"), request.session_id, str((input_data.context or {}).get("turn_ts", ""))
        ).temperature
    messages = [{"role": "system", "content": system}]
    messages.extend(
        {"role": item["role"], "content": str(item["content"])}
        for item in input_data.conversation_history or []
        if isinstance(item, dict) and item.get("role") in {"user", "assistant"} and item.get("content")
    )
    messages.append({"role": "user", "content": input_data.user_message})
    llm_service = get_llm_service()
    response = await llm_service.chat(messages, temperature=temperature, max_tokens=1200)
    raw_message = response.get("content", "") if isinstance(response, dict) else str(response or "")
    raw_message, style_regenerated = await regenerate_user_visible_text(
        llm_service,
        raw_message,
        max_tokens=1200,
    )
    message = _clean_fallback_text(raw_message)
    fallback_safety_filters: list[str] = []
    if mode == "MAINTENANCE_AI_FALLBACK":
        message, fallback_safety_filters = _sanitize_maintenance_ai_fallback(message)
    if mode == "MAINTENANCE_AI_FALLBACK":
        message = _ensure_maintenance_ai_disclaimer(message)
    return AgentOutput(
        agent_name="fix_agent",
        message=message,
        intention=(input_data.context or {}).get("intention"),
        metadata={"execution_mode": "general_ai_direct" if mode == "GENERAL_AI" else "maintenance_ai_fallback_direct", "deterministic_direct": True, "response_policy": policy, "scope_decision": (input_data.context or {}).get("scope_decision") or {}, "source_type": "ai", "disclaimer": mode == "MAINTENANCE_AI_FALLBACK", "fallback_safety_filters": fallback_safety_filters, "style_regenerated": style_regenerated},
        raw_response=response if isinstance(response, dict) else {"content": message},
    )


async def _try_post_retrieval_ai_fallback(
    request: ChatRequest,
    input_data: AgentInput,
    audited_output: AgentOutput,
) -> AgentOutput | None:
    """Generate safe general guidance after retrieval proves no usable evidence.

    The retrieval and graph trace remains attached for audit, but no manual
    citation, page binding, or evidence image is allowed to survive into an
    answer whose visible source is the model's general knowledge.
    """
    policy = (
        audited_output.metadata.get("response_policy")
        if isinstance(audited_output.metadata.get("response_policy"), dict)
        else {}
    )
    if (
        audited_output.metadata.get("coverage_status") != "unsupported"
        or policy.get("mode") != "MAINTENANCE_AI_FALLBACK"
        or policy.get("allow_ai_fallback") is not True
        or audited_output.metadata.get("blocked_for_document_isolation")
        or not _is_knowledge_output(audited_output)
    ):
        return None

    fallback_context = dict(input_data.context or {})
    fallback_context["response_policy"] = dict(policy)
    fallback_context["scope_decision"] = (
        audited_output.metadata.get("scope_decision")
        or fallback_context.get("scope_decision")
        or {"status": "unknown"}
    )
    fallback_input = input_data.model_copy(update={"context": fallback_context}, deep=True)
    generated = await _try_response_policy_direct(request, fallback_input)
    if generated is None:
        return None

    original_metadata = dict(audited_output.metadata)
    original_trace = list(original_metadata.get("react_trace") or [])
    retrieval_audit = original_metadata.get("response_audit")
    merged_metadata = {
        **original_metadata,
        **generated.metadata,
        "execution_mode": "maintenance_ai_fallback_after_retrieval",
        "coverage_status": "unsupported",
        "react_trace": original_trace,
        "evidence_images": [],
        "_deterministic_answer_evidence_pages": [],
        "_deterministic_answer_document_ids": [],
        "_deterministic_answer_section_ids": [],
        "_deterministic_answer_section_title": "",
        "_deterministic_answer_table_complete": False,
    }
    if retrieval_audit is not None:
        merged_metadata["retrieval_response_audit"] = retrieval_audit
    return AgentOutput(
        agent_name=generated.agent_name,
        message=generated.message,
        intention=generated.intention or audited_output.intention,
        tools_used=list(audited_output.tools_used or []),
        metadata=merged_metadata,
        latency_ms=audited_output.latency_ms + generated.latency_ms,
        raw_response=generated.raw_response,
    )


async def _finalize_knowledge_output_with_fallback(
    request: ChatRequest,
    input_data: AgentInput,
    output: AgentOutput,
    *,
    candidate_message: str | None = None,
) -> AgentOutput:
    """Share the same evidence audit and post-retrieval fallback in both APIs."""
    audited = _finalize_knowledge_output(
        request.message,
        output,
        candidate_message=candidate_message,
    )
    fallback = await _try_post_retrieval_ai_fallback(request, input_data, audited)
    return fallback or audited


async def _try_route_plan_direct(request: ChatRequest, input_data: AgentInput) -> AgentOutput | None:
    """Execute deterministic route handlers before any generative answer path."""
    payload = (input_data.context or {}).get("route_plan")
    if not isinstance(payload, dict):
        return None
    try:
        plan = RoutePlan.from_dict(payload)
    except (TypeError, ValueError):
        logger.exception("[routing] invalid route plan session=%s", request.session_id)
        return None
    inventory_tool = (
        get_knowledge_inventory_tool()
        if plan.action == RouteAction.KNOWLEDGE_INVENTORY
        else None
    )
    execution = await RouteExecutor().execute(plan, inventory_tool=inventory_tool)
    if execution is None:
        return None
    pending_selection = execution.metadata.get("pending_document_selection")
    if isinstance(pending_selection, dict):
        remember_pending_document_selection(
            request.session_id,
            pending_selection,
        )
    return AgentOutput(
        agent_name="fix_agent",
        message=execution.message,
        intention=plan.intent,
        tools_used=list(execution.tools_used),
        metadata={
            **execution.metadata,
            "route_plan": plan.to_dict(),
            "response_policy": (input_data.context or {}).get("response_policy") or {},
            "scope_decision": (input_data.context or {}).get("scope_decision") or {},
        },
        raw_response={"route_action": plan.action.value},
    )


def _enforce_route_document_gate(output: AgentOutput, input_data: AgentInput) -> AgentOutput:
    """Block a grounded answer if any retrieval evidence belongs to another document."""
    payload = (input_data.context or {}).get("route_plan")
    if not isinstance(payload, dict) or payload.get("action") != RouteAction.GROUNDED_RETRIEVAL.value:
        return output
    selected_document_id = str(payload.get("selected_document_id") or "")
    audit = EvidenceDocumentGate().audit(
        output.metadata,
        selected_document_id=selected_document_id,
    )
    output.metadata["document_evidence_gate"] = audit.to_dict()
    if audit.accepted:
        return output
    logger.error(
        "[routing][document_gate] selected=%s foreign=%s evidence=%s",
        selected_document_id,
        audit.foreign_document_ids,
        audit.evidence_document_ids,
    )
    output.message = (
        "本次检索结果混入了非当前选定文档的证据，系统已阻止生成答案。"
        "请重新发起查询，系统将只使用当前选定文档。"
    )
    output.metadata.update({
        "execution_mode": "document_evidence_gate_blocked",
        "deterministic_direct": True,
        "blocked_for_document_isolation": True,
        "react_trace": [],
        "evidence_images": [],
        "_deterministic_answer_document_ids": [],
    })
    return output


async def _stream_scope_guard_output(output: AgentOutput):
    import asyncio as _asyncio

    yield f"data: {json_dumps({'event': 'status', 'data': {'stage': '正在核对设备与手册范围', 'mode': 'scope_guard'}})}\n\n"
    visible_message = strip_user_visible_emojis(output.message)
    for index, char in enumerate(visible_message):
        yield f"data: {json_dumps({'event': 'token', 'data': {'content': char}})}\n\n"
        if index % 15 == 0:
            await _asyncio.sleep(0)
    yield f"data: {json_dumps({'event': 'verification', 'data': {'has_issues': False, 'summary': {'grounding_unverified': 0, 'graph_unverified': 0, 'safety_missing': 0}}})}\n\n"
    yield f"data: {json_dumps({'event': 'done', 'data': {'tools_used': output.tools_used, 'latency_ms': output.latency_ms, 'metadata': output.metadata}})}\n\n"


async def _stream_policy_direct_output(output: AgentOutput):
    import asyncio as _asyncio

    visible_message = strip_user_visible_emojis(output.message)
    for index, char in enumerate(visible_message):
        event = {"event": "token", "data": {"content": char}}
        yield "data: " + json_dumps(event) + chr(10) + chr(10)
        if index % 15 == 0:
            await _asyncio.sleep(0)
    done = {"event": "done", "data": {"tools_used": [], "latency_ms": output.latency_ms, "metadata": output.metadata}}
    yield "data: " + json_dumps(done) + chr(10) + chr(10)


def _causal_follow_up_trace(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "iteration": 0,
            "thought": "causal follow-up rerank",
            "tool_calls": [
                {
                    "name": FOLLOW_UP_TOOL_NAME,
                    "args": {
                        "question": result.get("question"),
                        "selected_option": (result.get("selectedOption") or {}).get("id"),
                    },
                    "result_data": result,
                }
            ],
        }
    ]


def _causal_follow_up_tool_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for item in result.get("hypotheses") or []:
        items.append(
            {
                "title": item.get("rootCause") or "候选根因",
                "content": item.get("distinguishingFeature") or item.get("suggestedCheck") or "",
                "type": "causal_follow_up",
                "score": item.get("confidence"),
                "metadata": {
                    "faultPart": item.get("faultPart"),
                    "candidateId": item.get("id"),
                },
            }
        )
    return items


async def _try_causal_follow_up_resolution(
    request: ChatRequest,
    input_data: AgentInput,
) -> AgentOutput | None:
    started = time.time()
    context = input_data.context or {}
    pending = context.get("pending_clarification")
    is_awaiting = isinstance(pending, Mapping) and pending.get("status") == "awaiting_answer"
    if is_awaiting and pending.get("kind") == "evidence_conflict":
        resolved_conflict = resolve_pending_clarification(context, input_data.user_message)
        if resolved_conflict:
            clear_pending_clarification(
                request.session_id,
                redis_client=_pending_clarification_redis_client(),
            )
            restored_query = str(resolved_conflict.get("original_query") or request.message).strip()
            selected_refs = [
                str(item)
                for item in resolved_conflict.get("selected_evidence_refs") or []
                if str(item).strip()
            ]
            return AgentOutput(
                agent_name="fix_agent",
                message=format_pending_resolution(resolved_conflict),
                intention="knowledge_query",
                tools_used=[FOLLOW_UP_TOOL_NAME],
                metadata={
                    "execution_mode": "evidence_conflict_resolved",
                    "deterministic_direct": True,
                    "confidence_source": "user_clarification",
                    "pending_clarification": resolved_conflict,
                    "diagnostic_follow_up": resolved_conflict,
                    "restored_query": restored_query,
                    "selected_evidence_refs": selected_refs,
                    "evidence_constraints": {
                        "allowed_evidence_refs": selected_refs,
                        "selection_source": "user_clarification",
                    },
                    "user_message": restored_query,
                    "original_user_message": restored_query,
                    "clarification_answer": input_data.user_message,
                },
                latency_ms=int((time.time() - started) * 1000),
                raw_response=resolved_conflict,
            )
        return AgentOutput(
            agent_name="fix_agent",
            message=str(pending.get("question") or "请从给出的选项中确认一个答案。"),
            intention="knowledge_query",
            tools_used=[FOLLOW_UP_TOOL_NAME],
            metadata={
                "execution_mode": "clarification_repeat",
                "deterministic_direct": True,
                "pending_clarification": dict(pending),
                "diagnostic_follow_up": dict(pending),
                "restored_query": str(pending.get("original_query") or ""),
                "clarification_answer": input_data.user_message,
            },
            latency_ms=int((time.time() - started) * 1000),
            raw_response=dict(pending),
        )
    resolved = resolve_follow_up(context, input_data.user_message)
    if not resolved:
        if is_awaiting:
            return AgentOutput(
                agent_name="fix_agent",
                message=str(pending.get("question") or "请从给出的选项中确认一个答案。"),
                intention="fault_diagnosis",
                tools_used=[FOLLOW_UP_TOOL_NAME],
                metadata={
                    "execution_mode": "clarification_repeat",
                    "deterministic_direct": True,
                    "pending_clarification": dict(pending),
                    "diagnostic_follow_up": dict(pending),
                },
                latency_ms=int((time.time() - started) * 1000),
                raw_response=dict(pending),
            )
        return None

    message = format_resolution_message(resolved)
    metadata = {
        "execution_mode": "causal_follow_up_resolved",
        "confidence_source": "causal_follow_up",
        "confidence_label": "追问收敛",
        "diagnostic_follow_up": resolved,
        "pending_clarification": resolved,
        "react_trace": _causal_follow_up_trace(resolved),
        "verification": {
            "grounding": {"unverified_count": 0},
            "graph": {"unverified_count": 0},
            "safety": {"missing_count": 0},
        },
        "user_message": input_data.user_message,
        "original_user_message": request.message,
    }
    return AgentOutput(
        agent_name="fix_agent",
        message=message,
        intention="fault_diagnosis",
        tools_used=[FOLLOW_UP_TOOL_NAME],
        metadata=metadata,
        latency_ms=int((time.time() - started) * 1000),
        raw_response=resolved,
    )


async def _stream_causal_follow_up_output(output: AgentOutput):
    import asyncio as _asyncio

    result = output.metadata.get("diagnostic_follow_up") or {}
    visible_message = strip_user_visible_emojis(output.message)
    yield f"data: {json_dumps({'event': 'status', 'data': {'stage': '已根据追问回答重排候选根因', 'mode': 'causal_follow_up'}})}\n\n"
    yield f"data: {json_dumps({'event': 'tool', 'data': {'tool': FOLLOW_UP_TOOL_NAME}})}\n\n"
    yield f"data: {json_dumps({'event': 'tool_result', 'data': {'tool': FOLLOW_UP_TOOL_NAME, 'text': visible_message, 'items': _causal_follow_up_tool_items(result)}})}\n\n"

    for i, char in enumerate(visible_message):
        yield f"data: {json_dumps({'event': 'token', 'data': {'content': char}})}\n\n"
        if i % 15 == 0:
            await _asyncio.sleep(0)

    done_data = {
        "tools_used": output.tools_used,
        "latency_ms": output.latency_ms,
        "diagnosticFollowUp": result,
        "metadata": output.metadata,
    }
    if result.get("diagnosisItems"):
        done_data["diagnosisItems"] = result["diagnosisItems"]
    yield f"data: {json_dumps({'event': 'verification', 'data': {'has_issues': False, 'summary': {'grounding_unverified': 0, 'graph_unverified': 0, 'safety_missing': 0}}})}\n\n"
    yield f"data: {json_dumps({'event': 'done', 'data': done_data})}\n\n"


def _is_knowledge_inventory_question(message: str) -> bool:
    text = message or ""
    content_terms = ("部件", "零件", "配件", "总成", "参数", "步骤", "装配", "拆卸", "安装", "表格", "图片", "章节", "故障", "原因", "结构", "组成")
    if any(term in text for term in content_terms):
        return False
    inventory_terms = ("有哪些", "有什么", "哪些", "列出", "查看", "清单", "目录", "已导入", "收录")
    knowledge_terms = ("知识库", "知识文件", "知识文档", "已上传", "上传", "已导入", "导入", "入库", "文档", "文件", "PDF", "pdf")
    return any(term in text for term in inventory_terms) and any(term in text for term in knowledge_terms)


def _is_high_risk_rag_question(message: str) -> bool:
    text = message or ""
    parameter_terms = (
        "参数", "多少", "扭矩", "力矩", "间隙", "规格", "型号", "标准", "数值",
        "N·m", "N路m", "mm", "MPa", "kPa", "电压", "电流", "torque", "spec",
    )
    procedure_terms = ("怎么", "如何", "步骤", "流程", "拆", "装", "更换", "维修", "检修", "安装", "调整", "操作")
    diagnosis_terms = ("故障", "原因", "过热", "异响", "漏油", "启动不了", "报警", "异常", "怎么回事", "排除")
    formal_plan_terms = ("检修方案", "维修方案", "SOP", "工单", "作业指导书", "安全措施")
    return any(
        term in text
        for term in parameter_terms + procedure_terms + diagnosis_terms + formal_plan_terms
    )


def _should_use_rag_fast_path(request: ChatRequest) -> bool:
    """保守触发简单 RAG 快速路径，避免普通诊断问题误绕过 ReAct。"""
    if request.images:
        return False
    context = request.context or {}
    if context.get("disable_fast_path") or context.get("force_react"):
        return False
    message = request.message or ""
    if _is_knowledge_inventory_question(message):
        return False
    if _is_high_risk_rag_question(message):
        return False
    if request.mode == AgentMode.RETRIEVAL:
        return True
    return any(
        keyword in message
        for keyword in ("根据知识库", "查知识库", "知识库回答", "只查资料", "根据资料", "根据手册")
    )


IMAGE_ONLY_DEFAULT_MESSAGE = "请识别图片中的设备或部件，并结合知识库判断它可能属于什么系统。"


def _compact_text(parts: list[str]) -> str:
    seen = set()
    compacted = []
    for part in parts:
        text = " ".join(str(part or "").split())
        if not text or text in seen:
            continue
        seen.add(text)
        compacted.append(text)
    return " ".join(compacted)


async def _build_image_understanding(images: list[str], user_message: str) -> dict:
    summaries = []
    for image_url in images:
        try:
            summary = await get_image_summary_service().understand_user_image(image_url, user_message=user_message)
            if not summary:
                summary = await get_image_summary_service().summarize(
                    image_url=image_url,
                    caption=user_message,
                    context_before="",
                    context_after="",
                    section_title="用户上传图片",
                )
        except Exception as exc:
            logger.warning("[chat][image_understanding] image summary failed: %s", exc)
            summary = {
                "image_title": "用户上传图片",
                "image_summary": "用户上传了一张待识别的设备或部件图片。",
                "summary_source": "fallback_error",
            }
        summaries.append({"image_url": image_url, **summary})

    enhanced_query = _compact_text(
        [user_message]
        + [
            " ".join(
                str(item.get(key, ""))
                for key in ("image_title", "image_summary")
                if item.get(key)
            )
            + " "
            + " ".join(str(keyword) for keyword in item.get("keywords", []) if keyword)
            for item in summaries
        ]
    )
    return {
        "summaries": summaries,
        "enhanced_query": enhanced_query or IMAGE_ONLY_DEFAULT_MESSAGE,
    }


def _normalize_scope_entity(value: Any) -> str:
    return re.sub(r"[\s_\-—–·,，。:：/\\()（）\[\]【】]+", "", str(value or "")).casefold()


def _clear_document_entity_device_identity(
    contract: QueryContract,
    *,
    document_id: str,
    catalog: DeviceCatalog,
) -> QueryContract:
    """Demote a section/component entity that the intent model called a device.

    A match is accepted only inside the already selected document and only
    when the span is not an identity head derived from that document's
    manifest.  The section index is semantic document structure, not a static
    device keyword list.
    """
    raw_span = _normalize_scope_entity(contract.raw_device_span)
    document = catalog.document(document_id)
    if not raw_span or document is None:
        return contract
    identity_heads = document_identity_heads(document)
    span_identity_heads = [head for head in identity_heads if head and head in raw_span]
    if span_identity_heads and raw_span not in identity_heads:
        return contract
    if raw_span in identity_heads:
        return contract
    try:
        from services.retrieval.section_index import SectionTitleIndex

        section_index = SectionTitleIndex.get_instance()
        section_index.build(get_vector_service())
        matches = section_index.find(contract.raw_device_span)
    except Exception as exc:
        logger.warning("[scope] section entity resolution unavailable: %s", exc)
        return contract
    is_document_entity = any(
        str(getattr(match, "document_id", "") or "") == document_id
        for match in matches or []
    )
    if not is_document_entity:
        return contract
    comparison = compare_query_to_document(contract, document)
    matched_section_text = _normalize_scope_entity(" ".join(
        str(value or "")
        for match in matches or []
        if str(getattr(match, "document_id", "") or "") == document_id
        for value in (
            getattr(match, "core_title", ""),
            getattr(match, "full_title", ""),
        )
    ))
    independent_identity_conflicts = [
        field
        for field in comparison.conflicts
        if field != "device_name"
        and _normalize_scope_entity(getattr(contract, field, ""))
        and _normalize_scope_entity(getattr(contract, field, "")) not in matched_section_text
    ]
    if independent_identity_conflicts:
        return contract
    payload = contract.to_dict()
    for field in (
        "raw_device_span",
        "device_name",
        "device_category",
        "carrier_or_application",
        "manufacturer",
        "model",
    ):
        payload[field] = ""
    return QueryContract.from_mapping(payload, raw_query=contract.raw_query)


async def _prepare_chat_agent_input(request: ChatRequest) -> AgentInput:
    raw_message = request.message or ""
    effective_message = raw_message.strip() or IMAGE_ONLY_DEFAULT_MESSAGE
    context = dict(request.context or {})
    _restore_trusted_pending_context(request.session_id, context)
    pending_document_selection = load_pending_document_selection(
        request.session_id,
        client_pending=context.get("pending_document_selection"),
    )
    if pending_document_selection:
        resolved_selection = resolve_pending_document_selection(
            pending_document_selection,
            raw_message,
        )
        if resolved_selection:
            clear_pending_document_selection(
                request.session_id,
            )
            context["confirmed_document_id"] = resolved_selection["selected_document_id"]
            context["resolved_document_selection"] = resolved_selection
            context["document_selection_answer"] = raw_message
            raw_message = resolved_selection["original_query"]
            effective_message = raw_message.strip() or IMAGE_ONLY_DEFAULT_MESSAGE
        else:
            pending_query = str(pending_document_selection.get("original_query") or raw_message)
            pending_contract = QueryContract.from_mapping({}, raw_query=pending_query)
            pending_options = tuple(
                dict(item)
                for item in pending_document_selection.get("alternatives") or []
                if isinstance(item, dict)
            )
            pending_plan = RoutePlan(
                action=RouteAction.CLARIFY_DOCUMENT,
                intent="knowledge_query",
                task_action="document_explain",
                query_contract=pending_contract,
                entity_role="document_component",
                candidate_document_ids=tuple(
                    str(item.get("document_id") or "") for item in pending_options
                ),
                selected_document_id="",
                allowed_tools=(),
                answer_source="deterministic_clarification",
                allow_ai_fallback=False,
                reason="awaiting_document_selection",
                clarification_options=pending_options,
            )
            context.update({
                "intent_decision": {
                    "intent": "knowledge_query",
                    "task_action": "document_explain",
                    "requires_knowledge_retrieval": False,
                },
                "intention": "knowledge_query",
                "query_contract": pending_contract.to_dict(),
                "route_plan": pending_plan.to_dict(),
                "scope_decision": {"status": "unknown", "reason": "awaiting_document_selection"},
                "retrieval_scope": {},
                "response_policy": {
                    "mode": "DOCUMENT_CLARIFICATION",
                    "source_type": "deterministic_clarification",
                    "allow_knowledge_retrieval": False,
                    "allow_ai_fallback": False,
                },
            })
            return AgentInput(
                user_message=effective_message,
                session_id=request.session_id,
                images=request.images,
                conversation_history=request.conversation_history,
                context=context,
            )
    # 同轮记忆写仲裁：为本轮生成唯一 turn_ts（毫秒），同时传给偏好兜底与主 Agent 的 save_memory；
    # 两路带同一个值，Java saveMemory 才能在"同一句话"上按来源优先级仲裁（漏洞#1修复）。
    turn_ts = int(time.time() * 1000)
    context["turn_ts"] = turn_ts
    session_document_id = context.get("confirmed_document_id")
    session_device_type = context.get("confirmed_device_type")

    intent_router = get_intent_router()
    intent_decision = await intent_router.classify(
        raw_message,
        images=request.images,
        context=context,
    )
    context["intent_decision"] = intent_decision.model_dump()
    context["intention"] = intent_decision.intent
    query_contract = QueryContract.from_mapping(
        intent_decision.model_dump(),
        raw_query=raw_message,
    )
    context["query_contract"] = query_contract.to_dict()
    technical_route = intent_decision.intent not in {"chat_social", "knowledge_inventory"}
    device_catalog = DeviceCatalog(())
    section_refs = ()
    if technical_route:
        try:
            device_catalog = await load_dynamic_device_catalog()
        except Exception as exc:
            logger.error("[scope] dynamic document catalog unavailable: %s", exc)
        try:
            section_index = SectionTitleIndex.get_instance()
            section_index.build(get_vector_service())
            section_refs = tuple(section_index.find(raw_message))
        except Exception as exc:
            logger.warning("[routing] dynamic section catalog unavailable: %s", exc)
    if (
        query_contract.has_explicit_device
        and any(
            query_mentions_unresolved_identity(query_contract, document)
            for document in device_catalog.documents
        )
    ):
        refine_query_contract = getattr(intent_router, "refine_query_contract", None)
        if callable(refine_query_contract):
            try:
                refined_contract = await refine_query_contract(raw_message)
                if isinstance(refined_contract, QueryContract):
                    refined_payload = refined_contract.to_dict()
                    refined_payload["intent"] = query_contract.intent
                    refined_payload["task_action"] = query_contract.task_action
                    query_contract = QueryContract.from_mapping(
                        refined_payload,
                        raw_query=raw_message,
                    )
                    context["query_contract"] = query_contract.to_dict()
            except Exception as exc:
                logger.warning("[scope] focused identity refinement unavailable: %s", exc)
    if (
        not query_contract.has_explicit_device
        and any(
            query_has_grounded_operation_target(query_contract, document)
            for document in device_catalog.documents
        )
    ):
        resolved_payload = query_contract.to_dict()
        resolved_payload["identity_resolution"] = "confirmed_absent"
        query_contract = QueryContract.from_mapping(
            resolved_payload,
            raw_query=raw_message,
        )
        context["query_contract"] = query_contract.to_dict()
    route_plan = await SemanticRoutingOrchestrator().build_plan(
        query=raw_message,
        decision=intent_decision,
        catalog=device_catalog,
        section_refs=section_refs,
        request_document_id=str(request.document_id or ""),
        session_document_id=str(session_document_id or ""),
        query_contract=query_contract,
    )
    query_contract = route_plan.query_contract
    context["query_contract"] = query_contract.to_dict()
    context["route_plan"] = route_plan.to_dict()
    selected_document_id = route_plan.selected_document_id
    scope_decision = decide_scope(
        raw_message,
        request_document_id=selected_document_id or request.document_id,
        request_device_type=request.device_type,
        session_document_id=session_document_id,
        session_device_type=session_device_type,
        query_contract=query_contract,
        catalog=device_catalog,
    )
    context["scope_decision"] = scope_decision.to_dict()
    context["retrieval_scope"] = (
        {"document_id": selected_document_id, "device_type": ""}
        if (
            route_plan.action == RouteAction.GROUNDED_RETRIEVAL
            and selected_document_id
            and scope_decision.status == "in_scope"
        )
        else {}
    )
    response_policy = derive_response_policy(
        intent_decision,
        context["scope_decision"],
        {},
        query=raw_message,
    ).to_dict()
    if route_plan.action == RouteAction.KNOWLEDGE_INVENTORY:
        response_policy.update({
            "mode": "KNOWLEDGE_INVENTORY",
            "source_type": "inventory_tool",
            "allow_knowledge_retrieval": False,
            "allow_ai_fallback": False,
            "disclaimer_required": False,
        })
    elif route_plan.action == RouteAction.CLARIFY_DOCUMENT:
        response_policy.update({
            "mode": "DOCUMENT_CLARIFICATION",
            "source_type": "deterministic_clarification",
            "allow_knowledge_retrieval": False,
            "allow_ai_fallback": False,
            "disclaimer_required": False,
        })
    elif route_plan.action == RouteAction.AI_FALLBACK:
        response_policy.update({
            "mode": "MAINTENANCE_AI_FALLBACK",
            "source_type": "ai",
            "allow_knowledge_retrieval": False,
            "allow_ai_fallback": True,
            "disclaimer_required": True,
            "style_profile": "maintenance_ai",
        })
    context["response_policy"] = response_policy
    logger.info(
        "[routing] session=%s intent=%s entity_role=%s candidates=%s selected_document=%s action=%s tools=%s source=%s reason=%s",
        request.session_id,
        route_plan.intent,
        route_plan.entity_role,
        route_plan.candidate_document_ids,
        route_plan.selected_document_id,
        route_plan.action.value,
        route_plan.allowed_tools,
        route_plan.answer_source,
        route_plan.reason,
    )

    # 用户画像确定性兜底：偏好/身份不再只靠主 Agent 自觉调 save_memory，
    # 命中门控即后台抽取并按规范 name upsert 到 memory_fact(type=user)，下一轮即生效。
    schedule_capture(raw_message, context.get("user_id"), turn_ts)

    if request.images and intent_decision.requires_image_understanding:
        image_understanding = await _build_image_understanding(request.images, effective_message)
        context["image_understanding"] = image_understanding
        context["enhanced_retrieval_query"] = image_understanding["enhanced_query"]
        context["original_user_message"] = raw_message

    return AgentInput(
        user_message=effective_message,
        session_id=request.session_id,
        images=request.images,
        conversation_history=request.conversation_history,
        context=context,
    )


def _evidence_item_to_text(item, index: int) -> str:
    data = item.model_dump() if hasattr(item, "model_dump") else item
    metadata = data.get("metadata") or {}
    source = data.get("id") or metadata.get("document_id") or f"evidence-{index}"
    score = data.get("score", "")
    content = data.get("content") or data.get("text") or ""
    page = metadata.get("page_number") or metadata.get("page")
    page_text = f", page={page}" if page else ""
    return f"[证据{index}] source={source}, score={score}{page_text}\n{content}"


def _plain_dict(value) -> dict:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return dict(value) if isinstance(value, dict) else {}


def _iter_trace_tool_payloads(metadata: dict):
    trace = (metadata or {}).get("react_trace") or []
    for step in trace:
        step_data = _plain_dict(step)
        for tool_call in step_data.get("tool_calls") or []:
            call_data = _plain_dict(tool_call)
            result_data = call_data.get("result_data")
            if result_data is None:
                result_data = call_data.get("data")
            if result_data is None:
                result_data = call_data.get("result")
            result_data = _plain_dict(result_data) if hasattr(result_data, "model_dump") else result_data
            yield call_data, result_data


def _iter_payload_result_items(result_data):
    result_data = _plain_dict(result_data) if hasattr(result_data, "model_dump") else result_data
    if isinstance(result_data, dict) and isinstance(result_data.get("data"), list):
        result_data = result_data["data"]
    elif isinstance(result_data, dict) and isinstance(result_data.get("results"), list):
        result_data = result_data["results"]
    if isinstance(result_data, list):
        for item in result_data:
            item_data = _plain_dict(item)
            if item_data:
                yield item_data


def _iter_trace_result_items(metadata: dict):
    for _, result_data in _iter_trace_tool_payloads(metadata):
        yield from _iter_payload_result_items(result_data)


_STRUCTURAL_RECOVERY_CANDIDATE_SOURCE = "_structural_recovery_candidate_source"
_STRUCTURAL_RECOVERY_LOOKUP_SOURCE = "_structural_recovery_lookup_source"
_STRUCTURAL_REFERENCE_LOCATOR_FIELDS = (
    "document_id",
    "document_version",
    "chunk_id",
    "section_title",
    "parent_section_id",
    "section_match_ids",
    "retrieval_plan_intent",
    "chunk_type",
    "source_chunk_type",
    "page",
    "page_number",
    "original_title_match",
)


def _iter_structural_recovery_payload_items(result_data):
    """Yield location candidates without widening the ordinary trace surface.

    ``reference_evidence`` may identify a document/section for a controlled
    follow-up lookup, but remains reference-only.  The private provenance marker
    lets the recovery path distinguish a location hint from answer evidence.
    """
    yield from _iter_payload_result_items(result_data)
    payload = _plain_dict(result_data)
    if not payload:
        return
    for source_name in ("qualified_evidence", "reference_evidence"):
        bucket = payload.get(source_name)
        if not isinstance(bucket, list):
            continue
        for item in bucket:
            item_data = _plain_dict(item)
            if not item_data:
                continue
            item_meta = dict(item_data.get("metadata") or {})
            if source_name == "reference_evidence":
                locator_meta = {
                    key: item_meta.get(key)
                    for key in _STRUCTURAL_REFERENCE_LOCATOR_FIELDS
                    if item_meta.get(key) not in (None, "", [])
                }
                locator_meta["qualification"] = "reference_only"
                locator_meta[_STRUCTURAL_RECOVERY_CANDIDATE_SOURCE] = source_name
                locator_id = str(
                    locator_meta.get("chunk_id")
                    or item_data.get("id")
                    or item_data.get("doc_id")
                    or ""
                )
                yield {
                    **({"id": locator_id} if locator_id else {}),
                    "metadata": locator_meta,
                }
                continue
            item_meta[_STRUCTURAL_RECOVERY_CANDIDATE_SOURCE] = source_name
            yield {**item_data, "metadata": item_meta}


def _iter_structural_recovery_candidate_items(metadata: dict):
    for _, result_data in _iter_trace_tool_payloads(metadata):
        yield from _iter_structural_recovery_payload_items(result_data)


_INVENTORY_QUERY_KEYWORDS = (
    "清单",
    "BOM",
    "bom",
    "部件",
    "零件",
    "料件",
    "配件",
    "明细",
    "列表",
)


def _is_inventory_table_query(message: str) -> bool:
    text = (message or "").strip()
    if not text:
        return False
    strong_keywords = ("清单", "BOM", "bom", "明细", "列表")
    if any(keyword in text for keyword in strong_keywords):
        return True
    procedure_hints = (
        "怎么", "如何", "步骤", "流程", "拆卸", "拆下", "取下", "取出",
        "安装", "装上", "放入", "依次",
    )
    if any(hint in text for hint in procedure_hints):
        return False
    return any(keyword in text for keyword in _INVENTORY_QUERY_KEYWORDS)


def _inventory_cell(value) -> str:
    return str(value or "").replace("\n", " ").strip().strip("|").strip()


def _inventory_header_index(headers: list[str], *keywords: str) -> int | None:
    for index, header in enumerate(headers):
        normalized = _inventory_cell(header)
        if any(keyword in normalized for keyword in keywords):
            return index
    return None


def _inventory_row_from_cells(headers: list[str], cells: list[str]) -> dict | None:
    cells = [_inventory_cell(cell) for cell in cells]
    if not any(cells):
        return None

    seq_index = _inventory_header_index(headers, "序号", "编号")
    name_index = _inventory_header_index(headers, "料件名称", "部件名称", "零件名称", "名称", "料件", "部件", "零件")
    quantity_index = _inventory_header_index(headers, "数量", "数目")
    remark_index = _inventory_header_index(headers, "备注", "说明", "工具")

    if seq_index is None and cells and re.fullmatch(r"\d+", cells[0]):
        seq_index = 0
    if name_index is None:
        name_index = 1 if len(cells) > 1 and seq_index == 0 else 0
    if quantity_index is None and len(cells) > 2:
        quantity_index = 2
    if remark_index is None and len(cells) > 3:
        remark_index = 3

    def pick(index: int | None) -> str:
        return cells[index] if index is not None and 0 <= index < len(cells) else ""

    name = pick(name_index)
    quantity = pick(quantity_index)
    if not name or name in {"料件名称", "部件名称", "零件名称", "名称"}:
        return None
    if name == quantity:
        quantity = ""
    return {
        "seq": pick(seq_index),
        "name": name,
        "quantity": quantity,
        "remark": pick(remark_index),
    }


def _inventory_row_from_key_values(content: str) -> dict | None:
    fields: dict[str, str] = {}
    for part in re.split(r"[；;]\s*", content or ""):
        if "=" in part:
            key, value = part.split("=", 1)
        elif "：" in part:
            key, value = part.split("：", 1)
        else:
            continue
        fields[_inventory_cell(key)] = _inventory_cell(value)

    if not fields:
        return None

    def find_value(*keywords: str) -> str:
        for key, value in fields.items():
            if any(keyword in key for keyword in keywords):
                return value
        return ""

    name = find_value("料件名称", "部件名称", "零件名称", "名称", "料件", "部件", "零件")
    if not name:
        return None
    return {
        "seq": find_value("序号", "编号"),
        "name": name,
        "quantity": find_value("数量", "数目"),
        "remark": find_value("备注", "说明", "工具"),
    }


def _inventory_rows_from_pipe_table(content: str) -> list[dict]:
    lines = [
        _inventory_cell(line)
        for line in (content or "").splitlines()
        if "|" in line and line.strip()
    ]
    if len(lines) < 2:
        return []

    headers = [_inventory_cell(cell) for cell in lines[0].split("|")]
    rows: list[dict] = []
    for line in lines[1:]:
        compact = line.replace("|", "").replace("-", "").replace(" ", "")
        if not compact:
            continue
        row = _inventory_row_from_cells(headers, [_inventory_cell(cell) for cell in line.split("|")])
        if row:
            rows.append(row)
    return rows


def _inventory_rows_from_table_full(table_full) -> list[dict]:
    if not table_full:
        return []
    if isinstance(table_full, str):
        return _inventory_rows_from_pipe_table(table_full)
    if not isinstance(table_full, dict):
        return []

    headers = table_full.get("headers") or table_full.get("columns") or []
    rows = table_full.get("rows") or table_full.get("data") or []
    parsed: list[dict] = []
    for raw_row in rows:
        if isinstance(raw_row, dict) and isinstance(raw_row.get("fields"), Mapping):
            fields = raw_row.get("fields") or {}
            row = _inventory_row_from_cells(
                [_inventory_cell(header) for header in headers],
                [_inventory_cell(fields.get(header)) for header in headers],
            )
            if row:
                row["_row_id"] = str(raw_row.get("row_id") or "")
                row["_source_page"] = raw_row.get("source_page")
                row["_source_index"] = raw_row.get("source_index")
        elif isinstance(raw_row, dict):
            row = _inventory_row_from_key_values(
                "；".join(f"{key}={value}" for key, value in raw_row.items())
            )
        elif isinstance(raw_row, (list, tuple)):
            row = _inventory_row_from_cells([_inventory_cell(header) for header in headers], list(raw_row))
        else:
            row = _inventory_row_from_key_values(str(raw_row))
        if row:
            parsed.append(row)
    return parsed


def _dedupe_inventory_rows(rows: list[dict]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for row in rows:
        key = (row.get("seq") or "", row.get("name") or "")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _inventory_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _inventory_declared_pages(metadata: Mapping[str, Any]) -> list[int]:
    """Read table page provenance emitted by both current and legacy indexes."""
    pages: set[int] = set()

    def add_page(value: Any) -> None:
        page = _inventory_int(value)
        if page is not None and page > 0:
            pages.add(page)

    table_full = metadata.get("table_full")
    if isinstance(table_full, Mapping):
        for value in table_full.get("page_span") or []:
            add_page(value)
    for value in metadata.get("page_span") or []:
        add_page(value)

    range_text = str(metadata.get("page_range") or "").strip()
    match = re.fullmatch(r"\s*(\d+)\s*[-—~至]\s*(\d+)\s*", range_text)
    if match:
        start, end = int(match.group(1)), int(match.group(2))
        if 0 < start <= end and end - start <= 100:
            pages.update(range(start, end + 1))
    elif range_text.isdigit():
        add_page(range_text)

    caption = str(metadata.get("caption") or "")
    caption_match = re.search(r"第\s*(\d+)\s*(?:[-—~至]\s*(\d+)\s*)?页", caption)
    if caption_match:
        start = int(caption_match.group(1))
        end = int(caption_match.group(2) or start)
        if 0 < start <= end and end - start <= 100:
            pages.update(range(start, end + 1))
    return sorted(pages)


def _inventory_rows_have_duplicate_sequence(rows: list[dict]) -> bool:
    seen: set[str] = set()
    for row in rows:
        seq = str(row.get("seq") or "").strip()
        if not seq:
            continue
        if seq in seen:
            return True
        seen.add(seq)
    return False


def _select_inventory_primary_table_candidates(candidates: list[dict]) -> list[dict]:
    """Drop likely auxiliary/diagram tables while keeping true multi-page BOM continuations.

    Some imported manuals assign a broad section range to an inventory chapter.
    In that case a later page may contain a small figure-callout table with the
    same headers and section title, but it is not the requested BOM body.  True
    continuation tables are usually structurally continuous: their sequence
    numbers keep increasing and do not restart/duplicate within the later table.
    """
    full_tables = [
        candidate for candidate in candidates
        if candidate.get("chunk_label") == "table_full" and candidate.get("rows")
    ]
    if len(full_tables) <= 1:
        return candidates

    ordered_full = sorted(
        full_tables,
        key=lambda candidate: (
            _inventory_int(candidate.get("page"), 9999) or 9999,
            _inventory_int(candidate.get("source_index"), 9999) or 9999,
            str(candidate.get("source_id") or ""),
        ),
    )
    primary = ordered_full[0]
    primary_rows = primary.get("rows") or []
    primary_seqs = [
        seq for seq in (_inventory_int(row.get("seq")) for row in primary_rows)
        if seq is not None
    ]
    if not primary_seqs:
        return candidates
    primary_max_seq = max(primary_seqs)

    kept_full_ids = {id(primary)}
    for candidate in ordered_full[1:]:
        rows = candidate.get("rows") or []
        seqs = [
            seq for seq in (_inventory_int(row.get("seq")) for row in rows)
            if seq is not None
        ]
        if not seqs:
            kept_full_ids.add(id(candidate))
            continue
        starts_after_primary = min(seqs) > primary_max_seq
        overlaps_primary_tail = min(seqs) <= primary_max_seq < max(seqs)
        if overlaps_primary_tail:
            kept_full_ids.add(id(candidate))
            continue
        if starts_after_primary:
            kept_full_ids.add(id(candidate))

    parent_ids_to_keep: set[str] = {
        str(candidate.get("source_id") or "")
        for candidate in candidates
        if id(candidate) in kept_full_ids and candidate.get("source_id")
    }
    selected: list[dict] = []
    for candidate in candidates:
        if candidate.get("chunk_label") == "table_full":
            if id(candidate) in kept_full_ids:
                selected.append(candidate)
            continue
        parent_id = str(candidate.get("parent_table_chunk_id") or "")
        if parent_ids_to_keep and parent_id and parent_id not in parent_ids_to_keep:
            continue
        selected.append(candidate)
    return selected or candidates


def _inventory_sort_key(row: dict) -> tuple[int, str]:
    seq = str(row.get("seq") or "").strip()
    if seq.isdigit():
        return (int(seq), "")
    return (10_000, seq)


def _inventory_torque_from_remark(remark: str) -> str:
    text = str(remark or "")
    match = re.search(
        r"(\d+(?:\.\d+)?\s*(?:±|卤|\+/-)\s*\d+(?:\.\d+)?\s*N\s*[·路.]\s*m|\d+(?:\.\d+)?\s*N\s*[·路.]\s*m)",
        text,
        re.IGNORECASE,
    )
    if not match:
        return ""
    torque = match.group(1)
    torque = torque.replace("卤", "±")
    torque = re.sub(r"\s*(?:±|\+/-)\s*", "±", torque)
    torque = re.sub(r"\s*N\s*[·路.]\s*m", " N·m", torque, flags=re.IGNORECASE)
    return torque.strip()


def _inventory_subject_from_title(title: str) -> str:
    subject = re.sub(r"^\s*\d+(?:\.\d+)*\s*", "", title or "").strip()
    for suffix in ("部件清单", "零件清单", "料件清单", "配件清单", "清单"):
        if subject.endswith(suffix):
            subject = subject[: -len(suffix)].strip()
            break
    return subject or "该装配"


def _compact_inventory_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).replace("－", "-").replace("．", ".")


def _inventory_row_terms(name: str) -> list[str]:
    text = _compact_inventory_text(name)
    terms: list[str] = []
    for pattern in (r"[A-Za-z]+\d+(?:[×x*.]\d+(?:\.\d+)?)*", r"\d+(?:\.\d+)?(?:[×x*]\d+(?:\.\d+)?)+", r"[一-鿿]{2,}"):
        for term in re.findall(pattern, text):
            if term and term not in terms:
                terms.append(term)
    for term in (
        "O型圈",
        "O形圈",
        "空心定位销",
        "定位销",
        "圆柱销",
        "螺栓",
        "螺母",
        "挡圈",
        "垫圈",
        "垫片",
        "拉玛",
        "规格",
    ):
        if term in text and term not in terms:
            terms.append(term)
    if text and text not in terms:
        terms.append(text)
    return terms


def _inventory_query_requests_specific_rows(message: str) -> bool:
    text = message or ""
    full_list_hints = ("有哪些", "都有哪些", "完整", "全部", "全量", "列一下", "列出", "展示", "看看")
    asks_value = any(term in text for term in ("数量", "多少", "扭矩", "扭力", "力矩", "是多少", "要求", "规格", "是什么", "校正力", "锁紧"))
    scoped = any(term in text for term in ("中", "里", "其中", "的数量", "的扭矩", "的扭力"))
    if asks_value and scoped:
        return True
    if any(term in text for term in ("M", "GB", "φ", "Φ", "×", "O型圈", "螺母", "垫片", "挡圈", "摩擦片")) and asks_value:
        return True
    if _is_inventory_table_query(text):
        return False
    return not any(hint in text for hint in full_list_hints)


def _inventory_row_query_score(
    message: str,
    row: dict,
    sibling_names: list[str] | None = None,
) -> tuple[int, int, int]:
    query = _compact_inventory_text(message)
    name = _compact_inventory_text(row.get("name") or "")
    remark = _compact_inventory_text(row.get("remark") or "")
    name_score = 0
    remark_score = 0
    total = 0
    if name and name in query:
        # Prefix suppression: if a longer sibling row name (e.g. "水泵盖") also
        # appears in the query and starts with this name (e.g. "水泵"), the user
        # is asking about the more specific part.  Do not let the short name win
        # on the full-name match bonus.
        overridden_by_longer = False
        for sibling in sibling_names or ():
            sibling_norm = _compact_inventory_text(sibling)
            if not sibling_norm or sibling_norm == name:
                continue
            # Compare against the sibling's main term (e.g. "水泵盖" from
            # "水泵盖（钛金）") so a parenthetical suffix does not hide the match.
            sibling_variants = {sibling_norm, *_inventory_row_terms(sibling_norm)}
            for variant in sibling_variants:
                if (
                    variant != name
                    and variant.startswith(name)
                    and variant in query
                ):
                    overridden_by_longer = True
                    break
            if overridden_by_longer:
                break
        if not overridden_by_longer:
            name_score += 80
    for term in _inventory_row_terms(name):
        if len(term) < 2:
            continue
        if term in query:
            weight = min(len(term), 12)
            if re.search(r"\d|[A-Za-z]", term):
                weight += 10
            name_score += weight
    # Reverse match: an alphanumeric part code from the query (e.g. "M10") that
    # appears inside the row name (e.g. "M10×1.25盖形法兰面螺母") should score,
    # even though the row's own token "M10×1.25" is not a query substring.
    for q_term in _inventory_row_terms(query):
        if len(q_term) < 2 or not re.match(r"^[A-Za-z]+\d", q_term):
            continue
        if q_term in name:
            name_score += min(len(q_term), 12) + 10
    for term in _inventory_row_terms(remark):
        if len(term) < 2:
            continue
        if term in query:
            weight = min(len(term), 12)
            if re.search(r"\d|[A-Za-z]", term):
                weight += 10
            remark_score += weight
    if "拉玛" in query and "拉玛" in remark:
        remark_score += 24
    if "规格" in query and "规格" in remark:
        remark_score += 10
    if "组件" in query and name in {"水泵", "机油泵"}:
        name_score += 8
    total += name_score
    total += remark_score
    asks_torque = any(term in query for term in ("扭矩", "扭力", "力矩", "锁紧", "校验"))
    if asks_torque and re.search(r"N[·.路]?\s*m|N·m|N路m", remark, re.IGNORECASE):
        total += 20
    asks_quantity = any(term in query for term in ("数量", "多少", "几", "数目"))
    if asks_quantity and row.get("quantity"):
        total += 4
    return total, name_score, remark_score


def _filter_inventory_rows_for_query(message: str, rows: list[dict]) -> list[dict]:
    """For targeted inventory questions, return only matching rows.

    Full-list questions still return the whole table.  This keeps the
    deterministic BOM path useful for "展示清单", while avoiding unrelated row
    quantities/torques when the user asks about a specific part.
    """
    if len(rows) <= 1 or not _inventory_query_requests_specific_rows(message):
        return rows
    all_names = [str(row.get("name") or "") for row in rows]
    scored = [
        (index, *_inventory_row_query_score(message, row, all_names), row)
        for index, row in enumerate(rows)
    ]
    candidates = [
        (index, total, name_score, remark_score, row)
        for index, total, name_score, remark_score, row in scored
        if name_score >= 4 or remark_score >= 4
    ]
    if not candidates:
        return []
    best = max(total for _, total, _, _, _ in candidates)
    if best < 10:
        return []
    filtered = [
        row
        for index, total, name_score, remark_score, row in candidates
        if total >= 10
    ]
    compact_message = _compact_inventory_text(message)
    if (
        "组件" in compact_message
        and any(term in compact_message for term in ("扭矩", "扭力", "力矩", "锁紧", "校验"))
        and filtered
    ):
        selected_indexes = {
            index for index, total, _name_score, _remark_score, _row in candidates if total >= 10
        }
        adjacent_indexes = {
            adjacent
            for index in selected_indexes
            for adjacent in (index - 1, index, index + 1)
            if 0 <= adjacent < len(rows)
        }
        expanded: list[dict] = []
        for index, _total, _name_score, _remark_score, row in scored:
            if index in adjacent_indexes and row not in expanded:
                expanded.append(row)
        if expanded:
            return expanded
    return filtered


def _format_inventory_table_answer_from_metadata(
    message: str,
    metadata: dict,
    extra_items: list[dict] | None = None,
) -> str | None:
    """从检索 trace 中的表格证据直接生成清单回答，避免 LLM 把已命中的表格说成未找到。"""
    if not _is_inventory_table_query(message):
        return None

    items = list(_iter_trace_result_items(metadata))
    if extra_items:
        items.extend(extra_items)
    if not items:
        return None

    section_match_ids: set[str] = set()
    for item in items:
        meta = item.get("metadata") or {}
        for sid in meta.get("section_match_ids") or []:
            section_match_ids.add(str(sid))

    candidates: list[dict] = []
    row_groups: dict[tuple[str, str, str], dict] = {}

    for item in items:
        meta = item.get("metadata") or {}
        chunk_type = meta.get("chunk_type") or meta.get("source_chunk_type") or ""
        chunk_label = str(meta.get("chunk_label") or "")
        if chunk_type != "table" and "table" not in chunk_label:
            continue

        content = item.get("content") or item.get("text") or ""
        section_id = str(meta.get("parent_section_id") or "")
        section_title = str(meta.get("section_title") or meta.get("chunk_label") or "").strip()
        document_id = str(meta.get("document_id") or "")
        page = meta.get("page_number") or meta.get("page")

        table_full = meta.get("table_full")
        rows = _inventory_rows_from_table_full(table_full)
        if not rows:
            rows = _inventory_rows_from_pipe_table(content)

        if rows:
            for row in rows:
                row.setdefault("_source_page", page)
                row.setdefault("_source_table_id", str(item.get("id") or item.get("doc_id") or ""))
                row.setdefault("_source_table_rows", len(rows))
            candidates.append({
                "rows": rows,
                "section_id": section_id,
                "section_title": section_title,
                "document_id": document_id,
                "page": page,
                "chunk_label": chunk_label,
                "source_id": str(item.get("id") or item.get("doc_id") or ""),
                "source_index": meta.get("source_index"),
                "parent_table_chunk_id": meta.get("parent_table_chunk_id"),
                "is_full_table": bool(table_full) or chunk_label == "table_full",
                "declared_table_rows": _inventory_int(meta.get("table_rows")),
                "declared_pages": _inventory_declared_pages(meta),
            })
            continue

        row = _inventory_row_from_key_values(content)
        if row:
            row.setdefault("_source_page", page)
            row.setdefault("_source_table_id", str(meta.get("parent_table_chunk_id") or ""))
            row.setdefault("_source_table_rows", meta.get("table_rows"))
            group_key = (section_id, section_title, str(page or ""))
            group = row_groups.setdefault(group_key, {
                "rows": [],
                "section_id": section_id,
                "section_title": section_title,
                "document_id": document_id,
                "page": page,
                "chunk_label": chunk_label,
                "source_id": str(meta.get("parent_table_chunk_id") or item.get("id") or item.get("doc_id") or ""),
                "source_index": meta.get("source_index"),
                "parent_table_chunk_id": meta.get("parent_table_chunk_id"),
            })
            group["rows"].append(row)

    candidates.extend(row_groups.values())
    if not candidates:
        return None
    candidates = _select_inventory_primary_table_candidates(candidates)

    groups: dict[tuple[str, str], dict] = {}
    for candidate in candidates:
        group_key = (
            candidate.get("section_id") or "",
            candidate.get("section_title") or "",
        )
        group = groups.setdefault(group_key, {
            "rows": [],
            "pages": [],
            "document_ids": [],
            "section_id": candidate.get("section_id") or "",
            "section_title": candidate.get("section_title") or "",
            "full_table_count": 0,
            "full_table_declarations": {},
            "declared_pages": [],
        })
        group["rows"].extend(candidate.get("rows") or [])
        page = candidate.get("page")
        if page and page not in group["pages"]:
            group["pages"].append(page)
        document_id = candidate.get("document_id")
        if document_id and document_id not in group["document_ids"]:
            group["document_ids"].append(document_id)
        if candidate.get("chunk_label") == "table_full":
            group["full_table_count"] += 1
        if candidate.get("is_full_table"):
            source_key = str(candidate.get("source_id") or "").strip()
            if not source_key:
                source_key = "|".join(
                    str(candidate.get(key) or "")
                    for key in ("page", "source_index", "section_id")
                )
            declared_rows = _inventory_int(candidate.get("declared_table_rows"))
            if declared_rows is not None and declared_rows > 0:
                group["full_table_declarations"][source_key] = max(
                    declared_rows,
                    int(group["full_table_declarations"].get(source_key) or 0),
                )
            for declared_page in candidate.get("declared_pages") or []:
                if declared_page not in group["declared_pages"]:
                    group["declared_pages"].append(declared_page)

    compact_query = _compact_inventory_text(message)

    def _content_hit_score(group: dict) -> int:
        # Count distinct row names that appear verbatim in the query.  When the
        # user asks about specific parts (e.g. 离合器弹簧) that live in a table
        # whose *title* does not match the query wording, this lets the table
        # actually containing the parts win over a title-only match.
        hits = 0
        seen: set[str] = set()
        for row in group.get("rows") or []:
            name = _compact_inventory_text(row.get("name") or "")
            if len(name) >= 3 and name in compact_query and name not in seen:
                seen.add(name)
                hits += 1
        return hits

    def score(group: dict) -> int:
        section_id = group.get("section_id") or ""
        section_score = 1000 if section_id and section_id in section_match_ids else 0
        full_table_score = 200 * int(group.get("full_table_count") or 0)
        title_score = 100 if "清单" in (group.get("section_title") or "") else 0
        content_hit_score = 600 * _content_hit_score(group)
        return section_score + full_table_score + title_score + content_hit_score + len(group.get("rows") or []) * 10

    best = max(groups.values(), key=score)
    rows = _dedupe_inventory_rows(best.get("rows") or [])
    if len(rows) < 2:
        return None

    rows = sorted(rows, key=_inventory_sort_key)
    declared_table_rows = sum(
        int(value)
        for value in (best.get("full_table_declarations") or {}).values()
        if _inventory_int(value) is not None and int(value) > 0
    )
    filtered_rows = _filter_inventory_rows_for_query(message, rows)
    if not filtered_rows:
        return None
    rows_were_filtered = len(filtered_rows) < len(rows)
    table_complete = (
        not rows_were_filtered
        and declared_table_rows > 0
        and len(rows) >= declared_table_rows
    )
    metadata["_deterministic_answer_table_complete"] = table_complete
    rows = filtered_rows
    title = best.get("section_title") or "部件清单"
    section_id = str(best.get("section_id") or "")
    if title:
        metadata["_deterministic_answer_section_title"] = title
    if section_id:
        metadata["_deterministic_answer_section_ids"] = [section_id]
    pages = best.get("pages") or []
    row_pages = {
        int(row.get("_source_page"))
        for row in rows
        if str(row.get("_source_page") or "").isdigit()
    }
    numeric_page_set = row_pages or {int(page) for page in pages if str(page).isdigit()}
    if table_complete:
        numeric_page_set = set(numeric_page_set) | {
            int(page)
            for page in best.get("declared_pages") or []
            if str(page).isdigit()
        }
    numeric_pages = sorted(numeric_page_set)
    if numeric_pages:
        metadata["_deterministic_answer_evidence_pages"] = numeric_pages
    document_ids = [doc for doc in (best.get("document_ids") or []) if doc]
    if document_ids:
        metadata["_deterministic_answer_document_ids"] = document_ids
    if len(numeric_pages) == 1:
        page_text = f"第{numeric_pages[0]}页"
    elif len(numeric_pages) > 1:
        page_text = f"第{numeric_pages[0]}-{numeric_pages[-1]}页"
    else:
        page_text = "对应表格"
    subject = _inventory_subject_from_title(title)

    if rows_were_filtered:
        lines = [f"根据手册{page_text}“{title}”，与问题匹配的清单条目如下："]
    else:
        lines = [f"根据手册{page_text}“{title}”，{subject}所用部件如下（按序号排列）："]
    for index, row in enumerate(rows, start=1):
        seq = str(row.get("seq") or index).strip()
        name = str(row.get("name") or "").strip()
        quantity = str(row.get("quantity") or "").strip()
        remark = str(row.get("remark") or "").strip()
        if not name:
            continue
        line = f"{seq}. {name}"
        if quantity:
            line += f"；数量：{quantity}"
        if remark:
            line += f"；备注：{remark}"
        torque = _inventory_torque_from_remark(remark)
        if torque:
            line += f"；扭矩：{torque}"
        lines.append(line)

    if _inventory_rows_have_duplicate_sequence(rows):
        lines.append("注：清单中存在重复序号，原表序号如此。")

    metadata["_deterministic_answer_mode"] = "evidence_rendered"
    return "\n".join(lines).strip()


_MANUAL_PROCEDURE_TERMS = ("怎么", "如何", "怎样", "步骤", "流程", "拆卸", "拆", "安装", "装", "更换", "调整", "调节", "校正", "操作")
_MANUAL_PARAMETER_TERMS = ("多少", "标准", "范围", "扭矩", "扭力", "力矩", "间隙", "压力", "容量", "数量")
_MANUAL_LOCATION_EVIDENCE_TERMS = (
    "哪些地方",
    "什么地方",
    "哪里",
    "位置",
    "涂抹",
    "涂",
    "密封胶",
    "密封硅胶",
    "平面密封",
    "润滑油",
)
_MANUAL_BROAD_LOCATION_EVIDENCE_TERMS = (
    "哪些地方",
    "什么地方",
    "哪里",
    "涂抹",
    "涂",
    "密封胶",
    "密封硅胶",
    "平面密封",
    "润滑油",
)
_MANUAL_ACTION_SYNONYMS = {
    "拆卸": ("拆卸", "拆下", "取下", "松开", "断开", "拉出", "取出"),
    "安装": ("安装", "装上", "装入", "放入", "合上", "拧紧", "套入", "旋入"),
    "检查": ("检查", "测量", "拨动", "转动", "校验"),
    "调整": ("调整", "调节", "校正"),
}
_MANUAL_ACTION_DESCRIPTOR_TERMS = {
    "步骤", "流程", "要求", "注意事项", "方法", "说明", "操作",
}
_MANUAL_OPPOSITE_ACTIONS = {
    "拆卸": _MANUAL_ACTION_SYNONYMS["安装"],
    "安装": _MANUAL_ACTION_SYNONYMS["拆卸"],
}


def _manual_query_kind(message: str) -> str:
    text = message or ""
    if _is_inventory_table_query(text):
        return ""
    if any(term in text for term in _MANUAL_LOCATION_EVIDENCE_TERMS):
        return "evidence"
    if any(term in text for term in ("判断", "原因", "是不是", "是否", "为何", "为什么")):
        return "evidence"
    if any(term in text for term in _MANUAL_PROCEDURE_TERMS):
        return "procedure"
    if any(term in text for term in _MANUAL_PARAMETER_TERMS):
        return "parameter"
    if any(term in text for term in ("检查", "项目", "技术要求")):
        return "evidence"
    return ""


def _manual_query_action(message: str) -> str:
    text = message or ""
    for action in ("拆卸", "安装", "检查", "调整"):
        if any(word in text for word in _MANUAL_ACTION_SYNONYMS[action]):
            return action
    return ""


def _manual_content_has_action(text: str, action: str) -> bool:
    return bool(action and any(word in (text or "") for word in _MANUAL_ACTION_SYNONYMS.get(action, ())))


def _manual_content_has_opposite_action(text: str, action: str) -> bool:
    return bool(action and any(word in (text or "") for word in _MANUAL_OPPOSITE_ACTIONS.get(action, ())))


def _manual_action_target(message: str, action: str) -> str:
    text = str(message or "")
    aliases = _MANUAL_ACTION_SYNONYMS.get(action, (action,))
    matches = [
        (text.find(alias), -len(alias), alias)
        for alias in aliases
        if alias and alias in text
    ]
    if not action or not matches:
        return ""
    _, _, matched_alias = min(matches)
    head, tail = text.split(matched_alias, 1)
    tail = re.split(r"[时的，,？?：:；;、\s]", tail, 1)[0]
    target = tail.strip()
    if target and target not in _MANUAL_ACTION_DESCRIPTOR_TERMS:
        return target
    head = re.sub(r"(?:怎么|如何|怎样|怎么进行|如何进行)$", "", head).strip()
    head = re.sub(r"[，,？?：:；;、\s]+$", "", head).strip()
    return head


def _manual_focus_records_to_query_subflow(
    records: list[dict],
    message: str,
    action: str,
) -> tuple[list[dict], bool]:
    """Select one document-defined procedure subflow when the match is unique."""
    query_target = normalize_procedure_target(_manual_action_target(message, action))
    if not records or not action or not query_target:
        return records, False

    groups: dict[str, tuple[Any, list[dict]]] = {}
    for record in records:
        scope = procedure_scope_from_metadata(record.get("metadata") or {})
        if not scope:
            continue
        if scope.scope_id not in groups:
            groups[scope.scope_id] = (scope, [])
        groups[scope.scope_id][1].append(record)
    if not groups:
        return records, False

    scored: list[tuple[int, str, list[dict], Any]] = []
    for scope_id, (scope, group) in groups.items():
        if scope.action != action:
            continue
        target_score = procedure_target_similarity(query_target, scope.target)
        group_text = normalize_procedure_target("\n".join(str(item.get("content") or "") for item in group))
        if query_target and query_target in group_text:
            target_score = max(target_score, 950)
        if target_score <= 0:
            continue
        scored.append((1000 + target_score, scope_id, group, scope))
    if not scored:
        return records, False

    scored.sort(key=lambda item: (-item[0], item[1]))
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return records, False
    _, scope_id, focused, scope = scored[0]
    metadata = {
        "procedure_scope_id": scope_id,
        "procedure_heading": scope.heading,
        "procedure_action": scope.action,
        "procedure_target": scope.target,
    }
    for record in focused:
        record_metadata = dict(record.get("metadata") or {})
        record_metadata.update({key: value for key, value in metadata.items() if value})
        record["metadata"] = record_metadata
    return focused, True


def _manual_drop_contained_duplicate_records(records: list[dict]) -> list[dict]:
    compact_records = [normalize_procedure_target(record.get("content") or "") for record in records]
    kept: list[dict] = []
    for index, record in enumerate(records):
        meta = record.get("metadata") or {}
        role = str(meta.get("answer_role") or "")
        compact = compact_records[index]
        parent_chunk_id = str(meta.get("parent_chunk_id") or "")
        if role == "safety_warning" and compact:
            duplicated = any(
                other_index != index
                and compact in other_compact
                and len(other_compact) > len(compact)
                and (
                    not parent_chunk_id
                    or parent_chunk_id
                    == str((records[other_index].get("metadata") or {}).get("parent_chunk_id") or "")
                )
                for other_index, other_compact in enumerate(compact_records)
            )
            if duplicated:
                continue
        kept.append(record)
    return kept


def _manual_query_anchor_terms(message: str) -> list[str]:
    """Return exact entity anchors from the user's manual question.

    Section titles are often broader than the user's target sub-step
    (for example, a chapter may be titled "安装活塞环" while the question asks
    about "安装活塞销挡圈").  These anchors are used as a generic reranking
    signal: a section containing the exact target entity should beat a broader
    title-only match.
    """
    action = _manual_query_action(message)
    raw_terms: list[str] = []
    if action:
        target = _manual_action_target(message, action)
        if target:
            raw_terms.append(target)
            for separator in ("并", "以及", "和", "及"):
                if separator in target:
                    raw_terms.extend(part for part in target.split(separator) if part)
    text = str(message or "")
    if "时" in text:
        after_when = text.split("时", 1)[1]
        after_when = re.split(
            r"(?:要|有什么|哪些|什么|怎么|如何|是多少|多少|要求|注意|[？?：:；;，,])",
            after_when,
            1,
        )[0]
        if after_when:
            raw_terms.append(after_when)
            for separator in ("并", "以及", "和", "及", "、"):
                if separator in after_when:
                    raw_terms.extend(part for part in after_when.split(separator) if part)

    anchors: list[str] = []
    for raw in raw_terms:
        term = _compact_inventory_text(raw)
        term = re.sub(r"(?:要求|步骤|流程|方法|位置|顺序|规范|标准)$", "", term)
        if len(term) < 3:
            continue
        if term in {"怎么", "如何", "安装", "拆卸", "检查", "装配"}:
            continue
        if term not in anchors:
            anchors.append(term)
    return anchors


def _manual_parameter_anchor_terms(message: str) -> list[str]:
    """Return entity+field anchors for parameter-style manual questions."""
    text = _compact_inventory_text(message)
    if not text:
        return []
    candidates: list[str] = []

    trimmed = text
    for suffix in (
        "是多少",
        "为多少",
        "多少",
        "标准值",
        "标准范围",
        "标准",
        "范围",
        "要求",
    ):
        if trimmed.endswith(suffix):
            trimmed = trimmed[: -len(suffix)]
    if trimmed:
        candidates.append(trimmed)

    for term in _MANUAL_PARAMETER_TERMS:
        if not term or term not in text:
            continue
        before = text.split(term, 1)[0]
        if before:
            candidates.append(before + term)
        candidates.append(term)

    anchors: list[str] = []
    stop_terms = {
        "多少", "标准", "范围", "扭矩", "扭力", "力矩", "间隙", "压力", "容量", "数量",
        "是多少", "什么", "哪些",
    }
    for raw in candidates:
        term = re.sub(r"(?:是多少|为多少|多少|标准值|标准范围|标准|范围|要求)$", "", raw)
        if len(term) < 2:
            continue
        if term in stop_terms:
            continue
        if term not in anchors:
            anchors.append(term)
    return anchors


def _manual_evidence_anchor_terms(message: str) -> list[str]:
    """Return condition/object anchors for diagnostic evidence questions."""
    text = _compact_inventory_text(message)
    if not text:
        return []
    candidates: list[str] = []

    if "时" in text:
        before_when = text.split("时", 1)[0]
        if before_when:
            candidates.append(before_when)
            for relation in ("低于", "小于", "高于", "大于", "等于", "超过", "低", "高"):
                if relation in before_when:
                    candidates.extend(part for part in before_when.split(relation) if part)

    for pattern in (
        r"是不是(.+?)(?:问题|故障|缺陷|原因)?$",
        r"是否(.+?)(?:问题|故障|缺陷|原因)?$",
        r"判断(.+?)(?:问题|故障|缺陷|原因)?$",
    ):
        match = re.search(pattern, text)
        if match:
            candidates.append(match.group(1))

    anchors: list[str] = []
    stop_terms = {"怎么", "如何", "判断", "是不是", "是否", "问题", "故障", "原因", "缺陷"}
    for raw in candidates:
        term = re.sub(r"(?:怎么判断|如何判断|判断|是不是|是否|问题|故障|缺陷|原因)+", "", raw)
        if len(term) < 2:
            continue
        if term in stop_terms:
            continue
        if term not in anchors:
            anchors.append(term)
    return anchors


_MANUAL_ATOMIC_ENTITY_TERMS = (
    "塞尺",
    "拉玛",
    "螺栓",
    "螺母",
    "O型圈",
    "O形圈",
    "定位销",
    "圆柱销",
    "挡圈",
    "垫圈",
    "垫片",
    "线束",
    "油封",
    "挺柱",
    "密封胶",
    "密封硅胶",
)


def _manual_atomic_entity_anchor_terms(message: str) -> list[str]:
    """Return short exact entities that should bind a query to body evidence.

    These are intentionally page-agnostic and case-agnostic.  They cover tools,
    standard parts and small visual entities that often appear only in the body
    text rather than in the section title.
    """
    text = _compact_inventory_text(message)
    if not text:
        return []
    anchors: list[str] = []
    for term in _MANUAL_ATOMIC_ENTITY_TERMS:
        if term in text and term not in anchors:
            anchors.append(term)
    for pattern in (
        r"[A-Za-z]\s*(?:孔|段|标记)",
        r"[A-Za-z]\/[A-Za-z]",
        r"[A-Za-z]\-[A-Za-z]",
        r"[Mｍ]\d+(?:[×x*.]\d+(?:\.\d+)?)+",
        r"\d+(?:\.\d+)?[×x*]\d+(?:\.\d+)?(?:[×x*]\d+(?:\.\d+)?)*",
    ):
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            anchor = _compact_inventory_text(match.group(0))
            if len(anchor) >= 2 and anchor not in anchors:
                anchors.append(anchor)
    return anchors


def _manual_tail_entity_candidates(prefix: str) -> list[str]:
    text = _compact_inventory_text(prefix)
    candidates: list[str] = []
    for size in (4, 3, 2):
        if len(text) >= size:
            candidate = text[-size:]
            if candidate and candidate not in candidates:
                candidates.append(candidate)
    return candidates


def _manual_flexible_anchor_token_groups(message: str) -> list[tuple[str, ...]]:
    """Return token groups that may not appear contiguously in OCR text.

    Example: the query says "曲柄C标记", while the manual OCR says
    "曲柄上的标记（图示“C”）".  Exact substring matching misses this, but the
    evidence is strong when "曲柄" + "C" + "标记" occur together.
    """
    text = _compact_inventory_text(message)
    if not text:
        return []
    groups: list[tuple[str, ...]] = []

    def add(tokens: tuple[str, ...]) -> None:
        normalized = tuple(
            _compact_inventory_text(token).lower()
            for token in tokens
            if _compact_inventory_text(token)
        )
        if not normalized:
            return
        if normalized not in groups:
            groups.append(normalized)

    for match in re.finditer(r"([\u4e00-\u9fff]{0,12})([A-Za-z])标记", text, flags=re.IGNORECASE):
        prefix, letter = match.group(1), match.group(2)
        add((letter, "标记"))
        for entity in _manual_tail_entity_candidates(prefix):
            add((entity, letter, "标记"))

    for match in re.finditer(r"([\u4e00-\u9fff]{0,12})([A-Za-z])(?:孔|段)", text, flags=re.IGNORECASE):
        prefix, letter = match.group(1), match.group(2)
        suffix = match.group(0)[-1:]
        add((letter, suffix))
        for entity in _manual_tail_entity_candidates(prefix):
            add((entity, letter, suffix))

    return groups


def _manual_target_action_heading_index(content: str, action: str, target: str) -> int:
    if not action or not target:
        return -1
    compact_target = _compact_inventory_text(target)
    lines = str(content or "").splitlines()
    offset = 0
    for line in lines:
        compact_line = _compact_inventory_text(line)
        needle = f"{action}{compact_target}"
        index = compact_line.find(needle)
        while index >= 0:
            prefix = compact_line[:index]
            if not _manual_heading_prefix_allowed(prefix):
                index = compact_line.find(needle, index + 1)
                continue
            end = index + len(needle)
            next_char = compact_line[end:end + 1]
            if not next_char or not re.match(r"[\u4e00-\u9fffA-Za-z0-9]", next_char):
                return offset
            index = compact_line.find(needle, index + 1)
        offset += len(line) + 1
    return -1


def _manual_heading_prefix_allowed(prefix: str) -> bool:
    if not prefix:
        return True
    return bool(
        re.fullmatch(r"\d+(?:\.\d+)+", prefix)
        or re.fullmatch(r"\d+[、．.]", prefix)
        or re.fullmatch(r"[（(]\d+[）)]", prefix)
    )


def _manual_opposite_target_action_heading_index(content: str, action: str, target: str) -> int:
    opposite_actions = {
        "拆卸": ("安装",),
        "安装": ("拆卸",),
    }.get(action, ())
    indexes = [
        _manual_target_action_heading_index(content, opposite_action, target)
        for opposite_action in opposite_actions
    ]
    indexes = [index for index in indexes if index >= 0]
    return min(indexes) if indexes else -1


def _manual_target_family_terms(target: str) -> list[str]:
    compact = _compact_inventory_text(target)
    if not compact:
        return []
    terms = [compact]
    for suffix in ("单向器", "分部件", "组件", "部件", "总成", "装配"):
        if compact.endswith(suffix) and len(compact) > len(suffix) + 2:
            terms.append(compact[: -len(suffix)])
    if len(compact) >= 8:
        terms.append(compact[:8])
    deduped: list[str] = []
    for term in terms:
        if len(term) >= 3 and term not in deduped:
            deduped.append(term)
    return deduped


def _manual_related_other_action_heading_index(content: str, action: str, target: str) -> int:
    if action != "检查" or not target:
        return -1
    related_terms = _manual_target_family_terms(target)
    if not related_terms:
        return -1
    other_actions = ("安装", "拆卸")
    lines = str(content or "").splitlines()
    offset = 0
    for line in lines:
        stripped = line.strip()
        compact_line = _compact_inventory_text(stripped)
        if not _manual_starts_with_numbered_step(stripped):
            if any(compact_line.startswith(other_action) for other_action in other_actions):
                if any(term in compact_line for term in related_terms):
                    return offset
        offset += len(line) + 1
    return -1


def _manual_slice_content_to_action_span(content: str, action: str, target: str) -> str:
    heading_index = _manual_target_action_heading_index(content, action, target)
    start = heading_index if heading_index >= 0 else 0
    sliced = str(content or "")[start:].strip()
    stop_index = _manual_opposite_target_action_heading_index(sliced, action, target)
    if stop_index > 0:
        sliced = sliced[:stop_index].strip()
    return sliced


def _manual_trim_records_to_target_action(records: list[dict], message: str, action: str) -> list[dict]:
    target = _manual_action_target(message, action)
    if not target:
        return records
    heading_orders = [
        _manual_item_order(record)
        for record in records
        if _manual_target_action_heading_index(record.get("content") or "", action, target) >= 0
    ]
    if not heading_orders:
        return records
    first_heading_order = min(heading_orders)
    compact_target = _compact_inventory_text(target)
    pre_heading_anchor_terms = [
        anchor for anchor in _manual_query_anchor_terms(message)
        if anchor and anchor != compact_target
    ]
    related_stop_orders = [
        _manual_item_order(record)
        for record in records
        if _manual_item_order(record) >= first_heading_order
        and _manual_related_other_action_heading_index(record.get("content") or "", action, target) >= 0
    ]
    related_stop_order = min(related_stop_orders) if related_stop_orders else None
    trimmed: list[dict] = []
    for record in records:
        content = record.get("content") or ""
        record_order = _manual_item_order(record)
        if related_stop_order is not None and record_order > related_stop_order:
            continue
        heading_index = _manual_target_action_heading_index(content, action, target)
        if heading_index >= 0:
            sliced = _manual_slice_content_to_action_span(content, action, target)
            related_index = _manual_related_other_action_heading_index(sliced, action, target)
            if related_index == 0:
                continue
            if related_index > 0:
                sliced = sliced[:related_index].strip()
            if sliced:
                record = {**record, "content": sliced}
                trimmed.append(record)
            continue
        if record_order < first_heading_order:
            compact_content = _compact_inventory_text(content)
            if (
                any(anchor in compact_content for anchor in pre_heading_anchor_terms)
                or (
                    _manual_has_numbered_step_line(content)
                    and not _manual_content_has_opposite_action(content, action)
                )
            ):
                trimmed.append({**record, "content": content})
            continue
        if record_order >= first_heading_order:
            opposite_index = _manual_opposite_target_action_heading_index(content, action, target)
            if opposite_index == 0:
                continue
            if opposite_index > 0:
                opposite_tail = content[opposite_index:]
                if not any(
                    anchor in _compact_inventory_text(opposite_tail)
                    for anchor in pre_heading_anchor_terms
                ):
                    content = content[:opposite_index].strip()
            related_index = _manual_related_other_action_heading_index(content, action, target)
            if related_index == 0:
                continue
            if related_index > 0:
                content = content[:related_index].strip()
            if content:
                trimmed.append({**record, "content": content})
    return trimmed


def _manual_should_trim_to_action(message: str, kind: str) -> bool:
    text = message or ""
    if any(term in text for term in _MANUAL_BROAD_LOCATION_EVIDENCE_TERMS):
        return False
    if kind == "procedure":
        return True
    action = _manual_query_action(text)
    target = _manual_action_target(text, action)
    return bool(kind in {"evidence", "parameter"} and len(_compact_inventory_text(target)) >= 3)


def _manual_starts_with_numbered_step(content: str) -> bool:
    first_line = str(content or "").splitlines()[0].strip()
    return bool(re.match(r"^\s*\d+\s*(?:[、．)）]|\.(?!\d))", first_line))


def _manual_has_numbered_step_line(content: str) -> bool:
    for line in str(content or "").splitlines():
        if re.match(r"^\s*\d+\s*(?:[、．)）]|\.(?!\d))", line.strip()):
            return True
    return False


def _manual_looks_like_part_list_continuation(content: str) -> bool:
    lines = [line.strip() for line in str(content or "").splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    if any(_manual_has_numbered_step_line(line) for line in lines):
        return False
    if any(len(line) > 48 for line in lines):
        return False
    joined = "\n".join(lines)
    if any(
        word in joined
        for action in _MANUAL_ACTION_SYNONYMS
        for word in _MANUAL_ACTION_SYNONYMS[action]
    ):
        return False
    part_markers = (
        "垫圈", "轴承", "半圆键", "齿", "螺栓", "螺母", "挡圈", "销",
        "密封圈", "O型圈", "弹簧", "衬套", "压盘", "从动片", "摩擦片",
        "组件", "分组件", "泵", "盖", "轴", "盘", "片", "圈",
    )
    return bool(
        any(marker in joined for marker in part_markers)
        or re.search(r"(?:φ|Φ|M\d|GB\d|K\d|\d+(?:\.\d+)?\s*[×x]\s*\d)", joined)
    )


def _manual_is_next_section_heading_noise(content: str, message: str) -> bool:
    lines = [line.strip() for line in str(content or "").splitlines() if line.strip()]
    if not lines or len(lines) > 3:
        return False
    first_line = lines[0]
    if not re.match(r"^\s*\d+(?:\.\d+)+\s+\S+", first_line):
        return False
    action = _manual_query_action(message)
    target = _manual_action_target(message, action)
    if action and target and _manual_target_action_heading_index(first_line, action, target) >= 0:
        return False
    return True


def _manual_strip_embedded_tail_heading(content: str, current_title: str = "") -> str:
    """Remove a short standalone heading accidentally glued to the end of a chunk."""
    lines = str(content or "").splitlines()
    while len(lines) >= 2:
        tail = lines[-1].strip()
        if not tail:
            lines.pop()
            continue
        compact_tail = _compact_inventory_text(tail)
        compact_title = _compact_inventory_text(current_title)
        if not compact_tail:
            lines.pop()
            continue
        if compact_title and compact_tail in compact_title:
            break
        if _manual_has_numbered_step_line(tail):
            break
        if len(compact_tail) > 12:
            break
        if re.search(r"[，,。；;：:、]|(?:mm|N·m|N路m)|(?:M\d|GB\d|φ|Φ|×|\d)", tail, flags=re.IGNORECASE):
            break
        if any(
            word in tail
            for words in _MANUAL_ACTION_SYNONYMS.values()
            for word in words
        ):
            break
        # A short noun phrase after a complete sentence is usually the next
        # section title leaked by page/section-boundary OCR chunking.  Do not
        # trim short noun phrases after another short noun phrase: those are
        # often valid exploded-view part-list continuations.
        previous = lines[-2].strip()
        if previous.endswith(("。", "；", ";", ".", "！", "!", "？", "?")):
            lines.pop()
            continue
        break
    return "\n".join(lines).strip()


def _manual_is_outline_navigation_noise(content: str, metadata: dict | None = None) -> bool:
    """Return True for TOC/navigation chunks that only list nearby headings.

    Some OCR chunks carry a section outline such as "7.3 ... / 拆卸... / 检查... /
    安装..." but keep stale metadata from a different page/section.  These chunks
    contain the query words yet have no actionable evidence, so they should not
    compete with real step/check records.
    """
    lines = [line.strip() for line in str(content or "").splitlines() if line.strip()]
    if len(lines) < 3 or len(lines) > 8:
        return False
    first_line = lines[0]
    if not re.match(r"^\s*\d+(?:\.\d+)+\s+\S+", first_line):
        return False

    compact_first = _compact_inventory_text(first_line)
    compact_title = _compact_inventory_text((metadata or {}).get("section_title") or "")
    if compact_title and (compact_first in compact_title or compact_title in compact_first):
        return False

    heading_actions = tuple(
        word
        for action in _MANUAL_ACTION_SYNONYMS
        for word in _MANUAL_ACTION_SYNONYMS[action]
    )
    detail_markers = (
        "：", ":", "。", "；", ";", "，", ",", "、",
        "应", "必须", "不得", "不能", "否则", "注意", "要求", "标准", "扭矩", "扭力",
        "±", "≤", "≥", "mm", "N·m", "M6", "M8", "M10", "φ", "Φ", "×",
    )
    body_lines = lines[1:]
    if any(any(marker in line for marker in detail_markers) for line in body_lines):
        return False
    if any(_manual_has_numbered_step_line(line) for line in body_lines):
        return False
    if any(len(_compact_inventory_text(line)) > 28 for line in body_lines):
        return False
    return all(
        re.match(r"^\s*(?:\d+(?:\.\d+)+\s*)?\S{2,32}$", line)
        and (
            any(line.startswith(action) for action in heading_actions)
            or re.match(r"^\s*\d+(?:\.\d+)+\s+\S+", line)
        )
        for line in body_lines
    )


def _manual_first_line_has_opposite_action(content: str, action: str) -> bool:
    first_line = str(content or "").splitlines()[0].strip()
    return _manual_content_has_opposite_action(first_line, action)


def _manual_item_order(item: dict) -> tuple[int, int, str]:
    meta = item.get("metadata") or {}
    try:
        page = int(meta.get("page_number") or meta.get("page") or 9999)
    except (TypeError, ValueError):
        page = 9999
    source_value = meta.get("source_index")
    if source_value is None:
        source_value = meta.get("chunk_index")
    try:
        source_index = int(source_value) if source_value is not None else 9999
    except (TypeError, ValueError):
        source_index = 9999
    return page, source_index, str(item.get("id") or item.get("doc_id") or "")


def _manual_clean_content(content: str) -> str:
    text = str(content or "").strip()
    text = re.sub(r"\bsource=[^\s，。；;）)]+", "", text)
    text = re.sub(r"\b(?:doc_id|chunk_id|image_url|top_k)\s*[:=]\s*[^\s，。；;）)]+", "", text)
    # Drop PDF page-number footers such as "No. 10 / 41" that leak into chunk text.
    text = re.sub(r"(?im)^\s*no\.?\s*\d+\s*/\s*\d+\s*$", "", text)
    text = re.sub(r"(?i)\s*no\.?\s*\d+\s*/\s*\d+\s*", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _manual_strip_next_procedure_heading(content: str, metadata: dict) -> str:
    current_scope = procedure_scope_from_metadata(metadata)
    if not current_scope:
        return content
    kept_lines: list[str] = []
    for index, line in enumerate(str(content or "").splitlines()):
        next_scope = procedure_scope_from_heading(line.strip()) if index > 0 else None
        if next_scope and next_scope.scope_id != current_scope.scope_id:
            break
        kept_lines.append(line)
    return "\n".join(kept_lines).strip()


def _manual_rebind_embedded_section_heading(content: str, metadata: dict) -> dict:
    """Repair a stale previous-section binding from a leading source heading.

    Some imported page-boundary chunks contain the next section's literal
    heading at the beginning while their metadata still points to the previous
    section.  Rebind only when an indexed full heading is a source-text prefix;
    mentioning another section later in the chunk is not sufficient.
    """
    meta = dict(metadata or {})
    document_id = str(meta.get("document_id") or "").strip()
    compact_content = _compact_inventory_text(content)
    if not document_id or not compact_content:
        return meta
    try:
        from services.retrieval.section_index import SectionTitleIndex

        vector_service = _initialized_or_injected_vector_service()
        if vector_service is None:
            return meta
        section_index = SectionTitleIndex.get_instance()
        section_index.build(vector_service)
        matches = section_index.find(str(content or "")[:240])
    except Exception:
        return meta

    candidates: list[tuple[int, Any]] = []
    for match in matches or []:
        if str(getattr(match, "document_id", "") or "") != document_id:
            continue
        full_title = str(getattr(match, "full_title", "") or "").strip()
        compact_title = _compact_inventory_text(full_title)
        if not compact_title or not compact_content.startswith(compact_title):
            continue
        candidates.append((len(compact_title), match))
    if not candidates:
        return meta

    _, best = max(candidates, key=lambda item: item[0])
    section_id = str(getattr(best, "section_id", "") or "").strip()
    full_title = str(getattr(best, "full_title", "") or "").strip()
    current_section_id = str(meta.get("parent_section_id") or "").strip()
    current_title = str(meta.get("section_title") or "").strip()
    if not section_id or (section_id == current_section_id and full_title == current_title):
        return meta
    meta["original_parent_section_id"] = current_section_id
    meta["original_section_title"] = current_title
    meta["parent_section_id"] = section_id
    meta["section_title"] = full_title
    meta["section_match_ids"] = [section_id]
    meta["embedded_heading_rebound"] = True
    toc_path = str(meta.get("toc_path") or "").strip()
    if toc_path:
        toc_parts = [
            part.strip()
            for part in re.split(r"\s*[>＞]\s*", toc_path)
            if part.strip()
        ]
        current_title_compact = _compact_inventory_text(current_title)
        replace_at = next(
            (
                index
                for index, part in enumerate(toc_parts)
                if current_title_compact
                and _compact_inventory_text(part) == current_title_compact
            ),
            max(len(toc_parts) - 1, 0),
        )
        meta["original_toc_path"] = toc_path
        meta["toc_path"] = " > ".join([*toc_parts[:replace_at], full_title])
    for key in (
        "procedure_scope_id",
        "procedure_heading",
        "procedure_action",
        "procedure_target",
    ):
        meta.pop(key, None)
    rebound_scope = procedure_scope_from_heading(full_title)
    if rebound_scope is not None:
        meta.update(rebound_scope.to_metadata())
    return meta


def _manual_evidence_records(metadata: dict) -> list[dict]:
    records: list[dict] = []
    for item in _iter_structural_recovery_candidate_items(metadata):
        meta = dict(item.get("metadata") or {})
        if meta.get(_STRUCTURAL_RECOVERY_CANDIDATE_SOURCE) == "reference_evidence":
            records.append({
                "id": str(item.get("id") or meta.get("chunk_id") or ""),
                "content": "",
                "metadata": meta,
            })
            continue
        chunk_type = meta.get("chunk_type") or meta.get("source_chunk_type") or ""
        if chunk_type in {"image", "image_summary", "outline"}:
            continue
        content = _manual_clean_content(item.get("content") or item.get("text") or "")
        if not content:
            continue
        meta = _manual_rebind_embedded_section_heading(content, meta)
        if re.fullmatch(r"\d+(?:\.\d+)+\s+.{1,30}", content):
            continue
        if _manual_is_outline_navigation_noise(content, meta):
            continue
        records.append({**item, "content": content, "metadata": meta})
    return records


def _manual_title_section_match_scores(message: str) -> dict[str, int]:
    try:
        from services.retrieval.section_index import SectionTitleIndex

        vector_service = _initialized_or_injected_vector_service()
        if vector_service is None:
            return {}
        section_index = SectionTitleIndex.get_instance()
        section_index.build(vector_service)
        scores: dict[str, int] = {}
        for rank, ref in enumerate(section_index.find(message or "")[:5]):
            section_id = str(getattr(ref, "section_id", "") or "")
            if not section_id:
                continue
            core_title = str(getattr(ref, "core_title", "") or "")
            full_title = str(getattr(ref, "full_title", "") or "")
            specificity = max(len(core_title), len(full_title))
            scores[section_id] = max(scores.get(section_id, 0), 320 - rank * 45 + min(specificity * 8, 80))
        return scores
    except Exception:
        return {}


def _manual_group_score(
    message: str,
    kind: str,
    records: list[dict],
    section_match_ids: set[str],
    title_section_scores: dict[str, int] | None = None,
) -> int:
    query = _compact_inventory_text(message)
    score = 0
    title_section_scores = title_section_scores or {}
    section_ids = {
        str((record.get("metadata") or {}).get("parent_section_id") or "")
        for record in records
    }
    titles = {
        _compact_inventory_text((record.get("metadata") or {}).get("section_title") or (record.get("metadata") or {}).get("chunk_label") or "")
        for record in records
    }
    group_text = _compact_inventory_text(
        "\n".join(
            [*titles]
            + [str(record.get("content") or "") for record in records]
        )
    )
    group_text_lower = group_text.lower()
    anchor_terms = _manual_query_anchor_terms(message)
    for anchor in _manual_atomic_entity_anchor_terms(message):
        if anchor not in anchor_terms:
            anchor_terms.append(anchor)
    if kind == "parameter":
        for anchor in _manual_parameter_anchor_terms(message):
            if anchor not in anchor_terms:
                anchor_terms.append(anchor)
    if kind == "evidence":
        for anchor in _manual_evidence_anchor_terms(message):
            if anchor not in anchor_terms:
                anchor_terms.append(anchor)
    for anchor in anchor_terms:
        anchor_text = _compact_inventory_text(anchor)
        if not anchor_text:
            continue
        anchor_lower = anchor_text.lower()
        if anchor_text in group_text or anchor_lower in group_text_lower:
            score += 500 + min(len(anchor) * 20, 180)
            if any(anchor_text in title or anchor_lower in title.lower() for title in titles):
                score += 80
    for token_group in _manual_flexible_anchor_token_groups(message):
        if all(token and token in group_text_lower for token in token_group):
            score += 220 + min(sum(len(token) for token in token_group) * 18, 180)
            if any(all(token in title.lower() for token in token_group) for title in titles):
                score += 60
    for section_id in section_ids:
        if section_id and section_id in title_section_scores:
            score += title_section_scores[section_id]
        if section_id and section_id in section_match_ids:
            score += 80
    for title in titles:
        if title and (title in query or query in title):
            score += 30
    query_terms = re.findall(r"[一-鿿A-Za-z0-9×.±/-]{2,}", query)
    title_matched_terms: set[str] = set()
    for term in query_terms:
        if term and any(term in title for title in titles):
            score += min(len(term), 12)
            title_matched_terms.add(term)
    for record in records:
        meta = record.get("metadata") or {}
        content = _compact_inventory_text(record.get("content") or "")
        chunk_type = meta.get("chunk_type") or meta.get("source_chunk_type") or ""
        for term in query_terms:
            if term and term not in title_matched_terms and term in content:
                score += min(len(term), 12)
        if kind == "parameter" and chunk_type == "table":
            score += 14
        if kind == "parameter" and re.search(r"\d", content):
            score += 8
            if any(anchor and anchor in content for anchor in _manual_parameter_anchor_terms(message)):
                score += 80
    # For a procedure query ("how to install/remove X, the steps"), a section that
    # carries real operation steps (a ``step_raw`` chunk) should outrank a mere part
    # list, even when the list's title matches the query anchor more literally
    # ("...装配部件清单" contains "装配" which collides with install-type anchors).
    # A pure part-list section (only tables, titled "...清单", no step chunk) is
    # "which parts exist", not "how to do it", so it is de-prioritised here.
    if kind == "procedure":
        group_has_step = any(
            (
                (record.get("metadata") or {}).get("chunk_type")
                or (record.get("metadata") or {}).get("source_chunk_type")
            ) == "step_raw"
            for record in records
        )
        group_has_table = any(
            (
                (record.get("metadata") or {}).get("chunk_type")
                or (record.get("metadata") or {}).get("source_chunk_type")
            ) == "table"
            for record in records
        )
        group_is_part_list_only = (
            not group_has_step
            and group_has_table
            and any("清单" in title for title in titles)
        )
        if group_has_step:
            score += 120
        elif group_is_part_list_only:
            score -= 100
    return score


def _manual_record_from_raw(raw: dict, section_match_ids: set[str] | None = None) -> dict | None:
    item = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw)
    meta = dict(item.get("metadata") or {})
    chunk_type = meta.get("chunk_type") or meta.get("source_chunk_type") or ""
    if chunk_type in {"image", "image_summary", "outline"}:
        return None
    content = _manual_clean_content(item.get("content") or item.get("text") or "")
    if not content:
        return None
    meta = _manual_rebind_embedded_section_heading(content, meta)
    if re.fullmatch(r"\d+(?:\.\d+)+\s+.{1,30}", content):
        return None
    if _manual_is_outline_navigation_noise(content, meta):
        return None
    if section_match_ids:
        meta.setdefault("section_match_ids", list(section_match_ids))
    return {**item, "content": content, "metadata": meta}


def _manual_expand_same_section_records(
    best_group: list[dict],
    section_match_ids: set[str],
    *,
    metadata: dict,
) -> list[dict]:
    if not best_group:
        return best_group
    reference_candidates = [
        record
        for record in best_group
        if (record.get("metadata") or {}).get(_STRUCTURAL_RECOVERY_CANDIDATE_SOURCE)
        == "reference_evidence"
    ]
    answer_records = [
        record
        for record in best_group
        if record not in reference_candidates
    ]
    if not _route_plan_authorizes_structural_lookup(metadata):
        return answer_records
    selected_document_id = str(
        ((metadata or {}).get("route_plan") or {}).get("selected_document_id") or ""
    ).strip()
    first_meta = (reference_candidates[0] if reference_candidates else best_group[0]).get("metadata") or {}
    document_id = str(first_meta.get("document_id") or "")
    section_id = str(first_meta.get("parent_section_id") or "")
    if not document_id or document_id != selected_document_id or not section_id:
        return answer_records
    try:
        vector_service = _initialized_or_injected_vector_service()
        if vector_service is None:
            return answer_records
        raw_records = vector_service.get_section_records(
            document_id,
            section_id,
            limit=200,
            chunk_type=None,
        )
    except Exception:
        return answer_records

    expanded: list[dict] = list(answer_records)
    seen_ids = {str(item.get("id") or item.get("doc_id") or "") for item in expanded}
    seen_content = {str(item.get("content") or "") for item in expanded}
    for raw in raw_records:
        record = _manual_record_from_raw(raw, section_match_ids)
        if not record:
            continue
        record_meta = dict(record.get("metadata") or {})
        if str(record_meta.get("document_id") or "") != document_id:
            continue
        if str(record_meta.get("parent_section_id") or "") != section_id:
            continue
        record_meta.pop(_STRUCTURAL_RECOVERY_CANDIDATE_SOURCE, None)
        record_meta[_STRUCTURAL_RECOVERY_LOOKUP_SOURCE] = "section_text_lookup"
        record["metadata"] = record_meta
        record_id = str(record.get("id") or record.get("doc_id") or "")
        content = str(record.get("content") or "")
        if record_id and record_id in seen_ids:
            continue
        if content and content in seen_content:
            continue
        if record_id:
            seen_ids.add(record_id)
        if content:
            seen_content.add(content)
        expanded.append(record)
    return expanded


def _manual_recover_numbered_steps_from_context(
    records: list[dict],
    *,
    action: str = "",
) -> list[dict]:
    """Recover numbered steps lost by an old parent/child import.

    Older Redis imports occasionally kept the first step only in a neighboring
    chunk's ``context_after`` while the actual child record was absent.  The
    context is still source text from the same section, so recover only numbered
    blocks before the next section/opposite-action heading and attach a stable
    derived provenance id.  No query or case-specific terms are used here.
    """
    if not records:
        return records
    existing_content = {
        _compact_inventory_text(str(record.get("content") or ""))
        for record in records
        if str(record.get("content") or "").strip()
    }
    existing_step_numbers: set[int] = set()
    for record in records:
        first_line = str(record.get("content") or "").splitlines()[0].strip()
        match = re.match(r"^(\d+)\s*(?:[、．)）]|\.(?!\d))", first_line)
        if match:
            existing_step_numbers.add(int(match.group(1)))

    recovered: list[dict] = []
    for anchor in sorted(records, key=_manual_item_order):
        meta = dict(anchor.get("metadata") or {})
        section_id = str(meta.get("parent_section_id") or "").strip()
        document_id = str(meta.get("document_id") or "").strip()
        if not section_id or not document_id:
            continue
        context = str(meta.get("context_after") or "")
        if not context:
            continue
        blocks: list[tuple[int, str]] = []
        current_number: int | None = None
        current_lines: list[str] = []
        for raw_line in context.splitlines():
            line = raw_line.strip()
            if not line:
                if current_number is not None:
                    current_lines.append("")
                continue
            if re.match(r"^\d+(?:\.\d+)+\s+\S+", line):
                break
            if action and _manual_first_line_has_opposite_action(line, action):
                break
            match = re.match(r"^(\d+)\s*(?:[、．)）]|\.(?!\d))\s*(.*)$", line)
            if match:
                if current_number is not None:
                    blocks.append((current_number, "\n".join(current_lines).strip()))
                current_number = int(match.group(1))
                current_lines = [line]
            elif current_number is not None:
                current_lines.append(line)
        if current_number is not None:
            blocks.append((current_number, "\n".join(current_lines).strip()))

        for number, content in blocks:
            compact = _compact_inventory_text(content)
            if not compact or compact in existing_content or number in existing_step_numbers:
                continue
            derived_id = f"{section_id}:context-step:{number}"
            recovered_meta = {
                **meta,
                "chunk_id": derived_id,
                "source_chunk_id": derived_id,
                "chunk_type": "step_raw",
                "chunk_label": "step_raw",
                "answer_role": "procedure_step",
                "context_recovered": True,
                "retrieval_route": "section_context_recovery",
                "source_index": number,
                "child_index": max(number - 1, 0),
            }
            recovered.append({
                "id": derived_id,
                "doc_id": derived_id,
                "content": content,
                "metadata": recovered_meta,
            })
            existing_content.add(compact)
            existing_step_numbers.add(number)

    if not recovered:
        return records
    return dedupe_and_sort_manual_records([*records, *recovered])


def _manual_expand_page_boundary_records(
    best_group: list[dict],
    section_match_ids: set[str],
    *,
    metadata: dict,
) -> list[dict]:
    if not best_group:
        return best_group
    if not _route_plan_authorizes_structural_lookup(metadata):
        return best_group
    selected_document_id = str(
        ((metadata or {}).get("route_plan") or {}).get("selected_document_id") or ""
    ).strip()
    document_id = str((best_group[0].get("metadata") or {}).get("document_id") or "")
    if not document_id or document_id != selected_document_id:
        return best_group
    titles = [
        _compact_inventory_text((record.get("metadata") or {}).get("section_title") or "")
        for record in best_group
    ]
    titles = [title for title in dict.fromkeys(titles) if len(title) >= 4]
    if not titles:
        return best_group
    pages: list[int] = []
    for record in best_group:
        meta = record.get("metadata") or {}
        try:
            page = int(meta.get("page_number") or meta.get("page"))
        except (TypeError, ValueError):
            continue
        if page not in pages:
            pages.append(page)
    if not pages:
        return best_group
    try:
        vector_service = _initialized_or_injected_vector_service()
        if vector_service is None:
            return best_group
    except Exception:
        return best_group

    extra: list[dict] = []
    seen_ids = {str(item.get("id") or item.get("doc_id") or "") for item in best_group}
    seen_content = {str(item.get("content") or "") for item in best_group}
    for page in pages[:4]:
        try:
            raw_records = vector_service.get_page_records(
                document_id,
                page,
                chunk_type=None,
                limit=120,
            )
        except Exception:
            continue
        for raw in raw_records:
            record = _manual_record_from_raw(raw, section_match_ids)
            if not record:
                continue
            record_id = str(record.get("id") or record.get("doc_id") or "")
            content = str(record.get("content") or "")
            compact_content = _compact_inventory_text(content)
            if not any(title and title in compact_content for title in titles):
                continue
            if record_id and record_id in seen_ids:
                continue
            if content and content in seen_content:
                continue
            if record_id:
                seen_ids.add(record_id)
            if content:
                seen_content.add(content)
            extra.append(record)
    return best_group + extra


def _manual_title_match_records(
    message: str,
    allowed_document_id: str = "",
) -> tuple[list[dict], set[str]]:
    try:
        from services.retrieval.section_index import SectionTitleIndex

        vector_service = _initialized_or_injected_vector_service(
            initialize=bool(allowed_document_id)
        )
        if vector_service is None:
            return [], set()
        section_index = SectionTitleIndex.get_instance()
        section_index.build(vector_service)
        refs = section_index.find(message or "")[:5]
    except Exception:
        return [], set()

    records: list[dict] = []
    section_ids: set[str] = set()
    constraints = extract_query_constraints(message)
    for ref in refs:
        document_id = str(getattr(ref, "document_id", "") or "")
        section_id = str(getattr(ref, "section_id", "") or "")
        if not document_id or not section_id:
            continue
        if allowed_document_id and document_id != allowed_document_id:
            continue
        try:
            raw_records = vector_service.get_section_records(
                document_id,
                section_id,
                limit=80,
                chunk_type=None,
            )
        except Exception:
            continue
        accepted_records: list[dict] = []
        for raw in raw_records:
            record = _manual_record_from_raw(raw)
            if not record:
                continue
            record_document_id = str((record.get("metadata") or {}).get("document_id") or "")
            if allowed_document_id and record_document_id != allowed_document_id:
                continue
            if candidate_constraint_conflicts(constraints, record):
                continue
            meta = dict(record.get("metadata") or {})
            meta["original_title_match"] = True
            record["metadata"] = meta
            accepted_records.append(record)
        if accepted_records:
            section_ids.add(section_id)
            for record in accepted_records:
                record["metadata"].setdefault("section_match_ids", [section_id])
            records.extend(accepted_records)
    return records, section_ids


def _manual_records_for_scoped_document(records: list[dict], metadata: dict) -> list[dict]:
    scoped_document_id = str(
        ((metadata or {}).get("scope_decision") or {}).get("document_id") or ""
    ).strip()
    route_document_id = ""
    if _route_plan_authorizes_structural_lookup(metadata):
        route_document_id = str(
            ((metadata or {}).get("route_plan") or {}).get("selected_document_id") or ""
        ).strip()
    if scoped_document_id and route_document_id and scoped_document_id != route_document_id:
        return []
    scoped_document_id = scoped_document_id or route_document_id
    if not scoped_document_id:
        return records
    return [
        record for record in records
        if str((record.get("metadata") or {}).get("document_id") or "") == scoped_document_id
    ]


def _manual_append_unique_records(records: list[dict], extra_records: list[dict]) -> list[dict]:
    if not extra_records:
        return records
    merged = list(records)
    seen_ids = {str(item.get("id") or item.get("doc_id") or "") for item in merged}
    seen_content = {str(item.get("content") or "") for item in merged}
    for record in extra_records:
        record_id = str(record.get("id") or record.get("doc_id") or "")
        content = str(record.get("content") or "")
        if record_id and record_id in seen_ids:
            continue
        if content and content in seen_content:
            continue
        if record_id:
            seen_ids.add(record_id)
        if content:
            seen_content.add(content)
        merged.append(record)
    return merged


def _manual_best_section_records(message: str, kind: str, metadata: dict) -> list[dict]:
    records = _manual_records_for_scoped_document(_manual_evidence_records(metadata), metadata)
    if not records:
        records = []
    retrieval_section_match_ids: set[str] = set()
    for record in records:
        for sid in (record.get("metadata") or {}).get("section_match_ids") or []:
            if sid:
                retrieval_section_match_ids.add(str(sid))
    route_authorizes_lookup = _route_plan_authorizes_structural_lookup(metadata)
    route_document_id = str(
        ((metadata or {}).get("route_plan") or {}).get("selected_document_id") or ""
    ).strip() if route_authorizes_lookup else ""
    title_match_records, title_match_section_ids = (
        _manual_title_match_records(message, route_document_id)
        if route_authorizes_lookup
        else ([], set())
    )
    title_match_records = _manual_records_for_scoped_document(title_match_records, metadata)
    title_match_section_ids = {
        str((record.get("metadata") or {}).get("parent_section_id") or "")
        for record in title_match_records
        if (record.get("metadata") or {}).get("parent_section_id")
    }
    section_match_ids = retrieval_section_match_ids | title_match_section_ids
    scoring_section_match_ids = (
        title_match_section_ids
        if title_match_section_ids
        else retrieval_section_match_ids
    )
    records = _manual_append_unique_records(records, title_match_records)
    if not records:
        return []
    title_section_scores = _manual_title_section_match_scores(message)
    groups: dict[tuple[str, str], list[dict]] = {}
    for record in records:
        meta = record.get("metadata") or {}
        key = (
            str(meta.get("parent_section_id") or ""),
            str(meta.get("section_title") or meta.get("chunk_label") or ""),
        )
        groups.setdefault(key, []).append(record)
    scored = [
        (_manual_group_score(message, kind, group, scoring_section_match_ids, title_section_scores), key, group)
        for key, group in groups.items()
    ]
    if not scored:
        return []
    best_score, _, best_group = max(scored, key=lambda item: item[0])
    if best_score < 18:
        return []
    best_group = _manual_expand_same_section_records(
        best_group,
        section_match_ids,
        metadata=metadata,
    )
    best_group = _manual_expand_page_boundary_records(
        best_group,
        section_match_ids,
        metadata=metadata,
    )
    action = _manual_query_action(message)
    subflow_focused = False
    if action:
        best_group, subflow_focused = _manual_focus_records_to_query_subflow(
            best_group,
            message,
            action,
        )
        if subflow_focused and best_group:
            selected_scope = procedure_scope_from_metadata(best_group[0].get("metadata") or {})
            if selected_scope:
                metadata["_deterministic_answer_procedure_scope_id"] = selected_scope.scope_id
                metadata["_deterministic_answer_procedure_action"] = selected_scope.action
                metadata["_deterministic_answer_procedure_target"] = selected_scope.target
    if kind == "procedure" or subflow_focused:
        best_group = _manual_recover_numbered_steps_from_context(best_group, action=action)
    if action and _manual_should_trim_to_action(message, kind) and not subflow_focused:
        best_group = _manual_trim_records_to_target_action(best_group, message, action)
        action_hits = [
            record for record in best_group
            if _manual_content_has_action(record.get("content") or "", action)
        ]
        if action_hits:
            anchor_terms = _manual_query_anchor_terms(message)
            best_group = [
                record for record in best_group
                if (
                    _manual_content_has_action(record.get("content") or "", action)
                    or (
                        _manual_has_numbered_step_line(record.get("content") or "")
                        and not _manual_first_line_has_opposite_action(record.get("content") or "", action)
                    )
                    or bool(re.match(r"^[A-Za-z][.、．)]", str(record.get("content") or "").strip()))
                    or _manual_looks_like_part_list_continuation(record.get("content") or "")
                    or (
                        kind in {"evidence", "parameter"}
                        and any(
                            anchor in _compact_inventory_text(record.get("content") or "")
                            for anchor in anchor_terms
                        )
                    )
                )
            ]
    if not subflow_focused:
        best_group = _manual_focus_directional_entity_pages(
            best_group,
            message,
            include_adjacent_continuation=kind == "procedure",
        )
    best_group = _manual_drop_contained_duplicate_records(best_group)
    deduped: list[dict] = []
    seen: set[str] = set()
    for record in sorted(best_group, key=_manual_item_order):
        content = _manual_strip_next_procedure_heading(
            record.get("content") or "",
            record.get("metadata") or {},
        )
        if not content:
            continue
        if _manual_is_next_section_heading_noise(content, message):
            continue
        if content in seen:
            continue
        seen.add(content)
        deduped.append({**record, "content": content})
    return deduped


def _manual_focus_directional_entity_pages(
    records: list[dict],
    message: str,
    *,
    include_adjacent_continuation: bool = True,
) -> list[dict]:
    constraints = extract_query_constraints(message)
    required_terms = list(constraints.required_terms)
    if not required_terms:
        required_terms = [
            term for term in _manual_atomic_entity_anchor_terms(message)
            if len(_compact_inventory_text(term)) >= 2
        ]
    if not records or not required_terms:
        return records
    target_pages: set[int] = set()
    for record in records:
        content = str(record.get("content") or "")
        compact_content = _compact_inventory_text(content)
        if not all(_compact_inventory_text(term) in compact_content for term in required_terms):
            continue
        if candidate_constraint_conflicts(constraints, {"content": content, "metadata": {}}):
            continue
        meta = record.get("metadata") or {}
        try:
            target_pages.add(int(meta.get("page_number") or meta.get("page")))
        except (TypeError, ValueError):
            continue
    if not target_pages:
        return records
    focused: list[dict] = []
    for record in records:
        meta = record.get("metadata") or {}
        try:
            page = int(meta.get("page_number") or meta.get("page"))
        except (TypeError, ValueError):
            continue
        if page in target_pages:
            focused.append(record)
    if not include_adjacent_continuation:
        return focused or records
    next_page = max(target_pages) + 1
    for record in sorted(records, key=_manual_item_order):
        meta = record.get("metadata") or {}
        try:
            page = int(meta.get("page_number") or meta.get("page"))
        except (TypeError, ValueError):
            continue
        if page != next_page:
            continue
        continuation_lines: list[str] = []
        reached_subprocedure = False
        for line in str(record.get("content") or "").splitlines():
            compact_line = _compact_inventory_text(line)
            if re.match(r"^(拆卸|安装|检查|更换|装配)[\u4e00-\u9fffA-Za-z0-9]", compact_line):
                reached_subprocedure = True
                break
            continuation_lines.append(line)
        continuation = "\n".join(continuation_lines).strip()
        if continuation:
            focused.append({**record, "content": continuation})
        if reached_subprocedure:
            break
    return focused or records


def _manual_requested_detail_terms(message: str) -> list[str]:
    text = message or ""
    required_terms = []
    if "型号" in text:
        required_terms.append("型号")
    if "材料" in text:
        required_terms.append("材料")
    if "公差" in text:
        required_terms.append("公差")
    if "扩张器" in text:
        required_terms.append("扩张器")
    for match in re.finditer(
        r"([\u4e00-\u9fffA-Za-z0-9]{2,18}(?:间隙|扭矩|力矩|压力|温度|公差))(?:是)?多少",
        text,
    ):
        term = match.group(1)
        if term not in required_terms:
            required_terms.append(term)
    return required_terms


def _manual_requested_presence_terms(message: str) -> list[str]:
    terms: list[str] = []
    for match in re.finditer(r"有([\u4e00-\u9fffA-Za-z0-9×.\-]{2,18})(?:吗|么)", message or ""):
        term = match.group(1)
        if term not in terms:
            terms.append(term)
    return terms


def _manual_answer_should_refuse_detail_query(message: str, records: list[dict]) -> bool:
    evidence = "\n".join(record.get("content") or "" for record in records)
    required_terms = _manual_requested_detail_terms(message)
    compact_evidence = _compact_inventory_text(evidence)

    def structured_parameter_is_supported(term: str) -> bool:
        compact_term = _compact_inventory_text(term)
        parameter = next(
            (
                marker for marker in (
                    "拧紧力矩", "拧紧扭矩", "标准间隙范围", "标准压力范围",
                    "力矩", "扭矩", "间隙", "压力", "温度", "公差",
                )
                if marker in compact_term
            ),
            "",
        )
        if not parameter or not re.search(r"\d", compact_evidence):
            return False
        subject = compact_term.split("时", 1)[-1].split(parameter, 1)[0].rstrip("的")
        subjects = [
            re.sub(r"^(?:安装|拆卸|检查|测量|调整|更换)", "", value).strip()
            for value in re.split(r"(?:和|及|与|、)", subject)
        ]
        subjects = [value for value in subjects if len(value) >= 2]
        if not subjects or not all(value in compact_evidence for value in subjects):
            return False
        if parameter in {"拧紧力矩", "拧紧扭矩", "力矩", "扭矩"}:
            return (
                ("力矩" in compact_evidence or "扭矩" in compact_evidence)
                and bool(re.search(r"N[·.]?m", evidence, flags=re.IGNORECASE))
            )
        if parameter in {"标准压力范围", "压力"}:
            return bool(re.search(r"(?:kPa|MPa|Pa|bar)", evidence, flags=re.IGNORECASE))
        if parameter == "温度":
            return "℃" in evidence or "°C" in evidence
        return parameter.replace("标准", "").replace("范围", "") in compact_evidence

    missing = [
        term
        for term in required_terms
        if _compact_inventory_text(term) not in compact_evidence
        and not structured_parameter_is_supported(term)
    ]
    return bool(required_terms and missing)


def _format_manual_detail_refusal_answer(message: str, records: list[dict]) -> str:
    requested_terms = _manual_requested_detail_terms(message)
    missing_terms = []
    evidence = "\n".join(record.get("content") or "" for record in records)
    for term in requested_terms:
        if term not in evidence and term not in missing_terms:
            missing_terms.append(term)
    if not missing_terms:
        missing_terms = requested_terms
    if "扩张器" in missing_terms and "型号" in missing_terms:
        missing_terms = [
            term for term in missing_terms
            if term not in {"扩张器", "型号"}
        ]
        missing_terms.insert(0, "扩张器型号")
    pages: list[int] = []
    titles: list[str] = []
    for record in records:
        meta = record.get("metadata") or {}
        try:
            page = int(meta.get("page_number") or meta.get("page"))
            if page not in pages:
                pages.append(page)
        except (TypeError, ValueError):
            pass
        title = str(meta.get("section_title") or meta.get("chunk_label") or "").strip()
        if title and title not in titles:
            titles.append(title)
    page_text = ""
    if pages:
        ordered_pages = sorted(pages)
        page_text = f"第{ordered_pages[0]}页" if len(ordered_pages) == 1 else f"第{ordered_pages[0]}-{ordered_pages[-1]}页"
    title_text = f"“{titles[0]}”" if titles else "相关章节"
    missing_text = "、".join(missing_terms)
    supported_presence = [
        term for term in _manual_requested_presence_terms(message)
        if term in evidence
    ]
    supported_text = ""
    if supported_presence:
        supported_text = f"手册列有{'、'.join(supported_presence)}；"
    return (
        f"{supported_text}手册未说明{missing_text}。"
        f"请以原厂手册、配件清单或实物标识为准。\n\n"
        f"（来源：手册{page_text}{title_text}）"
    )


def _has_qualified_manual_evidence(metadata: dict) -> bool:
    """Allow deterministic output only for qualified evidence on new traces.

    Legacy traces predate the qualification contract and remain readable during
    migration; every result produced by the new retrieval path carries the
    `qualification` field and therefore must satisfy it.
    """
    saw_qualification = False
    saw_retrieval_call = False
    saw_result_item = False
    for call_data, payload in _iter_trace_tool_payloads(metadata):
        if str(call_data.get("name") or "") not in {"", "knowledge_retrieval"}:
            continue
        arguments = call_data.get("arguments") or {}
        if isinstance(arguments, dict) and arguments.get("source") == "section_text_lookup":
            continue
        saw_retrieval_call = True
        payload_data = _plain_dict(payload)
        evidence_status = payload_data.get("evidence_status") if payload_data else None
        if evidence_status is not None:
            saw_qualification = True
            if evidence_status == "qualified":
                return True
            continue
        for item in _iter_payload_result_items(payload):
            saw_result_item = True
            item_meta = item.get("metadata") or {}
            qualification = item_meta.get("qualification")
            if qualification is not None:
                saw_qualification = True
                if qualification == "qualified":
                    return True
    if saw_retrieval_call and not saw_result_item and not saw_qualification:
        return False
    return not saw_qualification


def _has_original_title_match_in_source_trace(metadata: dict) -> bool:
    for call_data, payload in _iter_trace_tool_payloads(metadata):
        if str(call_data.get("name") or "") not in {"", "knowledge_retrieval"}:
            continue
        arguments = call_data.get("arguments") or {}
        if isinstance(arguments, dict) and arguments.get("source") == "section_text_lookup":
            continue
        for item in _iter_payload_result_items(payload):
            if bool((item.get("metadata") or {}).get("original_title_match")):
                return True
    return False


def _route_plan_authorizes_structural_lookup(metadata: dict) -> bool:
    """Allow section text recovery only for a resolved component route."""
    route_plan = metadata.get("route_plan")
    if not isinstance(route_plan, dict):
        return False
    selected_document_id = route_plan.get("selected_document_id")
    return bool(
        route_plan.get("action") == RouteAction.GROUNDED_RETRIEVAL.value
        and route_plan.get("entity_role") == "document_component"
        and isinstance(selected_document_id, str)
        and selected_document_id.strip()
    )


def _format_manual_evidence_answer_from_metadata(message: str, metadata: dict) -> str | None:
    """Build a concise answer directly from retrieved manual evidence.

    This is the non-table counterpart of the deterministic inventory path:
    when the retrieved evidence already contains ordered manual text, prefer a
    faithful evidence summary over a free-form rewrite.
    """
    kind = _manual_query_kind(message)
    if not kind:
        return None
    # Evaluate authorization before section lookup mutates the trace.  Records
    # discovered by the fallback lookup may enrich an already-authorized
    # answer, but must not promote excluded/reference-only source evidence.
    source_has_exact_section_match = _has_original_title_match_in_source_trace(metadata)
    source_has_qualified_evidence = _has_qualified_manual_evidence(metadata)
    route_authorizes_lookup = _route_plan_authorizes_structural_lookup(metadata)
    if (
        not source_has_qualified_evidence
        and not source_has_exact_section_match
        and not route_authorizes_lookup
    ):
        return None
    records = _manual_best_section_records(message, kind, metadata)
    if not records:
        return None
    # A deterministic exact-title section lookup is a stronger structural
    # contract than the stale relevance qualification attached to the original
    # semantic hit.  Keep reference-only/excluded records blocked unless the
    # selected records explicitly carry that exact-title provenance marker.
    has_exact_section_match = any(
        bool((record.get("metadata") or {}).get("original_title_match"))
        for record in records
    )
    has_structural_lookup_records = any(
        (record.get("metadata") or {}).get(_STRUCTURAL_RECOVERY_LOOKUP_SOURCE)
        == "section_text_lookup"
        for record in records
    )
    if (
        not _has_qualified_manual_evidence(metadata)
        and not has_exact_section_match
        and not route_authorizes_lookup
    ):
        return None
    if (
        route_authorizes_lookup
        and not source_has_qualified_evidence
        and not source_has_exact_section_match
    ):
        route_direct_text = "\n".join(
            "\n".join(filter(None, (
                str((record.get("metadata") or {}).get("section_title") or ""),
                str(record.get("content") or ""),
            )))
            for record in records
        )
        if not _direct_manual_text_supports_query(route_direct_text, message):
            return None
    original_bundle = _manual_bundle_from_trace(metadata)
    supported_aspects = [
        row
        for row in original_bundle.get("aspect_support") or []
        if isinstance(row, dict) and row.get("supported")
    ]
    if (
        str(original_bundle.get("coverage_status") or "") in {"partial", "unsupported"}
        and not supported_aspects
    ):
        direct_text = "\n".join(
            "\n".join(filter(None, (
                str((record.get("metadata") or {}).get("section_title") or ""),
                str(record.get("content") or ""),
            )))
            for record in records
        )
        direct_records_support_query = _direct_manual_text_supports_query(
            direct_text,
            message,
        )
        if not (
            direct_records_support_query
            and (
                source_has_qualified_evidence
                or has_exact_section_match
                or (route_authorizes_lookup and has_structural_lookup_records)
            )
        ):
            return None
    metadata.setdefault("original_user_message", message)
    _register_direct_manual_evidence(metadata, records, "section_text_lookup")
    metadata["_deterministic_answer_mode"] = "evidence_rendered"
    if _manual_answer_should_refuse_detail_query(message, records):
        return _format_manual_detail_refusal_answer(message, records)

    pages: list[int] = []
    titles: list[str] = []
    section_ids: list[str] = []
    document_ids: list[str] = []
    for record in records:
        meta = record.get("metadata") or {}
        try:
            page = int(meta.get("page_number") or meta.get("page"))
            if page not in pages:
                pages.append(page)
        except (TypeError, ValueError):
            pass
        document_id = str(meta.get("document_id") or "")
        if document_id and document_id not in document_ids:
            document_ids.append(document_id)
        title = str(meta.get("section_title") or meta.get("chunk_label") or "").strip()
        if title and title not in titles:
            titles.append(title)
        section_id = str(meta.get("parent_section_id") or "").strip()
        if section_id and section_id not in section_ids:
            section_ids.append(section_id)

    if pages:
        metadata["_deterministic_answer_evidence_pages"] = sorted(pages)
    if document_ids:
        metadata["_deterministic_answer_document_ids"] = document_ids
    if titles:
        # Use the dominant section's title rather than the first record's.  An
        # expanded cross-page continuation chunk may carry a neighbouring
        # section's title; picking it as titles[0] mislabels the answer and
        # misroutes the direct-image lookup.  Choose the title of the section
        # that contributes the most records.
        section_counts: dict[str, int] = {}
        section_first_title: dict[str, str] = {}
        for record in records:
            meta = record.get("metadata") or {}
            sid = str(meta.get("parent_section_id") or "").strip()
            title = str(meta.get("section_title") or meta.get("chunk_label") or "").strip()
            if not title:
                continue
            section_counts[sid] = section_counts.get(sid, 0) + 1
            section_first_title.setdefault(sid, title)
        if section_counts:
            dominant_sid = max(section_counts, key=lambda s: section_counts[s])
            metadata["_deterministic_answer_section_title"] = section_first_title[dominant_sid]
        else:
            metadata["_deterministic_answer_section_title"] = titles[0]
    if section_ids:
        metadata["_deterministic_answer_section_ids"] = section_ids

    page_text = "对应页"
    if pages:
        ordered_pages = sorted(pages)
        page_text = f"第{ordered_pages[0]}页" if len(ordered_pages) == 1 else f"第{ordered_pages[0]}-{ordered_pages[-1]}页"
    title_text = titles[0] if titles else "相关章节"
    lead = {
        "procedure": "可以按以下顺序操作：",
        "parameter": "相关参数如下：",
    }.get(kind, "可核对以下内容：")
    lines = [lead]

    rendered_number = 0
    active_parent_chunk_id = ""
    for record in records[:12]:
        content = _manual_clean_content(record.get("content") or "")
        content = _manual_strip_embedded_tail_heading(content, title_text)
        if not content:
            continue
        record_metadata = record.get("metadata") or {}
        record_scope = procedure_scope_from_metadata(record_metadata)
        if record_scope and normalize_procedure_target(content) == normalize_procedure_target(record_scope.heading):
            continue
        parent_chunk_id = str(record_metadata.get("parent_chunk_id") or "")
        first_line = content.splitlines()[0].strip()
        if _manual_starts_with_numbered_step(first_line):
            lines.append(content)
            number_match = re.match(r"^\s*(\d+)", first_line)
            if number_match:
                rendered_number = max(rendered_number, int(number_match.group(1)))
            active_parent_chunk_id = parent_chunk_id
        elif parent_chunk_id and parent_chunk_id == active_parent_chunk_id and len(lines) > 1:
            if normalize_procedure_target(content) not in normalize_procedure_target(lines[-1]):
                lines[-1] = f"{lines[-1].rstrip()}\n{content}"
        else:
            rendered_number += 1
            lines.append(f"{rendered_number}. {content}")
            active_parent_chunk_id = parent_chunk_id
    if len(lines) <= 1:
        return None
    lines.extend(["", f"（来源：手册{page_text}“{title_text}”）"])
    return "\n".join(lines).strip()


def _extract_evidence_images(metadata: dict) -> List[EvidenceImage]:
    images: List[EvidenceImage] = []
    seen = set()
    for item in _iter_trace_result_items(metadata):
        item_meta = dict(item.get("metadata") or {})
        image_url = item_meta.get("image_url") or item_meta.get("imageUrl") or item.get("image_url")
        if not image_url:
            continue
        chunk_type = item_meta.get("chunk_type") or item_meta.get("source_chunk_type") or ""
        has_image_metadata = bool(item_meta.get("caption") or item_meta.get("image_title") or item_meta.get("image_name"))
        if chunk_type not in {"image", "image_summary"} and not has_image_metadata:
            continue

        source_chunk_id = str(item.get("id") or item.get("doc_id") or item_meta.get("source_image_id") or "")
        dedupe_key = (image_url, source_chunk_id)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        images.append(
            EvidenceImage(
                image_url=image_url,
                caption=item_meta.get("caption") or item_meta.get("image_title") or item.get("content", ""),
                page=item_meta.get("page_number") or item_meta.get("page"),
                section_title=item_meta.get("section_title", ""),
                document_id=item_meta.get("document_id", ""),
                source_chunk_id=source_chunk_id,
                context_role=item_meta.get("context_role", ""),
                step_id=str((item_meta.get("related_step_chunk_ids") or [""])[0] or ""),
                step_ids=[str(item) for item in item_meta.get("related_step_chunk_ids") or [] if str(item)],
                aspect_id=str((item_meta.get("aspect_ids") or [""])[0] or ""),
                role=str(item_meta.get("binding_role") or item_meta.get("context_role") or ""),
                binding_confidence=float(item_meta.get("binding_confidence") or 0.0),
            )
        )
    return images


async def _collect_direct_section_table_items(message: str, metadata: dict) -> list[dict]:
    """清单直取通道：按确定性章节补全同节全部表格，解决跨页 BOM 只召回一页的问题。"""
    if not _is_inventory_table_query(message):
        return []
    if not _route_plan_authorizes_structural_lookup(metadata):
        return []
    try:
        plan_intent = ""
        document_id = ""
        section_match_ids: List[str] = []
        title_section_ids: List[str] = []
        evidence_section_ids: List[str] = []
        lookup_queries: List[str] = []
        section_titles_by_id: dict[str, str] = {}
        vector_service = None
        route_plan = (metadata or {}).get("route_plan") or {}
        route_document_id = str(route_plan.get("selected_document_id") or "").strip()
        document_id = route_document_id
        query_contract = route_plan.get("query_contract") or {}
        query_component = str(query_contract.get("component") or "").strip()
        query_action = str(query_contract.get("action") or "").strip()

        def append_unique(values: List[str], value: str) -> None:
            if value and value not in values:
                values.append(value)

        for key in ("original_user_message", "user_message", "message"):
            value = str((metadata or {}).get(key) or "").strip()
            if value:
                append_unique(lookup_queries, value)
        append_unique(lookup_queries, message)

        for item in _iter_structural_recovery_candidate_items(metadata):
            item_meta = dict(item.get("metadata") or {})
            candidate_source = item_meta.get(_STRUCTURAL_RECOVERY_CANDIDATE_SOURCE)
            item_document_id = str(item_meta.get("document_id") or "")
            if item_document_id != route_document_id:
                continue
            if item_meta.get("retrieval_plan_intent"):
                plan_intent = str(item_meta["retrieval_plan_intent"])
            sm_ids = item_meta.get("section_match_ids")
            if isinstance(sm_ids, list):
                for sid in sm_ids:
                    append_unique(section_match_ids, str(sid))
            parent_section_id = str(item_meta.get("parent_section_id") or "")
            section_title = str(item_meta.get("section_title") or "").strip()
            if parent_section_id and section_title:
                section_titles_by_id.setdefault(parent_section_id, section_title)
            chunk_type = item_meta.get("chunk_type") or item_meta.get("source_chunk_type") or ""
            if parent_section_id and chunk_type not in {"image", "image_summary"}:
                append_unique(evidence_section_ids, parent_section_id)

        if lookup_queries:
            try:
                from services.knowledge.vector_service import get_vector_service
                from services.retrieval.section_index import SectionTitleIndex

                if vector_service is None:
                    vector_service = get_vector_service()
                section_index = SectionTitleIndex.get_instance()
                section_index.build(vector_service)
                for query in lookup_queries:
                    for ref in section_index.find(query):
                        ref_document_id = str(getattr(ref, "document_id", "") or "")
                        if ref_document_id != route_document_id:
                            continue
                        ref_title = f"{getattr(ref, 'core_title', '')} {getattr(ref, 'full_title', '')}"
                        ref_section_id = str(getattr(ref, "section_id", "") or "")
                        if not ref_section_id:
                            continue
                        if "清单" in ref_title:
                            append_unique(title_section_ids, ref_section_id)
                        append_unique(section_match_ids, ref_section_id)
                        if ref_title.strip():
                            section_titles_by_id.setdefault(ref_section_id, ref_title.strip())
                    if title_section_ids:
                        break
            except Exception:
                pass

        if plan_intent not in ("outline", "procedure") and lookup_queries:
            try:
                from services.retrieval.planner import build_retrieval_plan

                inferred_plan = build_retrieval_plan(
                    lookup_queries[0],
                    section_match_ids=section_match_ids,
                )
                plan_intent = inferred_plan.intent
            except Exception:
                pass

        if title_section_ids and not plan_intent:
            plan_intent = "outline"

        if plan_intent not in ("outline", "procedure"):
            return []

        target_section_ids: List[str] = []
        for sid in title_section_ids:
            append_unique(target_section_ids, sid)
        if not target_section_ids:
            for sid in evidence_section_ids:
                if section_match_ids and sid not in section_match_ids:
                    continue
                append_unique(target_section_ids, sid)
        if not target_section_ids and section_match_ids:
            # 清单标题匹配通常把目标清单节排在首位。
            target_section_ids = section_match_ids[:1]
        if not document_id or not target_section_ids:
            return []

        if vector_service is None:
            from services.knowledge.vector_service import get_vector_service
            vector_service = get_vector_service()

        table_items: list[dict] = []
        seen_ids: set[str] = set()
        for sid in target_section_ids[:3]:
            try:
                records = vector_service.get_section_records(
                    document_id, sid, limit=200, chunk_type="table",
                )
            except Exception:
                continue
            for rec in records:
                rec = rec.model_dump() if hasattr(rec, "model_dump") else dict(rec)
                rec_id = str(rec.get("id") or rec.get("doc_id") or "")
                if rec_id and rec_id in seen_ids:
                    continue
                if rec_id:
                    seen_ids.add(rec_id)
                meta = dict(rec.get("metadata") or {})
                record_document_id = str(meta.get("document_id") or route_document_id)
                if record_document_id != route_document_id:
                    continue
                record_section_id = str(meta.get("parent_section_id") or sid)
                if record_section_id != sid:
                    continue
                locator_title = section_titles_by_id.get(sid, "")
                record_title = str(meta.get("section_title") or "")
                record_content = str(rec.get("content") or rec.get("text") or "")
                table_full = meta.get("table_full")
                support_text = "\n".join(filter(None, (
                    locator_title,
                    record_title,
                    record_content,
                    json.dumps(table_full, ensure_ascii=False) if table_full else "",
                )))
                compact_support = _compact_inventory_text(support_text)
                compact_component = _compact_inventory_text(query_component)
                if compact_component and compact_component not in compact_support:
                    continue
                if not compact_component and sid not in title_section_ids:
                    continue
                if query_action in _MANUAL_ACTION_SYNONYMS and not _direct_manual_text_supports_query(
                    support_text,
                    message,
                ):
                    continue
                meta["document_id"] = route_document_id
                meta["parent_section_id"] = sid
                meta.setdefault("section_match_ids", section_match_ids)
                meta.setdefault("retrieval_plan_intent", plan_intent)
                if sid in title_section_ids:
                    meta["original_title_match"] = True
                rec["metadata"] = meta
                table_items.append(rec)
        if not table_items:
            return []
        _register_direct_manual_evidence(metadata, table_items, "section_table_lookup")
        return table_items
    except Exception:
        return []


async def _collect_direct_section_images(metadata: dict) -> List[EvidenceImage]:
    """直取通道：procedure / outline 意图下，按 section_match_ids 确定性地查库取图。

    不依赖检索排名，走 get_section_records(chunk_type='image') 精确查库。
    消除图片返回的非确定性——目标章节的图只要存在就一定被拿到。
    """
    try:
        route_plan = (metadata or {}).get("route_plan") or {}
        selected_document_id = route_plan.get("selected_document_id")
        if not (
            route_plan.get("action") == RouteAction.GROUNDED_RETRIEVAL.value
            and isinstance(selected_document_id, str)
            and selected_document_id.strip()
            and (
                _has_qualified_manual_evidence(metadata)
                or _route_plan_authorizes_structural_lookup(metadata)
            )
        ):
            return []
        document_id = selected_document_id.strip()
        plan_intent = ""
        section_match_ids: List[str] = []
        primary_section_ids: List[str] = []
        evidence_section_ids: List[str] = []
        lookup_queries: List[str] = []
        vector_service = None

        def append_unique(values: List[str], value: str) -> None:
            if value and value not in values:
                values.append(value)

        for key in ("original_user_message", "user_message", "message"):
            value = str((metadata or {}).get(key) or "").strip()
            if value:
                append_unique(lookup_queries, value)

        for call_data, result_data in _iter_trace_tool_payloads(metadata):
            arguments = call_data.get("arguments") or call_data.get("args") or {}
            arguments = _plain_dict(arguments) if hasattr(arguments, "model_dump") else arguments
            if isinstance(arguments, dict):
                query_arg = str(arguments.get("query") or "").strip()
                if query_arg:
                    append_unique(lookup_queries, query_arg)
            elif isinstance(arguments, str) and arguments.strip():
                append_unique(lookup_queries, arguments.strip())
            for item_data in _iter_payload_result_items(result_data):
                item_meta = dict(item_data.get("metadata") or {})
                if str(item_meta.get("document_id") or "").strip() != document_id:
                    continue
                if item_meta.get("retrieval_plan_intent"):
                    plan_intent = str(item_meta["retrieval_plan_intent"])
                sm_ids = item_meta.get("section_match_ids")
                if isinstance(sm_ids, list) and sm_ids:
                    for sid in sm_ids:
                        append_unique(section_match_ids, str(sid))
                parent_section_id = str(item_meta.get("parent_section_id") or "")
                chunk_type = item_meta.get("chunk_type") or item_meta.get("source_chunk_type") or ""
                if parent_section_id and chunk_type not in {"image", "image_summary"}:
                    append_unique(evidence_section_ids, parent_section_id)
                    if item_meta.get("context_role") == "primary":
                        append_unique(primary_section_ids, parent_section_id)

        if not section_match_ids and lookup_queries:
            try:
                from services.knowledge.vector_service import get_vector_service
                from services.retrieval.section_index import SectionTitleIndex

                vector_service = get_vector_service()
                section_index = SectionTitleIndex.get_instance()
                section_index.build(vector_service)
                for query in lookup_queries:
                    for ref in section_index.find(query):
                        if str(getattr(ref, "document_id", "") or "") != document_id:
                            continue
                        append_unique(section_match_ids, str(ref.section_id))
                    if section_match_ids:
                        break
            except Exception:
                pass

        if plan_intent not in ("procedure", "outline", "safety", "image_identification") and lookup_queries:
            try:
                from services.retrieval.planner import build_retrieval_plan

                inferred_plan = build_retrieval_plan(
                    lookup_queries[0],
                    section_match_ids=section_match_ids,
                )
                plan_intent = inferred_plan.intent
            except Exception:
                pass

        if plan_intent not in ("procedure", "outline", "safety", "image_identification"):
            return []
        target_section_ids: List[str] = []
        for sid in primary_section_ids + evidence_section_ids:
            if section_match_ids and sid not in section_match_ids:
                continue
            append_unique(target_section_ids, sid)
        if not target_section_ids and section_match_ids:
            target_section_ids = section_match_ids[:1]
        if not document_id or not target_section_ids:
            return []

        if vector_service is None:
            from services.knowledge.vector_service import get_vector_service
            vector_service = get_vector_service()
        images: List[EvidenceImage] = []
        image_records: list[dict[str, Any]] = []
        seen_urls: set = set()
        target_procedure_scope_id = str(
            (metadata or {}).get("_deterministic_answer_procedure_scope_id") or ""
        )
        for sid in target_section_ids[:3]:
            try:
                records = vector_service.get_section_records(
                    document_id, sid, limit=20, chunk_type="image",
                )
            except Exception:
                continue
            for rec in records:
                rec = rec.model_dump() if hasattr(rec, "model_dump") else dict(rec)
                meta = dict(rec.get("metadata") or {})
                record_document_id = str(meta.get("document_id") or document_id).strip()
                record_section_id = str(meta.get("parent_section_id") or sid).strip()
                if record_document_id != document_id or record_section_id != sid:
                    continue
                image_scope_ids = {
                    str(value) for value in meta.get("procedure_scope_ids") or [] if str(value)
                }
                if (
                    target_procedure_scope_id
                    and image_scope_ids
                    and target_procedure_scope_id not in image_scope_ids
                ):
                    continue
                image_url = meta.get("image_url") or rec.get("image_url")
                if not image_url or image_url in seen_urls:
                    continue
                seen_urls.add(image_url)
                meta["document_id"] = document_id
                meta["parent_section_id"] = sid
                meta.setdefault("chunk_id", str(rec.get("id") or rec.get("doc_id") or ""))
                rec["metadata"] = meta
                rec.setdefault("content", meta.get("caption") or meta.get("image_title") or "")
                image_records.append(rec)
                images.append(EvidenceImage(
                    image_url=image_url,
                    caption=meta.get("caption") or meta.get("image_title") or "",
                    page=meta.get("page_number") or meta.get("page"),
                    section_title=meta.get("section_title", ""),
                    document_id=meta.get("document_id", ""),
                    source_chunk_id=str(rec.get("id") or rec.get("doc_id") or ""),
                    context_role="direct_lookup",
                    step_id=str((meta.get("related_step_chunk_ids") or [""])[0] or ""),
                    step_ids=[str(value) for value in meta.get("related_step_chunk_ids") or [] if str(value)],
                    role=str(meta.get("binding_role") or "direct_lookup"),
                    binding_confidence=float(meta.get("binding_confidence") or 0.0),
                ))
        _register_direct_manual_evidence(metadata, image_records, "section_image_lookup")
        return images
    except Exception:
        return []


def _merge_evidence_images(
    existing: List[EvidenceImage], direct: List[EvidenceImage],
) -> List[EvidenceImage]:
    """合并 trace 提取的图片和直取通道图片，按 image_url 去重，直取通道的排前面。"""
    seen = set()
    merged: List[EvidenceImage] = []
    direct_list = list(direct or [])
    direct_section_keys = {
        (img.document_id or "", img.section_title or "")
        for img in direct_list
        if img.document_id or img.section_title
    }
    existing_list = []
    for img in list(existing or []):
        if direct_section_keys:
            key = (img.document_id or "", img.section_title or "")
            if key not in direct_section_keys:
                continue
        existing_list.append(img)
    for img in direct_list + existing_list:
        key = img.image_url or img.source_chunk_id or f"image:{len(merged)}"
        if key not in seen:
            merged.append(img)
            seen.add(key)
    return merged


def _evidence_image_page(image: EvidenceImage) -> int | None:
    try:
        return int(image.page)
    except (TypeError, ValueError):
        return None


def _sort_unique_evidence_images(images: List[EvidenceImage]) -> List[EvidenceImage]:
    seen: set[str] = set()
    unique: List[EvidenceImage] = []
    for image in sorted(
        list(images or []),
        key=lambda img: (
            _evidence_image_page(img) if _evidence_image_page(img) is not None else 9999,
            img.source_chunk_id or "",
            img.image_url or "",
        ),
    ):
        key = image.image_url or image.source_chunk_id or f"page:{image.page}:{len(unique)}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(image)
    return unique


_IMAGE_QUERY_STOP_TERMS = {
    "怎么", "如何", "步骤", "流程", "安装", "拆卸", "装配", "部件", "零件", "清单",
    "数量", "扭矩", "扭力", "力矩", "标准", "范围", "多少", "哪些", "什么",
    "应该", "时候", "进行", "查看", "展示", "看看", "对应", "相关", "原文",
    "install", "remove", "check", "show", "list", "parts", "step", "steps",
}


def _image_query_terms(message: str) -> list[str]:
    raw = str(message or "").lower()
    compact = _compact_inventory_text(message).lower()
    terms: set[str] = set()
    for term in re.findall(r"[a-z]+\d*|\d+(?:\.\d+)?[a-z]*", raw):
        if len(term) >= 2 and term not in _IMAGE_QUERY_STOP_TERMS:
            terms.add(term)
    chinese_runs = re.findall(r"[\u4e00-\u9fff]+", compact)
    for run in chinese_runs:
        max_len = min(8, len(run))
        for size in range(max_len, 1, -1):
            for start in range(0, len(run) - size + 1):
                term = run[start:start + size]
                if term in _IMAGE_QUERY_STOP_TERMS:
                    continue
                if any(stop in term and len(term) <= len(stop) + 1 for stop in _IMAGE_QUERY_STOP_TERMS):
                    continue
                terms.add(term)
    return sorted(terms, key=lambda value: (-len(value), value))


def _page_image_matches_query(message: str, record: dict) -> bool:
    terms = _image_query_terms(message)
    if not terms:
        return True
    meta = dict(record.get("metadata") or {})
    target = _compact_inventory_text(
        " ".join(
            str(value or "")
            for value in (
                meta.get("section_title"),
                meta.get("caption"),
                meta.get("image_title"),
                meta.get("image_name"),
                meta.get("visual_context_text"),
                meta.get("contextual_text"),
                record.get("content"),
                record.get("text"),
            )
        )
    ).lower()
    if not target:
        return True
    return any(term in target for term in terms)


def _evidence_image_matches_query_anchor(message: str, image: EvidenceImage) -> bool:
    target = _compact_inventory_text(
        " ".join(
            str(value or "")
            for value in (
                image.section_title,
                image.caption,
                image.source_chunk_id,
            )
        )
    ).lower()
    if not target:
        return False
    anchors = _manual_query_anchor_terms(message)
    if anchors:
        return any(anchor.lower() in target for anchor in anchors)
    return _page_image_matches_query(
        message,
        {
            "content": image.caption or "",
            "metadata": {
                "section_title": image.section_title or "",
                "caption": image.caption or "",
            },
        },
    )


def _section_match_variants(title: str) -> list[str]:
    compact = _compact_inventory_text(title).lower()
    if not compact:
        return []
    variants = [compact]
    without_number = re.sub(r"^\d+(?:\.\d+)*", "", compact).strip()
    if without_number and without_number not in variants:
        variants.append(without_number)
    for suffix in ("部件清单", "零件清单", "料件清单", "配件清单", "清单"):
        if without_number.endswith(suffix):
            subject = without_number[: -len(suffix)].strip()
            if len(subject) >= 3 and subject not in variants:
                variants.append(subject)
            break
    return variants


def _image_matches_target_section(image: EvidenceImage, target_title: str) -> bool:
    target_variants = _section_match_variants(target_title)
    if not target_variants:
        return False
    image_text = _compact_inventory_text(
        " ".join(
            str(value or "")
            for value in (
                image.section_title,
                image.caption,
                image.source_chunk_id,
            )
        )
    ).lower()
    if not image_text:
        return False
    return any(
        variant and (variant in image_text or image_text in variant)
        for variant in target_variants
    )


def _page_image_supports_safe_section_rebinding(
    query: str,
    record: dict,
    target_title: str,
    selected_document_ids: list[str],
    fetched_document_id: str,
) -> bool:
    """Allow a page-local image to recover from an importer section misbinding.

    The importer can attach a figure to the adjacent heading even though the
    figure's page-local visual context contains the complete answer heading and
    requested operation.  Rebinding is deliberately narrow: the answer must
    have one deterministic document, the record must belong to that document,
    and the page-local visual context itself must prove the target section and
    query action/object.  No device, component, page, or case vocabulary is
    encoded here.
    """
    if len(selected_document_ids) != 1 or not target_title:
        return False
    selected_document_id = str(selected_document_ids[0] or "").strip()
    if not selected_document_id or fetched_document_id != selected_document_id:
        return False

    meta = dict(record.get("metadata") or {})
    record_document_id = str(meta.get("document_id") or "").strip()
    if record_document_id and record_document_id != selected_document_id:
        return False
    visual_context = str(meta.get("visual_context_text") or "").strip()
    if not visual_context:
        return False

    compact_target_title = _compact_inventory_text(target_title).lower()
    compact_visual_context = _compact_inventory_text(visual_context).lower()
    if not compact_target_title or compact_target_title not in compact_visual_context:
        return False
    if not _page_image_matches_query(query, record):
        return False

    action = _manual_query_action(query)
    if action and _action_context_score(query, visual_context) <= 0:
        return False
    anchors = _manual_query_anchor_terms(query)
    if anchors and not any(
        _compact_inventory_text(anchor).lower() in compact_visual_context
        for anchor in anchors
    ):
        return False
    return True


def _filter_evidence_images_to_target_section(
    images: List[EvidenceImage],
    metadata: dict,
) -> List[EvidenceImage]:
    """Keep images bound to the final deterministic answer section.

    Same PDF pages can contain the tail of one section and the beginning of the
    next.  Page-level image lookup intentionally closes recall gaps, but final
    response images must be re-bound to the section that actually supplied the
    text/table answer.  Do not use visual_context_text here: it often contains
    neighboring OCR text and is the source of cross-section leakage.
    """
    sorted_images = _sort_unique_evidence_images(images)
    target_title = str((metadata or {}).get("_deterministic_answer_section_title") or "").strip()
    if not sorted_images or not target_title:
        return sorted_images
    matched = [
        image for image in sorted_images
        if _image_matches_target_section(image, target_title)
    ]
    return matched or sorted_images


def _deterministic_document_ids(metadata: dict) -> list[str]:
    document_ids: list[str] = []

    def append(value) -> None:
        value = str(value or "").strip()
        if value and value not in document_ids:
            document_ids.append(value)

    for doc_id in (metadata or {}).get("_deterministic_answer_document_ids") or []:
        append(doc_id)
    for item in _iter_trace_result_items(metadata):
        item_meta = dict(item.get("metadata") or {})
        chunk_type = item_meta.get("chunk_type") or item_meta.get("source_chunk_type") or ""
        if chunk_type in {"image", "image_summary"}:
            continue
        append(item_meta.get("document_id"))
    return document_ids


def _document_source_hints(metadata: dict) -> dict[str, dict[str, str]]:
    hints: dict[str, dict[str, str]] = {}
    for item in _iter_trace_result_items(metadata):
        item_meta = dict(item.get("metadata") or {})
        chunk_type = item_meta.get("chunk_type") or item_meta.get("source_chunk_type") or ""
        if chunk_type in {"image", "image_summary"}:
            continue
        document_id = str(item_meta.get("document_id") or "").strip()
        if not document_id:
            continue
        current = hints.setdefault(document_id, {})
        for key in ("source_file_url", "file_name", "local_path"):
            value = str(item_meta.get(key) or "").strip()
            if value and not current.get(key):
                current[key] = value
    return hints


def _resolve_pdf_source_path(source_file_url: str = "", file_name: str = "", local_path: str = "") -> str:
    candidates: list[str] = []

    def append(value: str) -> None:
        value = str(value or "").strip().strip('"')
        if value and value not in candidates:
            candidates.append(value)

    append(local_path)
    append(source_file_url)
    if file_name:
        append(os.path.join(tempfile.gettempdir(), file_name))
    if source_file_url.startswith(("http://", "https://")):
        parsed = hashlib.md5(source_file_url.encode()).hexdigest()[:12]
        append(os.path.join(tempfile.gettempdir(), f"docparser_{parsed}.pdf"))

    for candidate in candidates:
        if os.path.exists(candidate) and candidate.lower().endswith(".pdf"):
            return candidate
    return ""


def _safe_path_segment(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    return text.strip("._") or hashlib.md5(str(value or "").encode()).hexdigest()[:12]


def _text_evidence_title_for_page(metadata: dict, page: int, document_id: str = "") -> str:
    for item in _iter_trace_result_items(metadata):
        item_meta = dict(item.get("metadata") or {})
        chunk_type = item_meta.get("chunk_type") or item_meta.get("source_chunk_type") or ""
        if chunk_type in {"image", "image_summary"}:
            continue
        if document_id and str(item_meta.get("document_id") or "") != document_id:
            continue
        try:
            item_page = int(item_meta.get("page_number") or item_meta.get("page"))
        except (TypeError, ValueError):
            continue
        if item_page == page:
            return str(item_meta.get("section_title") or item_meta.get("chunk_label") or "")
    return ""


def _render_evidence_pdf_page_image(metadata: dict, document_id: str, page: int) -> EvidenceImage | None:
    source_hints = _document_source_hints(metadata)
    hint = source_hints.get(document_id) or (next(iter(source_hints.values()), {}) if source_hints else {})
    pdf_path = _resolve_pdf_source_path(
        source_file_url=hint.get("source_file_url", ""),
        file_name=hint.get("file_name", ""),
        local_path=hint.get("local_path", ""),
    )
    if not pdf_path or page <= 0:
        return None
    try:
        import fitz

        doc = fitz.open(pdf_path)
        if page > len(doc):
            doc.close()
            return None
        storage_root = _settings.local_file_storage_dir
        doc_key = _safe_path_segment(document_id or os.path.basename(pdf_path))
        render_dir = os.path.join(storage_root, "rendered_pages", doc_key)
        os.makedirs(render_dir, exist_ok=True)
        image_name = f"page_{page:03d}.png"
        image_path = os.path.join(render_dir, image_name)
        if not os.path.exists(image_path):
            pix = doc[page - 1].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            pix.save(image_path)
        doc.close()
    except Exception:
        return None

    public_base = _settings.file_public_base_url.rstrip("/")
    return EvidenceImage(
        image_url=f"{public_base}/rendered_pages/{doc_key}/{image_name}",
        caption=f"第{page}页页面截图",
        page=page,
        section_title=_text_evidence_title_for_page(metadata, page, document_id),
        document_id=document_id,
        source_chunk_id=f"rendered-page:{document_id}:{page}",
        context_role="page_render",
    )


def _collect_direct_evidence_page_images(
    metadata: dict,
    vector_service=None,
) -> List[EvidenceImage]:
    """Fetch images by the pages that supplied the final text/table evidence.

    Some PDF pages contain multiple manual sections.  The image chunk may be
    attached to a neighboring section while the text evidence is attached to the
    precise operation section.  Page-level lookup closes that gap without
    hard-coding page numbers or case ids.
    """
    pages = _text_evidence_pages(metadata)
    if not pages:
        return []
    document_ids = _deterministic_document_ids(metadata)
    if not document_ids:
        return []
    query = str(
        (metadata or {}).get("original_user_message")
        or (metadata or {}).get("user_message")
        or (metadata or {}).get("message")
        or ""
    )
    target_section_title = str(
        (metadata or {}).get("_deterministic_answer_section_title") or ""
    ).strip()
    try:
        if vector_service is None:
            from services.knowledge.vector_service import get_vector_service
            vector_service = get_vector_service()
    except Exception:
        return []

    images: List[EvidenceImage] = []
    evidence_records: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for document_id in document_ids[:2]:
        for page in pages[:8]:
            try:
                records = vector_service.get_page_records(
                    document_id,
                    page,
                    chunk_type="image",
                    limit=20,
                )
            except Exception:
                continue
            page_had_indexed_image = False
            page_had_matched_image = False
            for rec in records:
                page_had_indexed_image = True
                rec = rec.model_dump() if hasattr(rec, "model_dump") else dict(rec)
                if not _page_image_matches_query(query, rec):
                    continue
                meta = dict(rec.get("metadata") or {})
                image_url = meta.get("image_url") or rec.get("image_url")
                if not image_url or image_url in seen_urls:
                    continue
                chunk_id = str(rec.get("id") or rec.get("doc_id") or "")
                candidate = EvidenceImage(
                    image_url=image_url,
                    caption=meta.get("caption") or meta.get("image_title") or rec.get("content", ""),
                    page=meta.get("page_number") or meta.get("page"),
                    section_title=meta.get("section_title", ""),
                    document_id=meta.get("document_id", ""),
                    source_chunk_id=chunk_id,
                    context_role="page_lookup",
                )
                if (
                    target_section_title
                    and candidate.section_title
                    and not _image_matches_target_section(candidate, target_section_title)
                ):
                    if not _page_image_supports_safe_section_rebinding(
                        query,
                        rec,
                        target_section_title,
                        document_ids,
                        document_id,
                    ):
                        continue
                    meta.setdefault("source_section_title", candidate.section_title)
                    meta["section_title"] = target_section_title
                    candidate.section_title = target_section_title
                page_had_matched_image = True
                seen_urls.add(image_url)
                meta.setdefault("chunk_id", chunk_id)
                rec["metadata"] = meta
                rec.setdefault("content", meta.get("caption") or meta.get("image_title") or "")
                evidence_records.append(rec)
                images.append(candidate)
            if not page_had_indexed_image or not page_had_matched_image:
                rendered = _render_evidence_pdf_page_image(metadata, document_id, page)
                if rendered and rendered.image_url not in seen_urls:
                    seen_urls.add(rendered.image_url)
                    images.append(rendered)
                    rendered_chunk_id = rendered.source_chunk_id or f"rendered-page-{document_id}-{page}"
                    evidence_records.append({
                        "id": rendered_chunk_id,
                        "content": rendered.caption or f"手册第{page}页图像",
                        "metadata": {
                            "document_id": rendered.document_id or document_id,
                            "chunk_id": rendered_chunk_id,
                            "page": rendered.page or page,
                            "chunk_type": "image",
                            "image_url": rendered.image_url,
                            "caption": rendered.caption,
                        },
                    })
    _register_direct_manual_evidence(metadata, evidence_records, "page_image_lookup")
    return _sort_unique_evidence_images(images)


def _text_evidence_pages(metadata: dict) -> list[int]:
    """Return page order from non-image evidence in the active target section.

    This is a response-level guardrail: pictures should follow the same pages
    that supplied text/table/step evidence.  It prevents a same-section direct
    image lookup from leaking adjacent opposite-action pages into the UI.
    """
    override_pages = (metadata or {}).get("_deterministic_answer_evidence_pages") or []
    pages: list[int] = []
    for page in override_pages:
        try:
            page_int = int(page)
        except (TypeError, ValueError):
            continue
        if page_int not in pages:
            pages.append(page_int)
    if pages:
        return pages

    section_match_ids: set[str] = set()
    non_image_items: list[dict] = []
    for item in _iter_trace_result_items(metadata):
        item_meta = dict(item.get("metadata") or {})
        for sid in item_meta.get("section_match_ids") or []:
            if sid:
                section_match_ids.add(str(sid))
        chunk_type = item_meta.get("chunk_type") or item_meta.get("source_chunk_type") or ""
        if chunk_type in {"image", "image_summary"}:
            continue
        non_image_items.append(item)

    pages: list[int] = []
    for item in non_image_items:
        item_meta = dict(item.get("metadata") or {})
        parent_section_id = str(item_meta.get("parent_section_id") or "")
        if section_match_ids and parent_section_id and parent_section_id not in section_match_ids:
            continue
        page = item_meta.get("page_number") or item_meta.get("page")
        try:
            page_int = int(page)
        except (TypeError, ValueError):
            continue
        if page_int not in pages:
            pages.append(page_int)
    return pages


_EXPLICIT_SINGLE_PAGE_PATTERNS = (
    "只要这一步",
    "只要这一页",
    "只要这页",
    "只返回这一步",
    "只返回这一页",
    "对应图只要",
    "对应图片只要",
    "那一页的",
    "那一步对应",
    "这一步对应的图",
    "这一页对应的图",
    "只返回检查对应图",
    "只返回",
)


def _query_explicit_single_page_intent(query: str) -> bool:
    """True when the user explicitly asks for only one step/page's image.

    Examples: "只要这一步对应的图", "对应图片只要安装右盖那一页的",
    "检查凸轮轴...只返回检查对应图".  These override same-section image
    expansion — adjacent pages of the same section must not be shown.
    """
    text = (query or "").replace(" ", "")
    return any(pattern in text for pattern in _EXPLICIT_SINGLE_PAGE_PATTERNS)


def _page_action_scores(
    metadata: dict,
    pages: list[int],
    action: str,
) -> dict[int, int]:
    """Score each page by how strongly its *text* context matches the action.

    Image captions are often empty for this manual, so the opposite-action
    filter that relies on image context fails.  The per-page step/text chunks
    do carry the action verbs (拆卸 vs 安装), so we use those instead.
    """
    if not action or not pages:
        return {}
    text_by_page = _text_context_by_page_for_image_narrowing(metadata, set(pages))
    # The react trace does not always carry text result_data (e.g. direct image
    # lookup path).  Fall back to reading each page's text/step chunks from the
    # index so the action direction can still be scored.
    if not any(text_by_page.get(page) for page in pages):
        text_by_page = _page_text_context_from_index(metadata, pages)
    action_words = _MANUAL_ACTION_SYNONYMS.get(action, ())
    opposite_words = _MANUAL_OPPOSITE_ACTIONS.get(action, ())
    scores: dict[int, int] = {}
    for page in pages:
        context = text_by_page.get(page, "")
        score = sum(2 for word in action_words if word and word in context)
        score -= sum(2 for word in opposite_words if word and word in context)
        scores[page] = score
    return scores


def _page_text_context_from_index(metadata: dict, pages: list[int]) -> dict[int, str]:
    """Read each page's non-image text/step chunks directly from the index.

    Used when the react trace lacks text result_data.  Concatenates the section
    step/text content so action verbs (拆卸 vs 安装) can be detected per page.
    """
    document_ids = (metadata or {}).get("_deterministic_answer_document_ids") or []
    if not document_ids:
        return {}
    vector_service = _initialized_or_injected_vector_service()
    if vector_service is None:
        return {}
    result: dict[int, str] = {}
    for page in pages:
        parts: list[str] = []
        for document_id in document_ids:
            if not document_id:
                continue
            try:
                records = vector_service.get_page_records(document_id, page, limit=30)
            except Exception:
                continue
            for raw in records or []:
                record = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw)
                meta = dict(record.get("metadata") or {})
                chunk_type = meta.get("chunk_type") or meta.get("source_chunk_type") or ""
                if chunk_type in {"image", "image_summary"}:
                    continue
                parts.append(str(record.get("text") or record.get("content") or ""))
        result[page] = " ".join(part for part in parts if part)
    return result


def _narrow_evidence_pages_by_action(
    metadata: dict,
    evidence_pages: list[int],
    query: str,
) -> list[int]:
    """When the answer section spans pages that split 拆卸/安装 across pages,
    keep only the pages whose text context matches the query action.

    Also honours an explicit single-page request by collapsing to the single
    best-matching page.  Returns evidence_pages unchanged when the signal is
    ambiguous (all pages score equally) so we never drop legitimate evidence.
    """
    if len(evidence_pages) <= 1:
        return evidence_pages
    action = _manual_query_action(query)
    if not action:
        return evidence_pages
    scores = _page_action_scores(metadata, evidence_pages, action)
    if not scores:
        return evidence_pages
    best = max(scores.values())
    worst = min(scores.values())
    # Ambiguous: every page matches the action equally -> keep all.
    if best == worst:
        return evidence_pages
    explicit_single = _query_explicit_single_page_intent(query)
    if explicit_single:
        # User asked for only one step/page: collapse to the best-matching page,
        # but only when a positive-scoring page exists (otherwise keep all).
        if best > 0:
            best_pages = [p for p in evidence_pages if scores[p] == best]
            return best_pages or evidence_pages
        return evidence_pages
    # Non-explicit: never drop a page that positively matches the action just
    # because another page scores higher (multi-page same-action procedures are
    # common).  Only drop pages where the opposite action strictly dominates
    # (negative score) AND at least one positive-scoring page remains.
    has_positive = any(score > 0 for score in scores.values())
    has_negative = any(score < 0 for score in scores.values())
    if not (has_positive and has_negative):
        return evidence_pages
    kept = [p for p in evidence_pages if scores[p] >= 0]
    return kept or evidence_pages


def _align_evidence_images_to_text_evidence_pages(
    images: List[EvidenceImage],
    metadata: dict,
) -> List[EvidenceImage]:
    """Filter and order evidence images using text/table/step evidence pages.

    The retrieval stage may return all images from a matched section.  For
    manuals, adjacent pages often contain the opposite action (拆卸 vs 安装).
    The text evidence pages are a stronger binding signal for what the answer
    actually used, so the UI images should be aligned to those pages.
    """
    sorted_images = _sort_unique_evidence_images(images)
    if not sorted_images:
        return []
    evidence_pages = _text_evidence_pages(metadata)
    if not evidence_pages:
        return sorted_images
    query = str(
        (metadata or {}).get("original_user_message")
        or (metadata or {}).get("user_message")
        or (metadata or {}).get("message")
        or ""
    )
    # When the answer section spans pages that split 拆卸/安装, or the user asks
    # for only one step/page's image, narrow evidence pages by action direction
    # using per-page text context (image captions are usually empty here).
    has_deterministic_pages = bool(
        (metadata or {}).get("_deterministic_answer_evidence_pages")
    )
    if not has_deterministic_pages or _query_explicit_single_page_intent(query):
        evidence_pages = _narrow_evidence_pages_by_action(metadata, evidence_pages, query)
    allowed = set(evidence_pages)
    explicit_single = _query_explicit_single_page_intent(query)
    max_evidence_page = max(evidence_pages)
    allow_adjacent_continuation = (
        _manual_query_kind(query) == "procedure" and not explicit_single
    )
    filtered = [
        image for image in sorted_images
        if (_evidence_image_page(image) in allowed)
        or (
            allow_adjacent_continuation
            and
            _evidence_image_page(image) == max_evidence_page + 1
            and _evidence_image_matches_query_anchor(query, image)
        )
    ]
    if filtered:
        return filtered
    # No candidate image falls on the evidence pages (or a valid continuation
    # page).  Do not fall back to unrelated candidates — that resurrects the
    # opposite-action / wrong-page images the evidence pages were meant to
    # exclude.  When every candidate is off the evidence pages, the target page
    # simply has no figure, so return nothing.
    candidate_pages = {
        _evidence_image_page(image) for image in sorted_images
    }
    if candidate_pages and candidate_pages.isdisjoint(allowed):
        return []
    return sorted_images


_IMAGE_TARGET_SPECIFIC_TERMS = (
    "标记",
    "朝向",
    "朝哪",
    "哪边",
    "方向",
    "朝下",
    "朝上",
    "朝外",
    "朝内",
    "开口",
    "缺口",
    "错开",
    "位置",
    "哪里",
    "插在",
    "数量",
    "参数",
    "标准",
    "范围",
    "扭矩",
    "扭力",
    "力矩",
    "间隙",
    "要求",
)


def _image_query_has_specific_target(query: str) -> bool:
    compact_query = _compact_inventory_text(query).lower()
    if not compact_query:
        return False
    if any(term in compact_query for term in _IMAGE_TARGET_SPECIFIC_TERMS):
        return True
    if re.search(r"[a-z]\d*|[a-z][/\-]?[a-z]|\d+(?:\.\d+)?", compact_query):
        return True
    return False


def _image_specific_anchor_terms(query: str) -> list[str]:
    compact_query = _compact_inventory_text(query).lower()
    anchors: list[str] = []

    def add(term: str) -> None:
        value = _compact_inventory_text(term).lower()
        value = re.sub(r"^(?:哪两个|哪些|哪个|哪张|什么|几个|多少|要拆|要装|应当|应该)+", "", value)
        value = re.sub(r"(?:是多少|是什么|怎么做|怎么装|怎么拆|要求|步骤|方法|位置)$", "", value)
        if len(value) < 2:
            return
        if value in {"安装", "拆卸", "检查", "装配", "清单", "步骤", "发动机"}:
            return
        if value not in anchors:
            anchors.append(value)

    # Extract drawing labels by shape, not by a catalogue of known parts.
    # Examples include K口, M区, Q槽, IN标记 and future unseen labels.
    for match in re.finditer(
        r"(?<![a-z0-9])[a-z][a-z0-9/\-]*(?:标记|[\u4e00-\u9fff])",
        compact_query,
    ):
        add(match.group(0))

    # Dimension-qualified objects are equally structural.  Split on natural
    # connectors first so the captured noun cannot consume the next object.
    clauses = re.split(r"(?:以及|并且|和|与|及|或|、|，|,|；|;)", compact_query)
    dimensioned_object_pattern = re.compile(
        r"(?:[φΦ]\s*)?\d+(?:\.\d+)?"
        r"(?:\s*[×xX*]\-?\s*\d+(?:\.\d+)?)+"
        r"[\u4e00-\u9fff]{0,12}"
    )
    for clause in clauses:
        for match in dimensioned_object_pattern.finditer(clause):
            add(match.group(0))
    return anchors


def _text_context_by_page_for_image_narrowing(metadata: dict, candidate_pages: set[int]) -> dict[int, str]:
    page_parts: dict[int, list[str]] = {page: [] for page in candidate_pages}
    for item in _iter_trace_result_items(metadata):
        item_meta = dict(item.get("metadata") or {})
        chunk_type = item_meta.get("chunk_type") or item_meta.get("source_chunk_type") or ""
        if chunk_type in {"image", "image_summary"}:
            continue
        try:
            page = int(item_meta.get("page_number") or item_meta.get("page"))
        except (TypeError, ValueError):
            continue
        if page not in candidate_pages:
            continue
        page_parts.setdefault(page, []).extend(
            str(value or "")
            for value in (
                item_meta.get("section_title"),
                item_meta.get("chunk_label"),
                item.get("content"),
                item.get("text"),
            )
        )
    return {
        page: " ".join(part for part in parts if part)
        for page, parts in page_parts.items()
    }


def _narrow_evidence_images_to_query_target_pages(
    images: List[EvidenceImage],
    metadata: dict,
    vector_service=None,
    *,
    force: bool = False,
) -> List[EvidenceImage]:
    """Narrow over-expanded image evidence to the pages that match the query target.

    Text answers intentionally expand same-section/page-boundary evidence for
    completeness.  Images need stricter binding: when a user asks for one
    specific visual/parameter/sub-step, adjacent pages from the same expanded
    text section should not automatically be shown.
    """
    sorted_images = _sort_unique_evidence_images(images)
    image_pages = {
        page for image in sorted_images
        for page in [_evidence_image_page(image)]
        if page is not None
    }
    if len(image_pages) <= 1:
        return sorted_images

    query = str(
        (metadata or {}).get("original_user_message")
        or (metadata or {}).get("user_message")
        or (metadata or {}).get("message")
        or ""
    )
    anchors = _image_specific_anchor_terms(query)
    semantic_anchors: list[str] = []
    structured_contract = _structured_query_contract(metadata or {})
    for field_name in ("component", "orientation"):
        raw_value = str(structured_contract.get(field_name) or "")
        for value in re.split(r"[、,，;/；\s]+", raw_value):
            normalized = _compact_inventory_text(value).lower()
            if len(normalized) < 2:
                continue
            if normalized not in semantic_anchors:
                semantic_anchors.append(normalized)
            if normalized not in anchors:
                anchors.append(normalized)
    query_action = _manual_query_action(query)
    if (
        not force
        and not _image_query_has_specific_target(query)
        and not anchors
        and not query_action
    ):
        return sorted_images

    try:
        from services.retrieval.image_selector import PageEvidence, score_pages_for_image_query
    except Exception:
        return sorted_images

    text_by_page = _text_context_by_page_for_image_narrowing(metadata, image_pages)
    has_cross_page_table_evidence = any(
        str(
            (item.get("metadata") or {}).get("chunk_type")
            or (item.get("metadata") or {}).get("source_chunk_type")
            or ""
        ) in {"table", "table_row", "table_summary"}
        and len(set(_inventory_declared_pages(item.get("metadata") or {}))) > 1
        for item in _iter_trace_result_items(metadata)
    )
    images_by_page: dict[int, list[EvidenceImage]] = {}
    for image in sorted_images:
        page = _evidence_image_page(image)
        if page is None:
            continue
        images_by_page.setdefault(page, []).append(image)

    image_context_by_page = {
        page: " ".join(
            _image_context_for_action_filter(image, vector_service=vector_service)
            for image in page_images
        )
        for page, page_images in images_by_page.items()
    }

    page_evidence = []
    for page in sorted(image_pages):
        page_images = images_by_page.get(page, [])
        image_context = image_context_by_page.get(page, "")
        group_key = " ".join(
            dict.fromkeys(
                str(image.section_title or "")
                for image in page_images
                if str(image.section_title or "").strip()
            )
        )
        page_evidence.append(
            PageEvidence(
                page=page,
                text=text_by_page.get(page, ""),
                image_text=image_context,
                group_key=group_key,
                images=[
                    {
                        "doc_id": image.source_chunk_id or image.image_url or f"image:{page}",
                        "content": image.caption or "",
                        "metadata": {
                            "chunk_type": "image",
                            "page": page,
                            "section_title": image.section_title or "",
                        },
                    }
                    for image in page_images
                ],
            )
        )

    if anchors:
        specific_text_anchor_hits: dict[int, int] = {}
        text_anchor_hits: dict[int, int] = {}
        image_anchor_hits: dict[int, int] = {}
        combined_anchor_hits: dict[int, int] = {}
        for page in sorted(image_pages):
            compact_text = _compact_inventory_text(
                text_by_page.get(page, "")
            ).lower()
            compact_image_text = _compact_inventory_text(
                image_context_by_page.get(page, "")
            ).lower()
            compact_combined_text = _compact_inventory_text(
                f"{text_by_page.get(page, '')} {image_context_by_page.get(page, '')}"
            ).lower()
            specific_text_anchor_hits[page] = sum(
                1 for anchor in anchors if anchor and anchor in compact_text
            )
            text_anchor_hits[page] = sum(
                1 for anchor in semantic_anchors if anchor and anchor in compact_text
            )
            image_anchor_hits[page] = sum(
                1 for anchor in anchors if anchor and anchor in compact_image_text
            )
            combined_anchor_hits[page] = sum(
                1 for anchor in anchors if anchor and anchor in compact_combined_text
            )
        # Prefer what the image itself depicts.  Cross-page table chunks can
        # legitimately retain the first page as their text metadata, so using
        # that stale page number as an equal image signal would bind the right
        # answer to the wrong picture.  Fall back to combined text only when
        # visual contexts cannot distinguish the candidates.
        if (
            not has_cross_page_table_evidence
            and max(specific_text_anchor_hits.values(), default=0) > 0
            and len(set(specific_text_anchor_hits.values())) > 1
        ):
            anchor_hits = specific_text_anchor_hits
        elif (
            max(image_anchor_hits.values(), default=0) > 0
            and len(set(image_anchor_hits.values())) > 1
        ):
            anchor_hits = image_anchor_hits
        elif (
            not has_cross_page_table_evidence
            and
            max(text_anchor_hits.values(), default=0) > 0
            and len(set(text_anchor_hits.values())) > 1
        ):
            anchor_hits = text_anchor_hits
        else:
            anchor_hits = combined_anchor_hits
        max_hits = max(anchor_hits.values(), default=0)
        if max_hits > 0:
            selected_pages = {
                page for page, hits in anchor_hits.items()
                if hits == max_hits
            }
            if selected_pages and selected_pages != image_pages:
                narrowed = [
                    image for image in sorted_images
                    if _evidence_image_page(image) in selected_pages
                ]
                if narrowed:
                    return narrowed

    if query_action:
        action_scores = {
            page: _action_context_score(
                query,
                f"{text_by_page.get(page, '')} {image_context_by_page.get(page, '')}",
            )
            for page in sorted(image_pages)
        }
        best_action_score = max(action_scores.values(), default=0)
        if best_action_score > 0 and len(set(action_scores.values())) > 1:
            selected_pages = {
                page
                for page, score in action_scores.items()
                if score == best_action_score
            }
            narrowed = [
                image for image in sorted_images
                if _evidence_image_page(image) in selected_pages
            ]
            if narrowed:
                return narrowed

    scores = score_pages_for_image_query(query, page_evidence)
    if not scores:
        return sorted_images
    best = scores[0]
    if best.score < 18:
        return sorted_images
    second_score = scores[1].score if len(scores) > 1 else 0.0
    dominant = best.score >= second_score + 18 or best.score >= max(second_score * 1.55, 1.0)
    if not dominant:
        return sorted_images

    threshold = max(best.score - 8.0, best.score * 0.86)
    selected_pages = {
        score.page for score in scores
        if score.score >= threshold
    }
    if not selected_pages or selected_pages == image_pages:
        return sorted_images
    narrowed = [
        image for image in sorted_images
        if _evidence_image_page(image) in selected_pages
    ]
    return narrowed or sorted_images


def _image_context_for_action_filter(image: EvidenceImage, vector_service=None) -> str:
    parts = [image.caption or "", image.section_title or ""]
    try:
        if vector_service is None:
            vector_service = _initialized_or_injected_vector_service()
            if vector_service is None:
                return " ".join(parts)
        page = _evidence_image_page(image)
        if not image.document_id or page is None:
            return " ".join(parts)
        records = vector_service.get_page_records(
            image.document_id,
            page,
            chunk_type="image",
            limit=20,
        )
    except Exception:
        return " ".join(parts)

    for raw in records or []:
        record = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw)
        meta = dict(record.get("metadata") or {})
        record_url = meta.get("image_url") or record.get("image_url")
        record_id = str(record.get("id") or record.get("doc_id") or "")
        if image.image_url and record_url and image.image_url != record_url:
            continue
        if image.source_chunk_id and record_id and image.source_chunk_id != record_id:
            continue
        page_local_visual_text = str(meta.get("visual_context_text") or "").strip()
        if page_local_visual_text:
            parts.extend(
                str(value or "")
                for value in (
                    meta.get("caption"),
                    meta.get("image_title"),
                    page_local_visual_text,
                )
            )
        else:
            parts.extend(
                str(value or "")
                for value in (
                    record.get("content"),
                    record.get("text"),
                    meta.get("caption"),
                    meta.get("image_title"),
                    meta.get("contextual_text"),
                )
            )
        break
    return " ".join(parts)


def _action_context_score(query: str, context: str) -> int:
    action = _manual_query_action(query)
    if not action:
        return 0
    target = _manual_action_target(query, action)
    compact_context = _compact_inventory_text(context)
    if not compact_context:
        return 0
    action_words = _MANUAL_ACTION_SYNONYMS.get(action, ())
    opposite_words = _MANUAL_OPPOSITE_ACTIONS.get(action, ())
    score = sum(2 for word in action_words if word and word in compact_context)
    score -= sum(2 for word in opposite_words if word and word in compact_context)
    if target:
        compact_target = _compact_inventory_text(target)
        if f"{action}{compact_target}" in compact_context:
            score += 4
        for opposite_word in opposite_words:
            if f"{opposite_word}{compact_target}" in compact_context:
                score -= 4
    return score


def _image_context_is_inventory_noise_for_query(query: str, context: str, score: int) -> bool:
    if _is_inventory_table_query(query):
        return False
    if _manual_query_kind(query) != "procedure":
        return False
    compact_context = _compact_inventory_text(context)
    if score > 0:
        return False
    action = _manual_query_action(query)
    if action and any(word and word in compact_context for word in _MANUAL_ACTION_SYNONYMS.get(action, ())):
        return False
    inventory_markers = ("清单", "BOM", "料件名称", "数量", "序号", "部件清单", "零件清单")
    return any(marker in compact_context for marker in inventory_markers)


def _filter_evidence_images_by_action_context(
    images: List[EvidenceImage],
    metadata: dict,
    vector_service=None,
) -> List[EvidenceImage]:
    sorted_images = _sort_unique_evidence_images(images)
    if len({_evidence_image_page(image) for image in sorted_images}) <= 1:
        return sorted_images
    query = str(
        (metadata or {}).get("original_user_message")
        or (metadata or {}).get("user_message")
        or (metadata or {}).get("message")
        or ""
    )
    if not _manual_query_action(query):
        return sorted_images
    scored: list[tuple[int, EvidenceImage, str]] = []
    for image in sorted_images:
        context = _image_context_for_action_filter(image, vector_service=vector_service)
        scored.append((_action_context_score(query, context), image, context))
    evidence_pages = set(_text_evidence_pages(metadata))
    has_positive_action_image = any(score > 0 for score, _, _ in scored)
    action = _manual_query_action(query)
    compact_target = _compact_inventory_text(_manual_action_target(query, action))
    positive_pages = [
        page
        for score, image, _ in scored
        if score > 0
        for page in [_evidence_image_page(image)]
        if page is not None
    ]
    positive: list[EvidenceImage] = []
    for score, image, context in scored:
        if score > 0:
            positive.append(image)
            continue
        image_page = _evidence_image_page(image)
        if image_page not in evidence_pages:
            continue
        compact_context = _compact_inventory_text(context)
        later_positive_page_exists = (
            image_page is not None
            and len(evidence_pages) > 1
            and any(positive_page > image_page for positive_page in positive_pages)
        )
        overridden_by_stronger_action_image = (
            has_positive_action_image
            and score < 0
            and len(compact_target) >= 2
            and compact_target in compact_context
            and later_positive_page_exists
        )
        if overridden_by_stronger_action_image:
            continue
        if _image_context_is_inventory_noise_for_query(query, context, score):
            continue
        positive.append(image)
    if not positive:
        evidence_page_images = [
            image for image in sorted_images
            if _evidence_image_page(image) in evidence_pages
        ]
        if evidence_page_images:
            return _sort_unique_evidence_images(evidence_page_images)
        return sorted_images
    if len(positive) < len(sorted_images):
        return _sort_unique_evidence_images(positive)
    return sorted_images


def _query_allows_rendered_page_fallback(query: str) -> bool:
    compact_query = _compact_inventory_text(query)
    visual_terms = (
        "图片", "图示", "插图", "看图", "对应图", "如图",
        "哪里", "位置", "插在哪里", "朝哪", "朝向", "哪边", "方向",
        "标记", "对齐", "缺口",
    )
    if any(term in compact_query for term in visual_terms):
        return True
    fact_only_terms = (
        "多少", "哪些", "是什么", "是多少", "数量", "规格",
        "扭矩", "力矩", "扭力", "涂什么",
    )
    if any(term in compact_query for term in fact_only_terms):
        return False
    return _manual_query_kind(query) == "procedure"


def _structured_query_contract(metadata: dict) -> dict:
    """Return the normalized semantic contract already produced by routing."""
    route_plan = metadata.get("route_plan")
    if isinstance(route_plan, dict):
        query_contract = route_plan.get("query_contract")
        if isinstance(query_contract, dict):
            return query_contract
    intent_decision = metadata.get("intent_decision")
    return intent_decision if isinstance(intent_decision, dict) else {}


def _route_scoped_visual_evidence_allowed(
    metadata: dict,
    images: List[EvidenceImage],
) -> bool:
    """Authorize visual evidence independently from text coverage.

    A failed text-coverage audit must not erase images that were deterministically
    recovered from the one document selected by RoutePlan.  The authorization is
    deliberately narrower than the normal response policy: unresolved entities,
    multiple documents and foreign-document images all fail closed.
    """
    route_plan = metadata.get("route_plan")
    if not isinstance(route_plan, dict):
        return False
    selected_document_id = route_plan.get("selected_document_id")
    if not (
        route_plan.get("action") == RouteAction.GROUNDED_RETRIEVAL.value
        and route_plan.get("entity_role") == "document_component"
        and isinstance(selected_document_id, str)
        and selected_document_id.strip()
        and images
    ):
        return False
    image_document_ids = {
        str(image.document_id or "").strip()
        for image in images
    }
    return image_document_ids == {selected_document_id.strip()}


def _select_evidence_images_for_response(
    images: List[EvidenceImage],
    metadata: dict,
) -> List[EvidenceImage]:
    """Select images once from the final audited evidence bindings."""
    from services.retrieval.image_selector import (
        ImageSelectionContract,
        PageEvidence,
        select_pages_for_contract,
    )
    from services.retrieval.query_understanding import has_negative_image_request

    policy = metadata.get("response_policy") if isinstance(metadata.get("response_policy"), dict) else {}
    query = str(
        metadata.get("original_user_message")
        or metadata.get("user_message")
        or metadata.get("message")
        or ""
    )
    target_pages = [
        int(page)
        for page in (
            metadata.get("_deterministic_answer_evidence_pages")
            or metadata.get("allowed_evidence_pages")
            or _text_evidence_pages(metadata)
        )
        if str(page).isdigit()
    ]
    target_pages = list(dict.fromkeys(target_pages))
    structured_contract = _structured_query_contract(metadata)
    has_structured_visual_focus = bool(
        str(structured_contract.get("orientation") or "").strip()
    )
    configured_mode = str(metadata.get("query_understanding_selection_mode") or "")
    if not configured_mode:
        for item in _iter_trace_result_items(metadata):
            item_metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            configured_mode = str(item_metadata.get("query_understanding_selection_mode") or "")
            if configured_mode:
                break
    route_scoped_visual_evidence_allowed = _route_scoped_visual_evidence_allowed(
        metadata,
        images,
    )
    metadata["route_scoped_visual_evidence_allowed"] = route_scoped_visual_evidence_allowed
    if (
        (policy and policy.get("images_allowed") is False and not route_scoped_visual_evidence_allowed)
        or has_negative_image_request(query)
    ):
        mode = "none"
    elif has_structured_visual_focus:
        mode = "single_target"
    elif configured_mode in {"single_target", "evidence_pages", "section_overview"}:
        mode = configured_mode
    elif _query_explicit_single_page_intent(query):
        mode = "single_target"
    elif _manual_query_kind(query) == "procedure" or len(target_pages) > 1:
        mode = "evidence_pages"
    else:
        mode = "section_overview"

    excluded_pages = [
        int(value)
        for value in re.findall(r"(?:不要|排除|不含|去掉|别用)[^。；，,]{0,12}?第?\s*(\d+)\s*页", query)
    ]
    mentioned_pages = [int(value) for value in re.findall(r"第\s*(\d+)\s*页", query)]
    explicit_pages = [page for page in mentioned_pages if page not in set(excluded_pages)]
    allowed_document_ids = [
        str(value) for value in metadata.get("allowed_document_ids") or [] if str(value).strip()
    ]
    candidates = _sort_unique_evidence_images(images)
    if allowed_document_ids:
        scoped = [image for image in candidates if image.document_id in set(allowed_document_ids)]
        candidates = scoped
    allows_rendered_page_fallback = (
        _query_allows_rendered_page_fallback(query)
        or (
            metadata.get("deterministic_table_answer") is True
            and metadata.get("_deterministic_answer_table_complete") is True
            and len(target_pages) > 1
        )
    )
    if not allows_rendered_page_fallback:
        candidates = [
            image for image in candidates
            if (image.context_role or image.role) != "page_render"
        ]
        if (
            metadata.get("_deterministic_answer_evidence_pages")
        ):
            target_page_set = set(target_pages)
            allowed_candidate_pages = set(target_page_set)
            if mode == "section_overview":
                for item in _iter_trace_result_items(metadata):
                    item_metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                    chunk_type = str(
                        item_metadata.get("chunk_type")
                        or item_metadata.get("source_chunk_type")
                        or ""
                    )
                    if chunk_type not in {"table", "table_row", "table_summary"}:
                        continue
                    item_pages = set(_inventory_declared_pages(item_metadata))
                    try:
                        item_page = int(
                            item_metadata.get("page_number")
                            or item_metadata.get("page")
                        )
                    except (TypeError, ValueError):
                        item_page = None
                    if item_page is not None:
                        item_pages.add(item_page)
                    if item_pages.intersection(target_page_set):
                        allowed_candidate_pages.update(item_pages)
            candidates = [
                image for image in candidates
                if _evidence_image_page(image) in allowed_candidate_pages
            ]
    if mode != "none":
        candidates = _filter_evidence_images_to_target_section(candidates, metadata)
        candidates = _narrow_evidence_images_to_query_target_pages(
            candidates,
            metadata,
            force=mode == "single_target",
        )

    target_step_ids = tuple(
        str(value) for value in metadata.get("allowed_source_chunk_ids") or [] if str(value).strip()
    )
    contract = ImageSelectionContract(
        mode=mode,
        target_pages=tuple(target_pages),
        target_evidence_ids=tuple(
            str(value) for value in metadata.get("allowed_evidence_refs") or [] if str(value).strip()
        ),
        target_step_ids=target_step_ids,
        document_id=allowed_document_ids[0] if len(allowed_document_ids) == 1 else "",
        action=_manual_query_action(query),
        orientation=next(
            (term for term in ("朝上", "朝下", "朝外", "朝内", "顺时针", "逆时针") if term in query),
            "",
        ),
        explicit_pages=tuple(explicit_pages),
        excluded_pages=tuple(excluded_pages),
    )
    images_by_page: dict[int, list[EvidenceImage]] = {}
    for image in candidates:
        page = _evidence_image_page(image)
        if page is not None:
            images_by_page.setdefault(page, []).append(image)
    text_by_page = _text_context_by_page_for_image_narrowing(metadata, set(images_by_page))
    page_evidence = [
        PageEvidence(
            page=page,
            text=text_by_page.get(page, ""),
            image_text=" ".join(
                " ".join(filter(None, (image.caption, image.section_title, image.context_role)))
                for image in page_images
            ),
            group_key=" ".join(dict.fromkeys(
                image.section_title for image in page_images if image.section_title
            )),
            images=[
                {
                    "doc_id": image.source_chunk_id or image.image_url,
                    "content": image.caption,
                    "metadata": {"chunk_type": "image", "page": page},
                }
                for image in page_images
            ],
        )
        for page, page_images in sorted(images_by_page.items())
    ]
    selected_pages = select_pages_for_contract(query, page_evidence, contract)
    selected = [image for image in candidates if _evidence_image_page(image) in set(selected_pages)]
    if target_step_ids:
        target_step_set = set(target_step_ids)

        def is_target_bound(image: EvidenceImage) -> bool:
            binding_ids = set(image.step_ids)
            if image.step_id:
                binding_ids.add(image.step_id)
            if image.source_chunk_id:
                binding_ids.add(image.source_chunk_id)
            return bool(target_step_set.intersection(binding_ids))

        if mode == "evidence_pages":
            page_scoped: list[EvidenceImage] = []
            for page in selected_pages:
                page_images = [
                    image for image in selected
                    if _evidence_image_page(image) == page
                ]
                step_bound = [
                    image for image in page_images
                    if is_target_bound(image)
                ]
                if step_bound:
                    page_scoped.extend(step_bound)
                    continue
                page_scoped.extend(
                    image for image in page_images
                    if not (image.step_ids or image.step_id)
                )
            selected = page_scoped
        else:
            step_bound = [
                image for image in selected
                if is_target_bound(image)
            ]
            if step_bound:
                selected = step_bound
    selected = _sort_unique_evidence_images(selected)
    if mode == "single_target":
        selected = selected[:1]
    metadata["image_selection_contract"] = {
        "mode": mode,
        "selection_mode": mode,
        "target_pages": target_pages,
        "target_evidence_ids": list(contract.target_evidence_ids),
        "target_step_ids": list(contract.target_step_ids),
        "explicit_pages": explicit_pages,
        "excluded_pages": excluded_pages,
        "selected_pages": [
            page for page in (_evidence_image_page(image) for image in selected) if page is not None
        ],
    }
    return [
        image.model_copy(update={
            "role": image.role or mode,
            "binding_confidence": max(
                image.binding_confidence,
                1.0 if (
                    _evidence_image_page(image) in set(target_pages)
                    or set(image.step_ids).intersection(target_step_ids)
                ) else 0.5,
            ),
        })
        for image in _sort_unique_evidence_images(selected)
    ]


_SUSPENDED_IMAGE_REFERENCES = ("如图所示", "按图所示", "见下图", "如下图")


def _apply_final_image_contract(
    message: str,
    images: List[EvidenceImage],
    metadata: dict,
) -> tuple[str, List[EvidenceImage]]:
    policy = metadata.get("response_policy") if isinstance(metadata.get("response_policy"), dict) else {}
    if (
        policy
        and policy.get("images_allowed") is False
        and not _route_scoped_visual_evidence_allowed(metadata, images)
    ):
        images = []
    if images:
        return message, images
    cleaned = str(message or "")
    for phrase in _SUSPENDED_IMAGE_REFERENCES:
        cleaned = cleaned.replace(phrase, "")
    cleaned = re.sub(r"[，、；：]?s*（?详见图示）?", "", cleaned) if "详见图示" in cleaned else cleaned
    return cleaned.strip(), []


async def _run_rag_fast_path(request: ChatRequest, input_data: AgentInput) -> AgentOutput | None:
    """执行 RAG -> 单次 LLM 生成的轻量链路；失败时返回 None 交给 ReAct 回退。"""
    total_t0 = time.time()
    retrieval_t0 = time.time()
    scope = (input_data.context or {}).get("retrieval_scope") or {}
    retrieval = await get_knowledge_retrieval_tool().run(
        query=request.message,
        top_k=5,
        document_id=scope.get("document_id"),
        device_type=scope.get("device_type"),
    )
    retrieval_ms = int((time.time() - retrieval_t0) * 1000)
    if not retrieval.success or not retrieval.data:
        logger.warning(
            "[chat][fast_path] session=%s retrieval failed_or_empty duration_ms=%s error=%s",
            request.session_id,
            retrieval_ms,
            retrieval.error,
        )
        return None

    evidence_items = retrieval.data
    trace = [{
        "iteration": 1,
        "action": "tool_call",
        "duration_ms": retrieval_ms,
        "tool_calls": [{
            "name": "knowledge_retrieval",
            "arguments": {"query": request.message, "top_k": 5, **scope},
            "result_summary": str(evidence_items)[:200],
            "result_data": [item.model_dump() if hasattr(item, "model_dump") else item for item in evidence_items],
        }],
    }]
    table_metadata = {
        "react_trace": trace,
        "user_message": request.message,
        "original_user_message": request.message,
    }
    direct_table_items = await _collect_direct_section_table_items(request.message, table_metadata)
    table_answer = _format_inventory_table_answer_from_metadata(
        request.message,
        table_metadata,
        direct_table_items,
    )
    if table_answer:
        total_ms = int((time.time() - total_t0) * 1000)
        fast_metadata = {
            "execution_mode": "rag_table_direct",
            "react_trace": trace,
            "react_iterations": 1,
            "deterministic_table_answer": True,
            "user_message": request.message,
            "original_user_message": request.message,
            "phase_timings_ms": {
                "retrieval": retrieval_ms,
                "llm_generation": 0,
                "fast_path_total": total_ms,
            },
        }
        for key in (
            "_deterministic_answer_mode",
            "_deterministic_answer_evidence_pages",
            "_deterministic_answer_document_ids",
            "_deterministic_answer_section_title",
            "_deterministic_answer_section_ids",
            "_deterministic_answer_table_complete",
        ):
            if key in table_metadata:
                fast_metadata[key] = table_metadata[key]
        logger.info(
            "[chat][fast_path] session=%s direct_table_answer retrieval_ms=%s total_ms=%s evidence_count=%s",
            request.session_id,
            retrieval_ms,
            total_ms,
            len(evidence_items),
        )
        output = AgentOutput(
            agent_name="fix_agent",
            message=table_answer,
            tools_used=["knowledge_retrieval"],
            metadata=fast_metadata,
            latency_ms=total_ms,
            raw_response={"content": table_answer},
        )
        return _finalize_knowledge_output(request.message, output)

    evidence_text = "\n\n".join(
        _evidence_item_to_text(item, idx)
        for idx, item in enumerate(evidence_items, start=1)
    )
    messages = [
        {
            "role": "system",
            "content": (
                "你是设备检修知识库问答助手。必须基于给定知识库证据回答；"
                "证据不足时明确说明不足，不要编造参数、型号或操作步骤。"
                "严格按证据原文中的步骤数量和顺序输出，不要自行新增步骤、合并步骤或拆分步骤。"
                "禁止使用 emoji。"
                "不允许把多个信息点挤在同一整段中。"
                "普通解释使用自然段；当内容包含编号、清单、选项、步骤或文件列表时，每一项必须单独换行。"
                "编号格式使用\"1. 内容\"\"2. 内容\"，不要把多个编号写在同一行。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"用户问题：{request.message}\n\n"
                f"知识库证据：\n{evidence_text}\n\n"
                "请用中文回答，必要时列出依据和不确定点。"
                "如果回答包含截止时间、比赛流程、注意事项等多个信息块，请使用清晰小段落和逐行编号。"
            ),
        },
    ]

    llm_t0 = time.time()
    response = await get_llm_service().chat(messages=messages, temperature=0.1)
    llm_ms = int((time.time() - llm_t0) * 1000)
    total_ms = int((time.time() - total_t0) * 1000)

    logger.info(
        "[chat][fast_path] session=%s retrieval_ms=%s llm_ms=%s total_ms=%s evidence_count=%s",
        request.session_id,
        retrieval_ms,
        llm_ms,
        total_ms,
        len(evidence_items),
    )

    output = AgentOutput(
        agent_name="fix_agent",
        message=response.get("content", ""),
        tools_used=["knowledge_retrieval"],
        metadata={
            "execution_mode": "rag_fast_path",
            "react_trace": trace,
            "react_iterations": 1,
            "phase_timings_ms": {
                "retrieval": retrieval_ms,
                "llm_generation": llm_ms,
                "fast_path_total": total_ms,
            },
        },
        latency_ms=total_ms,
        raw_response=response,
    )
    return _finalize_knowledge_output(request.message, output)


@app.post("/ai/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """
    核心对话接口 —— FixAgent ReAct 推理 + 3层确定性校验

    流程：
    1. FixAgent 通过 ReAct 循环自主决策工具调用
    2. 3层校验：检索依据校验 → 图谱路径校验 → 安全规则引擎
    3. 返回最终结果（含校验标注和安全补充）
    """
    try:
        chat_t0 = time.time()
        logger.info(f"[chat] 会话={request.session_id} 消息长度={len(request.message)}")

        input_data = await _prepare_chat_agent_input(request)

        fix_t0 = time.time()
        fix_result = None
        review_level = "full"
        fix_result = await _try_causal_follow_up_resolution(request, input_data)
        if fix_result is not None:
            review_level = "light"
        if fix_result is None:
            fix_result = await _try_route_plan_direct(request, input_data)
        if fix_result is not None:
            review_level = "light"
        if fix_result is None:
            fix_result = await _try_response_policy_direct(request, input_data)
        if fix_result is not None:
            review_level = "light"
        if fix_result is None:
            fix_result = _try_scope_guard(request, input_data)
        if fix_result is not None:
            review_level = "light"
        if fix_result is None:
            fix_result = await _try_domain_rule_direct(request, input_data)
        if fix_result is not None:
            review_level = "light"
        elif _should_use_rag_fast_path(request):
            fix_result = await _run_rag_fast_path(request, input_data)
            if fix_result is not None:
                review_level = "light"

        if fix_result is None:
            fix_result = await get_fix_agent().run_with_react(input_data)
        fix_result.metadata.setdefault("user_message", input_data.user_message)
        fix_result.metadata.setdefault("original_user_message", request.message)
        if input_data.context and input_data.context.get("intent_decision"):
            fix_result.metadata["intent_decision"] = input_data.context["intent_decision"]
        if input_data.context and input_data.context.get("scope_decision"):
            fix_result.metadata.setdefault("scope_decision", input_data.context["scope_decision"])
        if input_data.context and input_data.context.get("response_policy"):
            fix_result.metadata.setdefault("response_policy", input_data.context["response_policy"])
        if input_data.context and input_data.context.get("route_plan"):
            fix_result.metadata.setdefault("route_plan", input_data.context["route_plan"])
        fix_result = _enforce_route_document_gate(fix_result, input_data)
        fix_phase_ms = int((time.time() - fix_t0) * 1000)
        logger.info(
            "[chat][phase] session=%s execution_mode=%s fix_phase_ms=%s tools=%s",
            request.session_id,
            fix_result.metadata.get("execution_mode"),
            fix_phase_ms,
            fix_result.tools_used,
        )

        if fix_result.metadata.get("status") == "error":
            logger.warning(f"[chat] 会话={request.session_id} 诊断Agent错误: {fix_result.metadata.get('error_detail')}")
            return JSONResponse(
                status_code=500,
                content=ChatResponse(
                    success=False,
                    code=500,
                    session_id=request.session_id,
                    message=strip_user_visible_emojis(fix_result.message),
                    tools_used=None,
                    latency_ms=fix_result.latency_ms
                ).model_dump(by_alias=True)
            )

        review_t0 = time.time()
        if _is_deterministic_direct_output(fix_result):
            final_result = fix_result
        else:
            final_result = await get_review_agent().review(fix_result, level=review_level)
        if "react_trace" not in final_result.metadata and fix_result.metadata.get("react_trace"):
            final_result.metadata["react_trace"] = fix_result.metadata["react_trace"]
        review_phase_ms = int((time.time() - review_t0) * 1000)

        verification = final_result.metadata.get("verification", {})
        has_issues = final_result.metadata.get("verification_has_issues", False)
        total_phase_ms = int((time.time() - chat_t0) * 1000)

        logger.info(
            f"[chat] 会话={request.session_id} 完成 "
            f"有问题={'是' if has_issues else '否'} "
            f"review_level={review_level} "
            f"fix_phase={fix_phase_ms}ms review_phase={review_phase_ms}ms total={total_phase_ms}ms "
            f"返回耗时={final_result.latency_ms}ms"
        )

        response_policy = (
            final_result.metadata.get("response_policy")
            if isinstance(final_result.metadata.get("response_policy"), dict)
            else {}
        )
        structural_recovery_allowed = _route_plan_authorizes_structural_lookup(
            final_result.metadata
        )
        manual_overrides_allowed = (
            response_policy.get("mode") == "PENDING_RETRIEVAL"
            or response_policy.get("manual_citation_allowed") is not False
            or structural_recovery_allowed
        )
        direct_table_items = (
            await _collect_direct_section_table_items(request.message, final_result.metadata)
            if manual_overrides_allowed
            else []
        )

        # 低置信度检索时，跳过表格答案覆盖，保留 review 后的原始答案+声明
        low_confidence = final_result.metadata.get("low_confidence_retrieval", False)
        if low_confidence:
            response_message, diagnosis_items = _extract_structured_chat_payload(final_result.message)
            verification = final_result.metadata.get("verification", {})
            has_issues = final_result.metadata.get("verification_has_issues", False)
        else:
            manual_evidence_answer = None
            table_answer = (
                _format_inventory_table_answer_from_metadata(
                    request.message,
                    final_result.metadata,
                    direct_table_items,
                )
                if manual_overrides_allowed
                else None
            )
            if table_answer:
                final_result.metadata["deterministic_table_answer"] = True
                final_result.metadata["deterministic_table_answer_source"] = "api_response_override"
                response_message = table_answer
                diagnosis_items = None
                verification = {}
                has_issues = False
            else:
                manual_evidence_answer = (
                    _format_manual_evidence_answer_from_metadata(
                        request.message,
                        final_result.metadata,
                    )
                    if manual_overrides_allowed
                    else None
                )
                if manual_evidence_answer:
                    final_result.metadata["deterministic_manual_evidence_answer"] = True
                    final_result.metadata["deterministic_manual_evidence_answer_source"] = "api_response_override"
                    response_message = manual_evidence_answer
                    diagnosis_items = None
                    verification = {}
                    has_issues = False
                else:
                    response_message, diagnosis_items = _extract_structured_chat_payload(final_result.message)
            if not table_answer and not manual_evidence_answer and not _is_deterministic_direct_output(final_result):
                follow_up = build_follow_up(input_data.user_message, diagnosis_items, final_result.metadata)
                if follow_up:
                    final_result.metadata["execution_mode"] = "causal_follow_up_question"
                    final_result.metadata["confidence_source"] = "causal_follow_up"
                    final_result.metadata["diagnostic_follow_up"] = follow_up
                    final_result.metadata["pending_clarification"] = follow_up
                    final_result.tools_used = list(final_result.tools_used or [])
                    if FOLLOW_UP_TOOL_NAME not in final_result.tools_used:
                        final_result.tools_used.append(FOLLOW_UP_TOOL_NAME)
                    response_message = format_follow_up_message(follow_up)
                    diagnosis_items = None
                    verification = {}
                    has_issues = False
        evidence_images = _extract_evidence_images(final_result.metadata)
        # 直取通道：procedure 意图下，按确定性章节查库补图
        if manual_overrides_allowed:
            direct_images = await _collect_direct_section_images(final_result.metadata)
            if direct_images:
                evidence_images = _merge_evidence_images(evidence_images, direct_images)
            page_images = _collect_direct_evidence_page_images(final_result.metadata)
            if page_images:
                evidence_images = _merge_evidence_images(evidence_images, page_images)

        pre_audit_message = response_message
        final_result = await _finalize_knowledge_output_with_fallback(
            request,
            input_data,
            final_result,
            candidate_message=response_message,
        )
        response_message = final_result.message
        if (
            final_result.metadata.get("execution_mode") == "maintenance_ai_fallback_after_retrieval"
            and not _route_scoped_visual_evidence_allowed(final_result.metadata, evidence_images)
        ):
            evidence_images = []
            diagnosis_items = None
            verification = {}
            has_issues = False
        if response_message != pre_audit_message:
            diagnosis_items = None
            verification = {}
            has_issues = False
        evidence_images = _select_evidence_images_for_response(evidence_images, final_result.metadata)
        response_message, evidence_images = _apply_final_image_contract(
            response_message,
            evidence_images,
            final_result.metadata,
        )
        response_message = strip_user_visible_emojis(response_message)
        _sync_pending_clarification_state(request.session_id, final_result.metadata)

        return ChatResponse(
            session_id=request.session_id,
            message=response_message,
            tools_used=final_result.tools_used if final_result.tools_used else None,
            latency_ms=final_result.latency_ms,
            verification=verification if has_issues else None,
            diagnosis_items=diagnosis_items,
            evidence_images=evidence_images,
            metadata=final_result.metadata,
        )
    except Exception as e:
        logger.exception(f"[chat] session={request.session_id} error")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 检修助手出口兜底 ====================

@app.post("/ai/task/voice/decide", response_model=VoiceTaskDecision)
async def task_voice_decide(request: VoiceTaskRequest) -> VoiceTaskDecision:
    """Structured voice-maintenance decision endpoint used by Java."""
    try:
        return await get_voice_task_agent().decide(request)
    except Exception as e:
        logger.exception("[task_voice_decide] session=%s error", request.session_id)
        raise HTTPException(status_code=500, detail=str(e))


_MAINT_REFUSAL_HINTS = (
    "暂不能生成", "无法形成可确认", "无法给出", "资料不足以",
    "未检索到可支撑", "当前知识库未检索到", "当前资料不足",
)

# 最终硬保险话术：模型与兜底都翻车时，至少给工人一句安全、可操作的人话（绝不漏 JSON/冷拒答）
_MAINT_SAFE_FALLBACK_LINE = (
    "目前手册和图谱里还没有完全匹配这一情形的内容。请先确保安全、停止任何可能造成损伤的强行操作；"
    "如方便，请补充故障的具体部位与现象，或拍一张现场照片发我，我据此给你更针对性的下一步。"
)


def _render_maintenance_block(m: dict) -> str:
    """把 Java 注入的检修上下文渲染成纯文本背景块（兜底单轮对话用）。"""
    if not isinstance(m, dict):
        return ""
    lines = []
    t = m.get("task") or {}
    lines.append(
        f"设备：{t.get('deviceName', '') or '未知'}；"
        f"故障：{t.get('faultDescription', '') or '未填写'}；"
        f"检修等级：{t.get('maintenanceLevel', '') or '-'}"
    )
    prog = m.get("progress") or {}
    if prog:
        lines.append(
            f"进度：当前第 {prog.get('current', '?')} 步 / 共 {prog.get('total', '?')} 步，"
            f"已完成 {prog.get('done', 0)} 步"
        )
    fs = m.get("focusedStep")
    if isinstance(fs, dict):
        lines.append(f"【当前聚焦：第 {fs.get('sortOrder', '?')} 步】{fs.get('title', '')}")
        if fs.get("content"):
            lines.append(f"操作内容：{fs.get('content')}")
        if fs.get("safetyNote"):
            lines.append(f"安全提示：{fs.get('safetyNote')}")
        if fs.get("sources"):
            lines.append(f"该步参考依据：{fs.get('sources')}")
        if fs.get("status"):
            lines.append(f"该步当前状态：{fs.get('status')}")
        if fs.get("aiReason"):
            lines.append(f"AI 验收意见：{fs.get('aiReason')}")
        if fs.get("note"):
            lines.append(f"工人本步备注：{fs.get('note')}")
    ov = m.get("overview")
    if ov:
        lines.append("全部步骤：" + "；".join(ov))
    rej = m.get("rejectedSteps")
    if rej:
        lines.append("未通过步骤驳回理由：" + "；".join(
            f"第{r.get('sortOrder', '?')}步「{r.get('title', '')}」{r.get('aiReason', '')}" for r in rej
        ))
    return "【任务背景】\n" + "\n".join(lines)


def _is_unhelpful_maintenance_reply(message: str) -> bool:
    """判断检修助手回复是否「翻车」：控制结构 JSON / 残缺 JSON / 套话式软拒答。

    注意软拒答的判据要克制：长答案里偶尔出现"需现场确认/无法给出精确值"属正常谨慎措辞，
    不应判翻车（否则正经技术问答会被误降级为安全话术）。仅当「整段简短、且本身就是一句拒答」
    才判翻车——与 _maintenance_fallback_answer._bad 的判据保持一致。
    """
    s = (message or "").strip()
    if not s:
        return True
    plain, _ = _extract_structured_chat_payload(s)  # 合法 {"message",..} 会被抽成干净文本
    p = (plain or "").strip()
    if not p:
        return True
    # 控制结构 / 残缺 JSON：真翻车
    if (p.startswith("{") or p.startswith("```")
            or "needs_more_tools" in p or "needs_user_clarification" in p
            or '"status"' in p[:40]):
        return True
    # 软拒答：仅当「短（<80字）且整体像拒答」才判翻车；长答案中的谨慎措辞放行
    if len(p) < 80 and any(h in p for h in _MAINT_REFUSAL_HINTS):
        return True
    return False


def _clean_fallback_text(text: str) -> str:
    """抢救兜底输出：把可能的 {"message":..} 抽成纯文本。"""
    plain, _ = _extract_structured_chat_payload((text or "").strip())
    return (plain or "").strip()


_FALLBACK_UNVERIFIED_MEASUREMENT_RE = re.compile(
    r"(?<![A-Za-z0-9])\d+(?:\.\d+)?\s*(?:mm|cm|N\s*[·*]?\s*m|kPa|MPa|rpm|r/min|℃|°C|V|A|%)\b",
    flags=re.IGNORECASE,
)


_FALLBACK_UNVERIFIED_REFERENCE_RE = re.compile(
    r"(?:CCAR|FAR)\s*[- ]?\s*\d+(?:\.\d+)*|(?:FAA\s+AC|EASA\s+AMC)\b|手册第\s*\d+\s*页",
    flags=re.IGNORECASE,
)


def _sanitize_maintenance_ai_fallback(text: str) -> tuple[str, list[str]]:
    """Remove exact values and source-like citations from ungrounded AI guidance."""
    triggered: list[str] = []
    safe_lines: list[str] = []
    for line in str(text or "").splitlines():
        safe_segments: list[str] = []
        for segment in re.split(r"(?<=[。！？；;])", line):
            if _FALLBACK_UNVERIFIED_MEASUREMENT_RE.search(segment):
                triggered.append("unverified_measurement")
                continue
            if _FALLBACK_UNVERIFIED_REFERENCE_RE.search(segment):
                triggered.append("unverified_reference")
                continue
            safe_segments.append(segment)
        cleaned = "".join(safe_segments).strip()
        if cleaned:
            safe_lines.append(cleaned)
    message = "\n".join(safe_lines).strip()
    if not message:
        message = "可以先记录异响出现的工况、位置和伴随现象；如异响突然出现、持续加重或伴随其他异常，应停止运行并交由合格人员检查。"
    return message, list(dict.fromkeys(triggered))


def _ensure_maintenance_ai_disclaimer(message: str) -> str:
    missing: list[str] = []
    if "知识库" not in message:
        missing.append("知识库没有该设备对应文档")
    if "AI" not in message:
        missing.append("以下内容由 AI 基于通用知识生成")
    if "仅供参考" not in message:
        missing.append("内容仅供参考")
    if not missing:
        return message
    return "，".join(missing) + "。 " + message


async def _maintenance_fallback_answer(input_data: AgentInput, maint_ctx: dict):
    """检修场景兜底：抛开 ReAct/工具门槛，用「上下文+历史」做一次纯对话作答。"""
    decision = (input_data.context or {}).get("intent_decision") or {}
    policy = decision.get("policy") or {}
    knowledge_intents = {
        "knowledge_query",
        "parameter_query",
        "fault_diagnosis",
        "maintenance_guidance",
        "procedure_planning",
        "document_understanding",
    }
    if (
        decision.get("intent") in knowledge_intents
        or policy.get("evidence_level") == "required"
        or policy.get("requires_knowledge_retrieval")
        or decision.get("requires_knowledge_retrieval")
    ):
        from services.retrieval.evidence import EvidenceLedger
        from services.retrieval.response_plan import build_response_plan

        plan = build_response_plan(
            input_data.user_message,
            {
                "coverage_status": "unsupported",
                "coverage_reason": "maintenance_fallback_without_evidence",
                "aspect_support": [],
                "missing_aspect_ids": [],
                "conflict_eligible": [],
                "capabilities": {"may_offer_generic_guidance": False},
            },
            EvidenceLedger(),
        )
        return plan.deterministic_fallback()

    system = (
        "你是经验丰富的现场检修助手。请根据下面的【任务背景】和对话历史，"
        "用简明、安全第一、可操作的中文，直接给工人下一步可执行的建议。"
        "第一句话就给结论，即使知识库没有完全匹配的资料，也要基于通用检修经验务实作答。"
        "对常见故障原因、排查思路、原理性问题，要大胆运用专业常识给出有价值的判断；"
        "但涉及精确参数（扭矩、间隙、公差、具体型号规格、确切数值）时，只给方向、范围或排查方法，"
        "并提示『具体数值以该设备手册/铭牌为准』，绝不编造确切数字。"
        "严禁以「资料不足 / 无法回答 / 暂不能生成」搪塞；"
        "严禁输出任何 JSON、花括号 {} 或字段名，只用自然段中文回答。"
        + USER_VISIBLE_PLAIN_TEXT_RULES
        + "\n\n"
        + _render_maintenance_block(maint_ctx)
    )

    # 历史去 JSON 化：助手历史若是结构化输出，先抽成纯文本，仍是结构则丢弃，避免把模型带偏
    history_msgs = []
    for turn in (input_data.conversation_history or []):
        role = turn.get("role")
        content = turn.get("content")
        if role not in ("user", "assistant") or not content:
            continue
        if role == "assistant":
            content = _clean_fallback_text(content)
            if not content or content.startswith("{"):
                continue
        history_msgs.append({"role": role, "content": content})

    async def _ask(include_history: bool):
        msgs = [{"role": "system", "content": system}]
        if include_history:
            msgs.extend(history_msgs)
        msgs.append({"role": "user", "content": input_data.user_message})
        resp = await get_llm_service().chat(messages=msgs, temperature=0.5)
        raw = resp.get("content", "") if isinstance(resp, dict) else str(resp or "")
        return _clean_fallback_text(raw)

    def _bad(t: str) -> bool:
        if not t or t.startswith("{") or "needs_more_tools" in t:
            return True
        # 仅当「短且整体像拒答」才判坏；长答案里偶尔出现"无法给出精确值"等不算翻车
        return len(t) < 100 and any(h in t for h in _MAINT_REFUSAL_HINTS)

    try:
        text = await _ask(include_history=True)
        if _bad(text):
            # 历史可能带偏（结构化/拒答），去掉历史只凭背景再问一次
            text = await _ask(include_history=False)
        return None if _bad(text) else text
    except Exception:
        logger.exception("[maintenance_fallback] error session=%s", input_data.session_id)
        return None


@app.post("/ai/chat/stream")
async def chat_stream(request: ChatRequest):
    """
    SSE 流式对话接口（内联验证标记）

    采用「先缓冲再验证」策略：
    - ReAct 阶段实时推送 status / tool 事件（展示进度）
    - token 先缓冲不发送
    - ReAct 完成后运行 3 层验证（~300ms）
    - 逐字流式输出最终回答，在未验证内容前插入 marker 事件

    事件流：
    1. session_id 事件
    2. FixAgent ReAct 阶段：status / tool 事件（实时）
    3. 回答流式阶段：marker / token 事件（验证后输出）
    4. verification 事件（校验摘要）
    5. done 事件
    """
    async def event_generator():
        yield f"data: {json_dumps({'event': 'session_id', 'data': {'session_id': request.session_id}})}\n\n"

        input_data = await _prepare_chat_agent_input(request)

        try:
            follow_up_output = await _try_causal_follow_up_resolution(request, input_data)
            if follow_up_output is not None:
                async for event in _stream_causal_follow_up_output(follow_up_output):
                    yield event
                return

            route_output = await _try_route_plan_direct(request, input_data)
            if route_output is not None:
                async for event in _stream_policy_direct_output(route_output):
                    yield event
                return

            policy_output = await _try_response_policy_direct(request, input_data)
            if policy_output is not None:
                async for event in _stream_policy_direct_output(policy_output):
                    yield event
                return

            scope_output = _try_scope_guard(request, input_data)
            if scope_output is not None:
                async for event in _stream_scope_guard_output(scope_output):
                    yield event
                return

            direct_output = await _try_domain_rule_direct(request, input_data)
            if direct_output is not None:
                async for event in _stream_direct_agent_output(direct_output):
                    yield event
                return

            fix_agent = get_fix_agent()

            # 执行 FixAgent ReAct，转发进度事件（status/tool），缓冲 token
            # 等 ReAct 完成 + 验证管线跑完后再流式输出带内联标记的回答
            import asyncio as _asyncio
            token_buffer: list = []
            done_data: dict = {}
            tools_in_stream: list = []
            error_occurred = False

            async for event in fix_agent.run_with_react_stream(input_data):
                ev_type = event.get("event")
                if ev_type == "status":
                    yield f"data: {json_dumps(event)}\n\n"
                elif ev_type == "tool":
                    tools_in_stream.append(event.get("data", {}).get("tool", ""))
                    yield f"data: {json_dumps(event)}\n\n"
                elif ev_type == "tool_result":
                    yield f"data: {json_dumps(event)}\n\n"
                elif ev_type == "token":
                    token_buffer.append(event.get("data", {}).get("content", ""))
                elif ev_type == "done":
                    done_data = event.get("data", {})
                elif ev_type == "error":
                    error_occurred = True
                    yield f"data: {json_dumps(event)}\n\n"

            if error_occurred or not token_buffer:
                yield f"data: {json_dumps({'event': 'done', 'data': {}})}\n\n"
                return

            full_message = "".join(token_buffer)
            stream_react_trace = done_data.get("react_trace", [])
            stream_tools_used = done_data.get("tools_used", [])
            stream_metadata = done_data.get("metadata", {}) if isinstance(done_data.get("metadata"), dict) else {}
            fix_latency = done_data.get("latency_ms", 0)
            verified_tools = tools_in_stream if tools_in_stream else stream_tools_used
            verified_latency = fix_latency
            evidence_images = _extract_evidence_images({**stream_metadata, "react_trace": stream_react_trace})

            # —— 检修助手出口兜底（仅 maintenance 场景）——
            # 模型若吐出控制结构 JSON（needs_more_tools / 残缺 {"message"..}）或套话式软拒答，
            # 则抛开 ReAct 用「上下文+历史」重答一次，避免把内部结构/拒答暴露给工人。
            # **但**：如果是 low_confidence_retrieval（分数阈值拦截），那是有意拦截，不触发兜底。
            fallback_text = None
            maint_ctx = (input_data.context or {}).get("maintenance")
            insufficient_reason = stream_metadata.get("insufficient_evidence_reason", "")
            if (maint_ctx
                and _is_unhelpful_maintenance_reply(full_message)
                and insufficient_reason != "low_confidence_retrieval"):
                fallback_text = await _maintenance_fallback_answer(input_data, maint_ctx)
                if fallback_text:
                    logger.info("[chat_stream] 检修助手出口兜底已触发 session=%s", request.session_id)

            # —— A 硬兜底：evidence-required 意图却没检索 → 强制检索 + 据证据重答 ——
            if not fallback_text:
                used_tools = list(tools_in_stream or stream_tools_used or [])
                forced = await fix_agent.grounded_fallback_if_unretrieved(input_data, used_tools)
                if forced is not None:
                    full_message = forced.message
                    stream_react_trace = forced.metadata.get("react_trace", stream_react_trace)
                    stream_metadata = {**stream_metadata, **forced.metadata}
                    if "knowledge_retrieval" not in tools_in_stream:
                        tools_in_stream.append("knowledge_retrieval")
                    logger.info("[chat_stream] A 强制检索兜底已触发 session=%s", request.session_id)

            if fallback_text:
                # 兜底答案是基于上下文的务实建议，不走检索校验、不加内联标记
                final_message = fallback_text
                diagnosis_items = None
                verification = {}
                has_issues = False
                markers = []
                direct_images = await _collect_direct_section_images({
                    **stream_metadata,
                    "react_trace": stream_react_trace,
                    "user_message": input_data.user_message,
                    "original_user_message": request.message,
                })
                if direct_images:
                    evidence_images = _merge_evidence_images(evidence_images, direct_images)
            else:
                # 构建 AgentOutput 供验证管线校验
                fix_output = AgentOutput(
                    agent_name="fix_agent",
                    message=full_message,
                    intention=None,
                    tools_used=tools_in_stream if tools_in_stream else stream_tools_used,
                    metadata={
                        **stream_metadata,
                        "react_trace": stream_react_trace,
                        "user_message": input_data.user_message,
                        "original_user_message": request.message,
                        "intent_decision": (input_data.context or {}).get("intent_decision"),
                        "response_policy": (input_data.context or {}).get("response_policy"),
                        "route_plan": (input_data.context or {}).get("route_plan"),
                        "scope_decision": (input_data.context or {}).get("scope_decision"),
                    },
                    latency_ms=fix_latency
                )
                fix_output = _enforce_route_document_gate(fix_output, input_data)

                # 运行3层确定性校验（~300ms），获取内联标记位置
                if _is_deterministic_direct_output(fix_output):
                    verified_output = fix_output
                else:
                    verified_output = await get_review_agent().review(fix_output)
                if "react_trace" not in verified_output.metadata and fix_output.metadata.get("react_trace"):
                    verified_output.metadata["react_trace"] = fix_output.metadata["react_trace"]
                verified_output.metadata.setdefault("user_message", input_data.user_message)
                verified_output.metadata.setdefault("original_user_message", request.message)
                stream_metadata = {**stream_metadata, **verified_output.metadata}
                verification = verified_output.metadata.get("verification", {})
                has_issues = verified_output.metadata.get("verification_has_issues", False)
                evidence_images = _extract_evidence_images(verified_output.metadata)
                direct_images = await _collect_direct_section_images(verified_output.metadata)
                if direct_images:
                    evidence_images = _merge_evidence_images(evidence_images, direct_images)

                # 流式输出最终回答（逐字），在未验证语句前插入 marker 事件
                final_message, diagnosis_items = _extract_structured_chat_payload(verified_output.message)
                markers = get_review_agent().get_inline_markers(final_message, verification)
                verified_tools = verified_output.tools_used
                verified_latency = verified_output.latency_ms

                # 检修助手：review 因"证据不足"把回答压成软拒答（"知识库未检索到…请补型号"）时，
                # 不直接甩给工人——改用「上下文+常识」重答。通用原理/常见原因据此放开作答；
                # 精确参数由 _maintenance_fallback_answer 的 prompt 约束为"给方向、以手册为准、不编数值"。
                # **但**：如果是 low_confidence_retrieval（分数阈值拦截），说明召回结果不可信，这时应坚守拦截，不走常识兜底。
                insufficient_reason = verified_output.metadata.get("insufficient_evidence_reason", "")
                if (maint_ctx
                    and verified_output.metadata.get("blocked_for_insufficient_evidence")
                    and insufficient_reason != "low_confidence_retrieval"):
                    retry = await _maintenance_fallback_answer(input_data, maint_ctx)
                    if retry:
                        logger.info("[chat_stream] 检修助手证据不足→改用常识重答 session=%s", request.session_id)
                        final_message = retry
                        diagnosis_items = None
                        markers = []

            table_metadata = {
                **stream_metadata,
                "react_trace": stream_react_trace,
                "user_message": input_data.user_message,
                "original_user_message": request.message,
            }
            diagnostic_follow_up = None
            direct_table_items = await _collect_direct_section_table_items(request.message, table_metadata)
            table_answer = _format_inventory_table_answer_from_metadata(
                request.message,
                table_metadata,
                direct_table_items,
            )
            if table_answer:
                stream_metadata["deterministic_table_answer"] = True
                stream_metadata["deterministic_table_answer_source"] = "stream_response_override"
                for key in (
                    "_deterministic_answer_mode",
                    "_deterministic_answer_evidence_pages",
                    "_deterministic_answer_document_ids",
                    "_deterministic_answer_section_title",
                    "_deterministic_answer_section_ids",
                    "_deterministic_answer_table_complete",
                ):
                    if key in table_metadata:
                        stream_metadata[key] = table_metadata[key]
                final_message = table_answer
                diagnosis_items = None
                verification = {}
                has_issues = False
                markers = []

            if not table_answer:
                manual_metadata = {
                    **stream_metadata,
                    "react_trace": stream_react_trace,
                    "user_message": input_data.user_message,
                    "original_user_message": request.message,
                }
                manual_evidence_answer = _format_manual_evidence_answer_from_metadata(
                    request.message,
                    manual_metadata,
                )
                if manual_evidence_answer:
                    stream_metadata["deterministic_manual_evidence_answer"] = True
                    stream_metadata["deterministic_manual_evidence_answer_source"] = "stream_response_override"
                    for key in (
                        "_deterministic_answer_mode",
                        "_deterministic_answer_evidence_pages",
                        "_deterministic_answer_document_ids",
                        "_deterministic_answer_section_title",
                        "_deterministic_answer_section_ids",
                    ):
                        if key in manual_metadata:
                            stream_metadata[key] = manual_metadata[key]
                    final_message = manual_evidence_answer
                    diagnosis_items = None
                    verification = {}
                    has_issues = False
                    markers = []

            if not table_answer and not stream_metadata.get("deterministic_manual_evidence_answer"):
                diagnostic_follow_up = build_follow_up(
                    input_data.user_message,
                    diagnosis_items,
                    {**stream_metadata, "react_trace": stream_react_trace},
                )
                if diagnostic_follow_up:
                    stream_metadata["execution_mode"] = "causal_follow_up_question"
                    stream_metadata["confidence_source"] = "causal_follow_up"
                    stream_metadata["diagnostic_follow_up"] = diagnostic_follow_up
                    stream_metadata["pending_clarification"] = diagnostic_follow_up
                    verified_tools = list(verified_tools or [])
                    if FOLLOW_UP_TOOL_NAME not in verified_tools:
                        verified_tools.append(FOLLOW_UP_TOOL_NAME)
                    final_message = format_follow_up_message(diagnostic_follow_up)
                    diagnosis_items = None
                    verification = {}
                    has_issues = False
                    markers = []

            # —— 最终硬保险：检修场景下绝不让结构化 JSON / 冷拒答流给工人 ——
            if maint_ctx and _is_unhelpful_maintenance_reply(final_message):
                logger.info("[chat_stream] 检修助手最终保险触发，替换为安全话术 session=%s", request.session_id)
                final_message = _MAINT_SAFE_FALLBACK_LINE
                diagnosis_items = None
                markers = []

            final_output = AgentOutput(
                agent_name="fix_agent",
                message=final_message,
                tools_used=verified_tools,
                metadata={
                    **stream_metadata,
                    "react_trace": stream_react_trace,
                    "scope_decision": (input_data.context or {}).get("scope_decision")
                    or stream_metadata.get("scope_decision")
                    or {"status": "unknown"},
                },
                latency_ms=verified_latency,
            )
            pre_audit_message = final_message
            final_output = await _finalize_knowledge_output_with_fallback(
                request,
                input_data,
                final_output,
            )
            final_message = final_output.message
            stream_metadata = final_output.metadata
            if stream_metadata.get("execution_mode") == "maintenance_ai_fallback_after_retrieval":
                evidence_images = []
                diagnosis_items = None
                markers = []
                verification = {}
                has_issues = False
            if final_message != pre_audit_message:
                diagnosis_items = None
                markers = []
                verification = {}
                has_issues = False

            image_metadata = {
                **stream_metadata,
                "react_trace": stream_react_trace,
                "user_message": input_data.user_message,
                "original_user_message": request.message,
            }
            page_images = _collect_direct_evidence_page_images(image_metadata)
            if page_images:
                evidence_images = _merge_evidence_images(evidence_images, page_images)
            evidence_images = _select_evidence_images_for_response(evidence_images, image_metadata)
            final_message, evidence_images = _apply_final_image_contract(
                final_message,
                evidence_images,
                stream_metadata,
            )
            cleaned_final_message = strip_user_visible_emojis(final_message)
            if cleaned_final_message != final_message:
                final_message = cleaned_final_message
                markers = (
                    get_review_agent().get_inline_markers(final_message, verification)
                    if markers
                    else []
                )

            if diagnostic_follow_up:
                yield f"data: {json_dumps({'event': 'status', 'data': {'stage': '存在多个相近根因，正在生成区分性追问', 'mode': 'causal_follow_up'}})}\n\n"
                yield f"data: {json_dumps({'event': 'tool', 'data': {'tool': FOLLOW_UP_TOOL_NAME}})}\n\n"
                yield f"data: {json_dumps({'event': 'tool_result', 'data': {'tool': FOLLOW_UP_TOOL_NAME, 'text': final_message, 'items': _causal_follow_up_tool_items(diagnostic_follow_up)}})}\n\n"

            marker_idx = 0
            for i, char in enumerate(final_message):
                while marker_idx < len(markers) and markers[marker_idx]["char_pos"] <= i:
                    m = markers[marker_idx]
                    yield f"data: {json_dumps({'event': 'marker', 'data': {'text': strip_user_visible_emojis(m['text']), 'type': m['type']}})}\n\n"
                    marker_idx += 1

                yield f"data: {json_dumps({'event': 'token', 'data': {'content': char}})}\n\n"
                if i % 15 == 0:
                    await _asyncio.sleep(0)

            # 末尾剩余标记（安全追加文本中可能出现的新段落）
            while marker_idx < len(markers):
                m = markers[marker_idx]
                yield f"data: {json_dumps({'event': 'marker', 'data': {'text': strip_user_visible_emojis(m['text']), 'type': m['type']}})}\n\n"
                marker_idx += 1

            # 验证摘要事件
            verification_event = {
                "event": "verification",
                "data": {
                    "has_issues": has_issues,
                    "summary": {
                        "grounding_unverified": verification.get("grounding", {}).get("unverified_count", 0),
                        "graph_unverified": verification.get("graph", {}).get("unverified_count", 0),
                        "safety_missing": verification.get("safety", {}).get("missing_count", 0)
                    }
                }
            }
            yield f"data: {json_dumps(verification_event)}\n\n"

            # 完成事件
            final_done = {
                "event": "done",
                "data": {
                    "tools_used": verified_tools,
                    "latency_ms": verified_latency,
                }
            }
            if diagnostic_follow_up:
                final_done["data"]["diagnosticFollowUp"] = diagnostic_follow_up
            _sync_pending_clarification_state(request.session_id, stream_metadata)
            _attach_stream_done_metadata(final_done, stream_metadata)
            if diagnosis_items:
                final_done["data"]["diagnosisItems"] = _serialize_diagnosis_items(diagnosis_items)
            if evidence_images:
                final_done["data"]["evidenceImages"] = [
                    image.model_dump(by_alias=True)
                    for image in evidence_images
                ]
            yield f"data: {json_dumps(final_done)}\n\n"

        except Exception as e:
            logger.exception(f"[chat_stream] session={request.session_id} error")
            yield f"data: {json_dumps({'event': 'error', 'data': {'message': str(e)}})}\n\n"
            yield f"data: {json_dumps({'event': 'done', 'data': {}})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
    )


@app.post("/ai/knowledge/import", response_model=KnowledgeImportResponse)
async def knowledge_import(request: KnowledgeImportRequest) -> KnowledgeImportResponse:
    """
    文档导入并入库：解析 PDF → 向量化 → 存入 Redis 向量库
    """
    from services.knowledge.service import get_knowledge_service

    try:
        svc = get_knowledge_service()
        result = await svc.import_document(
            file_url=request.file_url,
            file_type=request.file_type,
            category=request.category,
            tags=request.tags,
            document_id=request.document_id,
            device_type=request.device_type,
            manual_type=request.manual_type,
            document_version=request.document_version,
            document_identity=request.document_identity,
            replace_existing=request.replace_existing
        )
        logger.info(f"[knowledge_import] 文件={result['file_name']} "
                    f"页数={result['total_pages']} "
                    f"文本={result['text_count']} 图片={result['image_count']} 表格={result['table_count']} "
                    f"耗时={result['process_time_ms']}ms")
        return KnowledgeImportResponse(
            success=True,
            message=f"导入完成：{result['file_name']}，共 {result['total_pages']} 页",
            code=200,
            file_name=result["file_name"],
            total_pages=result["total_pages"],
            text_count=result["text_count"],
            image_count=result["image_count"],
            image_summary_count=result.get("image_summary_count", 0),
            table_count=result["table_count"],
            sections=result["sections"],
            extraction_summary=result["extraction_summary"],
            process_time_ms=result["process_time_ms"],
            document_id=result.get("document_id"),
            document_version=result.get("document_version"),
            source_file_url=result.get("source_file_url")
        )
    except Exception as e:
        logger.exception(f"[knowledge_import] error")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/ai/knowledge/storage/stats", response_model=KnowledgeStorageStatsResponse)
async def knowledge_storage_stats() -> KnowledgeStorageStatsResponse:
    stats = get_vector_service().get_storage_stats()
    return KnowledgeStorageStatsResponse(
        success=True,
        message="knowledge storage statistics",
        code=200,
        **stats,
    )


@app.delete("/ai/knowledge/cache/embedding", response_model=KnowledgeCacheClearResponse)
async def knowledge_clear_embedding_cache() -> KnowledgeCacheClearResponse:
    deleted = get_vector_service().clear_embedding_cache()
    return KnowledgeCacheClearResponse(
        success=True,
        message="embedding cache cleared",
        code=200,
        **deleted,
    )


@app.post("/ai/knowledge/search", response_model=KnowledgeSearchResponse)
async def knowledge_search(request: KnowledgeSearchRequest) -> KnowledgeSearchResponse:
    """通过 KnowledgeRetrievalTool 进行向量检索，返回 TopK 相关片段。"""
    import time

    try:
        logger.info(f"[knowledge_search] 查询={request.query[:50]} 数量={request.top_k}")
        tool = get_knowledge_retrieval_tool()

        t0 = time.time()
        result = await tool.run(
            query=request.query,
            top_k=request.top_k,
            category=request.category,
            tags=request.tags,
            image_urls=request.images,
            document_id=request.document_id,
            chunk_type=request.chunk_type,
            device_type=request.device_type,
            document_version=request.document_version,
            manual_type=request.manual_type
        )
        query_time_ms = int((time.time() - t0) * 1000)

        if not result.success:
            raise HTTPException(
                status_code=500,
                detail=result.error.get("message", "检索失败") if result.error else "检索失败"
            )

        data = result.data
        if data:
            first_item = data[0]
            first_meta = first_item.metadata if hasattr(first_item, "metadata") else first_item.get("metadata", {})
        else:
            first_meta = {}

        logger.info(f"[knowledge_search] 找到={len(data)}条 耗时={query_time_ms}ms")
        return KnowledgeSearchResponse(
            success=True,
            message=f"检索完成，找到 {len(data)} 条结果",
            code=200,
            data=data,
            total=len(data),
            query_time_ms=query_time_ms,
            retrieval_confidence=first_meta.get("retrieval_confidence", "low"),
            matched_types=first_meta.get("matched_types", []),
            confidence_reason=first_meta.get("confidence_reason", {"candidate_count": 0})
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[knowledge_search] error")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ai/temporary-plan/generate", response_model=TemporaryPlanDraftResponse)
async def temporary_plan_generate(request: TemporaryPlanGenerateRequest) -> TemporaryPlanDraftResponse:
    """基于知识证据生成仅供审核的临时检修计划草稿。"""
    try:
        return await get_temporary_plan_service().generate(request)
    except Exception as e:
        logger.exception("[temporary_plan_generate] error")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ai/memory/consolidate", response_model=MemoryConsolidateResponse, response_model_by_alias=True)
async def memory_consolidate(request: MemoryConsolidateRequest) -> MemoryConsolidateResponse:
    """
    将多条原始对话压缩为结构化记忆摘要（滑动窗口 + 分类记忆）。
    """
    from datetime import datetime

    try:
        # 将消息列表转为带序号的字典格式，方便LLM阅读
        conv_dicts = [{"seq": i + 1, "role": m.role, "content": m.content} for i, m in enumerate(request.memoryMessages)]
        agent_input = AgentInput(
            user_message="请整理以下对话记录",
            session_id=request.session_id,
            context={
                "conversations": conv_dicts,
                "old_preferences": [p.model_dump() for p in request.memoryPreferenceVOList],
                # unresolved 现在带 id 字段，让LLM能通过ID精确标记已解决的事项
                "old_unresolved": [u.model_dump() for u in request.memoryUnresolvedVOList],
                # 上一轮摘要：让LLM生成渐进式摘要，避免信息丢失
                "previous_summary": request.previousSummary,
            }
        )

        logger.info(f"[memory_consolidate] 会话={request.session_id} 消息数={len(request.memoryMessages)}")
        result = await get_memory_agent().run(agent_input)
        logger.info(f"[memory_consolidate] 会话={request.session_id} 完成 耗时={result.latency_ms}ms")

        if result.metadata.get("status") == "error":
            error_type = result.metadata.get("error_type", "UnknownError")
            error_detail = result.metadata.get("error_detail", "记忆整理失败")
            logger.error(f"[memory_consolidate] 会话={request.session_id} 记忆Agent错误=[{error_type}] {error_detail}")
            # 返回200但带error状态，让Java端重试逻辑能解析
            return JSONResponse(content={
                "status": "error",
                "error_type": error_type,
                "error_detail": error_detail,
                "session_id": request.session_id
            })

        return MemoryConsolidateResponse(
            success=True,
            message="整理完成",
            code=200,
            session_id=request.session_id,
            summary=result.metadata.get("summary", {}),
            original_count=len(request.memoryMessages),
            consolidated_at=datetime.now().isoformat()
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class MemoryDedupRequest(BaseModel):
    user_id: str | None = None
    facts: list[dict] = []


@app.post("/ai/memory/dedup")
async def memory_dedup(request: MemoryDedupRequest):
    """语义去重（漏洞#2 离线 pass）：对某用户的活跃事实找出"真正重复"的分组，
    返回合并方案 {keep, drop[]}；Java 据此把非代表条 supersede（保守只并真重复，
    详见 services/memory_dedup_service）。"""
    from services.memory_dedup_service import dedup_facts
    try:
        groups = await dedup_facts(request.facts or [])
        logger.info(
            "[memory_dedup] user=%s facts=%d groups=%d",
            request.user_id, len(request.facts or []), len(groups),
        )
        return {"success": True, "groups": groups}
    except Exception as e:
        logger.exception("[memory_dedup] error")
        return {"success": False, "groups": [], "error": str(e)}


class DeleteFactsRequest(BaseModel):
    fact_ids: list[str]


@app.post("/ai/memory/delete_facts")
async def delete_facts(request: DeleteFactsRequest):
    """
    删除 Redis 向量库中的旧事实。
    Java 端整合产生 supersededIds 后调用此接口同步清理向量库。
    """
    if not request.fact_ids:
        return {"deleted": 0}

    svc = get_vector_service()
    deleted = svc.delete_batch(request.fact_ids)
    logger.info(f"[delete_facts] 删除旧事实向量 {deleted}/{len(request.fact_ids)} 条")
    return {"deleted": deleted}


# [已退役] /ai/memory/realtime_update 端点删除：实时记忆更新链路停用，
# 事实纠正改由对话内 save_memory/delete_memory 处理（旧链路去向量后只加不替、产生矛盾数据）。

# ==================== 检修案例沉淀 ====================

@app.post("/ai/case/draft", response_model=CaseDraftResponse)
async def ai_case_draft(req: CaseDraftRequest):
    """把原始材料整理成结构化检修案例草稿（含一轮 Basic Reflection 自检）。"""
    d = await draft_case(req)
    return CaseDraftResponse(**{k: d.get(k) for k in CaseDraftResponse.model_fields if k in d})


@app.post("/ai/case/compliance", response_model=CaseComplianceResponse)
async def ai_case_compliance(req: CaseComplianceRequest):
    """门控 LLM：判断内容是否可纳入设备检修知识库。"""
    return CaseComplianceResponse(**await check_compliance(req.text))


@app.post("/ai/case/extract", response_model=CaseExtractResponse)
async def ai_case_extract(req: CaseExtractRequest):
    """文件(pdf/txt/docx)抽文本 + 图片 VLM OCR → 汇总纯文本（供 /ai/case/draft 起草）。"""
    return CaseExtractResponse(**await extract_material(req))


@app.post("/ai/validate")
async def ai_validate(req: ValidateRequest):
    """通用入口校验守门：case=相关性+合规；task=宽松任务有效性；graph=待入图谱实体有效性。"""
    if req.purpose == "case":
        c = await check_compliance(req.text)
        return {"valid": c["compliant"], "reason": c["reason"]}
    if req.purpose == "graph":
        v = await validate_graph_entities(req.text)
        return {"valid": v["valid"], "reason": v["reason"]}
    v = await validate_task_text(req.text)
    return {"valid": v["valid"], "reason": v["reason"]}


# ==================== 多模态向量化（文本或图片，不融合）====================

class MultimodalEmbeddingRequest(BaseModel):
    """多模态向量化请求 — 传 text 或 image_base64s 之一，不做融合"""
    text: str = ""
    image_base64s: list = []   # Java 端下载图片后转的 base64 data URI

@app.post("/ai/embedding/multimodal")
async def multimodal_embedding(req: MultimodalEmbeddingRequest):
    """
    使用多模态模型（qwen2.5-vl-embedding，1024维）向量化。
    传 text 或 image_base64s 之一：
    - 仅 text：返回文本在多模态空间的向量
    - 仅 image_base64s：返回图片向量（多张取均值）
    - 不做融合，调用方应分别调用

    image_base64s 格式: ["data:image/jpeg;base64,/9j/4AAQ..."]
    """
    import numpy as np
    from embeddings.image_embedding import get_image_embedding

    has_text = bool(req.text and req.text.strip())
    has_images = bool(req.image_base64s)

    if not has_text and not has_images:
        raise HTTPException(status_code=400, detail="text 和 image_base64s 不能同时为空")

    try:
        img_emb = get_image_embedding()

        if has_images:
            # 图片向量（多张取均值后归一化）
            img_vecs = await img_emb.embed_batch(req.image_base64s)
            vec = np.mean(img_vecs, axis=0)
        else:
            # 纯文本 → 通过多模态模型映射到 1024 维空间
            vec = np.array(await img_emb.embed_text_as_multimodal(req.text.strip()))

        # 归一化
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm

        return {
            "vector": vec.tolist(),
            "dimension": len(vec),
            "has_text": has_text,
            "has_image": has_images
        }

    except Exception as e:
        logger.exception("[multimodal_embedding] error")
        raise HTTPException(status_code=500, detail=str(e))


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    return JSONResponse(
        status_code=500,
        content=BaseResponse(
            success=False,
            message=str(exc),
            code=500
        )
    )


# ==================== 知识过期判定 ====================

@app.post("/ai/expiration/check-task-promotion")
async def check_task_promotion_expiration(request: Request):
    """任务沉淀到图谱后触发过期判定（内部接口）。"""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")

    device_name = (body.get("device_name") or "").strip()
    new_fault_ids = body.get("new_fault_ids") or []
    new_sol_ids = body.get("new_sol_ids") or []

    if not device_name:
        raise HTTPException(status_code=400, detail="device_name required")

    logger.info("[过期判定API] 任务沉淀触发: device=%s, faults=%d, solutions=%d",
                device_name, len(new_fault_ids), len(new_sol_ids))

    from services.knowledge.expiration import get_expiration_service
    result = await get_expiration_service().check_new_knowledge(
        device_name, new_fault_ids, new_sol_ids
    )

    return {
        "success": True,
        "message": "操作成功",
        "code": 200,
        "data": result,
    }


@app.post("/ai/expiration/check-manual-upgrade")
async def check_manual_upgrade_expiration(request: Request):
    """手册更新后触发过期判定（内部接口）。"""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")

    manual_id = body.get("manual_id", 0)
    new_document_id = (body.get("new_document_id") or "").strip()
    manual_name = (body.get("manual_name") or "").strip()

    if not new_document_id:
        raise HTTPException(status_code=400, detail="new_document_id required")

    logger.info("[过期判定API] 手册更新触发: manualId=%s, documentId=%s, name=%s",
                manual_id, new_document_id, manual_name)

    from services.knowledge.expiration import get_expiration_service
    result = await get_expiration_service().check_manual_upgrade(
        manual_id, new_document_id, manual_name
    )

    return {
        "success": True,
        "message": "操作成功",
        "code": 200,
        "data": result,
    }


@app.post("/ai/manual-upgrade/sync")
async def manual_upgrade_sync(request: Request):
    """
    手册版本升级 → 知识图谱 chunk 级别同步（内部接口）。

    由 Java 端在新版手册向量导入完成后异步触发。
    对比 old_document_id / new_document_id 的 chunk diff，
    按 MODIFIED / ADDED / DELETED 三类处理图谱节点。
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")

    old_document_id = (body.get("old_document_id") or "").strip()
    new_document_id = (body.get("new_document_id") or "").strip()
    device_type = (body.get("device_type") or "").strip()
    manual_id = body.get("manual_id")

    if not new_document_id:
        raise HTTPException(status_code=400, detail="new_document_id required")

    logger.info(
        "[手册升级同步API] 触发: old=%s new=%s device=%s manualId=%s",
        old_document_id, new_document_id, device_type, manual_id,
    )

    from services.knowledge.manual_upgrade_sync import get_manual_upgrade_sync
    svc = get_manual_upgrade_sync()
    summary = await svc.sync(
        old_document_id=old_document_id,
        new_document_id=new_document_id,
        device_type=device_type,
        manual_id=manual_id,
    )

    return {
        "success": True,
        "message": "操作成功",
        "code": 200,
        "data": {
            "deleted_count": summary.deleted_count,
            "deprecated_count": summary.deprecated_count,
            "modified_count": summary.modified_count,
            "modified_replaced": summary.modified_replaced,
            "modified_supplemented": summary.modified_supplemented,
            "added_count": summary.added_count,
            "added_created": summary.added_created,
            "added_enriched": summary.added_enriched,
            "review_queue_size": len(summary.review_queue),
            "review_queue": summary.review_queue,
            "errors": summary.errors,
        },
    }


# ==================== 手册 KG 实体抽取 ====================

@app.post("/ai/manual-kg/extract")
async def manual_kg_extract(request: Request):
    """
    从一个已导入的手册文档中抽取实体并写入知识图谱（内部/管理接口）。

    由 Java 端在手册导入完成后异步触发，或管理员手动触发重抽。
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")

    document_id     = (body.get("document_id") or "").strip()
    device_type_hint = (body.get("device_type") or "").strip()
    manual_name     = (body.get("manual_name") or "").strip()
    manual_id_raw   = body.get("manual_id")
    # manual_id 作为图谱节点归属标识（供删手册时精确清理）；0/缺省视为无
    try:
        manual_id = int(manual_id_raw) if manual_id_raw not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        manual_id = None

    if not document_id:
        raise HTTPException(status_code=400, detail="document_id required")

    logger.info("[KG抽取API] 触发: document_id=%s manual_id=%s device_hint=%s manual_name=%s", document_id, manual_id, device_type_hint, manual_name)

    from services.knowledge.manual_kg_extractor import get_manual_kg_extractor
    result = await get_manual_kg_extractor().extract_document(document_id, device_type_hint, manual_id=manual_id, manual_name=manual_name)

    return {
        "success": True,
        "message": "操作成功",
        "code": 200,
        "data": {
            "document_id":       result.document_id,
            "device_name":       result.device_name,
            "device_id":         result.device_id,
            "components_created": result.components_created,
            "faults_created":    result.faults_created,
            "solutions_created": result.solutions_created,
            "procedures_created": result.procedures_created,
            "review_count":      len(result.review_items),
            "error_count":       len(result.errors),
            "errors":            result.errors,
            "skipped":           result.skipped,
            "skip_reason":       result.skip_reason,
        },
    }


@app.post("/ai/manual-kg/reextract-all")
async def manual_kg_reextract_all(request: Request):
    """
    全量重抽：遍历所有已导入手册，重新抽取并 MERGE 图谱实体。

    用于周期性修正或首次批量建图。耗时较长（每文档约数十秒），请异步调用。
    """
    logger.info("[KG全量重抽API] 触发")

    from services.knowledge.manual_kg_extractor import get_manual_kg_extractor
    result = await get_manual_kg_extractor().reextract_all()

    return {
        "success": True,
        "message": "操作成功",
        "code": 200,
        "data": result,
    }


@app.post("/ai/manual-kg/clear-all")
async def manual_kg_clear_all(request: Request):
    """清空 Neo4j 所有节点（测试用，内部接口）。"""
    from config.settings import get_settings
    import httpx as _httpx

    settings = get_settings()
    async with _httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{settings.java_service_url}/weixiu/kg/internal/clear-all",
            json={},
            headers={"X-Internal-Token": settings.internal_token},
        )
    return resp.json()
