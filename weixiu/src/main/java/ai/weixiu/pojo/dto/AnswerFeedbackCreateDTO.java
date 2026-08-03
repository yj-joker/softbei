package ai.weixiu.pojo.dto;

import lombok.Data;

@Data
public class AnswerFeedbackCreateDTO {
    private Long sessionId;
    private Long assistantMessageId;
    private String assistantAnswer;
    private String reasonCode;
    private String comment;
    private String deviceType;
    private String documentId;
}
