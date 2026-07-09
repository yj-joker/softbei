package ai.weixiu.service;

import ai.weixiu.pojo.vo.ActivityVO;

import java.util.List;

/**
 * 操作流水服务：记录关键写操作、查询最近动态。
 */
public interface OperationLogService {

    /**
     * 异步记录一条操作流水。失败仅告警，不影响主流程。
     *
     * @param userId     操作人ID（可为 null）
     * @param action     操作描述
     * @param targetType 对象类型（可为 null/空）
     * @param targetId   对象ID（可为 null/空）
     * @param status     状态标记（可为 null/空）
     */
    void record(Long userId, String action, String targetType, String targetId, String status);

    /** 最近动态（按时间倒序，最多 limit 条，1~50） */
    List<ActivityVO> recentActivities(int limit);
}
