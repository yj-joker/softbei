package ai.weixiu.entity;

import com.baomidou.mybatisplus.annotation.*;
import com.baomidou.mybatisplus.extension.handlers.JacksonTypeHandler;
import lombok.Data;
import lombok.EqualsAndHashCode;
import lombok.experimental.Accessors;

import java.io.Serializable;
import java.time.LocalDateTime;
import java.util.List;

@Data
@EqualsAndHashCode(callSuper = false)
@Accessors(chain = true)
@TableName(value = "procedure_step", autoResultMap = true)
public class ProcedureStep implements Serializable {

    private static final long serialVersionUID = 1L;

    @TableId(value = "id", type = IdType.ASSIGN_ID)
    private Long id;

    /** 关联规程ID */
    private Long procedureId;

    /** 步骤序号（从1开始） */
    private Integer stepOrder;

    /** 步骤标题 */
    private String title;

    /** 操作详细内容 */
    private String content;

    /** 安全注意事项 */
    private String safetyNote;

    /** 是否合规检查点 */
    private Boolean isCheckpoint;

    /** 检查项列表 */
    @TableField(typeHandler = JacksonTypeHandler.class)
    private List<String> checkpointItems;

    /** 预估耗时(分钟) */
    private Integer estimatedMinutes;

    /** 参考图片URL列表 */
    @TableField(typeHandler = JacksonTypeHandler.class)
    private List<String> referenceImages;

    private LocalDateTime createdAt;
}
