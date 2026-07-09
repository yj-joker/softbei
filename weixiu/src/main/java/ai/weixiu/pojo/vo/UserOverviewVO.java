package ai.weixiu.pojo.vo;

import lombok.Data;

import java.util.Map;

/**
 * 用户端首页概览统计。
 * 全部为实时 count 结果，无预置/模拟数据。
 */
@Data
public class UserOverviewVO {
    /** 设备总数（Neo4j Device 节点数） */
    private Long deviceTotal;
    /** 我的待办任务数（当前用户未 CLOSED 的任务） */
    private Long myOpenTasks;
    /** 我的已完成任务数（当前用户 CLOSED 任务） */
    private Long myClosedTasks;
    /** 我的任务总数 */
    private Long myTaskTotal;
    /** 任务完成率（myClosedTasks / myTaskTotal * 100，无任务时为 0），保留一位小数 */
    private Double completionRate;
    /**
     * 我的任务按状态分布：key 为状态(CREATED/GENERATING/GENERATED/EXECUTING/CLOSED)，value 为数量。
     * 用于首页「检修任务」流转展示。
     */
    private Map<String, Long> taskFlow;
}
