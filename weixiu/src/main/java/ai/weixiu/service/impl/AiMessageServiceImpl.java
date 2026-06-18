package ai.weixiu.service.impl;

import ai.weixiu.entity.AiMessage;
import ai.weixiu.mapper.AiMessageMapper;
import ai.weixiu.service.AiMessageService;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.util.List;

/**
 * <p>
 *  服务实现类
 * </p>
 *
 * @author author
 * @since 2026-05-07
 */
@Service
@Slf4j
public class AiMessageServiceImpl extends ServiceImpl<AiMessageMapper, AiMessage> implements AiMessageService {
    /**
     * 获取当前对话窗口内的消息（滑动窗口）
     *
     * 【修复说明】
     * 旧逻辑用 roundCount % maxMemory 计算起始位置，导致窗口大小不稳定：
     * - roundCount=5, maxMemory=4 → start=1 → 只拿2轮
     * - roundCount=8, maxMemory=4 → start=0 → 只拿1轮
     *
     * 新逻辑：永远取最近 maxMemory 轮的消息，用 ORDER BY DESC + LIMIT 实现。
     * 不再依赖 consolidated 字段过滤，因为窗口就是"最近N轮"的概念。
     *
     * @param id         会话ID
     * @param currentId  用户ID
     * @param maxMemory  窗口大小（轮次数）
     * @param roundCount 当前会话已进行的总轮次
     */
    @Override
    public List<AiMessage> findMemory(Long id, Long currentId, Integer maxMemory, Integer roundCount) {
        LambdaQueryWrapper<AiMessage> queryWrapper = new LambdaQueryWrapper<>();
        queryWrapper.eq(AiMessage::getUserId, currentId)
                .eq(AiMessage::getAiSessionId, id)
                // 按轮次倒序，取最近 maxMemory 轮（每轮2条消息：user + assistant）
                .orderByDesc(AiMessage::getRoundNo)
                .last("LIMIT " + (maxMemory * 2));
        List<AiMessage> list = this.list(queryWrapper);
        // 倒序取出后需要反转回时间正序，这样AI看到的消息是按时间顺序排列的
        java.util.Collections.reverse(list);
        log.info("查询记忆: 取最近{}轮，共{}条消息", maxMemory, list.size());
        return list;
    }

    /**
     * 获取需要整合的消息 —— 所有未整合的消息
     *
     * 【修复说明】
     * 旧逻辑用 between(roundCount-maxMemory, roundCount) 限制范围，
     * 导致如果上一次整合失败，那批消息虽然 consolidated=0，
     * 但下次触发时 roundCount 已经前进，旧消息超出范围就永远不会被整合了。
     *
     * 新逻辑：取该会话内所有 consolidated=0 的消息，按轮次正序排列。
     * 这样无论之前失败过多少次，未整合的消息都会被捞起来重新整合。
     *
     * @param roundCount 当前总轮次（保留参数但不再用于限制范围）
     * @param sessionId  会话ID
     * @param userId     用户ID
     * @param maxMemory  窗口大小（保留参数但不再用于限制范围）
     */
    @Override
    public List<AiMessage> getNeedIntegrationMemory(Integer roundCount, Long sessionId, Long userId, Integer maxMemory) {
        LambdaQueryWrapper<AiMessage> queryWrapper = new LambdaQueryWrapper<>();
        queryWrapper.eq(AiMessage::getUserId, userId)
                .eq(AiMessage::getAiSessionId, sessionId)
                .eq(AiMessage::getConsolidated, 0)
                // 不再用 between 限制轮次范围，取所有未整合的消息
                // 这样即使上次整合失败，这些消息也不会丢失
                .orderByAsc(AiMessage::getRoundNo);
        return this.list(queryWrapper);
    }
}
