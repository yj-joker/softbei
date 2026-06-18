package ai.weixiu.service.impl;

import ai.weixiu.common.RedisKey;
import ai.weixiu.entity.MaintenanceManual;
import ai.weixiu.enumerate.MaintenanceManualRankType;
import ai.weixiu.exceprion.NotFoundException;
import ai.weixiu.pojo.vo.MaintenanceManualRankVO;
import ai.weixiu.service.MaintenanceManualRankService;
import ai.weixiu.service.MaintenanceManualService;
import lombok.AllArgsConstructor;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ZSetOperations;
import org.springframework.stereotype.Service;

import java.time.LocalDate;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.time.temporal.IsoFields;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.concurrent.TimeUnit;

@Service
@AllArgsConstructor
/**
 * 基于 Redis ZSet 的维修手册有效阅读排行榜服务。
 *
 * <p>排行榜只保存手册 id 和分数。展示字段在查询榜单时再从手册服务读取，
 * 避免手册名称、封面修改或手册删除后，历史榜单里残留过期展示数据。</p>
 */
public class MaintenanceManualRankServiceImpl implements MaintenanceManualRankService {
    /** 榜单周期按业务时区切换，避免部署机器时区变化影响日榜和周榜边界。 */
    private static final ZoneId BUSINESS_ZONE = ZoneId.of("Asia/Shanghai");

    /** 前端未传 limit 或传入非法值时采用的默认榜单数量。 */
    private static final int DEFAULT_LIMIT = 10;

    /** 服务端允许返回的最大榜单数量，避免一次读取过多 ZSet 成员。 */
    private static final int MAX_LIMIT = 100;

    /** 榜单 ZSet 的读写入口。 */
    private final StringRedisTemplate stringRedisTemplate;

    /** 查询榜单展示字段时复用手册详情服务。 */
    private final MaintenanceManualService maintenanceManualService;

    @Override
    /**
     * 为一次有效阅读增加榜单分值。
     *
     * <p>排行榜分数表示有效阅读次数，不表示阅读秒数。
     * 阅读服务已经负责“同一用户同一天同一手册只计一次”，
     * 因此这里收到调用后直接为当前日榜、周榜、月榜和总榜加 1。</p>
     */
    public void increaseRank(Long manualId) {
        String member = manualId.toString();

        // 一次有效阅读同时更新当前日榜、周榜、月榜和总榜。
        // 周期榜设置过期时间，总榜长期累计。
        increment(getRankKey(MaintenanceManualRankType.DAY), member, 30, TimeUnit.DAYS);
        increment(getRankKey(MaintenanceManualRankType.WEEK), member, 84, TimeUnit.DAYS);
        increment(getRankKey(MaintenanceManualRankType.MONTH), member, 730, TimeUnit.DAYS);
        stringRedisTemplate.opsForZSet().incrementScore(RedisKey.MANUAL_RANK_TOTAL, member, 1);
    }

    @Override
    /**
     * 查询指定周期榜单。
     *
     * <p>Redis ZSet 只保存手册 id 和 score，查询时按分数倒序拿成员，
     * 再回到手册服务补齐名称、封面和描述。若历史榜单里残留了已删除手册，
     * 这里会跳过它而不是把失效记录返回给前端。</p>
     */
    public List<MaintenanceManualRankVO> getRankList(MaintenanceManualRankType rankType, Integer limit) {
        int rankLimit = normalizeLimit(limit);
        Set<ZSetOperations.TypedTuple<String>> tuples = stringRedisTemplate.opsForZSet()
                .reverseRangeWithScores(getRankKey(rankType), 0, rankLimit - 1L);
        List<MaintenanceManualRankVO> rankList = new ArrayList<>();
        if (tuples == null || tuples.isEmpty()) {
            return rankList;
        }

        int rank = 1;
        for (ZSetOperations.TypedTuple<String> tuple : tuples) {
            Long manualId = parseManualId(tuple.getValue());
            if (manualId == null) {
                continue;
            }
            try {
                // getManualById 会复用详情缓存保护逻辑，
                // 同时过滤掉排行榜生成后已经删除的手册。
                MaintenanceManual manual = maintenanceManualService.getManualById(manualId);
                rankList.add(toRankVO(rank, manual, tuple.getScore()));
                rank++;
            } catch (NotFoundException ignored) {
                // Historic rank keys can outlive deleted manuals.
            }
        }
        return rankList;
    }

    /** 对某个周期榜单成员加分，并刷新周期榜保留时间。 */
    private void increment(String key, String member, long timeout, TimeUnit timeUnit) {
        stringRedisTemplate.opsForZSet().incrementScore(key, member, 1);
        stringRedisTemplate.expire(key, timeout, timeUnit);
    }

    /**
     * 根据榜单类型生成当前周期对应的 Redis key。
     *
     * <p>日榜 key 使用 yyyyMMdd，周榜 key 使用 ISO 周年和周序号，
     * 月榜 key 使用 yyyyMM，总榜则固定使用一个长期累计 key。</p>
     */
    private String getRankKey(MaintenanceManualRankType rankType) {
        LocalDate today = LocalDate.now(BUSINESS_ZONE);

        // 榜单周期按业务时区计算，不依赖服务器机器时区，
        // 保证日榜、周榜、月榜 key 的边界稳定。
        return switch (rankType) {
            case DAY -> RedisKey.MANUAL_RANK_DAY + today.format(DateTimeFormatter.BASIC_ISO_DATE);
            case WEEK -> RedisKey.MANUAL_RANK_WEEK
                    + today.get(IsoFields.WEEK_BASED_YEAR)
                    + String.format("%02d", today.get(IsoFields.WEEK_OF_WEEK_BASED_YEAR));
            case MONTH -> RedisKey.MANUAL_RANK_MONTH + today.format(DateTimeFormatter.ofPattern("yyyyMM"));
            case TOTAL -> RedisKey.MANUAL_RANK_TOTAL;
        };
    }

    /** 规范化前端 limit，既提供默认值也限制最大读取数量。 */
    private int normalizeLimit(Integer limit) {
        if (limit == null || limit <= 0) {
            return DEFAULT_LIMIT;
        }
        return Math.min(limit, MAX_LIMIT);
    }

    /** 把 ZSet 成员解析为手册 id，脏值直接跳过。 */
    private Long parseManualId(String value) {
        try {
            return value == null ? null : Long.parseLong(value);
        } catch (NumberFormatException e) {
            return null;
        }
    }

    /** 把手册实体和 ZSet 分数转换成前端排行榜展示对象。 */
    private MaintenanceManualRankVO toRankVO(int rank, MaintenanceManual manual, Double score) {
        MaintenanceManualRankVO rankVO = new MaintenanceManualRankVO();
        rankVO.setRank(rank);
        rankVO.setManualId(manual.getId());
        rankVO.setManualName(manual.getManualName());
        rankVO.setManualImage(manual.getManualImage());
        rankVO.setManualDesc(manual.getManualDesc());
        rankVO.setScore(score == null ? 0L : score.longValue());
        return rankVO;
    }
}
