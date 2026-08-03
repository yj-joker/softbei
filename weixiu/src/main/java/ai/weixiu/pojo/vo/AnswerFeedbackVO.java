package ai.weixiu.pojo.vo;

import lombok.Data;

import java.time.LocalDateTime;

@Data
public class AnswerFeedbackVO {
    private Long id;
    private Long userId;
    private Long sessionId;
    private Long assistantMessageId;
    private Long questionMessageId;
    private String originalQuestion;
    private String originalAnswer;
    private String reasonCode;
    private String userComment;
    private String deviceType;
    private String documentId;
    private String status;
    private String correctedAnswer;
    private Long domainRuleId;
    private String processComment;
    private Long processedById;
    private LocalDateTime processedAt;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
