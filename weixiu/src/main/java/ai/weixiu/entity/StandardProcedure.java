package ai.weixiu.entity;

import com.baomidou.mybatisplus.annotation.*;
import lombok.Data;
import lombok.EqualsAndHashCode;
import lombok.experimental.Accessors;

import java.io.Serializable;
import java.time.LocalDateTime;

@Data
@EqualsAndHashCode(callSuper = false)
@Accessors(chain = true)
@TableName("standard_procedure")
public class StandardProcedure implements Serializable {

    private static final long serialVersionUID = 1L;

    @TableId(value = "id", type = IdType.ASSIGN_ID)
    private Long id;

    /** 规程名称 */
    private String name;

    /** 设备类型 */
    private String deviceType;

    /** 检修等级: ROUTINE / MINOR / MAJOR */
    private String maintenanceLevel;

    /** 规程说明 */
    private String description;

    /** 版本号 */
    private Integer version;

    /** 状态: DRAFT / PUBLISHED / ARCHIVED */
    private String status;

    /** 来源: MANUAL_CREATE / AI_GENERATE / TASK_PROMOTE */
    private String sourceType;

    /** 源任务ID（TASK_PROMOTE 时） */
    private Long sourceTaskId;

    /** 步骤总数（冗余） */
    private Integer totalSteps;

    /** 创建人ID */
    private Long createdBy;

    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
