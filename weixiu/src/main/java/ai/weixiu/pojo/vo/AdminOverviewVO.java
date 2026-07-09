package ai.weixiu.pojo.vo;

import lombok.Data;

import java.util.Map;

/**
 * 管理端首页概览统计。
 * 全部为实时 count 结果，无预置/模拟数据。
 */
@Data
public class AdminOverviewVO {
    /** 用户总数 */
    private Long userTotal;
    /** 检修任务总数 */
    private Long taskTotal;
    /** 待审核案例数（CaseRecord status=pending） */
    private Long pendingCaseTotal;
    /** 设备总数（Neo4j Device 节点数） */
    private Long deviceTotal;
    /**
     * 全部任务按状态分布：key 为状态(CREATED/GENERATING/GENERATED/EXECUTING/CLOSED)，value 为数量。
     * 用于管理端首页的任务状态分布饼图。
     */
    private Map<String, Long> taskStatusDist;
}
