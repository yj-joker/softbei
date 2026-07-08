package ai.weixiu.entity;

import com.baomidou.mybatisplus.annotation.*;
import com.baomidou.mybatisplus.extension.handlers.JacksonTypeHandler;
import lombok.Data;
import lombok.EqualsAndHashCode;
import lombok.experimental.Accessors;

import java.io.Serializable;
import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.List;

@Data
@EqualsAndHashCode(callSuper = false)
@Accessors(chain = true)
@TableName(value = "task_step_record", autoResultMap = true)
public class TaskStepRecord implements Serializable {

    private static final long serialVersionUID = 1L;

    @TableId(value = "id", type = IdType.ASSIGN_ID)
    private Long id;

    /** 所属任务ID */
    private Long taskId;

    /** 步骤序号（从1开始） */
    private Integer sortOrder;

    /** 步骤标题 */
    private String title;

    /** 步骤详细说明 */
    private String content;

    /** 安全注意事项 */
    private String safetyNote;

    /** 是否要求拍照 */
    private Boolean requirePhoto;

    /** 是否要求备注 */
    private Boolean requireNote;

    /** 预估耗时(分钟) */
    private Integer estimatedMinutes;

    /** PENDING / SUBMITTED / AI_PASSED / AI_REJECTED / COMPLETED / SKIPPED */
    private String status;

    /** 工人上传的照片 */
    @TableField(typeHandler = JacksonTypeHandler.class)
    private List<String> images;

    /** 工人填写的备注 */
    private String note;

    @TableField(updateStrategy = FieldStrategy.ALWAYS)
    private LocalDateTime completedAt;
    private LocalDateTime createdAt;

    // ===== 合规检查点 =====

    /** 是否为合规检查点 */
    private Boolean isCheckpoint;

    /** 检查项列表，如 ["已断电确认","已佩戴护目镜"] */
    @TableField(typeHandler = JacksonTypeHandler.class)
    private List<String> checkpointItems;

    /** 工人是否已确认所有检查项 */
    private Boolean checkpointConfirmed;

    // ===== 步骤来源溯源 =====

    /** 步骤来源引用(手册/图谱)，JSON 数组 */
    @TableField(typeHandler = JacksonTypeHandler.class)
    private Object sources;

    /** 生成置信度(0-1) */
    private BigDecimal generateConfidence;

    // ===== AI 验收字段 =====

    /** AI验证是否通过 */
    private Boolean aiPass;

    /** AI验证置信度(0-1) */
    private BigDecimal aiConfidence;

    /** AI验证理由 */
    private String aiReason;
}
