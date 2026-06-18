package ai.weixiu.pojo.vo;

import lombok.Data;
import java.util.List;
import java.util.Map;

@Data
public class QuizQuestionVO {
    private Long id;
    private String topic;
    private String questionType;
    private String stem;
    private List<Map<String, Object>> options;
    private String explanation;     // 答题前为 null，提交后回填
    private String correctAnswer;   // 答题前为 null，提交后回填
    private List<Map<String, Object>> sources; // 提交后回填
    private String workerAnswer;
    private Integer isCorrect;
    private Integer inBank;
    private Integer sortOrder;
}
