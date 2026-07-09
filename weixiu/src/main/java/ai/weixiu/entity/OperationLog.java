package ai.weixiu.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.experimental.Accessors;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 操作流水日志。由 {@code @OpLog} 注解 + AOP 切面在关键写操作成功后自动落库，
 * 供管理端首页「最近动态」展示。
 */
@Data
@Accessors(chain = true)
@TableName("operation_log")
public class OperationLog implements Serializable {

    private static final long serialVersionUID = 1L;

    @TableId(value = "id", type = IdType.AUTO)
    private Long id;

    /** 操作人ID */
    private Long userId;

    /** 操作人姓名（冗余，免联表） */
    private String userName;

    /** 操作描述，如“提交检修案例” */
    private String action;

    /** 操作对象类型: case/task/user 等 */
    private String targetType;

    /** 操作对象ID */
    private String targetId;

    /** 状态标记，用于前端动态圆点着色: pending/approved 等 */
    private String status;

    /** 操作时间 */
    private LocalDateTime createdAt;
}
