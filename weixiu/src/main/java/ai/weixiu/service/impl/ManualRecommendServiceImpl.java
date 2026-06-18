package ai.weixiu.service.impl;

import ai.weixiu.common.RedisKey;
import ai.weixiu.entity.KnowledgeDocument;
import ai.weixiu.entity.MaintenanceManual;
import ai.weixiu.entity.MaintenanceTask;
import ai.weixiu.entity.ManualDevice;
import ai.weixiu.pojo.vo.ManualRecommendVO;
import ai.weixiu.mapper.ManualDeviceMapper;
import ai.weixiu.mapper.MaintenanceManualMapper;
import ai.weixiu.mapper.MaintenanceTaskMapper;
import ai.weixiu.repository.DeviceRepository;
import ai.weixiu.service.KnowledgeDocumentService;
import ai.weixiu.service.ManualRecommendService;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.redis.core.RedisTemplate;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

import java.time.LocalDateTime;
import java.util.*;
import java.util.concurrent.TimeUnit;
import java.util.stream.Collectors;

/**
 * 维修手册个性化推荐服务实现（基于设备关联链）
 *
 * <h3>推荐算法</h3>
 * <p>核心逻辑：工人 → 检修任务 → 设备 → 手册</p>
 * <ol>
 *   <li><b>直接关联（权重 3.0）</b>——工人历史检修过的设备，关联的手册</li>
 *   <li><b>同场地扩展（权重 1.5）</b>——工人接触过的设备所在场地的其他设备，关联的手册</li>
 *   <li><b>时效加分（权重 1.0）</b>——最近 7 天内更新的手册额外加分</li>
 *   <li><b>兜底</b>——推荐数不足时用最新手册补齐</li>
 * </ol>
 *
 * <h3>缓存策略</h3>
 * <ul>
 *   <li>缓存 key：{@code Recommend:Manual:{userId}}，TTL 2 小时</li>
 *   <li>刷新时机：每次对话完成后异步刷新、偏好变更时清除</li>
 * </ul>
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class ManualRecommendServiceImpl implements ManualRecommendService {

    private final MaintenanceTaskMapper taskMapper;
    private final ManualDeviceMapper manualDeviceMapper;
    private final MaintenanceManualMapper manualMapper;
    private final DeviceRepository deviceRepository;
    private final RedisTemplate<String, Object> redisTemplate;
    private final KnowledgeDocumentService knowledgeDocumentService;

    /** 推荐缓存 TTL：2小时 */
    private static final long CACHE_TTL_HOURS = 2;

    /** 刷新冷却时间：30分钟内不重复刷新 */
    private static final long REFRESH_COOLDOWN_MINUTES = 30;

    /** 刷新冷却 key 前缀 */
    private static final String REFRESH_COOLDOWN_KEY = "Recommend:Manual:Cooldown:";

    /** 查询工人最近 N 个月的检修任务 */
    private static final int TASK_HISTORY_MONTHS = 6;

    @Override
    @SuppressWarnings("unchecked")
    public List<ManualRecommendVO> getRecommendations(Long userId, int limit) {
        // 1. 优先查缓存
        String cacheKey = RedisKey.MANUAL_RECOMMEND + userId;
        Object cached = redisTemplate.opsForValue().get(cacheKey);
        if (cached instanceof List) {
            List<ManualRecommendVO> cachedList = (List<ManualRecommendVO>) cached;
            log.debug("推荐缓存命中, userId={}, 数量={}", userId, cachedList.size());
            return cachedList.stream().limit(limit).collect(Collectors.toList());
        }

        // 2. 缓存未命中，实时计算
        List<ManualRecommendVO> recommendations = computeRecommendations(userId, limit);

        // 3. 写入缓存
        redisTemplate.opsForValue().set(cacheKey, recommendations, CACHE_TTL_HOURS, TimeUnit.HOURS);

        return recommendations;
    }

    @Override
    @Async
    public void refreshAsync(Long userId) {
        try {
            String cacheKey = RedisKey.MANUAL_RECOMMEND + userId;

            if (Boolean.FALSE.equals(redisTemplate.hasKey(cacheKey))) {
                log.debug("推荐缓存不存在，跳过刷新, userId={}", userId);
                return;
            }

            String cooldownKey = REFRESH_COOLDOWN_KEY + userId;
            Boolean acquired = redisTemplate.opsForValue()
                    .setIfAbsent(cooldownKey, "1", REFRESH_COOLDOWN_MINUTES, TimeUnit.MINUTES);
            if (Boolean.FALSE.equals(acquired)) {
                log.debug("推荐刷新冷却中，跳过, userId={}", userId);
                return;
            }

            List<ManualRecommendVO> recommendations = computeRecommendations(userId, 10);
            redisTemplate.opsForValue().set(cacheKey, recommendations, CACHE_TTL_HOURS, TimeUnit.HOURS);
            log.info("推荐缓存异步刷新完成, userId={}, 推荐数={}", userId, recommendations.size());
        } catch (Exception e) {
            log.warn("推荐缓存刷新失败, userId={}, error={}", userId, e.getMessage());
        }
    }

    @Override
    public void invalidateCache(Long userId) {
        String cacheKey = RedisKey.MANUAL_RECOMMEND + userId;
        String cooldownKey = REFRESH_COOLDOWN_KEY + userId;
        redisTemplate.delete(List.of(cacheKey, cooldownKey));
        log.info("推荐缓存已清除, userId={}", userId);
    }

    // ==================== 核心推荐计算 ====================

    private List<ManualRecommendVO> computeRecommendations(Long userId, int limit) {
        long start = System.currentTimeMillis();

        // 评分表：manualId → ScoredManual
        Map<Long, ScoredManual> scoreMap = new LinkedHashMap<>();

        // ---- 第一层：工人历史检修设备 → 直接关联手册（权重 3.0）----
        Set<String> workerDeviceIds = getWorkerDeviceIds(userId);

        if (!workerDeviceIds.isEmpty()) {
            // 查 manual_device 表：这些设备关联了哪些手册
            List<ManualDevice> directLinks = manualDeviceMapper.selectList(
                    Wrappers.<ManualDevice>lambdaQuery()
                            .in(ManualDevice::getDeviceId, workerDeviceIds));

            Set<Long> directManualIds = new HashSet<>();
            // deviceId → deviceName 映射（从 manual_device 的冗余字段）
            Map<Long, List<String>> manualDeviceNames = new HashMap<>();

            for (ManualDevice md : directLinks) {
                directManualIds.add(md.getManualId());
                manualDeviceNames.computeIfAbsent(md.getManualId(), k -> new ArrayList<>())
                        .add(md.getDeviceName() != null ? md.getDeviceName() : md.getDeviceId());
            }

            if (!directManualIds.isEmpty()) {
                List<MaintenanceManual> directManuals = manualMapper.selectList(
                        Wrappers.<MaintenanceManual>lambdaQuery()
                                .in(MaintenanceManual::getId, directManualIds)
                                .eq(MaintenanceManual::getStatus, 1));

                for (MaintenanceManual manual : directManuals) {
                    ScoredManual scored = scoreMap.computeIfAbsent(manual.getId(),
                            k -> new ScoredManual(manual));
                    scored.totalScore += 3.0;

                    List<String> names = manualDeviceNames.getOrDefault(manual.getId(), List.of());
                    String deviceDesc = names.size() <= 2
                            ? String.join("、", names)
                            : names.get(0) + "等" + names.size() + "台设备";
                    scored.reasons.add("你检修过的设备「" + deviceDesc + "」适用");
                }
            }
        }

        // ---- 第二层：同场地扩展（权重 1.5）----
        if (!workerDeviceIds.isEmpty()) {
            try {
                // 查工人接触过的设备所在的场地
                List<DeviceRepository.DeviceLocationProjection> deviceLocations =
                        deviceRepository.findLocationsByDeviceIds(new ArrayList<>(workerDeviceIds));

                Set<String> locations = deviceLocations.stream()
                        .map(DeviceRepository.DeviceLocationProjection::getLocation)
                        .filter(StringUtils::hasText)
                        .collect(Collectors.toSet());

                if (!locations.isEmpty()) {
                    // 查同场地的所有设备 ID
                    Set<String> sameLocationDeviceIds = new HashSet<>();
                    for (String location : locations) {
                        List<String> ids = deviceRepository.findDeviceIdsByLocation(location);
                        sameLocationDeviceIds.addAll(ids);
                    }
                    // 排除已经直接关联过的设备
                    sameLocationDeviceIds.removeAll(workerDeviceIds);

                    if (!sameLocationDeviceIds.isEmpty()) {
                        List<ManualDevice> locationLinks = manualDeviceMapper.selectList(
                                Wrappers.<ManualDevice>lambdaQuery()
                                        .in(ManualDevice::getDeviceId, sameLocationDeviceIds));

                        Set<Long> locationManualIds = locationLinks.stream()
                                .map(ManualDevice::getManualId)
                                .filter(id -> !scoreMap.containsKey(id)) // 排除已经直接关联的
                                .collect(Collectors.toSet());

                        if (!locationManualIds.isEmpty()) {
                            List<MaintenanceManual> locationManuals = manualMapper.selectList(
                                    Wrappers.<MaintenanceManual>lambdaQuery()
                                            .in(MaintenanceManual::getId, locationManualIds)
                                            .eq(MaintenanceManual::getStatus, 1));

                            for (MaintenanceManual manual : locationManuals) {
                                ScoredManual scored = scoreMap.computeIfAbsent(manual.getId(),
                                        k -> new ScoredManual(manual));
                                scored.totalScore += 1.5;
                                scored.reasons.add("你所在场地的相关设备适用");
                            }
                        }
                    }
                }
            } catch (Exception e) {
                // Neo4j 查询异常不应影响整个推荐
                log.warn("同场地扩展推荐查询失败: {}", e.getMessage());
            }
        }

        // ---- 第三层：时效加分（7 天内新增/更新的手册 +1.0）----
        LocalDateTime sevenDaysAgo = LocalDateTime.now().minusDays(7);
        for (ScoredManual scored : scoreMap.values()) {
            LocalDateTime updatedAt = scored.manual.getUpdatedAt();
            if (updatedAt != null && updatedAt.isAfter(sevenDaysAgo)) {
                scored.totalScore += 1.0;
                scored.reasons.add("近期更新");
            }
        }

        // ---- 排序 + 截取 ----
        List<ManualRecommendVO> result = scoreMap.values().stream()
                .sorted(Comparator.comparingDouble((ScoredManual s) -> s.totalScore).reversed())
                .limit(limit)
                .map(this::toVO)
                .collect(Collectors.toList());

        // ---- 兜底：推荐数不够时用最新手册补齐 ----
        if (result.size() < limit) {
            Set<Long> existingIds = result.stream()
                    .map(ManualRecommendVO::getId)
                    .collect(Collectors.toSet());

            List<MaintenanceManual> latest = manualMapper.selectList(
                    Wrappers.<MaintenanceManual>lambdaQuery()
                            .eq(MaintenanceManual::getStatus, 1)
                            .notIn(!existingIds.isEmpty(), MaintenanceManual::getId, existingIds)
                            .orderByDesc(MaintenanceManual::getCreatedAt)
                            .last("LIMIT " + (limit - result.size())));

            for (MaintenanceManual manual : latest) {
                result.add(buildFallbackVO(manual));
            }
        }

        log.info("推荐计算完成, userId={}, 工人设备数={}, 推荐数={}, 耗时={}ms",
                userId, workerDeviceIds.size(), result.size(), System.currentTimeMillis() - start);

        return result;
    }

    // ==================== 工人设备关系 ====================

    /**
     * 查询工人最近 N 个月检修过的设备 ID 集合。
     * 来源：maintenance_task 表中 reporter_id = userId 的任务的 device_id。
     */
    private Set<String> getWorkerDeviceIds(Long userId) {
        LocalDateTime since = LocalDateTime.now().minusMonths(TASK_HISTORY_MONTHS);

        List<MaintenanceTask> tasks = taskMapper.selectList(
                Wrappers.<MaintenanceTask>lambdaQuery()
                        .eq(MaintenanceTask::getReporterId, userId)
                        .ge(MaintenanceTask::getCreatedAt, since)
                        .isNotNull(MaintenanceTask::getDeviceId)
                        .select(MaintenanceTask::getDeviceId));

        return tasks.stream()
                .map(MaintenanceTask::getDeviceId)
                .filter(StringUtils::hasText)
                .collect(Collectors.toSet());
    }

    // ==================== VO 构建 ====================

    private ManualRecommendVO toVO(ScoredManual scored) {
        ManualRecommendVO vo = new ManualRecommendVO();
        MaintenanceManual m = scored.manual;
        vo.setId(m.getId());
        vo.setManualName(m.getManualName());
        vo.setManualDesc(m.getManualDesc());
        vo.setManualImage(m.getManualImage());
        vo.setCreatedAt(m.getCreatedAt());
        vo.setScore(Math.round(scored.totalScore * 100.0) / 100.0);
        vo.setReason(buildReasonText(scored.reasons));
        fillFileInfoFromActiveDoc(vo, m);
        return vo;
    }

    private ManualRecommendVO buildFallbackVO(MaintenanceManual manual) {
        ManualRecommendVO vo = new ManualRecommendVO();
        vo.setId(manual.getId());
        vo.setManualName(manual.getManualName());
        vo.setManualDesc(manual.getManualDesc());
        vo.setManualImage(manual.getManualImage());
        vo.setCreatedAt(manual.getCreatedAt());
        vo.setScore(0);
        vo.setReason("最新手册");
        fillFileInfoFromActiveDoc(vo, manual);
        return vo;
    }

    private String buildReasonText(List<String> reasons) {
        if (reasons.isEmpty()) return "";
        if (reasons.size() <= 2) return String.join("、", reasons);
        return reasons.get(0) + "、" + reasons.get(1) + "等" + reasons.size() + "个原因";
    }

    private void fillFileInfoFromActiveDoc(ManualRecommendVO vo, MaintenanceManual manual) {
        if (manual.getActiveDocumentId() != null) {
            KnowledgeDocument activeDoc = knowledgeDocumentService.getById(manual.getActiveDocumentId());
            if (activeDoc != null) {
                vo.setFileType(activeDoc.getFileType());
                vo.setFileSize(activeDoc.getFileSize());
                return;
            }
        }
        vo.setFileType(manual.getFileType());
        vo.setFileSize(manual.getFileSize());
    }

    // ==================== 内部数据结构 ====================

    private static class ScoredManual {
        final MaintenanceManual manual;
        double totalScore = 0;
        final List<String> reasons = new ArrayList<>();

        ScoredManual(MaintenanceManual manual) {
            this.manual = manual;
        }
    }
}
