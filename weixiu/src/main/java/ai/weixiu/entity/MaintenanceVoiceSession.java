package ai.weixiu.entity;

import com.baomidou.mybatisplus.annotation.FieldStrategy;
import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.EqualsAndHashCode;
import lombok.experimental.Accessors;

import java.io.Serializable;
import java.time.LocalDateTime;

@Data
@EqualsAndHashCode(callSuper = false)
@Accessors(chain = true)
@TableName("maintenance_voice_session")
public class MaintenanceVoiceSession implements Serializable {

    private static final long serialVersionUID = 1L;

    @TableId(value = "id", type = IdType.ASSIGN_ID)
    private Long id;

    private Long taskId;
    private Long userId;
    private Long currentStepId;
    private String status;
    private String compressedSummary;
    @TableField(updateStrategy = FieldStrategy.ALWAYS)
    private String pendingAction;
    @TableField(updateStrategy = FieldStrategy.ALWAYS)
    private Long pendingStepId;
    @TableField(updateStrategy = FieldStrategy.ALWAYS)
    private String pendingReply;
    @TableField(updateStrategy = FieldStrategy.ALWAYS)
    private String pendingAgentJson;
    private LocalDateTime lastActiveAt;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
