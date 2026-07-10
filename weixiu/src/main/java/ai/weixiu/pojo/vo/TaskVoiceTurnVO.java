package ai.weixiu.pojo.vo;

import lombok.Data;

import java.util.List;
import java.util.Map;

@Data
public class TaskVoiceTurnVO {
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
    private String voiceSummary;  // 对话压缩摘要（更新后）
    private Map<String, Object> agentDecision;
    private List<TaskStepRecordVO> steps;
    /** ASR 原始识别文本（清洗前），供前端展示「识别到」 */
    private String originalTranscript;
    /** 整理后发给主模型的文本，供前端展示「理解为」；与原文相同时为 null */
    private String cleanedTranscript;
}

