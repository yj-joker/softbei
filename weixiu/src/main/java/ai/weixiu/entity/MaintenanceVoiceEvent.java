package ai.weixiu.entity;

import com.baomidou.mybatisplus.annotation.IdType;
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
@TableName("maintenance_voice_event")
public class MaintenanceVoiceEvent implements Serializable {

    private static final long serialVersionUID = 1L;

    @TableId(value = "id", type = IdType.ASSIGN_ID)
    private Long id;

    private Long sessionId;
    private Long taskId;
    private Long userId;
    private String transcript;
    private Long focusedStepId;
    private String agentAction;
    private String actionLabel;
    private Long targetStepId;
    private String replyText;
    private String agentRawJson;
    private String executionResult;
    private String executionDetail;
    private Boolean overrideFlag;
    private String auditReason;
    private LocalDateTime createdAt;
}

