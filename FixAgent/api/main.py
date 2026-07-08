import json
import hashlib
import logging
import os
import re
import tempfile
import time
from functools import partial
from typing import Any, List
from fastapi import FastAPI, HTTPException
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
from services.knowledge.image_summary_service import get_image_summary_service
from services.intent_router import get_intent_router
from services.preference_capture import schedule_capture
from tools.knowledge_retrieval_tool import get_knowledge_retrieval_tool
from services.temporary_plan_service import get_temporary_plan_service
from config.settings import get_settings
from schemas.models import AgentMode

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)


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
    return _execution_mode(output.metadata) in {
        "knowledge_inventory_direct",
        "domain_rule_direct",
        "causal_follow_up_resolved",
    }


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
        match = await match_domain_rule(input_data.user_message, device_type=request.device_type)
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
        "react_trace": _domain_rule_trace(match),
        "verification": {
            "grounding": {"unverified_count": 0},
            "graph": {"unverified_count": 0},
            "safety": {"missing_count": 0},
        },
    }
    return AgentOutput(
        agent_name="fix_agent",
        message=match.get("message", ""),
        intention="fault_diagnosis",
        tools_used=[DOMAIN_RULE_TOOL_NAME],
        metadata=metadata,
        latency_ms=latency_ms,
        raw_response=match,
    )


async def _stream_direct_agent_output(output: AgentOutput):
    import asyncio as _asyncio

    match = output.metadata.get("domain_rule_match") or {}
    yield f"data: {json_dumps({'event': 'status', 'data': {'stage': '规则引擎命中，正在生成确定性诊断', 'mode': 'domain_rule'}})}\n\n"
    yield f"data: {json_dumps({'event': 'tool', 'data': {'tool': DOMAIN_RULE_TOOL_NAME}})}\n\n"
    yield f"data: {json_dumps({'event': 'tool_result', 'data': {'tool': DOMAIN_RULE_TOOL_NAME, 'text': output.message, 'items': _domain_rule_tool_items(match)}})}\n\n"

    for i, char in enumerate(output.message):
        yield f"data: {json_dumps({'event': 'token', 'data': {'content': char}})}\n\n"
        if i % 15 == 0:
            await _asyncio.sleep(0)

    yield f"data: {json_dumps({'event': 'verification', 'data': {'has_issues': False, 'summary': {'grounding_unverified': 0, 'graph_unverified': 0, 'safety_missing': 0}}})}\n\n"
    yield f"data: {json_dumps({'event': 'done', 'data': {'tools_used': output.tools_used, 'latency_ms': output.latency_ms, 'domainRule': output.metadata.get('domain_rule'), 'confidenceSource': output.metadata.get('confidence_source'), 'evidenceSources': output.metadata.get('evidence_sources', []), 'metadata': output.metadata}})}\n\n"


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
    resolved = resolve_follow_up(input_data.context or {}, input_data.user_message)
    if not resolved:
        return None

    message = format_resolution_message(resolved)
    metadata = {
        "execution_mode": "causal_follow_up_resolved",
        "confidence_source": "causal_follow_up",
        "confidence_label": "追问收敛",
        "diagnostic_follow_up": resolved,
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
    yield f"data: {json_dumps({'event': 'status', 'data': {'stage': '已根据追问回答重排候选根因', 'mode': 'causal_follow_up'}})}\n\n"
    yield f"data: {json_dumps({'event': 'tool', 'data': {'tool': FOLLOW_UP_TOOL_NAME}})}\n\n"
    yield f"data: {json_dumps({'event': 'tool_result', 'data': {'tool': FOLLOW_UP_TOOL_NAME, 'text': output.message, 'items': _causal_follow_up_tool_items(result)}})}\n\n"

    for i, char in enumerate(output.message):
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


async def _prepare_chat_agent_input(request: ChatRequest) -> AgentInput:
    raw_message = request.message or ""
    effective_message = raw_message.strip() or IMAGE_ONLY_DEFAULT_MESSAGE
    context = dict(request.context or {})
    # 同轮记忆写仲裁：为本轮生成唯一 turn_ts（毫秒），同时传给偏好兜底与主 Agent 的 save_memory；
    # 两路带同一个值，Java saveMemory 才能在"同一句话"上按来源优先级仲裁（漏洞#1修复）。
    turn_ts = int(time.time() * 1000)
    context["turn_ts"] = turn_ts
    # 检索范围强制隔离：把会话绑定的设备/手册透传给检索工具（注入钩子里覆盖 LLM，LLM 不可放宽）
    context["retrieval_scope"] = {
        "device_type": request.device_type,
        "document_id": request.document_id,
    }

    intent_decision = await get_intent_router().classify(
        raw_message,
        images=request.images,
        context=context,
    )
    context["intent_decision"] = intent_decision.model_dump()
    context["intention"] = intent_decision.intent

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


def _iter_trace_result_items(metadata: dict):
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
            if isinstance(result_data, dict) and isinstance(result_data.get("data"), list):
                result_data = result_data["data"]
            if isinstance(result_data, list):
                for item in result_data:
                    item_data = _plain_dict(item)
                    if item_data:
                        yield item_data


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
        if isinstance(raw_row, dict):
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
    primary_row_count = len(primary_rows)

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
        has_duplicate_seq = _inventory_rows_have_duplicate_sequence(rows)
        small_later_table = len(rows) <= max(3, primary_row_count // 2)
        if overlaps_primary_tail:
            kept_full_ids.add(id(candidate))
            continue
        if starts_after_primary and not (has_duplicate_seq and small_later_table):
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
        return rows
    best = max(total for _, total, _, _, _ in candidates)
    if best < 10:
        return rows
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
    return filtered or rows


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

        rows = _inventory_rows_from_table_full(meta.get("table_full"))
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
    filtered_rows = _filter_inventory_rows_for_query(message, rows)
    rows_were_filtered = len(filtered_rows) < len(rows)
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
    numeric_pages = sorted(row_pages or {int(page) for page in pages if str(page).isdigit()})
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

    return "\n".join(lines).strip()


_MANUAL_PROCEDURE_TERMS = ("怎么", "如何", "步骤", "流程", "拆卸", "拆", "安装", "装", "更换", "调整", "操作")
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
    for action in ("拆卸", "安装", "检查"):
        if any(word in text for word in _MANUAL_ACTION_SYNONYMS[action]):
            return action
    return ""


def _manual_content_has_action(text: str, action: str) -> bool:
    return bool(action and any(word in (text or "") for word in _MANUAL_ACTION_SYNONYMS.get(action, ())))


def _manual_content_has_opposite_action(text: str, action: str) -> bool:
    return bool(action and any(word in (text or "") for word in _MANUAL_OPPOSITE_ACTIONS.get(action, ())))


def _manual_action_target(message: str, action: str) -> str:
    text = str(message or "")
    if not action or action not in text:
        return ""
    tail = text.split(action, 1)[1]
    tail = re.split(r"[时的，,？?：:；;、\s]", tail, 1)[0]
    target = tail.strip()
    if target:
        return target
    head = text.split(action, 1)[0]
    head = re.sub(r"(?:怎么|如何|怎样|怎么进行|如何进行)$", "", head).strip()
    head = re.sub(r"[，,？?：:；;、\s]+$", "", head).strip()
    return head


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
            if any(anchor in compact_content for anchor in pre_heading_anchor_terms):
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
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _manual_evidence_records(metadata: dict) -> list[dict]:
    records: list[dict] = []
    for item in _iter_trace_result_items(metadata):
        meta = dict(item.get("metadata") or {})
        chunk_type = meta.get("chunk_type") or meta.get("source_chunk_type") or ""
        if chunk_type in {"image", "image_summary", "outline"}:
            continue
        content = _manual_clean_content(item.get("content") or item.get("text") or "")
        if not content:
            continue
        if re.fullmatch(r"\d+(?:\.\d+)+\s+.{1,30}", content):
            continue
        if _manual_is_outline_navigation_noise(content, meta):
            continue
        records.append({**item, "content": content, "metadata": meta})
    return records


def _manual_title_section_match_scores(message: str) -> dict[str, int]:
    try:
        from services.knowledge.vector_service import get_vector_service
        from services.retrieval.section_index import SectionTitleIndex

        vector_service = get_vector_service()
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
        if kind == "procedure" and chunk_type == "step_raw":
            score += 16
        if kind == "parameter" and chunk_type == "table":
            score += 14
        if kind == "parameter" and re.search(r"\d", content):
            score += 8
            if any(anchor and anchor in content for anchor in _manual_parameter_anchor_terms(message)):
                score += 80
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
    if re.fullmatch(r"\d+(?:\.\d+)+\s+.{1,30}", content):
        return None
    if _manual_is_outline_navigation_noise(content, meta):
        return None
    if section_match_ids:
        meta.setdefault("section_match_ids", list(section_match_ids))
    return {**item, "content": content, "metadata": meta}


def _manual_expand_same_section_records(best_group: list[dict], section_match_ids: set[str]) -> list[dict]:
    if not best_group:
        return best_group
    first_meta = best_group[0].get("metadata") or {}
    document_id = str(first_meta.get("document_id") or "")
    section_id = str(first_meta.get("parent_section_id") or "")
    if not document_id or not section_id:
        return best_group
    try:
        from services.knowledge.vector_service import get_vector_service

        vector_service = get_vector_service()
        raw_records = vector_service.get_section_records(
            document_id,
            section_id,
            limit=200,
            chunk_type=None,
        )
    except Exception:
        return best_group

    expanded: list[dict] = list(best_group)
    seen_ids = {str(item.get("id") or item.get("doc_id") or "") for item in expanded}
    seen_content = {str(item.get("content") or "") for item in expanded}
    for raw in raw_records:
        record = _manual_record_from_raw(raw, section_match_ids)
        if not record:
            continue
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


def _manual_expand_page_boundary_records(best_group: list[dict], section_match_ids: set[str]) -> list[dict]:
    if not best_group:
        return best_group
    document_id = str((best_group[0].get("metadata") or {}).get("document_id") or "")
    if not document_id:
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
        from services.knowledge.vector_service import get_vector_service

        vector_service = get_vector_service()
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


def _manual_title_match_records(message: str) -> tuple[list[dict], set[str]]:
    try:
        from services.knowledge.vector_service import get_vector_service
        from services.retrieval.section_index import SectionTitleIndex

        vector_service = get_vector_service()
        section_index = SectionTitleIndex.get_instance()
        section_index.build(vector_service)
        refs = section_index.find(message or "")[:2]
    except Exception:
        return [], set()

    records: list[dict] = []
    section_ids = {str(getattr(ref, "section_id", "") or "") for ref in refs if getattr(ref, "section_id", "")}
    for ref in refs:
        document_id = str(getattr(ref, "document_id", "") or "")
        section_id = str(getattr(ref, "section_id", "") or "")
        if not document_id or not section_id:
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
        for raw in raw_records:
            record = _manual_record_from_raw(raw, section_ids)
            if not record:
                continue
            meta = dict(record.get("metadata") or {})
            meta["original_title_match"] = True
            record["metadata"] = meta
            records.append(record)
    return records, section_ids


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
    records = _manual_evidence_records(metadata)
    if not records:
        records = []
    section_match_ids: set[str] = set()
    for record in records:
        for sid in (record.get("metadata") or {}).get("section_match_ids") or []:
            if sid:
                section_match_ids.add(str(sid))
    title_match_records, title_match_section_ids = _manual_title_match_records(message)
    if title_match_section_ids:
        section_match_ids.update(title_match_section_ids)
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
        (_manual_group_score(message, kind, group, section_match_ids, title_section_scores), key, group)
        for key, group in groups.items()
    ]
    if not scored:
        return []
    best_score, _, best_group = max(scored, key=lambda item: item[0])
    if best_score < 18:
        return []
    best_group = _manual_expand_same_section_records(best_group, section_match_ids)
    best_group = _manual_expand_page_boundary_records(best_group, section_match_ids)
    action = _manual_query_action(message)
    if action and _manual_should_trim_to_action(message, kind):
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
    deduped: list[dict] = []
    seen: set[str] = set()
    for record in sorted(best_group, key=_manual_item_order):
        content = record.get("content") or ""
        if _manual_is_next_section_heading_noise(content, message):
            continue
        if content in seen:
            continue
        seen.add(content)
        deduped.append(record)
    return deduped


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
    return required_terms


def _manual_answer_should_refuse_detail_query(message: str, records: list[dict]) -> bool:
    evidence = "\n".join(record.get("content") or "" for record in records)
    required_terms = _manual_requested_detail_terms(message)
    return bool(required_terms and not all(term in evidence for term in required_terms))


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
    return (
        f"根据手册{page_text}{title_text}，当前可检索到相关装配/检查内容，"
        f"但手册未提供{missing_text}。请以原厂手册、配件清单或实物标识为准。"
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
    records = _manual_best_section_records(message, kind, metadata)
    if not records:
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
    lead = "原文步骤如下" if kind == "procedure" else "原文相关内容如下"
    lines = [f"根据手册{page_text}“{title_text}”，{lead}："]

    for index, record in enumerate(records[:12], start=1):
        content = _manual_clean_content(record.get("content") or "")
        content = _manual_strip_embedded_tail_heading(content, title_text)
        if not content:
            continue
        first_line = content.splitlines()[0].strip()
        if _manual_starts_with_numbered_step(first_line):
            lines.append(content)
        else:
            lines.append(f"{index}. {content}")
    return "\n".join(lines).strip() if len(lines) > 1 else None


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
            )
        )
    return images


async def _collect_direct_section_table_items(message: str, metadata: dict) -> list[dict]:
    """清单直取通道：按确定性章节补全同节全部表格，解决跨页 BOM 只召回一页的问题。"""
    if not _is_inventory_table_query(message):
        return []
    try:
        plan_intent = ""
        document_id = ""
        section_match_ids: List[str] = []
        title_section_ids: List[str] = []
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
        append_unique(lookup_queries, message)

        for item in _iter_trace_result_items(metadata):
            item_meta = dict(item.get("metadata") or {})
            if item_meta.get("retrieval_plan_intent"):
                plan_intent = str(item_meta["retrieval_plan_intent"])
            if item_meta.get("document_id"):
                document_id = str(item_meta["document_id"])
            sm_ids = item_meta.get("section_match_ids")
            if isinstance(sm_ids, list):
                for sid in sm_ids:
                    append_unique(section_match_ids, str(sid))
            parent_section_id = str(item_meta.get("parent_section_id") or "")
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
                        ref_title = f"{getattr(ref, 'core_title', '')} {getattr(ref, 'full_title', '')}"
                        if "清单" in ref_title:
                            append_unique(title_section_ids, str(ref.section_id))
                        append_unique(section_match_ids, str(ref.section_id))
                        if not document_id and ref.document_id:
                            document_id = str(ref.document_id)
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
                meta.setdefault("section_match_ids", section_match_ids)
                meta.setdefault("retrieval_plan_intent", plan_intent)
                rec["metadata"] = meta
                table_items.append(rec)
        return table_items
    except Exception:
        return []


async def _collect_direct_section_images(metadata: dict) -> List[EvidenceImage]:
    """直取通道：procedure / outline 意图下，按 section_match_ids 确定性地查库取图。

    不依赖检索排名，走 get_section_records(chunk_type='image') 精确查库。
    消除图片返回的非确定性——目标章节的图只要存在就一定被拿到。
    """
    try:
        trace = (metadata or {}).get("react_trace") or []
        plan_intent = ""
        document_id = ""
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

        for step in trace:
            step_data = _plain_dict(step)
            for tool_call in (step_data.get("tool_calls") or []):
                call_data = _plain_dict(tool_call)
                arguments = call_data.get("arguments") or call_data.get("args") or {}
                arguments = _plain_dict(arguments) if hasattr(arguments, "model_dump") else arguments
                if isinstance(arguments, dict):
                    query_arg = str(arguments.get("query") or "").strip()
                    if query_arg:
                        append_unique(lookup_queries, query_arg)
                elif isinstance(arguments, str) and arguments.strip():
                    append_unique(lookup_queries, arguments.strip())
                result_data = call_data.get("result_data")
                if result_data is None:
                    result_data = call_data.get("data")
                if result_data is None:
                    result_data = call_data.get("result")
                result_data = _plain_dict(result_data) if hasattr(result_data, "model_dump") else result_data
                if isinstance(result_data, dict):
                    result_data = result_data.get("data", result_data)
                if not isinstance(result_data, list):
                    continue
                for item in result_data:
                    item_data = _plain_dict(item)
                    item_meta = dict(item_data.get("metadata") or {})
                    if item_meta.get("retrieval_plan_intent"):
                        plan_intent = str(item_meta["retrieval_plan_intent"])
                    if item_meta.get("document_id"):
                        document_id = str(item_meta["document_id"])
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
                        append_unique(section_match_ids, str(ref.section_id))
                        if not document_id and ref.document_id:
                            document_id = str(ref.document_id)
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
                image_url = meta.get("image_url") or rec.get("image_url")
                if not image_url or image_url in seen_urls:
                    continue
                seen_urls.add(image_url)
                images.append(EvidenceImage(
                    image_url=image_url,
                    caption=meta.get("caption") or meta.get("image_title") or "",
                    page=meta.get("page_number") or meta.get("page"),
                    section_title=meta.get("section_title", ""),
                    document_id=meta.get("document_id", ""),
                    source_chunk_id=str(rec.get("id") or rec.get("doc_id") or ""),
                    context_role="direct_lookup",
                ))
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
    try:
        if vector_service is None:
            from services.knowledge.vector_service import get_vector_service
            vector_service = get_vector_service()
    except Exception:
        return []

    images: List[EvidenceImage] = []
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
                page_had_matched_image = True
                meta = dict(rec.get("metadata") or {})
                image_url = meta.get("image_url") or rec.get("image_url")
                if not image_url or image_url in seen_urls:
                    continue
                seen_urls.add(image_url)
                images.append(EvidenceImage(
                    image_url=image_url,
                    caption=meta.get("caption") or meta.get("image_title") or rec.get("content", ""),
                    page=meta.get("page_number") or meta.get("page"),
                    section_title=meta.get("section_title", ""),
                    document_id=meta.get("document_id", ""),
                    source_chunk_id=str(rec.get("id") or rec.get("doc_id") or ""),
                    context_role="page_lookup",
                ))
            if not page_had_indexed_image or not page_had_matched_image:
                rendered = _render_evidence_pdf_page_image(metadata, document_id, page)
                if rendered and rendered.image_url not in seen_urls:
                    seen_urls.add(rendered.image_url)
                    images.append(rendered)
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
    try:
        from services.knowledge.vector_service import get_vector_service
        vector_service = get_vector_service()
    except Exception:
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
    "朝下",
    "朝上",
    "朝外",
    "开口",
    "缺口",
    "错开",
    "a孔",
    "b段",
    "d段",
    "塞尺",
    "组别",
    "放油",
    "放水",
    "水箱盖",
    "负极线",
    "正极线",
    "线束",
    "导出线束",
    "圆柱销",
    "定位销",
    "o型圈",
    "螺栓",
    "螺母",
    "数量",
    "扭矩",
    "扭力",
    "力矩",
    "间隙",
    "拉玛",
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

    for term in (
        "排放机油",
        "放油螺栓",
        "排放冷却液",
        "放水螺栓",
        "右水箱盖",
        "IN标记",
        "EX标记",
        "A孔",
        "B段",
        "D段",
        "C标记",
        "D标记",
        "塞尺",
        "组别",
        "导出线束",
        "负极线",
        "正极线",
    ):
        if term.lower() in compact_query:
            add(term)

    for pattern in (
        r"[a-z]+标记",
        r"[a-z]孔",
        r"[a-z]段",
        r"[\u4e00-\u9fffA-Za-z0-9φΦ×.\-]+(?:螺栓|螺母|o型圈|定位销|圆柱销|挡圈|垫圈|线束|拉玛)",
    ):
        for match in re.finditer(pattern, compact_query):
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
    if not _image_query_has_specific_target(query):
        return sorted_images

    try:
        from services.retrieval.image_selector import PageEvidence, score_pages_for_image_query
    except Exception:
        return sorted_images

    text_by_page = _text_context_by_page_for_image_narrowing(metadata, image_pages)
    images_by_page: dict[int, list[EvidenceImage]] = {}
    for image in sorted_images:
        page = _evidence_image_page(image)
        if page is None:
            continue
        images_by_page.setdefault(page, []).append(image)

    page_evidence = []
    for page in sorted(image_pages):
        page_images = images_by_page.get(page, [])
        image_context = " ".join(
            " ".join(
                str(value or "")
                for value in (
                    image.caption,
                    image.section_title,
                    image.source_chunk_id,
                )
            )
            for image in page_images
        )
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

    anchors = _image_specific_anchor_terms(query)
    if anchors:
        anchor_hits: dict[int, int] = {}
        for page in sorted(image_pages):
            page_text = _compact_inventory_text(
                f"{text_by_page.get(page, '')} "
                f"{' '.join(str(image.caption or '') + ' ' + str(image.section_title or '') for image in images_by_page.get(page, []))}"
            ).lower()
            anchor_hits[page] = sum(1 for anchor in anchors if anchor and anchor in page_text)
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
            from services.knowledge.vector_service import get_vector_service
            vector_service = get_vector_service()
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
        parts.extend(
            str(value or "")
            for value in (
                record.get("content"),
                record.get("text"),
                meta.get("caption"),
                meta.get("image_title"),
                meta.get("visual_context_text"),
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


async def _run_rag_fast_path(request: ChatRequest) -> AgentOutput | None:
    """执行 RAG -> 单次 LLM 生成的轻量链路；失败时返回 None 交给 ReAct 回退。"""
    total_t0 = time.time()
    retrieval_t0 = time.time()
    retrieval = await get_knowledge_retrieval_tool().run(
        query=request.message,
        top_k=5,
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
            "arguments": {"query": request.message, "top_k": 5},
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
            "_deterministic_answer_evidence_pages",
            "_deterministic_answer_document_ids",
            "_deterministic_answer_section_title",
            "_deterministic_answer_section_ids",
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
        return AgentOutput(
            agent_name="fix_agent",
            message=table_answer,
            tools_used=["knowledge_retrieval"],
            metadata=fast_metadata,
            latency_ms=total_ms,
            raw_response={"content": table_answer},
        )

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

    return AgentOutput(
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
            fix_result = await _try_domain_rule_direct(request, input_data)
        if fix_result is not None:
            review_level = "light"
        elif _should_use_rag_fast_path(request):
            fix_result = await _run_rag_fast_path(request)
            if fix_result is not None:
                review_level = "light"

        if fix_result is None:
            fix_result = await get_fix_agent().run_with_react(input_data)
        fix_result.metadata["user_message"] = input_data.user_message
        fix_result.metadata["original_user_message"] = request.message
        if input_data.context and input_data.context.get("intent_decision"):
            fix_result.metadata["intent_decision"] = input_data.context["intent_decision"]
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
                    message=fix_result.message,
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

        direct_table_items = await _collect_direct_section_table_items(request.message, final_result.metadata)
        table_answer = _format_inventory_table_answer_from_metadata(
            request.message,
            final_result.metadata,
            direct_table_items,
        )
        if table_answer:
            final_result.metadata["deterministic_table_answer"] = True
            final_result.metadata["deterministic_table_answer_source"] = "api_response_override"
            response_message = table_answer
            diagnosis_items = None
            verification = {}
            has_issues = False
        else:
            manual_evidence_answer = _format_manual_evidence_answer_from_metadata(
                request.message,
                final_result.metadata,
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
            if not manual_evidence_answer and not _is_deterministic_direct_output(final_result):
                follow_up = build_follow_up(input_data.user_message, diagnosis_items, final_result.metadata)
                if follow_up:
                    final_result.metadata["execution_mode"] = "causal_follow_up_question"
                    final_result.metadata["confidence_source"] = "causal_follow_up"
                    final_result.metadata["diagnostic_follow_up"] = follow_up
                    final_result.tools_used = list(final_result.tools_used or [])
                    if FOLLOW_UP_TOOL_NAME not in final_result.tools_used:
                        final_result.tools_used.append(FOLLOW_UP_TOOL_NAME)
                    response_message = format_follow_up_message(follow_up)
                    diagnosis_items = None
                    verification = {}
                    has_issues = False
        evidence_images = _extract_evidence_images(final_result.metadata)
        # 直取通道：procedure 意图下，按确定性章节查库补图
        direct_images = await _collect_direct_section_images(final_result.metadata)
        if direct_images:
            evidence_images = _merge_evidence_images(evidence_images, direct_images)
        page_images = _collect_direct_evidence_page_images(final_result.metadata)
        if page_images:
            evidence_images = _merge_evidence_images(evidence_images, page_images)
        evidence_images = _align_evidence_images_to_text_evidence_pages(evidence_images, final_result.metadata)
        evidence_images = _narrow_evidence_images_to_query_target_pages(evidence_images, final_result.metadata)
        evidence_images = _filter_evidence_images_by_action_context(evidence_images, final_result.metadata)
        evidence_images = _filter_evidence_images_to_target_section(evidence_images, final_result.metadata)

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


async def _maintenance_fallback_answer(input_data: AgentInput, maint_ctx: dict):
    """检修场景兜底：抛开 ReAct/工具门槛，用「上下文+历史」做一次纯对话作答。"""
    system = (
        "你是经验丰富的现场检修助手。请根据下面的【任务背景】和对话历史，"
        "用简明、安全第一、可操作的中文，直接给工人下一步可执行的建议。"
        "第一句话就给结论，即使知识库没有完全匹配的资料，也要基于通用检修经验务实作答。"
        "对常见故障原因、排查思路、原理性问题，要大胆运用专业常识给出有价值的判断；"
        "但涉及精确参数（扭矩、间隙、公差、具体型号规格、确切数值）时，只给方向、范围或排查方法，"
        "并提示『具体数值以该设备手册/铭牌为准』，绝不编造确切数字。"
        "严禁以「资料不足 / 无法回答 / 暂不能生成」搪塞；"
        "严禁输出任何 JSON、花括号 {} 或字段名，只用自然段中文回答。\n\n"
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
            fallback_text = None
            maint_ctx = (input_data.context or {}).get("maintenance")
            if maint_ctx and _is_unhelpful_maintenance_reply(full_message):
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
                    },
                    latency_ms=fix_latency
                )

                # 运行3层确定性校验（~300ms），获取内联标记位置
                if _is_deterministic_direct_output(fix_output):
                    verified_output = fix_output
                else:
                    verified_output = await get_review_agent().review(fix_output)
                if "react_trace" not in verified_output.metadata and fix_output.metadata.get("react_trace"):
                    verified_output.metadata["react_trace"] = fix_output.metadata["react_trace"]
                verified_output.metadata.setdefault("user_message", input_data.user_message)
                verified_output.metadata.setdefault("original_user_message", request.message)
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
                if maint_ctx and verified_output.metadata.get("blocked_for_insufficient_evidence"):
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
                    "_deterministic_answer_evidence_pages",
                    "_deterministic_answer_document_ids",
                    "_deterministic_answer_section_title",
                    "_deterministic_answer_section_ids",
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

            image_metadata = {
                **stream_metadata,
                "react_trace": stream_react_trace,
                "user_message": input_data.user_message,
                "original_user_message": request.message,
            }
            page_images = _collect_direct_evidence_page_images(image_metadata)
            if page_images:
                evidence_images = _merge_evidence_images(evidence_images, page_images)
            evidence_images = _align_evidence_images_to_text_evidence_pages(evidence_images, image_metadata)
            evidence_images = _narrow_evidence_images_to_query_target_pages(evidence_images, image_metadata)
            evidence_images = _filter_evidence_images_by_action_context(evidence_images, image_metadata)
            evidence_images = _filter_evidence_images_to_target_section(evidence_images, image_metadata)

            if diagnostic_follow_up:
                yield f"data: {json_dumps({'event': 'status', 'data': {'stage': '存在多个相近根因，正在生成区分性追问', 'mode': 'causal_follow_up'}})}\n\n"
                yield f"data: {json_dumps({'event': 'tool', 'data': {'tool': FOLLOW_UP_TOOL_NAME}})}\n\n"
                yield f"data: {json_dumps({'event': 'tool_result', 'data': {'tool': FOLLOW_UP_TOOL_NAME, 'text': final_message, 'items': _causal_follow_up_tool_items(diagnostic_follow_up)}})}\n\n"

            marker_idx = 0
            for i, char in enumerate(final_message):
                while marker_idx < len(markers) and markers[marker_idx]["char_pos"] <= i:
                    m = markers[marker_idx]
                    yield f"data: {json_dumps({'event': 'marker', 'data': {'text': m['text'], 'type': m['type']}})}\n\n"
                    marker_idx += 1

                yield f"data: {json_dumps({'event': 'token', 'data': {'content': char}})}\n\n"
                if i % 15 == 0:
                    await _asyncio.sleep(0)

            # 末尾剩余标记（安全追加文本中可能出现的新段落）
            while marker_idx < len(markers):
                m = markers[marker_idx]
                yield f"data: {json_dumps({'event': 'marker', 'data': {'text': m['text'], 'type': m['type']}})}\n\n"
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
                final_done["data"]["metadata"] = stream_metadata
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
        ).model_dump()
    )
