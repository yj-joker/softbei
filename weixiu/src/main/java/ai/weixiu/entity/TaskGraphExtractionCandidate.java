package ai.weixiu.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import com.baomidou.mybatisplus.extension.handlers.JacksonTypeHandler;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.FieldStrategy;
import lombok.Data;
import lombok.experimental.Accessors;

import java.time.LocalDateTime;

@Data
@Accessors(chain = true)
@TableName(value = "task_graph_extraction_candidate", autoResultMap = true)
public class TaskGraphExtractionCandidate {
    @TableId(value = "id", type = IdType.ASSIGN_ID)
    private Long id;
    private Long taskId;
    private Integer evidenceVersion;
    private String requestId;
    @TableField(typeHandler = JacksonTypeHandler.class) private Object candidateJson;
    @TableField(typeHandler = JacksonTypeHandler.class) private Object evidenceJson;
    @TableField(typeHandler = JacksonTypeHandler.class) private Object warnings;
    private String modelName;
    private String modelRequestId;
    private Integer attempt;
    private String extractionStatus;
    private String reviewStatus;
    @TableField(updateStrategy = FieldStrategy.ALWAYS)
    private Long reviewedBy;
    @TableField(updateStrategy = FieldStrategy.ALWAYS)
    private String reviewComment;
    @TableField(updateStrategy = FieldStrategy.ALWAYS)
    private LocalDateTime reviewedAt;
    private String editComment;
    private Long editedBy;
    private LocalDateTime editedAt;
    private Integer rowVersion;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
