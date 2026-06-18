package ai.weixiu.entity;

import com.baomidou.mybatisplus.annotation.*;
import com.baomidou.mybatisplus.extension.handlers.JacksonTypeHandler;
import lombok.Data;
import lombok.experimental.Accessors;
import java.io.Serializable;
import java.time.LocalDateTime;

@Data
@Accessors(chain = true)
@TableName(value = "quiz_question", autoResultMap = true)
public class QuizQuestion implements Serializable {
    private static final long serialVersionUID = 1L;
    @TableId(value = "id", type = IdType.ASSIGN_ID)
    private Long id;
    private Long sessionId;
    private Long userId;
    private String topic;
    private String questionType;  // single / multiple / judge
    private String stem;
    @TableField(typeHandler = JacksonTypeHandler.class)
    private Object options;       // List<Map{key,text}>
    private String correctAnswer; // 单选/判断=单key；多选=逗号升序 A,C
    private String explanation;
    @TableField(typeHandler = JacksonTypeHandler.class)
    private Object sources;
    private String workerAnswer;
    private Integer isCorrect;    // null=未答 0/1
    private Integer inBank;       // 0/1
    private Long bankQuestionId;
    private Integer sortOrder;
    private LocalDateTime createdAt;
}
