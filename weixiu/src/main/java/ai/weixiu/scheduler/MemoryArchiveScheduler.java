package ai.weixiu.scheduler;

import ai.weixiu.entity.MemoryFact;
import ai.weixiu.entity.MemoryIdempotent;
import ai.weixiu.mapper.MemoryIdempotentMapper;
import ai.weixiu.service.MemoryFactService;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import lombok.AllArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;
import java.util.List;

/**
 * 记忆归档调度器。
 *
 * 每天凌晨 3:00 执行：
 * 1. 归档 90 天未使用 + 重要度 ≤ 3 + 置信度 < 0.6 的 active 事实
 * 2. 清理 7 天前的幂等表记录
 * 3. 清理 180 天前的 superseded 事实（物理删除）
 */
@Component
@AllArgsConstructor
@Slf4j
public class MemoryArchiveScheduler {

    private final MemoryFactService memoryFactService;
    private final MemoryIdempotentMapper idempotentMapper;

    @Scheduled(cron = "0 0 3 * * ?")
    public void archiveAndCleanup() {
        log.info("[归档调度] 开始执行记忆归档和清理...");

        int archived = archiveStaleActiveFacts();
        int idempotentCleaned = cleanIdempotentTable();
        int supersededCleaned = cleanOldSupersededFacts();

        log.info("[归档调度] 完成: 归档事实={}, 清理幂等记录={}, 清理过时事实={}",
                archived, idempotentCleaned, supersededCleaned);
    }

    /**
     * 归档条件：active + 90天未使用 + 重要度≤3 + 置信度<0.6
     * 同时满足才归档，避免误归档重要但低频的事实。
     */
    private int archiveStaleActiveFacts() {
        try {
            LocalDateTime threshold = LocalDateTime.now().minusDays(90);

            LambdaUpdateWrapper<MemoryFact> wrapper = new LambdaUpdateWrapper<>();
            wrapper.eq(MemoryFact::getStatus, "active")
                    .le(MemoryFact::getImportance, 3)
                    .lt(MemoryFact::getConfidence, 0.6)
                    .and(w -> w.isNull(MemoryFact::getLastUsedAt)
                            .or()
                            .lt(MemoryFact::getLastUsedAt, threshold))
                    .set(MemoryFact::getStatus, "archived");

            boolean updated = memoryFactService.update(wrapper);
            if (updated) {
                log.info("[归档] 已归档低价值过期事实");
            }
            return updated ? 1 : 0;
        } catch (Exception e) {
            log.error("[归档] 归档事实失败: {}", e.getMessage());
            return 0;
        }
    }

    /** 清理 7 天前的幂等记录 */
    private int cleanIdempotentTable() {
        try {
            LambdaQueryWrapper<MemoryIdempotent> wrapper = new LambdaQueryWrapper<>();
            wrapper.lt(MemoryIdempotent::getCreatedAt, LocalDateTime.now().minusDays(7));
            List<MemoryIdempotent> old = idempotentMapper.selectList(wrapper);
            if (!old.isEmpty()) {
                List<String> ids = old.stream().map(MemoryIdempotent::getMessageId).toList();
                idempotentMapper.deleteBatchIds(ids);
                log.info("[归档] 清理过期幂等记录: {}条", ids.size());
                return ids.size();
            }
            return 0;
        } catch (Exception e) {
            log.error("[归档] 清理幂等表失败: {}", e.getMessage());
            return 0;
        }
    }

    /** 物理删除 180 天前的 superseded 事实 */
    private int cleanOldSupersededFacts() {
        try {
            LambdaQueryWrapper<MemoryFact> wrapper = new LambdaQueryWrapper<>();
            wrapper.eq(MemoryFact::getStatus, "superseded")
                    .lt(MemoryFact::getSupersededAt, LocalDateTime.now().minusDays(180));
            List<MemoryFact> old = memoryFactService.list(wrapper);
            if (!old.isEmpty()) {
                List<Long> ids = old.stream().map(MemoryFact::getId).toList();
                memoryFactService.removeByIds(ids);
                log.info("[归档] 物理删除过时事实: {}条", ids.size());
                return ids.size();
            }
            return 0;
        } catch (Exception e) {
            log.error("[归档] 清理过时事实失败: {}", e.getMessage());
            return 0;
        }
    }
}
