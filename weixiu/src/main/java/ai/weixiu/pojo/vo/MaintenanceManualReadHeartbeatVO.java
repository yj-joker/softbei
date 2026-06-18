package ai.weixiu.pojo.vo;

import lombok.AllArgsConstructor;
import lombok.Data;

@Data
@AllArgsConstructor
/** 一次阅读心跳处理后的统计结果。 */
public class MaintenanceManualReadHeartbeatVO {
    /** 当前用户当天阅读该手册的累计秒数。 */
    private Long currentDurationSeconds;

    /** 当前用户当天是否已经为该手册成功计入过排行榜。 */
    private Boolean counted;

    /** 本次心跳是否刚好触发了排行榜分值增加。 */
    private Boolean rankIncreased;
}
