"""
RabbitMQ 消费者

监听三组队列：
- memory.realtime.queue    → 实时记忆更新（prefetch=5）
- memory.consolidate.queue → 记忆整合（prefetch=1，单任务耗时长）
- knowledge.import.queue   → 知识库导入（prefetch=1，PDF解析+向量化耗时长）

处理完后将结果发到对应 result 队列，由 Java 端消费并更新状态。
"""

import json
import logging
import asyncio

import aio_pika
import httpx

from mq.connection import get_connection
from config.settings import get_settings

logger = logging.getLogger(__name__)

# ===== 记忆系统队列 =====
EXCHANGE_NAME = "memory.exchange"
RESULT_KEY = "memory.result"
REALTIME_QUEUE = "memory.realtime.queue"
CONSOLIDATE_QUEUE = "memory.consolidate.queue"
RESULT_QUEUE = "memory.result.queue"

# ===== 知识导入队列 =====
KNOWLEDGE_EXCHANGE = "knowledge.exchange"
KNOWLEDGE_IMPORT_QUEUE = "knowledge.import.queue"
KNOWLEDGE_RESULT_KEY = "knowledge.result"
KNOWLEDGE_RESULT_QUEUE = "knowledge.result.queue"

# ===== 检修任务队列 =====
TASK_EXCHANGE = "task.exchange"
TASK_GENERATE_QUEUE = "task.generate.queue"
TASK_GENERATE_RESULT_KEY = "task.generate.result"
TASK_GENERATE_RESULT_QUEUE = "task.generate.result.queue"

# ===== 画像出题队列 =====
QUIZ_GENERATE_QUEUE = "quiz.generate.queue"
QUIZ_GENERATE_RESULT_KEY = "quiz.generate.result"
QUIZ_GENERATE_RESULT_QUEUE = "quiz.generate.result.queue"

# ===== 步骤AI验证队列 =====
TASK_STEP_VERIFY_QUEUE = "task.step.verify.queue"
TASK_STEP_VERIFY_RESULT_KEY = "task.step.verify.result"
TASK_STEP_VERIFY_RESULT_QUEUE = "task.step.verify.result.queue"

# ===== 记忆反思队列 =====
REFLECTION_QUEUE = "memory.reflection.queue"
REFLECTION_RESULT_KEY = "memory.reflection.result"
REFLECTION_RESULT_QUEUE = "memory.reflection.result.queue"


async def publish_result(channel: aio_pika.abc.AbstractChannel, data: dict,
                         exchange_name: str = EXCHANGE_NAME, routing_key: str = RESULT_KEY):
    exchange = await channel.get_exchange(exchange_name)
    await exchange.publish(
        aio_pika.Message(
            body=json.dumps(data, ensure_ascii=False).encode(),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        ),
        routing_key=routing_key,
    )


# [已退役] handle_realtime 删除：实时记忆更新链路停用，事实纠正改由对话内 save_memory/delete_memory 处理。


async def handle_consolidate(message: aio_pika.abc.AbstractIncomingMessage, channel: aio_pika.abc.AbstractChannel):
    async with message.process(requeue=False):
        body = json.loads(message.body)
        session_id = str(body["sessionId"])
        user_id = body["userId"]
        round_count = body["roundCount"]
        max_memory = body["maxMemory"]
        logger.info("[MQ消费] 记忆整合开始, 会话ID:%s, 轮次:%s", session_id, round_count)

        try:
            settings = get_settings()

            # 从 Java 端拉取整合参数（携带内部鉴权令牌）
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{settings.java_service_url}/weixiu/memory/consolidation-params",
                    params={
                        "sessionId": session_id,
                        "userId": user_id,
                        "roundCount": round_count,
                        "maxMemory": max_memory,
                    },
                    headers={"X-Internal-Token": settings.internal_token},
                )
                resp.raise_for_status()
                api_result = resp.json()

            params = api_result.get("data")
            if not params:
                logger.info("[MQ消费] 无需整合（Java返回空）, 会话ID:%s", session_id)
                return

            # 调用已有的 memory_agent
            from agents.memory_agent import get_memory_agent
            from agents.base_agent import AgentInput

            conv_dicts = []
            for i, m in enumerate(params.get("memoryMessages", [])):
                conv_dicts.append({"seq": i + 1, "role": m["role"], "content": m["content"]})

            agent_input = AgentInput(
                user_message="请整理以下对话记录",
                session_id=session_id,
                context={
                    "conversations": conv_dicts,
                    "old_preferences": params.get("memoryPreferenceVOList", []),
                    "old_unresolved": params.get("memoryUnresolvedVOList", []),
                    "previous_summary": params.get("previousSummary"),
                    # 现有事实索引（name+type+description），让整合 LLM 看见已有事实，复用 name 去重 / 标记 superseded
                    "existing_facts": params.get("existingFactIndex", ""),
                },
            )

            result = await get_memory_agent().run(agent_input)

            if result.metadata.get("status") == "error":
                raise RuntimeError(result.metadata.get("error_detail", "整合失败"))

            summary_data = result.metadata.get("summary", {})
            summary_data["consolidatedMessageIds"] = params.get("messageIds", [])

            await publish_result(channel, {
                "type": "consolidation",
                "sessionId": session_id,
                "userId": user_id,
                "success": True,
                "data": summary_data,
            })
            logger.info("[MQ消费] 记忆整合完成, 会话ID:%s", session_id)

        except Exception as e:
            logger.error("[MQ消费] 记忆整合失败, 会话ID:%s, 错误:%s", session_id, e, exc_info=True)
            await publish_result(channel, {
                "type": "consolidation",
                "sessionId": session_id,
                "userId": user_id,
                "success": False,
                "error": str(e),
                "data": {},
            })


async def handle_knowledge_import(message: aio_pika.abc.AbstractIncomingMessage, channel: aio_pika.abc.AbstractChannel):
    """消费知识导入任务（含导入和删除两种动作）"""
    async with message.process(requeue=False):
        body = json.loads(message.body)
        action = body.get("action", "import")

        # ===== 删除动作：只清理向量，不解析文档 =====
        if action == "delete":
            document_id = body.get("documentId", "unknown")
            logger.info("[MQ消费] 文档删除开始, documentId=%s", document_id)
            try:
                from services.knowledge_service import get_knowledge_service
                result = get_knowledge_service().delete_document(document_id)
                logger.info(
                    "[MQ消费] 文档删除完成, documentId=%s, 向量=%d, 图片=%d, manifest=%s",
                    document_id, result["vectors_deleted"], result["images_deleted"], result["manifest_deleted"],
                )
            except Exception as e:
                logger.error("[MQ消费] 文档删除失败, documentId=%s, 错误:%s", document_id, e, exc_info=True)
            return

        # ===== 导入动作：解析文档 → 向量化 → 存入 Redis 向量库 =====
        document_id = body.get("documentId") or body.get("taskId", "unknown")
        file_url = body.get("fileUrl", "")
        file_type = body.get("fileType", "pdf")
        category = body.get("category")
        user_id = body.get("userId")
        document_version = body.get("documentVersion")
        device_type = body.get("deviceType")
        manual_type = body.get("manualType")
        old_document_id = body.get("oldDocumentId")
        replace_existing = body.get("replaceExisting", False)
        manual_id = body.get("manualId")
        logger.info("[MQ消费] 知识导入开始, documentId=%s, oldDocumentId=%s, version=%s",
                    document_id, old_document_id, document_version)

        try:
            from services.knowledge_service import get_knowledge_service

            service = get_knowledge_service()

            async def report_progress(stage: str, percent: int):
                # 阶段进度推送：复用知识结果队列，type=progress 由 Java 端识别后只转发 WebSocket
                await publish_result(channel, {
                    "type": "progress",
                    "documentId": document_id,
                    "manualId": manual_id,
                    "userId": user_id,
                    "data": {"stage": stage, "percent": percent},
                }, exchange_name=KNOWLEDGE_EXCHANGE, routing_key=KNOWLEDGE_RESULT_KEY)

            result = await service.import_document(
                file_url=file_url,
                file_type=file_type,
                category=category,
                document_id=document_id,
                device_type=device_type,
                manual_type=manual_type,
                document_version=document_version,
                replace_existing=replace_existing,
                old_document_id=old_document_id,
                manual_id=manual_id,
                progress_cb=report_progress,
            )

            await publish_result(channel, {
                "taskId": document_id,
                "documentId": document_id,
                "userId": user_id,
                "success": True,
                "data": {
                    "total_chunks": result.get("text_count", 0) + result.get("table_count", 0),
                    "text_count": result.get("text_count", 0),
                    "parsed_text_chunks_count": result.get("parsed_text_chunks_count", 0),
                    "chunked_text_chunks_count": result.get("chunked_text_chunks_count", 0),
                    "indexable_text_chunks_count": result.get("indexable_text_chunks_count", 0),
                    "image_count": result.get("image_count", 0),
                    "image_success_count": result.get("image_success_count", result.get("image_count", 0)),
                    "image_failed_count": result.get("image_failed_count", 0),
                    "image_embedding_failed_count": result.get("image_embedding_failed_count", 0),
                    "image_summary_failed_count": result.get("image_summary_failed_count", 0),
                    "table_count": result.get("table_count", 0),
                    "table_success_count": result.get("table_success_count", result.get("table_count", 0)),
                    "table_failed_count": result.get("table_failed_count", 0),
                    "stage_timings_ms": result.get("stage_timings_ms", {}),
                    "document_id": document_id,
                    "file_url": file_url,
                },
            }, exchange_name=KNOWLEDGE_EXCHANGE, routing_key=KNOWLEDGE_RESULT_KEY)

            logger.info("[MQ消费] 知识导入完成, documentId=%s, 解析文本块=%s, 拆分文本块=%s, 可入库文本块=%s, text=%s, image=%s, table=%s, 阶段耗时=%s",
                        document_id,
                        result.get("parsed_text_chunks_count", 0),
                        result.get("chunked_text_chunks_count", 0),
                        result.get("indexable_text_chunks_count", 0),
                        result.get("text_count", 0),
                        result.get("image_count", 0), result.get("table_count", 0),
                        result.get("stage_timings_ms", {}))
            logger.info("[MQ消费] 知识导入统计, documentId=%s, 图片成功=%s, 图片失败=%s, 图片向量兜底=%s, 表格成功=%s, 表格失败=%s",
                        document_id,
                        result.get("image_success_count", result.get("image_count", 0)),
                        result.get("image_failed_count", 0),
                        result.get("image_embedding_failed_count", 0),
                        result.get("table_success_count", result.get("table_count", 0)),
                        result.get("table_failed_count", 0))

        except Exception as e:
            logger.error("[MQ消费] 知识导入失败, documentId=%s, 错误:%s", document_id, e, exc_info=True)
            await publish_result(channel, {
                "taskId": document_id,
                "documentId": document_id,
                "userId": user_id,
                "success": False,
                "error": str(e),
                "data": {},
            }, exchange_name=KNOWLEDGE_EXCHANGE, routing_key=KNOWLEDGE_RESULT_KEY)


async def handle_task_generate(message: aio_pika.abc.AbstractIncomingMessage, channel: aio_pika.abc.AbstractChannel):
    """消费检修任务生成请求，调用 MaintenanceAgent 生成步骤"""
    async with message.process(requeue=False):
        body = json.loads(message.body)
        task_id = body.get("taskId")
        task_number = body.get("taskNumber", "unknown")
        logger.info("[MQ消费] 检修步骤生成开始, taskId=%s, taskNumber=%s", task_id, task_number)

        try:
            from agents.maintenance_agent import get_maintenance_agent

            agent = get_maintenance_agent()
            result = await agent.generate_steps(
                fault_description=body.get("faultDescription", ""),
                device_id=body.get("deviceId"),
                device_name=body.get("deviceName"),
                urgency_level=body.get("urgencyLevel", 1),
                report_images=body.get("reportImages"),
                procedure_steps=body.get("procedureSteps"),
                procedure_id=body.get("procedureId"),
                procedure_name=body.get("procedureName"),
            )

            if result.get("success"):
                msg_body = {
                    "taskId": task_id,
                    "success": True,
                    "steps": result["steps"],
                }
                # 传递AI提取的图谱线索（用于知识沉淀）
                if result.get("graphExtraction"):
                    msg_body["graphExtraction"] = result["graphExtraction"]
                await publish_result(channel, msg_body,
                                     exchange_name=TASK_EXCHANGE, routing_key=TASK_GENERATE_RESULT_KEY)
                logger.info("[MQ消费] 检修步骤生成成功, taskId=%s, 步骤数=%d",
                            task_id, len(result["steps"]))
            else:
                await publish_result(channel, {
                    "taskId": task_id,
                    "success": False,
                    "error": result.get("error", "生成失败"),
                }, exchange_name=TASK_EXCHANGE, routing_key=TASK_GENERATE_RESULT_KEY)
                logger.error("[MQ消费] 检修步骤生成失败, taskId=%s, error=%s",
                             task_id, result.get("error"))

        except Exception as e:
            logger.error("[MQ消费] 检修步骤生成异常, taskId=%s, 错误:%s", task_id, e, exc_info=True)
            await publish_result(channel, {
                "taskId": task_id,
                "success": False,
                "error": str(e),
            }, exchange_name=TASK_EXCHANGE, routing_key=TASK_GENERATE_RESULT_KEY)


async def handle_quiz_generate(message: aio_pika.abc.AbstractIncomingMessage, channel: aio_pika.abc.AbstractChannel):
    """消费出题请求，调用 QuizAgent 生成客观题。"""
    async with message.process(requeue=False):
        body = json.loads(message.body)
        session_id = body.get("quizSessionId")
        logger.info("[MQ消费] 出题生成开始, sessionId=%s, userId=%s", session_id, body.get("userId"))
        try:
            from agents.quiz_agent import get_quiz_agent
            result = await get_quiz_agent().generate(body)

            if result.get("success"):
                # QuizQuestionOut 用 by_alias 输出 camelCase
                from schemas.quiz import QuizQuestionOut
                questions = [QuizQuestionOut(**q).model_dump(by_alias=True) for q in result["questions"]]
                await publish_result(channel, {
                    "quizSessionId": session_id,
                    "success": True,
                    "questions": questions,
                }, exchange_name=TASK_EXCHANGE, routing_key=QUIZ_GENERATE_RESULT_KEY)
                logger.info("[MQ消费] 出题成功, sessionId=%s, 题数=%d", session_id, len(questions))
            else:
                await publish_result(channel, {
                    "quizSessionId": session_id, "success": False,
                    "error": result.get("error", "出题失败"),
                }, exchange_name=TASK_EXCHANGE, routing_key=QUIZ_GENERATE_RESULT_KEY)
                logger.warning("[MQ消费] 出题失败, sessionId=%s, error=%s", session_id, result.get("error"))
        except Exception as e:
            logger.error("[MQ消费] 出题异常, sessionId=%s, 错误:%s", session_id, e, exc_info=True)
            await publish_result(channel, {
                "quizSessionId": session_id, "success": False, "error": str(e),
            }, exchange_name=TASK_EXCHANGE, routing_key=QUIZ_GENERATE_RESULT_KEY)


async def handle_step_verify(message: aio_pika.abc.AbstractIncomingMessage, channel: aio_pika.abc.AbstractChannel):
    """消费步骤AI验证请求，调用 StepVerifyAgent 多模态验证"""
    async with message.process(requeue=False):
        body = json.loads(message.body)
        task_id = body.get("taskId")
        step_id = body.get("stepId")
        logger.info("[MQ消费] 步骤AI验证开始, taskId=%s, stepId=%s", task_id, step_id)

        try:
            from agents.step_verify_agent import get_step_verify_agent

            agent = get_step_verify_agent()
            result = await agent.verify(
                step_title=body.get("stepTitle", ""),
                step_content=body.get("stepContent", ""),
                images=body.get("images"),
                note=body.get("note"),
                safety_note=body.get("safetyNote"),
                device_name=body.get("deviceName"),
                fault_description=body.get("faultDescription"),
            )

            await publish_result(channel, {
                "taskId": task_id,
                "stepId": step_id,
                "aiPass": result["pass"],
                "confidence": result["confidence"],
                "reason": result["reason"],
            }, exchange_name=TASK_EXCHANGE, routing_key=TASK_STEP_VERIFY_RESULT_KEY)

            logger.info("[MQ消费] 步骤AI验证完成, stepId=%s, pass=%s, confidence=%.2f",
                        step_id, result["pass"], result["confidence"])

        except Exception as e:
            logger.error("[MQ消费] 步骤AI验证异常, stepId=%s, 错误:%s", step_id, e, exc_info=True)
            await publish_result(channel, {
                "taskId": task_id,
                "stepId": step_id,
                "aiPass": False,
                "confidence": 0.0,
                "reason": f"AI验证服务异常: {e}",
            }, exchange_name=TASK_EXCHANGE, routing_key=TASK_STEP_VERIFY_RESULT_KEY)


async def handle_reflection(message: aio_pika.abc.AbstractIncomingMessage, channel: aio_pika.abc.AbstractChannel):
    """消费反思任务，从 Java 拉取用户事实，调用 ReflectionAgent 生成画像"""
    async with message.process(requeue=False):
        body = json.loads(message.body)
        user_id = body["userId"]
        logger.info("[MQ消费] 用户画像反思开始, userId:%s", user_id)

        try:
            settings = get_settings()

            # 从 Java 拉取：①用户长期事实(辅证据) ②已审核通过的检修案例履历(主证据)
            headers = {"X-Internal-Token": settings.internal_token}
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{settings.java_service_url}/weixiu/memory/user-facts",
                    params={"userId": user_id}, headers=headers,
                )
                resp.raise_for_status()
                facts = resp.json().get("data", [])

                try:
                    tresp = await client.get(
                        f"{settings.java_service_url}/weixiu/memory/user-task-history",
                        params={"userId": user_id}, headers=headers,
                    )
                    tresp.raise_for_status()
                    task_history = tresp.json().get("data", [])
                except Exception as te:
                    logger.warning("[MQ消费] 拉取检修履历失败(降级为仅事实), userId:%s, err:%s", user_id, te)
                    task_history = []

            if not facts and not task_history:
                logger.info("[MQ消费] 用户无事实也无检修履历，跳过反思, userId:%s", user_id)
                return

            from agents.reflection_agent import get_reflection_agent
            from agents.base_agent import AgentInput

            agent = get_reflection_agent()
            result = await agent.run(AgentInput(
                user_message="请分析用户画像",
                session_id=str(user_id),
                context={"facts": facts, "task_history": task_history, "user_id": user_id}
            ))

            if result.metadata.get("status") == "ok":
                await publish_result(channel, {
                    "type": "reflection",
                    "userId": user_id,
                    "success": True,
                    "data": {
                        "reflections": result.metadata.get("reflections", []),
                        "factCount": result.metadata.get("fact_count", 0),
                    },
                })
                logger.info("[MQ消费] 用户画像反思完成, userId:%s", user_id)
            else:
                raise RuntimeError(result.metadata.get("error", "反思失败"))

        except Exception as e:
            logger.error("[MQ消费] 用户画像反思失败, userId:%s, 错误:%s", user_id, e, exc_info=True)
            await publish_result(channel, {
                "type": "reflection",
                "userId": user_id,
                "success": False,
                "error": str(e),
                "data": {},
            })


async def _declare_topology(channel: aio_pika.abc.AbstractChannel):
    """
    声明 Exchange / Queue / Binding，与 Java 端 RabbitMQConfig 保持一致。
    declare 是幂等的：如果已存在且参数相同则直接返回，不会重复创建。
    这样 Python 和 Java 无论谁先启动都能正常工作。
    """
    # 死信
    dlx = await channel.declare_exchange(
        "memory.dlx", aio_pika.ExchangeType.FANOUT, durable=True
    )
    dlx_queue = await channel.declare_queue("memory.dlx.queue", durable=True)
    await dlx_queue.bind(dlx)

    # ===== 记忆系统拓扑 =====
    exchange = await channel.declare_exchange(
        EXCHANGE_NAME, aio_pika.ExchangeType.TOPIC, durable=True
    )

    # 实时更新队列（TTL 5min）
    realtime_q = await channel.declare_queue(
        REALTIME_QUEUE, durable=True,
        arguments={"x-message-ttl": 300_000, "x-dead-letter-exchange": "memory.dlx"},
    )
    await realtime_q.bind(exchange, "memory.realtime")

    # 整合队列（TTL 10min）
    consolidate_q = await channel.declare_queue(
        CONSOLIDATE_QUEUE, durable=True,
        arguments={"x-message-ttl": 600_000, "x-dead-letter-exchange": "memory.dlx"},
    )
    await consolidate_q.bind(exchange, "memory.consolidate")

    # 结果队列
    result_q = await channel.declare_queue(RESULT_QUEUE, durable=True)
    await result_q.bind(exchange, "memory.result")

    # ===== 知识导入拓扑 =====
    knowledge_exchange = await channel.declare_exchange(
        KNOWLEDGE_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
    )

    # 知识导入队列（TTL 30min，PDF解析+向量化耗时长）
    knowledge_import_q = await channel.declare_queue(
        KNOWLEDGE_IMPORT_QUEUE, durable=True,
        arguments={"x-message-ttl": 1_800_000, "x-dead-letter-exchange": "memory.dlx"},
    )
    await knowledge_import_q.bind(knowledge_exchange, "knowledge.import")

    # 知识导入结果队列
    knowledge_result_q = await channel.declare_queue(
        KNOWLEDGE_RESULT_QUEUE, durable=True,
    )
    await knowledge_result_q.bind(knowledge_exchange, "knowledge.result")

    # ===== 检修任务拓扑 =====
    task_exchange = await channel.declare_exchange(
        TASK_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
    )

    # 检修步骤生成队列（TTL 5min）
    task_generate_q = await channel.declare_queue(
        TASK_GENERATE_QUEUE, durable=True,
        arguments={"x-message-ttl": 300_000, "x-dead-letter-exchange": "memory.dlx"},
    )
    await task_generate_q.bind(task_exchange, "task.generate")

    # 检修步骤生成结果队列
    task_generate_result_q = await channel.declare_queue(
        TASK_GENERATE_RESULT_QUEUE, durable=True,
    )
    await task_generate_result_q.bind(task_exchange, "task.generate.result")

    # ===== 画像出题拓扑（复用 task.exchange） =====
    quiz_generate_q = await channel.declare_queue(
        QUIZ_GENERATE_QUEUE, durable=True,
        arguments={"x-message-ttl": 300_000, "x-dead-letter-exchange": "memory.dlx"},
    )
    await quiz_generate_q.bind(task_exchange, "quiz.generate")

    quiz_generate_result_q = await channel.declare_queue(
        QUIZ_GENERATE_RESULT_QUEUE, durable=True,
    )
    await quiz_generate_result_q.bind(task_exchange, "quiz.generate.result")

    # ===== 步骤AI验证拓扑 =====

    # 步骤验证队列（TTL 5min）
    step_verify_q = await channel.declare_queue(
        TASK_STEP_VERIFY_QUEUE, durable=True,
        arguments={"x-message-ttl": 300_000, "x-dead-letter-exchange": "memory.dlx"},
    )
    await step_verify_q.bind(task_exchange, "task.step.verify")

    # 步骤验证结果队列
    step_verify_result_q = await channel.declare_queue(
        TASK_STEP_VERIFY_RESULT_QUEUE, durable=True,
    )
    await step_verify_result_q.bind(task_exchange, "task.step.verify.result")

    # ===== 记忆反思拓扑 =====

    # 反思队列（TTL 10min）
    reflection_q = await channel.declare_queue(
        REFLECTION_QUEUE, durable=True,
        arguments={"x-message-ttl": 600_000, "x-dead-letter-exchange": "memory.dlx"},
    )
    await reflection_q.bind(exchange, "memory.reflection")

    # 反思结果队列
    reflection_result_q = await channel.declare_queue(
        REFLECTION_RESULT_QUEUE, durable=True,
    )
    await reflection_result_q.bind(exchange, "memory.reflection.result")

    return realtime_q, consolidate_q, knowledge_import_q, task_generate_q, step_verify_q, reflection_q


async def start_consumers():
    connection = await get_connection()

    # 先用一个临时通道声明拓扑
    init_channel = await connection.channel()
    realtime_q, consolidate_q, knowledge_import_q, task_generate_q, step_verify_q, reflection_q = await _declare_topology(init_channel)
    await init_channel.close()

    # [已退役] 实时更新消费者删除：事实纠正改由对话内 save_memory/delete_memory 处理。
    # 队列拓扑仍保留（Java 端已停止发送，队列长期为空，无副作用）。

    # 记忆整合通道（prefetch=1，单任务耗时长，串行处理）
    consolidate_channel = await connection.channel()
    await consolidate_channel.set_qos(prefetch_count=1)
    consolidate_queue = await consolidate_channel.get_queue(CONSOLIDATE_QUEUE)
    await consolidate_queue.consume(
        lambda msg: handle_consolidate(msg, consolidate_channel)
    )

    # 知识导入通道（prefetch=1，PDF解析+向量化耗时长，串行处理）
    knowledge_channel = await connection.channel()
    await knowledge_channel.set_qos(prefetch_count=1)
    knowledge_queue = await knowledge_channel.get_queue(KNOWLEDGE_IMPORT_QUEUE)
    await knowledge_queue.consume(
        lambda msg: handle_knowledge_import(msg, knowledge_channel)
    )

    # 检修任务生成通道（prefetch=1，LLM推理耗时长，串行处理）
    task_channel = await connection.channel()
    await task_channel.set_qos(prefetch_count=1)
    task_queue = await task_channel.get_queue(TASK_GENERATE_QUEUE)
    await task_queue.consume(
        lambda msg: handle_task_generate(msg, task_channel)
    )

    # 出题生成通道（prefetch=1，LLM 检索+生成耗时，串行处理）
    quiz_channel = await connection.channel()
    await quiz_channel.set_qos(prefetch_count=1)
    quiz_queue = await quiz_channel.get_queue(QUIZ_GENERATE_QUEUE)
    await quiz_queue.consume(
        lambda msg: handle_quiz_generate(msg, quiz_channel)
    )

    # 步骤AI验证通道（prefetch=1，多模态LLM验证耗时，串行处理）
    step_verify_channel = await connection.channel()
    await step_verify_channel.set_qos(prefetch_count=1)
    step_verify_queue = await step_verify_channel.get_queue(TASK_STEP_VERIFY_QUEUE)
    await step_verify_queue.consume(
        lambda msg: handle_step_verify(msg, step_verify_channel)
    )

    # 记忆反思通道（prefetch=1，LLM归纳耗时，串行处理）
    reflection_channel = await connection.channel()
    await reflection_channel.set_qos(prefetch_count=1)
    reflection_queue = await reflection_channel.get_queue(REFLECTION_QUEUE)
    await reflection_queue.consume(
        lambda msg: handle_reflection(msg, reflection_channel)
    )

    logger.info("[MQ消费] 消费者启动完成，监听 %s, %s, %s, %s, %s, %s",
                REALTIME_QUEUE, CONSOLIDATE_QUEUE, KNOWLEDGE_IMPORT_QUEUE,
                TASK_GENERATE_QUEUE, TASK_STEP_VERIFY_QUEUE, REFLECTION_QUEUE)
