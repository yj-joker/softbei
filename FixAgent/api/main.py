import json
import logging
import os
import time
from functools import partial
from typing import List
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
                "禁止使用 emoji。"
                "不允许把多个信息点挤在同一整段中。"
                "普通解释使用自然段；当内容包含编号、清单、选项、步骤或文件列表时，每一项必须单独换行。"
                "编号格式使用“1. 内容”“2. 内容”，不要把多个编号写在同一行。"
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
        if _should_use_rag_fast_path(request):
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
        if fix_result.metadata.get("execution_mode") == "knowledge_inventory_direct":
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

        response_message, diagnosis_items = _extract_structured_chat_payload(final_result.message)
        evidence_images = _extract_evidence_images(final_result.metadata)

        return ChatResponse(
            session_id=request.session_id,
            message=response_message,
            tools_used=final_result.tools_used if final_result.tools_used else None,
            latency_ms=final_result.latency_ms,
            verification=verification if has_issues else None,
            diagnosis_items=diagnosis_items,
            evidence_images=evidence_images,
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
                    },
                    latency_ms=fix_latency
                )

                # 运行3层确定性校验（~300ms），获取内联标记位置
                if fix_output.metadata.get("execution_mode") == "knowledge_inventory_direct":
                    verified_output = fix_output
                else:
                    verified_output = await get_review_agent().review(fix_output)
                if "react_trace" not in verified_output.metadata and fix_output.metadata.get("react_trace"):
                    verified_output.metadata["react_trace"] = fix_output.metadata["react_trace"]
                verification = verified_output.metadata.get("verification", {})
                has_issues = verified_output.metadata.get("verification_has_issues", False)
                evidence_images = _extract_evidence_images(verified_output.metadata)

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

            # —— 最终硬保险：检修场景下绝不让结构化 JSON / 冷拒答流给工人 ——
            if maint_ctx and _is_unhelpful_maintenance_reply(final_message):
                logger.info("[chat_stream] 检修助手最终保险触发，替换为安全话术 session=%s", request.session_id)
                final_message = _MAINT_SAFE_FALLBACK_LINE
                diagnosis_items = None
                markers = []

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
                    "latency_ms": verified_latency
                }
            }
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
