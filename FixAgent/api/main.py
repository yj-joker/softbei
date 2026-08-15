import json
import hashlib
import logging
import os
import re
import subprocess
import tempfile
import time
from dataclasses import replace
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
    ImageEvidenceBinding,
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
from agents.fix_agent import FixAgent, get_fix_agent
from agents.voice_task_agent import get_voice_task_agent
from guardrails import get_review_agent
from agents.memory_agent import get_memory_agent
from agents.base_agent import AgentInput, AgentOutput, make_experiment_tool_profile
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
    build_follow_up,  # 兼容旧扩展点；运行时仅调用 build_evidence_follow_up。
    format_follow_up_message,
    format_resolution_message,
    resolve_follow_up,
    build_evidence_follow_up,
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
    IN_SCOPE,
    OUT_OF_SCOPE,
    UNKNOWN_SCOPE,
    ScopeDecision,
    decide_scope,
    format_scope_guard_message,
)
from services.retrieval.device_identity import (
    DeviceCatalog,
    QueryContract,
    compare_query_to_document,
    document_identity_heads,
    load_dynamic_device_catalog,
    normalize_query_identity,
    query_has_grounded_operation_target,
    query_mentions_unresolved_identity,
)
from services.retrieval.evidence import EvidenceLedger
from services.retrieval.graph_pre_retrieval import GraphPreRetrievalService
from services.retrieval.manual_scope import (
    AdditiveManualScopes,
    build_additive_manual_scopes,
    build_manual_retrieval_kwargs,
)
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
from services.routing.graph_candidate_provider import (
    filter_candidates_by_resolved_scope,
    get_graph_candidate_provider,
)
from services.routing.graph_policy import decide_graph_use
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
from services.grounded_turn_context import (
    GroundedTurnContextStore,
    context_from_successful_answer,
)
from services.clarification.state import (
    ClarificationStateStore,
    ClarificationStatus,
    ResolvedScope,
    topic_signature_for_contract,
)
from services.clarification.llm_fallback import (
    LLMClarificationService,
    build_safe_observation_fallback,
)
from tools.knowledge_retrieval_tool import get_knowledge_retrieval_tool
from tools.knowledge_inventory_tool import get_knowledge_inventory_tool
from services.temporary_plan_service import get_temporary_plan_service
from config.settings import get_settings, validate_auth_tokens
from schemas.models import AgentMode

logger = logging.getLogger(__name__)

_CLARIFICATION_STATE_STORE: ClarificationStateStore | None = None
_GROUNDED_TURN_CONTEXT_STORE: GroundedTurnContextStore | None = None


def _clarification_state_store() -> ClarificationStateStore:
    global _CLARIFICATION_STATE_STORE
    if _CLARIFICATION_STATE_STORE is None:
        _CLARIFICATION_STATE_STORE = ClarificationStateStore(
            redis_client=_pending_clarification_redis_client()
            if "_pending_clarification_redis_client" in globals()
            else None,
        )
    return _CLARIFICATION_STATE_STORE


def _clarification_mode() -> str:
    return str(getattr(get_settings(), "clarification_mode", "enforce") or "enforce").strip().lower()

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
    settings = get_settings()
    validate_auth_tokens(settings.internal_token, settings.api_token)

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
        "rag_variant": _current_rag_variant(),
    }


@app.get("/ai/runtime")
async def runtime_info() -> dict[str, Any]:
    runtime = _runtime_snapshot()
    return {
        "status": "ok" if runtime["catalog_available"] else "degraded",
        "rag_variant": _current_rag_variant(),
        "runtime": runtime,
    }

_settings = get_settings()
os.makedirs(_settings.local_file_storage_dir, exist_ok=True)
app.mount(_settings.file_public_base_url, StaticFiles(directory=_settings.local_file_storage_dir), name="rag_files")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Api-Token", "X-Internal-Token"],
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
        or (
            path.startswith(_settings.file_public_base_url + "/")
            and path[len(_settings.file_public_base_url) + 1:].startswith(("rendered_pages/", "public/", "images/"))
        )
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

_GRAPH_RAG_TOOL_NAMES = frozenset({
    "java_graph_diagnosis_path",
    "java_graph_device_search",
    "component_reverse_device",
})


def _current_rag_variant() -> str:
    return str(getattr(get_settings(), "rag_variant", "production") or "production")


def _graph_candidates_enabled(rag_variant: str) -> bool:
    return str(rag_variant or "production") != "no_graph"


def _graph_candidate_query_count_for_status(status: Any) -> int:
    normalized = str(status or "").strip().lower()
    return 0 if normalized in {"", "not_applicable"} else 1


def _server_graph_scope_from_candidate(candidate: Any) -> dict[str, Any]:
    dimensions = getattr(candidate, "dimensions", {}) or {}
    scope: dict[str, Any] = {}
    for source, target in (
        ("path_id", "allowed_path_ids"),
        ("device_id", "allowed_device_ids"),
        ("component_id", "allowed_component_ids"),
        ("fault_id", "allowed_fault_ids"),
    ):
        value = str(dimensions.get(source) or "").strip()
        if value:
            scope[target] = [value]
    for source, target in (
        ("document_id", "document_id"),
        ("document_version", "document_version"),
    ):
        value = str(getattr(candidate, source, "") or "").strip()
        if value:
            scope[target] = value
    for source, target in (
        ("section_id", "allowed_section_ids"),
        ("source_chunk_uids", "allowed_source_chunk_uids"),
        ("evidence_refs", "allowed_evidence_refs"),
        ("pages", "pages"),
        ("node_ids", "allowed_graph_node_ids"),
    ):
        raw_values = getattr(candidate, source, ()) or ()
        if not isinstance(raw_values, (list, tuple, set, frozenset)):
            raw_values = (raw_values,)
        values = list(dict.fromkeys(
            value for value in raw_values if str(value or "").strip()
        ))
        if values:
            scope[target] = values
    scope["graph_quality_tier"] = str(getattr(candidate, "quality_tier", "medium"))
    return scope


def _effective_server_graph_scope(
    route_scope: Mapping[str, Any] | None,
    graph_candidates: Any,
) -> dict[str, Any]:
    """Use only a route-selected graph scope; candidates never become a hard scope."""
    del graph_candidates
    return dict(route_scope or {})


def _build_effective_manual_scopes(
    *,
    selected_document_id: str,
    selected_section_id: str,
    resolved_scope: Mapping[str, Any] | None,
    effective_graph_scope: Mapping[str, Any] | None,
) -> AdditiveManualScopes:
    """Keep ordinary RAG scope independent from optional graph seed locators."""
    return build_additive_manual_scopes(
        selected_document_id=selected_document_id,
        selected_section_id=selected_section_id,
        resolved_scope=resolved_scope,
        graph_scope=effective_graph_scope,
    )


def _review_level_for_rag_variant(
    rag_variant: str,
    requested_level: str,
    context: Mapping[str, Any] | None = None,
) -> str:
    requested = str(requested_level or "full").lower()
    if requested != "full":
        return requested
    context = context or {}
    policy = context.get("graph_policy")
    policy = policy if isinstance(policy, Mapping) else {}
    graph_batch = context.get("graph_pre_retrieval")
    graph_batch = graph_batch if isinstance(graph_batch, Mapping) else {}
    diagnostics = graph_batch.get("diagnostics")
    diagnostics = diagnostics if isinstance(diagnostics, Mapping) else {}
    graph_review_enabled = bool(
        str(rag_variant or "") in {"graph", "graph_full", "production"}
        and policy.get("graph_review_enabled") is True
        and int(diagnostics.get("qualified_count") or 0) > 0
    )
    return "full" if graph_review_enabled else "standard"


def _initialize_rag_variant_context(context: dict[str, Any]) -> str:
    rag_variant = _current_rag_variant()
    context["rag_variant"] = rag_variant
    context["graph_candidate_query_count"] = 0
    context["graph_candidate_count"] = 0
    context["graph_candidate_retrieval"] = {
        "status": "not_applicable",
        "reason": "not_queried",
        "diagnostics": {},
    }
    context["graph_pre_retrieval"] = {
        "status": "not_applicable",
        "reason": "not_queried",
        "evidence": [],
        "diagnostics": {},
    }
    context.pop("_experiment_tool_profile", None)
    if rag_variant == "no_graph":
        context["_experiment_tool_profile"] = make_experiment_tool_profile("rag_only")
    elif rag_variant == "graph_full":
        context["_experiment_tool_profile"] = make_experiment_tool_profile("rag_kg")
    elif rag_variant == "graph_shadow":
        context["_experiment_tool_profile"] = make_experiment_tool_profile("rag_only")
    return rag_variant


def _rag_variant_audit_metadata(
    *,
    context: Mapping[str, Any] | None,
    metadata: Mapping[str, Any] | None,
    review_level: str,
) -> dict[str, Any]:
    context = context or {}
    metadata = metadata or {}
    graph_tool_calls: list[str] = []
    for step in metadata.get("react_trace") or []:
        if not isinstance(step, Mapping):
            continue
        for call in step.get("tool_calls") or []:
            if not isinstance(call, Mapping):
                continue
            name = str(call.get("name") or "")
            executed = call.get("executed")
            legacy_not_found = str(call.get("result_summary") or "").startswith("tool not found:")
            if name in _GRAPH_RAG_TOOL_NAMES and executed is not False and not legacy_not_found:
                graph_tool_calls.append(name)
    rag_variant = str(context.get("rag_variant") or "production")
    candidate_retrieval = context.get("graph_candidate_retrieval")
    candidate_retrieval = candidate_retrieval if isinstance(candidate_retrieval, Mapping) else {}
    graph_retrieval = context.get("graph_pre_retrieval")
    graph_retrieval = graph_retrieval if isinstance(graph_retrieval, Mapping) else {}
    graph_diagnostics = graph_retrieval.get("diagnostics")
    graph_diagnostics = graph_diagnostics if isinstance(graph_diagnostics, Mapping) else {}
    graph_evidence = [
        item for item in graph_retrieval.get("evidence") or []
        if isinstance(item, Mapping)
    ]
    graph_evidence_ids = list(dict.fromkeys(
        str(item.get("evidence_id") or "")
        for item in graph_evidence
        if str(item.get("evidence_id") or "").strip()
    ))
    claim_evidence_bindings = [
        {
            **dict(binding),
            "evidence_ids": list(binding.get("evidence_ids") or []),
        }
        for binding in metadata.get("claim_evidence_bindings") or []
        if isinstance(binding, Mapping)
    ]
    bound_evidence_ids = {
        str(evidence_id)
        for binding in claim_evidence_bindings
        for evidence_id in binding.get("evidence_ids") or []
        if str(evidence_id).strip()
    }
    declared_graph_used = {
        str(evidence_id)
        for evidence_id in metadata.get("graph_evidence_used_ids") or []
        if str(evidence_id).strip()
    }
    graph_evidence_used_ids = [
        evidence_id
        for evidence_id in graph_evidence_ids
        if evidence_id in bound_evidence_ids and evidence_id in declared_graph_used
    ]
    intent_decision = context.get("intent_decision")
    intent_decision = dict(intent_decision) if isinstance(intent_decision, Mapping) else {}
    query_contract = context.get("query_contract")
    query_contract = dict(query_contract) if isinstance(query_contract, Mapping) else {}
    frozen_route_contract = context.get("_evaluation_route_contract")
    if (
        context.get("evaluation_route_contract_applied") is True
        and isinstance(frozen_route_contract, Mapping)
        and isinstance(frozen_route_contract.get("intent_decision"), Mapping)
        and isinstance(frozen_route_contract.get("query_contract"), Mapping)
    ):
        route_contract = {
            "intent_decision": dict(frozen_route_contract["intent_decision"]),
            "query_contract": dict(frozen_route_contract["query_contract"]),
        }
    else:
        route_contract = {
            "intent_decision": intent_decision,
            "query_contract": query_contract,
        }
    route_contract_signature = hashlib.sha256(
        json.dumps(
            route_contract,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "rag_variant": rag_variant,
        "graph_candidate_query_count": int(context.get("graph_candidate_query_count") or 0),
        "graph_candidate_count": int(context.get("graph_candidate_count") or 0),
        "graph_candidate_status": str(candidate_retrieval.get("status") or "not_applicable"),
        "graph_candidate_reason": str(candidate_retrieval.get("reason") or ""),
        "graph_retrieval_status": str(graph_retrieval.get("status") or "not_applicable"),
        "graph_retrieval_reason": str(graph_retrieval.get("reason") or ""),
        "graph_scope": dict(context.get("graph_scope") or {}),
        "graph_qualified_count": int(graph_diagnostics.get("qualified_count") or 0),
        "graph_routing_only_count": int(graph_diagnostics.get("routing_only_count") or 0),
        "graph_rejected_count": int(graph_diagnostics.get("rejected_count") or 0),
        "graph_evidence_ids": graph_evidence_ids,
        "claim_evidence_bindings": claim_evidence_bindings,
        "graph_evidence_used_ids": graph_evidence_used_ids,
        "graph_relationship_types": sorted({
            str(relation)
            for item in graph_evidence
            for relation in item.get("relationship_types") or []
            if str(relation).strip()
        }),
        "graph_provenance_statuses": sorted({
            str(item.get("provenance_status"))
            for item in graph_evidence
            if str(item.get("provenance_status") or "").strip()
        }),
        "graph_retrieval_latency_ms": int(graph_diagnostics.get("latency_ms") or 0),
        "graph_tool_call_count": len(graph_tool_calls),
        "graph_tools_used": sorted(set(graph_tool_calls)),
        "graph_review_enabled": bool(
            isinstance(context.get("graph_policy"), Mapping)
            and context["graph_policy"].get("graph_review_enabled") is True
            and int(graph_diagnostics.get("qualified_count") or 0) > 0
            and str(review_level or "").lower() == "full"
        ),
        "intent_decision": intent_decision,
        "query_contract": query_contract,
        "evaluation_route_contract_applied": bool(
            context.get("evaluation_route_contract_applied")
        ),
        "route_contract_signature": route_contract_signature,
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
    normalized_trace: list[dict[str, Any]] = []
    for step in (metadata or {}).get("react_trace") or []:
        if not isinstance(step, dict):
            continue
        normalized_calls: list[dict[str, Any]] = []
        for call in step.get("tool_calls") or []:
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
            if isinstance(payload, list):
                nested_bundle = next((
                    (item.get("metadata") or {}).get("evidence_bundle")
                    for item in payload
                    if isinstance(item, dict)
                    and isinstance(item.get("metadata"), dict)
                    and isinstance((item.get("metadata") or {}).get("evidence_bundle"), dict)
                ), None)
                if isinstance(nested_bundle, dict):
                    bundle = dict(nested_bundle)
                    bundle.setdefault("qualified_evidence", [
                        item
                        for item in payload
                        if isinstance(item, dict)
                        and str(
                            item.get("qualification")
                            or (item.get("metadata") or {}).get("qualification")
                            or ""
                        ).strip() == "qualified"
                    ])
                    bundle.setdefault("reference_evidence", [
                        item
                        for item in payload
                        if isinstance(item, dict)
                        and str(
                            item.get("qualification")
                            or (item.get("metadata") or {}).get("qualification")
                            or ""
                        ).strip() == "reference_only"
                    ])
                    payload = bundle
            if isinstance(payload, dict) and not any(
                key in payload
                for key in ("aspect_support", "coverage_status", "conflict_eligible")
            ):
                continue
            if not isinstance(payload, (dict, list)):
                continue
            normalized_calls.append({**call, "result_data": payload})
        if normalized_calls:
            normalized_trace.append({**step, "tool_calls": normalized_calls})
    return FixAgent._merged_knowledge_bundle(normalized_trace)


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

    # Direction questions ask for the relation, not a literal copy of the
    # requested orientation. For example, "哪一端朝上" is answered by a
    # manual statement that the dense end must face down. Strip only the
    # interrogative suffix and require an explicit directional assertion in
    # the evidence; this is structural language handling, not a domain term
    # catalogue.
    anchor_query = query
    direction_question = re.search(r"哪(?:一)?(?:端|边|侧|面)", query or "")
    if direction_question:
        anchor_query = query[:direction_question.start()]
    anchors = _manual_query_anchor_terms(anchor_query)
    if direction_question and not anchors:
        target = _manual_action_target(query, _manual_query_action(query))
        target = re.split(r"哪(?:一)?(?:端|边|侧|面)", target or "", maxsplit=1)[0]
        target = _compact_inventory_text(target)
        if len(target) >= 2:
            anchors = [target]
    minimal_anchors = [
        anchor
        for anchor in anchors
        if not any(
            other != anchor and len(other) < len(anchor) and other in anchor
            for other in anchors
        )
    ]
    if minimal_anchors and not all(
        _manual_anchor_supported_by_text(anchor, evidence)
        for anchor in minimal_anchors
    ):
        return False

    if direction_question and not re.search(r"(?:朝|向)[\u4e00-\u9fff]", evidence):
        return False

    if not minimal_anchors:
        # A route-authorized exact section title is sufficient for a broad
        # procedure query such as "水泵应该怎样拆装". The title overlap is
        # derived from the document text itself, so no component catalogue is
        # needed here.
        query_text = _compact_inventory_text(query)
        section_titles = [
            _compact_inventory_text(match.group(1))
            for match in re.finditer(
                r"(?m)^\s*\d+(?:\.\d+)*\s+([^\n]+)",
                direct_text or "",
            )
        ]
        allow_action_prefixed_title = _manual_query_kind(query) == "procedure"
        if allow_action_prefixed_title:
            for title in section_titles:
                if not title:
                    continue
                if title in query_text:
                    return True
                # Section headings often lead with the documented operation.
                # For a fault-treatment question, the component title is still
                # a deterministic anchor even when the query does not repeat
                # that heading verbatim.
                core_title = re.sub(
                    r"^(?:检查|安装|拆卸|调整|测量|更换|维修|诊断)",
                    "",
                    title,
                )
                if len(core_title) >= 2 and core_title in query_text:
                    return True
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
    output.metadata.update(audited.to_metadata())
    if "graph_evidence_ids" in output.metadata:
        retrieved_graph_ids = {
            str(evidence_id)
            for evidence_id in output.metadata.get("graph_evidence_ids") or []
            if str(evidence_id).strip()
        }
        used_graph_ids = [
            evidence_id
            for evidence_id in output.metadata.get("graph_evidence_used_ids") or []
            if evidence_id in retrieved_graph_ids
        ]
        output.metadata["graph_evidence_used_ids"] = used_graph_ids
        output.metadata["claim_evidence_bindings"] = [
            {
                **dict(binding),
                "evidence_ids": [
                    evidence_id
                    for evidence_id in binding.get("evidence_ids") or []
                    if evidence_id in used_graph_ids
                ],
            }
            for binding in output.metadata.get("claim_evidence_bindings") or []
            if any(
                evidence_id in used_graph_ids
                for evidence_id in binding.get("evidence_ids") or []
            )
        ]
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


def _ensure_stream_done_image_field(event: dict[str, Any]) -> dict[str, Any]:
    if event.get("event") == "done":
        event.setdefault("data", {}).setdefault("evidenceImages", [])
    return event


def _attach_stream_done_metadata(event: dict[str, Any], metadata: dict | None) -> None:
    diagnostics = {
        key: (metadata or {}).get(key)
        for key in (
            "scope_decision",
            "coverage_status",
            "response_plan_id",
            "evidence_ledger_digest",
            "authorized_claim_evidence_bindings",
            "claim_evidence_bindings",
            "graph_evidence_bound_ids",
            "graph_evidence_used_ids",
            "pending_clarification",
            "_deterministic_answer_evidence_pages",
            "_deterministic_answer_document_ids",
            "_deterministic_answer_section_title",
            "_deterministic_answer_section_ids",
            "_deterministic_answer_table_complete",
            "rag_variant",
            "graph_candidate_query_count",
            "graph_candidate_count",
            "graph_candidate_status",
            "graph_candidate_reason",
            "graph_retrieval_status",
            "graph_retrieval_reason",
            "graph_scope",
            "graph_qualified_count",
            "graph_routing_only_count",
            "graph_rejected_count",
            "graph_evidence_ids",
            "graph_relationship_types",
            "graph_provenance_statuses",
            "graph_retrieval_latency_ms",
            "graph_tool_call_count",
            "graph_tools_used",
            "graph_review_enabled",
            "image_selection_status",
            "image_selection_failed_stage",
            "image_selection_error_type",
            "image_followup_inherited",
            "image_followup_context_conflict",
            "image_followup_context_conflict_fields",
            "resolved_image_query",
            "image_followup_base_query",
            "allowed_document_ids",
            "inherited_document_ids",
            "allowed_source_chunk_ids",
            "inherited_non_image_source_ids",
            "procedure_scope_ids",
            "_deterministic_answer_procedure_scope_id",
            "inherited_procedure_scope_ids",
            "allowed_evidence_pages",
            "inherited_evidence_pages",
        )
        if key in (metadata or {})
    }
    contract = (metadata or {}).get("image_selection_contract")
    contract = contract if isinstance(contract, Mapping) else {}
    if contract:
        diagnostics["image_selection_summary"] = {
            "mode": contract.get("mode"),
            "decision_reason": contract.get("decision_reason"),
            "candidate_count": contract.get("candidate_count", 0),
            "authorized_count": contract.get("authorized_count", 0),
            "selected_count": contract.get("selected_count", 0),
            "reject_reason_counts": contract.get("reject_reason_counts", {}),
            "selected_source_chunk_ids": [
                item.get("source_chunk_id")
                for item in contract.get("selected_image_bindings", [])
                if isinstance(item, Mapping)
            ],
        }
    if diagnostics:
        event.setdefault("data", {}).setdefault("metadata", {}).update(diagnostics)


def _initialized_or_injected_vector_service(*, initialize: bool = False):
    try:
        from services.knowledge import vector_service as vector_service_module

        getter = getattr(vector_service_module, "get_vector_service", None)
        if callable(getter) and getattr(getter, "__name__", "") != "get_vector_service":
            return getter()
        service = getattr(vector_service_module, "_vector_service", None)
        if service is not None:
            return service
        if callable(getter) and initialize:
            return getter()
    except Exception:
        pass
    return None


def _pending_clarification_redis_client():
    service = _initialized_or_injected_vector_service()
    return getattr(service, "redis", None) if service is not None else None


def _grounded_turn_context_store() -> GroundedTurnContextStore:
    global _GROUNDED_TURN_CONTEXT_STORE
    if _GROUNDED_TURN_CONTEXT_STORE is None:
        _GROUNDED_TURN_CONTEXT_STORE = GroundedTurnContextStore(
            redis_client=_pending_clarification_redis_client(),
            ttl_seconds=900,
        )
    return _GROUNDED_TURN_CONTEXT_STORE


def _should_preserve_grounded_context_for_image_followup(
    session_id: str,
    query: str,
    metadata: Mapping[str, object],
    store: GroundedTurnContextStore,
) -> bool:
    from services.retrieval.query_understanding import is_deictic_image_followup

    if (
        metadata.get("image_followup_inherited") is not True
        or metadata.get("image_selection_status") != "ok"
        or not is_deictic_image_followup(query)
    ):
        return False
    prior = store.load(session_id)
    if prior is None:
        return False
    contract = metadata.get("image_selection_contract")
    if not isinstance(contract, Mapping):
        return False
    try:
        selected_count = int(contract.get("selected_count") or 0)
    except (TypeError, ValueError):
        return False
    if selected_count < 1:
        return False
    strong_bindings = [
        item
        for item in contract.get("selected_image_bindings") or ()
        if isinstance(item, Mapping)
        and item.get("reason") in {"answer_evidence_binding", "procedure_scope_binding"}
        and str(item.get("source_chunk_id") or "").strip()
    ]
    if not strong_bindings:
        return False
    target_document_ids = {
        str(value).strip()
        for value in contract.get("target_document_ids") or ()
        if str(value).strip()
    }
    if target_document_ids != {prior.document_id}:
        return False
    target_source_ids = {
        str(value).strip()
        for value in contract.get("target_non_image_source_ids") or ()
        if str(value).strip()
    }
    target_scope_ids = {
        str(value).strip()
        for value in contract.get("target_procedure_scope_ids") or ()
        if str(value).strip()
    }
    return bool(
        target_source_ids.intersection(prior.source_chunk_ids)
        or target_scope_ids.intersection(prior.procedure_scope_ids)
    )


def _sync_grounded_turn_context(
    request: ChatRequest,
    query: str,
    metadata: Mapping[str, object],
) -> None:
    context = context_from_successful_answer(
        query,
        metadata,
        device_type=request.device_type or "",
    )
    store = _grounded_turn_context_store()
    if context is None:
        if _should_preserve_grounded_context_for_image_followup(
            request.session_id,
            query,
            metadata,
            store,
        ):
            if isinstance(metadata, dict):
                metadata["grounded_turn_context_preserved"] = True
            return
        store.clear(request.session_id)
        return
    store.remember(request.session_id, context)


def _restore_grounded_image_followup(
    request: ChatRequest,
    raw_message: str,
    context: dict,
) -> str:
    from services.retrieval.query_understanding import is_deictic_image_followup

    if request.images or not is_deictic_image_followup(raw_message):
        return ""
    prior = _grounded_turn_context_store().load(request.session_id)
    if prior is None:
        return ""

    requested_document_ids = {
        str(value).strip()
        for value in (request.document_id, context.get("confirmed_document_id"))
        if str(value or "").strip()
    }
    requested_section_id = str(context.get("confirmed_section_id") or "").strip()
    requested_device_type = str(
        request.device_type or context.get("device_type") or ""
    ).strip()
    resolved_scope = context.get("resolved_scope")
    resolved_scope = resolved_scope if isinstance(resolved_scope, Mapping) else {}
    resolved_document_id = str(resolved_scope.get("document_id") or "").strip()
    resolved_section_ids = {
        str(value).strip()
        for value in resolved_scope.get("allowed_section_ids") or ()
        if str(value).strip()
    }
    resolved_pages = {
        int(value)
        for value in (
            resolved_scope.get("pages")
            or resolved_scope.get("allowed_evidence_pages")
            or ()
        )
        if str(value).isdigit()
    }

    conflicts: list[str] = []
    if requested_document_ids and requested_document_ids != {prior.document_id}:
        conflicts.append("document")
    if requested_section_id and requested_section_id != prior.section_id:
        conflicts.append("section")
    if requested_device_type and requested_device_type != prior.device_type:
        conflicts.append("device_type")
    if resolved_document_id and resolved_document_id != prior.document_id:
        conflicts.append("resolved_scope.document")
    if (
        prior.section_id
        and resolved_section_ids
        and prior.section_id not in resolved_section_ids
    ):
        conflicts.append("resolved_scope.section")
    if (
        prior.evidence_pages
        and resolved_pages
        and set(prior.evidence_pages).isdisjoint(resolved_pages)
    ):
        conflicts.append("resolved_scope.page")
    if conflicts:
        context["image_followup_context_conflict"] = True
        context["image_followup_context_conflict_fields"] = conflicts
        return ""

    context["confirmed_document_id"] = prior.document_id
    if prior.section_id:
        context["confirmed_section_id"] = prior.section_id
    resolved_query = f"{prior.base_query}；用户追问：{raw_message}"
    context["image_followup_base_query"] = prior.base_query
    context["resolved_image_query"] = resolved_query
    context["image_followup_original_message"] = raw_message
    context["inherited_image_evidence"] = {
        "document_id": prior.document_id,
        "device_type": prior.device_type,
        "section_id": prior.section_id,
        "section_title": prior.section_title,
        "evidence_pages": list(prior.evidence_pages),
        "source_chunk_ids": list(prior.source_chunk_ids),
        "procedure_scope_ids": list(prior.procedure_scope_ids),
    }
    context["image_followup_inherited"] = True
    return resolved_query


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
            candidates = []
            for item in pending.get("alternatives") or ():
                if not isinstance(item, Mapping):
                    continue
                candidates.append({
                    **dict(item),
                    "constraints": {
                        "selected_value": str(item.get("value") or ""),
                        "selected_unit": str(item.get("unit") or ""),
                        "selected_evidence_refs": list(item.get("evidence_refs") or ()),
                        "selected_source_labels": list(item.get("source_labels") or ()),
                    },
                })
            _clarification_state_store().create(
                session_id,
                {
                    **dict(pending),
                    "status": "awaiting",
                    "topic_signature": str(pending.get("topic_signature") or pending.get("original_query") or ""),
                    "candidates": candidates,
                },
                route_snapshot={
                    "clarification_question": str(pending.get("question") or "请确认适用的证据来源。"),
                    "legacy_pending": dict(pending),
                },
                max_rounds=2,
            )
        elif pending.get("status") == "resolved":
            clear_pending_clarification(session_id, redis_client=redis_client)
        return
    if isinstance(pending, Mapping) and pending.get("kind"):
        status = str(pending.get("status") or "")
        if status == "awaiting_answer":
            candidates = pending.get("candidates") or pending.get("alternatives") or pending.get("options") or ()
            payload = {
                **dict(pending),
                "status": "awaiting",
                "topic_signature": str(pending.get("topic_signature") or pending.get("originalQuery") or pending.get("original_query") or ""),
                "original_query": str(pending.get("original_query") or pending.get("originalQuery") or ""),
                "candidates": [dict(item) for item in candidates if isinstance(item, Mapping)],
            }
            state = _clarification_state_store().create(
                session_id,
                payload,
                route_snapshot={
                    "clarification_question": str(pending.get("question") or "请从候选项中确认一个答案。"),
                    "legacy_pending": dict(pending),
                },
                max_rounds=2,
            )
            if isinstance(metadata, dict):
                metadata["pending_clarification"] = state.to_dict()
        elif status == "resolved":
            state = _clarification_state_store().load(session_id)
            if state and state.status is ClarificationStatus.RESOLVED:
                return


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
    done = _ensure_stream_done_image_field({
        "event": "done",
        "data": {
            "tools_used": output.tools_used,
            "latency_ms": output.latency_ms,
            "domainRule": output.metadata.get("domain_rule"),
            "confidenceSource": output.metadata.get("confidence_source"),
            "evidenceSources": output.metadata.get("evidence_sources", []),
            "metadata": output.metadata,
        },
    })
    yield f"data: {json_dumps(done)}\n\n"


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


def _response_policy_direct_must_defer(context: Mapping[str, Any] | None) -> bool:
    """已选定章节时，证据不足策略必须让位给章节检索。"""
    context = context or {}
    policy = context.get("response_policy")
    return bool(
        isinstance(policy, Mapping)
        and policy.get("mode") == "INSUFFICIENT_EVIDENCE"
        and _route_plan_authorizes_structural_lookup(context)
    )


async def _try_response_policy_direct(request: ChatRequest, input_data: AgentInput) -> AgentOutput | None:
    """Handle non-retrieval answer modes before ReAct can call knowledge tools."""
    policy = (input_data.context or {}).get("response_policy") or {}
    mode = policy.get("mode")
    if mode not in {"GENERAL_AI", "MAINTENANCE_AI_FALLBACK", "INSUFFICIENT_EVIDENCE"}:
        return None
    if mode == "INSUFFICIENT_EVIDENCE" and not _response_policy_direct_must_defer(input_data.context):
        return AgentOutput(
            agent_name="fix_agent",
            message="当前资料未说明所问的具体参数或操作步骤，因此无法可靠确认。请补充对应手册或其他可验证资料后再核对。",
            intention=(input_data.context or {}).get("intention"),
            metadata={"execution_mode": "insufficient_evidence_direct", "deterministic_direct": True, "response_policy": policy, "scope_decision": (input_data.context or {}).get("scope_decision") or {}},
            raw_response={"mode": mode},
        )
    intent = (input_data.context or {}).get("intent_decision") or {}
    scope_decision = (input_data.context or {}).get("scope_decision") or {}
    if (
        mode == "MAINTENANCE_AI_FALLBACK"
        and intent.get("task_action") == "find_cause"
        and scope_decision.get("status") != OUT_OF_SCOPE
        and not (input_data.context or {}).get("post_retrieval_fallback")
    ):
        # Diagnostic ambiguity can only be assessed after retrieval/graph
        # candidates exist.  Let the normal agent path gather that evidence;
        # the finalizer will still use the AI fallback when no candidates are
        # available.
        return None
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
            "你是检修 AI 助手。当前知识库没有找到足以支持本次回答的可靠资料。"
            "请说明知识库证据不足、以下内容来自 AI、仅供参考，然后给出低风险通用分析。"
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
    input_context = input_data.context or {}
    clarification_constraints = input_context.get("clarification_constraints")
    clarification_constraints = (
        clarification_constraints if isinstance(clarification_constraints, Mapping) else {}
    )
    resolved_clarification = input_context.get("resolved_clarification")
    resolved_clarification = (
        resolved_clarification if isinstance(resolved_clarification, Mapping) else {}
    )
    resolved_observation = bool(
        (
            clarification_constraints.get("clarification_source") == "llm_fallback"
            and clarification_constraints.get("clarification_dimension")
            in {"symptom", "operating_condition"}
        )
        or resolved_clarification.get("kind")
        in {"graph_observation", "llm_slot_clarification"}
    )
    policy_allows_fallback = bool(
        policy.get("mode") == "MAINTENANCE_AI_FALLBACK"
        and policy.get("allow_ai_fallback") is True
    )
    if (
        audited_output.metadata.get("coverage_status") != "unsupported"
        or not (policy_allows_fallback or resolved_observation)
        or audited_output.metadata.get("blocked_for_document_isolation")
        or not _is_knowledge_output(audited_output)
    ):
        return None

    fallback_context = dict(input_data.context or {})
    fallback_policy = dict(policy)
    if resolved_observation:
        fallback_policy.update({
            "mode": "MAINTENANCE_AI_FALLBACK",
            "source_type": "ai",
            "allow_knowledge_retrieval": False,
            "allow_ai_fallback": True,
            "manual_citation_allowed": False,
            "images_allowed": False,
            "disclaimer_required": True,
            "style_profile": "maintenance_ai",
        })
    fallback_context["post_retrieval_fallback"] = True
    fallback_context["response_policy"] = fallback_policy
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
    pending = output.metadata.get("pending_clarification") if isinstance(output.metadata, Mapping) else None
    route_plan = output.metadata.get("route_plan") if isinstance(output.metadata, Mapping) else None
    if (
        isinstance(pending, Mapping)
        and str(pending.get("status") or "") in {"awaiting", "reasked", "awaiting_answer"}
    ) or (
        isinstance(route_plan, Mapping)
        and route_plan.get("action") in {RouteAction.CLARIFY.value, RouteAction.CLARIFY_DOCUMENT.value}
    ):
        if candidate_message is not None:
            output.message = candidate_message
        return output
    audited = _finalize_knowledge_output(
        input_data.user_message,
        output,
        candidate_message=candidate_message,
    )
    try:
        fallback = await _try_post_retrieval_ai_fallback(request, input_data, audited)
    except Exception as exc:
        logger.warning(
            "[fallback] post-retrieval AI unavailable session=%s error_type=%s",
            request.session_id,
            type(exc).__name__,
        )
        audited.metadata["post_retrieval_fallback_status"] = "unavailable"
        audited.metadata["post_retrieval_fallback_error_type"] = type(exc).__name__
        return audited
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
    pending_clarification = execution.metadata.get("pending_clarification")
    if isinstance(pending_clarification, Mapping):
        if _clarification_mode() == "off":
            return None
        state = _clarification_state_store().create(
            request.session_id,
            {
                **dict(pending_clarification),
                "topic_signature": topic_signature_for_contract(plan.query_contract)
                or str(pending_clarification.get("topic_signature") or ""),
            },
            route_snapshot=plan.to_dict(),
            max_rounds=int(pending_clarification.get("max_rounds") or 2),
        )
        execution.metadata["pending_clarification"] = state.to_dict()
        execution.metadata["clarification_state_authoritative"] = True
        if _clarification_mode() == "shadow":
            return None
    if (
        isinstance(pending_selection, dict)
        and _clarification_mode() == "enforce"
        and (
            not isinstance(pending_clarification, Mapping)
            or str(pending_clarification.get("kind") or "") == "document_selection"
        )
    ):
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
    done = _ensure_stream_done_image_field({
        "event": "done",
        "data": {
            "tools_used": output.tools_used,
            "latency_ms": output.latency_ms,
            "metadata": output.metadata,
        },
    })
    yield f"data: {json_dumps(done)}\n\n"


async def _stream_policy_direct_output(output: AgentOutput):
    import asyncio as _asyncio

    visible_message = strip_user_visible_emojis(output.message)
    for index, char in enumerate(visible_message):
        event = {"event": "token", "data": {"content": char}}
        yield "data: " + json_dumps(event) + chr(10) + chr(10)
        if index % 15 == 0:
            await _asyncio.sleep(0)
    done = _ensure_stream_done_image_field({
        "event": "done",
        "data": {"tools_used": [], "latency_ms": output.latency_ms, "metadata": output.metadata},
    })
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
    unified_conflict = context.get("resolved_evidence_conflict")
    if isinstance(unified_conflict, Mapping):
        restored_query = str(unified_conflict.get("original_query") or request.message).strip()
        selected_refs = [
            str(item) for item in unified_conflict.get("selected_evidence_refs") or ()
            if str(item).strip()
        ]
        return AgentOutput(
            agent_name="fix_agent",
            message=format_pending_resolution(unified_conflict),
            intention="knowledge_query",
            tools_used=[FOLLOW_UP_TOOL_NAME],
            metadata={
                "execution_mode": "evidence_conflict_resolved",
                "deterministic_direct": True,
                "confidence_source": "user_clarification",
                "pending_clarification": dict(unified_conflict),
                "restored_query": restored_query,
                "selected_evidence_refs": selected_refs,
                "evidence_constraints": {
                    "allowed_evidence_refs": selected_refs,
                    "selection_source": "user_clarification",
                },
            },
            latency_ms=int((time.time() - started) * 1000),
            raw_response=dict(unified_conflict),
        )
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
    if is_awaiting and pending.get("kind") != "evidence_conflict":
        state = _clarification_state_store().load(request.session_id)
        if state and state.status in {ClarificationStatus.AWAITING, ClarificationStatus.REASKED}:
            repeated = _clarification_state_store().reask(
                request.session_id,
                expected_version=state.version,
            )
            current = repeated or state
            question = str(current.route_snapshot.get("clarification_question") or pending.get("question") or "请从候选项中确认一个答案。")
            if current.status is ClarificationStatus.EXHAUSTED:
                question = "当前信息仍不足以安全确定适用对象，已停止继续猜测。请补充设备型号、文档版本或可验证的现场信息。"
            return AgentOutput(
                agent_name="fix_agent",
                message=question,
                intention="knowledge_query",
                tools_used=[FOLLOW_UP_TOOL_NAME],
                metadata={
                    "execution_mode": "clarification_repeat",
                    "deterministic_direct": True,
                    "pending_clarification": current.to_dict(),
                    "clarification_state_authoritative": True,
                },
                latency_ms=int((time.time() - started) * 1000),
                raw_response=current.to_dict(),
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
    done = _ensure_stream_done_image_field({"event": "done", "data": done_data})
    yield f"data: {json_dumps(done)}\n\n"


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


def _apply_resolved_scope_authority(
    decision: ScopeDecision,
    *,
    resolved_scope: ResolvedScope | None,
    selected_document_id: str,
    selected_section_id: str,
) -> ScopeDecision:
    """Authorize a server-resolved clarification scope without relaxing conflicts."""
    if decision.status != UNKNOWN_SCOPE or resolved_scope is None:
        return decision
    if not selected_document_id or selected_document_id != resolved_scope.document_id:
        return decision
    allowed_sections = set(resolved_scope.allowed_section_ids)
    if allowed_sections and selected_section_id not in allowed_sections:
        return decision
    return ScopeDecision(**{
        **decision.to_dict(),
        "status": IN_SCOPE,
        "source": "resolved_clarification",
        "reason": "server_authoritative_scope",
        "document_id": selected_document_id,
    })


def _apply_graph_scope_authority(
    decision: ScopeDecision,
    *,
    route_plan: RoutePlan,
) -> ScopeDecision:
    """Authorize a graph-selected document after graph provenance has converged."""
    if decision.status != OUT_OF_SCOPE:
        return decision
    if not route_plan.selected_graph_candidate_id or not route_plan.selected_document_id:
        return decision
    graph_scope = route_plan.graph_scope if isinstance(route_plan.graph_scope, Mapping) else {}
    selected_document_id = str(route_plan.selected_document_id or "").strip()
    if str(graph_scope.get("document_id") or "").strip() != selected_document_id:
        return decision
    if str(decision.document_id or "").strip() != selected_document_id:
        return decision
    if str(decision.reason or "").strip() != "device_document_conflict":
        return decision
    return ScopeDecision(**{
        **decision.to_dict(),
        "status": IN_SCOPE,
        "source": "resolved_graph_scope",
        "reason": "server_authoritative_graph_scope",
        "document_id": selected_document_id,
    })


def _apply_llm_clarification_constraints(
    contract: QueryContract,
    constraints: Mapping[str, Any] | None,
) -> QueryContract:
    """Merge a confirmed observable answer into retrieval and generation text.

    LLM options remain hints only. Graph observation options additionally carry
    server-owned graph allow-lists, but their visible symptom still needs to be
    copied into the query contract so the selected phenomenon affects ranking
    and any cold-start AI fallback answer.
    """
    source = constraints if isinstance(constraints, Mapping) else {}
    llm_clarification = source.get("clarification_source") == "llm_fallback"
    graph_observation = str(source.get("observable_symptom") or "").strip()
    if not llm_clarification and not graph_observation:
        return contract
    payload = contract.to_dict()
    component = str(source.get("component") or "").strip() if llm_clarification else ""
    if component:
        payload["component"] = component
    for field in ("symptoms", "operating_conditions"):
        current = payload.get(field) if isinstance(payload.get(field), list) else []
        added = source.get(field) if isinstance(source.get(field), (list, tuple)) else []
        if field == "symptoms" and graph_observation:
            added = (*added, graph_observation)
        payload[field] = list(dict.fromkeys(
            str(value).strip() for value in (*current, *added) if str(value).strip()
        ))
    return QueryContract.from_mapping(payload, raw_query=contract.raw_query)


def _clarified_query_text(contract: QueryContract) -> str:
    """Expose confirmed observable details to retrieval and the final Agent turn."""
    query = str(contract.raw_query or "").strip()
    additions: list[str] = []
    if contract.component and contract.component not in query:
        additions.append(f"部件线索：{contract.component}")
    for label, values in (
        ("现场现象", contract.symptoms),
        ("发生工况", contract.operating_conditions),
    ):
        fresh = [str(value) for value in values if str(value) and str(value) not in query]
        if fresh:
            additions.append(f"{label}：{'、'.join(fresh)}")
    return f"{query}；用户已确认：{'；'.join(additions)}" if additions else query


def _llm_clarification_round(context: Mapping[str, Any]) -> int:
    constraints = context.get("clarification_constraints")
    if not isinstance(constraints, Mapping):
        return 0
    if constraints.get("clarification_source") != "llm_fallback":
        return 0
    try:
        return max(0, int(constraints.get("clarification_round") or 0))
    except (TypeError, ValueError):
        return 0


def _is_vague_diagnostic_request(
    plan: RoutePlan,
    intent_decision: IntentDecision,
) -> bool:
    """Use structured fields to identify diagnosis requests missing key context."""
    contract = plan.query_contract
    raw_query = str(contract.raw_query or "").strip()
    asks_for_operating_condition = bool(
        re.search(
            r"(?:什么|哪个|哪些|何种|哪种).{0,10}(?:工况|运行阶段|阶段|时候|时机|情况下)|"
            r"(?:工况|运行阶段|阶段|时候|时机).{0,10}(?:什么|哪个|哪些|何种|哪种)",
            raw_query,
        )
    )
    if (
        asks_for_operating_condition
        or
        intent_decision.intent in {"parameter_query", "procedure_planning"}
        or intent_decision.task_action in {"parameter_lookup", "formal_procedure"}
    ):
        return False

    has_fault_observation = bool(
        contract.fault
        or contract.raw_fault_span
        or contract.symptoms
    )
    is_diagnostic = bool(
        has_fault_observation
        and (
            intent_decision.intent in {"fault_diagnosis", "maintenance_guidance"}
            or intent_decision.task_action in {"find_cause", "repair_guidance"}
        )
    )
    if not is_diagnostic:
        return False

    has_component = bool(contract.component or contract.raw_component_span)
    has_operating_condition = bool(contract.operating_conditions)
    return not (has_component and has_fault_observation and has_operating_condition)


async def _maybe_apply_llm_clarification(
    plan: RoutePlan,
    *,
    intent_decision: IntentDecision,
    graph_candidates: tuple,
    context: Mapping[str, Any],
) -> RoutePlan:
    """Ask the LLM for one safe observable discriminator after evidence ambiguity."""
    diagnostic_evidence_gap = bool(
        plan.reason == "diagnostic_ambiguity_without_observable_discriminator"
        or _is_vague_diagnostic_request(plan, intent_decision)
    )
    graph_route_is_usable = bool(
        plan.selected_graph_candidate_id
        or plan.graph_scope
        or plan.action == RouteAction.CLARIFY
    )
    if (
        _clarification_mode() == "off"
        or not diagnostic_evidence_gap
        or plan.action not in {RouteAction.AI_FALLBACK, RouteAction.GROUNDED_RETRIEVAL}
        or graph_route_is_usable
    ):
        return plan
    previous_round = _llm_clarification_round(context)
    if previous_round >= 2:
        return plan
    round_count = previous_round + 1
    draft = await LLMClarificationService(get_llm_service()).build(
        query=plan.query_contract.raw_query,
        query_contract=plan.query_contract.to_dict(),
        confirmed_constraints=context.get("clarification_constraints")
        if isinstance(context.get("clarification_constraints"), Mapping)
        else None,
        graph_candidates=graph_candidates,
        round_count=round_count,
    )
    used_safe_fallback = draft is None
    if draft is None:
        draft = build_safe_observation_fallback(
            plan.query_contract.to_dict(),
            round_count=round_count,
        )
    return replace(
        plan,
        action=RouteAction.CLARIFY,
        allowed_tools=(),
        answer_source="llm_clarification",
        allow_ai_fallback=False,
        reason=(
            "safe_observation_clarification_after_llm_gap"
            if used_safe_fallback
            else "llm_observation_clarification_after_evidence_gap"
        ),
        clarification_options=draft.options,
        clarification_kind="llm_slot_clarification",
        clarification_question=draft.question,
        selected_section_id="",
        graph_scope={},
        selected_graph_candidate_id="",
    )


async def _prepare_chat_agent_input(request: ChatRequest) -> AgentInput:
    original_user_message = request.message or ""
    raw_message = original_user_message
    effective_message = raw_message.strip() or IMAGE_ONLY_DEFAULT_MESSAGE
    context = dict(request.context or {})
    rag_variant = _initialize_rag_variant_context(context)
    authoritative_query_contract: QueryContract | None = None
    _restore_trusted_pending_context(request.session_id, context)
    # 统一反问状态优先于客户端上下文；客户端只能提交答案，不能替换候选集合。
    clarification_state = (
        _clarification_state_store().load(request.session_id)
        if _clarification_mode() == "enforce"
        else None
    )
    if clarification_state and clarification_state.status in {
        ClarificationStatus.AWAITING,
        ClarificationStatus.REASKED,
    }:
        resolved_state = _clarification_state_store().resolve(
            request.session_id,
            answer=raw_message,
            expected_version=clarification_state.version,
        )
        if resolved_state is None and clarification_state.kind == "evidence_conflict":
            legacy_pending = clarification_state.route_snapshot.get("legacy_pending")
            legacy_resolved = resolve_pending_clarification(
                {"pending_clarification": legacy_pending},
                raw_message,
            ) if isinstance(legacy_pending, Mapping) else None
            if legacy_resolved:
                resolved_state = _clarification_state_store().resolve(
                    request.session_id,
                    answer=str(legacy_resolved.get("selected_option_id") or ""),
                    expected_version=clarification_state.version,
                )
        if resolved_state and resolved_state.status is ClarificationStatus.RESOLVED:
            constraints = dict(resolved_state.selected_constraints)
            resolved_scope = ResolvedScope.from_constraints(constraints)
            snapshot_contract = resolved_state.route_snapshot.get("query_contract")
            if isinstance(snapshot_contract, Mapping):
                authoritative_query_contract = QueryContract.from_mapping(
                    snapshot_contract,
                    raw_query=resolved_state.original_query,
                )
            if constraints.get("document_id"):
                context["confirmed_document_id"] = str(constraints["document_id"])
            if constraints.get("section_id"):
                context["confirmed_section_id"] = str(constraints["section_id"])
            elif resolved_scope and len(resolved_scope.allowed_section_ids) == 1:
                context["confirmed_section_id"] = resolved_scope.allowed_section_ids[0]
            if resolved_scope:
                context["resolved_scope"] = resolved_scope.to_dict()
            context["clarification_constraints"] = constraints
            context["resolved_clarification"] = resolved_state.to_dict()
            context["clarification_answer"] = raw_message
            legacy_pending = resolved_state.route_snapshot.get("legacy_pending")
            if isinstance(legacy_pending, Mapping):
                if legacy_pending.get("kind") == "evidence_conflict":
                    context["resolved_evidence_conflict"] = {
                        **dict(legacy_pending),
                        **dict(resolved_state.selected_constraints),
                        "status": "resolved",
                        "selected_option_id": resolved_state.selected_option_id,
                    }
                else:
                    context["diagnostic_follow_up"] = dict(legacy_pending)
                    context["selected_option_id"] = resolved_state.selected_option_id
            clear_pending_clarification(
                request.session_id,
                redis_client=_pending_clarification_redis_client(),
            )
            clear_pending_document_selection(request.session_id)
            raw_message = resolved_state.original_query
            effective_message = raw_message.strip() or IMAGE_ONLY_DEFAULT_MESSAGE
        else:
            new_topic = False
            try:
                candidate_decision = await get_intent_router().classify(
                    raw_message,
                    images=request.images,
                    context=context,
                )
                candidate_contract = QueryContract.from_mapping(
                    candidate_decision.model_dump(),
                    raw_query=raw_message,
                )
                candidate_topic = topic_signature_for_contract(candidate_contract)
                new_topic = bool(
                    candidate_topic
                    and clarification_state.topic_signature
                    and candidate_topic != clarification_state.topic_signature
                )
            except Exception as exc:
                logger.warning("[clarification] topic comparison unavailable: %s", exc)
            if new_topic:
                _clarification_state_store().cancel_for_topic(
                    request.session_id,
                    candidate_topic,
                )
                clear_pending_document_selection(request.session_id)
                context.pop("pending_clarification", None)
            else:
                context["pending_clarification"] = clarification_state.to_dict()
                pending_plan_payload = clarification_state.route_snapshot
                if isinstance(pending_plan_payload, Mapping):
                    context["route_plan"] = dict(pending_plan_payload)
                context["intent_decision"] = {
                    "intent": "knowledge_query",
                    "task_action": "document_explain",
                    "requires_knowledge_retrieval": False,
                }
                context["response_policy"] = {
                    "mode": "CLARIFICATION",
                    "source_type": "deterministic_clarification",
                    "allow_knowledge_retrieval": False,
                    "allow_ai_fallback": False,
                }
                return AgentInput(
                    user_message=effective_message,
                    session_id=request.session_id,
                    images=request.images,
                    conversation_history=request.conversation_history,
                    context=context,
                )
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
    context["original_user_message"] = original_user_message
    if raw_message == original_user_message:
        resolved_image_query = _restore_grounded_image_followup(
            request,
            original_user_message,
            context,
        )
        if resolved_image_query:
            raw_message = resolved_image_query
            effective_message = resolved_image_query

    turn_ts = int(time.time() * 1000)
    context["turn_ts"] = turn_ts
    session_document_id = context.get("confirmed_document_id")
    session_device_type = context.get("confirmed_device_type")

    intent_router = get_intent_router()
    frozen_contract = context.get("_evaluation_route_contract")
    frozen_intent = frozen_contract.get("intent_decision") if isinstance(frozen_contract, Mapping) else None
    frozen_query = frozen_contract.get("query_contract") if isinstance(frozen_contract, Mapping) else None
    if isinstance(frozen_intent, Mapping) and isinstance(frozen_query, Mapping):
        intent_decision = IntentDecision.model_validate(dict(frozen_intent))
        authoritative_query_contract = QueryContract.from_mapping(
            frozen_query,
            raw_query=raw_message,
        )
        context["evaluation_route_contract_applied"] = True
    else:
        intent_decision = await intent_router.classify(
            raw_message,
            images=request.images,
            context=context,
        )
        context["evaluation_route_contract_applied"] = False
    context["intent_decision"] = intent_decision.model_dump()
    context["intention"] = intent_decision.intent
    query_contract = authoritative_query_contract or QueryContract.from_mapping(
        intent_decision.model_dump(),
        raw_query=raw_message,
    )
    query_contract = _apply_llm_clarification_constraints(
        query_contract,
        context.get("clarification_constraints")
        if isinstance(context.get("clarification_constraints"), Mapping)
        else None,
    )
    effective_message = _clarified_query_text(query_contract) or effective_message
    context["query_contract"] = query_contract.to_dict()
    technical_route = intent_decision.intent not in {"chat_social", "knowledge_inventory"}
    device_catalog = DeviceCatalog(())
    section_refs = ()
    if technical_route:
        try:
            device_catalog = await load_dynamic_device_catalog()
        except Exception as exc:
            logger.error("[scope] dynamic document catalog unavailable: %s", exc)
        identity_result = normalize_query_identity(query_contract, device_catalog)
        query_contract = identity_result.contract
        context["query_contract"] = query_contract.to_dict()
        context["identity_normalization"] = {
            "status": identity_result.status,
            "reason": identity_result.reason,
            "raw_device_span": query_contract.raw_device_span,
            "canonical_device_name": query_contract.device_name,
            "catalog_verified": query_contract.identity_resolution == "catalog_exact",
            "matched_document_id": identity_result.matched_document_id,
        }
        try:
            section_index = SectionTitleIndex.get_instance()
            section_index.build(get_vector_service())
            title_section_refs = tuple(section_index.find(raw_message))
            find_exact = getattr(section_index, "find_exact", None)
            find_evidence = getattr(section_index, "find_evidence", None)
            exact_section_refs = tuple(find_exact(raw_message)) if callable(find_exact) else ()
            evidence_section_refs = tuple(find_evidence(query_contract)) if callable(find_evidence) else ()
            if exact_section_refs:
                section_refs = exact_section_refs
            elif evidence_section_refs:
                section_refs = evidence_section_refs
            else:
                section_refs = title_section_refs
            resolved_scope = ResolvedScope.from_constraints(context.get("resolved_scope") or {})
            confirmed_section_id = str(context.get("confirmed_section_id") or "").strip()
            allowed_section_ids = (
                resolved_scope.allowed_section_ids
                if resolved_scope is not None
                else ((confirmed_section_id,) if confirmed_section_id else ())
            )
            if allowed_section_ids:
                scoped_refs = tuple(
                    ref for ref in section_refs
                    if ref.section_id in allowed_section_ids
                    and (resolved_scope is None or ref.document_id == resolved_scope.document_id)
                )
                if not scoped_refs and resolved_scope is not None:
                    refs_for_scope = getattr(section_index, "refs_for_scope", None)
                    if callable(refs_for_scope):
                        scoped_refs = tuple(refs_for_scope(
                            document_id=resolved_scope.document_id,
                            section_ids=allowed_section_ids,
                        ))
                section_refs = scoped_refs
        except Exception as exc:
            logger.warning("[routing] dynamic section catalog unavailable: %s", exc)
    if (
        authoritative_query_contract is None
        and
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
                    identity_result = normalize_query_identity(query_contract, device_catalog)
                    query_contract = identity_result.contract
                    context["query_contract"] = query_contract.to_dict()
                    context["identity_normalization"] = {
                        "status": identity_result.status,
                        "reason": identity_result.reason,
                        "raw_device_span": query_contract.raw_device_span,
                        "canonical_device_name": query_contract.device_name,
                        "catalog_verified": query_contract.identity_resolution == "catalog_exact",
                        "matched_document_id": identity_result.matched_document_id,
                    }
            except Exception as exc:
                logger.warning("[scope] focused identity refinement unavailable: %s", exc)
    if authoritative_query_contract is None:
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
    graph_policy = decide_graph_use(rag_variant, query_contract.to_dict())
    context["graph_policy"] = {
        "candidate_enabled": graph_policy.candidate_enabled,
        "pre_retrieval_enabled": graph_policy.pre_retrieval_enabled,
        "may_influence_route": graph_policy.may_influence_route,
        "may_enter_evidence": graph_policy.may_enter_evidence,
        "graph_review_enabled": graph_policy.graph_review_enabled,
        "allowed_claim_types": list(graph_policy.allowed_claim_types),
        "reason": graph_policy.reason,
    }
    graph_candidates = ()
    if technical_route and graph_policy.candidate_enabled:
        try:
            graph_scope = ResolvedScope.from_constraints(context.get("resolved_scope") or {})
            graph_provider = get_graph_candidate_provider()
            candidate_result = await graph_provider.fetch_result(
                query_contract,
                image_urls=list(request.images or ()),
                allowed_document_ids=(
                    (graph_scope.document_id,)
                    if graph_scope is not None and graph_scope.document_id
                    else ((str(request.document_id),) if str(request.document_id or "").strip() else ())
                ),
                allowed_section_ids=graph_scope.allowed_section_ids if graph_scope else (),
                allowed_source_chunk_uids=graph_scope.allowed_source_chunk_uids if graph_scope else (),
                allowed_evidence_refs=graph_scope.allowed_evidence_refs if graph_scope else (),
                allowed_device_ids=graph_scope.allowed_device_ids if graph_scope else (),
                allowed_component_ids=graph_scope.allowed_component_ids if graph_scope else (),
                allowed_fault_ids=graph_scope.allowed_fault_ids if graph_scope else (),
                allowed_path_ids=graph_scope.allowed_path_ids if graph_scope else (),
                allowed_graph_node_ids=graph_scope.allowed_graph_node_ids if graph_scope else (),
            )
            graph_candidates = candidate_result.candidates
            graph_candidates = filter_candidates_by_resolved_scope(
                graph_candidates,
                graph_scope,
            )
            candidate_retrieval = dict(candidate_result.retrieval_status)
            context["graph_candidate_retrieval"] = candidate_retrieval
            context["graph_candidate_query_count"] = _graph_candidate_query_count_for_status(
                candidate_retrieval.get("status")
            )
        except Exception as exc:
            logger.info("[routing] graph candidate query unavailable: %s", exc)
            context["graph_candidate_query_count"] = 1
            context["graph_candidate_retrieval"] = {
                "status": "unavailable",
                "reason": "candidate_query_exception",
                "diagnostics": {"error": str(exc)},
            }
    elif not graph_policy.candidate_enabled:
        context["graph_candidate_retrieval"]["reason"] = graph_policy.reason
    else:
        context["graph_candidate_retrieval"]["reason"] = "non_technical_route"
    context["graph_candidate_count"] = len(graph_candidates)
    route_plan = await SemanticRoutingOrchestrator().build_plan(
        query=raw_message,
        decision=intent_decision,
        catalog=device_catalog,
        section_refs=section_refs,
        request_document_id=str(request.document_id or ""),
        session_document_id=str(session_document_id or ""),
        query_contract=query_contract,
        graph_candidates=(graph_candidates if graph_policy.may_influence_route else ()),
        preserve_query_contract=authoritative_query_contract is not None,
        graph_policy=context["graph_policy"],
    )
    route_plan = await _maybe_apply_llm_clarification(
        route_plan,
        intent_decision=intent_decision,
        graph_candidates=tuple(graph_candidates or ()),
        context=context,
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
    resolved_scope = ResolvedScope.from_constraints(context.get("resolved_scope") or {})
    scope_decision = _apply_resolved_scope_authority(
        scope_decision,
        resolved_scope=resolved_scope,
        selected_document_id=selected_document_id,
        selected_section_id=route_plan.selected_section_id,
    )
    scope_decision = _apply_graph_scope_authority(
        scope_decision,
        route_plan=route_plan,
    )
    context["scope_decision"] = scope_decision.to_dict()
    effective_graph_scope = _effective_server_graph_scope(
        route_plan.graph_scope,
        graph_candidates,
    )
    context["graph_scope"] = effective_graph_scope
    graph_pre_started = time.time()
    try:
        graph_batch = await GraphPreRetrievalService().retrieve(
            rag_variant=rag_variant,
            route_plan=route_plan.to_dict(),
            graph_scope=effective_graph_scope,
            image_urls=list(request.images or ()),
        )
        graph_pre_payload = graph_batch.to_dict()
        graph_pre_payload.setdefault("diagnostics", {})["latency_ms"] = int(
            (time.time() - graph_pre_started) * 1000
        )
        if graph_policy.may_enter_evidence:
            context["graph_pre_retrieval"] = graph_pre_payload
        elif rag_variant == "graph_shadow":
            context["graph_shadow_retrieval"] = graph_pre_payload
            context["graph_pre_retrieval"] = {
                "status": graph_pre_payload.get("status", "not_applicable"),
                "reason": "shadow_audit_only",
                "evidence": [],
                "diagnostics": graph_pre_payload.get("diagnostics", {}),
            }
        else:
            context["graph_pre_retrieval"] = graph_pre_payload
    except Exception as exc:
        logger.info("[routing] graph pre-retrieval unavailable: %s", exc)
        context["graph_pre_retrieval"] = {
            "status": "unavailable",
            "reason": "graph_pre_retrieval_exception",
            "evidence": [],
            "diagnostics": {
                "error": str(exc),
                "latency_ms": int((time.time() - graph_pre_started) * 1000),
            },
        }
    if (
        route_plan.action == RouteAction.GROUNDED_RETRIEVAL
        and selected_document_id
        and scope_decision.status == "in_scope"
    ):
        manual_scopes = _build_effective_manual_scopes(
            selected_document_id=selected_document_id,
            selected_section_id=route_plan.selected_section_id,
            resolved_scope=(resolved_scope.to_dict() if resolved_scope is not None else None),
            effective_graph_scope=effective_graph_scope,
        )
        context["retrieval_scope"] = manual_scopes.baseline
        context["graph_seed_retrieval_scope"] = manual_scopes.graph_seed
    else:
        context["retrieval_scope"] = {}
        context["graph_seed_retrieval_scope"] = {}
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
    elif route_plan.action == RouteAction.CLARIFY:
        response_policy.update({
            "mode": "CLARIFICATION",
            "source_type": route_plan.answer_source,
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
    schedule_capture(original_user_message, context.get("user_id"), turn_ts)

    if request.images and intent_decision.requires_image_understanding:
        image_understanding = await _build_image_understanding(request.images, effective_message)
        context["image_understanding"] = image_understanding
        context["enhanced_retrieval_query"] = image_understanding["enhanced_query"]
        context["original_user_message"] = original_user_message

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


def _structured_contract_requests_table_lookup(metadata: dict | None) -> bool:
    route_plan = (metadata or {}).get("route_plan")
    if not isinstance(route_plan, dict):
        return False
    query_contract = route_plan.get("query_contract")
    if not isinstance(query_contract, dict):
        return False
    task_action = str(
        route_plan.get("task_action")
        or query_contract.get("task_action")
        or ""
    ).strip()
    if task_action != "parameter_lookup":
        return False
    targets = [
        item for item in query_contract.get("targets") or []
        if isinstance(item, dict)
    ]
    has_target = bool(
        str(query_contract.get("part_spec") or "").strip()
        or str(query_contract.get("component") or "").strip()
        or any(
            str(item.get("part_spec") or item.get("component") or "").strip()
            for item in targets
        )
    )
    requested_fields = list(query_contract.get("requested_fields") or [])
    requested_fields.extend(
        field
        for item in targets
        for field in item.get("requested_fields") or []
    )
    return has_target and any(str(field or "").strip() for field in requested_fields)


def _is_fault_diagnosis_route(metadata: dict | None) -> bool:
    route_plan = (metadata or {}).get("route_plan")
    if not isinstance(route_plan, Mapping):
        return False
    query_contract = route_plan.get("query_contract")
    query_contract = query_contract if isinstance(query_contract, Mapping) else {}
    intent = str(
        route_plan.get("intent")
        or query_contract.get("intent")
        or ""
    ).strip()
    task_action = str(
        route_plan.get("task_action")
        or query_contract.get("task_action")
        or ""
    ).strip()
    return intent == "fault_diagnosis" or task_action == "find_cause"


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
    remark = pick(remark_index)
    mapped_indexes = {
        index
        for index in (seq_index, name_index, quantity_index, remark_index)
        if index is not None
    }
    extra_fields = []
    for index, value in enumerate(cells):
        if index in mapped_indexes or not value:
            continue
        header = _inventory_cell(headers[index]) if index < len(headers) else ""
        extra_fields.append(f"{header} {value}".strip())
    if extra_fields:
        remark = " ".join(value for value in (remark, *extra_fields) if value)
    return {
        "seq": pick(seq_index),
        "name": name,
        "quantity": quantity,
        "remark": remark,
    }


def _inventory_row_from_key_values(content: str) -> dict | None:
    fields: dict[str, str] = {}
    current_key = ""
    for part in re.split(r"[；;]\s*", content or ""):
        # Table-row chunks may serialize a cell's value over several physical
        # lines.  Once the remark/tool cell has started, keep those continuation
        # lines attached to that field instead of silently dropping parameters.
        for line in str(part).splitlines():
            line = _inventory_cell(line)
            if not line:
                continue
            if "=" in line:
                key, value = line.split("=", 1)
            elif "：" in line:
                key, value = line.split("：", 1)
            else:
                if current_key and any(
                    marker in current_key for marker in ("备注", "说明", "工具")
                ):
                    fields[current_key] = (
                        f"{fields.get(current_key, '').strip()} {line}"
                    ).strip()
                continue
            current_key = _inventory_cell(key)
            fields[current_key] = _inventory_cell(value)

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
    seen: dict[tuple[str, str], dict] = {}
    deduped: list[dict] = []
    for row in rows:
        key = (row.get("seq") or "", row.get("name") or "")
        existing = seen.get(key)
        if existing is not None:
            # The same table row can arrive through the direct section lookup
            # and the semantic trace.  Prefer the union of their fields so a
            # truncated row cannot hide continuation parameters from a richer
            # evidence record.
            for field in ("seq", "name", "quantity"):
                if not str(existing.get(field) or "").strip() and str(row.get(field) or "").strip():
                    existing[field] = row[field]
            incoming_remark = str(row.get("remark") or "").strip()
            existing_remark = str(existing.get("remark") or "").strip()
            if incoming_remark and incoming_remark not in existing_remark:
                existing["remark"] = " ".join(
                    value for value in (existing_remark, incoming_remark) if value
                )
            continue
        copied = dict(row)
        seen[key] = copied
        deduped.append(copied)
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


def _structured_inventory_part_specs(metadata: dict | None) -> tuple[str, ...]:
    route_plan = metadata.get("route_plan") if isinstance(metadata, dict) else None
    query_contract = route_plan.get("query_contract") if isinstance(route_plan, dict) else None
    if not isinstance(query_contract, dict):
        return ()
    values = [query_contract.get("part_spec")]
    for target in query_contract.get("targets") or []:
        if isinstance(target, dict):
            values.append(target.get("part_spec"))
    return tuple(dict.fromkeys(
        normalized
        for value in values
        if (normalized := _compact_inventory_text(value)) and len(normalized) >= 2
    ))


def _filter_inventory_rows_for_query(
    message: str,
    rows: list[dict],
    metadata: dict | None = None,
) -> list[dict]:
    """For targeted inventory questions, return only matching rows.

    Full-list questions still return the whole table.  This keeps the
    deterministic BOM path useful for "展示清单", while avoiding unrelated row
    quantities/torques when the user asks about a specific part.
    """
    structured_specs = _structured_inventory_part_specs(metadata)
    if structured_specs:
        exact_rows = [
            row for row in rows
            if any(
                spec in _compact_inventory_text(row.get("name") or "")
                for spec in structured_specs
            )
        ]
        if exact_rows:
            return exact_rows
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
    if _is_fault_diagnosis_route(metadata):
        return None
    if not (
        _is_inventory_table_query(message)
        or _structured_contract_requests_table_lookup(metadata)
    ):
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
    filtered_rows = _filter_inventory_rows_for_query(message, rows, metadata)
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


def _manual_focus_records_to_structured_action(
    records: list[dict],
    metadata: dict,
) -> tuple[list[dict], bool]:
    """Focus an open-vocabulary procedure action from the semantic contract.

    The legacy renderer knows a small set of operation families for ordered
    install/remove flows.  Other actions must not be added to that list one by
    one.  Instead, use the router's structured action and component as exact
    evidence constraints, and keep the legacy group only when no record proves
    that structured action.
    """
    route_plan = (metadata or {}).get("route_plan")
    if not isinstance(route_plan, Mapping):
        return records, False
    query_contract = route_plan.get("query_contract")
    if not isinstance(query_contract, Mapping):
        return records, False
    structured_action = _compact_inventory_text(query_contract.get("action") or "")
    if len(structured_action) < 2:
        return records, False

    action_hits = [
        record
        for record in records
        if structured_action in _compact_inventory_text(record.get("content") or "")
    ]
    if not action_hits:
        return records, False

    targets = [
        item for item in query_contract.get("targets") or []
        if isinstance(item, Mapping)
    ]
    component_anchors = tuple(dict.fromkeys(
        value
        for value in (
            _compact_inventory_text(query_contract.get("component") or ""),
            *(
                _compact_inventory_text(item.get("component") or "")
                for item in targets
            ),
        )
        if len(value) >= 2
    ))
    if component_anchors:
        component_hits = [
            record
            for record in action_hits
            if any(
                anchor in _compact_inventory_text(record.get("content") or "")
                for anchor in component_anchors
            )
        ]
        if component_hits:
            return component_hits, True
    return action_hits, True


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
    target = re.sub(r"(?:是什么|是怎样|是怎么|怎么|如何|怎样)$", "", tail.strip())
    target = target.strip()
    if target and target not in _MANUAL_ACTION_DESCRIPTOR_TERMS:
        return target
    head = re.sub(r"(?:怎么|如何|怎样|怎么进行|如何进行)$", "", head).strip()
    head = re.sub(r"[，,？?：:；;、\s]+$", "", head).strip()
    head = re.sub(r"^(?:这个|该|此)", "", head).strip()
    head = re.sub(r"的$", "", head).strip()
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
    title_bindings: list[tuple[str, str, str]] = []
    for source_record in best_group:
        source_meta = source_record.get("metadata") or {}
        source_title = str(source_meta.get("section_title") or "").strip()
        compact_title = _compact_inventory_text(source_title)
        source_section_id = str(source_meta.get("parent_section_id") or "").strip()
        binding = (compact_title, source_section_id, source_title)
        if len(compact_title) >= 4 and binding not in title_bindings:
            title_bindings.append(binding)
    titles = [binding[0] for binding in title_bindings]
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
            # A page-boundary chunk can carry the next section's literal title
            # while its imported metadata still points to the previous section.
            # Rebind only when the source text starts with the already-selected
            # title; a later mention of that title is not sufficient.  This lets
            # downstream procedure grouping keep the record inside the resolved
            # section without introducing any domain vocabulary.
            matching_bindings = [
                binding
                for binding in title_bindings
                if binding[0] and compact_content.startswith(binding[0])
            ]
            if matching_bindings:
                _, target_section_id, target_title = max(
                    matching_bindings,
                    key=lambda binding: len(binding[0]),
                )
                record_meta = dict(record.get("metadata") or {})
                current_section_id = str(record_meta.get("parent_section_id") or "").strip()
                current_title = str(record_meta.get("section_title") or "").strip()
                if target_section_id and current_section_id != target_section_id:
                    record_meta.setdefault("original_parent_section_id", current_section_id)
                    record_meta.setdefault("original_section_title", current_title)
                    record_meta["parent_section_id"] = target_section_id
                    record_meta["section_title"] = target_title
                    record_meta["section_match_ids"] = [target_section_id]
                    record_meta["embedded_heading_rebound"] = True
                    record["metadata"] = record_meta
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
    retrieval_scope = (metadata or {}).get("retrieval_scope") or {}
    allowed_section_ids = {
        str(value).strip()
        for value in retrieval_scope.get("allowed_section_ids") or []
        if str(value).strip()
    } if isinstance(retrieval_scope, Mapping) else set()
    filtered = []
    for record in records:
        record_metadata = record.get("metadata") or {}
        if (
            scoped_document_id
            and str(record_metadata.get("document_id") or "") != scoped_document_id
        ):
            continue
        if (
            allowed_section_ids
            and str(record_metadata.get("parent_section_id") or "") not in allowed_section_ids
        ):
            continue
        filtered.append(record)
    return filtered


def _manual_append_unique_records(records: list[dict], extra_records: list[dict]) -> list[dict]:
    if not extra_records:
        return records
    merged = [dict(item) for item in records]
    by_id = {
        str(item.get("id") or item.get("doc_id") or ""): item
        for item in merged
        if str(item.get("id") or item.get("doc_id") or "")
    }
    by_content = {
        str(item.get("content") or ""): item
        for item in merged
        if str(item.get("content") or "")
    }
    for record in extra_records:
        record_id = str(record.get("id") or record.get("doc_id") or "")
        content = str(record.get("content") or "")
        existing = by_id.get(record_id) if record_id else None
        if existing is None and content:
            existing = by_content.get(content)
        if existing is not None:
            existing_metadata = dict(existing.get("metadata") or {})
            existing_metadata.update(record.get("metadata") or {})
            existing["metadata"] = existing_metadata
            continue
        copied = dict(record)
        merged.append(copied)
        if record_id:
            by_id[record_id] = copied
        if content:
            by_content[content] = copied
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
    # A resolved clarification is already bound to server-owned section IDs.
    # The user's second message usually contains only the original component
    # and therefore cannot title-match the selected section again. Recover that
    # exact section directly from the vector store instead of reopening search.
    if route_authorizes_lookup:
        route_plan = (metadata or {}).get("route_plan") or {}
        retrieval_scope = (metadata or {}).get("retrieval_scope") or {}
        selected_section_ids = {
            str(value).strip()
            for value in (
                retrieval_scope.get("allowed_section_ids")
                if isinstance(retrieval_scope, Mapping)
                else ()
            ) or ()
            if str(value).strip()
        }
        selected_section_id = str(route_plan.get("selected_section_id") or "").strip()
        if selected_section_id:
            selected_section_ids.add(selected_section_id)
        if selected_section_ids and route_document_id:
            vector_service = _initialized_or_injected_vector_service(initialize=True)
            scoped_records: list[dict] = []
            if vector_service is not None:
                retrieval_scope = (metadata or {}).get("retrieval_scope") or {}
                authorized_record_ids = [
                    str(value).strip()
                    for value in (
                        retrieval_scope.get("allowed_evidence_refs")
                        if isinstance(retrieval_scope, Mapping)
                        else ()
                    ) or ()
                    if str(value).strip()
                ]
                # The selected evidence IDs are server-owned and survive the
                # clarification turn.  Read them directly before falling back
                # to RediSearch, whose indexed section query can be transiently
                # empty while concurrent requests are using the same index.
                if authorized_record_ids and hasattr(vector_service, "get_vector_records"):
                    try:
                        raw_records = vector_service.get_vector_records(authorized_record_ids)
                    except Exception:
                        raw_records = []
                    for raw in raw_records or ():
                        record = _manual_record_from_raw(raw)
                        if not record:
                            continue
                        record_metadata = dict(record.get("metadata") or {})
                        if (
                            str(record_metadata.get("document_id") or "") != route_document_id
                            or str(record_metadata.get("parent_section_id") or "") not in selected_section_ids
                        ):
                            continue
                        record_metadata[_STRUCTURAL_RECOVERY_LOOKUP_SOURCE] = "record_id_lookup"
                        record_metadata["section_match_ids"] = list(selected_section_ids)
                        record["metadata"] = record_metadata
                        scoped_records.append(record)
                for section_id in selected_section_ids:
                    if scoped_records:
                        break
                    try:
                        raw_records = vector_service.get_section_records(
                            route_document_id,
                            section_id,
                            limit=80,
                            chunk_type=None,
                        )
                    except Exception:
                        continue
                    for raw in raw_records or ():
                        record = _manual_record_from_raw(raw)
                        if not record:
                            continue
                        record_metadata = dict(record.get("metadata") or {})
                        if (
                            str(record_metadata.get("document_id") or "") != route_document_id
                            or str(record_metadata.get("parent_section_id") or "") != section_id
                        ):
                            continue
                        record_metadata[_STRUCTURAL_RECOVERY_LOOKUP_SOURCE] = "section_text_lookup"
                        record_metadata["section_match_ids"] = [section_id]
                        record["metadata"] = record_metadata
                        scoped_records.append(record)
            title_match_records = _manual_append_unique_records(title_match_records, scoped_records)
            title_match_section_ids.update(selected_section_ids)
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
    elif kind == "procedure":
        best_group, subflow_focused = _manual_focus_records_to_structured_action(
            best_group,
            metadata,
        )
        if subflow_focused:
            metadata["_deterministic_answer_procedure_action"] = str(
                (((metadata or {}).get("route_plan") or {}).get("query_contract") or {}).get("action")
                or ""
            ).strip()
    if kind == "procedure" or subflow_focused:
        if not (subflow_focused and not action):
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
    base_authorized = bool(
        route_plan.get("action") == RouteAction.GROUNDED_RETRIEVAL.value
        and isinstance(selected_document_id, str)
        and selected_document_id.strip()
    )
    if not base_authorized:
        return False
    if route_plan.get("entity_role") == "document_component":
        return True

    # A section clarification starts from an explicit device query, so the
    # semantic entity role can remain ``device_identity`` after the user picks
    # one option.  The selected section and its evidence boundary are still
    # server-owned and are a stronger authorization signal than that original
    # role.  Require the complete resolved scope so an unbounded device route
    # cannot promote reference-only evidence into answer evidence.
    selected_section_id = str(route_plan.get("selected_section_id") or "").strip()
    retrieval_scope = metadata.get("retrieval_scope")
    if not selected_section_id or not isinstance(retrieval_scope, Mapping):
        return False
    scope_document_id = str(retrieval_scope.get("document_id") or "").strip()
    allowed_section_ids = {
        str(value).strip()
        for value in retrieval_scope.get("allowed_section_ids") or []
        if str(value).strip()
    }
    allowed_evidence_refs = {
        str(value).strip()
        for value in retrieval_scope.get("allowed_evidence_refs") or []
        if str(value).strip()
    }
    return bool(
        scope_document_id == selected_document_id.strip()
        and selected_section_id in allowed_section_ids
        and allowed_evidence_refs
    )


def _manual_overrides_blocked_by_low_confidence(metadata: dict) -> bool:
    """低语义置信度不应屏蔽已授权的章节结构化恢复。"""
    return bool(
        metadata.get("low_confidence_retrieval")
        and not _route_plan_authorizes_structural_lookup(metadata)
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
        in {"section_text_lookup", "record_id_lookup"}
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
        def structural_support_text(record: dict) -> str:
            record_metadata = record.get("metadata") or {}
            return "\n".join(filter(None, (
                str(record_metadata.get("section_title") or ""),
                str(record_metadata.get("toc_path") or ""),
                str(record_metadata.get("procedure_action") or ""),
                str(record_metadata.get("procedure_target") or ""),
                str(record_metadata.get("assembly_context") or ""),
                str(record_metadata.get("orientation") or ""),
                str(record.get("content") or ""),
            )))

        route_direct_text = "\n".join(
            structural_support_text(record)
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
    graph_batch = metadata.get("graph_pre_retrieval")
    if (
        isinstance(graph_batch, Mapping)
        and str(graph_batch.get("status") or "") == "found"
        and any(
            isinstance(item, Mapping)
            and str(item.get("qualification") or "") == "qualified"
            for item in graph_batch.get("evidence") or []
        )
    ):
        # Graph diagnostics must compose the path relation with the manual
        # treatment.  The section renderer intentionally omits graph claims,
        # so keep the audited composed answer while still registering these
        # direct manual records for the final evidence plan.
        metadata["_manual_evidence_registered_for_graph_composition"] = True
        return None
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


_AUTHORIZATION_BINDING_FIELDS = (
    "source_chunk_id",
    "document_id",
    "page",
    "section_id",
    "source_section_id",
    "section_title",
    "source_section_title",
    "context_role",
    "role",
    "binding_confidence",
)


def _image_binding_ids(image: EvidenceImage) -> set[str]:
    flat_ids = {
        str(value).strip()
        for value in (
            image.step_id,
            *image.step_ids,
            *image.text_ids,
            *image.procedure_scope_ids,
        )
        if str(value).strip()
    }
    edge_ids = {
        str(binding.get("target_id") if isinstance(binding, Mapping) else binding.target_id).strip()
        for binding in image.bindings
        if str(binding.get("target_id") if isinstance(binding, Mapping) else binding.target_id).strip()
    }
    return flat_ids | edge_ids


def _binding_bundle_rank(image: EvidenceImage) -> tuple[int, float]:
    from services.retrieval.image_evidence_gate import (
        STRONG_BINDING_ROLES,
        normalize_binding_role,
    )

    has_binding_ids = bool(_image_binding_ids(image))
    complete_strong_binding = bool(
        normalize_binding_role(image.role) in STRONG_BINDING_ROLES
        and image.binding_confidence >= 0.8
        and has_binding_ids
    )
    completeness = 2 if complete_strong_binding else 1 if has_binding_ids else 0
    return completeness, float(image.binding_confidence or 0.0)


def _merge_duplicate_evidence_image(
    current: EvidenceImage,
    incoming: EvidenceImage,
) -> EvidenceImage:
    donor = (
        incoming
        if _binding_bundle_rank(incoming) > _binding_bundle_rank(current)
        else current
    )
    updates = {
        field: getattr(donor, field)
        for field in _AUTHORIZATION_BINDING_FIELDS
    }
    def ordered_union(*groups) -> list[str]:
        return list(dict.fromkeys(
            str(value).strip()
            for group in groups
            for value in group
            if str(value).strip()
        ))

    step_ids = ordered_union(current.step_ids, incoming.step_ids)
    text_ids = ordered_union(current.text_ids, incoming.text_ids)
    procedure_scope_ids = ordered_union(
        current.procedure_scope_ids,
        incoming.procedure_scope_ids,
    )
    bindings_by_key: dict[tuple[str, str, str], ImageEvidenceBinding] = {}
    for raw_binding in [*current.bindings, *incoming.bindings]:
        binding = (
            ImageEvidenceBinding.model_validate(raw_binding)
            if isinstance(raw_binding, Mapping)
            else raw_binding
        )
        key = (binding.target_id, binding.target_type, binding.relation)
        existing = bindings_by_key.get(key)
        if existing is None or binding.confidence > existing.confidence:
            bindings_by_key[key] = binding
    updates["step_ids"] = step_ids
    updates["step_id"] = step_ids[0] if step_ids else ""
    updates["text_ids"] = text_ids
    updates["procedure_scope_ids"] = procedure_scope_ids
    updates["binding_schema_version"] = max(
        current.binding_schema_version,
        incoming.binding_schema_version,
    )
    updates["bindings"] = list(bindings_by_key.values())
    caption_donor = max(
        (current, incoming),
        key=lambda item: (
            bool(str(item.caption or "").strip()),
            float(item.caption_confidence or 0.0),
        ),
    )
    updates["caption"] = caption_donor.caption
    updates["caption_confidence"] = caption_donor.caption_confidence
    updates["image_url"] = current.image_url or incoming.image_url
    updates["image_title"] = current.image_title or incoming.image_title
    updates["image_summary"] = current.image_summary or incoming.image_summary
    updates["aspect_id"] = current.aspect_id or incoming.aspect_id
    return current.model_copy(update=updates)


def _extract_evidence_images(metadata: dict) -> List[EvidenceImage]:
    images_by_key: dict[str, EvidenceImage] = {}

    for item in _iter_trace_result_items(metadata):
        item_meta = dict(item.get("metadata") or {})
        image_url = item_meta.get("image_url") or item_meta.get("imageUrl") or item.get("image_url")
        if not image_url:
            continue
        chunk_type = item_meta.get("chunk_type") or item_meta.get("source_chunk_type") or ""
        has_image_metadata = bool(item_meta.get("caption") or item_meta.get("image_title") or item_meta.get("image_name"))
        if chunk_type not in {"image", "image_summary"} and not has_image_metadata:
            continue

        record_id = str(item.get("id") or item.get("doc_id") or "")
        source_image_id = str(item_meta.get("source_image_id") or "")
        source_chunk_id = source_image_id or record_id
        image = EvidenceImage(
                image_url=image_url,
                caption=item_meta.get("caption") or item_meta.get("image_title") or item.get("content", ""),
                caption_confidence=float(item_meta.get("caption_confidence") or 0.0),
                image_title=str(item_meta.get("image_title") or ""),
                image_summary=str(item_meta.get("image_summary") or ""),
                page=item_meta.get("page_number") or item_meta.get("page"),
                section_id=str(item_meta.get("section_id") or item_meta.get("parent_section_id") or ""),
                source_section_id=str(
                    item_meta.get("source_section_id")
                    or item_meta.get("section_id")
                    or item_meta.get("parent_section_id")
                    or ""
                ),
                section_title=item_meta.get("section_title", ""),
                source_section_title=str(
                    item_meta.get("source_section_title")
                    or item_meta.get("section_title")
                    or ""
                ),
                document_id=item_meta.get("document_id", ""),
                source_chunk_id=source_chunk_id,
                context_role=item_meta.get("context_role", ""),
                step_id=str((item_meta.get("related_step_chunk_ids") or [""])[0] or ""),
                step_ids=[str(item) for item in item_meta.get("related_step_chunk_ids") or [] if str(item)],
                text_ids=[str(item) for item in item_meta.get("related_text_chunk_ids") or [] if str(item)],
                procedure_scope_ids=[
                    str(item) for item in item_meta.get("procedure_scope_ids") or [] if str(item)
                ],
                aspect_id=str((item_meta.get("aspect_ids") or [""])[0] or ""),
                role=str(item_meta.get("binding_role") or item_meta.get("context_role") or ""),
                binding_confidence=float(item_meta.get("binding_confidence") or 0.0),
                binding_schema_version=int(item_meta.get("image_binding_schema_version") or 0),
                bindings=[
                    ImageEvidenceBinding.model_validate(binding)
                    for binding in item_meta.get("image_bindings") or []
                    if isinstance(binding, Mapping)
                ],
        )
        entity_key = source_chunk_id or str(image_url)
        current = images_by_key.get(entity_key)
        if current is None and image_url:
            current_key = next(
                (
                    key for key, candidate in images_by_key.items()
                    if candidate.image_url == image_url
                ),
                "",
            )
            if current_key:
                current = images_by_key.pop(current_key)
        images_by_key[entity_key] = (
            _merge_duplicate_evidence_image(current, image)
            if current
            else image
        )
    return list(images_by_key.values())


async def _collect_direct_section_table_items(message: str, metadata: dict) -> list[dict]:
    """清单直取通道：按确定性章节补全同节全部表格，解决跨页 BOM 只召回一页的问题。"""
    if _is_fault_diagnosis_route(metadata):
        return []
    if not (
        _is_inventory_table_query(message)
        or _structured_contract_requests_table_lookup(metadata)
    ):
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
        route_section_id = str(route_plan.get("selected_section_id") or "").strip()
        retrieval_scope = (metadata or {}).get("retrieval_scope") or {}
        resolved_section_ids = [
            str(value).strip()
            for value in retrieval_scope.get("allowed_section_ids") or []
            if str(value).strip()
        ] if isinstance(retrieval_scope, Mapping) else []
        structured_parameter_lookup = _structured_contract_requests_table_lookup(metadata)
        document_id = route_document_id
        query_contract = route_plan.get("query_contract") or {}
        query_component = str(query_contract.get("component") or "").strip()
        query_action = str(query_contract.get("action") or "").strip()
        query_part_specs = _structured_inventory_part_specs(metadata)

        def append_unique(values: List[str], value: str) -> None:
            if value and value not in values:
                values.append(value)

        append_unique(section_match_ids, route_section_id)

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
                if structured_parameter_lookup and hasattr(section_index, "find_evidence"):
                    contract = QueryContract.from_mapping(
                        query_contract,
                        raw_query=message,
                    )
                    for ref in section_index.find_evidence(contract):
                        ref_document_id = str(getattr(ref, "document_id", "") or "")
                        if ref_document_id != route_document_id:
                            continue
                        ref_section_id = str(getattr(ref, "section_id", "") or "")
                        if not ref_section_id:
                            continue
                        append_unique(title_section_ids, ref_section_id)
                        append_unique(section_match_ids, ref_section_id)
                        ref_title = f"{getattr(ref, 'core_title', '')} {getattr(ref, 'full_title', '')}".strip()
                        if ref_title:
                            section_titles_by_id.setdefault(ref_section_id, ref_title)
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

        if plan_intent not in ("outline", "procedure") and not structured_parameter_lookup:
            return []

        target_section_ids: List[str] = []
        if structured_parameter_lookup:
            for sid in title_section_ids:
                append_unique(target_section_ids, sid)
            append_unique(target_section_ids, route_section_id)
        else:
            append_unique(target_section_ids, route_section_id)
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
        if resolved_section_ids:
            resolved_set = set(resolved_section_ids)
            target_section_ids = [
                sid for sid in target_section_ids if sid in resolved_set
            ]
            for sid in resolved_section_ids:
                append_unique(target_section_ids, sid)
        if not document_id or not target_section_ids:
            return []

        if vector_service is None:
            from services.knowledge.vector_service import get_vector_service
            vector_service = get_vector_service()

        table_items: list[dict] = []
        seen_ids: set[str] = set()
        authorized_records_by_section: dict[str, list[dict]] | None = None

        def authorized_table_records() -> dict[str, list[dict]]:
            nonlocal authorized_records_by_section
            if authorized_records_by_section is not None:
                return authorized_records_by_section
            authorized_records_by_section = {}
            if not isinstance(retrieval_scope, Mapping):
                return authorized_records_by_section
            record_ids = [
                str(value).strip()
                for value in retrieval_scope.get("allowed_evidence_refs") or []
                if str(value).strip()
            ]
            get_records = getattr(vector_service, "get_vector_records", None)
            if not record_ids or not callable(get_records):
                return authorized_records_by_section
            try:
                records = get_records(record_ids)
            except Exception:
                return authorized_records_by_section
            allowed_sections = set(resolved_section_ids or target_section_ids)
            for raw_record in records or []:
                record = (
                    raw_record.model_dump()
                    if hasattr(raw_record, "model_dump")
                    else dict(raw_record)
                )
                meta = dict(record.get("metadata") or {})
                record_document_id = str(meta.get("document_id") or "").strip()
                record_section_id = str(meta.get("parent_section_id") or "").strip()
                chunk_type = str(meta.get("chunk_type") or meta.get("source_chunk_type") or "")
                if (
                    record_document_id != route_document_id
                    or record_section_id not in allowed_sections
                    or chunk_type != "table"
                ):
                    continue
                record_id = str(record.get("id") or record.get("doc_id") or "").strip()
                if record_id:
                    record["id"] = record_id
                record.setdefault("content", record.get("text") or "")
                authorized_records_by_section.setdefault(record_section_id, []).append(record)
            return authorized_records_by_section

        for sid in target_section_ids[:3]:
            try:
                records = vector_service.get_section_records(
                    document_id, sid, limit=200, chunk_type="table",
                )
            except Exception:
                records = []
            if not records:
                records = authorized_table_records().get(sid, [])
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
                compact_specs = tuple(
                    _compact_inventory_text(spec) for spec in query_part_specs
                    if _compact_inventory_text(spec)
                )
                if compact_specs and not any(spec in compact_support for spec in compact_specs):
                    continue
                if not compact_specs and compact_component and compact_component not in compact_support:
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
                raise
            for rec in records:
                rec = rec.model_dump() if hasattr(rec, "model_dump") else dict(rec)
                meta = dict(rec.get("metadata") or {})
                image_chunk_id = str(rec.get("id") or rec.get("doc_id") or "")
                if image_chunk_id and ":img:" in image_chunk_id:
                    summary_record = vector_service.get_vector_record(
                        image_chunk_id.replace(":img:", ":ims:", 1)
                    )
                    summary_meta = dict((summary_record or {}).get("metadata") or {})
                    if (
                        summary_meta.get("chunk_type") == "image_summary"
                        and str(summary_meta.get("source_image_id") or "") == image_chunk_id
                    ):
                        meta["image_title"] = str(summary_meta.get("image_title") or "")
                        meta["image_summary"] = str(
                            summary_meta.get("image_summary")
                            or (summary_record or {}).get("text")
                            or ""
                        )
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
                meta.setdefault("chunk_id", image_chunk_id)
                rec["metadata"] = meta
                rec.setdefault("content", meta.get("caption") or meta.get("image_title") or "")
                images.append(EvidenceImage(
                    image_url=image_url,
                    caption=meta.get("caption") or meta.get("image_title") or "",
                    caption_confidence=float(meta.get("caption_confidence") or 0.0),
                    image_title=str(meta.get("image_title") or ""),
                    image_summary=str(meta.get("image_summary") or ""),
                    page=meta.get("page_number") or meta.get("page"),
                    section_id=str(meta.get("section_id") or meta.get("parent_section_id") or ""),
                    source_section_id=str(
                        meta.get("source_section_id")
                        or meta.get("section_id")
                        or meta.get("parent_section_id")
                        or ""
                    ),
                    section_title=meta.get("section_title", ""),
                    source_section_title=str(
                        meta.get("source_section_title")
                        or meta.get("section_title")
                        or ""
                    ),
                    document_id=meta.get("document_id", ""),
                    source_chunk_id=image_chunk_id,
                    context_role=str(meta.get("context_role") or "direct_lookup"),
                    step_id=str((meta.get("related_step_chunk_ids") or [""])[0] or ""),
                    step_ids=[str(value) for value in meta.get("related_step_chunk_ids") or [] if str(value)],
                    text_ids=[str(value) for value in meta.get("related_text_chunk_ids") or [] if str(value)],
                    procedure_scope_ids=[
                        str(value) for value in meta.get("procedure_scope_ids") or [] if str(value)
                    ],
                    role=str(meta.get("binding_role") or "direct_lookup"),
                    binding_confidence=float(meta.get("binding_confidence") or 0.0),
                ))
        return images
    except Exception:
        raise


def _merge_evidence_images(
    existing: List[EvidenceImage], direct: List[EvidenceImage],
) -> List[EvidenceImage]:
    """Merge direct and traced images while preserving image-local semantics."""
    merged_by_key: dict[str, EvidenceImage] = {}
    order: list[str] = []
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
        key = img.image_url or img.source_chunk_id or f"image:{len(order)}"
        if key not in merged_by_key:
            merged_by_key[key] = img
            order.append(key)
        else:
            merged_by_key[key] = _merge_duplicate_evidence_image(merged_by_key[key], img)
    return [merged_by_key[key] for key in order]


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
    meta = dict(record.get("metadata") or {})
    # Page OCR describes every figure on the page and therefore cannot identify
    # one image.  Only image-local fields are eligible for image-level matching.
    caption = str(meta.get("caption") or "").strip()
    caption_confidence = float(meta.get("caption_confidence") or 0.0)
    local_values = [meta.get("image_title"), meta.get("image_summary")]
    if caption_confidence >= 0.8:
        local_values.append(caption)
    target = _compact_inventory_text(" ".join(str(value or "") for value in local_values)).lower()
    if not target:
        return False

    anchors = [
        _compact_inventory_text(value).lower()
        for value in _manual_query_anchor_terms(message)
        if len(_compact_inventory_text(value)) >= 2
    ]
    if not anchors:
        try:
            from services.retrieval.query_understanding import understand_query

            understood = understand_query(message)
            query_target = _compact_inventory_text(understood.target_query).lower()
        except Exception:
            query_target = ""
        if len(query_target) >= 2:
            anchors = [query_target]
    if not anchors:
        terms = _image_query_terms(message)
        anchors = [term for term in terms if len(term) >= 3][:1]
    if not anchors:
        return False
    return any(anchor in target for anchor in anchors)


def _evidence_image_matches_query_anchor(message: str, image: EvidenceImage) -> bool:
    local_values = [image.image_title, image.image_summary]
    if image.caption_confidence >= 0.8:
        local_values.append(image.caption)
    target = _compact_inventory_text(
        " ".join(str(value or "") for value in local_values)
    ).lower()
    if not target:
        return False
    specific_anchors = _image_specific_anchor_terms(message)
    if specific_anchors:
        return any(anchor in target for anchor in specific_anchors)
    anchors = [
        anchor for anchor in _manual_query_anchor_terms(message)
        if re.sub(
            r"[^\u4e00-\u9fffa-z0-9]+", "", _compact_inventory_text(anchor).lower(),
        ) not in {
            "图片", "插图", "图示", "示意图", "结构图", "位置图", "图纸", "配图",
        }
    ]
    if anchors:
        return any(anchor.lower() in target for anchor in anchors)
    try:
        from services.retrieval.query_understanding import understand_query

        understood_target = _compact_inventory_text(
            understand_query(message).target_query
        ).lower()
    except Exception:
        understood_target = ""
    understood_target = re.sub(r"^(?:请|帮我|麻烦)", "", understood_target)
    if len(understood_target) >= 2 and understood_target in target:
        return True
    return _page_image_matches_query(
        message,
        {
            "content": image.image_summary or image.image_title or image.caption or "",
            "metadata": {
                "caption": image.caption or "",
                "caption_confidence": image.caption_confidence,
                "image_title": image.image_title,
                "image_summary": image.image_summary,
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
    """Reject cross-section rebinding based only on shared page OCR.

    Page OCR may contain the tail of one section and the beginning of another,
    so it cannot prove that a particular image belongs to the answer section.
    Import-time image/text bindings are the safe recovery path.
    """
    return False


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
    if not sorted_images:
        return []
    target_section_ids = set(_final_answer_section_ids(metadata))
    target_title = _final_answer_section_title(metadata)
    target_source_ids = _final_answer_non_image_source_ids(metadata)
    direct_image_ids = _final_answer_direct_image_source_ids(metadata)
    target_scope_ids = _final_answer_procedure_scope_ids(metadata)
    query = str(
        (metadata or {}).get("resolved_image_query")
        or (metadata or {}).get("original_user_message")
        or (metadata or {}).get("user_message")
        or ""
    )
    matched: list[EvidenceImage] = []
    for image in sorted_images:
        image_source_id = str(image.source_chunk_id or "").strip()
        image_section_ids = {
            str(value or "").strip()
            for value in (image.section_id, image.source_section_id)
            if str(value or "").strip()
        }
        binding_ids = {
            str(value).strip()
            for value in (image.step_id, *image.step_ids, *image.text_ids)
            if str(value).strip()
        }
        scope_ids = {
            str(value).strip()
            for value in image.procedure_scope_ids
            if str(value).strip()
        }
        if (
            (image.context_role or image.role) == "page_render"
            and _query_allows_rendered_page_fallback(query)
        ):
            matched.append(image)
        elif image_source_id and image_source_id in direct_image_ids:
            matched.append(image)
        elif target_section_ids.intersection(image_section_ids):
            matched.append(image)
        elif target_source_ids.intersection(binding_ids):
            matched.append(image)
        elif target_scope_ids.intersection(scope_ids):
            matched.append(image)
        elif target_title and _image_matches_target_section(image, target_title):
            matched.append(image)
    return matched


def _deterministic_document_ids(metadata: dict) -> list[str]:
    return _final_answer_document_ids(metadata)


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

    # Explicit page requests can be valid even when text retrieval is empty,
    # so there may be no trace item from which to recover the PDF source.  The
    # route's single selected document is the authoritative fallback; it does
    # not authorize any image by itself and is only used to locate the source.
    route_plan = metadata.get("route_plan") if isinstance(metadata, dict) else None
    selected_document_id = str(
        route_plan.get("selected_document_id")
        if isinstance(route_plan, Mapping)
        else ""
    ).strip()
    if (
        route_plan
        and route_plan.get("action") == RouteAction.GROUNDED_RETRIEVAL.value
        and selected_document_id
        and not hints.get(selected_document_id)
    ):
        vector_service = _initialized_or_injected_vector_service()
        try:
            manifest = (
                vector_service.get_document_manifest(selected_document_id)
                if vector_service is not None
                else {}
            )
        except Exception:
            manifest = {}
        if isinstance(manifest, Mapping):
            fallback = {
                key: str(manifest.get(key) or "").strip()
                for key in ("source_file_url", "file_name", "local_path")
                if str(manifest.get(key) or "").strip()
            }
            if fallback:
                hints[selected_document_id] = fallback
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
    if len(document_ids) != 1:
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
        raise

    images: List[EvidenceImage] = []
    seen_urls: set[str] = set()
    explicit_page_render = _query_allows_rendered_page_fallback(query)
    for document_id in document_ids:
        for page in pages[:8]:
            if explicit_page_render:
                rendered = _render_evidence_pdf_page_image(metadata, document_id, page)
                if rendered and rendered.image_url not in seen_urls:
                    seen_urls.add(rendered.image_url)
                    images.append(rendered)
                continue
            try:
                records = vector_service.get_page_records(
                    document_id,
                    page,
                    chunk_type="image",
                    limit=20,
                )
            except Exception:
                raise
            page_had_indexed_image = False
            page_had_matched_image = False
            for rec in records:
                page_had_indexed_image = True
                rec = rec.model_dump() if hasattr(rec, "model_dump") else dict(rec)
                meta = dict(rec.get("metadata") or {})
                image_url = meta.get("image_url") or rec.get("image_url")
                if not image_url or image_url in seen_urls:
                    continue
                chunk_id = str(rec.get("id") or rec.get("doc_id") or "")
                candidate = EvidenceImage(
                    image_url=image_url,
                    caption=meta.get("caption") or meta.get("image_title") or rec.get("content", ""),
                    caption_confidence=float(meta.get("caption_confidence") or 0.0),
                    image_title=str(meta.get("image_title") or ""),
                    image_summary=str(meta.get("image_summary") or ""),
                    page=meta.get("page_number") or meta.get("page"),
                    section_id=str(meta.get("section_id") or meta.get("parent_section_id") or ""),
                    source_section_id=str(
                        meta.get("source_section_id")
                        or meta.get("section_id")
                        or meta.get("parent_section_id")
                        or ""
                    ),
                    section_title=meta.get("section_title", ""),
                    source_section_title=str(
                        meta.get("source_section_title")
                        or meta.get("section_title")
                        or ""
                    ),
                    document_id=meta.get("document_id", ""),
                    source_chunk_id=chunk_id,
                    context_role="page_lookup",
                    step_id=str((meta.get("related_step_chunk_ids") or [""])[0] or ""),
                    step_ids=[
                        str(value) for value in meta.get("related_step_chunk_ids") or [] if str(value)
                    ],
                    text_ids=[
                        str(value) for value in meta.get("related_text_chunk_ids") or [] if str(value)
                    ],
                    procedure_scope_ids=[
                        str(value) for value in meta.get("procedure_scope_ids") or [] if str(value)
                    ],
                    role=str(meta.get("binding_role") or "page_lookup"),
                    binding_confidence=float(meta.get("binding_confidence") or 0.0),
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
                    continue
                page_had_matched_image = True
                seen_urls.add(image_url)
                meta.setdefault("chunk_id", chunk_id)
                rec["metadata"] = meta
                rec.setdefault("content", meta.get("caption") or meta.get("image_title") or "")
                images.append(candidate)
            if (
                _query_allows_rendered_page_fallback(query)
                and (not page_had_indexed_image or not page_had_matched_image)
            ):
                rendered = _render_evidence_pdf_page_image(metadata, document_id, page)
                if rendered and rendered.image_url not in seen_urls:
                    seen_urls.add(rendered.image_url)
                    images.append(rendered)
    return _sort_unique_evidence_images(images)


def _text_evidence_pages(metadata: dict) -> list[int]:
    """Return only pages authorized by final claim evidence."""
    return _final_answer_evidence_pages(metadata)


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
    # A complete-procedure request intentionally needs every audited page.
    # The lexical narrowing below is useful for a single visual target, but
    # it used to collapse a multi-page installation/removal answer to the one
    # page with the strongest action score (for example, piston/cylinder
    # assembly).  Keep the deterministic page set intact unless the caller
    # explicitly asks for one target page.
    complete_procedure_request = any(
        phrase in query
        for phrase in ("完整步骤", "完整流程", "全部步骤", "所有步骤", "完整的步骤")
    )
    if complete_procedure_request and not force:
        return sorted_images
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


def _query_allows_rendered_page_fallback(query: str) -> bool:
    compact_query = _compact_inventory_text(query)
    explicit_page_terms = (
        "整页", "页面截图", "整页截图", "原页", "原文页", "查看第", "显示第",
    )
    has_page_number = bool(re.search(r"第?\s*\d+\s*页", query or ""))
    return any(term in compact_query for term in explicit_page_terms) and has_page_number


def _image_local_query_overlap_score(message: str, image: EvidenceImage) -> float:
    """Score a named visual sub-target against image-local text only."""
    local_values = [image.image_title, image.image_summary]
    if image.caption_confidence >= 0.8:
        local_values.append(image.caption)
    targets = [
        _compact_inventory_text(str(value or "")).lower()
        for value in local_values
        if str(value or "").strip()
    ]
    anchors = [
        *(_image_specific_anchor_terms(message)),
        *(_manual_query_anchor_terms(message)),
    ]
    best = 0.0
    for raw_anchor in anchors:
        anchor = _compact_inventory_text(raw_anchor).lower()
        if len(anchor) < 2:
            continue
        anchor_pairs = {anchor[index:index + 2] for index in range(len(anchor) - 1)}
        for target in targets:
            if anchor in target:
                best = max(best, 1.0)
                continue
            if len(anchor_pairs) < 2:
                continue
            target_pairs = {target[index:index + 2] for index in range(len(target) - 1)}
            coverage = len(anchor_pairs.intersection(target_pairs)) / len(anchor_pairs)
            if coverage >= 0.5:
                best = max(best, coverage)
    return best


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
        and isinstance(selected_document_id, str)
        and selected_document_id.strip()
        and images
    ):
        return False
    image_document_ids = {
        str(image.document_id or "").strip()
        for image in images
    }
    if image_document_ids != {selected_document_id.strip()}:
        return False
    query = str(
        metadata.get("resolved_image_query")
        or metadata.get("original_user_message")
        or metadata.get("user_message")
        or ""
    )
    if _query_allows_rendered_page_fallback(query):
        requested_pages = set(_final_answer_evidence_pages(metadata))
        return bool(
            len(requested_pages) == 1
            and all(
                (image.context_role or image.role) == "page_render"
                and _evidence_image_page(image) in requested_pages
                for image in images
            )
        )
    return route_plan.get("entity_role") == "document_component"


_IMAGE_CHUNK_TYPES = frozenset({"image", "image_summary"})


def _claim_bound_manual_entries(metadata: dict) -> list[dict]:
    bound_evidence_ids = {
        str(evidence_id).strip()
        for binding in (metadata or {}).get("authorized_claim_evidence_bindings") or ()
        if isinstance(binding, Mapping)
        for evidence_id in binding.get("evidence_ids") or ()
        if str(evidence_id).strip()
    }
    return [
        entry
        for entry in EvidenceLedger.from_react_trace(metadata or {}).entries
        if str(entry.get("evidence_id") or "") in bound_evidence_ids
        and str(entry.get("source_type") or "") == "manual"
    ]


def _ledger_source_id(entry: Mapping[str, Any]) -> str:
    source = entry.get("source") if isinstance(entry.get("source"), Mapping) else {}
    return str(source.get("source_chunk_id") or source.get("chunk_id") or "").strip()


def _final_answer_direct_image_source_ids(metadata: dict) -> set[str]:
    return {
        _ledger_source_id(entry)
        for entry in _claim_bound_manual_entries(metadata)
        if str((entry.get("source") or {}).get("chunk_type") or "") in _IMAGE_CHUNK_TYPES
        and _ledger_source_id(entry)
    }


def _final_answer_non_image_source_ids(metadata: dict) -> set[str]:
    claim_bound = {
        _ledger_source_id(entry)
        for entry in _claim_bound_manual_entries(metadata)
        if str((entry.get("source") or {}).get("chunk_type") or "") not in _IMAGE_CHUNK_TYPES
        and _ledger_source_id(entry)
    }
    inherited = {
        str(value).strip()
        for value in (metadata or {}).get("inherited_non_image_source_ids") or ()
        if (metadata or {}).get("image_followup_inherited") is True
        and str(value).strip()
    }
    return claim_bound | inherited


def _final_answer_document_ids(metadata: dict) -> list[str]:
    values = [
        str((entry.get("source") or {}).get("document_id") or "").strip()
        for entry in _claim_bound_manual_entries(metadata)
    ]
    if (metadata or {}).get("image_followup_inherited") is True:
        values.extend(
            str(value).strip()
            for value in (metadata or {}).get("inherited_document_ids") or ()
        )
    query = str(
        (metadata or {}).get("resolved_image_query")
        or (metadata or {}).get("original_user_message")
        or (metadata or {}).get("user_message")
        or ""
    )
    route = (metadata or {}).get("route_plan")
    route = route if isinstance(route, Mapping) else {}
    if (
        _query_allows_rendered_page_fallback(query)
        and route.get("action") == RouteAction.GROUNDED_RETRIEVAL.value
    ):
        values.append(str(route.get("selected_document_id") or "").strip())
    return list(dict.fromkeys(value for value in values if value))


def _final_answer_evidence_pages(metadata: dict) -> list[int]:
    pages: list[int] = []

    def append(value: object) -> None:
        try:
            page = int(value)
        except (TypeError, ValueError):
            return
        if page > 0 and page not in pages:
            pages.append(page)

    for entry in _claim_bound_manual_entries(metadata):
        append((entry.get("source") or {}).get("page"))
    if (metadata or {}).get("image_followup_inherited") is True:
        for value in (metadata or {}).get("inherited_evidence_pages") or ():
            append(value)
    query = str(
        (metadata or {}).get("resolved_image_query")
        or (metadata or {}).get("original_user_message")
        or (metadata or {}).get("user_message")
        or ""
    )
    if _query_allows_rendered_page_fallback(query):
        for value in re.findall(r"第\s*(\d+)\s*页", query):
            append(value)
    return pages


def _final_answer_section_ids(metadata: dict) -> list[str]:
    values: list[str] = []

    def append(value: object) -> None:
        normalized = str(value or "").strip()
        if normalized and normalized not in values:
            values.append(normalized)

    entries = _claim_bound_manual_entries(metadata)
    claim_source_ids = {
        _ledger_source_id(entry)
        for entry in entries
        if _ledger_source_id(entry)
    }
    for entry in entries:
        source = entry.get("source") if isinstance(entry.get("source"), Mapping) else {}
        append(source.get("section_id") or source.get("parent_section_id"))
    for item in _iter_trace_result_items(metadata):
        if _trace_item_source_id(item) not in claim_source_ids:
            continue
        item_meta = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
        append(item_meta.get("section_id") or item_meta.get("parent_section_id"))
    if (metadata or {}).get("image_followup_inherited") is True:
        for value in (metadata or {}).get("inherited_section_ids") or ():
            append(value)
    return values


def _final_answer_section_title(metadata: dict) -> str:
    target_ids = (
        _final_answer_non_image_source_ids(metadata)
        | _final_answer_direct_image_source_ids(metadata)
    )
    titles: list[str] = []
    for item in _iter_trace_result_items(metadata):
        if _trace_item_source_id(item) not in target_ids:
            continue
        item_meta = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
        title = str(item_meta.get("section_title") or "").strip()
        if title and title not in titles:
            titles.append(title)
    inherited_title = str((metadata or {}).get("inherited_section_title") or "").strip()
    if (
        (metadata or {}).get("image_followup_inherited") is True
        and inherited_title
        and inherited_title not in titles
    ):
        titles.append(inherited_title)
    return titles[0] if len(titles) == 1 else ""


def _trace_item_source_id(item: Mapping[str, Any]) -> str:
    item_meta = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
    return str(
        item_meta.get("chunk_id")
        or item.get("chunk_id")
        or item.get("id")
        or item.get("doc_id")
        or ""
    ).strip()


def _final_answer_procedure_scope_ids(metadata: dict) -> set[str]:
    target_ids = _final_answer_non_image_source_ids(metadata)
    scopes: set[str] = set()
    for item in _iter_trace_result_items(metadata):
        item_meta = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
        if _trace_item_source_id(item) not in target_ids:
            continue
        scopes.update(
            str(value).strip()
            for value in (
                item_meta.get("procedure_scope_ids")
                or [item_meta.get("procedure_scope_id")]
            )
            if str(value or "").strip()
        )
    if (metadata or {}).get("image_followup_inherited") is True:
        scopes.update(
            str(value).strip()
            for value in (metadata or {}).get("inherited_procedure_scope_ids") or ()
            if str(value).strip()
        )
    return scopes


def _target_procedure_scope_ids(metadata: dict) -> set[str]:
    return _final_answer_procedure_scope_ids(metadata)


def _final_answer_has_procedure_evidence(metadata: dict) -> bool:
    if _final_answer_procedure_scope_ids(metadata):
        return True
    target_ids = _final_answer_non_image_source_ids(metadata)
    if not target_ids:
        return False
    for item in _iter_trace_result_items(metadata):
        item_meta = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
        chunk_type = str(
            item_meta.get("chunk_type")
            or item_meta.get("source_chunk_type")
            or ""
        )
        chunk_label = str(item_meta.get("chunk_label") or "")
        answer_role = str(item_meta.get("answer_role") or "")
        if chunk_type in _IMAGE_CHUNK_TYPES:
            continue
        if _trace_item_source_id(item) not in target_ids:
            continue
        if (
            chunk_type in {"step", "step_raw", "procedure", "procedure_step"}
            or chunk_label in {"step", "procedure_step"}
            or answer_role in {"step", "procedure", "procedure_step"}
        ):
            return True
    return False


def _response_needs_images(query: str, metadata: dict) -> bool:
    from services.retrieval.query_understanding import (
        has_negative_image_request,
        understand_query,
    )

    if has_negative_image_request(query):
        return False
    if _query_allows_rendered_page_fallback(query):
        return True
    if understand_query(query).intent == "image_lookup":
        return True
    response_audit = (metadata or {}).get("response_audit")
    response_audit = response_audit if isinstance(response_audit, Mapping) else {}
    return bool(
        _manual_query_kind(query) == "procedure"
        and response_audit.get("passed") is True
        and _final_answer_has_procedure_evidence(metadata)
    )


def _complete_inventory_requests_section_overview(query: str, metadata: dict) -> bool:
    """Return whether the user requested the complete inventory section.

    A lookup for one row or field remains text-only.  The final answer section
    must already be known so the query cannot authorize images by page alone.
    """
    return bool(
        _is_inventory_table_query(query)
        and not _inventory_query_requests_specific_rows(query)
        and not _structured_contract_requests_table_lookup(metadata)
        and (
            _final_answer_section_ids(metadata)
            or _final_answer_section_title(metadata)
        )
    )


def _exact_target_section_image_source_ids(
    images: List[EvidenceImage],
    metadata: dict,
) -> set[str]:
    """Collect real image IDs structurally bound to the final answer section."""
    target_section_ids = set(_final_answer_section_ids(metadata))
    target_title = _final_answer_section_title(metadata)
    target_document_ids = set(_final_answer_document_ids(metadata))
    matched: set[str] = set()
    for image in images:
        if (image.context_role or image.role) == "page_render":
            continue
        if target_document_ids and image.document_id not in target_document_ids:
            continue
        source_id = str(image.source_chunk_id or "").strip()
        if not source_id:
            continue
        image_section_ids = {
            str(value or "").strip()
            for value in (image.section_id, image.source_section_id)
            if str(value or "").strip()
        }
        if target_section_ids.intersection(image_section_ids):
            matched.add(source_id)
        elif target_title and _image_matches_target_section(image, target_title):
            matched.add(source_id)
    return matched


def _apply_inherited_image_evidence(
    metadata: dict,
    input_context: Mapping[str, object],
) -> None:
    inherited = input_context.get("inherited_image_evidence")
    if not isinstance(inherited, Mapping):
        return

    def values(*groups) -> set[str]:
        return {
            str(value).strip()
            for group in groups
            for value in (group or ())
            if str(value).strip()
        }

    def fill_if_empty(key: str, value) -> None:
        if value and not metadata.get(key):
            metadata[key] = value

    inherited_document_id = str(inherited.get("document_id") or "").strip()
    inherited_device_type = str(inherited.get("device_type") or "").strip()
    inherited_section_id = str(inherited.get("section_id") or "").strip()
    inherited_source_ids = values(inherited.get("source_chunk_ids"))
    inherited_scope_ids = values(inherited.get("procedure_scope_ids"))
    inherited_pages = {
        int(page)
        for page in inherited.get("evidence_pages") or ()
        if str(page).isdigit()
    }
    if not inherited_document_id:
        metadata["image_followup_inherited"] = False
        metadata["image_followup_context_conflict"] = True
        metadata["image_followup_context_conflict_fields"] = ["document"]
        return

    current_document_ids = values(_deterministic_document_ids(metadata))
    current_section_ids = values(metadata.get("_deterministic_answer_section_ids"))
    current_source_ids = values(_final_answer_non_image_source_ids(metadata))
    current_scope_ids = values(_final_answer_procedure_scope_ids(metadata))
    current_pages = {
        int(page)
        for page in _text_evidence_pages(metadata)
        if str(page).isdigit()
    }
    current_device_type = str(metadata.get("device_type") or "").strip()

    conflicts: list[str] = []
    if current_document_ids and current_document_ids != {inherited_document_id}:
        conflicts.append("document")
    if inherited_section_id and current_section_ids and inherited_section_id not in current_section_ids:
        conflicts.append("section")
    if inherited_source_ids and current_source_ids and inherited_source_ids.isdisjoint(current_source_ids):
        conflicts.append("source")
    if inherited_scope_ids and current_scope_ids and inherited_scope_ids.isdisjoint(current_scope_ids):
        conflicts.append("procedure_scope")
    if inherited_pages and current_pages and inherited_pages.isdisjoint(current_pages):
        conflicts.append("page")
    if inherited_device_type and current_device_type and inherited_device_type != current_device_type:
        conflicts.append("device_type")
    if conflicts:
        metadata["image_followup_context_conflict"] = True
        metadata["image_followup_context_conflict_fields"] = conflicts
        metadata["image_followup_inherited"] = False
        return

    fill_if_empty("resolved_image_query", str(input_context.get("resolved_image_query") or "").strip())
    fill_if_empty("image_followup_base_query", str(input_context.get("image_followup_base_query") or "").strip())
    fill_if_empty("allowed_document_ids", [inherited_document_id])
    fill_if_empty("_deterministic_answer_document_ids", [inherited_document_id])
    fill_if_empty("allowed_source_chunk_ids", sorted(inherited_source_ids))
    fill_if_empty("allowed_evidence_pages", sorted(inherited_pages))
    fill_if_empty("_deterministic_answer_evidence_pages", sorted(inherited_pages))
    if inherited_section_id:
        fill_if_empty("_deterministic_answer_section_ids", [inherited_section_id])
    section_title = str(inherited.get("section_title") or "").strip()
    if section_title:
        fill_if_empty("_deterministic_answer_section_title", section_title)
    fill_if_empty("procedure_scope_ids", sorted(inherited_scope_ids))
    metadata["inherited_document_ids"] = [inherited_document_id]
    metadata["inherited_evidence_pages"] = sorted(inherited_pages)
    metadata["inherited_non_image_source_ids"] = sorted(inherited_source_ids)
    metadata["inherited_procedure_scope_ids"] = sorted(inherited_scope_ids)
    if len(inherited_scope_ids) == 1:
        fill_if_empty("_deterministic_answer_procedure_scope_id", next(iter(inherited_scope_ids)))
    metadata["image_followup_inherited"] = True


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
    from services.retrieval.image_evidence_gate import (
        ImageEvidenceContext,
        authorize_images,
        has_strong_answer_binding,
    )
    from services.retrieval.query_understanding import (
        has_negative_image_request,
        understand_query,
    )

    policy = metadata.get("response_policy") if isinstance(metadata.get("response_policy"), dict) else {}
    query = str(
        metadata.get("resolved_image_query")
        or metadata.get("original_user_message")
        or metadata.get("user_message")
        or metadata.get("message")
        or ""
    )
    target_pages = [
        int(page)
        for page in _final_answer_evidence_pages(metadata)
        if str(page).isdigit()
    ]
    target_pages = list(dict.fromkeys(target_pages))
    structured_contract = _structured_query_contract(metadata)
    has_structured_visual_focus = bool(
        str(structured_contract.get("orientation") or "").strip()
    )
    configured_mode = str(metadata.get("query_understanding_selection_mode") or "")
    understood_query = understand_query(query)
    if not configured_mode and understood_query.intent == "image_lookup":
        configured_mode = understood_query.selection_mode
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
    inventory_section_overview = _complete_inventory_requests_section_overview(
        query,
        metadata,
    )
    needs_images = (
        _response_needs_images(query, metadata)
        or has_structured_visual_focus
        or inventory_section_overview
    )
    if (
        (policy and policy.get("images_allowed") is False and not route_scoped_visual_evidence_allowed)
        or has_negative_image_request(query)
    ):
        mode = "none"
    elif _query_allows_rendered_page_fallback(query):
        mode = "single_target"
    elif has_structured_visual_focus:
        mode = "single_target"
    elif inventory_section_overview:
        mode = "section_overview"
    elif needs_images and configured_mode in {"single_target", "evidence_pages", "section_overview"}:
        mode = configured_mode
    elif needs_images and _query_explicit_single_page_intent(query):
        mode = "single_target"
    elif needs_images and _manual_query_kind(query) == "procedure":
        mode = "evidence_pages"
    else:
        mode = "none"

    excluded_pages = [
        int(value)
        for value in re.findall(r"(?:不要|排除|不含|去掉|别用)[^。；，,]{0,12}?第?\s*(\d+)\s*页", query)
    ]
    mentioned_pages = [int(value) for value in re.findall(r"第\s*(\d+)\s*页", query)]
    explicit_pages = [page for page in mentioned_pages if page not in set(excluded_pages)]
    allowed_document_ids = _final_answer_document_ids(metadata)
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
        if target_pages:
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
    target_non_image_source_ids = _final_answer_non_image_source_ids(metadata)
    protected_candidates: list[EvidenceImage] = []
    if mode != "none":
        candidates = _filter_evidence_images_to_target_section(candidates, metadata)
        protected_candidates = [
            image
            for image in candidates
            if has_strong_answer_binding(image, target_non_image_source_ids)
            and _evidence_image_page(image) not in set(excluded_pages)
            and (
                not explicit_pages
                or _evidence_image_page(image) in set(explicit_pages)
            )
        ]
        protected_keys = {
            (image.image_url, image.source_chunk_id)
            for image in protected_candidates
        }
        fallback_candidates = [
            image
            for image in candidates
            if (image.image_url, image.source_chunk_id) not in protected_keys
        ]
        narrowed_fallback = _narrow_evidence_images_to_query_target_pages(
            fallback_candidates,
            metadata,
            force=mode == "single_target",
        )
        candidates = _sort_unique_evidence_images([
            *protected_candidates,
            *narrowed_fallback,
        ])

    direct_image_source_ids = _final_answer_direct_image_source_ids(metadata)
    exact_section_image_source_ids = (
        _exact_target_section_image_source_ids(candidates, metadata)
        if inventory_section_overview
        else set()
    )
    target_step_ids = tuple(sorted(target_non_image_source_ids))
    target_procedure_scope_ids = _final_answer_procedure_scope_ids(metadata)
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
                " ".join(filter(None, (
                    image.caption,
                    image.image_title,
                    image.image_summary,
                    image.section_title,
                    image.context_role,
                )))
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
    protected_keys = {
        (image.image_url, image.source_chunk_id)
        for image in protected_candidates
    }
    selected_page_set = set(selected_pages)
    selected = [
        image
        for image in candidates
        if (
            (image.image_url, image.source_chunk_id) in protected_keys
            or _evidence_image_page(image) in selected_page_set
        )
    ]
    explicit_visual_request = bool(
        understood_query.intent == "image_lookup" or has_structured_visual_focus
    )
    gate_context = ImageEvidenceContext(
        target_non_image_source_ids=frozenset(target_non_image_source_ids),
        direct_image_source_ids=frozenset(direct_image_source_ids),
        exact_section_image_source_ids=frozenset(exact_section_image_source_ids),
        target_procedure_scope_ids=frozenset(target_procedure_scope_ids),
        needs_images=needs_images,
        explicit_visual_request=explicit_visual_request,
        negative_image_request=has_negative_image_request(query),
        explicit_page_render=_query_allows_rendered_page_fallback(query),
        require_local_semantic_match=mode == "single_target",
    )
    accepted, rejected = authorize_images(
        selected,
        gate_context,
        semantic_matcher=lambda image: (
            _evidence_image_matches_query_anchor(query, image)
            or _image_local_query_overlap_score(query, image) >= 0.5
        ),
    )
    authorization_by_key = {
        (image.image_url, image.source_chunk_id): decision.reason
        for image, decision in accepted
    }
    selected = _sort_unique_evidence_images([image for image, _ in accepted])
    if mode == "single_target":
        local_scores = [
            (_image_local_query_overlap_score(query, image), image)
            for image in selected
        ]
        best_local_score = max((score for score, _ in local_scores), default=0.0)
        if best_local_score >= 0.5:
            minimum_relevant_score = max(0.5, best_local_score * 0.6)
            local_scores = [
                (score, image)
                for score, image in local_scores
                if score >= minimum_relevant_score
            ]
        selected = [
            image
            for _, image in sorted(
                local_scores,
                key=lambda item: (
                    -item[0],
                    _evidence_image_page(item[1])
                    if _evidence_image_page(item[1]) is not None else 9999,
                    item[1].source_chunk_id or item[1].image_url,
                ),
            )
        ]
    elif mode == "evidence_pages":
        selected = _sort_unique_evidence_images(selected)
    reject_reason_counts: dict[str, int] = {}
    for item in rejected:
        reason = str(item.get("reason") or "no_image_level_binding")
        reject_reason_counts[reason] = reject_reason_counts.get(reason, 0) + 1
    if has_negative_image_request(query):
        decision_reason = "negative_image_request"
    elif not needs_images:
        decision_reason = "query_does_not_require_images"
    else:
        decision_reason = "image_evidence_gate"
    metadata["image_selection_contract"] = {
        "mode": mode,
        "selection_mode": mode,
        "needs_images": needs_images,
        "decision_reason": decision_reason,
        "candidate_count": len(candidates),
        "page_selected_count": len(selected_pages),
        "authorized_count": len(accepted),
        "selected_count": len(selected),
        "protected_strong_binding_count": len(protected_candidates),
        "protected_strong_binding_ids": [
            image.source_chunk_id for image in protected_candidates
        ],
        "target_pages": target_pages,
        "target_document_ids": allowed_document_ids,
        "target_section_ids": _final_answer_section_ids(metadata),
        "target_section_title": _final_answer_section_title(metadata),
        "target_evidence_ids": list(contract.target_evidence_ids),
        "target_step_ids": list(contract.target_step_ids),
        "target_non_image_source_ids": sorted(target_non_image_source_ids),
        "direct_image_source_ids": sorted(direct_image_source_ids),
        "exact_section_image_source_ids": sorted(exact_section_image_source_ids),
        "target_procedure_scope_ids": sorted(target_procedure_scope_ids),
        "explicit_pages": explicit_pages,
        "excluded_pages": excluded_pages,
        "selected_image_bindings": [
            {
                "source_chunk_id": image.source_chunk_id,
                "page": _evidence_image_page(image),
                "reason": authorization_by_key.get((image.image_url, image.source_chunk_id), ""),
            }
            for image in selected
        ],
        "rejected_images": rejected,
        "reject_reason_counts": reject_reason_counts,
        "selected_pages": [
            page for page in (_evidence_image_page(image) for image in selected) if page is not None
        ],
    }
    return _sort_unique_evidence_images(selected)


_SUSPENDED_IMAGE_REFERENCES = (
    "如下图所示",
    "如图所示",
    "按图所示",
    "详见图示",
    "参见图示",
    "见下图",
    "如下图",
)


def _strip_unbacked_image_references(message: str) -> str:
    cleaned = str(message or "")
    for phrase in _SUSPENDED_IMAGE_REFERENCES:
        cleaned = re.sub(rf"[，、；：]?\s*{re.escape(phrase)}", "", cleaned)
    cleaned = re.sub(r"[，、；：]?\s*（?详见图示）?", "", cleaned)
    return cleaned.strip()


def _apply_final_image_contract(
    message: str,
    images: List[EvidenceImage],
    metadata: dict,
) -> tuple[str, List[EvidenceImage]]:
    route_plan = metadata.get("route_plan") if isinstance(metadata.get("route_plan"), dict) else {}
    query = str(
        metadata.get("resolved_image_query")
        or metadata.get("original_user_message")
        or metadata.get("user_message")
        or ""
    )
    explicit_page_render_allowed = bool(
        _query_allows_rendered_page_fallback(query)
        and _route_scoped_visual_evidence_allowed(metadata, images)
    )
    if (
        not explicit_page_render_allowed
        and (
            metadata.get("blocked_for_insufficient_evidence") is True
            or route_plan.get("action") == RouteAction.INSUFFICIENT_EVIDENCE.value
            or metadata.get("execution_mode") == "maintenance_ai_fallback_after_retrieval"
            or metadata.get("evidence_status") == "no_evidence"
            or metadata.get("execution_mode") == "generic_guidance"
        )
    ):
        images = []
    policy = metadata.get("response_policy") if isinstance(metadata.get("response_policy"), dict) else {}
    if (
        policy
        and policy.get("images_allowed") is False
        and not _route_scoped_visual_evidence_allowed(metadata, images)
    ):
        images = []
    if images:
        return message, images
    return _strip_unbacked_image_references(message), []


async def _safe_build_response_images(
    message: str,
    metadata: dict,
    *,
    input_context: Mapping[str, object],
    session_id: str,
) -> tuple[str, list[EvidenceImage]]:
    stage = "apply_inherited_context"
    try:
        _apply_inherited_image_evidence(metadata, input_context)
        stage = "extract_trace_images"
        images = _extract_evidence_images(metadata)
        stage = "section_image_lookup"
        section_images = await _collect_direct_section_images(metadata)
        stage = "merge_section_images"
        images = _merge_evidence_images(images, section_images)
        stage = "page_image_lookup"
        page_images = _collect_direct_evidence_page_images(metadata)
        stage = "merge_page_images"
        images = _merge_evidence_images(images, page_images)
        stage = "image_evidence_gate"
        images = _select_evidence_images_for_response(images, metadata)
        stage = "final_image_contract"
        final_message, images = _apply_final_image_contract(message, images, metadata)
        metadata["image_selection_status"] = "ok"
        contract = metadata.get("image_selection_contract")
        contract = contract if isinstance(contract, Mapping) else {}
        logger.info(
            "[image_selection] session=%s mode=%s candidates=%s authorized=%s selected=%s reject_reasons=%s selected_ids=%s",
            session_id,
            contract.get("mode"),
            contract.get("candidate_count", 0),
            contract.get("authorized_count", 0),
            contract.get("selected_count", len(images)),
            contract.get("reject_reason_counts", {}),
            [image.source_chunk_id for image in images],
        )
        return final_message, images
    except Exception as exc:
        logger.warning(
            "[image_selection] session=%s stage=%s failed closed",
            session_id,
            stage,
            exc_info=True,
        )
        metadata["image_selection_status"] = "failed"
        metadata["image_selection_failed_stage"] = stage
        metadata["image_selection_error_type"] = type(exc).__name__
        metadata["image_selection_contract"] = {
            "mode": "failed_closed",
            "needs_images": False,
            "decision_reason": "image_pipeline_error",
            "candidate_count": 0,
            "page_selected_count": 0,
            "authorized_count": 0,
            "selected_count": 0,
            "selected_image_bindings": [],
            "rejected_images": [],
            "reject_reason_counts": {"image_pipeline_error": 1},
        }
        return _strip_unbacked_image_references(message), []


async def _run_rag_fast_path(request: ChatRequest, input_data: AgentInput) -> AgentOutput | None:
    """执行 RAG -> 单次 LLM 生成的轻量链路；失败时返回 None 交给 ReAct 回退。"""
    total_t0 = time.time()
    retrieval_t0 = time.time()
    effective_query = input_data.user_message or request.message
    scope = (input_data.context or {}).get("retrieval_scope") or {}
    retrieval_kwargs = build_manual_retrieval_kwargs(
        effective_query,
        scope,
        top_k=5,
        query_contract=(input_data.context or {}).get("query_contract") or {},
    )
    graph_seed_scope = (input_data.context or {}).get("graph_seed_retrieval_scope") or {}
    if graph_seed_scope:
        retrieval_kwargs["_graph_seed_scope"] = dict(graph_seed_scope)
    retrieval = await get_knowledge_retrieval_tool().run(**retrieval_kwargs)
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
            "arguments": dict(retrieval_kwargs),
            "result_summary": str(evidence_items)[:200],
            "result_data": [item.model_dump() if hasattr(item, "model_dump") else item for item in evidence_items],
        }],
    }]
    table_metadata = {
        "react_trace": trace,
        "user_message": effective_query,
        "original_user_message": request.message,
    }
    direct_table_items = await _collect_direct_section_table_items(effective_query, table_metadata)
    table_answer = _format_inventory_table_answer_from_metadata(
        effective_query,
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
        return _finalize_knowledge_output(effective_query, output)

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
                f"用户问题：{effective_query}\n\n"
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
    return _finalize_knowledge_output(effective_query, output)


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
        rag_variant = str((input_data.context or {}).get("rag_variant") or "production")

        fix_t0 = time.time()
        fix_result = None
        review_level = _review_level_for_rag_variant(
            rag_variant,
            "full",
            input_data.context,
        )
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
        if input_data.context and input_data.context.get("retrieval_scope"):
            fix_result.metadata.setdefault("retrieval_scope", input_data.context["retrieval_scope"])
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
            _grounded_turn_context_store().clear(request.session_id)
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
        final_result.metadata.update(
            _rag_variant_audit_metadata(
                context=input_data.context,
                metadata=final_result.metadata,
                review_level=review_level,
            )
        )
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
        route_payload = final_result.metadata.get("route_plan")
        is_clarification_output = bool(
            isinstance(route_payload, Mapping)
            and route_payload.get("action") in {
                RouteAction.CLARIFY.value,
                RouteAction.CLARIFY_DOCUMENT.value,
            }
        ) or bool(
            isinstance(final_result.metadata.get("pending_clarification"), Mapping)
            and final_result.metadata["pending_clarification"].get("status") in {"awaiting", "reasked", "awaiting_answer"}
        )
        structural_recovery_allowed = _route_plan_authorizes_structural_lookup(
            final_result.metadata
        )
        manual_overrides_allowed = (
            not is_clarification_output
            and
            response_policy.get("mode") == "PENDING_RETRIEVAL"
            or response_policy.get("manual_citation_allowed") is not False
            or structural_recovery_allowed
        )
        direct_table_items = (
            await _collect_direct_section_table_items(input_data.user_message, final_result.metadata)
            if manual_overrides_allowed
            else []
        )

        # 低置信度检索时，跳过表格答案覆盖，保留 review 后的原始答案+声明
        low_confidence = _manual_overrides_blocked_by_low_confidence(final_result.metadata)
        logger.info(
            "[chat][manual_override] session=%s allowed=%s structural=%s low_conf=%s mode=%s route=%s",
            request.session_id,
            manual_overrides_allowed,
            structural_recovery_allowed,
            low_confidence,
            response_policy.get("mode"),
            (route_payload or {}).get("action") if isinstance(route_payload, Mapping) else "",
        )
        if low_confidence:
            response_message, diagnosis_items = _extract_structured_chat_payload(final_result.message)
            verification = final_result.metadata.get("verification", {})
            has_issues = final_result.metadata.get("verification_has_issues", False)
        else:
            manual_evidence_answer = None
            table_answer = (
                _format_inventory_table_answer_from_metadata(
                    input_data.user_message,
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
                        input_data.user_message,
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
                follow_up = build_evidence_follow_up(
                    input_data.user_message,
                    final_result.metadata,
                    diagnosis_items=diagnosis_items,
                )
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
            logger.info(
                "[chat][manual_override] session=%s evidence_answer=%s table_answer=%s",
                request.session_id,
                bool(manual_evidence_answer),
                bool(table_answer),
            )
        evidence_images: list[EvidenceImage] = []

        pre_audit_message = response_message
        if not is_clarification_output:
            final_result = await _finalize_knowledge_output_with_fallback(
                request,
                input_data,
                final_result,
                candidate_message=response_message,
            )
        else:
            evidence_images = []
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
        response_message, evidence_images = await _safe_build_response_images(
            response_message,
            final_result.metadata,
            input_context=input_data.context or {},
            session_id=request.session_id,
        )
        response_message = strip_user_visible_emojis(response_message)
        _sync_pending_clarification_state(request.session_id, final_result.metadata)
        _sync_grounded_turn_context(request, request.message, final_result.metadata)

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
        _grounded_turn_context_store().clear(request.session_id)
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
    # Do not use ``\b`` here: Chinese characters are word characters in
    # Unicode regex semantics, so ``12V且`` would otherwise evade the guard.
    r"(?<![A-Za-z0-9])\d+(?:\.\d+)?\s*(?:mm|cm|N\s*[·*]?\s*m|kPa|MPa|rpm|r/min|℃|°C|V|A|%)(?![A-Za-z0-9])",
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

        try:
            input_data = await _prepare_chat_agent_input(request)
            follow_up_output = await _try_causal_follow_up_resolution(request, input_data)
            if follow_up_output is not None:
                _sync_grounded_turn_context(request, request.message, follow_up_output.metadata)
                async for event in _stream_causal_follow_up_output(follow_up_output):
                    yield event
                return

            route_output = await _try_route_plan_direct(request, input_data)
            if route_output is not None:
                _sync_grounded_turn_context(request, request.message, route_output.metadata)
                async for event in _stream_policy_direct_output(route_output):
                    yield event
                return

            policy_output = await _try_response_policy_direct(request, input_data)
            if policy_output is not None:
                _sync_grounded_turn_context(request, request.message, policy_output.metadata)
                async for event in _stream_policy_direct_output(policy_output):
                    yield event
                return

            scope_output = _try_scope_guard(request, input_data)
            if scope_output is not None:
                _sync_grounded_turn_context(request, request.message, scope_output.metadata)
                async for event in _stream_scope_guard_output(scope_output):
                    yield event
                return

            direct_output = await _try_domain_rule_direct(request, input_data)
            if direct_output is not None:
                _sync_grounded_turn_context(request, request.message, direct_output.metadata)
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
                _grounded_turn_context_store().clear(request.session_id)
                done = _ensure_stream_done_image_field({"event": "done", "data": {}})
                yield f"data: {json_dumps(done)}\n\n"
                return

            full_message = "".join(token_buffer)
            stream_react_trace = done_data.get("react_trace", [])
            stream_tools_used = done_data.get("tools_used", [])
            stream_metadata = done_data.get("metadata", {}) if isinstance(done_data.get("metadata"), dict) else {}
            fix_latency = done_data.get("latency_ms", 0)
            verified_tools = tools_in_stream if tools_in_stream else stream_tools_used
            verified_latency = fix_latency

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
                if input_data.context and input_data.context.get("retrieval_scope"):
                    fix_output.metadata.setdefault(
                        "retrieval_scope",
                        input_data.context["retrieval_scope"],
                    )
                fix_output = _enforce_route_document_gate(fix_output, input_data)

                # 运行3层确定性校验（~300ms），获取内联标记位置
                if _is_deterministic_direct_output(fix_output):
                    verified_output = fix_output
                    stream_review_level = "light"
                else:
                    stream_review_level = _review_level_for_rag_variant(
                        str((input_data.context or {}).get("rag_variant") or "production"),
                        "full",
                        input_data.context,
                    )
                    verified_output = await get_review_agent().review(
                        fix_output,
                        level=stream_review_level,
                    )
                if "react_trace" not in verified_output.metadata and fix_output.metadata.get("react_trace"):
                    verified_output.metadata["react_trace"] = fix_output.metadata["react_trace"]
                verified_output.metadata.setdefault("user_message", input_data.user_message)
                verified_output.metadata.setdefault("original_user_message", request.message)
                verified_output.metadata.update(
                    _rag_variant_audit_metadata(
                        context=input_data.context,
                        metadata=verified_output.metadata,
                        review_level=stream_review_level,
                    )
                )
                stream_metadata = {**stream_metadata, **verified_output.metadata}
                if input_data.context and input_data.context.get("retrieval_scope"):
                    stream_metadata.setdefault(
                        "retrieval_scope",
                        input_data.context["retrieval_scope"],
                    )
                verification = verified_output.metadata.get("verification", {})
                has_issues = verified_output.metadata.get("verification_has_issues", False)

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
            direct_table_items = await _collect_direct_section_table_items(input_data.user_message, table_metadata)
            table_answer = _format_inventory_table_answer_from_metadata(
                input_data.user_message,
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
                    input_data.user_message,
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
                diagnostic_follow_up = build_evidence_follow_up(
                    input_data.user_message,
                    {**stream_metadata, "react_trace": stream_react_trace},
                    diagnosis_items=diagnosis_items,
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
                diagnosis_items = None
                markers = []
                verification = {}
                has_issues = False
            if final_message != pre_audit_message:
                diagnosis_items = None
                markers = []
                verification = {}
                has_issues = False

            stream_metadata["react_trace"] = stream_react_trace
            stream_metadata["user_message"] = input_data.user_message
            stream_metadata["original_user_message"] = request.message
            final_message, evidence_images = await _safe_build_response_images(
                final_message,
                stream_metadata,
                input_context=input_data.context or {},
                session_id=request.session_id,
            )
            _sync_grounded_turn_context(request, request.message, stream_metadata)
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
            final_done["data"]["evidenceImages"] = [
                image.model_dump(by_alias=True)
                for image in evidence_images
            ]
            _ensure_stream_done_image_field(final_done)
            yield f"data: {json_dumps(final_done)}\n\n"

        except Exception as e:
            logger.exception(f"[chat_stream] session={request.session_id} error")
            _grounded_turn_context_store().clear(request.session_id)
            yield f"data: {json_dumps({'event': 'error', 'data': {'message': str(e)}})}\n\n"
            done = _ensure_stream_done_image_field({"event": "done", "data": {}})
            yield f"data: {json_dumps(done)}\n\n"

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
    sync_success = not bool(summary.errors)

    return {
        "success": sync_success,
        "message": "操作成功" if sync_success else "同步存在错误，旧版本资源保留",
        "code": 200 if sync_success else 500,
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
        "success": not bool(result.errors),
        "message": "操作成功" if not result.errors else "知识图谱抽取部分失败，请查看 errors",
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
