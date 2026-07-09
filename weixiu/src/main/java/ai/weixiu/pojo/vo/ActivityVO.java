package ai.weixiu.pojo.vo;

import lombok.Data;

import java.time.LocalDateTime;

/**
 * 最近动态条目，供管理端首页展示。
 */
@Data
public class ActivityVO {
    /** 操作人姓名 */
    private String user;
    /** 操作描述，如“提交检修案例” */
    private String action;
    /** 状态标记（前端圆点着色）: pending/approved 等 */
    private String status;
    /** 操作时间（前端转“x 分钟前”相对时间展示） */
    private LocalDateTime time;
}
