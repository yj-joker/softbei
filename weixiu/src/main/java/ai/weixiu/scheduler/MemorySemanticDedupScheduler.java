package ai.weixiu.scheduler;

import ai.weixiu.entity.MemoryDedupState;
import ai.weixiu.entity.MemoryFact;
import ai.weixiu.mapper.MemoryDedupStateMapper;
import ai.weixiu.service.MemoryFactService;
import cn.hutool.json.JSONArray;
import cn.hutool.json.JSONObject;
import cn.hutool.json.JSONUtil;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import lombok.AllArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * 记忆语义去重调度器（漏洞#2 离线 pass）。
 *
 * <p>每天 03:30（避开 03:00 的归档调度）：对"自上次去重以来新增活跃事实 &gt; 20"的用户，
 * 拉其活跃事实交给 Python /ai/memory/dedup 找"真正重复"分组，把非代表条标 superseded。</p>
 *
 * <p>设计取向：只并真重复、保守、superseded 不物理删（留 180 天恢复窗口，由 {@code
 * MemoryArchiveScheduler} 物理清理）。暂不加分布式锁（当前单机；多实例时再统一给各调度器加 ShedLock）。</p>
 */
@Component
@AllArgsConstructor
@Slf4j
public class MemorySemanticDedupScheduler {

    /** 自上次去重以来新增活跃事实数 &gt; 此阈值才跑（避免少量新增也烧 LLM）。 */
    private static final int NEW_FACT_THRESHOLD = 20;
    /** 不参与语义去重的类型：用户画像/未决（name 稳定、各自专管，语义合并有风险）。 */
    private static final List<String> EXCLUDED_TYPES = List.of("user", "unresolved");
    /** 单用户一次最多送多少条给 LLM，防超长。 */
    private static final int MAX_FACTS_PER_USER = 200;

    private final MemoryFactService memoryFactService;
    private final MemoryDedupStateMapper dedupStateMapper;
    private final WebClient webClient;

    @Scheduled(cron = "0 30 3 * * ?")
    public void semanticDedup() {
        log.info("[语义去重] 开始...");
        int eligibleUsers = 0;
        int supersededTotal = 0;
        try {
            for (Long userId : activeUserIds()) {
                try {
                    LocalDateTime lastDedupAt = lastDedupAt(userId);
                    long newCount = countNewActiveFacts(userId, lastDedupAt);
                    if (newCount <= NEW_FACT_THRESHOLD) {
                        continue;
                    }
                    eligibleUsers++;
                    supersededTotal += dedupOneUser(userId);
                    touchDedupState(userId);
                } catch (Exception e) {
                    log.warn("[语义去重] 用户 {} 处理失败: {}", userId, e.getMessage());
                }
            }
        } catch (Exception e) {
            log.error("[语义去重] 调度异常: {}", e.getMessage(), e);
        }
        log.info("[语义去重] 完成: 命中用户={}, 合并(superseded)={}", eligibleUsers, supersededTotal);
    }

    /** 有活跃可去重事实的用户ID（排除 user/unresolved 类型）。 */
    private List<Long> activeUserIds() {
        LambdaQueryWrapper<MemoryFact> w = new LambdaQueryWrapper<>();
        w.select(MemoryFact::getUserId)
                .eq(MemoryFact::getStatus, "active")
                .notIn(MemoryFact::getType, EXCLUDED_TYPES)
                .groupBy(MemoryFact::getUserId);
        List<Object> objs = memoryFactService.listObjs(w);
        List<Long> ids = new ArrayList<>();
        for (Object o : objs) {
            if (o instanceof Number n) {
                ids.add(n.longValue());
            }
        }
        return ids;
    }

    private LocalDateTime lastDedupAt(Long userId) {
        MemoryDedupState s = dedupStateMapper.selectById(userId);
        return s == null ? null : s.getLastDedupAt();
    }

    private long countNewActiveFacts(Long userId, LocalDateTime lastDedupAt) {
        LambdaQueryWrapper<MemoryFact> w = new LambdaQueryWrapper<>();
        w.eq(MemoryFact::getUserId, userId)
                .eq(MemoryFact::getStatus, "active")
                .notIn(MemoryFact::getType, EXCLUDED_TYPES);
        if (lastDedupAt != null) {
            w.gt(MemoryFact::getCreatedAt, lastDedupAt);
        }
        return memoryFactService.count(w);
    }

    /** 拉用户活跃事实 → 调 Python 出合并方案 → 把 drop 标 superseded。返回 superseded 条数。 */
    private int dedupOneUser(Long userId) {
        LambdaQueryWrapper<MemoryFact> w = new LambdaQueryWrapper<>();
        w.eq(MemoryFact::getUserId, userId)
                .eq(MemoryFact::getStatus, "active")
                .notIn(MemoryFact::getType, EXCLUDED_TYPES)
                .orderByDesc(MemoryFact::getImportance)
                .last("LIMIT " + MAX_FACTS_PER_USER);
        List<MemoryFact> facts = memoryFactService.list(w);
        if (facts == null || facts.size() < 2) {
            return 0;
        }

        List<Map<String, Object>> factPayload = new ArrayList<>();
        for (MemoryFact f : facts) {
            if (f.getName() == null || f.getName().isBlank()) {
                continue;
            }
            Map<String, Object> m = new HashMap<>();
            m.put("name", f.getName());
            m.put("description", f.getDescription());
            m.put("content", f.getContent());
            m.put("type", f.getType());
            m.put("importance", f.getImportance());
            m.put("turn_ts", f.getTurnTs());
            factPayload.add(m);
        }
        if (factPayload.size() < 2) {
            return 0;
        }

        Map<String, Object> body = new HashMap<>();
        body.put("user_id", String.valueOf(userId));
        body.put("facts", factPayload);

        String resp;
        try {
            resp = webClient.post()
                    .uri("/ai/memory/dedup")
                    .bodyValue(body)
                    .retrieve()
                    .bodyToMono(String.class)
                    .block();
        } catch (Exception e) {
            log.warn("[语义去重] 调用 Python /ai/memory/dedup 失败 user={}: {}", userId, e.getMessage());
            return 0;
        }
        if (resp == null || resp.isBlank()) {
            return 0;
        }

        JSONObject obj = JSONUtil.parseObj(resp);
        JSONArray groups = obj.getJSONArray("groups");
        if (groups == null || groups.isEmpty()) {
            return 0;
        }

        int superseded = 0;
        for (int i = 0; i < groups.size(); i++) {
            JSONObject g = groups.getJSONObject(i);
            String keep = g.getStr("keep");
            JSONArray dropArr = g.getJSONArray("drop");
            if (keep == null || keep.isBlank() || dropArr == null || dropArr.isEmpty()) {
                continue;
            }
            List<String> dropNames = new ArrayList<>();
            for (int j = 0; j < dropArr.size(); j++) {
                String d = dropArr.getStr(j);
                if (d != null && !d.isBlank() && !d.equals(keep)) {
                    dropNames.add(d);
                }
            }
            if (dropNames.isEmpty()) {
                continue;
            }
            // 把被并条标 superseded（不物理删，留恢复窗口；只动该用户、仍 active 的）
            LambdaUpdateWrapper<MemoryFact> uw = new LambdaUpdateWrapper<>();
            uw.eq(MemoryFact::getUserId, userId)
                    .in(MemoryFact::getName, dropNames)
                    .eq(MemoryFact::getStatus, "active")
                    .set(MemoryFact::getStatus, "superseded")
                    .set(MemoryFact::getSupersededAt, LocalDateTime.now());
            memoryFactService.update(uw);
            superseded += dropNames.size();
            log.info("[语义去重] user={} 合并: keep={} drop={}", userId, keep, dropNames);
        }
        return superseded;
    }

    /** 更新该用户 last_dedup_at=now（无论是否有合并，避免下次对同批未变事实重复跑）。 */
    private void touchDedupState(Long userId) {
        MemoryDedupState s = dedupStateMapper.selectById(userId);
        if (s == null) {
            dedupStateMapper.insert(new MemoryDedupState().setUserId(userId).setLastDedupAt(LocalDateTime.now()));
        } else {
            s.setLastDedupAt(LocalDateTime.now());
            dedupStateMapper.updateById(s);
        }
    }
}
