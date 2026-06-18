package ai.weixiu.service.impl;

import ai.weixiu.common.RedisKey;
import ai.weixiu.entity.AiSession;
import ai.weixiu.entity.MemoryFact;
import ai.weixiu.entity.MemoryPreference;
import ai.weixiu.entity.MemoryRecallTrace;
import ai.weixiu.entity.MemoryReflection;
import ai.weixiu.entity.MemoryUnresolved;
import ai.weixiu.enumerate.PreferenceCategoryEnum;
import ai.weixiu.mapper.MemoryRecallTraceMapper;
import ai.weixiu.pojo.dto.RecallContext;
import ai.weixiu.service.AiSessionService;
import ai.weixiu.service.MemoryFactService;
import ai.weixiu.service.MemoryPreferenceService;
import ai.weixiu.service.MemoryRecallService;
import ai.weixiu.service.MemoryReflectionService;
import ai.weixiu.service.MemoryStore;
import ai.weixiu.service.MemoryUnresolvedService;
import cn.hutool.json.JSONObject;
import cn.hutool.json.JSONUtil;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.redis.core.RedisTemplate;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;

/**
 * 记忆召回服务实现
 *
 * <p>从 AiServiceImpl 中抽取的 searchRelevantFacts + loadPreferences + getUnresolved 逻辑，
 * 统一入口、统一出口，并在每次召回后异步记录 trace。</p>
 */
@Service
@RequiredArgsConstructor
@Slf4j
public class MemoryRecallServiceImpl implements MemoryRecallService {

    private final AiSessionService aiSessionService;
    private final MemoryFactService memoryFactService;
    private final MemoryPreferenceService memoryPreferenceService;
    private final MemoryReflectionService memoryReflectionService;
    private final MemoryUnresolvedService memoryUnresolvedService;
    private final RedisTemplate<String, Object> redisTemplate;
    private final MemoryRecallTraceMapper recallTraceMapper;
    private final MemoryStore memoryStore;

    @Override
    public RecallContext recall(Long sessionId, Long userId, String userMessage, Integer roundNo,
                                String deviceType, String equipmentId, String siteId, String taskId) {
        long totalStart = System.currentTimeMillis();
        RecallContext ctx = new RecallContext();

        // ========== 1. 获取上一轮摘要 ==========
        AiSession session = aiSessionService.getById(sessionId);
        ctx.setPreviousSummary(session != null ? session.getSummary() : null);

        // ========== 2. 事实召回：文件式索引注入（已彻底去向量）==========
        // 事实改由「索引常驻 + LLM 按需 read_memory 懒加载」承载，注入记忆目录；
        // relevantFacts 不再走向量召回，恒为空。偏好/未决/画像仍并行查询。
        long factStart = System.currentTimeMillis();
        ctx.setMemoryIndex(memoryStore.loadIndex(userId));
        List<JSONObject> relevantFacts = new ArrayList<>();

        long prefStart = System.currentTimeMillis();
        CompletableFuture<List<MemoryPreference>> preferencesFuture =
                CompletableFuture.supplyAsync(() -> loadPreferences(sessionId, userId));

        CompletableFuture<List<MemoryUnresolved>> unresolvedFuture =
                CompletableFuture.supplyAsync(() -> memoryUnresolvedService.getUnresolvedByUser(userId));

        CompletableFuture<List<MemoryReflection>> reflectionsFuture =
                CompletableFuture.supplyAsync(() -> memoryReflectionService.getActiveReflections(userId));

        CompletableFuture.allOf(preferencesFuture, unresolvedFuture, reflectionsFuture).join();

        long factEnd = System.currentTimeMillis();

        List<MemoryPreference> preferences = preferencesFuture.join();
        long prefEnd = System.currentTimeMillis();

        List<MemoryUnresolved> unresolved = unresolvedFuture.join();

        List<MemoryReflection> reflections = reflectionsFuture.join();

        // ========== 3. 填充 RecallContext ==========
        ctx.setRelevantFacts(relevantFacts);
        ctx.setPreferences(preferences);
        ctx.setUnresolvedItems(unresolved);

        // 填充用户画像
        List<Map<String, String>> profileItems = new ArrayList<>();
        for (MemoryReflection r : reflections) {
            Map<String, String> item = new HashMap<>();
            item.put("type", r.getReflectionType());
            item.put("content", r.getContent());
            profileItems.add(item);
        }
        ctx.setUserProfile(profileItems);

        // 提取事实内容（供 MQ recentFacts）
        List<String> factContents = new ArrayList<>();
        List<String> factIds = new ArrayList<>();
        List<Double> factScores = new ArrayList<>();
        for (JSONObject fact : relevantFacts) {
            String content = fact.getStr("content");
            if (content != null && !content.isEmpty()) {
                factContents.add(content);
            }
            factIds.add(fact.getStr("doc_id", ""));
            factScores.add(fact.getDouble("score", 0.0));
        }
        ctx.setRecentFactContents(factContents);
        ctx.setFactIds(factIds);
        ctx.setFactScores(factScores);

        long totalEnd = System.currentTimeMillis();
        ctx.setTotalLatencyMs(totalEnd - totalStart);
        ctx.setFactLatencyMs(factEnd - factStart);
        ctx.setPreferenceLatencyMs(prefEnd - prefStart);

        // ========== 4. 异步保存 Trace ==========
        CompletableFuture.runAsync(() -> saveTrace(sessionId, userId, roundNo, userMessage, ctx));

        // ========== 5. 异步更新事实使用统计 ==========
        if (!factIds.isEmpty()) {
            CompletableFuture.runAsync(() -> updateFactUsage(factIds));
        }

        return ctx;
    }

    // ==================== 从 AiServiceImpl 迁移的私有方法 ====================

    /**
     * 加载偏好。偏好已并入 memory_fact(type=user)，由 LLM 工具精确增删；
     * 每轮读最新、不再缓存，避免 LLM 直写后缓存脏读。
     */
    private List<MemoryPreference> loadPreferences(Long sessionId, Long userId) {
        return memoryPreferenceService.getPreference(sessionId, userId);
    }

    /**
     * 异步保存召回 trace 到数据库。
     */
    private void saveTrace(Long sessionId, Long userId, Integer roundNo, String userMessage, RecallContext ctx) {
        try {
            MemoryRecallTrace trace = new MemoryRecallTrace();
            trace.setSessionId(sessionId);
            trace.setUserId(userId);
            trace.setRoundNo(roundNo);
            trace.setQueryText(userMessage != null && userMessage.length() > 500
                    ? userMessage.substring(0, 500) : userMessage);
            trace.setFactCount(ctx.getRelevantFacts().size());
            trace.setFactIds(JSONUtil.toJsonStr(ctx.getFactIds()));
            trace.setFactScores(JSONUtil.toJsonStr(ctx.getFactScores()));
            trace.setFactContents(JSONUtil.toJsonStr(ctx.getRecentFactContents()));
            trace.setPreferenceCount(ctx.getPreferences().size());
            trace.setUnresolvedCount(ctx.getUnresolvedItems().size());
            trace.setHasSummary(ctx.getPreviousSummary() != null && !ctx.getPreviousSummary().isEmpty());
            trace.setTotalLatencyMs((int) ctx.getTotalLatencyMs());
            trace.setFactLatencyMs((int) ctx.getFactLatencyMs());
            trace.setPreferenceLatencyMs((int) ctx.getPreferenceLatencyMs());

            recallTraceMapper.insert(trace);
        } catch (Exception e) {
            log.warn("保存召回trace失败（不影响主流程）: {}", e.getMessage());
        }
    }

    /**
     * 更新被召回事实的使用统计（last_used_at + usage_count++）
     */
    private void updateFactUsage(List<String> factIds) {
        try {
            List<String> validIds = factIds.stream()
                    .filter(id -> id != null && !id.isEmpty())
                    .toList();
            if (validIds.isEmpty()) return;

            LambdaUpdateWrapper<MemoryFact> wrapper = new LambdaUpdateWrapper<>();
            wrapper.in(MemoryFact::getFactId, validIds)
                    .set(MemoryFact::getLastUsedAt, LocalDateTime.now())
                    .setSql("usage_count = usage_count + 1");
            memoryFactService.update(wrapper);
            log.debug("更新事实使用统计, 数量:{}", validIds.size());
        } catch (Exception e) {
            log.warn("更新事实使用统计失败（不影响主流程）: {}", e.getMessage());
        }
    }
}
