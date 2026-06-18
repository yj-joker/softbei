package ai.weixiu.entity;

import com.baomidou.mybatisplus.annotation.*;
import com.baomidou.mybatisplus.extension.handlers.JacksonTypeHandler;
import lombok.Data;
import lombok.experimental.Accessors;
import java.io.Serializable;
import java.time.LocalDateTime;

@Data
@Accessors(chain = true)
@TableName(value = "user_question_bank", autoResultMap = true)
public class UserQuestionBank implements Serializable {
    private static final long serialVersionUID = 1L;
    @TableId(value = "id", type = IdType.ASSIGN_ID)
    private Long id;
    private Long userId;
    private String topic;
    private String questionType;
    private String stem;
    @TableField(typeHandler = JacksonTypeHandler.class)
    private Object options;
    private String correctAnswer;
    private String explanation;
    @TableField(typeHandler = JacksonTypeHandler.class)
    private Object sources;
    private String folder;        // 二期用
    private Long sourceSessionId;
    private LocalDateTime createdAt;
}
