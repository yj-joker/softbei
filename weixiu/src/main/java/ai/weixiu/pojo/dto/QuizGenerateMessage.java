package ai.weixiu.pojo.dto;

import lombok.Data;
import java.util.List;
import java.util.Map;

/** 发给 Python QuizAgent 的出题请求。画像/掌握度/履历由 Java 直接打包，Python 无需回调。 */
@Data
public class QuizGenerateMessage {
    private Long quizSessionId;
    private Long userId;
    private Integer targetCount;                    // 目标题数(默认5)
    private List<Map<String, Object>> portrait;     // 画像 [{type,content,confidence}]
    private List<Map<String, Object>> mastery;      // 掌握度 [{topic,correctRate,totalCount}]
    private List<Map<String, Object>> taskHistory;  // 履历(approved CaseRecord)
    private List<String> deviceScope;               // 工人常修设备名(从画像/履历提)
    private List<String> existingTopics;            // 该工人已有 topic，供 LLM 复用去重
}
