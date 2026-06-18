package ai.weixiu.entity;

import com.baomidou.mybatisplus.annotation.*;
import com.baomidou.mybatisplus.extension.handlers.JacksonTypeHandler;
import lombok.Data;
import lombok.experimental.Accessors;
import java.io.Serializable;
import java.time.LocalDateTime;

@Data
@Accessors(chain = true)
@TableName(value = "quiz_session", autoResultMap = true)
public class QuizSession implements Serializable {
    private static final long serialVersionUID = 1L;
    @TableId(value = "id", type = IdType.ASSIGN_ID)
    private Long id;
    private Long userId;
    private String mode;          // AI_GENERATE / BANK_PRACTICE
    private String status;        // GENERATING / READY / SUBMITTED / FAILED
    @TableField(typeHandler = JacksonTypeHandler.class)
    private Object topicPlan;     // List<Map> 主题规划
    private Integer questionCount;
    private Integer score;
    private Integer correctCount;
    private String errorMsg;
    private LocalDateTime createdAt;
    private LocalDateTime submittedAt;
}
