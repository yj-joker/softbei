package ai.weixiu.mq;

import ai.weixiu.common.RedisKey;
import ai.weixiu.config.RabbitMQConfig;
import ai.weixiu.entity.*;
import ai.weixiu.entity.MemoryIdempotent;
import ai.weixiu.entity.MemoryReflection;
import ai.weixiu.mapper.MemoryIdempotentMapper;
import ai.weixiu.service.*;
import ai.weixiu.service.ManualRecommendService;
import cn.hutool.json.JSONArray;
import cn.hutool.json.JSONObject;
import cn.hutool.json.JSONUtil;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.rabbitmq.client.Channel;
import lombok.AllArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.amqp.support.AmqpHeaders;
import org.springframework.data.redis.core.RedisTemplate;
import org.springframework.messaging.handler.annotation.Header;
import org.springframework.stereotype.Component;

import org.springframework.web.reactive.function.client.WebClient;

import java.io.IOException;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.stream.Collectors;

@Component
@AllArgsConstructor
@Slf4j
public class MemoryResultListener {

    private final MemoryFactService memoryFactService;
    private final MemoryReflectionService memoryReflectionService;
    private final AiSessionService aiSessionService;
    private final AiMessageService aiMessageService;
    private final ManualRecommendService manualRecommendService;
    private final RedisTemplate<String, Object> redisTemplate;
    private final WebClient webClient;
    private final MemoryIdempotentMapper idempotentMapper;

    @RabbitListener(queues = RabbitMQConfig.RESULT_QUEUE)
    public void onMemoryResult(Map<String, Object> msg, Channel channel, @Header(AmqpHeaders.DELIVERY_TAG) long tag) throws IOException {
        try {
            String type = (String) msg.get("type");
            boolean success = Boolean.TRUE.equals(msg.get("success"));
            String sessionId = String.valueOf(msg.get("sessionId"));
            Number userIdNum = (Number) msg.get("userId");
            Long userId = userIdNum != null ? userIdNum.longValue() : null;

            // 幂等检查
            String messageId = (String) msg.get("messageId");
            if (isDuplicate(messageId, type)) {
                channel.basicAck(tag, false);
                return;
            }

            if (!success) {
                log.warn("[MQ结果] 任务失败, type={}, sessionId={}, error={}", type, sessionId, msg.get("error"));
                channel.basicAck(tag, false);
                return;
            }

            @SuppressWarnings("unchecked")
            Map<String, Object> data = (Map<String, Object>) msg.get("data");
            if (data == null) {
                log.warn("[MQ结果] data为空, type={}, sessionId={}", type, sessionId);
                channel.basicAck(tag, false);
                return;
            }

            if ("consolidation".equals(type)) {
                processConsolidationResult(data, sessionId, userId);
            } else if ("reflection".equals(type)) {
                processReflectionResult(data, userId);
            } else {
                log.warn("[MQ结果] 未知type: {}", type);
            }

            channel.basicAck(tag, false);
        } catch (Exception e) {
            log.error("[MQ结果] 处理失败: {}", e.getMessage(), e);
            channel.basicNack(tag, false, false);
        }
    }

    // [已退役] processRealtimeResult 整体删除：实时记忆更新链路停用，
    // 事实纠正改由对话内 delete_memory/save_memory 处理（见 AiServiceImpl doOnComplete 注释）。

    private void processConsolidationResult(Map<String, Object> data, String sessionId, Long userId) {
        JSONObject summaryData = JSONUtil.parseObj(data);

        // 整合写入的 turn_ts = 本窗口最后一条消息时间（毫秒），供同轮写仲裁（漏洞#1 1b）；
        // 取不到则置 null（保守：不覆盖任何带真实 turn_ts 的实时写）。
        Long windowTurnTs = computeWindowTurnTs(data);

        // 1. 保存newFacts —— 按 (user_id, name) upsert，与对话内 save_memory 收敛到同一 name 键空间，避免重复 active 事实。
        //    · 同名已存在(active/superseded) → 就地更新并重新激活；
        //    · 同名已被用户 delete_memory 软删 → 跳过，不因自动抽取而复活；
        //    · 无 name(legacy) → 直接插入。
        JSONArray factIds = summaryData.getJSONArray("fact_ids");
        JSONArray newFacts = summaryData.getJSONArray("newFacts");
        if (newFacts != null && !newFacts.isEmpty()) {
            int inserted = 0, updated = 0, skipped = 0;
            for (int i = 0; i < newFacts.size(); i++) {
                JSONObject fact = newFacts.getJSONObject(i);
                MemoryFact memoryFact = new MemoryFact();
                memoryFact.setSessionId(sessionId);
                memoryFact.setUserId(userId);
                String vectorDocId = (factIds != null && i < factIds.size()) ? factIds.getStr(i) : null;
                memoryFact.setFactId((vectorDocId != null && !vectorDocId.isEmpty()) ? vectorDocId : UUID.randomUUID().toString());
                memoryFact.setContent(fact.getStr("content"));
                memoryFact.setKeywords(fact.getStr("keywords"));
                memoryFact.setSourceSeqRange(fact.getStr("sourceSeqRange"));
                memoryFact.setStatus("active");
                memoryFact.setImportance(fact.getInt("importance", 5));
                memoryFact.setConfidence(fact.getDouble("confidence", 0.80));
                memoryFact.setUsageCount(0);
                // 同轮写仲裁（漏洞#1 1b）：整合写带来源 + 窗口 turn_ts
                memoryFact.setSource("consolidation");
                memoryFact.setTurnTs(windowTurnTs);
                // 业务维度（Phase 4）
                String deviceType = fact.getStr("deviceType", "");
                if (!deviceType.isEmpty()) {
                    memoryFact.setDeviceType(deviceType);
                }
                String equipmentIdStr = fact.getStr("equipmentId", "");
                if (!equipmentIdStr.isEmpty()) {
                    try { memoryFact.setEquipmentId(Long.valueOf(equipmentIdStr)); } catch (NumberFormatException ignored) {}
                }
                String siteIdStr = fact.getStr("siteId", "");
                if (!siteIdStr.isEmpty()) {
                    try { memoryFact.setSiteId(Long.valueOf(siteIdStr)); } catch (NumberFormatException ignored) {}
                }
                String taskIdStr = fact.getStr("taskId", "");
                if (!taskIdStr.isEmpty()) {
                    try { memoryFact.setTaskId(Long.valueOf(taskIdStr)); } catch (NumberFormatException ignored) {}
                }
                // 文件式记忆索引字段（Task 4）：name/description/type/why/how_to_apply
                applyIndexFields(memoryFact, fact);

                switch (upsertFactByName(userId, memoryFact)) {
                    case "updated" -> updated++;
                    case "skipped" -> skipped++;
                    default -> inserted++;
                }
            }
            log.info("[MQ结果] 整合事实 upsert 完成, 新增:{} 更新:{} 跳过(已删除):{}", inserted, updated, skipped);
        }

        // 2. 更新supersededIds
        JSONArray supersededIds = summaryData.getJSONArray("supersededIds");
        if (supersededIds != null && !supersededIds.isEmpty()) {
            List<String> supersededFactIds = new ArrayList<>();
            for (int i = 0; i < supersededIds.size(); i++) {
                supersededFactIds.add(supersededIds.getStr(i));
            }
            LambdaUpdateWrapper<MemoryFact> factWrapper = new LambdaUpdateWrapper<>();
            factWrapper.in(MemoryFact::getFactId, supersededFactIds)
                    .set(MemoryFact::getStatus, "superseded")
                    .set(MemoryFact::getSupersededAt, LocalDateTime.now());
            memoryFactService.update(factWrapper);
            log.info("[MQ结果] 更新已替代事实, 数量:{}", supersededFactIds.size());

            // 同步通知 Python 删除 Redis 向量库中的旧事实
            try {
                Map<String, Object> deleteRequest = new HashMap<>();
                deleteRequest.put("fact_ids", supersededFactIds);
                webClient.post()
                        .uri("/ai/memory/delete_facts")
                        .bodyValue(deleteRequest)
                        .retrieve()
                        .bodyToMono(String.class)
                        .block();
                log.info("[MQ结果] 已通知Python删除旧事实向量, 数量:{}", supersededFactIds.size());
            } catch (Exception e) {
                log.warn("[MQ结果] 通知Python删除旧事实向量失败（不影响主流程）: {}", e.getMessage());
            }
        }

        // 3. [已退役] updatedPreferences 不再写 memory_preference。
        // 偏好已并入 memory_fact(type=user)，由 LLM 的 save_memory/delete_memory 工具精确管理；
        // 此处忽略 consolidation 产出的 updatedPreferences。

        // 4. updatedUnresolved —— 未决并入 memory_fact(type=unresolved, 用户级)，走与事实相同的 (user_id,name) upsert。
        //    未决类别(未答复问题/进行中任务/用户待办)存入 description；name 缺省兜底为 todo-<factId>。
        JSONArray updatedUnresolved = summaryData.getJSONArray("updatedUnresolved");
        if (updatedUnresolved != null && !updatedUnresolved.isEmpty()) {
            int uIns = 0, uUpd = 0, uSkip = 0;
            for (int i = 0; i < updatedUnresolved.size(); i++) {
                JSONObject u = updatedUnresolved.getJSONObject(i);
                MemoryFact mf = new MemoryFact();
                mf.setUserId(userId);
                mf.setSessionId(sessionId);
                mf.setFactId("mem:" + UUID.randomUUID().toString().substring(0, 13));
                mf.setContent(u.getStr("content"));
                mf.setType("unresolved");
                mf.setDescription(u.getStr("type"));   // 未决类别存 description
                mf.setStatus("active");
                mf.setImportance(5);
                mf.setUsageCount(0);
                mf.setSource("consolidation");
                mf.setTurnTs(windowTurnTs);
                String name = u.getStr("name");
                if (name == null || name.isBlank()) {
                    name = "todo-" + mf.getFactId();
                }
                mf.setName(name);
                switch (upsertFactByName(userId, mf)) {
                    case "updated" -> uUpd++;
                    case "skipped" -> uSkip++;
                    default -> uIns++;
                }
            }
            log.info("[MQ结果] 整合未决 upsert 完成, 新增:{} 更新:{} 跳过(已删除):{}", uIns, uUpd, uSkip);
        }

        // 5. resolvedUnresolvedNames —— 整合判定已解决的未决，按 (user_id,name) 软删 type=unresolved 记忆
        JSONArray resolvedNames = summaryData.getJSONArray("resolvedUnresolvedNames");
        if (resolvedNames != null && !resolvedNames.isEmpty()) {
            List<String> names = new ArrayList<>();
            for (int i = 0; i < resolvedNames.size(); i++) {
                String n = resolvedNames.getStr(i);
                if (n != null && !n.isBlank()) names.add(n);
            }
            if (!names.isEmpty()) {
                LambdaUpdateWrapper<MemoryFact> w = new LambdaUpdateWrapper<>();
                w.eq(MemoryFact::getUserId, userId)
                        .eq(MemoryFact::getType, "unresolved")
                        .in(MemoryFact::getName, names)
                        .ne(MemoryFact::getStatus, "deleted")
                        .set(MemoryFact::getStatus, "deleted");
                memoryFactService.update(w);
                log.info("[MQ结果] 整合标记未决已解决(软删), 数量:{}", names.size());
            }
        }

        // 6. 更新briefSummary
        String briefSummary = summaryData.getStr("briefSummary");
        if (briefSummary != null && !briefSummary.isEmpty()) {
            LambdaUpdateWrapper<AiSession> sessionWrapper = new LambdaUpdateWrapper<>();
            sessionWrapper.eq(AiSession::getId, Long.valueOf(sessionId))
                    .set(AiSession::getSummary, briefSummary);
            aiSessionService.update(sessionWrapper);
        }

        // 7. 标记已整合的消息
        @SuppressWarnings("unchecked")
        List<Number> messageIdNums = (List<Number>) data.get("consolidatedMessageIds");
        if (messageIdNums != null && !messageIdNums.isEmpty()) {
            List<Long> messageIds = messageIdNums.stream().map(Number::longValue).collect(Collectors.toList());
            LambdaUpdateWrapper<AiMessage> msgWrapper = new LambdaUpdateWrapper<>();
            msgWrapper.in(AiMessage::getId, messageIds)
                    .set(AiMessage::getConsolidated, 1);
            aiMessageService.update(msgWrapper);
            log.info("[MQ结果] 标记消息为已整合, 数量:{}", messageIds.size());
        }

        log.info("[MQ结果] 记忆整合完成, 会话ID:{}", sessionId);
    }

    @SuppressWarnings("unchecked")
    private void processReflectionResult(Map<String, Object> data, Long userId) {
        List<Map<String, Object>> reflections = (List<Map<String, Object>>) data.get("reflections");
        Number factCountNum = (Number) data.get("factCount");
        int factCount = factCountNum != null ? factCountNum.intValue() : 0;

        if (reflections == null || reflections.isEmpty()) {
            log.info("[MQ结果] 反思结果为空, userId:{}", userId);
            return;
        }

        for (Map<String, Object> r : reflections) {
            String type = (String) r.get("type");
            String content = (String) r.get("content");
            Number confidenceNum = (Number) r.get("confidence");
            double confidence = confidenceNum != null ? confidenceNum.doubleValue() : 0.70;

            // Upsert: 同 userId + type 的旧记录标记为 archived
            LambdaUpdateWrapper<MemoryReflection> archiveWrapper = new LambdaUpdateWrapper<>();
            archiveWrapper.eq(MemoryReflection::getUserId, userId)
                    .eq(MemoryReflection::getReflectionType, type)
                    .eq(MemoryReflection::getStatus, "active")
                    .set(MemoryReflection::getStatus, "archived");
            memoryReflectionService.update(archiveWrapper);

            // 查旧版本号
            LambdaQueryWrapper<MemoryReflection> versionQuery = new LambdaQueryWrapper<>();
            versionQuery.eq(MemoryReflection::getUserId, userId)
                    .eq(MemoryReflection::getReflectionType, type)
                    .orderByDesc(MemoryReflection::getVersion)
                    .last("LIMIT 1");
            MemoryReflection lastVersion = memoryReflectionService.getOne(versionQuery);
            int newVersion = (lastVersion != null && lastVersion.getVersion() != null)
                    ? lastVersion.getVersion() + 1 : 1;

            // 保存新画像
            MemoryReflection reflection = new MemoryReflection();
            reflection.setUserId(userId);
            reflection.setReflectionType(type);
            reflection.setContent(content);
            reflection.setEvidenceFactCount(factCount);
            reflection.setConfidence(confidence);
            reflection.setVersion(newVersion);
            reflection.setStatus("active");
            memoryReflectionService.save(reflection);
        }

        log.info("[MQ结果] 用户画像反思保存完成, userId:{}, 维度数:{}", userId, reflections.size());
    }

    /**
     * 按 (user_id, name) upsert 一条 memory_fact，与对话内 save_memory 收敛同一 name 键空间：
     * 同名 active/superseded → 就地更新并重新激活；同名 deleted → 跳过(不因自动抽取复活)；无同名 → 插入。
     * 调用前实体须已 setName / setFactId。返回 "inserted"/"updated"/"skipped" 供计数。
     */
    private String upsertFactByName(Long userId, MemoryFact mf) {
        String name = mf.getName();
        if (name == null || name.isBlank()) {
            memoryFactService.save(mf);
            return "inserted";
        }
        LambdaQueryWrapper<MemoryFact> dupQuery = new LambdaQueryWrapper<>();
        dupQuery.eq(MemoryFact::getUserId, userId)
                .eq(MemoryFact::getName, name)
                .last("LIMIT 1");
        MemoryFact existing = memoryFactService.getOne(dupQuery);
        if (existing == null) {
            memoryFactService.save(mf);
            return "inserted";
        } else if ("deleted".equals(existing.getStatus())) {
            return "skipped";
        } else {
            // 同轮写仲裁（漏洞#1 1b）：整合较低优先级，若不应覆盖既有(更新的实时写/同轮更高优先级)则跳过
            if (!shouldOverwrite(existing, mf.getTurnTs(), mf.getSource())) {
                return "skipped";
            }
            mf.setId(existing.getId());
            mf.setFactId(existing.getFactId());   // 保留原 factId，维持引用一致
            mf.setCreatedAt(existing.getCreatedAt());
            memoryFactService.updateById(mf);
            return "updated";
        }
    }

    /**
     * 计算本次整合窗口的 turn_ts（毫秒）= 被整合消息中最后一条的创建时间。
     * 取不到（无 consolidatedMessageIds / 查询失败）→ 返回 null；仲裁里 null 视为"最旧"，
     * 即整合不会覆盖任何带真实 turn_ts 的实时写，保守安全。
     */
    private Long computeWindowTurnTs(Map<String, Object> data) {
        try {
            @SuppressWarnings("unchecked")
            List<Number> ids = (List<Number>) data.get("consolidatedMessageIds");
            if (ids == null || ids.isEmpty()) {
                return null;
            }
            List<Long> msgIds = ids.stream().map(Number::longValue).collect(Collectors.toList());
            LambdaQueryWrapper<AiMessage> q = new LambdaQueryWrapper<>();
            q.in(AiMessage::getId, msgIds)
                    .orderByDesc(AiMessage::getCreatedAt)
                    .last("LIMIT 1");
            AiMessage last = aiMessageService.getOne(q);
            if (last == null || last.getCreatedAt() == null) {
                return null;
            }
            return last.getCreatedAt().atZone(java.time.ZoneId.systemDefault()).toInstant().toEpochMilli();
        } catch (Exception e) {
            log.warn("[MQ结果] 计算整合 turn_ts 失败，置空(保守不覆盖实时写): {}", e.getMessage());
            return null;
        }
    }

    /**
     * 同轮写仲裁（漏洞#1）：时间为主裁判、来源仅作同轮 tie-break。
     * turn_ts 更新→覆盖；相同→来源优先级 >= 既有才覆盖；更旧→跳过。
     * （与 MemoryStoreImpl 的实时/兜底仲裁同规则；整合走 MQ 不经 saveMemory，故此处内联一份。）
     */
    private boolean shouldOverwrite(MemoryFact existing, Long incomingTurnTs, String incomingSource) {
        long existingTs = existing.getTurnTs() == null ? Long.MIN_VALUE : existing.getTurnTs();
        long incomingTs = incomingTurnTs == null ? Long.MIN_VALUE : incomingTurnTs;
        if (incomingTs != existingTs) {
            return incomingTs > existingTs;
        }
        return sourcePriority(incomingSource) >= sourcePriority(existing.getSource());
    }

    /** 来源优先级：用户实时 > 整合 > 兜底；未知/老调用方视为最高（保持旧覆盖行为）。 */
    private static int sourcePriority(String source) {
        if (source == null) {
            return Integer.MAX_VALUE;
        }
        return switch (source) {
            case "agent_explicit" -> 3;
            case "consolidation" -> 2;
            case "capture_fallback" -> 1;
            default -> Integer.MAX_VALUE;
        };
    }

    /**
     * 幂等检查：如果消息已处理过则返回 true（跳过），否则插入记录返回 false（继续处理）。
     */
    private boolean isDuplicate(String messageId, String messageType) {
        if (messageId == null || messageId.isEmpty()) {
            return false;
        }
        try {
            MemoryIdempotent existing = idempotentMapper.selectById(messageId);
            if (existing != null) {
                log.warn("[幂等] 消息已处理过, messageId:{}, type:{}", messageId, messageType);
                return true;
            }
            MemoryIdempotent record = new MemoryIdempotent();
            record.setMessageId(messageId);
            record.setMessageType(messageType);
            record.setStatus("processed");
            idempotentMapper.insert(record);
            return false;
        } catch (Exception e) {
            if (e.getMessage() != null && e.getMessage().contains("Duplicate")) {
                log.warn("[幂等] 并发重复消息, messageId:{}", messageId);
                return true;
            }
            log.warn("[幂等] 检查失败（放行处理）: {}", e.getMessage());
            return false;
        }
    }

    /**
     * 从结果 JSON 读取文件式记忆索引字段并 set 到 MemoryFact 实体（Task 4）。
     *
     * 字段来源：
     *   - name/description/type/why：单词字段，camelCase 与 snake_case 同名，直接读
     *   - how_to_apply：兼容 camelCase(howToApply, 整合/HTTP路径) 与 snake_case(how_to_apply, 实时路径)
     *
     * 兜底规则：
     *   - type 为空 → 默认 "project"
     *   - name 为空 → 用已设置的 factId 生成 slug（fact-{factId}），保证 index 模式能按 name 寻址
     *   - why / how_to_apply 可空，null 可接受，直接 set
     *
     * 注意：调用本方法前必须已对实体 setFactId（用于 name 兜底）。
     */
    private void applyIndexFields(MemoryFact memoryFact, JSONObject src) {
        String type = src.getStr("type");
        memoryFact.setType((type != null && !type.isBlank()) ? type : "project");

        memoryFact.setDescription(src.getStr("description"));
        memoryFact.setWhy(src.getStr("why"));
        // how_to_apply 兼容两种命名（整合路径 camelCase / 实时路径 snake_case）
        String howToApply = src.getStr("howToApply");
        if (howToApply == null) {
            howToApply = src.getStr("how_to_apply");
        }
        memoryFact.setHowToApply(howToApply);

        // name 兜底：保证非空，index 模式靠 name 寻址
        String name = src.getStr("name");
        if (name == null || name.isBlank()) {
            String factId = memoryFact.getFactId();
            name = "fact-" + ((factId != null && !factId.isEmpty()) ? factId : UUID.randomUUID().toString());
        }
        memoryFact.setName(name);
    }
}
