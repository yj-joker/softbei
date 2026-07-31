package ai.weixiu.entity;

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
@TableName("answer_feedback")
public class AnswerFeedback implements Serializable {

    private static final long serialVersionUID = 1L;

    @TableId(value = "id", type = IdType.ASSIGN_ID)
    private Long id;

    @TableField("user_id")
    private Long userId;

    @TableField("session_id")
    private Long sessionId;

    @TableField("assistant_message_id")
    private Long assistantMessageId;

    @TableField("question_message_id")
    private Long questionMessageId;

    @TableField("original_question")
    private String originalQuestion;

    @TableField("original_answer")
    private String originalAnswer;

    @TableField("reason_code")
    private String reasonCode;

    @TableField("user_comment")
    private String userComment;

    @TableField("device_type")
    private String deviceType;

    @TableField("document_id")
    private String documentId;

    @TableField("status")
    private String status;

    @TableField("corrected_answer")
    private String correctedAnswer;

    @TableField("domain_rule_id")
    private Long domainRuleId;

    @TableField("process_comment")
    private String processComment;

    @TableField("processed_by_id")
    private Long processedById;

    @TableField("processed_at")
    private LocalDateTime processedAt;

    @TableField("created_at")
    private LocalDateTime createdAt;

    @TableField("updated_at")
    private LocalDateTime updatedAt;
}
