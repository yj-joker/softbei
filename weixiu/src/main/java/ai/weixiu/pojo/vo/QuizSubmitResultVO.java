package ai.weixiu.pojo.vo;

import lombok.Data;
import java.util.List;

@Data
public class QuizSubmitResultVO {
    private Long sessionId;
    private Integer score;          // 答对数
    private Integer total;
    private List<QuizQuestionVO> questions; // 含答案/解析/来源/对错
}
