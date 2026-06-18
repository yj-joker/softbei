package ai.weixiu.scheduler;

import ai.weixiu.entity.MemoryFact;
import ai.weixiu.entity.MemoryReflection;
import ai.weixiu.mq.MemoryMessageProducer;
import ai.weixiu.service.MemoryFactService;
import ai.weixiu.service.MemoryReflectionService;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import lombok.AllArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * 反思定时触发器。
 *
 * 每 2 小时扫描一次，找出新增事实数 ≥ 10 且最近 24 小时内未反思的用户，
 * 发 MQ 触发 Python 端 MemoryReflectionAgent。
 */
@Component
@AllArgsConstructor
@Slf4j
public class ReflectionScheduler {

    private final MemoryFactService memoryFactService;
    private final MemoryReflectionService memoryReflectionService;
    private final MemoryMessageProducer memoryMessageProducer;

    /** 触发反思所需的最小新事实数 */
    private static final int MIN_FACTS_FOR_REFLECTION = 10;

    @Scheduled(fixedDelay = 2 * 3600 * 1000, initialDelay = 300_000)
    public void checkAndTriggerReflection() {
        log.info("[反思调度] 开始检查是否需要触发用户画像反思...");

        try {
            // 找出所有有 active 事实的不同用户
            LambdaQueryWrapper<MemoryFact> factQuery = new LambdaQueryWrapper<>();
            factQuery.eq(MemoryFact::getStatus, "active")
                    .ne(MemoryFact::getType, "user")   // 偏好不计入画像触发
                    .isNotNull(MemoryFact::getUserId)
                    .select(MemoryFact::getUserId);
            List<MemoryFact> facts = memoryFactService.list(factQuery);

            Set<Long> processedUsers = new HashSet<>();
            for (MemoryFact fact : facts) {
                if (fact.getUserId() != null) {
                    processedUsers.add(fact.getUserId());
                }
            }

            for (Long userId : processedUsers) {
                // 统计该用户 active 事实总数
                LambdaQueryWrapper<MemoryFact> countQuery = new LambdaQueryWrapper<>();
                countQuery.eq(MemoryFact::getUserId, userId)
                        .eq(MemoryFact::getStatus, "active")
                        .ne(MemoryFact::getType, "user");   // 偏好不计入 ≥10 阈值
                long factCount = memoryFactService.count(countQuery);

                if (factCount < MIN_FACTS_FOR_REFLECTION) {
                    continue;
                }

                // 检查最近 24 小时是否已有反思
                LambdaQueryWrapper<MemoryReflection> reflectionQuery = new LambdaQueryWrapper<>();
                reflectionQuery.eq(MemoryReflection::getUserId, userId)
                        .ge(MemoryReflection::getUpdatedAt, LocalDateTime.now().minusHours(24));
                long recentReflections = memoryReflectionService.count(reflectionQuery);

                if (recentReflections > 0) {
                    continue;
                }

                // 触发反思
                memoryMessageProducer.sendReflection(userId, (int) factCount);
                log.info("[反思调度] 触发用户画像反思, userId:{}, factCount:{}", userId, factCount);
            }
        } catch (Exception e) {
            log.error("[反思调度] 检查失败: {}", e.getMessage(), e);
        }
    }
}
