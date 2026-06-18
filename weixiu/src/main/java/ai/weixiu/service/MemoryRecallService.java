package ai.weixiu.service;

import ai.weixiu.pojo.dto.RecallContext;

/**
 * 记忆召回服务 — 统一封装所有记忆类型的召回逻辑。
 *
 * <p>从 AiServiceImpl.finalAiContext() 中抽出，统一管理：
 * summary、relevantFacts、preferences、unresolved 四类召回，
 * 并记录每次召回的完整 trace。</p>
 */
public interface MemoryRecallService {

    /**
     * 执行完整的记忆召回（无业务上下文）。
     *
     * @param sessionId    会话ID
     * @param userId       用户ID
     * @param userMessage  用户当前消息（用于事实向量检索）
     * @param roundNo      当前对话轮次（用于 trace 记录）
     * @return 包含所有召回数据和 trace 的上下文对象
     */
    default RecallContext recall(Long sessionId, Long userId, String userMessage, Integer roundNo) {
        return recall(sessionId, userId, userMessage, roundNo, null, null, null, null);
    }

    /**
     * 执行完整的记忆召回（带业务上下文）。
     *
     * <p>当调用者知道当前正在讨论的设备/场地/任务时，传递业务参数可以让
     * FactReranker 的 business_match 因子优先返回相关记忆。</p>
     *
     * @param sessionId    会话ID
     * @param userId       用户ID
     * @param userMessage  用户当前消息（用于事实向量检索）
     * @param roundNo      当前对话轮次（用于 trace 记录）
     * @param deviceType   设备类型（可选）
     * @param equipmentId  设备ID（可选）
     * @param siteId       场地ID（可选）
     * @param taskId       检修任务ID（可选）
     * @return 包含所有召回数据和 trace 的上下文对象
     */
    RecallContext recall(Long sessionId, Long userId, String userMessage, Integer roundNo,
                         String deviceType, String equipmentId, String siteId, String taskId);
}
