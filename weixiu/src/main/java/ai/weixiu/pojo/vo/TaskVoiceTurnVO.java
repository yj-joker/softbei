package ai.weixiu.pojo.vo;

import lombok.Data;

import java.util.List;
import java.util.Map;

@Data
public class TaskVoiceTurnVO {
    private Long sessionId;
    private String replyText;
    private String action;
    private String actionLabel;
    private Long targetStepId;
    private Long currentStepId;
    private Boolean needsConfirmation;
    private Boolean overrideRecommended;
    private Boolean canExecute;
    private String executionResult;
    private String executionDetail;
    private String auditReason;
    private Map<String, Object> agentDecision;
    private List<TaskStepRecordVO> steps;
}

