package ai.weixiu.pojo.vo;

import lombok.Data;

import java.time.LocalDateTime;

@Data
public class TaskVoiceSessionVO {
    private Long id;
    private Long taskId;
    private Long userId;
    private Long currentStepId;
    private String status;
    private String pendingAction;
    private Long pendingStepId;
    private String pendingReply;
    private LocalDateTime lastActiveAt;
}

