package ai.weixiu.service;

import ai.weixiu.pojo.vo.ManualRecommendVO;

import java.util.List;

/**
 * 维修手册个性化推荐服务
 *
 * <p>根据用户画像（偏好记忆 + 事实记忆 + 近期对话）为每个用户
 * 推荐最相关的维修手册，解决"全局热榜 ≠ 个人相关性"的问题。</p>
 */
public interface ManualRecommendService {

    /**
     * 获取个性化推荐手册列表（优先走缓存）
     *
     * @param userId 用户ID
     * @param limit  最多返回几条
     * @return 按推荐分数降序排列的手册列表
     */
    List<ManualRecommendVO> getRecommendations(Long userId, int limit);

    /**
     * 异步刷新用户推荐缓存（对话完成后调用）
     *
     * @param userId 用户ID
     */
    void refreshAsync(Long userId);

    /**
     * 清除用户推荐缓存（偏好变更时调用）
     *
     * @param userId 用户ID
     */
    void invalidateCache(Long userId);
}
