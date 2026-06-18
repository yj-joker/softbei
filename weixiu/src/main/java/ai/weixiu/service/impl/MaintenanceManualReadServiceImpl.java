package ai.weixiu.service.impl;

import ai.weixiu.common.RedisKey;
import ai.weixiu.entity.MaintenanceManual;
import ai.weixiu.entity.ManualReadRecord;
import ai.weixiu.exceprion.NotFoundException;
import ai.weixiu.exceprion.NullException;
import ai.weixiu.mapper.ManualReadRecordMapper;
import ai.weixiu.pojo.PageResult;
import ai.weixiu.pojo.vo.ManualReadHistoryVO;
import ai.weixiu.pojo.vo.MaintenanceManualReadHeartbeatVO;
import ai.weixiu.pojo.vo.MaintenanceManualReadStartVO;
import ai.weixiu.service.MaintenanceManualRankService;
import ai.weixiu.service.MaintenanceManualReadService;
import ai.weixiu.service.MaintenanceManualService;
import ai.weixiu.utils.BaseContext;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import lombok.AllArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.redisson.api.RLock;
import org.redisson.api.RedissonClient;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

import java.time.Duration;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.LocalTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;
import java.util.concurrent.TimeUnit;

@Slf4j
@Service
@AllArgsConstructor
/**
 * 维修手册阅读会话与心跳累计服务。
 *
 * <p>单次阅读会话可以很短，但阅读时长累计按”用户 + 手册 + 当天”保存。
 * 这样用户一天内多次短时查看同一本手册，也能逐步累计到 60 秒有效阅读阈值。</p>
 */
public class MaintenanceManualReadServiceImpl implements MaintenanceManualReadService {
    /** 阅读统计使用的业务时区，保证按天累计和前端理解的日期边界一致。 */
    private static final ZoneId BUSINESS_ZONE = ZoneId.of("Asia/Shanghai");

    /** 返回给前端的建议心跳间隔，单位为秒。 */
    private static final int HEARTBEAT_INTERVAL_SECONDS = 20;

    /** 单次心跳最多认可的阅读秒数，用于限制断网或伪造上报造成的异常补时。 */
    private static final int MAX_HEARTBEAT_SECONDS = 30;

    /** 当天累计阅读达到该秒数后，才允许为手册排行榜增加一次分值。 */
    private static final int VALID_READ_THRESHOLD_SECONDS = 60;

    /** 阅读会话过期时间；前端持续心跳时会被刷新。 */
    private static final Duration READ_SESSION_TTL = Duration.ofMinutes(5);

    /** 阅读会话、累计时长和计榜标记都使用字符串 Redis 操作。 */
    private final StringRedisTemplate stringRedisTemplate;

    /** 串行化同一阅读会话的并发心跳。 */
    private final RedissonClient redissonClient;

    /** 开始阅读前用它确认手册真实存在。 */
    private final MaintenanceManualService maintenanceManualService;

    /** 阅读达到阈值且抢到当天计榜标记后，调用它更新各周期榜单。 */
    private final MaintenanceManualRankService maintenanceManualRankService;

    /** 阅读记录持久化。 */
    private final ManualReadRecordMapper manualReadRecordMapper;

    @Override
    /**
     * 创建一次阅读会话。
     *
     * <p>会话本身只绑定当前登录用户和本次打开的手册，真正的当天累计阅读时长并不放在会话里，
     * 而是放在“用户 + 手册 + 日期”维度的独立 key 中。这样用户同一天反复打开手册，
     * 每次会话都能把有效心跳继续累计到同一份当天阅读时长上。</p>
     */
    public MaintenanceManualReadStartVO start(Long manualId) {
        if (manualId == null) {
            throw new NullException("Maintenance manual id cannot be empty");
        }
        Long userId = currentUserId();
        maintenanceManualService.getManualById(manualId);

        // 阅读会话将后续心跳绑定到当前登录用户和当前手册，
        // 前端只需要保存 readSessionId 并在心跳时带回。
        String readSessionId = UUID.randomUUID().toString().replace("-", "");
        long now = System.currentTimeMillis();
        Map<String, String> session = new HashMap<>();
        session.put("userId", userId.toString());
        session.put("manualId", manualId.toString());
        session.put("startAt", Long.toString(now));
        session.put("lastHeartbeatAt", Long.toString(now));
        String sessionKey = RedisKey.MANUAL_READ_SESSION + readSessionId;
        stringRedisTemplate.opsForHash().putAll(sessionKey, session);
        stringRedisTemplate.expire(sessionKey, READ_SESSION_TTL);

        // 持久化阅读记录：首次 INSERT，重复打开 UPDATE last_read_at
        recordReadHistory(userId, manualId);

        return new MaintenanceManualReadStartVO(readSessionId, HEARTBEAT_INTERVAL_SECONDS, VALID_READ_THRESHOLD_SECONDS);
    }

    @Override
    /**
     * 处理一次阅读心跳。
     *
     * <p>心跳接口不信任前端自行提交的阅读秒数，只接受会话 id，
     * 再用服务端记录的最近心跳时间计算本次可累计时长。
     * 同一会话加锁后再更新最近心跳时间，避免并发请求把同一段时长重复计入。</p>
     */
    public MaintenanceManualReadHeartbeatVO heartbeat(String readSessionId) {
        if (!StringUtils.hasText(readSessionId)) {
            throw new NullException("Read session id cannot be empty");
        }
        Long userId = currentUserId();

        // 同一阅读会话的连续或并发心跳不能同时使用同一个 lastHeartbeatAt，
        // 否则会把同一段阅读秒数重复累计。
        RLock lock = redissonClient.getLock(RedisKey.MANUAL_READ_SESSION_LOCK + readSessionId);
        boolean locked = false;
        try {
            locked = lock.tryLock(1, 10, TimeUnit.SECONDS);
            if (!locked) {
                throw new IllegalArgumentException("Read heartbeat is too frequent");
            }
            return heartbeatLocked(readSessionId, userId);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new RuntimeException("Read heartbeat lock interrupted", e);
        } finally {
            if (locked && lock.isHeldByCurrentThread()) {
                lock.unlock();
            }
        }
    }

    /**
     * 在会话锁持有期间完成心跳主流程。
     *
     * <p>流程包括读取并校验会话、计算本次有效秒数、更新当天累计时长、
     * 刷新会话 TTL，以及在达到 60 秒阈值后尝试抢占当天计榜标记。
     * 抢占成功才会真正更新排行榜，所以同一用户当天多次打开同一手册也只加一分。</p>
     */
    private MaintenanceManualReadHeartbeatVO heartbeatLocked(String readSessionId, Long userId) {
        String sessionKey = RedisKey.MANUAL_READ_SESSION + readSessionId;
        Map<Object, Object> session = stringRedisTemplate.opsForHash().entries(sessionKey);
        if (session.isEmpty()) {
            throw new NotFoundException("Read session expired");
        }
        Long sessionUserId = parseLong(session.get("userId"));
        Long manualId = parseLong(session.get("manualId"));
        Long lastHeartbeatAt = parseLong(session.get("lastHeartbeatAt"));
        if (!Objects.equals(sessionUserId, userId) || manualId == null || lastHeartbeatAt == null) {
            throw new IllegalArgumentException("Invalid read session");
        }

        long now = System.currentTimeMillis();
        long elapsedSeconds = Math.max(0, (now - lastHeartbeatAt) / 1000);

        // 阅读秒数以服务端时间计算，并限制单次心跳最多累计的秒数，
        // 防止客户端长时间断开后一次性补报异常长的阅读时长。
        long effectiveSeconds = Math.min(elapsedSeconds, MAX_HEARTBEAT_SECONDS);
        String durationKey = getDurationKey(userId, manualId);
        Long currentDuration = readDuration(durationKey);
        if (effectiveSeconds > 0) {
            currentDuration = stringRedisTemplate.opsForValue().increment(durationKey, effectiveSeconds);
            stringRedisTemplate.expire(durationKey, currentDayTtl());
        }

        stringRedisTemplate.opsForHash().put(sessionKey, "lastHeartbeatAt", Long.toString(now));
        stringRedisTemplate.expire(sessionKey, READ_SESSION_TTL);

        boolean rankIncreased = false;
        String countedKey = getCountedKey(userId, manualId);
        if (currentDuration != null && currentDuration >= VALID_READ_THRESHOLD_SECONDS) {
            // setIfAbsent 用于抢占“当天已计榜”标记。
            // 标记写入成功后，后续会话仍可继续上报阅读时长，
            // 但当天不会再次把同一用户对同一手册的阅读写入排行榜。
            Boolean countClaimed = stringRedisTemplate.opsForValue()
                    .setIfAbsent(countedKey, "1", Duration.ofDays(365));
            if (Boolean.TRUE.equals(countClaimed)) {
                try {
                    maintenanceManualRankService.increaseRank(manualId);
                    rankIncreased = true;
                } catch (RuntimeException e) {
                    stringRedisTemplate.delete(countedKey);
                    throw e;
                }
            }
        }
        boolean counted = Boolean.TRUE.equals(stringRedisTemplate.hasKey(countedKey));
        return new MaintenanceManualReadHeartbeatVO(currentDuration == null ? 0L : currentDuration, counted, rankIncreased);
    }

    /** 从线程上下文取得当前登录用户 id，阅读统计必须绑定真实用户。 */
    private Long currentUserId() {
        Long userId = BaseContext.getCurrentId();
        if (userId == null) {
            throw new IllegalArgumentException("User must login before reading manual");
        }
        return userId;
    }

    /** 读取当天累计阅读秒数；key 不存在时从 0 秒开始累计。 */
    private Long readDuration(String durationKey) {
        String duration = stringRedisTemplate.opsForValue().get(durationKey);
        return duration == null ? 0L : Long.parseLong(duration);
    }

    /** 容错解析 Redis Hash 中的数字字段，格式不合法时返回 null 交给上层判定会话无效。 */
    private Long parseLong(Object value) {
        if (value == null) {
            return null;
        }
        try {
            return Long.parseLong(value.toString());
        } catch (NumberFormatException e) {
            return null;
        }
    }

    /** 拼装当天累计阅读时长 key。 */
    private String getDurationKey(Long userId, Long manualId) {
        return RedisKey.MANUAL_READ_DURATION + userId + ":" + manualId + ":" + currentDay();
    }

    /** 拼装已计榜标记 key（终身一次，不含日期）。 */
    private String getCountedKey(Long userId, Long manualId) {
        return RedisKey.MANUAL_READ_COUNTED + userId + ":" + manualId;
    }

    /** 获取业务时区下的当前日期文本，作为按天统计 key 的组成部分。 */
    private String currentDay() {
        return LocalDate.now(BUSINESS_ZONE).format(DateTimeFormatter.BASIC_ISO_DATE);
    }

    /**
     * 计算当天阅读统计相关 key 的剩余寿命。
     *
     * <p>TTL 到次日零点后一小时，而不是固定 24 小时，
     * 这样可以让日期维度自然切换，也给跨零点附近的心跳留出处理缓冲。</p>
     */
    private Duration currentDayTtl() {
        // 当天累计时长和计榜标记延续到上海时区次日零点后一小时，
        // 给跨日边界附近的心跳处理留出缓冲。
        LocalDateTime now = LocalDateTime.now(BUSINESS_ZONE);
        LocalDateTime tomorrow = LocalDateTime.of(LocalDate.now(BUSINESS_ZONE).plusDays(1), LocalTime.MIDNIGHT);
        return Duration.between(now, tomorrow).plusHours(1);
    }

    // ==================== 阅读历史 ====================

    /**
     * 记录一次阅读：首次 INSERT，重复打开 UPDATE last_read_at。
     */
    private void recordReadHistory(Long userId, Long manualId) {
        try {
            ManualReadRecord existing = manualReadRecordMapper.selectOne(
                    Wrappers.<ManualReadRecord>lambdaQuery()
                            .eq(ManualReadRecord::getUserId, userId)
                            .eq(ManualReadRecord::getManualId, manualId));

            LocalDateTime now = LocalDateTime.now(BUSINESS_ZONE);
            if (existing == null) {
                ManualReadRecord record = new ManualReadRecord()
                        .setUserId(userId)
                        .setManualId(manualId)
                        .setLastReadAt(now);
                manualReadRecordMapper.insert(record);
            } else {
                existing.setLastReadAt(now);
                manualReadRecordMapper.updateById(existing);
            }
        } catch (Exception e) {
            // 阅读记录写入失败不应影响阅读会话的正常创建
            log.warn("阅读记录写入失败: userId={}, manualId={}, error={}", userId, manualId, e.getMessage());
        }
    }

    @Override
    public PageResult<ManualReadHistoryVO> getReadHistory(Integer page, Integer size) {
        Long userId = currentUserId();
        int pageNum = (page == null || page <= 0) ? 1 : page;
        int pageSize = (size == null || size <= 0) ? 10 : Math.min(size, 50);

        Page<ManualReadRecord> recordPage = manualReadRecordMapper.selectPage(
                new Page<>(pageNum, pageSize),
                Wrappers.<ManualReadRecord>lambdaQuery()
                        .eq(ManualReadRecord::getUserId, userId)
                        .orderByDesc(ManualReadRecord::getLastReadAt));

        List<ManualReadHistoryVO> voList = new ArrayList<>();
        for (ManualReadRecord record : recordPage.getRecords()) {
            try {
                MaintenanceManual manual = maintenanceManualService.getManualById(record.getManualId());
                ManualReadHistoryVO vo = new ManualReadHistoryVO();
                vo.setManualId(manual.getId());
                vo.setManualName(manual.getManualName());
                vo.setManualImage(manual.getManualImage());
                vo.setManualDesc(manual.getManualDesc());
                vo.setLastReadAt(record.getLastReadAt());
                voList.add(vo);
            } catch (NotFoundException ignored) {
                // 手册已删除，跳过
            }
        }

        return new PageResult<>(voList, recordPage.getTotal(), pageNum, pageSize);
    }
}
